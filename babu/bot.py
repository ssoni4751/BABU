import asyncio
import json
import os
import re
import sys
import threading
import time
BOT_START_TIME = time.time()
tg_application = None
LAST_TELEGRAM_SUCCESS_TIME = None
LAST_FB_SUCCESS_TIME = None
LAST_GOOGLE_SUCCESS_TIME = None
LAST_WEB_SUCCESS_TIME = None
import traceback
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, List, Literal, Optional, TypedDict

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

try:
    from .google_service import execute_google_action, is_google_configured
    from .social_media import run_autonomous_social_post
except ImportError:
    from google_service import execute_google_action, is_google_configured
    from social_media import run_autonomous_social_post
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "babu_checkpoint.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

REFUSAL_PRIVATE_DATA = "Information unavailable. No authoritative business records were found."

AUTHORITY_MEMORY = "AUTHORITY_MEMORY"
AUTHORITY_DATABASE = "AUTHORITY_DATABASE"
AUTHORITY_LEDGER = "AUTHORITY_LEDGER"
AUTHORITY_WEB = "AUTHORITY_WEB"
AUTHORITY_MODEL = "AUTHORITY_MODEL"

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL and (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
        import psycopg2
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(url), True
    else:
        return sqlite3.connect(DB_PATH), False

def init_postgres_db():
    if not DATABASE_URL or not (DATABASE_URL.startswith("postgres://") or DATABASE_URL.startswith("postgresql://")):
        return
    try:
        import psycopg2
        url = DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sealed_epochs (
                epoch_id TEXT PRIMARY KEY,
                sealed_at TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS search_cache (
                query_hash TEXT PRIMARY KEY,
                raw_query TEXT,
                distilled_results TEXT,
                sources TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS execution_ledger (
                event_id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                task_id TEXT,
                department TEXT,
                event_type TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT,
                metadata TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_memory (
                key TEXT PRIMARY KEY,
                data TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trusted_templates (
                template_id TEXT PRIMARY KEY,
                template_signature TEXT UNIQUE,
                goal_graph_json TEXT,
                version INTEGER DEFAULT 1,
                execution_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                consecutive_failures INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ACTIVE',
                promoted_from_goal_id TEXT,
                promotion_epoch INTEGER,
                average_execution_time REAL DEFAULT 0.0,
                average_token_cost REAL DEFAULT 0.0,
                last_used TEXT,
                created_at TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS babu_temporal_timeline (
                event_id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                event_category VARCHAR(50) NOT NULL,
                summary TEXT NOT NULL,
                outcome VARCHAR(20),
                impact_score REAL DEFAULT 1.0,
                cause TEXT,
                effect TEXT,
                resolution TEXT,
                confidence DOUBLE PRECISION,
                metadata TEXT
            );
        """)
        for col, col_type in [("cause", "TEXT"), ("effect", "TEXT"), ("resolution", "TEXT"), ("confidence", "DOUBLE PRECISION")]:
            cursor.execute(f"ALTER TABLE babu_temporal_timeline ADD COLUMN IF NOT EXISTS {col} {col_type};")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS architecture_knowledge (
                record_id TEXT PRIMARY KEY,
                record_type TEXT NOT NULL,
                title TEXT NOT NULL,
                phase TEXT,
                problem TEXT,
                decision TEXT,
                reason TEXT,
                outcome TEXT,
                tradeoff TEXT,
                impact_score INTEGER,
                supersedes TEXT,
                status TEXT DEFAULT 'Active',
                timestamp TEXT
            );
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS babu_k0_working_memory (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal_id TEXT NOT NULL,
                user_query TEXT NOT NULL,
                response TEXT,
                response_full TEXT,
                response_summary TEXT,
                status TEXT NOT NULL,
                failures TEXT,
                retrieved_records TEXT,
                knowledge_classes TEXT,
                source_records TEXT,
                conversation_reference BOOLEAN DEFAULT FALSE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_k0_session_id ON babu_k0_working_memory (session_id);")
        
        # Safe migration / column additions for babu_k0_working_memory if table exists
        try:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN IF NOT EXISTS response_full TEXT;")
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN IF NOT EXISTS response_summary TEXT;")
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN IF NOT EXISTS knowledge_classes TEXT;")
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN IF NOT EXISTS source_records TEXT;")
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN IF NOT EXISTS conversation_reference BOOLEAN DEFAULT FALSE;")
        except Exception as e:
            print(f"[POSTGRES K0 MIGRATION WARNING] Failed to run K0 column migrations: {e}", flush=True)
        
        # Safe migration from babu_adr if exists
        try:
            cursor.execute("SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'babu_adr');")
            has_legacy = cursor.fetchone()[0]
        except Exception:
            has_legacy = False
            
        if has_legacy:
            try:
                cursor.execute("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'babu_adr'
                """)
                legacy_cols = [r[0] for r in cursor.fetchall()]
                target_cols = [
                    "record_id", "record_type", "title", "phase", "problem", "decision",
                    "reason", "outcome", "tradeoff", "impact_score", "supersedes", "status", "timestamp"
                ]
                common_cols = [c for c in target_cols if c in legacy_cols]
                
                if "record_id" in common_cols and "title" in common_cols:
                    cols_str = ", ".join(common_cols)
                    placeholders = ", ".join(["%s"] * len(common_cols))
                    cursor.execute(f"SELECT {cols_str} FROM babu_adr")
                    for row in cursor.fetchall():
                        rec_id = row[common_cols.index("record_id")]
                        cursor.execute("SELECT COUNT(*) FROM architecture_knowledge WHERE record_id = %s", (rec_id,))
                        if cursor.fetchone()[0] == 0:
                            cursor.execute(f"""
                                INSERT INTO architecture_knowledge ({cols_str})
                                VALUES ({placeholders})
                            """, row)
                cursor.execute("ALTER TABLE babu_adr RENAME TO babu_adr_backup;")
                print("[POSTGRES MIGRATION] Migrated babu_adr to architecture_knowledge and backed up old table.", flush=True)
            except Exception as e:
                print(f"[POSTGRES MIGRATION WARNING] Legacy migration failed: {e}", flush=True)
                
        cursor.execute("SELECT COUNT(*) FROM architecture_knowledge;")
        if cursor.fetchone()[0] == 0:
            seed_records = [
                ("ADR-001", "ADR", "Dynamic Imports", "Phase 1", "Render memory pressure (512MB RAM cap limits worker capacity)", "Move heavy imports to local runtime scope", "Reduce startup memory usage by ~100MB+, avoiding OOM kills on startup", "Successfully kept startup memory footprint at ~310MB", "Slightly slower first invocation/execution latency for target tasks due to on-demand loading, but achieves high system stability", 8, None, "Active", "2026-05-27T08:00:00Z"),
                ("ADR-002", "ADR", "Runtime Index", "Phase 2", "System-state queries (identity, health, telemetry) incorrectly routed through planner and web search, causing high token costs and latency", "Introduce runtime_index.md and deterministic query routing", "Prevent LLMs from planning external web search/research for internal system awareness", "Zero-token system introspection and deterministic response within milliseconds", "Static/manual mapping of system-aware queries requires maintainers to register keywords in bot code", 9, None, "Active", "2026-06-11T12:00:00Z"),
                ("ADR-003", "ADR", "Gemini Embeddings", "Phase 3", "OpenAI dependency and embedding API cost quota limits causing RAG failures", "Migrate to Gemini embedding-001 model", "Switch to a free, highly capable embedding API with LangChain integration", "Free embedding pipeline restoring search functionality", "Strict rate limits on free-tier Gemini API, handled via staged rate-limiting fallback gateways", 7, None, "Active", "2026-06-17T06:00:00Z"),
                ("POSTMORTEM-001", "POSTMORTEM", "Telegram Outage Incident", "Phase 3", "Transport layer unavailable (webhook timeouts and network blocks)", "Implement async polling failsafe and local console simulation", "Ensure core execution and debugging is not blocked by third-party API availability", "Core loop decoupled from messaging network state", "Console simulator does not test webhooks/network edge cases directly", 6, None, "Active", "2026-06-17T08:00:00Z"),
                ("LESSON-001", "LESSON", "Self-Awareness Hierarchy (K1-K7)", "Phase 3", "Lack of semantic boundary organization leading to retrieval confusion", "Formally register Knowledge Classes (K1-K7) inside runtime routing", "Organize self-awareness data: K1 Identity, K2 Runtime, K3 User, K4 Execution, K5 Architecture, K6 Domain, K7 External", "High-precision intent routing and scoped RAG retrieval", "Requires categorizing user queries into explicit knowledge classes", 8, None, "Active", "2026-06-17T09:00:00Z")
            ]
            for rec in seed_records:
                cursor.execute("""
                    INSERT INTO architecture_knowledge (record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                """, rec)
        conn.commit()
        cursor.close()
        conn.close()
        print("[POSTGRES] Database tables verified/created successfully.", flush=True)
    except Exception as e:
        print(f"[POSTGRES ERROR] init_postgres_db failed: {e}", flush=True)

def init_durable_checkpoint_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sealed_epochs (
            epoch_id TEXT PRIMARY KEY,
            sealed_at TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query_hash TEXT PRIMARY KEY,
            raw_query TEXT,
            distilled_results TEXT,
            sources TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_ledger (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            goal_id TEXT NOT NULL,
            task_id TEXT,
            department TEXT,
            event_type TEXT NOT NULL,
            state_before TEXT,
            state_after TEXT,
            metadata TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_memory (
            key TEXT PRIMARY KEY,
            data TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trusted_templates (
            template_id TEXT PRIMARY KEY,
            template_signature TEXT UNIQUE,
            goal_graph_json TEXT,
            version INTEGER DEFAULT 1,
            execution_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            consecutive_failures INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ACTIVE',
            promoted_from_goal_id TEXT,
            promotion_epoch INTEGER,
            average_execution_time REAL DEFAULT 0.0,
            average_token_cost REAL DEFAULT 0.0,
            last_used TEXT,
            created_at TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_temporal_timeline (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_category TEXT NOT NULL,
            summary TEXT NOT NULL,
            outcome TEXT,
            impact_score REAL DEFAULT 1.0,
            cause TEXT,
            effect TEXT,
            resolution TEXT,
            confidence REAL,
            metadata TEXT
        );
    """)
    for col, col_type in [("cause", "TEXT"), ("effect", "TEXT"), ("resolution", "TEXT"), ("confidence", "REAL")]:
        try:
            cursor.execute(f"ALTER TABLE babu_temporal_timeline ADD COLUMN {col} {col_type};")
        except Exception:
            pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS architecture_knowledge (
            record_id TEXT PRIMARY KEY,
            record_type TEXT NOT NULL,
            title TEXT NOT NULL,
            phase TEXT,
            problem TEXT,
            decision TEXT,
            reason TEXT,
            outcome TEXT,
            tradeoff TEXT,
            impact_score INTEGER,
            supersedes TEXT,
            status TEXT DEFAULT 'Active',
            timestamp TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_k0_working_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            goal_id TEXT NOT NULL,
            user_query TEXT NOT NULL,
            response TEXT,
            response_full TEXT,
            response_summary TEXT,
            status TEXT NOT NULL,
            failures TEXT,
            retrieved_records TEXT,
            knowledge_classes TEXT,
            source_records TEXT,
            conversation_reference BOOLEAN DEFAULT 0,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_k0_session_id ON babu_k0_working_memory (session_id);")
    
    # Safe migration for SQLite to add new columns if they do not exist
    try:
        cursor.execute("PRAGMA table_info(babu_k0_working_memory);")
        existing_cols = [c[1] for c in cursor.fetchall()]
        if "response_full" not in existing_cols:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN response_full TEXT;")
        if "response_summary" not in existing_cols:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN response_summary TEXT;")
        if "knowledge_classes" not in existing_cols:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN knowledge_classes TEXT;")
        if "source_records" not in existing_cols:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN source_records TEXT;")
        if "conversation_reference" not in existing_cols:
            cursor.execute("ALTER TABLE babu_k0_working_memory ADD COLUMN conversation_reference BOOLEAN DEFAULT 0;")
    except Exception as e:
        print(f"[SQLITE K0 MIGRATION WARNING] Failed to run SQLite K0 migrations: {e}", flush=True)
    
    # Safe migration from babu_adr if exists
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='babu_adr';")
        has_legacy = cursor.fetchone() is not None
    except Exception:
        has_legacy = False
        
    if has_legacy:
        try:
            cursor.execute("PRAGMA table_info(babu_adr);")
            legacy_cols = [c[1] for c in cursor.fetchall()]
            target_cols = [
                "record_id", "record_type", "title", "phase", "problem", "decision",
                "reason", "outcome", "tradeoff", "impact_score", "supersedes", "status", "timestamp"
            ]
            common_cols = [c for c in target_cols if c in legacy_cols]
            
            if "record_id" in common_cols and "title" in common_cols:
                cols_str = ", ".join(common_cols)
                placeholders = ", ".join(["?"] * len(common_cols))
                cursor.execute(f"SELECT {cols_str} FROM babu_adr")
                for row in cursor.fetchall():
                    rec_id = row[common_cols.index("record_id")]
                    cursor.execute("SELECT COUNT(*) FROM architecture_knowledge WHERE record_id = ?", (rec_id,))
                    if cursor.fetchone()[0] == 0:
                        cursor.execute(f"""
                            INSERT INTO architecture_knowledge ({cols_str})
                            VALUES ({placeholders})
                        """, row)
            cursor.execute("ALTER TABLE babu_adr RENAME TO babu_adr_backup;")
            print("[SQLITE MIGRATION] Migrated babu_adr to architecture_knowledge and backed up old table.", flush=True)
        except Exception as e:
            print(f"[SQLITE MIGRATION WARNING] Legacy migration failed: {e}", flush=True)

    cursor.execute("SELECT COUNT(*) FROM architecture_knowledge;")
    if cursor.fetchone()[0] == 0:
        seed_records = [
            ("ADR-001", "ADR", "Dynamic Imports", "Phase 1", "Render memory pressure (512MB RAM cap limits worker capacity)", "Move heavy imports to local runtime scope", "Reduce startup memory usage by ~100MB+, avoiding OOM kills on startup", "Successfully kept startup memory footprint at ~310MB", "Slightly slower first invocation/execution latency for target tasks due to on-demand loading, but achieves high system stability", 8, None, "Active", "2026-05-27T08:00:00Z"),
            ("ADR-002", "ADR", "Runtime Index", "Phase 2", "System-state queries (identity, health, telemetry) incorrectly routed through planner and web search, causing high token costs and latency", "Introduce runtime_index.md and deterministic query routing", "Prevent LLMs from planning external web search/research for internal system awareness", "Zero-token system introspection and deterministic response within milliseconds", "Static/manual mapping of system-aware queries requires maintainers to register keywords in bot code", 9, None, "Active", "2026-06-11T12:00:00Z"),
            ("ADR-003", "ADR", "Gemini Embeddings", "Phase 3", "OpenAI dependency and embedding API cost quota limits causing RAG failures", "Migrate to Gemini embedding-001 model", "Switch to a free, highly capable embedding API with LangChain integration", "Free embedding pipeline restoring search functionality", "Strict rate limits on free-tier Gemini API, handled via staged rate-limiting fallback gateways", 7, None, "Active", "2026-06-17T06:00:00Z"),
            ("POSTMORTEM-001", "POSTMORTEM", "Telegram Outage Incident", "Phase 3", "Transport layer unavailable (webhook timeouts and network blocks)", "Implement async polling failsafe and local console simulation", "Ensure core execution and debugging is not blocked by third-party API availability", "Core loop decoupled from messaging network state", "Console simulator does not test webhooks/network edge cases directly", 6, None, "Active", "2026-06-17T08:00:00Z"),
            ("LESSON-001", "LESSON", "Self-Awareness Hierarchy (K1-K7)", "Phase 3", "Lack of semantic boundary organization leading to retrieval confusion", "Formally register Knowledge Classes (K1-K7) inside runtime routing", "Organize self-awareness data: K1 Identity, K2 Runtime, K3 User, K4 Execution, K5 Architecture, K6 Domain, K7 External", "High-precision intent routing and scoped RAG retrieval", "Requires categorizing user queries into explicit knowledge classes", 8, None, "Active", "2026-06-17T09:00:00Z")
        ]
        for rec in seed_records:
            cursor.execute("""
                INSERT INTO architecture_knowledge (record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, rec)
    conn.commit()
    return conn


def log_temporal_event(
    event_category: str,
    summary: str,
    outcome: Optional[str] = None,
    impact_score: float = 1.0,
    metadata: Optional[dict] = None,
    cause: Optional[str] = None,
    effect: Optional[str] = None,
    resolution: Optional[str] = None,
    confidence: Optional[float] = None
):
    """Log a system chronological event to babu_temporal_timeline."""
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        meta_str = json.dumps(metadata or {})
        
        if is_pg:
            cursor.execute("""
                INSERT INTO babu_temporal_timeline (event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, meta_str))
        else:
            cursor.execute("""
                INSERT INTO babu_temporal_timeline (event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, meta_str))
            
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[TEMPORAL LOG] [{event_category}] {summary} - {outcome} (Cause: {cause}, Effect: {effect}, Resolution: {resolution}, Conf: {confidence})", flush=True)
    except Exception as e:
        print(f"[TEMPORAL ERROR] Failed to log temporal event: {e}", flush=True)

def get_temporal_events(limit: int = 50) -> list[dict]:
    """Retrieve the latest system chronological events from babu_temporal_timeline."""
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        if is_pg:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, metadata
                FROM babu_temporal_timeline
                ORDER BY event_id DESC
                LIMIT %s
            """, (limit,))
        else:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, metadata
                FROM babu_temporal_timeline
                ORDER BY event_id DESC
                LIMIT ?
            """, (limit,))
            
        rows = cursor.fetchall()
        events = []
        for row in rows:
            try:
                meta = json.loads(row[10]) if row[10] else {}
            except Exception:
                meta = {}
            events.append({
                "event_id": row[0],
                "timestamp": str(row[1]),
                "event_category": row[2],
                "summary": row[3],
                "outcome": row[4],
                "impact_score": row[5],
                "cause": row[6],
                "effect": row[7],
                "resolution": row[8],
                "confidence": row[9],
                "metadata": meta
            })
            
        cursor.close()
        conn.close()
        return events
    except Exception as e:
        print(f"[TEMPORAL ERROR] Failed to fetch temporal events: {e}", flush=True)
        return []


try:
    db_conn = init_durable_checkpoint_db()
    checkpointer = SqliteSaver(db_conn)
except Exception as e:
    print(f"[CHECKPOINTER WARNING] Failed to initialize SqliteSaver: {e}", flush=True)
    checkpointer = None

PRICING_TABLE = {
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-1.5-flash": (0.075, 0.30),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama3-70b-8192": (0.59, 0.79),
    "llama3-8b-8208": (0.05, 0.08),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.150, 0.600),
    "o1-mini": (3.00, 12.00)
}

def get_token_costs(model_name: str) -> tuple[float, float]:
    if not model_name:
        return 0.15 / 1_000_000, 0.60 / 1_000_000 # default fallback
    m_lower = model_name.lower().strip()
    for key, rates in PRICING_TABLE.items():
        if key in m_lower:
            return rates[0] / 1_000_000, rates[1] / 1_000_000
    return 0.15 / 1_000_000, 0.60 / 1_000_000


def is_epoch_sealed(epoch_id: str) -> bool:
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if is_pg else "?"
        cursor.execute(f"SELECT 1 FROM sealed_epochs WHERE epoch_id = {placeholder}", (epoch_id,))
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        return bool(res)
    except Exception as e:
        print(f"[DB ERROR] is_epoch_sealed failed: {e}", flush=True)
        return False

def seal_epoch(epoch_id: str):
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                "INSERT INTO sealed_epochs (epoch_id, sealed_at) VALUES (%s, %s) ON CONFLICT (epoch_id) DO NOTHING",
                (epoch_id, datetime.now(timezone.utc).isoformat())
            )
        else:
            cursor.execute(
                "INSERT OR IGNORE INTO sealed_epochs (epoch_id, sealed_at) VALUES (?, ?)",
                (epoch_id, datetime.now(timezone.utc).isoformat())
            )
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[GOVERNANCE] Epoch '{epoch_id}' successfully sealed.", flush=True)
    except Exception as e:
        print(f"[DB ERROR] seal_epoch failed: {e}", flush=True)


def get_last_goal_graph(session_id: str) -> Optional[dict]:
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if is_pg else "?"
        cursor.execute(f"""
            SELECT metadata FROM execution_ledger
            WHERE session_id = {placeholder} AND event_type = 'PLANNING'
            ORDER BY event_id DESC LIMIT 1
        """, (session_id,))
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        if res and res[0]:
            meta = json.loads(res[0])
            return meta.get("graph")
    except Exception as e:
        print(f"[DB ERROR] get_last_goal_graph failed: {e}", flush=True)
    return None

# ── Part 3: State Execution Ledger Helpers ───────────────────────────────

def log_execution_ledger_event(session_id: str, goal_id: str, task_id: Optional[str], department: Optional[str], event_type: str, state_before: Optional[str] = None, state_after: Optional[str] = None, metadata: Optional[dict] = None):
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        meta_str = json.dumps(metadata) if metadata else None
        placeholders = "%s, %s, %s, %s, %s, %s, %s, %s" if is_pg else "?, ?, ?, ?, ?, ?, ?, ?"
        cursor.execute(f"""
            INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata)
            VALUES ({placeholders})
        """, (session_id, goal_id, task_id, department, event_type, state_before, state_after, meta_str))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] log_execution_ledger_event failed: {e}", flush=True)

# ── Part 2: Stateful Search Cache Helpers ───────────────────────────────────

import hashlib

def _query_hash(query: str) -> str:
    return hashlib.sha256(query.lower().strip().encode("utf-8")).hexdigest()

def get_cached_search(query: str, ttl_hours: float = 12.0) -> Optional[dict]:
    try:
        q_hash = _query_hash(query)
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("""
                SELECT distilled_results, sources, created_at 
                FROM search_cache 
                WHERE query_hash = %s AND (EXTRACT(EPOCH FROM NOW()) - EXTRACT(EPOCH FROM created_at)) < %s
            """, (q_hash, ttl_hours * 3600))
        else:
            cursor.execute("""
                SELECT distilled_results, sources, created_at 
                FROM search_cache 
                WHERE query_hash = ? AND (strftime('%s', 'now') - strftime('%s', created_at)) < ?
            """, (q_hash, ttl_hours * 3600))
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        if res:
            return {"results": res[0], "sources": res[1]}
    except Exception as e:
        print(f"[DB ERROR] get_cached_search failed: {e}", flush=True)
    return None

def store_cached_search(query: str, distilled_results: str, sources: str):
    try:
        q_hash = _query_hash(query)
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("""
                INSERT INTO search_cache (query_hash, raw_query, distilled_results, sources, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (query_hash) DO UPDATE SET 
                    raw_query = EXCLUDED.raw_query,
                    distilled_results = EXCLUDED.distilled_results,
                    sources = EXCLUDED.sources,
                    created_at = CURRENT_TIMESTAMP
            """, (q_hash, query, distilled_results, sources))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO search_cache (query_hash, raw_query, distilled_results, sources, created_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (q_hash, query, distilled_results, sources))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] store_cached_search failed: {e}", flush=True)

from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
PORT            = int(os.environ.get("PORT", 8080))
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")
API_CHAT_TOKEN  = os.environ.get("API_CHAT_TOKEN", "").strip()

CURRENT_PA_MODEL   = "llama-3.1-8b-instant"
CURRENT_DEPT_MODEL = "llama-3.3-70b-versatile"

def build_llm(model_name: str, temp: float):
    """Dynamically construct ChatGroq, ChatGoogleGenerativeAI, or ChatOpenAI based on model name and available credentials."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    target_model = model_name.strip()

    # 1. Google Gemini Native Support
    if target_model.startswith("gemini-"):
        if gemini_key:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=target_model, temperature=temp, google_api_key=gemini_key)
        elif openrouter_key:
            or_model = f"google/{target_model}"
            print(f"[LLM FALLBACK] Gemini key missing. Routing '{target_model}' through OpenRouter as '{or_model}'.", flush=True)
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=or_model,
                temperature=temp,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            fallback = "llama-3.1-8b-instant"
            print(f"[LLM REDIRECT] Both Gemini and OpenRouter keys missing. Mapping '{target_model}' to Groq '{fallback}'.", flush=True)
            return ChatGroq(model=fallback, temperature=temp, api_key=groq_key)

    # 2. OpenRouter Support (Any model containing '/' or starting with 'openrouter/')
    elif "/" in target_model or target_model.startswith("openrouter/"):
        clean_model = target_model.replace("openrouter/", "")
        if not openrouter_key:
            raise ValueError("OPENROUTER_API_KEY is not configured in environment vbabubles.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=clean_model,
            temperature=temp,
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1"
        )

    # 3. OpenAI Native Support
    elif target_model.startswith("gpt-"):
        if not openai_key:
            raise ValueError("OPENAI_API_KEY is not configured in environment vbabubles.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=target_model, temperature=temp, api_key=openai_key)

    # 4. Default: Groq Support
    else:
        return ChatGroq(model=target_model, temperature=temp, api_key=groq_key)

llm_pa   = build_llm(CURRENT_PA_MODEL,   0.2)
llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)

# ---------------------------------------------------------------------------
# Provider-level auto-failover for rate limits
# ---------------------------------------------------------------------------

# Map Groq models to their OpenRouter equivalents and alternate Groq models
_FALLBACK_CHAIN = {
    "llama-3.1-8b-instant":    ["gemma2-9b-it", "meta-llama/llama-3.1-8b-instruct", "google/gemini-2.5-flash"],
    "llama-3.3-70b-versatile": ["llama-3.1-8b-instant", "gemma2-9b-it", "meta-llama/llama-3.3-70b-instruct", "google/gemini-2.5-flash"],
}
_RATE_LIMIT_SIGNALS = ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")


def invoke_with_fallback(messages, model_name: str, temp: float):
    """Invoke an LLM with automatic provider failover on rate limits and general failures.

    Tries the primary model first. If it hits a 429/rate-limit error,
    it retries up to 3 times with backoff. If it still fails, or if it encounters
    any other error, it automatically falls back to alternative models in the chain.

    Parameters
    ----------
    messages : list
        Langchain message list [SystemMessage, HumanMessage, ...].
    model_name : str
        Primary model identifier (e.g. 'llama-3.1-8b-instant').
    temp : float
        Temperature for generation.

    Returns
    -------
    LLM response object with .content and .response_metadata.
    """
    import time
    fallbacks = _FALLBACK_CHAIN.get(model_name, ["google/gemini-2.0-flash-001"])
    models_to_try = [model_name] + fallbacks

    last_exc = None
    for i, model in enumerate(models_to_try):
        max_attempts = 3 if i == 0 else 1
        for attempt in range(max_attempts):
            try:
                llm = build_llm(model, temp)
                response = llm.invoke(messages)
                if i > 0:
                    print(f"[LLM FAILOVER SUCCESS] '{model}' responded after primary '{model_name}' failed.", flush=True)
                return response
            except Exception as exc:
                last_exc = exc
                exc_str = str(exc).lower()
                is_rate_limit = any(sig in exc_str for sig in _RATE_LIMIT_SIGNALS)
                
                if is_rate_limit and attempt < max_attempts - 1:
                    sleep_time = (attempt + 1) * 2
                    print(f"[LLM RATE LIMIT] '{model}' rate-limited. Retrying in {sleep_time}s (attempt {attempt + 1}/{max_attempts})...", flush=True)
                    time.sleep(sleep_time)
                    continue
                break

        # If we exhausted attempts or got any error, transition to next fallback model
        if i < len(models_to_try) - 1:
            next_model = models_to_try[i + 1]
            print(f"[LLM FAILOVER] '{model}' failed ({last_exc}) → switching to '{next_model}'", flush=True)
            continue

    # Last model in chain — propagate the last exception
    raise last_exc

USER_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")
_profile_lock = threading.Lock()

def load_user_profile() -> dict:
    with _profile_lock:
        if os.path.exists(USER_PROFILE_PATH):
            try:
                with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PROFILE LOAD ERROR] {e}", flush=True)
        return {}

USER_PROFILE = load_user_profile()

def get_current_profile() -> dict:
    """Dynamically load fresh user profile from disk to guarantee live state access."""
    return load_user_profile()

def is_profile_relevant_query(query: str) -> bool:
    """Determine if a query is related to the user's personal identity, family, business, or background."""
    if not query:
        return False
    q = query.lower()
    
    # User nicknames, names, business identifiers, and contact placeholders
    personal_keywords = {
        "anshu", "shubham", "swarnkar", "ash", "ssoni", "computer", "consultancy", 
        "tax", "consultant", "consultants", "compliance", "e-governance", "csc",
        "who am i", "who i am", "my name", "my nickname", "my business", "my company", 
        "my bussiness", "my busines", "bussiness", "busines", "bussines",
        "my work", "my job", "my shop", "my family", "my father", "my mother", 
        "my brother", "my sibling", "my parents", "my cousin", "my background", 
        "my journey", "my education", "my career", "my email", "my phone", 
        "my number", "my address", "my location", "where i live", "where do i live",
        "tell me about me", "my profile", "my biography", "my bio", "who is speaking",
        "who is talking", "about me", "know about me", "know about my", "pf", "itr", "gst",
        "orai", "jalaun", "official mail", "official email", "personal mail", "personal email",
        "my mail", "to my mail", "to my email"
    }
    
    # Check exact keyword matching or substring match
    return any(kw in q for kw in personal_keywords)

def is_private_data_query(query: str, category: Optional[str] = None) -> bool:
    """Determine if a query is asking for private user-owned data or business operations."""
    if not query:
        return False
    q = query.lower()
    
    # 1. Check intent category if provided
    if category in ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION"):
        return True

    # 2. Check for private business and personal keywords (with possessive/pronoun cues or strong topics)
    possessives = {"my", "mere", "meri", "mera", "apna", "apne", "apni", "mne", "our", "us", "mine", "we"}
    private_topics = {
        "client", "clients", "customer", "customers", "invoice", "invoices", "payment",
        "payments", "transaction", "transactions", "sales", "earnings", "revenue", "profit",
        "profits", "ledger", "ledgers", "pf claim", "pf claims", "uan consolidation", "kyc correction",
        "joint declaration", "gst registration", "gstr-1", "gstr-3b"
    }
    
    # Check if any possessive prefix matches
    words = q.split()
    for i, w in enumerate(words):
        if w in possessives and i + 1 < len(words) and words[i+1] in private_topics:
            return True

    # Check for direct private topics unless it is a general explanation query
    is_general = any(g in q for g in ("what is", "how to", "definition", "explain", "tutorial", "general process", "generic"))
    if not is_general:
        if any(topic in q for topic in private_topics):
            return True
            
    # Or strong standalone indicators of user's personal operations
    strong_indicators = {
        "kitne clients", "client details", "client records", "customer details", "customer records",
        "official mail", "official email", "personal mail", "personal email"
    }
    if any(ind in q for ind in strong_indicators):
        return True
        
    return False

def get_user_profile_text(profile_type: str = "FULL") -> str:
    """Return L1 Daily Profile Context, selectively retrieving context based on type."""
    profile = get_current_profile()
    if not profile:
        return ""
    
    details = profile.get("personal_details", {})
    prefs = profile.get("preferences", {})
    nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
    
    if profile_type in ("THIN", "WALK"):
        # Ultra-thin identity context for casual conversation
        lines = [
            "[USER PERSONALIZATION CONTEXT]",
            f"  • User Name: {nickname}",
            f"  • Tone Preference: {prefs.get('communication_style', 'Warm, brief, natural')}"
        ]
        return "\n".join(lines)
        
    name = details.get("full_name", "")
    business = profile.get("business_context", {})
    
    lines = ["[USER PROFILE & CONTEXT]"]
    if name:
        lines.append(f"  • User Name: {name} (Nickname: {nickname})" if nickname else f"  • User Name: {name}")
    if details.get("personal_email"):
        lines.append(f"  • Personal Email: {details.get('personal_email')}")
    if details.get("official_email"):
        lines.append(f"  • Official Email: {details.get('official_email')}")
    if details.get("mobile_number"):
        lines.append(f"  • Mobile Number: {details.get('mobile_number')}")
    if details.get("residential_address"):
        addr = details.get("residential_address", {})
        if isinstance(addr, dict):
            addr_str = f"{addr.get('address', '')}, {addr.get('city', '')}, {addr.get('state', '')}, {addr.get('country', '')}"
            lines.append(f"  • Residential Address: {addr_str.strip(', ')}")
        else:
            lines.append(f"  • Residential Address: {addr}")

    if business:
        lines.append("  • Business Details:")
        lines.append(f"    - Name: {business.get('business_name', '')}")
        lines.append(f"    - Type: {business.get('business_type', '') or business.get('legacy_name', '')}")
        if business.get("location"):
            lines.append(f"    - Location: {business.get('location', {}).get('office', '')}")
        if business.get("contact"):
            contact = business.get("contact", {})
            lines.append(f"    - Website: {contact.get('website', '')}")
            lines.append(f"    - Contact Email: {contact.get('email', '')}")
        if business.get("marketing_identity"):
            lines.append(f"    - Tagline: {business.get('marketing_identity', {}).get('tagline', '')}")
        if business.get("core_services"):
            lines.append("    - Core Services:")
            for srv_cat, srv_list in business.get("core_services", {}).items():
                lines.append(f"      * {srv_cat.replace('_', ' ').title()}: {', '.join(srv_list)}")
        if business.get("growth_focus"):
            lines.append(f"    - Growth Focus: {', '.join(business.get('growth_focus', []))}")
            
    if prefs:
        lines.append(f"  • Timezone: {prefs.get('timezone', 'Asia/Kolkata')}")
        lines.append(f"  • Communication Style: {prefs.get('communication_style', 'Logical and warm')}")
        
    # Append family graph summary
    family = profile.get("family_graph", {})
    if family:
        lines.append("  • Family Relations:")
        for rel_cat, rel_val in family.items():
            if isinstance(rel_val, dict):
                members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in rel_val.items() if v)
                lines.append(f"    - {rel_cat.replace('_', ' ').title()}: {members}")
            else:
                lines.append(f"    - {rel_cat.replace('_', ' ').title()}: {rel_val}")

    # Append mindset & journey highlights
    journey = profile.get("mindset_and_journey", {})
    if journey:
        lines.append("  • Mindset & Journey Highlights:")
        for k, v in journey.get("life_journey_highlights", {}).items():
            lines.append(f"    - {k.replace('_', ' ').title()}: {v}")
        cognitive = journey.get("cognitive_profile", {})
        if cognitive:
            lines.append("    - Cognitive Profile:")
            for ck, cv in cognitive.items():
                lines.append(f"      * {ck.replace('_', ' ').title()}: {cv}")

    return "\n".join(lines)


def get_profile_fact_answer(query: str) -> str:
    """Deterministic profile answers for high-frequency identity questions."""
    profile = get_current_profile()
    if not profile:
        return ""

    q = (query or "").lower()
    details = profile.get("personal_details", {})
    business = profile.get("business_context", {})
    family = profile.get("family_graph", {})

    full_name = details.get("full_name", "") or details.get("primary_nickname", "")
    nickname = details.get("primary_nickname", "")

    if any(k in q for k in ("who am i", "my name", "who i am", "who am i?")):
        if full_name and nickname:
            return f"You are {full_name}, also known as {nickname}."
        if full_name:
            return f"You are {full_name}."
        return ""

    if any(k in q for k in ("where i work", "where do i work", "my work", "where i am working")):
        business_name = business.get("business_name", "")
        classification = business.get("classification", "")
        if business_name and classification:
            return f"You work at {business_name} ({classification})."
        if business_name:
            return f"You work at {business_name}."
        return ""

    if any(k in q for k in ("my business", "whats my business", "what is my business", "my company", "my shop", "about my business")):
        business_name = business.get("business_name", "")
        business_type = business.get("business_type", "") or business.get("classification", "")
        if business_name and business_type:
            return f"Your business is {business_name}, a {business_type}."
        if business_name:
            return f"Your business is {business_name}."
        return ""

    if "father" in q:
        for relation, data in family.items():
            rel = relation.lower().replace("_", " ")
            if "father" in rel and not isinstance(data, (dict, list)):
                return f"Your father's name is {data}."
            if isinstance(data, dict):
                for key, value in data.items():
                    k = key.lower().replace("_", " ")
                    if "father" in k:
                        return f"Your father's name is {value}."
                    if "father" in rel and value:
                        return f"Your father's name is {value}."
        return ""

    return ""


def is_action_status_query(query: str) -> bool:
    q = (query or "").lower()
    markers = (
        "did you send",
        "have you sent",
        "was it sent",
        "is it sent",
        "action triggered",
        "triggered action",
        "mail sent",
        "email sent",
        "did you create",
        "was it created",
    )
    return any(m in q for m in markers)


def search_profile(query: str, bypass_filter: bool = False) -> str:
    """Perform a local directory search on L1 (Personal Details/Business), L2 (Family Graph) and L3 (Legacy Memory) to retrieve specific context."""
    profile = get_current_profile()
    if not profile:
        return ""
    
    cleaned = clean_search_query(query)
    q = cleaned.lower().strip()
    
    # Programmatic Me/Myself/I override:
    # If the query is a general question asking about themselves, load the ENTIRE profile history and context!
    personal_pronouns = {
        "myself", "who am i", "my journey", "my background", "tell me about me", 
        "my profile", "my biography", "my bio", "who is talk", "who is speak",
        "user profile", "profile information", "gather user profile", "know about me",
        "about me", "personal details", "profile data"
    }
    is_general_profile = any(p in q for p in personal_pronouns)
    
    # Programmatic Query vs Statement Classifier:
    # If the user is just sharing a conversational statement, diary entry, or thought, do NOT search the database!
    if not bypass_filter and not is_general_profile:
        question_starters = (
            "who", "what", "when", "where", "why", "how", "is", "are", "was", "were", 
            "can", "could", "should", "would", "do", "does", "did", "tell", "show", 
            "search", "google", "find", "get", "retrieve", "lookup", "which"
        )
        query_phrases = ["what's", "who's", "where's", "how's", "can you", "could you", "do you know"]
        
        is_inquiry = (
            q.endswith("?") 
            or q.startswith(question_starters) 
            or any(p in q for p in query_phrases)
            or len(q.split()) < 4  # Short keyphrase lookups (e.g. "father name") are treated as queries
        )
        if not is_inquiry:
            # Reassurance: Treated as a conversational statement or diary share. Skip database query!
            return ""

    if is_general_profile:
        results = []
        
        # Load L1 Daily Details
        details = profile.get("personal_details", {})
        if details:
            results.append("Personal Details:")
            for k, v in details.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
        # Load L1 Business Context
        business = profile.get("business_context", {})
        if business:
            results.append("Business Context:")
            for k, v in business.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")

        # Load L2 Family Graph Summary
        family = profile.get("family_graph", {})
        if family:
            results.append("Family structure:")
            for rel, d in family.items():
                if isinstance(d, dict):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in d.items() if v and not str(v).startswith("["))
                    if members:
                        results.append(f"  • {rel.replace('_', ' ').title()}: {members}")
                        
        # Load L3 Legacy Autobiographical History & Journey
        journey = profile.get("mindset_and_journey", {})
        for cat, det in journey.items():
            results.append(f"{cat.replace('_', ' ').title()} Background:")
            if isinstance(det, dict):
                for k, v in det.items():
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
            else:
                results.append(f"  • {det}")
                
        edu_career = profile.get("education_and_career", {})
        for edu in edu_career.get("education", []):
            results.append(f"  • Education Record: {edu}")
        for emp in edu_career.get("employment_history", []):
            results.append(f"  • Employment Record: {emp}")
            
        return "[Local User Profile (Full Personal Directory Loaded)]\n" + "\n".join(results)

    results = []
    
    # Simple stop-words list to filter out conversational noise
    stopwords = {
        "which", "year", "i", "passed", "grade", "ecam", "exam", "my", "me", "in", "on", 
        "at", "to", "for", "of", "who", "when", "what", "is", "was", "are", "do", "you", 
        "know", "tell", "show", "did", "does", "have", "has", "had", "a", "an", "the", "about",
        "hi", "hello", "hey", "yo"  # Add conversational greetings to stopwords
    }
    search_words = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in q.split()]
    search_words = [w for w in search_words if w not in stopwords and len(w) >= 1]
    
    def matches_word(text: str) -> bool:
        t_lower = text.lower()
        return any(re.search(r'\b' + re.escape(w) + r'\b', t_lower) for w in search_words)

    # 1. Search L1: Personal Details
    details = profile.get("personal_details", {})
    if details:
        details_clean = str(details).lower()
        if "personal" in q or "email" in q or "contact" in q or "phone" in q or "mobile" in q or matches_word(details_clean):
            results.append(f"• Personal Details: Name: {details.get('full_name', '')} - Nickname: {details.get('primary_nickname', '')} - Email: {details.get('personal_email', '')} - Official Email: {details.get('official_email', '')} - Mobile: {details.get('mobile_number', '')}")

    # 2. Search L1: Business Context
    business = profile.get("business_context", {})
    if business:
        business_name = business.get("business_name", "").lower()
        business_type = business.get("business_type", "").lower()
        if "business" in q or "company" in q or "consultancy" in q or "anshu" in q or matches_word(business_name) or matches_word(business_type):
            results.append(f"• Business Name: {business.get('business_name', '')}")
            results.append(f"• Business Type: {business.get('business_type', '')}")
            if business.get("location"):
                results.append(f"• Business Location: {business.get('location', {}).get('office', '')}")
            if business.get("contact"):
                contact = business.get("contact", {})
                results.append(f"• Business Contact: Website: {contact.get('website', '')}, Email: {contact.get('email', '')}")
            if business.get("core_services"):
                results.append(f"• Business Services: {json.dumps(business.get('core_services', {}))}")
            if business.get("marketing_identity"):
                results.append(f"• Business Tagline: {business.get('marketing_identity', {}).get('tagline', '')}")
            if business.get("growth_focus"):
                results.append(f"• Business Growth Focus: {', '.join(business.get('growth_focus', []))}")

    # 3. Search L2: Family Graph
    family = profile.get("family_graph", {})
    for relation, details_val in family.items():
        relation_clean = relation.lower().replace("_", " ")
        if isinstance(details_val, dict):
            for member_key, member_val in details_val.items():
                member_key_clean = member_key.lower().replace("_", " ")
                # Match if key is in query, or query is in key, or any search word matches key/value as a whole word
                if member_key_clean in q or q in member_key_clean or matches_word(member_key_clean) or matches_word(str(member_val)):
                    results.append(f"• Family Connection ({relation.replace('_', ' ').title()} - {member_key.replace('_', ' ').title()}): {member_val}")
        elif isinstance(details_val, list):
            for item in details_val:
                if q in str(item).lower() or matches_word(str(item)):
                    results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {item}")
        else:
            if relation_clean in q or q in relation_clean or matches_word(str(details_val)):
                results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {details_val}")
                
    # 4. Search L3: Legacy & Autobiographical Memory
    edu_career = profile.get("education_and_career", {})
    for edu in edu_career.get("education", []):
        edu_str = str(edu).lower()
        if q in edu_str or matches_word(edu_str):
            results.append(f"• Education Record: {edu}")
            
    for emp in edu_career.get("employment_history", []):
        emp_str = str(emp).lower()
        if q in emp_str or matches_word(emp_str):
            results.append(f"• Employment Record: {emp}")
            
    journey = profile.get("mindset_and_journey", {})
    for category, details_val in journey.items():
        category_clean = category.lower().replace("_", " ")
        if isinstance(details_val, dict):
            for k, v in details_val.items():
                k_clean = k.lower().replace("_", " ")
                if k_clean in q or category_clean in q or q in k_clean or q in str(v).lower() or matches_word(str(v)):
                    results.append(f"• Background History ({category.replace('_', ' ').title()} - {k.replace('_', ' ').title()}): {v}")
        else:
            if category_clean in q or q in category_clean or q in str(details_val).lower() or matches_word(str(details_val)):
                results.append(f"• Background History ({category.replace('_', ' ').title()}): {details_val}")
                
    if results:
        return "[Local User Profile Matches]\n" + "\n".join(results)
# â”€â”€ Knowledge base â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

KNOWLEDGE_BASE = {
    "babu": (
        "BABU (Behavioral Autonomous Bureaucratic Utility) is a multi-agent AI system "
        "built on LangGraph + Groq/Llama. It dynamically classifies user intent "
        "and plans a custom task graph executed by independent departments. "
        "Available on Telegram and the web."
    ),
    "planning": (
        "Intent routing: dynamically builds a custom task graph (DAG). "
        "Workers like writing, analysis, and execution run independently "
        "while research executes as a branch only when deep research is explicitly required."
    ),
    "tools": (
        "Every BABU agent has access to: live web search (DuckDuckGo), "
        "conversation memory (per-session history), the BABU knowledge base, "
        "and Direct Google Workspace automation (email via Gmail, Calendar events, Sheets logging, and more)."
    ),
    "models": (
        "Router and research agents use llama-3.1-8b-instant (fast). "
        "The Personal Assistant (PA) uses llama-3.3-70b-versatile (highest quality). "
        "All inference runs on Groq free tier."
    ),
}

def clean_search_query(query: str) -> str:
    """Strip Telegram commands and conversational greetings to produce a high-quality search query."""
    # 1. Strip command prefixes like /sprint, /launch, /walk, !sprint, !launch, !walk
    cleaned = re.sub(r'^(?:/[a-zA-Z]+|![a-zA-Z]+)\s*', '', query, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    
    # 2. Strip conversational introductions/fillers
    patterns = [
        r'^(?:hi|hello|hey|yo|greetings|good\s+morning|good\s+afternoon|good\s+evening)\b[,!\s]*',
        r'^(?:please|kindly|could\s+you\s+please|can\s+you\s+tell\s+me|do\s+you\s+know)\b[,!\s]*',
        r'^(?:tell\s+me|find\s+out|search\s+for|look\s+up)\b[,!\s]*'
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
    # 3. Clean trailing punctuation from individual words
    cleaned = re.sub(r'[?.,!]+$', '', cleaned)
    cleaned = re.sub(r'\s+[?.,!]+$', '', cleaned)
    
    # 4. Standardize common profile typos
    cleaned = re.sub(r'\b(?:bussiness|bussines|busines)\b', 'business', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\b(?:bussinesses|bussinesses|businesses)\b', 'businesses', cleaned, flags=re.IGNORECASE)
        
    return cleaned if cleaned else query


def search_knowledge(query: str) -> str:
    cleaned = clean_search_query(query)
    q = cleaned.lower()
    hits = [v for k, v in KNOWLEDGE_BASE.items() if k in q or any(w in q for w in k.split())]
    return "\n".join(hits) if hits else ""


# ── Web search ────────────────────────────────────────────────────────────────

def wikipedia_search(query: str, max_results: int = 3) -> str:
    """Query Wikipedia MediaWiki API to fetch high-quality, structured summaries for research data.
    
    Complies with MediaWiki User-Agent guidelines for up to 200+ requests per minute.
    """
    cleaned = clean_search_query(query)
    if not cleaned:
        return "No search query provided."
    import requests
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": cleaned,
        "limit": max_results,
        "namespace": 0,
        "format": "json"
    }
    headers = {
        "User-Agent": "BABU-Assistant/1.0 (ssoni4751@gmail.com) Python-Requests/2.0"
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code != 200:
            return f"[Wikipedia search failed: status {response.status_code}]"
        
        data = response.json()
        if len(data) < 4:
            return "No Wikipedia matches found."
            
        titles = data[1]
        descriptions = data[2]
        urls = data[3]
        
        if not titles:
            return "No Wikipedia articles matched."
            
        # Batch-fetch extracts for titles with missing or short descriptions to avoid sequential HTTP requests in a loop
        titles_needing_extracts = []
        for i in range(len(titles)):
            desc = descriptions[i] if i < len(descriptions) else ""
            if not desc or len(desc) < 30:
                titles_needing_extracts.append(titles[i])
                
        extracts = {}
        if titles_needing_extracts:
            extract_params = {
                "action": "query",
                "prop": "extracts",
                "exintro": True,
                "explaintext": True,
                "redirects": 1,
                "titles": "|".join(titles_needing_extracts),
                "format": "json"
            }
            try:
                ext_resp = requests.get(url, params=extract_params, headers=headers, timeout=4)
                if ext_resp.status_code == 200:
                    ext_data = ext_resp.json()
                    pages = ext_data.get("query", {}).get("pages", {})
                    for page_id, page_val in pages.items():
                        title_val = page_val.get("title")
                        extract_val = page_val.get("extract")
                        if title_val and extract_val:
                            extracts[title_val] = extract_val
            except Exception as e:
                print(f"[WIKIPEDIA WARNING] Failed to batch fetch extracts: {e}", flush=True)

        lines = []
        for i in range(len(titles)):
            title = titles[i]
            desc = descriptions[i] if i < len(descriptions) else ""
            link = urls[i] if i < len(urls) else ""
            
            if not desc or len(desc) < 30:
                desc = extracts.get(title, desc)
                
            if not desc:
                desc = "No summary available."
                
            lines.append(f"• Wikipedia: {title} [Confidence: 0.95]\n  {desc}\n  Source: {link}")
            
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Wikipedia search unavailable: {e}]"


def web_search(query: str, max_results: int = 4) -> str:
    if is_private_data_query(query):
        print(f"[SEARCH BLOCK] Web search blocked for private personal/business data query: '{query}'", flush=True)
        return REFUSAL_PRIVATE_DATA

    cleaned = clean_search_query(query)
    if not cleaned:
        return "No results found."

    # Check Stateful Search Cache (12-hour TTL)
    cached = get_cached_search(cleaned)
    if cached:
        print(f"[SEARCH CACHE HIT] Reusing cached search results for: '{cleaned[:40]}'", flush=True)
        return cached["results"]
    
    ddg_text = ""
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        print(f"[SEARCH] Querying Tavily Search API for: '{cleaned[:40]}'", flush=True)
        try:
            import requests
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": tavily_key,
                    "query": cleaned,
                    "search_depth": "basic",
                    "max_results": max_results
                },
                timeout=15
            )
            if resp.status_code == 200:
                tavily_results = resp.json().get("results", [])
                tavily_lines = []
                for r in tavily_results:
                    score = r.get("score", 0.8)
                    tavily_lines.append(f"• {r['title']} [Confidence: {score}]\n  {r['content']}\n  Source: {r['url']}")
                ddg_text = "\n\n".join(tavily_lines)
                print(f"[SEARCH SUCCESS] Tavily returned {len(tavily_results)} results.", flush=True)
            else:
                print(f"[SEARCH WARNING] Tavily API returned status {resp.status_code}: {resp.text}", flush=True)
        except Exception as e:
            print(f"[SEARCH WARNING] Tavily query failed: {e}. Falling back to DuckDuckGo.", flush=True)
            
    # Fallback to DuckDuckGo if Tavily is not set or yielded no results
    if not ddg_text:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(cleaned, max_results=max_results))
            ddg_lines = []
            if results:
                for r in results:
                    href = r.get("href", "").lower()
                    # Dynamic Source Confidence Weighting
                    confidence = 0.50
                    if any(ext in href for ext in (".edu", ".gov", ".org")):
                        confidence = 0.98 if any(ext in href for ext in (".edu", ".gov")) else 0.85
                    elif any(news in href for news in ("reuters.com", "apnews.com", "bbc.co.uk", "nytimes.com", "cnn.com", "bloomberg.com")):
                        confidence = 0.85
                    elif any(low in href for low in ("reddit.com", "medium.com", "blogspot.com", "twitter.com", "facebook.com", "x.com")):
                        confidence = 0.25
                    ddg_lines.append(f"• {r['title']} [Confidence: {confidence}]\n  {r['body']}\n  Source: {r['href']}")
            ddg_text = "\n\n".join(ddg_lines)
        except Exception as e:
            ddg_text = f"[DuckDuckGo search unavailable: {e}]"

    # 2. Fetch Wikipedia results (max 2 for optimal token management)
    wiki_text = wikipedia_search(cleaned, max_results=2)

    # Merge results factually
    merged = []
    if wiki_text and not wiki_text.startswith("[") and "No Wikipedia" not in wiki_text:
        merged.append("[Wikipedia Research Matches]")
        merged.append(wiki_text)
    if ddg_text and not ddg_text.startswith("[") and "No results found" not in ddg_text:
        merged.append("[Web Search Results]")
        merged.append(ddg_text)
        
    if not merged:
        merged_text = "No web or Wikipedia results found."
    else:
        merged_text = "\n\n".join(merged)
        
    # Store in local SQLite search cache
    if merged and "No web" not in merged_text:
        store_cached_search(cleaned, merged_text, "Wikipedia, DuckDuckGo")
        
    return merged_text


# â”€â”€ Direct Google Workspace automation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Actions ARIA can detect and trigger
MAKE_ACTIONS = {
    "send_email":       "Send an email via Gmail",
    "create_event":     "Create a Google Calendar event",
    "log_to_sheet":     "Log data to a Google Sheet",
    "create_doc":       "Create a Google Doc",
    "send_slack":       "Send a Slack message",
    "create_task":      "Create a task (Notion / Todoist / Sheets)",
    "copy_photos_to_drive": "Copy photos/videos from Google Photos to Google Drive",
    "copy_contacts_to_drive": "Fetch Google Contacts and write them to a Google Sheet in Google Drive",
    "search_sheet":     "Search for a query or name inside a specific Google Sheet (e.g. Contacts)",
    "search_image":     "Search the web for an image of a given topic and return it",
    "post_to_facebook": "Publish a post (with image and caption) to Facebook Page",
}


def _normalize_action_name(raw_action) -> str:
    """Reduce arbitrary action payloads to one supported action name."""
    if raw_action is None:
        return ""
    if isinstance(raw_action, list):
        raw_action = raw_action[0] if raw_action else ""
    action_text = str(raw_action).strip().lower()
    if not action_text:
        return ""

    # Handle payloads like "[send_email, create_doc]" or "send_email,create_doc"
    action_text = action_text.strip("[](){}")
    first = re.split(r"[,\s|;/]+", action_text)[0].strip()
    if first in ("send_facebook_post", "facebook_post"):
        first = "post_to_facebook"
    return first if first in MAKE_ACTIONS else ""


def sanitize_single_action_payload(payload: Optional[dict]) -> Optional[dict]:
    """Ensure at most one valid action proceeds per request."""
    if not payload or not isinstance(payload, dict):
        return None
    action_name = _normalize_action_name(payload.get("action"))
    if not action_name:
        return None
    params = payload.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return {"action": action_name, "params": params}

ACTION_DETECTION_PROMPT = (
    "Detect if the user message requests a SINGLE, DIRECT automation action.\n"
    "Actions: send_email, create_event, log_to_sheet, create_doc, send_slack, "
    "create_task, copy_photos_to_drive, copy_contacts_to_drive, search_sheet, search_image, post_to_facebook\n\n"
    "CRITICAL RULES:\n"
    "- If the query is complex, has multiple steps, requires research, asks for a 'report', or is conversational, reply exactly: NO_ACTION\n"
    "- Do NOT classify goals requiring planning, web search, or synthesis as single actions. Reply NO_ACTION.\n"
    "- Only classify direct, single-action commands (e.g. 'send email to x', 'schedule y', 'search sheet z', 'post to facebook') as actions.\n\n"
    "If action found, reply with JSON ONLY containing 'action' and 'params' keys.\n"
    "Params by action: send_email(to,subject,body), create_event(title,date,time,duration,description), "
    "log_to_sheet(sheet_name,data), create_doc(title,content), send_slack(channel,message), "
    "create_task(title,due_date,notes), copy_photos_to_drive(category,folder_name), "
    "copy_contacts_to_drive(sheet_name), search_sheet(sheet_name,query), search_image(query), "
    "post_to_facebook(caption,topic)\n\n"
    "If NO action: reply exactly NO_ACTION"
)


ACTION_TRIGGER_WORDS = {
    "send", "mail", "email", "create", "schedule", "log", "post",
    "write", "book", "copy", "search", "find", "make", "add", "save"
}

def detect_action(message: str, history_text: str = "") -> Optional[dict]:
    """Two-stage detection: zero-token keyword gate, then LLM only if needed."""
    # Stage 1: Zero-token gate â€” skip LLM entirely for obvious non-actions
    msg_lower = message.lower()
    if not any(word in msg_lower for word in ACTION_TRIGGER_WORDS):
        return None
    # Stage 2: LLM detection â€” only reached if keyword gate passed
    try:
        content = ""
        profile_text = get_user_profile_text() if is_profile_relevant_query(message) else ""
        if profile_text:
            content += f"{profile_text}\n\n"
        if history_text:
            content += f"[Recent Conversation History]\n{history_text}\n\n"
        content += f"User's Current Message: {message}"
        
        res = llm_dept.invoke([
            SystemMessage(content=ACTION_DETECTION_PROMPT),
            HumanMessage(content=content),
        ])
        text = res.content.strip()
        if text == "NO_ACTION" or not text.startswith("{"):
            return None
        # Extract JSON even if LLM adds extra text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return None
    except Exception:
        return None



# â”€â”€ Memory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_memory_lock = threading.Lock()
_histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))


def get_history_text(session_id: str) -> str:
    with _memory_lock:
        h = list(_histories[session_id])
    if not h:
        return ""
    return "\n".join(f"{role.upper()}: {content}" for role, content in h)


def add_to_history(session_id: str, user_msg: str, babu_msg: str) -> None:
    with _memory_lock:
        _histories[session_id].append(("user", user_msg))
        _histories[session_id].append(("babu", babu_msg))


def compact_completed_session_history(session_id: str) -> None:
    """Wipe intermediate details from conversation history once the goal epoch is completed.
    
    Keeps only high-level requests and concise summaries to prevent context contamination.
    """
    with _memory_lock:
        history = _histories[session_id]
        if not history:
            return
            
        compacted = deque(maxlen=20)
        for role, content in history:
            if len(content) > 800:
                summary = content[:300] + "\n... [Intermediate details cleared upon successful audit validation] ...\n" + content[-200:]
                compacted.append((role, summary))
            else:
                compacted.append((role, content))
        _histories[session_id] = compacted


def save_k0_memory_entry(session_id: str, goal_id: str, user_query: str, response: str, output_state: dict):
    # Determine status
    goal_graph_dict = output_state.get("goal_graph")
    status = "COMPLETED"
    if goal_graph_dict:
        status = goal_graph_dict.get("status", "COMPLETED")
        if status == "ACTIVE":
            status = "COMPLETED"

    # Collect failures
    failures_list = []
    execution_log = output_state.get("execution_log") or []
    for entry in execution_log:
        if entry.get("status") == "FAILED" or entry.get("error"):
            failures_list.append(f"Task '{entry.get('task_id')}' failed: {entry.get('error') or entry.get('status')}")

    conn, is_pg = get_db_connection()
    if conn:
        cursor = conn.cursor()
        try:
            # We can also check execution_ledger for errors to be absolutely sure
            # (e.g. if the graph failed before tasks ran)
            if is_pg:
                cursor.execute("""
                    SELECT event_type, metadata FROM execution_ledger 
                    WHERE goal_id = %s AND event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id ASC
                """, (goal_id,))
            else:
                cursor.execute("""
                    SELECT event_type, metadata FROM execution_ledger 
                    WHERE goal_id = ? AND event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id ASC
                """, (goal_id,))
            for r in cursor.fetchall():
                failures_list.append(f"{r[0]}: {r[1]}")
        except Exception as e:
            print(f"[K0 MEMORY ERROR] Failed to query failures: {e}", flush=True)

        failures_str = "; ".join(failures_list) if failures_list else None

        # Collect retrieved records summary
        retrieved_parts = []
        try:
            if is_pg:
                cursor.execute("SELECT metadata FROM execution_ledger WHERE goal_id = %s AND event_type = 'RAG_RETRIEVAL'", (goal_id,))
            else:
                cursor.execute("SELECT metadata FROM execution_ledger WHERE goal_id = ? AND event_type = 'RAG_RETRIEVAL'", (goal_id,))
            for r in cursor.fetchall():
                try:
                    meta = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                    if meta and meta.get("retrieved_tokens", 0) > 0:
                        retrieved_parts.append(f"RAG Knowledge (tokens: {meta.get('retrieved_tokens')})")
                except Exception:
                    pass
        except Exception as e:
            print(f"[K0 MEMORY ERROR] Failed to query RAG ledger: {e}", flush=True)

        # Check what SQL memories were retrieved based on user query keywords
        q_lower = user_query.lower()
        if any(k in q_lower for k in ("who is", "profile", "identity", "about me", "preferences", "interest")):
            retrieved_parts.append("K3 - User Profile")
        if any(k in q_lower for k in ("goal", "query", "run", "request", "task list", "dag")):
            retrieved_parts.append("K2 - Runtime Goals")
        if any(k in q_lower for k in ("fail", "error", "reject", "violation", "why did", "problem", "warn")):
            retrieved_parts.append("K2 - Runtime Failures")
        if any(k in q_lower for k in ("timeline", "happen", "change", "unresolved", "recent", "chronology", "status", "history")):
            retrieved_parts.append("K2 - Runtime Timeline")
        if any(k in q_lower for k in ("rule", "anti-pattern", "immune", "lesson", "pattern", "governance")):
            retrieved_parts.append("K5 - Architecture Anti-Patterns")
        if any(k in q_lower for k in ("template", "etemp", "promoted", "compiled")):
            retrieved_parts.append("K4 - Execution Templates")
        if any(k in q_lower for k in ("adr", "architecture", "tradeoff", "postmortem", "lesson", "evolution", "upgrades", "gemini", "dynamic import", "runtime index", "supersede", "impact_score", "milestone", "hierarchy")):
            retrieved_parts.append("K5 - Architecture Knowledge System (AKS)")

        retrieved_str = ", ".join(retrieved_parts) if retrieved_parts else None

        try:
            if is_pg:
                cursor.execute("""
                    INSERT INTO babu_k0_working_memory (session_id, goal_id, user_query, response, status, failures, retrieved_records)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (session_id, goal_id, user_query, response, status, failures_str, retrieved_str))
            else:
                cursor.execute("""
                    INSERT INTO babu_k0_working_memory (session_id, goal_id, user_query, response, status, failures, retrieved_records)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (session_id, goal_id, user_query, response, status, failures_str, retrieved_str))
            conn.commit()
        except Exception as e:
            print(f"[K0 MEMORY ERROR] Failed to save K0 working memory: {e}", flush=True)
        finally:
            cursor.close()
            conn.close()


def retrieve_k0_memory(session_id: str, limit: int = 5) -> str:
    conn, is_pg = get_db_connection()
    if not conn:
        return ""
    cursor = conn.cursor()
    context = ""
    try:
        if is_pg:
            cursor.execute("""
                SELECT timestamp, goal_id, user_query, response, status, failures, retrieved_records
                FROM babu_k0_working_memory
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
            """, (session_id, limit))
        else:
            cursor.execute("""
                SELECT timestamp, goal_id, user_query, response, status, failures, retrieved_records
                FROM babu_k0_working_memory
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
            """, (session_id, limit))
        rows = cursor.fetchall()
        if rows:
            rows.reverse()
            parts = []
            for r in rows:
                ts = r[0]
                ts_str = ts.strftime('%Y-%m-%d %H:%M:%S') if hasattr(ts, 'strftime') else str(ts)
                goal_id = r[1]
                query = r[2]
                response = r[3]
                status = r[4]
                failures = r[5]
                retrieved = r[6]

                block = (
                    f"[{ts_str}] Goal ID: {goal_id} | Status: {status}\n"
                    f"- User Query: {query}\n"
                    f"- Response: {response}"
                )
                if failures:
                    block += f"\n- Failures: {failures}"
                if retrieved:
                    block += f"\n- Retrieved Records: {retrieved}"
                parts.append(block)
            context = "=== K0 - CONVERSATIONAL WORKING MEMORY (Recent Goals & Turns) ===\n" + "\n\n".join(parts)
    except Exception as e:
        print(f"[K0 RETRIEVAL ERROR] Failed: {e}", flush=True)
    finally:
        cursor.close()
        conn.close()
    return context


def extract_tokens(res) -> dict:
    """Safely extract prompt, completion, and total tokens from an LLM response."""
    usage = {"prompt": 0, "completion": 0, "total": 0}
    if not res:
        return usage
        
    # 1. Try unified usage_metadata field (Standard in newer LangChain)
    usage_meta = getattr(res, "usage_metadata", None)
    if usage_meta:
        usage["prompt"] = usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0) or 0
        usage["completion"] = usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0) or 0
        usage["total"] = usage_meta.get("total_tokens", 0) or (usage["prompt"] + usage["completion"])
        return usage

    # 2. Try response_metadata -> token_usage (OpenAI / Groq)
    metadata = getattr(res, "response_metadata", {})
    token_usage = metadata.get("token_usage")
    if token_usage:
        usage["prompt"] = token_usage.get("prompt_tokens", 0)
        usage["completion"] = token_usage.get("completion_tokens", 0)
        usage["total"] = token_usage.get("total_tokens", 0)
        return usage

    return usage


def add_tokens(existing: dict, new: dict) -> dict:
    """LangGraph reducer to sum up cumulative token usage across swarm nodes."""
    if not existing:
        existing = {"prompt": 0, "completion": 0, "total": 0}
    if not new:
        return existing
    return {
        "prompt": existing.get("prompt", 0) + new.get("prompt", 0),
        "completion": existing.get("completion", 0) + new.get("completion", 0),
        "total": existing.get("total", 0) + new.get("total", 0)
    }


# â”€â”€ LangGraph state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class BabuState(TypedDict):
    messages:       Annotated[list[BaseMessage], "Conversation"]
    research_data:  List[str]
    user_query:     str
    history_text:   str
    session_id:     str
    search_results: str
    action_result:  str   # result of direct Google Workspace action if triggered
    tokens:         Annotated[dict, add_tokens]
    detected_action: Optional[dict]
    active_goal:    Optional[dict]
    execution_tracker: dict
    compressed_research: str
    routing_metadata: dict
    pending_action_notice: str
    goal_graph:     Optional[dict]
    execution_log:  list[dict]
    final_brief:    str
    knowledge_classes: Optional[List[str]]
    source_records: Optional[List[str]]
    conversation_reference: Optional[bool]
    is_deterministic_response: Optional[bool]


_pending_actions_lock = threading.Lock()
_pending_actions: dict[str, dict] = {}


def _is_approval_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"1", "yes", "approve", "approved", "ok", "confirm", "proceed"}


def _is_reject_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"0", "2", "no", "cancel", "reject", "stop"}


# â”€â”€ Nodes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def should_escalate_to_workflow(text: str, history_text: str = "") -> bool:
    """Classify user query to separate conversational interactions from complex workflows.
    
    Returns True ONLY if the query explicitly requests swarm orchestration, or is detected
    as a dynamic correction/rerun of a previous orchestration workflow in history.
    """
    t = (text or "").lower().strip()
    
    # Check slash/exclamation command prefixes
    if t.startswith("/") or t.startswith("!"):
        cmd = t.removeprefix("/").removeprefix("!")
        if cmd.startswith("launch") or cmd.startswith("sprint") or cmd.startswith("postnow"):
            return True
        return False
        
    # Check raw keyword prefix triggers
    if t.startswith("launch") or t.startswith("sprint") or t.startswith("postnow"):
        return True
        
    # Dynamic Goal Correction & Rerun Detection:
    # If the user has a recent workflow in history, scan for corrective pattern keywords
    history_lower = (history_text or "").lower()
    has_recent_workflow = "launch" in history_lower or "sprint" in history_lower or "goal" in history_lower or "graph" in history_lower
    if has_recent_workflow:
        correction_keywords = {
            "meant", "instead", "no not", "correct to", "change to", "wrong", 
            "typo", "error", "re-run", "rerun", "relaunch", "run again",
            "correct the topic", "not monetary", "should be"
        }
        if any(kw in t for kw in correction_keywords):
            print(f"[ROUTER] Dynamic escalation: Detected goal correction pattern in query: '{text}'", flush=True)
            return True
        
    return False


def intent_router(state: BabuState):
    import time
    _router_t0 = time.time()
    
    query = state["messages"][-1].content
    history_text = state.get("history_text", "")
    lowered = query.lower().strip()
    session_id = state.get("session_id", "default")

    # Strip command prefix overrides to keep the processed query clean
    clean_query = query
    t_lower = query.lower().strip()
    for prefix in ("/launch", "!launch", "launch", "/sprint", "!sprint", "sprint", "/walk", "!walk", "walk"):
        if t_lower.startswith(prefix):
            clean_query = query[len(prefix):].strip()
            break

    try:
        from .planner import classify_intent
    except ImportError:
        from planner import classify_intent

    intent_packet = classify_intent(clean_query, history_text, model_name=CURRENT_PA_MODEL)
    ic_tokens = getattr(intent_packet, "tokens", None) or {"prompt": 0, "completion": 0, "total": 0}

    detected_action = None
    pending_action_notice = ""

    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending and _is_approval_message(query):
        detected_action = pending
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
    elif pending and _is_reject_message(query):
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        pending_action_notice = "Pending action cancelled."
    elif pending:
        pending_action_notice = "You already have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard."

    try:
        try:
            from .memory import log_routing_decision
        except ImportError:
            from memory import log_routing_decision
        log_routing_decision(
            session_id=state.get("session_id", "default"),
            query=query,
            selected_gear="DYNAMIC",
            reason="routing_dispatch",
            has_action=bool(detected_action),
        )
    except Exception as e:
        print(f"[ROUTING MEMORY WARNING] Failed to log routing decision: {e}", flush=True)

    _router_duration = round(time.time() - _router_t0, 4)
    tracker = state.get("execution_tracker") or {
        "start_time": time.time(),
        "router_duration": 0.0,
        "planner_duration": 0.0,
        "executor_duration": 0.0,
        "pa_duration": 0.0,
        "governance_duration": 0.0,
        "task_latencies": [],
    }
    tracker["router_duration"] = _router_duration
    return {
        "user_query": clean_query,
        "research_data": [],
        "search_results": "",
        "action_result": "",
        "detected_action": detected_action,
        "execution_tracker": tracker,
        "compressed_research": "",
        "routing_metadata": {
            "mode": "command_only",
            "reason": "explicit_command",
            "intent_packet": intent_packet.to_dict() if intent_packet else None
        },
        "pending_action_notice": pending_action_notice,
        "tokens": ic_tokens
    }


def is_pure_greeting(text: str) -> bool:
    t = (text or "").lower().strip()
    t = t.removeprefix("/").removeprefix("!")
    for char in "?!.,":
        t = t.replace(char, "")
    t = t.strip()
    
    greetings = {
        "hi", "hello", "hey", "how are you", "how's it going", "how you doing", 
        "how doing", "yo", "hi buddy", "hey buddy", "hello buddy", "good morning", 
        "good afternoon", "good evening"
    }
    return t in greetings


def is_deterministic_faq_query(query: str) -> bool:
    t = (query or "").lower().strip()
    t = t.removeprefix("/").removeprefix("!")
    for char in "?!.,":
        t = t.replace(char, "")
    t = t.strip()
    
    faq_keywords = (
        "current time", "time in ist", "time here in ist", "what is the time", "what time is it",
        "how old are you", "how old you are", "your age", "what is your age", "date of birth", "dob of babu",
        "who are you", "tell me about yourself", "about yourself", "know about yourself", "about you", "tell me about you", "know about you",
        "describe yourself", "introduce yourself", "your identity", "what is your name",
        "your architecture", "tell me about your architecture", "how are you built", "how do you work",
        "failures happened", "recent failures", "what are failures", "failures in last", "failures happened in last",
        "system health", "status dashboard", "how are you doing", "what is your status", "health dashboard",
        "current state", "your current state", "what is your current state", "system status", "system status dashboard",
        "upgrades received", "recent upgrades", "what upgrades", "upgrades did you receive", "upgrades did you recieve", "upgrades in last",
        "upgrade received", "recent upgrade", "what upgrade", "upgrade did you receive", "upgrade did you recieve", "upgrade in last",
        "tradeoff", "tradeoffs", "architectural tradeoffs", "architectural tradeoff",
        "highest impact", "largest impact", "biggest impact", "most impact",
        "evolution", "evolve", "history", "timeline", "adr", "architecture decision",
        "solve", "incident", "postmortem", "lesson", "milestone"
    )
    return any(k in t for k in faq_keywords)


def has_multiple_tasks_or_requests(query: str, intent_packet_dict: Optional[dict] = None) -> bool:
    import re
    t = (query or "").lower().strip()
    
    # 1. Check intent packet indicators for multiple actions or core departments
    if intent_packet_dict:
        allowed_depts = intent_packet_dict.get("allowed_departments", [])
        allowed_actions = intent_packet_dict.get("allowed_actions", [])
        
        # Core departments are everything except "pa" and non-mutating "execution"
        mutating_actions = {
            "send_email", "create_event", "log_to_sheet", "create_doc", 
            "copy_photos_to_drive", "copy_contacts_to_drive", 
            "send_slack", "create_task", "post_to_facebook", "generate_image"
        }
        has_mutating = any(act in allowed_actions for act in mutating_actions)
        core_depts = [d for d in allowed_depts if d != "pa" and (d != "execution" or has_mutating)]
        if len(core_depts) > 1:
            return True
            
        # Multiple actions requested (only count active mutating actions)
        mutating_actions = {
            "send_email", "create_event", "log_to_sheet", "create_doc", 
            "copy_photos_to_drive", "copy_contacts_to_drive", 
            "send_slack", "create_task", "post_to_facebook", "generate_image"
        }
        active_mutating_actions = [act for act in allowed_actions if act in mutating_actions]
        if len(active_mutating_actions) > 1:
            return True
            
        # If we have an execution action and another informational/research department active
        has_execution = len(active_mutating_actions) > 0
        if has_execution and ("information" in allowed_depts or "research" in allowed_depts):
            return True

    # 2. Check text-based checks for conjunctions and multiple verbs/requests
    conjunction_patterns = [r'\band\b', r'\balso\b', r'\bthen\b', r'\bplus\b', r'\balong with\b', r'\bas well as\b', r';']
    has_conjunction = any(re.search(pat, t) for pat in conjunction_patterns)
    
    if has_conjunction:
        parts = re.split(r'\band\b|\balso\b|\bthen\b|\bplus\b|\balong with\b|\bas well as\b|;', t)
        parts = [p.strip() for p in parts if p.strip()]
        
        valid_requests_count = 0
        for part in parts:
            if len(part) < 4:
                continue
            request_keywords = (
                "current time", "time in ist", "what time", "what is the time",
                "how old", "your age", "date of birth", "dob", "who are you", "about yourself",
                "system health", "status dashboard", "system status", "upgrades", "upgrade",
                "tradeoff", "highest impact", "evolution", "evolve", "history", "timeline",
                "send", "email", "mail", "create", "event", "calendar", "log", "sheet", "spreadsheet",
                "document", "doc", "slack", "post", "facebook", "search", "find", "look up", "lookup",
                "tell", "check", "show", "who", "what", "where", "when", "how", "why"
            )
            if any(k in part for k in request_keywords):
                valid_requests_count += 1
                
        if valid_requests_count > 1:
            return True
            
    # 3. Check for multiple distinct FAQ/system query categories in the same query
    faq_types_present = set()
    if any(k in t for k in ("current time", "time in ist", "time here in ist", "what is the time", "what time is it")):
        faq_types_present.add("time")
    if any(k in t for k in ("how old are you", "how old you are", "your age", "what is your age", "date of birth", "dob of babu")):
        faq_types_present.add("age")
    if any(k in t for k in ("who are you", "tell me about yourself", "about yourself", "your identity", "what is your name")):
        faq_types_present.add("identity")
    if any(k in t for k in ("system health", "status dashboard", "health dashboard", "system status")):
        faq_types_present.add("health")
    if any(k in t for k in ("upgrades", "upgrade", "adr", "tradeoff", "highest impact", "evolution", "evolve", "history", "timeline", "incident")):
        faq_types_present.add("system")
        
    if len(faq_types_present) > 1:
        return True

    # 4. Check if there is an FAQ query keyword AND a workspace/web search requirement
    has_faq = any(k in t for k in (
        "current time", "time in ist", "what is the time", "what time is it",
        "how old are you", "your age", "date of birth", "dob of babu",
        "who are you", "tell me about yourself", "your identity", "what is your name",
        "system health", "status dashboard", "system status",
        "upgrades", "upgrade", "tradeoff", "highest impact", "evolution", "history", "timeline"
    ))
    if has_faq:
        if requires_workspace_access(query) or requires_web_search(query):
            return True
            
    return False


def route_after_router(state: BabuState) -> str:
    notice = state.get("pending_action_notice", "")
    if notice:
        return "pending"
    
    query = state.get("user_query", "")
    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    
    query_category = None
    if intent_packet_dict:
        query_category = intent_packet_dict.get("query_category")
        
    # Deterministic AKS/FAQ/system queries short-circuit BEFORE private-category plan-forcing.
    # These queries are answered from the database or in-memory with zero LLM tokens.
    if (is_pure_greeting(query) or is_deterministic_faq_query(query)) and not has_multiple_tasks_or_requests(query, intent_packet_dict):
        print(f"[ROUTE AFTER ROUTER] Deterministic FAQ/greeting detected for query: '{query}'. Short-circuiting directly to PA node.", flush=True)
        return "pa"

    # Only force plan route for private queries that are NOT deterministic FAQ/AKS.
    PRIVATE_QUERY_TYPES = ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION")
    if query_category in PRIVATE_QUERY_TYPES:
        print(f"[ROUTE AFTER ROUTER] Private category '{query_category}' detected. Disabling PA direct response bypass and forcing plan route.", flush=True)
        return "plan"
        
    return "plan"


def is_simple_query(text: str) -> bool:
    t = (text or "").lower().strip()
    # Remove common command prefixes
    t = t.removeprefix("/").removeprefix("!")
    
    # Common greetings, basic phrases, and stats
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 15:
        return True
    return False


def requires_workspace_access(query: str) -> bool:
    t = query.lower()
    pattern = r'\b(mail|email|gmail|sheet|sheets|spreadsheet|spreadsheets|calendar|calendars|event|events|meeting|meetings|slack|contact|contacts|photos|drive)\b'
    return bool(re.search(pattern, t))


def get_babu_age_string() -> str:
    from datetime import datetime, timezone
    dob = datetime(2026, 5, 27, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dob
    days = diff.days
    if days < 0:
        return "recently launched"
    
    years = days // 365
    remaining_days = days % 365
    months = remaining_days // 30
    remaining_days = remaining_days % 30
    
    parts = []
    if years > 0:
        parts.append(f"{years} year" + ("s" if years > 1 else ""))
    if months > 0:
        parts.append(f"{months} month" + ("s" if months > 1 else ""))
    if remaining_days > 0 or not parts:
        parts.append(f"{remaining_days} day" + ("s" if remaining_days > 1 else ""))
        
    return " and ".join(parts) if len(parts) == 2 else ", ".join(parts)


def get_dynamic_self_identity() -> str:
    """Retrieve the operational identity of the system based on the Runtime Index."""
    try:
        # Determine enabled services
        enabled_services = []
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            enabled_services.append("Telegram Interface")
        if os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN"):
            enabled_services.append("Facebook Publishing")
        if is_google_configured():
            enabled_services.append("Google Workspace")
        enabled_services.append("Web Dashboard")
        
        services_str = ", ".join(enabled_services) if enabled_services else "None"
        
        identity_text = (
            f"=== 👤 IDENTITY INDEX ===\n"
            f"**Name:** Project BABU (Behavioral Autonomous Bureaucratic Utility)\n"
            f"**Version:** 3.5.0\n"
            f"**Purpose:** Next-generation AI agentic assistant designed to automate research, analysis, writing, and Google Workspace execution tasks using a decentralized swarm architecture.\n\n"
            f"**Capabilities:**\n"
            f"- Active PA Model: `{CURRENT_PA_MODEL}`\n"
            f"- Active Department Model: `{CURRENT_DEPT_MODEL}`\n"
            f"- Enabled Services: {services_str}\n\n"
            f"**Architecture:**\n"
            f"LangGraph-based decentralized swarm framework:\n"
            f"- Router Node: Evaluates query intent & directs routing.\n"
            f"- Planner Node: Generates topologically sorted execution DAGs.\n"
            f"- Task Engine: Orchestrates task status transitions.\n"
            f"- Swarm Departments: Research, Information, Analysis, Writing, Execution.\n"
            f"- Governance Gatekeepers: Pre-Execution Gatekeeper, Post-Execution Validator, and Epistemic Immune System.\n"
            f"- Cache layer: E[Temp] compiled templates."
        )
        return identity_text
    except Exception as e:
        print(f"[DYNAMIC IDENTITY ERROR] {e}", flush=True)
        return "I am **Project BABU**, a governed multi-agent assistant. (Identity details currently unavailable)."


def get_system_health_dashboard() -> str:
    """Generate a comprehensive real-time System Health & Self-Audit Dashboard."""
    import os
    import json
    import time
    from datetime import datetime, timezone
    import requests
    
    PRICING_TABLE = {
        "gemini-2.5-pro": (1.25, 5.00),
        "gemini-2.5-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 5.00),
        "gemini-1.5-flash": (0.075, 0.30),
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "llama-3.1-70b-versatile": (0.59, 0.79),
        "llama-3.1-8b-instant": (0.05, 0.08),
        "llama3-70b-8192": (0.59, 0.79),
        "llama3-8b-8208": (0.05, 0.08),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.150, 0.600),
        "o1-mini": (3.00, 12.00)
    }
    
    def get_token_costs(model_name: str) -> tuple[float, float]:
        if not model_name:
            return 0.15 / 1_000_000, 0.60 / 1_000_000
        m_lower = model_name.lower().strip()
        for key, rates in PRICING_TABLE.items():
            if key in m_lower:
                return rates[0] / 1_000_000, rates[1] / 1_000_000
        return 0.15 / 1_000_000, 0.60 / 1_000_000

    def parse_db_timestamp(ts_str):
        if not ts_str:
            return None
        try:
            cleaned = ts_str.strip()
            if "." in cleaned:
                parts = cleaned.split(".")
                sec_part = parts[1]
                suffix = ""
                if sec_part.endswith("Z"):
                    suffix = "Z"
                    sec_part = sec_part[:-1]
                elif "+" in sec_part:
                    sec_part, suffix = sec_part.split("+", 1)
                    suffix = "+" + suffix
                sec_part = sec_part[:6] # microsecond limit
                cleaned = parts[0] + "." + sec_part + suffix
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            return datetime.fromisoformat(cleaned)
        except Exception:
            return None

    def format_last_success(last_time):
        if not last_time:
            return "Never"
        diff = time.time() - last_time
        if diff < 60:
            return "<1 min ago"
        mins = int(diff // 60)
        if mins < 60:
            return f"{mins} min ago"
        hours = int(mins // 60)
        if hours < 24:
            return f"{hours} hours ago"
        return f"{int(hours // 24)} days ago"

    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Goal counts
        cursor.execute("SELECT COUNT(DISTINCT goal_id) FROM execution_ledger WHERE event_type = 'GOAL_COMPLETED'")
        completed_goals = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(DISTINCT goal_id) FROM execution_ledger WHERE event_type = 'GOAL_FAILED'")
        failed_goals = cursor.fetchone()[0] or 0
        
        total_goals = completed_goals + failed_goals
        success_rate = (completed_goals / total_goals * 100) if total_goals > 0 else 100.0
        
        # 2. Tokens, Cost, Governance Blocks, and failures
        cursor.execute("SELECT event_type, metadata FROM execution_ledger")
        rows = cursor.fetchall()
        
        total_tokens = 0
        total_cost = 0.0
        gov_blocks = 0
        last_failure = "None recently"
        
        for ev_type, meta_str in rows:
            if ev_type in ("AUDIT_PRE_FAIL", "AUDIT_POST_FAIL", "PLANNER_CONSTRAINT_VIOLATION"):
                gov_blocks += 1
            
            if ev_type in ("EXECUTION_FAIL", "AUDIT_PRE_FAIL", "AUDIT_POST_FAIL", "PLANNER_CONSTRAINT_VIOLATION"):
                if last_failure == "None recently":
                    last_failure = f"{ev_type}"
                    if meta_str:
                        try:
                            meta = json.loads(meta_str)
                            reason = meta.get("reason") or meta.get("error") or meta.get("details") or ""
                            if reason:
                                last_failure += f" ({reason[:60]})"
                        except Exception:
                            pass
            
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                    tokens = meta.get("tokens")
                    if tokens and isinstance(tokens, dict):
                        prompt = tokens.get("prompt", 0) or 0
                        completion = tokens.get("completion", 0) or 0
                        total_t = tokens.get("total", 0) or (prompt + completion)
                        total_tokens += total_t
                        
                        model_name = meta.get("model", "")
                        p_rate, c_rate = get_token_costs(model_name)
                        total_cost += (prompt * p_rate) + (completion * c_rate)
                except Exception:
                    pass

        # Rolling success rates (Last 100 goals and Last 24 hours)
        cursor.execute("""
            SELECT timestamp, goal_id, event_type 
            FROM execution_ledger 
            WHERE event_type IN ('GOAL_COMPLETED', 'GOAL_FAILED') 
            ORDER BY event_id DESC
        """)
        goal_rows = cursor.fetchall()
        
        goals_seen = {}
        for ts_str, gid, ev_type in goal_rows:
            if gid not in goals_seen:
                goals_seen[gid] = (ts_str, ev_type == "GOAL_COMPLETED")
                
        parsed_goals = []
        for gid, (ts_str, succ) in goals_seen.items():
            dt = parse_db_timestamp(ts_str)
            parsed_goals.append((dt, succ))
            
        parsed_goals.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        
        # Last 100
        last_100 = parsed_goals[:100]
        completed_100 = sum(1 for dt, succ in last_100 if succ)
        total_100 = len(last_100)
        success_rate_100 = (completed_100 / total_100 * 100) if total_100 > 0 else 100.0
        
        # Last 24h
        now_utc = datetime.now(timezone.utc)
        last_24h = [g for g in parsed_goals if g[0] and (now_utc - g[0]).total_seconds() <= 86400]
        completed_24h = sum(1 for dt, succ in last_24h if succ)
        total_24h = len(last_24h)
        success_rate_24h = (completed_24h / total_24h * 100) if total_24h > 0 else 100.0

        # Unresolved Failures Check
        cursor.execute("""
            SELECT event_type, timestamp, metadata 
            FROM execution_ledger 
            WHERE event_type IN ('EXECUTION_FAIL', 'AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'PLANNER_CONSTRAINT_VIOLATION') 
            ORDER BY event_id DESC LIMIT 1
        """)
        last_fail_row = cursor.fetchone()
        
        cursor.execute("""
            SELECT timestamp 
            FROM execution_ledger 
            WHERE event_type = 'GOAL_COMPLETED' 
            ORDER BY event_id DESC LIMIT 1
        """)
        last_comp_row = cursor.fetchone()
        
        last_failure_type = None
        last_failure_ts = None
        last_failure_reason = ""
        
        if last_fail_row:
            last_failure_type = last_fail_row[0]
            last_failure_ts = parse_db_timestamp(last_fail_row[1])
            if last_fail_row[2]:
                try:
                    meta = json.loads(last_fail_row[2])
                    last_failure_reason = meta.get("reason") or meta.get("error") or meta.get("details") or ""
                except Exception:
                    pass
        
        last_comp_ts = parse_db_timestamp(last_comp_row[0]) if last_comp_row else None
        
        is_unresolved = False
        if last_failure_ts:
            if not last_comp_ts or last_failure_ts > last_comp_ts:
                is_unresolved = True

        if is_unresolved:
            failures_status_str = "DEGRADED (Active issues detected)"
            failures_nodes_str = (
                f"- {last_failure_type or 'System Error'}: {last_failure_reason or 'No details'}\n"
                f"  * Status: Active (Unresolved)\n"
                f"  * Timestamp: {last_fail_row[1] if last_fail_row else 'Unknown'}"
            )
        else:
            failures_status_str = "All systems operational"
            failures_nodes_str = "- No active issues detected"
            if last_fail_row:
                failures_nodes_str += f"\n- Last failure: {last_failure_type} ({last_failure_reason})\n  * Status: Resolved (Subsequent goals succeeded)"

        # Last goal query
        cursor.execute("SELECT metadata FROM execution_ledger WHERE event_type = 'GOAL_RECEIVED' ORDER BY event_id DESC LIMIT 1")
        last_goal_row = cursor.fetchone()
        last_goal = "None"
        if last_goal_row and last_goal_row[0]:
            try:
                meta = json.loads(last_goal_row[0])
                last_goal = meta.get("query") or meta.get("goal") or "System awareness check"
            except Exception:
                pass
                
        # Last completed goal query
        cursor.execute("SELECT metadata FROM execution_ledger WHERE event_type = 'GOAL_COMPLETED' ORDER BY event_id DESC LIMIT 1")
        last_completed_row = cursor.fetchone()
        last_completed = "None"
        if last_completed_row and last_completed_row[0]:
            try:
                meta = json.loads(last_completed_row[0])
                last_completed = meta.get("query") or meta.get("goal") or "System awareness check"
            except Exception:
                pass

        cursor.close()
        conn.close()
        db_status = "ONLINE"
    except Exception as e:
        db_status = f"OFFLINE ({str(e)[:40]})"
        total_goals = completed_goals = failed_goals = gov_blocks = 0
        success_rate = success_rate_100 = success_rate_24h = 100.0
        total_tokens = 0
        total_cost = 0.0
        last_goal = last_completed = "Unknown (DB Offline)"
        last_failure = "Unknown (DB Offline)"
        failures_status_str = f"DB Connection Failure: {str(e)[:40]}"
        failures_nodes_str = f"- Error connecting to database: {str(e)}"

    # Check Transport/Services status
    global LAST_TELEGRAM_SUCCESS_TIME, LAST_FB_SUCCESS_TIME, LAST_GOOGLE_SUCCESS_TIME, LAST_WEB_SUCCESS_TIME

    # Telegram
    telegram_configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_operational = False
    telegram_reason = "Not configured"
    if telegram_configured:
        if tg_application and getattr(tg_application, "running", False):
            try:
                token = os.environ.get("TELEGRAM_BOT_TOKEN")
                r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=1.5)
                if r.status_code == 200:
                    telegram_operational = True
                    telegram_reason = "Running & connected"
                    LAST_TELEGRAM_SUCCESS_TIME = time.time()
                else:
                    telegram_reason = f"API error (HTTP {r.status_code})"
            except Exception as e:
                telegram_reason = f"API unreachable: {str(e)[:25]}"
        else:
            telegram_reason = "Polling not active"

    # Facebook
    fb_configured = bool(os.environ.get("FACEBOOK_PAGE_ID") and os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN"))
    fb_operational = False
    fb_reason = "Not configured"
    if fb_configured:
        try:
            page_id = os.environ.get("FACEBOOK_PAGE_ID")
            page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
            r = requests.get(f"https://graph.facebook.com/v19.0/{page_id}?access_token={page_token}", timeout=1.5)
            if r.status_code == 200:
                fb_operational = True
                fb_reason = "API authorized"
                LAST_FB_SUCCESS_TIME = time.time()
            else:
                fb_reason = f"API error (HTTP {r.status_code})"
        except Exception as e:
            fb_reason = f"API unreachable: {str(e)[:25]}"

    # Google Workspace
    google_configured = is_google_configured()
    google_operational = False
    google_reason = "Not configured"
    if google_configured:
        try:
            from google_service import get_google_creds
            creds = get_google_creds()
            if creds and (creds.valid or creds.refresh_token):
                google_operational = True
                google_reason = "OAuth Authorized"
                LAST_GOOGLE_SUCCESS_TIME = time.time()
            else:
                google_reason = "Token expired/invalid"
        except Exception as e:
            google_reason = f"Auth check failed: {str(e)[:25]}"

    # Web Dashboard
    web_configured = True
    web_operational = False
    web_reason = "Server thread not active"
    for th in threading.enumerate():
        if th.name == "web_dashboard_health_server" and th.is_alive():
            web_operational = True
            web_reason = "Running"
            LAST_WEB_SUCCESS_TIME = time.time()
            break

    # Calculate uptime
    uptime_sec = time.time() - BOT_START_TIME
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    secs = int(uptime_sec % 60)
    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}d")
    if hours > 0:
        uptime_parts.append(f"{hours}h")
    if mins > 0:
        uptime_parts.append(f"{mins}m")
    uptime_parts.append(f"{secs}s")
    uptime_str = " ".join(uptime_parts)

    block_rate = (gov_blocks / total_goals * 100) if total_goals > 0 else 0.0
    age_str = get_babu_age_string()

    dashboard = (
        f"=== 📊 SYSTEM HEALTH & SELF-AUDIT DASHBOARD ===\n\n"
        f"**System Health Status**\n"
        f"- Overall Health: {failures_status_str}\n\n"
        f"**Identity**\n"
        f"- Name: Project BABU\n"
        f"- Version: 3.5.0\n"
        f"- System Age: {age_str}\n"
        f"- Current Process Uptime: {uptime_str}\n\n"
        f"**Capabilities**\n"
        f"- Active PA Model: `{CURRENT_PA_MODEL}`\n"
        f"- Active Department Model: `{CURRENT_DEPT_MODEL}`\n\n"
        f"**Services & Transport Layers**\n"
        f"- Telegram:\n"
        f"  * Configured: {'Yes' if telegram_configured else 'No'}\n"
        f"  * Operational: {'Yes' if telegram_operational else 'No'}\n"
        f"  * Reason: {telegram_reason}\n"
        f"  * Last Success: {format_last_success(LAST_TELEGRAM_SUCCESS_TIME)}\n"
        f"- Facebook Publishing:\n"
        f"  * Configured: {'Yes' if fb_configured else 'No'}\n"
        f"  * Operational: {'Yes' if fb_operational else 'No'}\n"
        f"  * Reason: {fb_reason}\n"
        f"  * Last Success: {format_last_success(LAST_FB_SUCCESS_TIME)}\n"
        f"- Google Workspace:\n"
        f"  * Configured: {'Yes' if google_configured else 'No'}\n"
        f"  * Operational: {'Yes' if google_operational else 'No'}\n"
        f"  * Reason: {google_reason}\n"
        f"  * Last Success: {format_last_success(LAST_GOOGLE_SUCCESS_TIME)}\n"
        f"- Web Dashboard:\n"
        f"  * Configured: Yes\n"
        f"  * Operational: {'Yes' if web_operational else 'No'}\n"
        f"  * Reason: {web_reason}\n"
        f"  * Last Success: {format_last_success(LAST_WEB_SUCCESS_TIME)}\n\n"
        f"**Telemetry & Goals**\n"
        f"- Goals Received: {total_goals}\n"
        f"- Completed Goals: {completed_goals}\n"
        f"- Failed Goals: {failed_goals}\n"
        f"- Governance Blocks: {gov_blocks}\n"
        f"- Block Rate: {block_rate:.1f}%\n"
        f"- Lifetime Success Rate: {success_rate:.1f}%\n"
        f"- Last 100 Goals Success Rate: {success_rate_100:.1f}%\n"
        f"- Last 24h Success Rate: {success_rate_24h:.1f}%\n"
        f"- Tokens Processed: {total_tokens:,}\n"
        f"- Cost Incurred: ${total_cost:,.4f}\n\n"
        f"**Recent Activity**\n"
        f"- Last Goal: {last_goal}\n"
        f"- Last Completed Goal: {last_completed}\n"
        f"- Last Failure: {last_failure}\n\n"
        f"**Failure Nodes**\n"
        f"- Status: {failures_status_str}\n"
        f"{failures_nodes_str}"
    )
    return dashboard

def get_babu_self_context(session_id: str = "default") -> str:
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    import sys
    
    age_str = get_babu_age_string()
    
    k0_ctx = retrieve_k0_memory(session_id)
    k0_part = f"{k0_ctx}\n\n" if k0_ctx else ""
    
    return f"""{k0_part}=== BABU SELF CONTEXT ===
- Name: Project BABU (Behavioral Autonomous Bureaucratic Utility)
- Date of Birth (Creation): May 27, 2026 (Launch epoch)
- Age: {age_str} (exactly { (now_utc - datetime(2026, 5, 27, tzinfo=timezone.utc)).days } days since creation)
- Purpose: Next-generation AI agentic assistant designed to automate research, analysis, writing, and Google Workspace execution tasks using a decentralized swarm architecture.
- Architecture: LangGraph-based decentralized swarm framework.
  * Router Node: Evaluates query intent and directs requests.
  * Planner Node: Generates execution DAGs (GoalGraph) topologically sorted.
  * Task Engine: Orchestrates TaskDTO status states.
  * Departments: Research, Information, Analysis, Writing, Execution.
  * Governance: Bipartite Auditor (PreExecutionGatekeeper, PostExecutionValidator) and Epistemic Immune System.
  * Compiled Cognition: E[Temp] trusted templates for speed-up match caching.
- Operating Environment: Python {sys.version.split()[0]} on Windows.
- Authoritative Knowledge: Automated tax, compliance (PF, GST, CSC services), and e-governance assistant.
"""


def is_system_aware_query(query: str) -> bool:
    """Determine if a query is related to BABU's codebase, architecture, templates, governance, or self-identity."""
    q = query.lower()
    keywords = {
        "etemp", "governance", "auditor", "anti-pattern", "failures", "telemetry",
        "self-awareness", "self-rag", "architecture", "codebase", "immune lesson",
        "why did this task fail", "why did my task fail", "why did task fail",
        "how does babu work", "how do you work", "bipartite auditor", "what governance rule",
        "what recurring problems", "what fixes were previously applied",
        "about yourself", "know about yourself", "describe yourself", "about you", "tell me about you", "know about you",
        "introduce yourself", "who are you", "what is your name", "your identity",
        "how old are you", "your age", "date of birth", "dob of babu",
        "adr", "architecture decision", "tradeoff", "lessons learned", "evolution",
        "architecture report", "system upgrades", "gemini chosen", "dynamic imports", "runtime_index"
    }
    if any(kw in q for kw in keywords):
        return True
        
    # Heuristics for implicit system queries
    if "you " in q or "your " in q or "you've" in q or "did you" in q:
        system_terms = ["upgrade", "update", "code", "system", "feature", "recieve", "receive", "new capability"]
        if any(term in q for term in system_terms):
            return True
            
    return False


def requires_web_search(query: str) -> bool:
    t = query.lower().strip()
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 10:
        return False
        
    # If it is a local profile fact lookup, we don't need web search (unless they explicitly ask to search the web)
    if is_profile_relevant_query(query):
        # Only require web search if they explicitly use web search keywords
        if not any(kw in t for kw in ("search the web", "search google", "web search", "google search", "wikipedia", "search online")):
            return False

    # Do not require web search for ARIA internal / self-awareness questions or deterministic FAQ queries
    if is_system_aware_query(query) or is_deterministic_faq_query(query):
        if not any(kw in t for kw in ("search the web", "search google", "web search", "google search", "wikipedia", "search online")):
            return False

    web_patterns = [
        r'\b(search|web|google|wikipedia|wiki|ddg|duckduckgo)\b',
        r'\b(weather|temperature|forecast|climate)\b',
        r'\b(news|headlines|current affairs|stock|price|market)\b',
        r'\b(match|matches|score|scores|cricket|football|sports|game|games)\b',
        r'\b(current|latest|recent|upcoming|newest|today|now)\b',
        r'\b(who is|who was|what is|what are|where is|when is|how to|why did)\b'
    ]
    return any(re.search(pat, t) for pat in web_patterns)


def retrieve_system_memory_via_sql(query: str) -> str:
    """Retrieve system memory context directly from database tables using SQL instead of RAG."""
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    context_parts = []
    q_lower = query.lower()
    
    # 1. User Identity & Profiles
    if any(k in q_lower for k in ("who is", "profile", "identity", "about me", "preferences", "interest")):
        try:
            profile_text = get_user_profile_text("FULL")
            if profile_text:
                context_parts.append(f"=== K3 - USER IDENTITY & PROFILES ===\n{profile_text}")
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch profile: {e}", flush=True)

    # 2. Goals
    if any(k in q_lower for k in ("goal", "query", "run", "request", "task list", "dag")):
        try:
            if is_pg:
                cursor.execute("""
                    SELECT timestamp, goal_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type = 'GOAL_RECEIVED'
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            else:
                cursor.execute("""
                    SELECT timestamp, goal_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type = 'GOAL_RECEIVED'
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K2 - SYSTEM RUNTIME GOALS ===\n"
                for r in rows:
                    part += f"[{r[0]}] Goal ID: {r[1]} | Details: {r[3]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch goals: {e}", flush=True)
            
    # 3. Failures / Rejections / Errors / Warnings
    if any(k in q_lower for k in ("fail", "error", "reject", "violation", "why did", "problem", "warn")):
        try:
            if is_pg:
                cursor.execute("""
                    SELECT timestamp, goal_id, task_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            else:
                cursor.execute("""
                    SELECT timestamp, goal_id, task_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K2 - SYSTEM RUNTIME FAILURES ===\n"
                for r in rows:
                    part += f"[{r[0]}] Goal: {r[1]} | Task: {r[2] or '-'} | Event: {r[3]} | Details: {r[4]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch failures: {e}", flush=True)
            
    # 4. Timeline / Chronology / What happened / What changed / What is unresolved
    if any(k in q_lower for k in ("timeline", "happen", "change", "unresolved", "recent", "chronology", "status", "history")):
        try:
            if is_pg:
                cursor.execute("""
                    SELECT timestamp, event_category, summary, outcome, cause, effect, resolution, confidence
                    FROM babu_temporal_timeline
                    ORDER BY event_id DESC
                    LIMIT 10
                """)
            else:
                cursor.execute("""
                    SELECT timestamp, event_category, summary, outcome, cause, effect, resolution, confidence
                    FROM babu_temporal_timeline
                    ORDER BY event_id DESC
                    LIMIT 10
                """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K2 - SYSTEM RUNTIME TIMELINE ===\n"
                for r in rows:
                    part += f"[{r[0]}] [{r[1]}] {r[2]} | Outcome: {r[3] or '-'} | Cause: {r[4] or '-'} | Effect: {r[5] or '-'} | Resolution: {r[6] or '-'} | Confidence: {r[7] or '-'}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch timeline: {e}", flush=True)
            
    # 5. Rules / Anti-patterns / Immune rules
    if any(k in q_lower for k in ("rule", "anti-pattern", "immune", "lesson", "pattern", "governance")):
        try:
            cursor.execute("SELECT key, data FROM system_memory WHERE key LIKE 'anti_pattern_%' OR key = 'immune_rules'")
            rows = cursor.fetchall()
            if rows:
                part = "=== K5 - SYSTEM ARCHITECTURE ANTI-PATTERNS ===\n"
                for r in rows:
                    part += f"[{r[0]}]: {r[1]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch rules: {e}", flush=True)
            
    # 6. Templates
    if any(k in q_lower for k in ("template", "etemp", "promoted", "compiled")):
        try:
            cursor.execute("SELECT template_id, template_signature, status, execution_count, success_count FROM trusted_templates")
            rows = cursor.fetchall()
            if rows:
                part = "=== K4 - SYSTEM EXECUTION TEMPLATES ===\n"
                for r in rows:
                    part += f"Template ID: {r[0]} | Sig: {r[1]} | Status: {r[2]} | Execs: {r[3]} | Successes: {r[4]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch templates: {e}", flush=True)
            
    # 7. Architecture Decisions & Knowledge (AKS)
    if any(k in q_lower for k in ("adr", "architecture", "tradeoff", "postmortem", "lesson", "evolution", "upgrades", "gemini", "dynamic import", "runtime index", "supersede", "impact_score", "milestone", "hierarchy")):
        try:
            cursor.execute("""
                SELECT record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp 
                FROM architecture_knowledge ORDER BY record_id ASC
            """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K5 - ARCHITECTURE KNOWLEDGE SYSTEM (AKS) ===\n"
                for r in rows:
                    part += (
                        f"[{r[1]}] ID: {r[0]} | Title: {r[2]} | Phase: {r[3]} | Status: {r[11]} | Impact Score: {r[9]}\n"
                        f"- Problem: {r[4]}\n"
                        f"- Decision: {r[5]}\n"
                        f"- Reason: {r[6]}\n"
                        f"- Outcome: {r[7]}\n"
                        f"- Trade-off: {r[8] or 'None'}\n"
                        f"- Supersedes: {r[10] or 'None'}\n"
                        f"- Date: {r[12]}\n\n"
                    )
                context_parts.append(part.strip())
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch architecture knowledge: {e}", flush=True)
            
    cursor.close()
    conn.close()
    return "\n\n".join(context_parts)


def planner_node(state: BabuState):
    """Decompose user goal into a structured GoalGraph."""
    import time
    from datetime import datetime, timezone
    try:
        from .planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
    except ImportError:
        from planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
        
    query = state["user_query"]
    history_text = state.get("history_text", "")
    session_id = state.get("session_id", "default")
    
    active_goal = state.get("active_goal") or {}
    pre_goal_id = active_goal.get("goal_id")
    
    log_temporal_event(
        event_category="GOAL_RECEIVED",
        summary=f"Received goal: {query[:80]}",
        outcome="SUCCESS",
        metadata={"session_id": session_id, "goal_id": pre_goal_id}
    )
    
    is_profile = is_profile_relevant_query(query)
    is_system = is_system_aware_query(query)
    profile_text = get_user_profile_text() if is_profile else ""
    
    # 0. Load BABU Self Context if system query
    self_ctx = ""
    if is_system:
        self_ctx = get_babu_self_context(session_id)
        profile_text = (profile_text + "\n\n" + self_ctx).strip()
    
    # 1. Retrieve system memory context via SQL first if system/self-aware query
    sql_context = ""
    if is_system:
        sql_context = retrieve_system_memory_via_sql(query)
        if sql_context:
            profile_text = (profile_text + "\n\n" + sql_context).strip()
            
    # 2. Retrieve RAG context if query relates to codebase, architecture, or documents
    retrieved = []
    if is_system:
        doc_keywords = ("architecture", "codebase", "how do you work", "how does babu work", "docs", "walkthrough", "implementation", "design", "blueprint")
        if any(kw in query.lower() for kw in doc_keywords) or not sql_context:
            try:
                from .rag_storage import retrieve_knowledge
            except ImportError:
                from rag_storage import retrieve_knowledge
                
            rag_start = time.time()
            retrieved = retrieve_knowledge(query, collections=["babu_docs", "engineering_history"])
            rag_end = time.time()
            rag_latency = round((rag_end - rag_start) * 1000, 2)
            
            hit = len(retrieved) > 0
            retrieved_tokens = sum(len(x["chunk_text"]) // 4 for x in retrieved) if hit else 0
            
            # Log RAG_RETRIEVAL event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pre_goal_id or "G-PLAN",
                task_id=None,
                department=None,
                event_type="RAG_RETRIEVAL",
                state_before=None,
                state_after=None,
                metadata={
                    "query": query,
                    "retrieval_requests": 1,
                    "retrieval_hits": 1 if hit else 0,
                    "retrieval_misses": 0 if hit else 1,
                    "retrieval_latency_ms": rag_latency,
                    "retrieved_tokens": retrieved_tokens,
                    "collections_accessed": list(set(x["collection"] for x in retrieved)) if hit else []
                }
            )
            log_temporal_event(
                event_category="RAG_RETRIEVAL",
                summary=f"Retrieved self-awareness document context for: '{query[:50]}'",
                outcome="SUCCESS" if hit else "FAIL",
                metadata={"hits": len(retrieved), "latency_ms": rag_latency}
            )
            
            if hit:
                context_str = "\n\n=== SELF_AWARENESS_DOCUMENT_CONTEXT ===\n"
                for item in retrieved:
                    context_str += f"[{item['collection']} - {item['title']}]:\n{item['chunk_text']}\n\n"
                context_str += "=== END OF SELF_AWARENESS_DOCUMENT_CONTEXT ===\n"
                profile_text = (profile_text + "\n" + context_str).strip()
            
    print(f"[PLANNER NODE] Planning goal for query: '{query[:50]}' (goal_id: {pre_goal_id})", flush=True)
    
    # 1. Intent Governance stage
    ic_start_time = time.time()
    ic_start_iso = datetime.now(timezone.utc).isoformat()
    
    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    if intent_packet_dict:
        try:
            from .planner import IntentPacket
        except ImportError:
            from planner import IntentPacket
        intent_packet = IntentPacket.from_dict(intent_packet_dict)
        print(f"[PLANNER NODE] Reusing pre-classified intent packet (category: {intent_packet.query_category})", flush=True)
    else:
        intent_packet = classify_intent(query, history_text, model_name=CURRENT_PA_MODEL)
        
    ic_end_time = time.time()
    ic_end_iso = datetime.now(timezone.utc).isoformat()
    ic_latency_ms = round((ic_end_time - ic_start_time) * 1000, 2)
    ic_latency_sec = round(ic_end_time - ic_start_time, 4)

    # Log INTENT_CLASSIFICATION event
    ic_tokens = getattr(intent_packet, "tokens", None) or {"prompt": 0, "completion": 0, "total": 0}
    ic_model = getattr(intent_packet, "model", None) or CURRENT_DEPT_MODEL or "unknown"
    p_ic, c_ic = get_token_costs(ic_model)
    ic_cost = (ic_tokens.get("prompt", 0) * p_ic) + (ic_tokens.get("completion", 0) * c_ic)
    
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=pre_goal_id or "G-PLAN",
        task_id=None,
        department=None,
        event_type="INTENT_CLASSIFICATION",
        state_before=None,
        state_after="CLASSIFIED",
        metadata={
            "query": query,
            "event_start_time": ic_start_iso,
            "event_end_time": ic_end_iso,
            "latency_ms": ic_latency_ms,
            "latency": ic_latency_sec,
            "tokens": ic_tokens,
            "cost": round(ic_cost, 6),
            "model": ic_model,
        }
    )

    plan_start_time = time.time()
    plan_start_iso = datetime.now(timezone.utc).isoformat()

    # --- Step 1: E[Temp] Template Lookup (Priority over fast-track) ---
    template = None
    is_compatible = False
    sig = ""
    
    # E[Temp] Telemetry vbabubles initialization
    template_lookup_attempted = True
    template_candidates_found = 0
    template_selected = None
    template_confidence = None
    template_rejected_reason = None
    template_execution_used = False
    template_tokens_saved = 0

    try:
        from .governance import check_constraint_compatibility
    except ImportError:
        from governance import check_constraint_compatibility
        
    flow_order = ["research", "information", "analysis", "writing", "execution", "pa"]
    depts = [d for d in flow_order if d in intent_packet.allowed_departments]
    sig = ":".join(depts)
    if "execution" in depts and intent_packet.allowed_actions:
        sorted_actions = sorted(intent_packet.allowed_actions)
        sig += ":" + ":".join(sorted_actions)

    print(f"[PLANNER NODE] Template signature built for lookup: {sig}", flush=True)
    
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Query total candidates for telemetry
        if is_pg:
            cursor.execute(
                "SELECT COUNT(*) FROM trusted_templates WHERE template_signature = %s AND status = 'ACTIVE'",
                (sig,)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM trusted_templates WHERE template_signature = ? AND status = 'ACTIVE'",
                (sig,)
            )
        template_candidates_found = cursor.fetchone()[0]
        
        if template_candidates_found > 0:
            if is_pg:
                cursor.execute(
                    "SELECT template_id, goal_graph_json, status FROM trusted_templates WHERE template_signature = %s AND status = 'ACTIVE'",
                    (sig,)
                )
            else:
                cursor.execute(
                    "SELECT template_id, goal_graph_json, status FROM trusted_templates WHERE template_signature = ? AND status = 'ACTIVE'",
                    (sig,)
                )
            row = cursor.fetchone()
            if row:
                template = {
                    "template_id": row[0],
                    "goal_graph_json": row[1],
                    "status": row[2]
                }
        cursor.close()
    except Exception as db_err:
        print(f"[PLANNER DB ERROR] Failed to query trusted_templates: {db_err}", flush=True)
        template_rejected_reason = f"Database query error: {db_err}"
    finally:
        conn.close()

    if not template and not template_rejected_reason:
        template_rejected_reason = "No ACTIVE template found for signature in database"

    if template:
        is_compatible = check_constraint_compatibility(query, template)
        if is_compatible:
            try:
                from .task_engine import GoalGraph
            except ImportError:
                from task_engine import GoalGraph
            
            try:
                graph_dict = json.loads(template["goal_graph_json"])
                # Use template's graph but override ID and goal
                graph_dict["goal_id"] = pre_goal_id
                graph_dict["goal"] = query
                graph = GoalGraph.from_dict(graph_dict)
                graph.planner_status = "TEMPLATE_MATCH"
                print(f"[PLANNER NODE] E[Temp] Muscle Memory Hit! Using template {template['template_id']} for signature {sig}", flush=True)
                
                # Update telemetry for successful hit
                template_selected = template["template_id"]
                template_confidence = 1.0
                template_execution_used = True
                template_tokens_saved = 2300
            except Exception as parse_err:
                print(f"[PLANNER NODE] Failed to load template goal graph: {parse_err}. Falling back to dynamic planner.", flush=True)
                template = None
                is_compatible = False
                template_rejected_reason = f"Parsing error: {parse_err}"
        else:
            template_rejected_reason = "Constraint compatibility checks failed (modifiers, negations, or slots mismatch)"

    # --- Step 2: Fallback gates if no active/compatible template is matched ---
    if not template or not is_compatible:
        # 2a. Bounded Governance Gate: Clarification fallback on low confidence
        if intent_packet.confidence < 0.65:
            print(f"[INTENT GOVERNANCE] Low confidence ({intent_packet.confidence} < 0.65) -> bypassing planner and returning AMBIGUOUS_QUERY fallback.", flush=True)
            graph = _build_fallback_graph(
                query,
                goal_id=pre_goal_id,
                goal_type="NEW",
                planner_status="AMBIGUOUS_QUERY",
                intent_packet=intent_packet.to_dict()
            )
        # 2b. Simple Local Lookup (No web search or other complex intents are active)
        elif intent_packet.lookup and not (intent_packet.research or intent_packet.generate or intent_packet.execute or requires_workspace_access(query) or requires_web_search(query)) and not has_multiple_tasks_or_requests(query, intent_packet.to_dict()):
            PRIVATE_QUERY_TYPES = ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION")
            if intent_packet.query_category in PRIVATE_QUERY_TYPES:
                print(f"[PLANNER NODE] Private query category '{intent_packet.query_category}' detected → Disabling fast-track simple lookup shortcut.", flush=True)
                graph = plan_goal(
                    query=query,
                    history_text=history_text,
                    profile_text=profile_text,
                    model_name=CURRENT_DEPT_MODEL,
                    goal_id=pre_goal_id,
                    is_correction=False,
                    last_goal_text=None,
                    intent_packet=intent_packet,
                    session_id=session_id,
                )
            else:
                print(f"[PLANNER NODE] Fast-tracking simple lookup/websearch query (lookup={intent_packet.lookup}, websearch={intent_packet.websearch}) directly to PA response", flush=True)
                graph = build_walk_graph(query, goal_id=pre_goal_id)
                graph.planner_status = "WALK"
        else:
            # Check if this query is a correction referencing a recent workflow
            is_correction = False
            last_goal_text = None
            last_graph = get_last_goal_graph(session_id)
            if last_graph:
                last_goal_text = last_graph.get("goal")
                # If the escalation router classified this as a workflow escalation, AND it doesn't explicitly start with a command trigger keyword,
                # it is a corrective query referencing the last goal.
                t_clean = query.lower().strip()
                has_command_trigger = (
                    t_clean.startswith("/") or 
                    t_clean.startswith("!") or 
                    t_clean.startswith("launch") or 
                    t_clean.startswith("sprint") or 
                    t_clean.startswith("postnow")
                )
                if should_escalate_to_workflow(query, history_text=history_text) and not has_command_trigger:
                    is_correction = True
                    print(f"[PLANNER NODE] Correction detected. Previous goal: '{last_goal_text}'", flush=True)

            graph = plan_goal(
                query=query,
                history_text=history_text,
                profile_text=profile_text,
                model_name=CURRENT_DEPT_MODEL,
                goal_id=pre_goal_id,
                is_correction=is_correction,
                last_goal_text=last_goal_text,
                intent_packet=intent_packet,
                session_id=session_id,
            )

    plan_end_time = time.time()
    plan_end_iso = datetime.now(timezone.utc).isoformat()
    plan_latency_ms = round((plan_end_time - plan_start_time) * 1000, 2)
    plan_latency_sec = round(plan_end_time - plan_start_time, 4)
    
    # Store in graph so we can access it during execution completion
    if getattr(graph, "planner_status", "") in ("TEMPLATE_MATCH", "WALK", "FAST_TRACK"):
        graph.planning_tokens = {"prompt": 0, "completion": 0, "total": 0}
    else:
        graph.planning_tokens = getattr(graph, "planning_tokens", None) or {"prompt": 1800, "completion": 500, "total": 2300}
    
    # Log GOAL_CREATED lifecycle event
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="GOAL_CREATED",
        state_before="NONE",
        state_after="ACTIVE",
        metadata={
            "query": query,
            "goal": graph.goal,
            "planner_status": graph.planner_status
        }
    )
    
    if getattr(graph, "planner_status", "") in ("TEMPLATE_MATCH", "WALK", "FAST_TRACK"):
        plan_tokens = {"prompt": 0, "completion": 0, "total": 0}
        plan_cost = 0.0
        has_actual_tokens = True
    else:
        has_actual_tokens = bool(getattr(graph, "planning_tokens", None))
        plan_tokens = getattr(graph, "planning_tokens", None) or {"prompt": 1800, "completion": 500, "total": 2300}
        p_plan, c_plan = get_token_costs(CURRENT_DEPT_MODEL)
        plan_cost = (plan_tokens.get("prompt", 0) * p_plan) + (plan_tokens.get("completion", 0) * c_plan)
    
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="PLANNING",
        state_before=None,
        state_after="PLANNED",
        metadata={
            "query": query,
            "graph": graph.to_dict(),
            "planner_status": graph.planner_status,
            "tokens": plan_tokens,
            "cost": round(plan_cost, 6),
            "model": CURRENT_DEPT_MODEL,
            "is_estimated": not has_actual_tokens,
            "event_start_time": plan_start_iso,
            "event_end_time": plan_end_iso,
            "latency_ms": plan_latency_ms,
            "latency": plan_latency_sec
        }
    )
    
    tracker = state.get("execution_tracker") or {}
    tracker["planner_duration"] = plan_latency_sec
    
    total_planner_tokens = {
        "prompt": ic_tokens.get("prompt", 0) + plan_tokens.get("prompt", 0),
        "completion": ic_tokens.get("completion", 0) + plan_tokens.get("completion", 0),
        "total": ic_tokens.get("total", 0) + plan_tokens.get("total", 0)
    }
        
    # Log TEMPLATE_LOOKUP_TELEMETRY event
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="TEMPLATE_LOOKUP_TELEMETRY",
        metadata={
            "template_lookup_attempted": template_lookup_attempted,
            "template_candidates_found": template_candidates_found,
            "template_selected": template_selected,
            "template_confidence": template_confidence,
            "template_rejected_reason": template_rejected_reason,
            "template_execution_used": template_execution_used,
            "planner_tokens": plan_tokens,
            "template_tokens_saved": template_tokens_saved
        }
    )
    
    log_temporal_event(
        event_category="GOAL_PLANNED",
        summary=f"Goal planned using {graph.planner_status} strategy: {graph.goal[:80]}",
        outcome="SUCCESS",
        metadata={
            "session_id": session_id,
            "goal_id": graph.goal_id,
            "planner_status": graph.planner_status,
            "tasks_count": len(graph.tasks)
        }
    )
        
    routing_meta = state.get("routing_metadata") or {}
    routing_meta["sql_context"] = sql_context
    routing_meta["retrieved_rag"] = retrieved

    ret_dict = {
        "goal_graph": graph.to_dict(), 
        "execution_tracker": tracker, 
        "tokens": total_planner_tokens,
        "routing_metadata": routing_meta
    }
    if is_system:
        ret_dict["compressed_research"] = (self_ctx + "\n\n" + sql_context).strip()
    return ret_dict


def task_executor_node(state: BabuState):
    """Executes the task DAG using TaskEngine and Department Heads."""
    import time
    from datetime import datetime, timezone
    try:
        from .task_engine import TaskEngine, GoalGraph, TaskState
        from .departments import get_department_head
        from .auditor import BipartiteAuditor
    except ImportError:
        from task_engine import TaskEngine, GoalGraph, TaskState
        from departments import get_department_head
        from auditor import BipartiteAuditor
    
    start_time = time.time()
    session_id = state.get("session_id", "default")
    
    graph_dict = state.get("goal_graph")
    if not graph_dict:
        try:
            from .planner import build_action_graph
        except ImportError:
            from planner import build_action_graph
        detected_action = state.get("detected_action")
        query = state["user_query"]
        active_goal = state.get("active_goal") or {}
        pre_goal_id = active_goal.get("goal_id")
        if detected_action:
            graph = build_action_graph(query, detected_action, goal_id=pre_goal_id)
            graph_dict = graph.to_dict()
        else:
            return {"action_result": "No executable action found.", "final_brief": "Execution failed: no action found."}
            
    goal_graph = GoalGraph.from_dict(graph_dict)
    engine = TaskEngine(goal_graph)
    auditor = BipartiteAuditor(llm=llm_dept)
    
    is_template_match = (goal_graph.planner_status == "TEMPLATE_MATCH")
    if is_template_match:
        try:
            from .governance import micro_audit_dag
        except ImportError:
            from governance import micro_audit_dag
        passed_micro = micro_audit_dag(goal_graph.to_dict())
        if not passed_micro:
            print(f"[EXECUTOR] Micro-audit failed for template goal DAG: {goal_graph.goal_id}", flush=True)
            for t in goal_graph.tasks:
                engine.mark_failed(t.task_id, "Micro-audit failed")
                
    print(f"[EXECUTOR] Executing goal DAG: {goal_graph.goal_id}", flush=True)
    
    # Log DEPENDENCY_WAIT for all downstream tasks initially
    for task in goal_graph.tasks:
        if task.depends_on:
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="DEPENDENCY_WAIT",
                state_before="PENDING",
                state_after="PENDING",
                metadata={"depends_on": task.depends_on}
            )
            
    def track_cascading_blocks(action_fn, *args, **kwargs):
        pre_blocked = {t.task_id for t in engine.goal.tasks if t.state == TaskState.BLOCKED}
        action_fn(*args, **kwargs)
        post_blocked = {t.task_id for t in engine.goal.tasks if t.state == TaskState.BLOCKED}
        
        newly_blocked = post_blocked - pre_blocked
        for b_tid in newly_blocked:
            b_task = engine._task_map[b_tid]
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=b_tid,
                department=b_task.department,
                event_type="DEPENDENCY_BLOCKED",
                state_before="PENDING",
                state_after="BLOCKED",
                metadata={"blocked_by": args[0] if args else "unknown", "reason": "dependency failure propagation"}
            )
    
    execution_log = state.get("execution_log") or []
    
    shared_resources = {
        "user_query": state["user_query"]
    }
    
    total_audit_tokens = {"prompt": 0, "completion": 0, "total": 0}
    
    # Execution loop
    while not engine.is_goal_complete() and not engine.is_goal_blocked():
        ready_tasks = engine.get_ready_tasks()
        if not ready_tasks:
            break
            
        for task in ready_tasks:
            # Token Budget Check
            accumulated_goal_tokens = state.get("tokens", {}).get("total", 0)
            accumulated_task_tokens = 0
            for entry in execution_log:
                accumulated_goal_tokens += entry.get("tokens", {}).get("total", 0)
                if entry.get("task_id") == task.task_id:
                    accumulated_task_tokens += entry.get("tokens", {}).get("total", 0)
                
            is_ok, budget_reason = engine.verify_token_budget(task.task_id, accumulated_task_tokens, accumulated_goal_tokens)
            if not is_ok:
                print(f"[EXECUTOR] Token budget exhausted: {budget_reason}", flush=True)
                track_cascading_blocks(engine.mark_cancelled, task.task_id, f"Token Budget Exhausted: {budget_reason}")
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": f"Token Budget Exhausted: {budget_reason}",
                    "status": "CANCELLED",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0}
                })
                continue
                
            engine.mark_running(task.task_id)
            
            # Time pre-execution audit
            pre_start_time = time.time()
            pre_start_iso = datetime.now(timezone.utc).isoformat()
            
            # Layer 5 Bipartite Auditor: Pre-Execution Gatekeeper check
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_PRE",
                state_before="RUNNING",
                state_after="AUDITING_PRE",
                metadata={"objective": task.objective}
            )
            if is_template_match:
                passed_pre = True
                reason_pre = "Bypassed via template match micro-audit"
            else:
                passed_pre, reason_pre = auditor.audit_pre(task)
            
            pre_end_time = time.time()
            pre_end_iso = datetime.now(timezone.utc).isoformat()
            pre_latency_ms = round((pre_end_time - pre_start_time) * 1000, 2)
            pre_latency_sec = round(pre_end_time - pre_start_time, 4)
            
            if not passed_pre:
                print(f"[EXECUTOR] Pre-execution audit blocked task {task.task_id}: {reason_pre}", flush=True)
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="AUDIT_PRE_FAIL",
                    state_before="AUDITING_PRE",
                    state_after="FAILED",
                    metadata={
                        "objective": task.objective,
                        "reason": reason_pre,
                        "event_start_time": pre_start_iso,
                        "event_end_time": pre_end_iso,
                        "latency_ms": pre_latency_ms,
                        "latency": pre_latency_sec,
                        "tokens": {"prompt": 0, "completion": 0, "total": 0},
                        "cost": 0.0,
                        "model": "rules_engine"
                    }
                )
                log_temporal_event(
                    event_category="AUDIT_PRE_FAIL",
                    summary=f"Pre-execution audit blocked task {task.task_id} [{task.department}]: {reason_pre[:80]}",
                    outcome="FAIL",
                    metadata={"session_id": session_id, "goal_id": goal_graph.goal_id, "task_id": task.task_id, "reason": reason_pre},
                    cause=reason_pre,
                    effect=f"Task {task.task_id} execution aborted; cascading blocks triggered for downstream tasks.",
                    resolution="Align planner constraints with the intent classifier capability boundaries.",
                    confidence=0.0
                )
                track_cascading_blocks(engine.mark_failed, task.task_id, f"Pre-execution Audit Blocked: {reason_pre}")
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": f"Pre-execution Audit Blocked: {reason_pre}",
                    "status": "FAILED"
                })
                # Add to tracker even on failure
                tracker = state.get("execution_tracker") or {}
                task_lats = tracker.get("task_latencies") or []
                task_lats.append({
                    "task_id": task.task_id,
                    "dept": task.department,
                    "worker_ms": 0.0,
                    "audit_pre_ms": pre_latency_ms,
                    "audit_post_ms": 0.0
                })
                tracker["task_latencies"] = task_lats
                continue
                
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_PRE_PASS",
                state_before="AUDITING_PRE",
                state_after="RUNNING",
                metadata={
                    "objective": task.objective,
                    "event_start_time": pre_start_iso,
                    "event_end_time": pre_end_iso,
                    "latency_ms": pre_latency_ms,
                    "latency": pre_latency_sec,
                    "tokens": {"prompt": 0, "completion": 0, "total": 0},
                    "cost": 0.0,
                    "model": "rules_engine"
                }
            )
                
            dept_head = get_department_head(task.department)
            
            # Inject upstream results into context
            completed_results = engine.get_completed_results()
            task.context["upstream_results"] = [
                {
                    "task_id": tid,
                    "result": get_department_head(engine._task_map[tid].department).compress_result_for_downstream(res),
                    "department": engine._task_map[tid].department,
                    "objective": engine._task_map[tid].objective
                }
                for tid, res in completed_results.items()
                if tid in task.depends_on
            ]
            
            # If the task is an execution task, we MUST ask the user for approval
            # with the fully resolved parameters (including upstream findings!)
            if task.department == "execution":
                action = task.context.get("action", "")
                try:
                    from .governance import get_constitution
                except ImportError:
                    from governance import get_constitution
                mandatory_approvals = get_constitution("mandatory_human_approval", [])
                
                if action in mandatory_approvals:
                    task.context["approved"] = False
                elif action in ("search_sheet", "search_gmail"):
                    task.context["approved"] = True
                if not task.context.get("approved"):
                    action = task.context.get("action", "")
                    params = task.context.get("params", {})
                    
                    # Resolve params with upstream research/writing text
                    upstream_texts = [
                        item["result"] for item in task.context.get("upstream_results", [])
                    ]
                    upstream_text = "\n\n".join(upstream_texts) if upstream_texts else ""
                    
                    # Call execution department head _resolve_params method dynamically
                    resolved_params = dept_head._resolve_params(params, upstream_text)
                    
                    # Save in pending action lock
                    with _pending_actions_lock:
                        _pending_actions[session_id] = {
                            "action": action,
                            "params": resolved_params,
                            "task_id": task.task_id,
                            "goal_id": goal_graph.goal_id
                        }
                        
                    # Format a beautiful preview of the action plan!
                    preview_fields = {k: v for k, v in resolved_params.items() if k not in ("body", "content", "caption")}
                    fields_str = "\n".join(f"  • {k.capitalize()}: {v}" for k, v in preview_fields.items())
                    body_preview = resolved_params.get("body", resolved_params.get("content", resolved_params.get("caption", "")))
                    
                    preview = fields_str
                    if body_preview:
                        preview += f"\n\n**Draft Content:**\n{body_preview}"
                        
                    pending_action_notice = (
                        f"Action authorization required.\n\n"
                        f"Proposed action: **{action}**\n"
                        f"{preview}\n\n"
                        "Reply with '1' / 'approve' to execute, or '0' / 'cancel' to reject."
                    )
                    
                    duration = round(time.time() - start_time, 2)
                    tracker = state.get("execution_tracker") or {}
                    tracker["task_manager_duration"] = duration
                    
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=goal_graph.goal_id,
                        task_id=task.task_id,
                        department=task.department,
                        event_type="WAITING_FOR_APPROVAL",
                        state_before="RUNNING",
                        state_after="WAITING",
                        metadata={"action": action, "params": resolved_params}
                    )
                    
                    node_tokens = {"prompt": 0, "completion": 0, "total": 0}
                    for entry in execution_log:
                        t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
                        node_tokens["prompt"] += t.get("prompt", 0)
                        node_tokens["completion"] += t.get("completion", 0)
                        node_tokens["total"] += t.get("total", 0)
                        
                    return {
                        "goal_graph": engine.goal.to_dict(),
                        "execution_log": execution_log,
                        "final_brief": pending_action_notice,
                        "action_result": "",
                        "execution_tracker": tracker,
                        "pending_action_notice": pending_action_notice,
                        "tokens": node_tokens
                    }
            
            exec_latency_ms = 0.0
            exec_latency = 0.0
            t_dispatch_start_iso = datetime.now(timezone.utc).isoformat()
            t_dispatch_end_iso = t_dispatch_start_iso
            try:
                # Dispatch task to the department head
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_START",
                    state_before="RUNNING",
                    state_after="RUNNING",
                    metadata={"objective": task.objective}
                )
                log_temporal_event(
                    event_category="TASK_DISPATCHED",
                    summary=f"Dispatched task {task.task_id} [{task.department}] - {task.objective[:80]}",
                    outcome="SUCCESS",
                    metadata={"session_id": session_id, "goal_id": goal_graph.goal_id, "task_id": task.task_id, "department": task.department}
                )
                
                t_dispatch_start = time.time()
                t_dispatch_start_iso = datetime.now(timezone.utc).isoformat()
                result, task_tokens = dept_head.dispatch(task, shared_resources, llm_dept)
                t_dispatch_end = time.time()
                t_dispatch_end_iso = datetime.now(timezone.utc).isoformat()
                exec_latency = round(t_dispatch_end - t_dispatch_start, 2)
                exec_latency_ms = round((t_dispatch_end - t_dispatch_start) * 1000, 2)
                
                p_rate, c_rate = get_token_costs(CURRENT_DEPT_MODEL)
                exec_cost = (task_tokens.get("prompt", 0) * p_rate) + (task_tokens.get("completion", 0) * c_rate)
                
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_DONE",
                    state_before="RUNNING",
                    state_after="RUNNING",
                    metadata={
                        "objective": task.objective,
                        "latency": exec_latency,
                        "latency_ms": exec_latency_ms,
                        "event_start_time": t_dispatch_start_iso,
                        "event_end_time": t_dispatch_end_iso,
                        "tokens": task_tokens,
                        "cost": round(exec_cost, 6),
                        "model": CURRENT_DEPT_MODEL,
                        "result_preview": (result or "")[:500],
                        "is_estimated": False
                    }
                )
                
                # Layer 5 Bipartite Auditor: Post-Execution Validator check
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="AUDIT_POST",
                    state_before="RUNNING",
                    state_after="AUDITING_POST",
                    metadata={"objective": task.objective}
                )
                
                post_start_time = time.time()
                post_start_iso = datetime.now(timezone.utc).isoformat()
                if is_template_match:
                    passed_post = True
                    audit_result = "Bypassed via template match micro-audit"
                    audit_tokens = {"prompt": 0, "completion": 0, "total": 0}
                else:
                    passed_post, audit_result = auditor.audit_post(task, result)
                    audit_tokens = getattr(auditor, "last_tokens", {"prompt": 0, "completion": 0, "total": 0})
                    if type(audit_tokens).__name__ in ("MagicMock", "Mock") or not isinstance(audit_tokens, dict):
                        audit_tokens = {"prompt": 0, "completion": 0, "total": 0}
                
                post_end_time = time.time()
                post_end_iso = datetime.now(timezone.utc).isoformat()
                post_latency_ms = round((post_end_time - post_start_time) * 1000, 2)
                post_latency_sec = round(post_end_time - post_start_time, 4)
                
                # Accumulate auditor tokens
                total_audit_tokens["prompt"] += audit_tokens.get("prompt", 0)
                total_audit_tokens["completion"] += audit_tokens.get("completion", 0)
                total_audit_tokens["total"] += audit_tokens.get("total", 0)
                
                post_cost = (audit_tokens.get("prompt", 0) * p_rate) + (audit_tokens.get("completion", 0) * c_rate)
                
                # Update tracker with granular task latencies
                tracker = state.get("execution_tracker") or {}
                task_lats = tracker.get("task_latencies") or []
                task_lats.append({
                    "task_id": task.task_id,
                    "dept": task.department,
                    "worker_ms": exec_latency_ms,
                    "audit_pre_ms": pre_latency_ms,
                    "audit_post_ms": post_latency_ms
                })
                tracker["task_latencies"] = task_lats
                
                if not passed_post:
                    print(f"[EXECUTOR] Post-execution audit failed task {task.task_id}: {audit_result}", flush=True)
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=goal_graph.goal_id,
                        task_id=task.task_id,
                        department=task.department,
                        event_type="AUDIT_POST_FAIL",
                        state_before="AUDITING_POST",
                        state_after="FAILED",
                        metadata={
                            "objective": task.objective,
                            "audit_result": audit_result,
                            "tokens": audit_tokens,
                            "cost": round(post_cost, 6),
                            "model": CURRENT_DEPT_MODEL,
                            "is_estimated": False,
                            "event_start_time": post_start_iso,
                            "event_end_time": post_end_iso,
                            "latency_ms": post_latency_ms,
                            "latency": post_latency_sec
                        }
                    )
                    log_temporal_event(
                        event_category="AUDIT_POST_FAIL",
                        summary=f"Post-execution audit failed task {task.task_id} [{task.department}]: {audit_result[:80]}",
                        outcome="FAIL",
                        metadata={"session_id": session_id, "goal_id": goal_graph.goal_id, "task_id": task.task_id, "reason": audit_result},
                        cause=f"Post-audit checklist failure: {audit_result}",
                        effect=f"Task {task.task_id} marked as failed; cascading blocks triggered for downstream tasks.",
                        resolution="Refine worker result format or checklist requirements.",
                        confidence=0.0
                    )
                    track_cascading_blocks(engine.mark_failed, task.task_id, f"Post-execution Audit Failed: {audit_result}")
                    execution_log.append({
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "department": task.department,
                        "error": f"Post-execution Audit Failed: {audit_result}",
                        "status": "FAILED",
                        "tokens": task_tokens
                    })
                    # Log failure to the immune system to learn from errors!
                    if goal_graph.goal_type != "CORRECTION":
                        try:
                            from .memory import log_execution_failure
                        except ImportError:
                            from memory import log_execution_failure
                        log_execution_failure(
                            domain=f"department.{task.department}",
                            method=task.objective,
                            exception_msg=f"Post-execution Audit Failed: {audit_result}",
                            goal=goal_graph.goal,
                            intent_packet=goal_graph.intent_packet
                        )
                    else:
                        print(f"[IMMUNE SYSTEM GATE] Bypassing failure logging for CORRECTION goal execution failure to prevent database noise.", flush=True)
                else:
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=goal_graph.goal_id,
                        task_id=task.task_id,
                        department=task.department,
                        event_type="AUDIT_POST_PASS",
                        state_before="AUDITING_POST",
                        state_after="COMPLETED",
                        metadata={
                            "objective": task.objective,
                            "audit_result": audit_result,
                            "tokens": audit_tokens,
                            "cost": round(post_cost, 6),
                            "model": CURRENT_DEPT_MODEL,
                            "is_estimated": False,
                            "event_start_time": post_start_iso,
                            "event_end_time": post_end_iso,
                            "latency_ms": post_latency_ms,
                            "latency": post_latency_sec
                        }
                    )
                    audit_metrics = task.context.get("audit_metrics") or {}
                    log_temporal_event(
                        event_category="TASK_COMPLETED",
                        summary=f"Completed task {task.task_id} [{task.department}] - {task.objective[:80]}",
                        outcome="SUCCESS",
                        metadata={"session_id": session_id, "goal_id": goal_graph.goal_id, "task_id": task.task_id, "department": task.department},
                        cause="Task completed worker dispatch successfully with post-audit pass.",
                        effect="Engine marking task completed and checking downstream dependencies.",
                        resolution="Task resolved successfully.",
                        confidence=audit_metrics.get("confidence", 1.0)
                    )
                    
                    # Track newly ready tasks unlocked by completing this task
                    pre_ready = {t.task_id for t in engine.get_ready_tasks()}
                    engine.mark_completed(task.task_id, audit_result)
                    post_ready = {t.task_id for t in engine.get_ready_tasks()}
                    
                    newly_ready = post_ready - pre_ready
                    for n_tid in newly_ready:
                        n_task = engine._task_map[n_tid]
                        log_execution_ledger_event(
                            session_id=session_id,
                            goal_id=goal_graph.goal_id,
                            task_id=n_tid,
                            department=n_task.department,
                            event_type="DEPENDENCY_SATISFIED",
                            state_before="PENDING",
                            state_after="READY",
                            metadata={"satisfied_by": task.task_id}
                        )
                        
                    execution_log.append({
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "department": task.department,
                        "result": audit_result,
                        "status": "SUCCESS",
                        "tokens": task_tokens
                    })
                    # Register success to heal the immune system!
                    try:
                        from .memory import register_successful_execution
                    except ImportError:
                        from memory import register_successful_execution
                    register_successful_execution(domain=f"department.{task.department}")
                    if task.department == "execution" and task.context.get("action"):
                        register_successful_execution(domain=f"action.{task.context['action']}")
            except Exception as e:
                t_dispatch_end = time.time()
                t_dispatch_end_iso = datetime.now(timezone.utc).isoformat()
                exec_latency = round(t_dispatch_end - t_dispatch_start, 2)
                exec_latency_ms = round((t_dispatch_end - t_dispatch_start) * 1000, 2)
                err_msg = str(e)
                print(f"[EXECUTOR ERROR] Task {task.task_id} failed: {err_msg}", flush=True)
                
                # Update tracker even on failure
                tracker = state.get("execution_tracker") or {}
                task_lats = tracker.get("task_latencies") or []
                task_lats.append({
                    "task_id": task.task_id,
                    "dept": task.department,
                    "worker_ms": exec_latency_ms,
                    "audit_pre_ms": pre_latency_ms,
                    "audit_post_ms": 0.0
                })
                tracker["task_latencies"] = task_lats
                
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_FAIL",
                    state_before="RUNNING",
                    state_after="FAILED",
                    metadata={
                        "objective": task.objective,
                        "error": err_msg,
                        "latency": exec_latency,
                        "latency_ms": exec_latency_ms,
                        "event_start_time": t_dispatch_start_iso,
                        "event_end_time": t_dispatch_end_iso,
                        "tokens": {"prompt": 0, "completion": 0, "total": 0},
                        "cost": 0.0,
                        "model": CURRENT_DEPT_MODEL
                    }
                )
                track_cascading_blocks(engine.mark_failed, task.task_id, err_msg)
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": err_msg,
                    "status": "FAILED",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0}
                })
                # Log failure to the immune system to learn from errors!
                if goal_graph.goal_type != "CORRECTION":
                    try:
                        from .memory import log_execution_failure
                    except ImportError:
                        from memory import log_execution_failure
                    log_execution_failure(
                        domain=f"department.{task.department}",
                        method=task.objective,
                        exception_msg=f"Task Execution Exception: {err_msg}",
                        goal=goal_graph.goal,
                        intent_packet=goal_graph.intent_packet
                    )
                else:
                    print(f"[IMMUNE SYSTEM GATE] Bypassing failure logging for CORRECTION goal execution failure to prevent database noise.", flush=True)
                
    # Update tracker
    duration = round(time.time() - start_time, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["executor_duration"] = duration
    tracker["task_manager_duration"] = duration
    
    start_time_float = tracker.get("start_time", start_time)
    goal_start_iso = datetime.fromtimestamp(start_time_float, timezone.utc).isoformat()
    goal_end_iso = datetime.now(timezone.utc).isoformat()
    goal_duration = time.time() - start_time_float
    goal_latency_ms = round(goal_duration * 1000, 2)
    goal_latency_sec = round(goal_duration, 4)
    
    # Calculate goal tokens & costs cumulative summary
    total_goal_tokens = {"prompt": 0, "completion": 0, "total": 0}
    ic_tokens = (goal_graph.intent_packet or {}).get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
    plan_tokens = goal_graph.planning_tokens or {"prompt": 0, "completion": 0, "total": 0}
    
    tasks_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for entry in execution_log:
        t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        tasks_tokens["prompt"] += t.get("prompt", 0)
        tasks_tokens["completion"] += t.get("completion", 0)
        tasks_tokens["total"] += t.get("total", 0)
        
    total_goal_tokens["prompt"] = ic_tokens.get("prompt", 0) + plan_tokens.get("prompt", 0) + tasks_tokens.get("prompt", 0) + total_audit_tokens.get("prompt", 0)
    total_goal_tokens["completion"] = ic_tokens.get("completion", 0) + plan_tokens.get("completion", 0) + tasks_tokens.get("completion", 0) + total_audit_tokens.get("completion", 0)
    total_goal_tokens["total"] = total_goal_tokens["prompt"] + total_goal_tokens["completion"]
    
    ic_model = (goal_graph.intent_packet or {}).get("model") or CURRENT_DEPT_MODEL or "unknown"
    p_ic, c_ic = get_token_costs(ic_model)
    cost_ic = (ic_tokens.get("prompt", 0) * p_ic) + (ic_tokens.get("completion", 0) * c_ic)
    
    p_plan, c_plan = get_token_costs(CURRENT_DEPT_MODEL)
    cost_plan = (plan_tokens.get("prompt", 0) * p_plan) + (plan_tokens.get("completion", 0) * c_plan)
    
    p_work, c_work = get_token_costs(CURRENT_DEPT_MODEL)
    cost_workers = (tasks_tokens.get("prompt", 0) * p_work) + (tasks_tokens.get("completion", 0) * c_work)
    cost_audit = (total_audit_tokens.get("prompt", 0) * p_work) + (total_audit_tokens.get("completion", 0) * c_work)
    
    total_goal_cost = cost_ic + cost_plan + cost_workers + cost_audit
    
    final_brief = engine.get_execution_summary()
    
    # Goal lifecycle outcomes logging
    if engine.is_goal_complete():
        is_graceful_recovery = (goal_graph.planner_status not in ("SUCCESS", "TEMPLATE_MATCH", "FAST_TRACK", "WALK", None, ""))
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=None,
            department=None,
            event_type="GOAL_COMPLETED",
            state_before="ACTIVE",
            state_after="COMPLETED",
            metadata={
                "latency_sec": goal_latency_sec,
                "latency_ms": goal_latency_ms,
                "event_start_time": goal_start_iso,
                "event_end_time": goal_end_iso,
                "tokens": total_goal_tokens,
                "cost": round(total_goal_cost, 6),
                "model": CURRENT_DEPT_MODEL,
                "summary": final_brief[:1000],
                "graceful_recovery": is_graceful_recovery,
                "planner_status": goal_graph.planner_status
            }
        )
        if is_graceful_recovery:
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=None,
                department=None,
                event_type="RECOVERY_REGISTERED",
                state_before="DEGRADED",
                state_after="COMPLETED",
                metadata={
                    "planner_status": goal_graph.planner_status,
                    "recovery_mechanism": "Conversational assistant fallback graph",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
            print(f"[EXECUTOR] GRACEFUL RECOVERY REGISTERED: Planner failed with status {goal_graph.planner_status}, but execution completed successfully.", flush=True)
    elif engine.is_goal_blocked() or not engine.is_goal_complete():
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=None,
            department=None,
            event_type="GOAL_FAILED",
            state_before="ACTIVE",
            state_after="FAILED",
            metadata={
                "latency_sec": goal_latency_sec,
                "latency_ms": goal_latency_ms,
                "event_start_time": goal_start_iso,
                "event_end_time": goal_end_iso,
                "tokens": total_goal_tokens,
                "cost": round(total_goal_cost, 6),
                "model": CURRENT_DEPT_MODEL,
                "error": "Goal execution blocked or stalled"
            }
        )
        
    # Store action result if there was an execution task
    action_res = ""
    for entry in execution_log:
        if entry.get("department") == "execution" and entry.get("status") == "SUCCESS":
            action_res = entry.get("result", "")
            
    node_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for entry in execution_log:
        t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        node_tokens["prompt"] += t.get("prompt", 0)
        node_tokens["completion"] += t.get("completion", 0)
        node_tokens["total"] += t.get("total", 0)
            
    log_temporal_event(
        event_category="GOAL_EXECUTED",
        summary=f"Finished goal execution with status: {'COMPLETED' if engine.is_goal_complete() else 'FAILED'}",
        outcome="SUCCESS" if engine.is_goal_complete() else "FAIL",
        metadata={
            "session_id": session_id,
            "goal_id": goal_graph.goal_id,
            "latency_ms": goal_latency_ms,
            "cost": round(total_goal_cost, 6),
            "is_goal_complete": engine.is_goal_complete()
        }
    )
             
    return {
        "goal_graph": engine.goal.to_dict(),
        "execution_log": execution_log,
        "final_brief": final_brief,
        "action_result": action_res,
        "execution_tracker": tracker,
        "tokens": node_tokens
    }


SPRINT_AGENTS = [
    ("ANALYST",    "Hard data, stats, technical context only."),
    ("SKEPTIC",    "Challenge assumptions. Risks and blind spots only."),
    ("STRATEGIST", "Long-term implications and opportunities only."),
]

LAUNCH_ROUND_1 = [
    ("ANALYST",    "Hard data, stats, technical context. Be comprehensive."),
    ("SKEPTIC",    "Challenge every assumption. Risks and failure modes only."),
    ("STRATEGIST", "Long-term implications, second-order effects, opportunities."),
]

LAUNCH_ROUND_2 = [
    ("HISTORIAN",   "Historical precedents and analogies. What patterns apply?"),
    ("FUTURIST",    "5-10 year implications. Disruptive possibilities only."),
    ("SYNTHESIZER", "Read ALL prior reports. Resolve contradictions, surface consensus, give single most important takeaway."),
]


def action_node(state: BabuState):
    """Execute Google Workspace API actions after research runs, resolving research placeholders programmatically."""
    import time
    action_start = time.time()
    action_data = state.get("detected_action")
    if not action_data or "action" not in action_data:
        tracker = state.get("execution_tracker") or {}
        tracker["action_duration"] = 0.0
        return {"action_result": "", "execution_tracker": tracker}

    action = action_data["action"]
    params = action_data.get("params", {})
    
    # Grab compiled research context if any parameter needs it
    research_text = ""
    if state.get("research_data"):
        research_text += "Research Reports:\n" + "\n\n".join(state["research_data"]) + "\n\n"
    if state.get("search_results"):
        research_text += "Live Web Search Results:\n" + state["search_results"]
        
    resolved_params = resolve_action_params(params, research_text=research_text)

    print(f"[GOOGLE] Executing reordered action={action} params={resolved_params}", flush=True)
    ok, msg = execute_google_action(action, resolved_params)
    print(f"[GOOGLE] Result: {ok} - {msg}", flush=True)
    
    duration = round(time.time() - action_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["action_duration"] = duration
    return {"action_result": msg, "execution_tracker": tracker}


def resolve_action_params(params: dict, research_text: str = "") -> dict:
    """Resolve profile placeholders and optional research placeholders."""
    profile = get_current_profile()
    details = profile.get("personal_details", {}) if profile else {}
    address_str = details.get("residential_address", {}).get("address", "") if isinstance(details.get("residential_address"), dict) else details.get("residential_address", "")
    
    # Helper to extract existing file path from context
    def _extract_existing_file_path(text: str) -> Optional[str]:
        if not text:
            return None
        import re
        import os
        # Check for bracketed document attachment pattern first
        m = re.search(r'\[Document Attached:\s*([^\]]+)\]', text)
        if m:
            path_candidate = m.group(1).strip()
            if os.path.exists(path_candidate) and os.path.isfile(path_candidate):
                return path_candidate
        path_candidate = text.strip()
        if os.path.exists(path_candidate) and os.path.isfile(path_candidate):
            return path_candidate
        # Split by common delimiters
        words = re.split(r'[\s"\']', text)
        for word in words:
            word_clean = word.strip().strip(".:()[]{}")
            if not word_clean:
                continue
            try:
                if os.path.exists(word_clean) and os.path.isfile(word_clean):
                    return word_clean
            except Exception:
                pass
        return None

    upstream_file_path = _extract_existing_file_path(research_text)

    def resolve_value(val):
        if not isinstance(val, str):
            return val
        val_clean = val.strip().lower().replace("_", " ").replace("'", "").replace('"', "")
        # Fuzzy substring resolution — catches 'official mail address', 'user official mail', etc.
        official_email_triggers = (
            "my official email", "my official mail", "official email address", "official mail address",
            "user official email", "user official mail", "users official email", "users official mail",
            "official email", "official mail", "my_official_email"
        )
        if any(t in val_clean for t in official_email_triggers):
            return details.get("official_email", "")
        personal_email_triggers = (
            "my personal email", "my personal mail", "personal email address", "personal mail address",
            "user personal email", "user personal mail", "users personal email", "users personal mail",
            "personal email", "personal mail", "my_personal_email"
        )
        if any(t in val_clean for t in personal_email_triggers):
            return details.get("personal_email", "")
        if any(t in val_clean for t in (
            "my mobile", "my mobile number", "my phone", "my phone number",
            "user mobile", "user phone", "my_mobile", "my_mobile_number"
        )):
            return details.get("mobile_number", "")
        if any(t in val_clean for t in ("my name", "user name", "users name", "my_name")):
            return details.get("full_name", "")
        if any(t in val_clean for t in ("my address", "user address", "users address", "my_address")):
            return address_str
        return val

    resolved_params = {}
    for k, v in (params or {}).items():
        val_str = str(v).strip()
        resolved_val = resolve_value(v)
        if resolved_val != v:
            resolved_params[k] = resolved_val
        elif "[NEEDS_RESEARCH_CONTEXT]" in val_str:
            if k in ("image_path", "file_path") and upstream_file_path:
                resolved_params[k] = upstream_file_path
            else:
                resolved_params[k] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", research_text.strip() if research_text else "(No research context found)")
        else:
            resolved_params[k] = v

    # If we have an upstream file path but it wasn't explicitly resolved, set it
    if upstream_file_path:
        if "image_path" not in resolved_params or not resolved_params["image_path"]:
            resolved_params["image_path"] = upstream_file_path
        if "file_path" not in resolved_params or not resolved_params["file_path"]:
            resolved_params["file_path"] = upstream_file_path

    return resolved_params


def task_manager_node(state: BabuState):
    """Verify research reports and state legitimacy before authorizing tool execution."""
    import time
    tm_start = time.time()
    detected = state.get("detected_action")
    detected = sanitize_single_action_payload(detected)
    active_goal = state.get("active_goal")
    
    if not active_goal:
        active_goal = {
            "goal_id": "goal_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "canonical_instruction": state["user_query"],
            "status": "NEW",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
    if not detected:
        print("[TASK MANAGER] No tool action detected. Bypassing validation.", flush=True)
        active_goal["status"] = "COMPLETED"
        
        duration = round(time.time() - tm_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["task_manager_duration"] = duration
        return {"active_goal": active_goal, "execution_tracker": tracker}
        
    print(f"[TASK MANAGER] Auditing pending action: {detected['action']}...", flush=True)
    
    action_name = detected["action"]
    if action_name not in MAKE_ACTIONS:
        active_goal["status"] = "BLOCKED"
        duration = round(time.time() - tm_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["task_manager_duration"] = duration
        return {
            "detected_action": None,
            "active_goal": active_goal,
            "action_result": f"Action blocked: unsupported action '{action_name}'.",
            "execution_tracker": tracker
        }
    tool_domain = f"action.{action_name}"
    # Fallback to general publisher domain if it is the Facebook publisher
    if action_name == "facebook_publish" or "facebook" in action_name:
        tool_domain = "social_media.facebook_publisher"
        
    try:
        from .memory import get_anti_pattern_rules
    except ImportError:
        from memory import get_anti_pattern_rules
    tool_rules = get_anti_pattern_rules(tool_domain)
    
    if tool_rules:
        print(f"[TASK MANAGER] Auditing constraints for {tool_domain}:\n{tool_rules}", flush=True)
        # If a persistent credential or OAuth block is logged, bypass execution to protect token limits
        if any(word in tool_rules.lower() for word in ["expired", "invalid", "malformed", "bypassed", "quota"]):
            print(f"[TASK MANAGER WARNING] Proactively bypassing '{action_name}' due to persistent historical failure.", flush=True)
            active_goal["status"] = "BYPASSED"
            
            duration = round(time.time() - tm_start, 2)
            tracker = state.get("execution_tracker") or {}
            tracker["task_manager_duration"] = duration
            return {
                "detected_action": None,
                "active_goal": active_goal,
                "action_result": f"Action bypassed by Task Manager due to persistent historical failures:\n{tool_rules}",
                "execution_tracker": tracker
            }
    
    # Validate context-informed parameters
    params = detected.get("params", {})
    has_placeholder = any("[NEEDS_RESEARCH_CONTEXT]" in str(v) for v in params.values())
    
    if has_placeholder:
        # Check if research successfully compiled reports
        has_research = len(state.get("research_data", [])) > 0 or len(state.get("search_results", "").strip()) > 0
        if not has_research:
            print("[TASK MANAGER WARNING] Action requires research context, but research_data is empty! Blocking execution.", flush=True)
            active_goal["status"] = "BLOCKED"
            
            duration = round(time.time() - tm_start, 2)
            tracker = state.get("execution_tracker") or {}
            tracker["task_manager_duration"] = duration
            return {
                "detected_action": None,
                "active_goal": active_goal,
                "action_result": "Action blocked by Task Manager: Missing required research context.",
                "execution_tracker": tracker
            }
        else:
            print("[TASK MANAGER SUCCESS] Research context validated. Authorizing action.", flush=True)
            active_goal["status"] = "VERIFIED"
    else:
        print("[TASK MANAGER SUCCESS] Action requires no research context. Authorizing directly.", flush=True)
        active_goal["status"] = "VERIFIED"
        
    duration = round(time.time() - tm_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["task_manager_duration"] = duration
    return {"active_goal": active_goal, "execution_tracker": tracker}


def research_dept(state: BabuState):
    import time
    research_start = time.time()
    gear = state["gear"]
    query = state["user_query"]

    if gear == "WALK":
        tracker = state.get("execution_tracker") or {}
        tracker["research_duration"] = 0.0
        return {"research_data": [], "search_results": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}, "execution_tracker": tracker}

    print(f"[SEARCH] {query[:60]}", flush=True)
    search_ctx = web_search(query)
    kb_ctx = search_knowledge(query)
    profile_ctx = search_profile(query)
    agent_tokens = []

    def build_task_dto(name: str, role: str, extra_context: str = "") -> dict:
        context = {
            "query": query,
            "datetime_utc": f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} / {(datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime('%Y-%m-%d %H:%M')} IST (Indian Standard Time)",
            "constraints": ["be concise", "cite facts from context"],
            "web_search": search_ctx or "",
            "knowledge_base": kb_ctx or "",
        }
        if profile_ctx and any(k in query.lower() for k in ("my", "me", "profile", "family", "career", "education")):
            context["profile_slice"] = profile_ctx
        if extra_context:
            context["prior_round_context"] = extra_context
        return {
            "agent": name,
            "role": role,
            "objective": f"Produce the {name.lower()} perspective for this query.",
            "context": context,
        }

    def run_agent(name: str, role: str, extra_context: str = "") -> str:
        import gc
        if agent_tokens:
            time.sleep(1.2)

        try:
            from .memory import get_anti_pattern_rules
        except ImportError:
            from memory import get_anti_pattern_rules
        anti_patterns = get_anti_pattern_rules(f"swarm_agent.{name.lower()}")
        task_dto = build_task_dto(name, role, extra_context=extra_context)
        system = (
            f"ARIA Swarm [{name}]: {role}\n\n"
            "You receive a scoped task DTO. Use only the provided context and avoid speculation."
        )
        if anti_patterns:
            system += f"\n\n{anti_patterns}"

        user_prompt = f"Task DTO:\n{json.dumps(task_dto, ensure_ascii=False)}"
        res = llm_dept.invoke([SystemMessage(content=system), HumanMessage(content=user_prompt)])
        agent_tokens.append(extract_tokens(res))
        gc.collect()
        return f"[{name}] {res.content}"

    if gear == "SPRINT":
        reports = [run_agent(name, role) for name, role in SPRINT_AGENTS]
        total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        for t in agent_tokens:
            total_tokens["prompt"] += t["prompt"]
            total_tokens["completion"] += t["completion"]
            total_tokens["total"] += t["total"]

        duration = round(time.time() - research_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["research_duration"] = duration
        return {"research_data": reports, "search_results": search_ctx, "tokens": total_tokens, "execution_tracker": tracker}

    r1 = [run_agent(name, role) for name, role in LAUNCH_ROUND_1]
    r1_ctx = "\n\n".join(r1)
    r2 = [run_agent(name, role, extra_context=r1_ctx) for name, role in LAUNCH_ROUND_2]
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for t in agent_tokens:
        total_tokens["prompt"] += t["prompt"]
        total_tokens["completion"] += t["completion"]
        total_tokens["total"] += t["total"]

    duration = round(time.time() - research_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["research_duration"] = duration
    return {"research_data": r1 + r2, "search_results": search_ctx, "tokens": total_tokens, "execution_tracker": tracker}


def deterministic_compress_reports(reports: List[str], max_chars: int = 2600) -> str:
    if not reports:
        return ""
    compressed = []
    seen = set()
    for idx, report in enumerate(reports, start=1):
        line = " ".join(str(report).split())
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        compressed.append(f"{idx}. {line}")
    merged = "\n".join(compressed)
    if len(merged) > max_chars:
        merged = merged[:max_chars].rsplit(" ", 1)[0] + " ..."
    return merged


def department_synthesizer(state: BabuState):
    reports = state.get("research_data", [])
    return {"compressed_research": deterministic_compress_reports(reports)}

def pa_node(state: BabuState):
    import time
    final_brief = state.get("final_brief")
    if final_brief and "Respond directly to user query" in final_brief:
        final_brief = None
    research      = final_brief or state.get("compressed_research") or "\n\n".join(state.get("research_data", []))
    history       = state.get("history_text", "")
    action_result = state.get("action_result", "")
    user_query    = state["user_query"]
    pending_action_notice = state.get("pending_action_notice", "")

    # Determine conversational vs workflow mode dynamically based on the planned graph
    is_conversational = True
    has_tasks = False
    graph_dict = state.get("goal_graph")
    if graph_dict:
        tasks = graph_dict.get("tasks", [])
        if len(tasks) > 1:
            is_conversational = False
        if len(tasks) >= 1:
            has_tasks = True

    # Soft Continuity: Suppress conversational history for fresh greetings to avoid residual bias
    lowered_query = user_query.lower().strip().removeprefix("/").removeprefix("!")
    for char in "?!.,":
        lowered_query = lowered_query.replace(char, "")
    lowered_query = lowered_query.strip()
    
    intent_packet_dict = state.get("routing_metadata", {}).get("intent_packet")
    is_multi_request = has_multiple_tasks_or_requests(user_query, intent_packet_dict)
    
    # Deterministic FAQ short-circuits for high-frequency queries:
    # 1. Current Time in IST
    if not is_multi_request and any(k in lowered_query for k in ("current time", "time in ist", "time here in ist", "what is the time", "what time is it")):
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        time_response = f"The current time in Indian Standard Time (IST) is **{now_ist.strftime('%I:%M %p (%A, %B %d, %Y)')}**."
        print(f"[PA NODE] Deterministic short-circuit for time query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=time_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    # 2. How old are you? / date of birth of babu
    if not is_multi_request and any(k in lowered_query for k in ("how old are you", "how old you are", "your age", "what is your age", "date of birth of babu", "babu birth", "babu creation", "dob of babu")):
        age_str = get_babu_age_string()
        age_response = f"I am **Project BABU** (Behavioral Autonomous Bureaucratic Utility). My date of birth is **May 27, 2026**. I have been active for **{age_str}**!"
        print(f"[PA NODE] Deterministic short-circuit for age query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=age_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    # 3. Who are you / Tell me about yourself
    if not is_multi_request and any(k in lowered_query for k in ("who are you", "tell me about yourself", "about yourself", "know about yourself", "describe yourself", "introduce yourself", "your identity", "what is your name")):
        identity_response = get_dynamic_self_identity()
        print(f"[PA NODE] Deterministic short-circuit for identity query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=identity_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}
        
    # 3.5 System Health Dashboard
    if not is_multi_request and any(k in lowered_query for k in ("system health", "status dashboard", "how are you doing", "what is your status", "health dashboard", "current state", "your current state", "what is your current state", "system status", "system status dashboard")):
        dashboard_response = get_system_health_dashboard()
        print(f"[PA NODE] Deterministic short-circuit for health dashboard query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=dashboard_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    # 3.6 Upgrades / ADR / Architecture Decisions / System Evolution / AKS
    if any(k in lowered_query for k in ("upgrades received", "recent upgrades", "what upgrades", "upgrades did you receive", "upgrades did you recieve", "upgrades in last", "upgrade received", "recent upgrade", "what upgrade", "upgrade did you receive", "upgrade did you recieve", "upgrade in last", "adr", "architecture decision", "tradeoff", "tradeoffs", "lessons learned", "evolution", "upgrades", "upgrade", "gemini", "dynamic imports", "runtime_index", "postmortem", "lesson", "incident", "impact_score", "highest impact", "largest impact", "biggest impact", "most impact", "supersedes", "solve", "evolve", "hierarchy")):
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp 
                FROM architecture_knowledge ORDER BY record_id ASC
            """)
            rows = cursor.fetchall()
            if rows:
                # Case 1: Comparative / Highest Impact
                if any(k in lowered_query for k in ("largest impact", "biggest impact", "highest impact", "most impact", "largest architectural impact")):
                    sorted_by_impact = sorted(rows, key=lambda x: x[9] or 0, reverse=True)
                    highest = sorted_by_impact[0]
                    upgrades_response = (
                        f"### 📈 Highest Architectural Impact Upgrade\n"
                        f"The upgrade with the highest architectural impact score is **{highest[2]}** ({highest[0]}) with an **Impact Score of {highest[9]}**.\n\n"
                        f"- **Type:** {highest[1]}\n"
                        f"- **Problem:** {highest[4]}\n"
                        f"- **Decision:** {highest[5]}\n"
                        f"- **Reason:** {highest[6]}\n"
                        f"- **Outcome:** {highest[7]}\n"
                        f"- **Trade-off:** {highest[8]}"
                    )
                # Case 2: Tradeoffs
                elif "tradeoff" in lowered_query:
                    specific_row = None
                    for r in rows:
                        # check if query specifies imports or index
                        if r[0].lower() in lowered_query or any(w in r[2].lower().split() for w in lowered_query.split() if len(w) > 3):
                            specific_row = r
                            break
                    if specific_row:
                        upgrades_response = (
                            f"### ⚖️ Tradeoffs for {specific_row[2]} ({specific_row[0]})\n"
                            f"For the decision to **{specific_row[5]}**, the tradeoffs are:\n"
                            f"- **Memory savings:** {specific_row[7]}\n"
                            f"- **vs:** {specific_row[8]}"
                        )
                    else:
                        lines = ["### ⚖️ Architectural Tradeoffs\nHere are the tradeoffs for BABU's major decisions:\n"]
                        for r in rows:
                            lines.append(f"- **{r[2]}** ({r[0]}): {r[8] or 'None'}")
                        upgrades_response = "\n".join(lines)
                # Case 3: Evolution timeline
                elif any(k in lowered_query for k in ("evolve", "evolution", "timeline", "history")):
                    phases = {}
                    for r in rows:
                        ph = r[3] or "General"
                        if ph not in phases:
                            phases[ph] = []
                        phases[ph].append(f"  * **{r[2]}** ({r[0]} - {r[1]}): {r[4]} -> Decision: {r[5]}")
                    
                    lines = ["### 🚀 BABU ARCHITECTURAL EVOLUTION"]
                    for ph in sorted(phases.keys()):
                        lines.append(f"\n#### 📍 {ph}")
                        lines.extend(phases[ph])
                    upgrades_response = "\n".join(lines)
                # Case 4: Specific problem / why query matching
                else:
                    specific_row = None
                    for r in rows:
                        # match record ID or title keywords
                        title_words = [w.lower() for w in r[2].lower().split() if len(w) > 3]
                        if r[0].lower() in lowered_query or any(w in lowered_query for w in title_words):
                            specific_row = r
                            break
                    
                    if specific_row:
                        upgrades_response = (
                            f"### 📑 {specific_row[1]}: {specific_row[2]} ({specific_row[0]})\n"
                            f"- **Problem Solved:** {specific_row[4]}\n"
                            f"- **Decision:** {specific_row[5]}\n"
                            f"- **Reason:** {specific_row[6]}\n"
                            f"- **Outcome:** {specific_row[7]}\n"
                            f"- **Tradeoff:** {specific_row[8] or 'None'}\n"
                            f"- **Impact Score:** {specific_row[9]}"
                        )
                    else:
                        # Fallback: List everything
                        lines = [
                            "### 🚀 BABU ARCHITECTURE KNOWLEDGE SYSTEM (AKS) & EVOLUTION",
                            "Here are the documented records tracking the system's key upgrades, trade-offs, and design evolutions:\n"
                        ]
                        for r in rows:
                            lines.append(
                                f"#### 📑 **{r[0]}: {r[2]}** ({r[3]}) - *{r[11]}* [Type: {r[1]}, Impact: {r[9]}]\n"
                                f"- **Problem:** {r[4]}\n"
                                f"- **Decision:** {r[5]}\n"
                                f"- **Reason:** {r[6]}\n"
                                f"- **Outcome:** {r[7]}\n"
                                f"- **Tradeoff:** {r[8]}\n"
                                f"- **Date:** {r[12]}\n"
                            )
                        upgrades_response = "\n".join(lines)
            else:
                upgrades_response = "No architecture knowledge records have been recorded in the database."
        except Exception as e:
            upgrades_response = f"Failed to retrieve upgrades from database: {e}"
        finally:
            cursor.close()
            conn.close()
        print(f"[PA NODE] Dynamic short-circuit for upgrades/ADR query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=upgrades_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    # 4. Tell me about your architecture
    if not is_multi_request and any(k in lowered_query for k in ("your architecture", "tell me about your architecture", "how are you built", "how do you work")):
        arch_response = (
            "My architecture is a decentralized LangGraph-based swarm framework. It consists of:\n"
            "1. **Strategic Planner & Intent Classifier**: Decomposes user queries and enforces capability boundaries.\n"
            "2. **Task Engine**: Orchestrates execution DAGs topologically.\n"
            "3. **Cognitive Departments**: Five specialized heads (**Research**, **Information**, **Analysis**, **Writing**, and **Execution**).\n"
            "4. **Bipartite Auditor**: A dual-stage governance gatekeeper (`PreExecutionGatekeeper` and `PostExecutionValidator`) that ensures safety and compliance."
        )
        print(f"[PA NODE] Deterministic short-circuit for architecture query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=arch_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    # 5. what are failures happened in last 5 days?
    if not is_multi_request and any(k in lowered_query for k in ("failures happened", "recent failures", "what are failures", "failures in last", "failures happened in last")):
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            if is_pg:
                cursor.execute("""
                    SELECT timestamp, goal_id, task_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            else:
                cursor.execute("""
                    SELECT timestamp, goal_id, task_id, event_type, metadata
                    FROM execution_ledger
                    WHERE event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                    ORDER BY event_id DESC
                    LIMIT 5
                """)
            rows = cursor.fetchall()
            if rows:
                lines = ["Here are the recent system failures recorded in the execution ledger:\n"]
                for r in rows:
                    ts = r[0][:19] if r[0] else "Unknown Time"
                    goal = r[1]
                    task = r[2] or "N/A"
                    event = r[3]
                    details = r[4]
                    lines.append(f"• **{ts}** | Event: `{event}` | Goal: `{goal}` | Task: `{task}`\n  *Details*: {details}")
                failures_response = "\n".join(lines)
            else:
                failures_response = "No system failures have been recorded in the execution ledger."
        except Exception as e:
            failures_response = f"Failed to retrieve failures log from database: {e}"
        finally:
            cursor.close()
            conn.close()
            
        print(f"[PA NODE] Deterministic short-circuit for failures query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=failures_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}
    
    greetings = {
        "hi", "hello", "hey", "how are you", "how's it going", "how you doing", 
        "how doing", "yo", "hi buddy", "hey buddy", "hello buddy", "good morning", 
        "good afternoon", "good evening"
    }
    is_fresh_greeting = lowered_query in greetings or any(lowered_query.startswith(g + " ") for g in greetings)
    
    if not is_multi_request and is_fresh_greeting and not action_result:
        profile = get_current_profile()
        details = profile.get("personal_details", {}) if profile else {}
        nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
        import random
        greeting_responses = [
            f"Hello {nickname}! How can I help you today?",
            f"Hi {nickname}! What can I do for you?",
            f"Hey {nickname}! How's it going?",
            f"Hello {nickname}! Hope you're having a great day. How can I assist you?",
        ]
        chosen_response = random.choice(greeting_responses)
        print(f"[PA NODE] Deterministic chitchat short-circuit for greeting: '{user_query}'", flush=True)
        response = AIMessage(content=chosen_response)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    category = None
    if intent_packet_dict:
        category = intent_packet_dict.get("query_category")
    if not category:
        try:
            from .planner import classify_intent
        except ImportError:
            from planner import classify_intent
        intent_packet = classify_intent(user_query, history, model_name=CURRENT_PA_MODEL)
        category = intent_packet.query_category

    # Extract all collected sources from the task execution log or goal graph.
    sources = {}
    graph_dict = state.get("goal_graph")
    if graph_dict:
        tasks = graph_dict.get("tasks", [])
        for t in tasks:
            t_ctx = t.get("context", {})
            sc = t_ctx.get("scoped_context", {})
            t_sources = sc.get("sources", {})
            if isinstance(t_sources, dict):
                for k, v in t_sources.items():
                    if v:
                        sources[k] = v
            for k in ("profile_slice", "knowledge_base"):
                if sc.get(k):
                    sources[k] = sc[k]

    # Enforcement of programmatic hard refusal check
    if category == "BUSINESS_INFORMATION":
        allowed_keys = ["AUTHORITY_MEMORY", "AUTHORITY_DATABASE", "AUTHORITY_LEDGER"]
        has_local_source = False
        for k in allowed_keys:
            if sources.get(k):
                has_local_source = True
                break
        for k in ("profile_slice", "knowledge_base"):
            if k in sources and sources[k]:
                has_local_source = True
                break
        
        if not has_local_source:
            refusal_msg = "Mere paas aapke actual client records ka access nahi hai."
            print(f"[PA NODE] Hard Refusal triggered: category is BUSINESS_INFORMATION with 0 local sources.", flush=True)
            return {"messages": state["messages"] + [AIMessage(content=refusal_msg)], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    is_private = is_private_data_query(user_query, category)

    if is_fresh_greeting or not is_conversational:
        history = ""

    if pending_action_notice:
        response = AIMessage(content=pending_action_notice)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Hard guard: if no action was executed this turn, never claim execution.
    if not action_result and is_action_status_query(user_query):
        response_text = "No action was executed in this turn."
        if not is_conversational:
            tracker = state.get("execution_tracker", {})
            if tracker and "start_time" in tracker:
                import time
                tot = round(time.time() - tracker["start_time"], 2)
                response_text += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
        response = AIMessage(content=response_text)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Deterministic short-circuit for basic profile facts.
    if not is_multi_request and not action_result:
        direct_fact = get_profile_fact_answer(user_query)
        if direct_fact:
            if not is_conversational:
                tracker = state.get("execution_tracker", {})
                if tracker and "start_time" in tracker:
                    import time
                    tot = round(time.time() - tracker["start_time"], 2)
                    direct_fact += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
            response = AIMessage(content=direct_fact)
            return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Dynamic L2/L3 profile retrieval fallback:
    # If there's no research, query search_profile to fetch matching personal details.
    if not research and not action_result:
        profile_ctx = search_profile(state["user_query"])
        if profile_ctx and ("[Local User Profile Matches]" in profile_ctx or "[Local User Profile" in profile_ctx):
            research = profile_ctx

    # Full profile injection for broad identity/family/business queries in conversational mode:
    # search_profile keyword search is too narrow for general questions like "what do you know about me".
    if not research and not action_result and is_profile_relevant_query(user_query):
        full_profile = get_user_profile_text("FULL")
        if full_profile:
            research = full_profile
            print(f"[PA NODE] Injecting full user profile for profile-relevant conversational query.", flush=True)

    if is_conversational:
        style = "[CONVERSATIONAL]\nBrief, warm, direct. Max two short paragraphs. Confirm any automation action clearly."
    else:
        style = "[WORKFLOW]\nStructured briefing: ## headers. Cover overview, findings, risks, outlook. End with one concrete recommendation. Dense and precise."

    # Inject live temporal awareness for PA synthesis
    from datetime import datetime, timezone, timedelta
    now_utc_dt = datetime.now(timezone.utc)
    now_ist_dt = now_utc_dt + timedelta(hours=5, minutes=30)
    now_str = f"{now_utc_dt.strftime('%A, %d %B %Y, %H:%M UTC')} / {now_ist_dt.strftime('%A, %d %B %Y, %H:%M')} IST (Indian Standard Time)"

    # Tiered Prompt Architecture
    if is_conversational:
        # Ultra-thin manifesto for casual conversational mode
        profile = get_current_profile()
        details = profile.get("personal_details", {}) if profile else {}
        nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
        manifesto = (
            f"You are ARIA, a warm, direct, and helpful personal companion. Current date/time: {now_str}.\n"
            f"Style: Warm, brief, natural human dialogue. Max two short paragraphs. Do not mention internal details.\n"
            f"Recipient: You are talking directly to {nickname}.\n"
            f"CRITICAL: If the user asks about their personal details, family, business, career, or background, you MUST use the information provided in [Internal Research] (which is retrieved from the authoritative local user profile).\n"
            f"If the required personal/business/family information is NOT present in [Internal Research] or [Conversation History], DO NOT invent, infer, or hallucinate any details (such as occupation, business name, meetings, or clients). In such cases, politely and warmly state that you do not have that information in their profile yet."
        )
    else:
        # Full Workflow Prompt
        # Only inject the full user profile if the query is profile-relevant
        if is_profile_relevant_query(user_query):
            profile_text = get_user_profile_text("FULL")
        else:
            # Otherwise, use ultra-thin context just for username and style warmness
            profile_text = get_user_profile_text("THIN")
        profile_ctx = f"\n\nUser Profile:\n{profile_text}" if profile_text else ""
        google_tools = ", ".join(MAKE_ACTIONS.keys())
        google_ctx = f"\n\nGoogle Workspace active [{google_tools}]. Confirm any triggered actions clearly."
        
        try:
            from .memory import get_anti_pattern_rules
        except ImportError:
            from memory import get_anti_pattern_rules
        pa_rules = get_anti_pattern_rules("pa")

        manifesto = (
            f"ARIA. Current date/time: {now_str}. Never reveal internal agents. {style}"
            f" Use history for context, never repeat it verbatim."
            f"{google_ctx}{profile_ctx}"
        )
        if not action_result:
            manifesto += "\n\nCRITICAL: Do not claim any action was executed/sent/created in this turn unless [Automation Result] is explicitly present."
        if pa_rules:
            manifesto += "\n\n" + pa_rules

    if is_private:
        manifesto += (
            "\n\nCRITICAL EPISTEMIC DIRECTIVES (AUTHORITY LEVELS):\n"
            "- You must strictly answer based ONLY on the [AUTHORITATIVE SOURCES] provided in the context.\n"
            "- AUTHORITY_MEMORY represents the local user profile; AUTHORITY_DATABASE represents the local knowledge base; AUTHORITY_LEDGER represents the system's ledger.\n"
            "- Do NOT fabricate, assume, or generalize any business metrics, client details, transaction numbers, or claims not explicitly listed in these sources.\n"
            "- If the requested details are not present, refuse the query or state that you do not have access to these records."
        )

    parts = []
    if history:
        parts.append(f"[Conversation History]\n{history}")
    
    session_id = state.get("session_id", "default")
    k0_ctx = retrieve_k0_memory(session_id)
    if k0_ctx:
        parts.append(f"[K0 Working Memory]\n{k0_ctx}")

    parts.append(f"User: {state['user_query']}")
    if action_result:
        parts.append(f"[Automation Result]\n{action_result}")
    if research:
        parts.append(f"[Internal Research]\n{research}")
    if sources:
        import json
        sources_str = "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in sources.items())
        parts.append(f"[AUTHORITATIVE SOURCES]\n{sources_str}")

    pa_start_time = time.time()
    pa_start_iso = datetime.now(timezone.utc).isoformat()
    response = llm_pa.invoke([SystemMessage(content=manifesto), HumanMessage(content="\n\n".join(parts))])
    pa_end_time = time.time()
    pa_end_iso = datetime.now(timezone.utc).isoformat()
    pa_latency_ms = round((pa_end_time - pa_start_time) * 1000, 2)
    pa_latency_sec = round(pa_end_time - pa_start_time, 4)
    
    # Check for planner degradation and append warning card if active
    if graph_dict and graph_dict.get("planner_status", "SUCCESS") not in ("SUCCESS", "WALK", "TEMPLATE_MATCH"):
        p_status = graph_dict.get("planner_status")
        reason_map = {
            "RATE_LIMIT": "Planner rate-limited by Groq API limits (429)",
            "NETWORK": "Planner encountered network timeout or connectivity issues",
            "PROVIDER_ERROR": "Planner API provider returned an execution error",
            "JSON_ERROR": "Planner LLM output could not be parsed as valid JSON",
            "VALIDATION_ERROR": "Planner generated an invalid or cyclic task dependency graph"
        }
        reason_text = reason_map.get(p_status, "Planner encountered an unexpected exception")
        degradation_notice = (
            "\n\n---\n"
            "⚠️ **WORKFLOW STATUS: DEGRADED**\n"
            f"• **Reason**: {reason_text}\n"
            "• **Capability Impact**: Multi-agent research planning & automation pipelines are temporarily unavailable\n"
            "• **Fallback**: Active conversational assistant recovery mode"
        )
        response.content += degradation_notice
    
    # Programmatic safeguard: ensure [IMAGE] tag is preserved in the response if found in action_result
    if action_result and "[IMAGE]" in action_result:
        # Check if the response already contains the image tag
        if "[IMAGE]" not in response.content:
            # Extract the complete [IMAGE] tag line from action_result
            match = re.search(r'(\[IMAGE\]\s*url=[^\s\n]+(?:\s+caption=[^\n]+)?)', action_result)
            if match:
                response.content += "\n\n" + match.group(1)
                
    # ​​Performance Telemetry Footnote ​​
    tracker = state.get("execution_tracker", {})
    tracker["pa_duration"] = pa_latency_sec
    if not is_conversational:
        if tracker and "start_time" in tracker:
            import time
            tot = round(time.time() - tracker["start_time"], 2)
            router_dur = tracker.get("router_duration", 0.0)
            planner_dur = tracker.get("planner_duration", 0.0)
            
            task_lats = tracker.get("task_latencies", [])
            workers_dur = round(sum(t.get("worker_ms", 0.0) for t in task_lats) / 1000.0, 2)
            audit_dur = round(sum(t.get("audit_pre_ms", 0.0) + t.get("audit_post_ms", 0.0) for t in task_lats) / 1000.0, 2)
            
            telemetry_footnote = f"\n\nSwarm profile: Router {router_dur}s | Planner {planner_dur}s | Workers {workers_dur}s | Audit {audit_dur}s | PA {pa_latency_sec}s | Total {tot}s"
            response.content += telemetry_footnote
                
    token_stats = extract_tokens(response)
    p_rate, c_rate = get_token_costs(CURRENT_PA_MODEL)
    pa_cost = (token_stats.get("prompt", 0) * p_rate) + (token_stats.get("completion", 0) * c_rate)
    try:
        goal_graph_dict = state.get("goal_graph") or {}
        active_goal_dict = state.get("active_goal") or {}
        g_id = goal_graph_dict.get("goal_id") or active_goal_dict.get("goal_id") or "G-WALK"
        log_execution_ledger_event(
            session_id=state.get("session_id", "default"),
            goal_id=g_id,
            task_id="T-PA",
            department="pa",
            event_type="PA_SYNTHESIS",
            state_before="RUNNING",
            state_after="COMPLETED",
            metadata={
                "query": user_query,
                "tokens": token_stats,
                "cost": round(pa_cost, 6),
                "response_preview": response.content[:300],
                "model": CURRENT_PA_MODEL,
                "is_estimated": False,
                "event_start_time": pa_start_iso,
                "event_end_time": pa_end_iso,
                "latency_ms": pa_latency_ms,
                "latency": pa_latency_sec
            }
        )
    except Exception as e:
        print(f"[PA TELEMETRY WARNING] Failed to log PA ledger event: {e}", flush=True)
    try:
        try:
            from .memory import log_workflow_event
        except ImportError:
            from memory import log_workflow_event
        tracker = state.get("execution_tracker", {})
        log_workflow_event(
            session_id=state.get("session_id", "default"),
            gear="DYNAMIC",
            sequence=["router", "planner", "executor", "pa"] if not is_conversational else ["router", "pa"],
            total_tokens=token_stats.get("total", 0),
            latency_seconds=tracker.get("research_duration", 0.0) + tracker.get("task_manager_duration", 0.0) + tracker.get("action_duration", 0.0),
            success=True,
            note=state.get("user_query", "")[:160],
        )
    except Exception as e:
        print(f"[WORKFLOW MEMORY WARNING] Failed to log workflow event: {e}", flush=True)

    return {"messages": state["messages"] + [response], "tokens": token_stats, "execution_tracker": tracker}


# â”€â”€ Graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

workflow = StateGraph(BabuState)
workflow.add_node("router",       intent_router)
workflow.add_node("planner",      planner_node)
workflow.add_node("executor",     task_executor_node)
workflow.add_node("pa",           pa_node)

workflow.set_entry_point("router")
workflow.add_conditional_edges("router", route_after_router, {
    "plan": "planner",
    "pending": "pa",
    "pa": "pa",
})
workflow.add_edge("planner", "executor")
workflow.add_edge("executor", "pa")
workflow.add_edge("pa",           END)
if checkpointer:
    babu_brain = workflow.compile(checkpointer=checkpointer)
else:
    babu_brain = workflow.compile()


# â”€â”€ Core invoke helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def invoke_babu(message: str, session_id: str = "default", goal_id: Optional[str] = None, gear: str = "LAUNCH") -> tuple[str, str, dict]:
    t_start = time.time()
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    
    epoch_id = f"{session_id}:{goal_id}"
    
    if is_epoch_sealed(epoch_id):
        print(f"[GOVERNANCE REFUSAL] Epoch '{epoch_id}' is sealed. Execution blocked.", flush=True)
        return "This execution epoch is completed and permanently sealed. No further actions or mutations are permitted.", "WALK", {}
    
    history_text = get_history_text(session_id)
    
    config = {}
    if checkpointer:
        config = {"configurable": {"thread_id": epoch_id}}
        
    output = babu_brain.invoke({
        "messages":       [HumanMessage(content=message)],
        "research_data":  [],
        "user_query":     message,
        "history_text":   history_text,
        "session_id":     session_id,
        "search_results": "",
        "action_result":  "",
        "detected_action": None,
        "active_goal":    {"goal_id": goal_id},
        "compressed_research": "",
        "routing_metadata": {},
        "pending_action_notice": "",
        "goal_graph":     None,
        "execution_log":  [],
        "final_brief":    "",
        "tokens":         {"prompt": 0, "completion": 0, "total": 0},
        "knowledge_classes": [],
        "source_records": [],
        "conversation_reference": False,
        "is_deterministic_response": False
    }, config)
    
    reply = output["messages"][-1].content
    tokens = output.get("tokens", {"prompt": 0, "completion": 0, "total": 0})
    # If pa_node fired a deterministic short-circuit (no LLM used), zero out accumulated ic_tokens
    if output.get("is_deterministic_response"):
        tokens = {"prompt": 0, "completion": 0, "total": 0}
    
    add_to_history(session_id, message, reply)
    
    goal_graph_dict = output.get("goal_graph")
    if goal_graph_dict:
        status = goal_graph_dict.get("status", "ACTIVE")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            t_seal_start = time.time()
            seal_epoch(epoch_id)
            t_seal_duration = round(time.time() - t_seal_start, 4)
            t_seal_ms = round(t_seal_duration * 1000, 2)
            tracker = output.get("execution_tracker") or {}
            tracker["governance_duration"] = t_seal_duration
            
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_id,
                task_id=None,
                department="governance",
                event_type="EPOCH_SEAL",
                state_before="ACTIVE",
                state_after="SEALED",
                metadata={
                    "latency_sec": t_seal_duration,
                    "latency_ms": t_seal_ms,
                    "tokens": {"prompt": 0, "completion": 0, "total": 0},
                    "cost": 0.0,
                    "model": "rules_engine"
                }
            )
            compact_completed_session_history(session_id)
            
            # --- E[Temp] Promotion / Demotion Logic ---
            try:
                planner_status = goal_graph_dict.get("planner_status", "")
                is_template = (planner_status == "TEMPLATE_MATCH")
                
                # Generate signature from GoalGraph dict
                tasks = goal_graph_dict.get("tasks", [])
                depts = []
                sorted_tasks = sorted(tasks, key=lambda t: t.get("task_id", ""))
                for t in sorted_tasks:
                    d = t.get("department")
                    if d and d not in depts:
                        depts.append(d)
                actions = []
                for t in sorted_tasks:
                    if t.get("department") == "execution":
                        ctx = t.get("context") or {}
                        act = ctx.get("action")
                        if act and act not in actions:
                            actions.append(act)
                sig = ":".join(depts)
                if actions:
                    sig += ":" + ":".join(actions)
                
                # Retrieve actual cost and tokens from execution ledger
                total_tokens = 0
                total_cost = 0.0
                conn, is_pg = get_db_connection()
                try:
                    cursor = conn.cursor()
                    if is_pg:
                        cursor.execute("SELECT metadata FROM execution_ledger WHERE goal_id = %s", (goal_id,))
                    else:
                        cursor.execute("SELECT metadata FROM execution_ledger WHERE goal_id = ?", (goal_id,))
                    rows = cursor.fetchall()
                    for r in rows:
                        if r[0]:
                            meta = json.loads(r[0]) if isinstance(r[0], str) else r[0]
                            if meta and "cost" in meta:
                                total_cost += meta.get("cost", 0.0)
                            if meta and "tokens" in meta:
                                total_tokens += meta.get("tokens", {}).get("total", 0)
                    cursor.close()
                except Exception as e:
                    print(f"[SEALING METRICS ERROR] Failed to fetch ledger costs: {e}", flush=True)
                finally:
                    conn.close()

                current_latency = time.time() - t_start
                
                if is_template:
                    # Update template metrics in DB
                    conn, is_pg = get_db_connection()
                    try:
                        cursor = conn.cursor()
                        if is_pg:
                            cursor.execute(
                                "SELECT execution_count, success_count, consecutive_failures, average_execution_time, average_token_cost, status FROM trusted_templates WHERE template_signature = %s",
                                (sig,)
                            )
                        else:
                            cursor.execute(
                                "SELECT execution_count, success_count, consecutive_failures, average_execution_time, average_token_cost, status FROM trusted_templates WHERE template_signature = ?",
                                (sig,)
                            )
                        row = cursor.fetchone()
                        if row:
                            old_count, old_success, old_consecutive, old_avg_time, old_avg_cost, current_status = row
                            new_count = old_count + 1
                            new_success = old_success + (1 if status == "COMPLETED" else 0)
                            new_consecutive = 0 if status == "COMPLETED" else (old_consecutive + 1)
                            
                            new_avg_time = ((old_avg_time * old_count) + current_latency) / new_count
                            new_avg_cost = ((old_avg_cost * old_count) + total_cost) / new_count
                            
                            try:
                                from .governance import get_policy
                            except ImportError:
                                from governance import get_policy
                            demotion_max_failures = get_policy("demotion_max_failures", 2)
                            demotion_min_success_rate = get_policy("demotion_min_success_rate", 0.90)
                            
                            success_rate = new_success / new_count if new_count > 0 else 1.0
                            new_status = current_status
                            
                            if new_consecutive >= demotion_max_failures:
                                if current_status == "DEMOTED":
                                    new_status = "RETIRED"
                                    print(f"[GOVERNANCE DEMOTION] Template {sig} RETIRED due to persistent failures.", flush=True)
                                else:
                                    new_status = "DEMOTED"
                                    print(f"[GOVERNANCE DEMOTION] Template {sig} DEMOTED due to {new_consecutive} consecutive failures.", flush=True)
                            elif new_count >= 5 and success_rate < demotion_min_success_rate:
                                new_status = "DEMOTED"
                                print(f"[GOVERNANCE DEMOTION] Template {sig} DEMOTED due to success rate {success_rate:.2f} < {demotion_min_success_rate}.", flush=True)
                                
                            last_used = datetime.now(timezone.utc).isoformat()
                            if is_pg:
                                cursor.execute(
                                    """
                                    UPDATE trusted_templates SET
                                        execution_count = %s,
                                        success_count = %s,
                                        consecutive_failures = %s,
                                        average_execution_time = %s,
                                        average_token_cost = %s,
                                        status = %s,
                                        last_used = %s
                                    WHERE template_signature = %s
                                    """,
                                    (new_count, new_success, new_consecutive, new_avg_time, new_avg_cost, new_status, last_used, sig)
                                )
                            else:
                                cursor.execute(
                                    """
                                    UPDATE trusted_templates SET
                                        execution_count = ?,
                                        success_count = ?,
                                        consecutive_failures = ?,
                                        average_execution_time = ?,
                                        average_token_cost = ?,
                                        status = ?,
                                        last_used = ?
                                    WHERE template_signature = ?
                                    """,
                                    (new_count, new_success, new_consecutive, new_avg_time, new_avg_cost, new_status, last_used, sig)
                                )
                            conn.commit()
                        cursor.close()
                    except Exception as db_err:
                        print(f"[SEALING METRICS ERROR] Failed to update template metrics: {db_err}", flush=True)
                    finally:
                        conn.close()
                elif status == "COMPLETED":
                    # Check Promotion eligibility
                    conn, is_pg = get_db_connection()
                    run_count = 0
                    success_count = 0
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT goal_id, metadata FROM execution_ledger WHERE event_type = 'PLANNING' ORDER BY event_id DESC LIMIT 50")
                        rows = cursor.fetchall()
                        goal_ids = []
                        for r in rows:
                            goal_id_val, meta_str = r
                            meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
                            if meta:
                                graph_dict = meta.get("graph")
                                if graph_dict:
                                    g_tasks = graph_dict.get("tasks", [])
                                    g_depts = []
                                    g_sorted_tasks = sorted(g_tasks, key=lambda t: t.get("task_id", ""))
                                    for gt in g_sorted_tasks:
                                        gd = gt.get("department")
                                        if gd and gd not in g_depts:
                                            g_depts.append(gd)
                                    g_actions = []
                                    for gt in g_sorted_tasks:
                                        if gt.get("department") == "execution":
                                            g_ctx = gt.get("context") or {}
                                            g_act = g_ctx.get("action")
                                            if g_act and g_act not in g_actions:
                                                g_actions.append(g_act)
                                    g_sig = ":".join(g_depts)
                                    if g_actions:
                                        g_sig += ":" + ":".join(g_actions)
                                        
                                    if g_sig == sig:
                                        goal_ids.append(goal_id_val)
                        
                        goal_ids = list(set(goal_ids))
                        if goal_ids:
                            placeholders = ",".join(["%s" if is_pg else "?" for _ in goal_ids])
                            query_str = f"SELECT goal_id, event_type FROM execution_ledger WHERE goal_id IN ({placeholders}) AND event_type IN ('EPOCH_SEAL', 'GOAL_COMPLETED')"
                            cursor.execute(query_str, tuple(goal_ids))
                            completed_goals = {row[0] for row in cursor.fetchall()}
                            
                            run_count = len(goal_ids)
                            success_count = len(completed_goals)
                        cursor.close()
                    except Exception as prom_err:
                        print(f"[SEALING PROMOTION ERROR] Failed to check promotion eligibility: {prom_err}", flush=True)
                    finally:
                        conn.close()
                        
                    try:
                        from .governance import get_policy
                    except ImportError:
                        from governance import get_policy
                    promotion_min_runs = get_policy("promotion_min_runs", 5)
                    promotion_success_rate = get_policy("promotion_success_rate", 0.95)
                    
                    success_rate = success_count / run_count if run_count > 0 else 0.0
                    
                    already_exists = False
                    conn, is_pg = get_db_connection()
                    try:
                        cursor = conn.cursor()
                        if is_pg:
                            cursor.execute("SELECT 1 FROM trusted_templates WHERE template_signature = %s AND status = 'ACTIVE'", (sig,))
                        else:
                            cursor.execute("SELECT 1 FROM trusted_templates WHERE template_signature = ? AND status = 'ACTIVE'", (sig,))
                        if cursor.fetchone():
                            already_exists = True
                        cursor.close()
                    except Exception as db_err:
                        pass
                    finally:
                        conn.close()
                        
                    if run_count >= promotion_min_runs and success_rate >= promotion_success_rate and not already_exists:
                        promotion_note = (
                            f"\n\n💡 *System Suggestion: Promote Workflow*\n"
                            f"The workflow signature `{sig}` has successfully run {success_count}/{run_count} times.\n"
                            f"To compile this workflow into ARIA's muscle memory (E[Temp]), approve by sending:\n"
                            f"`/promote {sig} {goal_id}`"
                        )
                        reply += promotion_note
            except Exception as outer_err:
                print(f"[SEALING METRICS ERROR] General error in promotion/demotion checks: {outer_err}", flush=True)
            
    try:
        save_k0_memory_entry(session_id, goal_id, message, reply, output)
    except Exception as k0_err:
        print(f"[K0 HOOK ERROR] Failed to save K0 working memory: {k0_err}", flush=True)

    return reply, "DYNAMIC", tokens


# â”€â”€ Health / chat HTTP server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARIA — Cognitive Swarm Telemetry Control Panel</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {
    --bg-base: #07070e;
    --bg-card: rgba(18, 18, 35, 0.4);
    --border-color: rgba(255, 255, 255, 0.08);
    --border-hover: rgba(124, 58, 237, 0.3);
    --text-primary: #f4f4f5;
    --text-secondary: #a1a1aa;
    --color-research: #06b6d4;
    --color-analysis: #f59e0b;
    --color-writing: #a855f7;
    --color-execution: #10b981;
    --color-pa: #ec4899;
    --color-governance: #6366f1;
  }
  
  * {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  
  body {
    background: var(--bg-base);
    color: var(--text-primary);
    min-height: 100vh;
    overflow-x: hidden;
    background-image: 
      radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
      radial-gradient(at 100% 0%, rgba(236, 72, 153, 0.1) 0px, transparent 50%),
      radial-gradient(at 50% 100%, rgba(6, 182, 212, 0.08) 0px, transparent 50%);
    background-attachment: fixed;
    padding: 40px 20px;
  }
  
  .container {
    max-width: 1400px;
    margin: 0 auto;
  }
  
  /* Header section */
  header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 40px;
    border-bottom: 1px solid var(--border-color);
    padding-bottom: 24px;
    flex-wrap: wrap;
    gap: 16px;
  }
  
  .brand-group {
    display: flex;
    align-items: center;
    gap: 16px;
  }
  
  .logo-glow {
    width: 48px;
    height: 48px;
    background: linear-gradient(135deg, #6366f1, #ec4899);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 800;
    font-size: 1.5rem;
    color: #fff;
    box-shadow: 0 0 20px rgba(99, 102, 241, 0.5);
  }
  
  h1 {
    font-size: 1.8rem;
    font-weight: 800;
    letter-spacing: -0.5px;
    background: linear-gradient(to right, #ffffff, #a1a1aa);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }
  
  .sub-title {
    color: var(--text-secondary);
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 2px;
    margin-top: 2px;
  }
  
  .status-badge {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34d399;
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 0.8rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  .pulse-dot {
    width: 8px;
    height: 8px;
    background: #10b981;
    border-radius: 50%;
    animation: pulse-ring 2s infinite;
  }
  
  @keyframes pulse-ring {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
    70% { transform: scale(1); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
  }
  
  /* Counter card grid */
  .metrics-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 24px;
    margin-bottom: 40px;
  }
  
  .metric-card {
    background: var(--bg-card);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--border-color);
    border-radius: 16px;
    padding: 24px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    transition: all 0.3s ease;
  }
  
  .metric-card:hover {
    border-color: var(--border-hover);
    transform: translateY(-2px);
  }
  
  .metric-label {
    color: var(--text-secondary);
    font-size: 0.85rem;
    font-weight: 500;
    letter-spacing: 0.5px;
    margin-bottom: 12px;
  }
  
  .metric-value {
    font-size: 2.2rem;
    font-weight: 800;
    background: linear-gradient(to right, #ffffff, #e4e4e7);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: -1px;
  }
  
  .metric-footer {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 8px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  
  /* Split section for analytics and allocation */
  .layout-split {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 32px;
    margin-bottom: 40px;
  }
  
  @media (max-width: 1024px) {
    .layout-split {
      grid-template-columns: 1fr;
    }
  }
  
  .dashboard-panel {
    background: var(--bg-card);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid var(--border-color);
    border-radius: 16px;
    padding: 28px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
    position: relative;
    overflow: hidden;
  }
  
  .panel-title {
    font-size: 1.1rem;
    font-weight: 700;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
    padding-bottom: 14px;
  }
  
  .text-muted {
    color: var(--text-secondary);
    font-size: 0.8rem;
  }
  
  .text-highlight {
    color: var(--text-primary);
    font-weight: 600;
  }
  
  /* Allocation progress bars */
  .allocation-item {
    margin-bottom: 18px;
  }
  
  .allocation-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 0.88rem;
    margin-bottom: 8px;
  }
  
  .allocation-name {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 600;
  }
  
  .dot-indicator {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
  }
  
  .allocation-value {
    color: var(--text-secondary);
    font-size: 0.82rem;
  }
  
  .progress-bg {
    width: 100%;
    height: 6px;
    background: rgba(255, 255, 255, 0.04);
    border-radius: 10px;
    overflow: hidden;
  }
  
  .progress-fill {
    height: 100%;
    border-radius: 10px;
    transition: width 1s ease-in-out;
  }
  
  /* Table Styles */
  .table-wrapper {
    width: 100%;
    overflow-x: auto;
  }
  
  table {
    width: 100%;
    border-collapse: collapse;
    text-align: left;
    font-size: 0.88rem;
  }
  
  th {
    color: var(--text-secondary);
    font-weight: 600;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border-color);
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  
  td {
    padding: 16px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    vertical-align: middle;
  }
  
  tr:hover td {
    background: rgba(255, 255, 255, 0.01);
  }
  
  /* Filter controls */
  .table-controls {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    gap: 16px;
    flex-wrap: wrap;
  }
  
  .search-box {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border-color);
    border-radius: 10px;
    color: var(--text-primary);
    padding: 10px 16px;
    font-size: 0.88rem;
    outline: none;
    width: 320px;
    transition: all 0.3s;
  }
  
  .search-box:focus {
    border-color: var(--border-hover);
    box-shadow: 0 0 15px rgba(124, 58, 237, 0.15);
  }
  
  .tab-filters {
    display: flex;
    background: rgba(255, 255, 255, 0.03);
    padding: 4px;
    border-radius: 10px;
    border: 1px solid var(--border-color);
    gap: 4px;
  }
  
  .tab-btn {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 6px 14px;
    border-radius: 8px;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }
  
  .tab-btn:hover {
    color: var(--text-primary);
  }
  
  .tab-btn.active {
    background: rgba(255, 255, 255, 0.08);
    color: var(--text-primary);
  }
  
  /* Badges */
  .badge-category {
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 0.72rem;
    font-weight: 700;
    text-transform: uppercase;
    display: inline-block;
  }
  
  .badge-research { background: rgba(6, 182, 212, 0.1); border: 1px solid rgba(6, 182, 212, 0.2); color: #22d3ee; }
  .badge-analysis { background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.2); color: #fbbf24; }
  .badge-writing { background: rgba(168, 85, 247, 0.1); border: 1px solid rgba(168, 85, 247, 0.2); color: #c084fc; }
  .badge-execution { background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.2); color: #34d399; }
  .badge-pa { background: rgba(236, 72, 153, 0.1); border: 1px solid rgba(236, 72, 153, 0.2); color: #f472b6; }
  .badge-governance { background: rgba(99, 102, 241, 0.1); border: 1px solid rgba(99, 102, 241, 0.2); color: #818cf8; }
  
  .badge-provider {
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.65rem;
    font-weight: 800;
    text-transform: uppercase;
    margin-right: 6px;
    display: inline-block;
  }
  .prov-google { background: rgba(219, 68, 85, 0.15); color: #f87171; border: 1px solid rgba(219, 68, 85, 0.2); }
  .prov-openai { background: rgba(16, 163, 127, 0.15); color: #34d399; border: 1px solid rgba(16, 163, 127, 0.2); }
  .prov-groq { background: rgba(245, 98, 0, 0.15); color: #f59e0b; border: 1px solid rgba(245, 98, 0, 0.2); }
  .prov-openrouter { background: rgba(124, 58, 237, 0.15); color: #a78bfa; border: 1px solid rgba(124, 58, 237, 0.2); }
  
  .badge-method {
    font-size: 0.65rem;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    display: inline-block;
  }
  .meth-est { background: rgba(255,255,255,0.04); color: var(--text-secondary); }
  .meth-meas { background: rgba(16, 185, 129, 0.1); color: #34d399; }
  
  /* Reasoning Panel custom style */
  .reasoning-panel th {
    font-size: 0.78rem;
  }
  
  /* Query Controls */
  .query-controls {
    display: flex;
    gap: 16px;
    align-items: center;
    margin-top: 16px;
    flex-wrap: wrap;
  }
  
  .gear-selector {
    display: flex;
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 4px;
    gap: 4px;
  }
  
  .gear-btn {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    padding: 10px 20px;
    border-radius: 9px;
    font-size: 0.85rem;
    font-weight: 700;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  
  .gear-btn:hover {
    color: var(--text-primary);
  }
  
  .gear-btn.active {
    background: linear-gradient(135deg, #6366f1, #ec4899);
    color: #fff;
    box-shadow: 0 0 15px rgba(99, 102, 241, 0.5);
  }
  
  .trigger-btn {
    background: linear-gradient(135deg, #10b981, #06b6d4);
    border: none;
    border-radius: 12px;
    color: #fff;
    padding: 14px 32px;
    font-weight: 700;
    font-size: 0.9rem;
    cursor: pointer;
    transition: all 0.3s ease;
    display: flex;
    align-items: center;
    gap: 10px;
    box-shadow: 0 0 20px rgba(16, 185, 129, 0.3);
  }
  
  .trigger-btn:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 0 25px rgba(16, 185, 129, 0.6);
  }
  
  .trigger-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  
  .spinner {
    width: 18px;
    height: 18px;
    border: 2px solid rgba(255,255,255,0.3);
    border-top-color: #fff;
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
  
  /* Model Controller Grid & Cards */
  .model-card-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }
  
  @media (max-width: 768px) {
    .model-card-grid {
      grid-template-columns: 1fr;
    }
  }
  
  .model-card {
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    transition: all 0.3s ease;
  }
  
  .model-card:hover {
    border-color: var(--border-hover);
    background: rgba(255, 255, 255, 0.03);
  }
  
  .model-card-title {
    font-size: 0.9rem;
    font-weight: 700;
    color: var(--text-primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  
  .model-select {
    width: 100%;
    background: rgba(7, 7, 14, 0.8);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    color: var(--text-primary);
    padding: 10px 14px;
    font-size: 0.85rem;
    font-weight: 600;
    outline: none;
    cursor: pointer;
    transition: border-color 0.2s;
  }
  
  .model-select:focus {
    border-color: #6366f1;
  }
  
  /* Badge styles for operational statuses */
  .badge-status {
    padding: 4px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
  .status-working {
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.3);
    color: #34d399;
  }
  .status-rate_limited {
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.3);
    color: #fbbf24;
  }
  .status-timeout_degraded {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #f87171;
  }
  
  /* Auth API token input style */
  .auth-input-group {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  
  .auth-box {
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid var(--border-color);
    border-radius: 8px;
    color: var(--text-primary);
    padding: 8px 12px;
    font-size: 0.8rem;
    outline: none;
    width: 150px;
    transition: all 0.3s ease;
  }
  
  .auth-box:focus {
    border-color: var(--border-hover);
    width: 200px;
    box-shadow: 0 0 10px rgba(124, 58, 237, 0.2);
  }
  
  footer {
    text-align: center;
    margin-top: 60px;
    color: #52525b;
    font-size: 0.8rem;
    letter-spacing: 0.5px;
    border-top: 1px solid var(--border-color);
    padding-top: 24px;
  }
  /* Latency & Heatmap Styles */
  .heatmap-item {
    margin-bottom: 12px;
  }
  .heatmap-header {
    display: flex;
    justify-content: space-between;
    font-size: 0.8rem;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .heatmap-bar-bg {
    background: rgba(255, 255, 255, 0.04);
    height: 10px;
    border-radius: 5px;
    overflow: hidden;
  }
  .heatmap-bar-fill {
    height: 100%;
    border-radius: 5px;
    transition: width 0.8s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.3s;
  }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="brand-group">
      <div class="logo-glow">A</div>
      <div>
        <h1>ARIA COGNITIVE SWARM</h1>
        <p class="sub-title">Real-Time Telemetry & Token Ledger</p>
      </div>
    </div>
    <div style="display: flex; align-items: center; gap: 16px; flex-wrap: wrap;">
      <div class="auth-input-group">
        <span style="font-size: 0.75rem; font-weight: 600; color: var(--text-secondary); letter-spacing: 0.5px;">API KEY</span>
        <input type="password" id="auth-token-input" class="auth-box" placeholder="Optional token..." oninput="saveAuthToken()">
      </div>
      <div class="status-badge">
        <span class="pulse-dot"></span>
        <span id="refresh-status">LIVE AUTO-REFRESH</span>
      </div>
    </div>
  </header>
  
  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-label">Total Swarm Tokens</div>
      <div class="metric-value" id="val-total-tokens">-</div>
      <div class="metric-footer" id="val-prompt-comp">-</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Estimated USD Cost</div>
      <div class="metric-value" id="val-total-cost">-</div>
      <div class="metric-footer">Dynamically calculated by model rates</div>
    </div>
    <div class="metric-card">
      <div class="metric-label">Operations Logged</div>
      <div class="metric-value" id="val-ops-count">-</div>
      <div class="metric-footer" id="val-unique-sessions">-</div>
    </div>
  </div>
  
  <!-- Swarm Dispatcher Control Panel -->
  <div class="dashboard-panel query-panel" style="margin-bottom: 40px;">
    <div class="panel-title">
      <span>Swarm Operations Dispatcher Cockpit</span>
      <span class="text-muted" style="font-weight: normal;">Decompose goals or run queries directly across swarm engines</span>
    </div>
    <div class="query-controls">
      <input type="text" id="query-input" class="search-box" style="flex: 1; padding: 14px 20px; font-size: 1rem;" placeholder="Analyze spreadsheet, compile research report, send email summary...">
      <button class="trigger-btn" id="trigger-btn" onclick="submitQuery()">
        <span id="trigger-text">TRIGGER ACTION</span>
        <div class="spinner" id="trigger-spinner" style="display: none;"></div>
      </button>
    </div>
    
    <div class="execution-output-wrapper" id="execution-output-wrapper" style="display: none; margin-top: 24px; border-top: 1px solid var(--border-color); padding-top: 20px;">
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
        <span style="font-size: 0.85rem; font-weight: 700; text-transform: uppercase; color: var(--color-research); letter-spacing: 0.5px;">Swarm Output Execution Feed</span>
        <span id="output-meta" style="font-size: 0.78rem; color: var(--text-secondary);"></span>
      </div>
      <div id="execution-output" style="background: rgba(0,0,0,0.3); padding: 20px; border-radius: 12px; font-family: monospace; font-size: 0.9rem; line-height: 1.6; white-space: pre-wrap; max-height: 350px; overflow-y: auto; border: 1px solid rgba(255,255,255,0.04); color: #f4f4f5;"></div>
    </div>

    <!-- Action Authorization Controls Panel -->
    <div id="dashboard-auth-container" style="display: none; background: rgba(99, 102, 241, 0.08); border: 1px solid rgba(99, 102, 241, 0.25); border-radius: 12px; padding: 20px; margin-top: 24px; animation: fadeIn 0.3s ease;">
      <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
        <span style="font-size: 0.85rem; font-weight: 700; text-transform: uppercase; color: #818cf8; letter-spacing: 0.5px;">🛡️ Action Authorization Required</span>
        <span id="dashboard-auth-type" style="font-size: 0.72rem; background: rgba(99, 102, 241, 0.2); color: #818cf8; padding: 3px 10px; border-radius: 12px; font-weight: 600; text-transform: uppercase;"></span>
      </div>
      <div id="dashboard-auth-details" style="font-size: 0.9rem; color: #a1a1aa; line-height: 1.6; background: rgba(0, 0, 0, 0.2); padding: 15px; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.05); margin-bottom: 15px; white-space: pre-wrap; max-height: 200px; overflow-y: auto; font-family: monospace;"></div>
      <div id="dashboard-auth-image-container" style="display: none; text-align: center; margin-bottom: 15px;">
        <img id="dashboard-auth-img" style="max-width: 100%; max-height: 180px; object-fit: cover; border-radius: 8px; border: 1px solid rgba(255, 255, 255, 0.1);" src="" alt="Post Preview">
      </div>
      <div style="display: flex; gap: 12px; justify-content: flex-end;">
        <button onclick="sendDashboardApproval('cancel')" style="padding: 8px 16px; border: 1px solid rgba(239, 68, 68, 0.3); background: rgba(239, 68, 68, 0.1); color: #ef4444; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 0.85rem; transition: background 0.2s;">Reject / Cancel</button>
        <button onclick="sendDashboardApproval('approve')" id="dashboard-approve-btn" style="padding: 8px 16px; border: none; background: #10b981; color: white; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 0.85rem; transition: background 0.2s;">Approve & Execute</button>
      </div>
    </div>
  </div>
  
  <!-- Swarm Model Control & Registry Split Panel -->
  <div class="layout-split" style="margin-bottom: 40px;">
    <!-- Model Controller swapper -->
    <div class="dashboard-panel">
      <div class="panel-title">
        <span>Active Swarm Intelligence Mapping</span>
        <span class="text-muted" style="font-weight: normal;">Switch engines in real-time</span>
      </div>
      <div class="model-card-grid">
        <div class="model-card">
          <div class="model-card-title">
            <span class="dot-indicator" style="background:#6366f1"></span>
            Swarm Operations Engine
          </div>
          <div class="text-muted" style="font-size:0.75rem; line-height:1.4;">Drives Strategic Planner, Swarm Workers (Research, Analysis, Writing), and Bipartite Auditor.</div>
          <select id="swarm-model-select" class="model-select" onchange="switchModel('swarm', this.value)">
            <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
            <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
            <option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
            <option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
            <option value="llama-3.3-70b-versatile">Llama 3.3 70B (Groq)</option>
            <option value="llama-3.1-8b-instant">Llama 3.1 8B (Groq)</option>
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o-Mini</option>
            <option value="o1-mini">o1-mini</option>
          </select>
        </div>
        
        <div class="model-card">
          <div class="model-card-title">
            <span class="dot-indicator" style="background:#ec4899"></span>
            Personal Assistant Engine
          </div>
          <div class="text-muted" style="font-size:0.75rem; line-height:1.4;">Synthesizes final briefs, formats direct Telegram / Chat responses, and structures briefs.</div>
          <select id="pa-model-select" class="model-select" onchange="switchModel('pa', this.value)">
            <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
            <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
            <option value="gemini-1.5-pro">Gemini 1.5 Pro</option>
            <option value="gemini-1.5-flash">Gemini 1.5 Flash</option>
            <option value="llama-3.3-70b-versatile">Llama 3.3 70B (Groq)</option>
            <option value="llama-3.1-8b-instant">Llama 3.1 8B (Groq)</option>
            <option value="gpt-4o">GPT-4o</option>
            <option value="gpt-4o-mini">GPT-4o-Mini</option>
            <option value="o1-mini">o1-mini</option>
          </select>
        </div>
      </div>
    </div>
    
    <!-- Intelligence Matrix Registry -->
    <div class="dashboard-panel">
      <div class="panel-title">
        <span>Intelligence Capability & Availability Board</span>
      </div>
      <div class="table-wrapper" style="max-height: 250px; overflow-y: auto;">
        <table>
          <thead>
            <tr>
              <th>Model Name</th>
              <th>Provider</th>
              <th>Token Limit</th>
              <th>Operational Status</th>
              <th style="text-align: right;">Avg Latency</th>
            </tr>
          </thead>
          <tbody id="model-matrix-body">
            <!-- Populated dynamically -->
          </tbody>
        </table>
      </div>
    </div>
  </div>
  
  <div class="layout-split">
    <div class="dashboard-panel">
      <div class="panel-title">
        <span>Token Allocation</span>
        <span class="text-muted" style="font-weight: normal;">by Swarm Role</span>
      </div>
      
      <div id="allocation-bars">
        <!-- Bars populated by JS -->
      </div>
    </div>
    
    <div class="dashboard-panel">
      <div class="panel-title">
        <span>Governance & Auditor Auditing Metrics</span>
      </div>
      <div class="allocation-item" style="margin-bottom: 24px;">
        <div class="allocation-header">
          <div class="allocation-name"><span class="dot-indicator" style="background:#6366f1"></span>Governance Overhead</div>
          <div class="allocation-value" id="gov-percent">-</div>
        </div>
        <div class="progress-bg"><div class="progress-fill" id="gov-progress" style="background:#6366f1"></div></div>
      </div>
      <div class="allocation-item">
        <div class="allocation-header">
          <div class="allocation-name"><span class="dot-indicator" style="background:#06b6d4"></span>Swarm Deep Execution</div>
          <div class="allocation-value" id="exec-percent">-</div>
        </div>
        <div class="progress-bg"><div class="progress-fill" id="exec-progress" style="background:#06b6d4"></div></div>
      </div>
      <div style="margin-top: 24px; border-top: 1px solid var(--border-color); padding-top: 16px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; text-align: center;">
         <div>
            <div class="text-muted" style="font-size: 0.68rem; text-transform: uppercase; margin-bottom: 4px;">Violations</div>
            <strong id="val-planner-violations" style="color: #f87171; font-size: 1.15rem; font-weight: 700;">-</strong>
         </div>
         <div>
            <div class="text-muted" style="font-size: 0.68rem; text-transform: uppercase; margin-bottom: 4px;">Rejections</div>
            <strong id="val-gov-rejections" style="color: #fbbf24; font-size: 1.15rem; font-weight: 700;">-</strong>
         </div>
         <div>
            <div class="text-muted" style="font-size: 0.68rem; text-transform: uppercase; margin-bottom: 4px;">Recoveries</div>
            <strong id="val-recovery-invocations" style="color: #60a5fa; font-size: 1.15rem; font-weight: 700;">-</strong>
         </div>
      </div>
    </div>
    
    <div class="dashboard-panel latency-panel" style="grid-column: span 2; margin-bottom: 32px;">
      <div class="panel-title">
        <span>Swarm Event Latency &amp; Performance Cockpit</span>
        <span class="text-muted" style="font-weight: normal;">Interactive heatmap and bottleneck tracking</span>
      </div>
      
      <div class="latency-grid" style="display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 32px;">
         <div>
            <h4 style="margin: 0 0 16px 0; color: var(--text-primary); font-size: 0.88rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Component Average Latency Heatmap</h4>
            <div id="latency-heatmap-container" style="display: flex; flex-direction: column; gap: 14px;">
               <!-- Populated by JS -->
            </div>
         </div>
         
         <div style="display: flex; flex-direction: column; gap: 20px;">
            <div>
               <h4 style="margin: 0 0 12px 0; color: var(--text-primary); font-size: 0.88rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Average System Benchmarks</h4>
               <div style="background: rgba(255,255,255,0.02); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-color); display: flex; flex-direction: column; gap: 8px;">
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Planner Avg Latency:</span>
                     <strong id="val-planner-avg-lat" style="color: #6366f1;">-</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Auditor Avg Latency:</span>
                     <strong id="val-auditor-avg-lat" style="color: #06b6d4;">-</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Execution Avg Latency:</span>
                     <strong id="val-exec-avg-lat" style="color: #34d399;">-</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px; margin-top: 4px;">
                     <span class="text-muted">Self-RAG Requests:</span>
                     <strong id="val-rag-requests" style="color: #a855f7;">-</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Self-RAG Hits/Misses:</span>
                     <strong id="val-rag-hits" style="color: #e879f9;">- / -</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Self-RAG Avg Latency:</span>
                     <strong id="val-rag-avg-lat" style="color: #f472b6;">-</strong>
                  </div>
                  <div style="display: flex; justify-content: space-between; font-size: 0.8rem;">
                     <span class="text-muted">Self-RAG Tokens:</span>
                     <strong id="val-rag-tokens" style="color: #fb7185;">-</strong>
                  </div>
               </div>
            </div>
            
            <div>
               <h4 style="margin: 0 0 12px 0; color: var(--text-primary); font-size: 0.88rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Slowest Models</h4>
               <div class="table-wrapper" style="max-height: 140px; overflow-y: auto;">
                  <table style="font-size: 0.78rem;">
                     <thead>
                        <tr>
                           <th>Model</th>
                           <th style="text-align: right;">Avg Latency</th>
                        </tr>
                     </thead>
                     <tbody id="latency-models-body">
                        <!-- Populated by JS -->
                     </tbody>
                  </table>
               </div>
            </div>
         </div>
      </div>
      
      <div style="margin-top: 32px; border-top: 1px solid var(--border-color); padding-top: 24px;">
         <h4 style="margin: 0 0 16px 0; color: var(--text-primary); font-size: 0.88rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;">Top 10 Slowest Workflows (Bottlenecks)</h4>
         <div class="table-wrapper" style="max-height: 250px; overflow-y: auto;">
            <table>
               <thead>
                  <tr>
                     <th>Goal ID</th>
                     <th>Query / Goal</th>
                     <th>Timestamp</th>
                     <th style="text-align: right;">Total Latency</th>
                  </tr>
               </thead>
               <tbody id="latency-workflows-body">
                  <!-- Populated by JS -->
               </tbody>
            </table>
         </div>
      </div>
    </div>
    
    <div class="dashboard-panel reasoning-panel" style="grid-column: span 2; margin-bottom: 32px;">
      <div class="panel-title">
        <span>Reasoning Efficiency Cockpit (Outcome &divide; Resources)</span>
        <span class="text-muted" style="font-weight: normal;">Latest Swarm Goals</span>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Goal / Query</th>
              <th>Status</th>
              <th style="text-align: right;">Total Resources (Tokens)</th>
              <th style="text-align: right;">System Latency (Duration)</th>
              <th style="text-align: right;">Cost (USD)</th>
            </tr>
          </thead>
          <tbody id="reasoning-body">
            <tr>
              <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 20px;">No goals completed yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    
    <div class="dashboard-panel timeline-panel" style="grid-column: span 2; margin-bottom: 32px;">
      <div class="panel-title">
        <span>Temporal System Chronology Timeline</span>
        <span class="text-muted" style="font-weight: normal;">Granular trace of bot operations &amp; actions</span>
      </div>
      <div class="table-wrapper" style="max-height: 300px; overflow-y: auto;">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Category</th>
              <th>Summary</th>
              <th>Outcome</th>
              <th style="text-align: right;">Impact Score</th>
            </tr>
          </thead>
          <tbody id="timeline-body">
            <tr>
              <td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 20px;">No temporal events logged yet.</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="dashboard-panel ledger-panel" style="grid-column: span 2;">
      <div class="panel-title">
        <span>Unified Ledger Operations Log</span>
      </div>
      
      <div class="table-controls">
        <input type="text" id="search-input" class="search-box" placeholder="Search by Session, Goal ID, Task ID, Event Type...">
        <div class="tab-filters">
          <button class="tab-btn active" onclick="filterCategory('all')">ALL</button>
          <button class="tab-btn" onclick="filterCategory('governance')">GOVERNANCE</button>
          <button class="tab-btn" onclick="filterCategory('workers')">SWARM WORKERS</button>
          <button class="tab-btn" onclick="filterCategory('pa')">PA SYNTHESIS</button>
        </div>
      </div>
      
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Goal / Session ID</th>
              <th>Task ID</th>
              <th>Category</th>
              <th>Event Type</th>
              <th style="text-align: right;">Tokens</th>
              <th style="text-align: right;">Cost (USD)</th>
            </tr>
          </thead>
          <tbody id="ledger-body">
            <tr>
              <td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">Loading real-time ledger data...</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
  
  <footer>
    <p>ARIA Engine &bull; Self-Correction Checkpoints &bull; Bipartite Auditor &bull; SQLite Ledger</p>
  </footer>
</div>

<script>
  let dashboardSessionId = localStorage.getItem('babu_dashboard_session_id');
  if (!dashboardSessionId) {
    dashboardSessionId = 'web_dashboard_' + Math.floor(Date.now() / 1000) + '_' + Math.random().toString(36).substring(2, 9);
    localStorage.setItem('babu_dashboard_session_id', dashboardSessionId);
  }

  // Convert UTC ISO timestamps or sqlite timestamps to IST (UTC+5:30) dynamically
  function formatIST(isoString) {
    if (!isoString) return '-';
    try {
      let cleanStr = isoString.trim();
      cleanStr = cleanStr.replace(' ', 'T');
      if (!cleanStr.endsWith('Z') && !cleanStr.includes('+') && !cleanStr.includes('-')) {
        cleanStr += 'Z';
      }
      const date = new Date(cleanStr);
      if (isNaN(date.getTime())) {
        return isoString.replace('T', ' ').substring(0, 19);
      }
      const options = {
        timeZone: 'Asia/Kolkata',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      };
      const formatter = new Intl.DateTimeFormat('en-US', options);
      const parts = formatter.formatToParts(date);
      let year = '', month = '', day = '', hour = '', minute = '', second = '';
      for (const part of parts) {
        if (part.type === 'year') year = part.value;
        else if (part.type === 'month') month = part.value;
        else if (part.type === 'day') day = part.value;
        else if (part.type === 'hour') hour = part.value;
        else if (part.type === 'minute') minute = part.value;
        else if (part.type === 'second') second = part.value;
      }
      return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
    } catch (e) {
      return isoString.replace('T', ' ').substring(0, 19);
    }
  }

  let telemetryData = null;
  let activeCategory = 'all';

  // Authorization token management
  function saveAuthToken() {
    const val = document.getElementById('auth-token-input').value;
    localStorage.setItem('babu_api_token', val);
  }
  function loadAuthToken() {
    const val = localStorage.getItem('babu_api_token') || '';
    document.getElementById('auth-token-input').value = val;
  }
  function getHeaders() {
    const headers = {
      'Content-Type': 'application/json'
    };
    const token = localStorage.getItem('babu_api_token') || '';
    if (token) {
      headers['Authorization'] = 'Bearer ' + token;
      headers['X-API-Key'] = token;
    }
    return headers;
  }

  // Submit Query to /api/chat
  async function submitQuery() {
    const queryInput = document.getElementById('query-input');
    const query = queryInput.value.trim();
    if (!query) return;
    
    const triggerBtn = document.getElementById('trigger-btn');
    const triggerText = document.getElementById('trigger-text');
    const triggerSpinner = document.getElementById('trigger-spinner');
    
    const outWrapper = document.getElementById('execution-output-wrapper');
    const outDiv = document.getElementById('execution-output');
    const outMeta = document.getElementById('output-meta');
    
    triggerBtn.disabled = true;
    triggerText.textContent = "EXECUTING SWARM...";
    triggerSpinner.style.display = "block";
    
    outWrapper.style.display = "block";
    outDiv.textContent = "Swarm is initializing... Routing query through Intent Governance Gatekeeper...";
    outMeta.textContent = "Swarm Mode: Dynamic";
    
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          message: query,
          session_id: dashboardSessionId
        })
      });
      
      const resData = await response.json();
      triggerBtn.disabled = false;
      triggerText.textContent = "TRIGGER ACTION";
      triggerSpinner.style.display = "none";
      
      if (!response.ok) {
        throw new Error(resData.error || 'Server error: ' + response.status);
      }
      
      outDiv.textContent = resData.reply;
      outMeta.textContent = "Finished";
      
      // Instantly refresh telemetry to show new ledger logs
      fetchTelemetry();
      checkDashboardPending();
    } catch (e) {
      console.error(e);
      outDiv.textContent = "Error executing swarm: " + e.message;
      outMeta.textContent = "Failed";
      triggerBtn.disabled = false;
      triggerText.textContent = "TRIGGER ACTION";
      triggerSpinner.style.display = "none";
    }
  }

  // Switch Model on backend
  async function switchModel(role, modelName) {
    console.log(`Switching model for ${role} to ${modelName}`);
    try {
      const response = await fetch('/api/models/switch', {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          role: role,
          model: modelName
        })
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Switch failed');
      console.log("Model switch successful", data);
      
      // Highlight selector temporarily
      const el = document.getElementById(role + '-model-select');
      el.style.borderColor = "#10b981";
      setTimeout(() => {
        el.style.borderColor = "";
      }, 1500);
      
      fetchTelemetry();
    } catch (e) {
      alert("Failed to switch model: " + e.message);
      fetchTelemetry(); // revert selector to actual state
    }
  }

  async function fetchTelemetry() {
    const statusDot = document.getElementById('refresh-status');
    try {
      const response = await fetch('/api/telemetry', {
        headers: getHeaders()
      });
      if (!response.ok) throw new Error('API request failed');
      const data = await response.json();
      telemetryData = data;
      updateUI();
      statusDot.textContent = "LIVE AUTO-REFRESH";
      statusDot.parentElement.style.borderColor = "rgba(16, 185, 129, 0.3)";
      statusDot.parentElement.style.color = "#34d399";
    } catch (e) {
      console.error("Failed to fetch telemetry:", e);
      statusDot.textContent = "DISCONNECTED";
      statusDot.parentElement.style.borderColor = "rgba(239, 68, 68, 0.3)";
      statusDot.parentElement.style.color = "#f87171";
    }
  }

  function filterCategory(cat) {
    activeCategory = cat;
    document.querySelectorAll('.tab-btn').forEach(btn => {
      btn.classList.remove('active');
      if (btn.textContent.toLowerCase() === cat || (cat === 'workers' && btn.textContent.toLowerCase().includes('workers'))) {
        btn.classList.add('active');
      }
    });
    updateUI();
  }

  function updateUI() {
    if (!telemetryData) return;
    
    const aggregates = telemetryData.aggregates;
    const ledger = telemetryData.ledger;
    const reasoning = telemetryData.reasoning_efficiency || [];
    const latencyMetrics = telemetryData.latency_metrics || {};
    
    function formatDuration(ms) {
      if (ms === undefined || ms === null || isNaN(ms) || ms === 0.0) return '-';
      if (ms < 1000) return `${ms.toFixed(0)}ms`;
      return `${(ms / 1000.0).toFixed(2)}s`;
    }
    
    // Update Latency Benchmarks
    if (latencyMetrics.planner_average_latency !== undefined) {
      const el = document.getElementById('val-planner-avg-lat');
      if (el) el.textContent = formatDuration(latencyMetrics.planner_average_latency);
    }
    if (latencyMetrics.auditor_average_latency !== undefined) {
      const el = document.getElementById('val-auditor-avg-lat');
      if (el) el.textContent = formatDuration(latencyMetrics.auditor_average_latency);
    }
    if (latencyMetrics.execution_average_latency !== undefined) {
      const el = document.getElementById('val-exec-avg-lat');
      if (el) el.textContent = formatDuration(latencyMetrics.execution_average_latency);
    }
    
    // Update Latency Heatmap
    const heatmapContainer = document.getElementById('latency-heatmap-container');
    if (heatmapContainer) {
      heatmapContainer.innerHTML = '';
      const eventLabels = {
        'INTENT_CLASSIFICATION': 'Intent Classification',
        'PLANNING': 'Strategic Planning',
        'AUDIT_PRE': 'Pre-Execution Audit',
        'EXECUTION': 'Swarm Task Execution',
        'AUDIT_POST': 'Post-Execution Audit',
        'PA_SYNTHESIS': 'Response Synthesis',
        'GOAL_COMPLETE': 'Goal Lifecycle Outcomes'
      };
      
      let maxVal = 2000;
      const evAvg = latencyMetrics.avg_latency_by_event || {};
      for (const ev in evAvg) {
        if (evAvg[ev] > maxVal) {
          maxVal = evAvg[ev];
        }
      }
      
      for (const ev in eventLabels) {
        const avg = evAvg[ev] || 0.0;
        const label = eventLabels[ev];
        const pct = Math.min(100, (avg / maxVal * 100)).toFixed(1);
        
        let color = '#34d399'; // green
        if (avg >= 500 && avg <= 2000) {
          color = '#fbbf24'; // yellow
        } else if (avg > 2000) {
          color = '#f87171'; // red
        }
        
        const item = document.createElement('div');
        item.className = 'heatmap-item';
        item.innerHTML = `
          <div class="heatmap-header">
            <span class="text-muted" style="font-size: 0.78rem;">${label}</span>
            <span style="color: ${color}; font-size: 0.78rem; font-weight: 700;">${formatDuration(avg)}</span>
          </div>
          <div class="heatmap-bar-bg">
            <div class="heatmap-bar-fill" style="width: ${pct}%; background-color: ${color};"></div>
          </div>
        `;
        heatmapContainer.appendChild(item);
      }
    }
    
    // Update Slowest Models
    const modelsBody = document.getElementById('latency-models-body');
    if (modelsBody) {
      modelsBody.innerHTML = '';
      const slowestModels = latencyMetrics.top_10_slowest_models || [];
      if (slowestModels.length === 0) {
        modelsBody.innerHTML = `<tr><td colspan="2" style="text-align: center; color: var(--text-secondary); padding: 10px;">No model latencies.</td></tr>`;
      } else {
        slowestModels.forEach(m => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><code style="font-size: 0.72rem; color: #fff;">${m.model}</code></td>
            <td style="text-align: right; font-weight: 700; color: #fbbf24;">${formatDuration(m.avg_latency_ms)}</td>
          `;
          modelsBody.appendChild(tr);
        });
      }
    }
    
    // Update Slowest Workflows
    const workflowsBody = document.getElementById('latency-workflows-body');
    if (workflowsBody) {
      workflowsBody.innerHTML = '';
      const slowestWorkflows = latencyMetrics.top_10_slowest_workflows || [];
      if (slowestWorkflows.length === 0) {
        workflowsBody.innerHTML = `<tr><td colspan="4" style="text-align: center; color: var(--text-secondary); padding: 20px;">No slowest workflows logged yet.</td></tr>`;
      } else {
        slowestWorkflows.forEach(w => {
          const tr = document.createElement('tr');
          const ts = formatIST(w.timestamp);
          tr.innerHTML = `
            <td><code style="background:rgba(255,255,255,0.06); padding:2px 6px; border-radius:4px; font-size:0.75rem;">${w.goal_id}</code></td>
            <td><div class="text-highlight" style="max-width: 450px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${w.query}">${w.query}</div></td>
            <td class="text-muted">${ts}</td>
            <td style="text-align: right; font-weight: 700; color: #f87171;">${formatDuration(w.total_latency_ms)}</td>
          `;
          workflowsBody.appendChild(tr);
        });
      }
    }
    
    // Update Model selects if not currently active
    if (telemetryData.current_dept_model) {
      const el = document.getElementById('swarm-model-select');
      if (document.activeElement !== el) {
        el.value = telemetryData.current_dept_model;
      }
    }
    if (telemetryData.current_pa_model) {
      const el = document.getElementById('pa-model-select');
      if (document.activeElement !== el) {
        el.value = telemetryData.current_pa_model;
      }
    }
    
    // Update Capability Board
    const matrixBody = document.getElementById('model-matrix-body');
    matrixBody.innerHTML = '';
    
    if (telemetryData.model_matrix) {
      telemetryData.model_matrix.forEach(row => {
        const tr = document.createElement('tr');
        const provClass = `prov-${row.provider}`;
        const provBadge = `<span class="badge-provider ${provClass}">${row.provider}</span>`;
        
        let statusClass = 'status-working';
        let statusText = 'WORKING';
        if (row.status === 'RATE_LIMITED') {
          statusClass = 'status-rate_limited';
          statusText = 'RATE LIMITED';
        } else if (row.status === 'TIMEOUT_DEGRADED') {
          statusClass = 'status-timeout_degraded';
          statusText = 'TIMEOUT DEGRADED';
        }
        
        const latVal = row.avg_latency ? `${row.avg_latency}s` : '-';
        const isCurrent = (row.model === telemetryData.current_dept_model || row.model === telemetryData.current_pa_model);
        const nameStyle = isCurrent ? 'font-weight: 700; color: #fff;' : '';
        
        tr.innerHTML = `
          <td style="${nameStyle}">${row.model} ${isCurrent ? '<span style="color:#6366f1;font-size:0.7rem;margin-left:4px;">(ACTIVE)</span>' : ''}</td>
          <td>${provBadge}</td>
          <td class="text-muted">${row.token_limit}</td>
          <td><span class="badge-status ${statusClass}">${statusText}</span></td>
          <td style="text-align: right;"><strong style="color:#06b6d4;">${latVal}</strong></td>
        `;
        matrixBody.appendChild(tr);
      });
    }
    
    // Update Counter Cards
    document.getElementById('val-total-tokens').textContent = aggregates.total_tokens.toLocaleString();
    
    // Sum prompt/completion breakdown
    let totalPrompt = 0;
    let totalComp = 0;
    for (const key in aggregates.categories) {
      totalPrompt += aggregates.categories[key].prompt;
      totalComp += aggregates.categories[key].completion;
    }
    document.getElementById('val-prompt-comp').textContent = `${totalPrompt.toLocaleString()} prompt • ${totalComp.toLocaleString()} comp`;
    document.getElementById('val-total-cost').textContent = '$' + aggregates.total_cost.toFixed(5);
    
    document.getElementById('val-ops-count').textContent = ledger.length;
    
    // Count unique session IDs
    const sessions = new Set(ledger.map(row => row.session_id));
    document.getElementById('val-unique-sessions').textContent = `${sessions.size} active sessions`;
    
    // Render allocation bars
    const allocContainer = document.getElementById('allocation-bars');
    allocContainer.innerHTML = '';
    
    const categoriesInfo = {
      'research': { label: 'Research Worker', color: 'var(--color-research)' },
      'analysis': { label: 'Analysis Worker', color: 'var(--color-analysis)' },
      'writing': { label: 'Writing Worker', color: 'var(--color-writing)' },
      'execution': { label: 'Execution Worker', color: 'var(--color-execution)' },
      'pa': { label: 'Personal Assistant (PA)', color: 'var(--color-pa)' },
      'governance': { label: 'Governance & Audits', color: 'var(--color-governance)' }
    };
    
    const sortedCategories = Object.keys(aggregates.categories).sort((a,b) => {
      return aggregates.categories[b].total - aggregates.categories[a].total;
    });
    
    sortedCategories.forEach(cat => {
      const catData = aggregates.categories[cat];
      const percentage = aggregates.total_tokens > 0 ? (catData.total / aggregates.total_tokens * 100).toFixed(1) : 0;
      const info = categoriesInfo[cat];
      
      const item = document.createElement('div');
      item.className = 'allocation-item';
      item.innerHTML = `
        <div class="allocation-header">
          <div class="allocation-name">
            <span class="dot-indicator" style="background:${info.color}"></span>
            ${info.label}
          </div>
          <div class="allocation-value">${catData.total.toLocaleString()} tokens (${percentage}%)</div>
        </div>
        <div class="progress-bg">
          <div class="progress-fill" style="background:${info.color}; width: ${percentage}%"></div>
        </div>
      `;
      allocContainer.appendChild(item);
    });
    
    // Update Governance vs Execution metrics
    const govTokens = aggregates.categories['governance'].total;
    const workerTokens = aggregates.total_tokens - govTokens;
    const govPercent = aggregates.total_tokens > 0 ? (govTokens / aggregates.total_tokens * 100).toFixed(1) : 0;
    const workerPercent = aggregates.total_tokens > 0 ? (workerTokens / aggregates.total_tokens * 100).toFixed(1) : 0;
    
    document.getElementById('gov-percent').textContent = `${govTokens.toLocaleString()} tokens (${govPercent}%)`;
    document.getElementById('gov-progress').style.width = `${govPercent}%`;
    document.getElementById('exec-percent').textContent = `${workerTokens.toLocaleString()} tokens (${workerPercent}%)`;
    document.getElementById('exec-progress').style.width = `${workerPercent}%`;
    
    // Update newly aggregated telemetry counters
    if (telemetryData.planner_constraint_violations !== undefined) {
      document.getElementById('val-planner-violations').textContent = telemetryData.planner_constraint_violations;
    }
    if (telemetryData.governance_rejections !== undefined) {
      document.getElementById('val-gov-rejections').textContent = telemetryData.governance_rejections;
    }
    if (telemetryData.recovery_invocations !== undefined) {
      document.getElementById('val-recovery-invocations').textContent = telemetryData.recovery_invocations;
    }
    
    // Update RAG Telemetry
    if (telemetryData.retrieval_requests !== undefined) {
      document.getElementById('val-rag-requests').textContent = telemetryData.retrieval_requests;
      document.getElementById('val-rag-hits').textContent = `${telemetryData.retrieval_hits} / ${telemetryData.retrieval_misses}`;
      document.getElementById('val-rag-avg-lat').textContent = formatDuration(telemetryData.retrieval_latency_ms);
      document.getElementById('val-rag-tokens').textContent = `${telemetryData.retrieved_tokens.toLocaleString()} tokens`;
    }
    
    // Update Reasoning Efficiency Table
    const reasoningBody = document.getElementById('reasoning-body');
    reasoningBody.innerHTML = '';
    
    if (reasoning.length === 0) {
      reasoningBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 20px;">No completed goals found.</td></tr>`;
    } else {
      reasoning.forEach(goal => {
        const tr = document.createElement('tr');
        const ts = formatIST(goal.timestamp);
        
        let statusBadge = 'badge-governance';
        if (goal.status === 'COMPLETED' || goal.status === 'SUCCESS') statusBadge = 'badge-execution';
        else if (goal.status === 'FAILED') statusBadge = 'badge-pa';
        
        tr.innerHTML = `
          <td class="text-muted">${ts}</td>
          <td><div class="text-highlight" style="max-width: 450px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${goal.query}">${goal.query}</div></td>
          <td><span class="badge-category ${statusBadge}">${goal.status}</span></td>
          <td style="text-align: right;"><strong class="text-highlight">${goal.total_tokens.toLocaleString()}</strong> <span class="text-muted">tokens</span></td>
          <td style="text-align: right;"><strong style="color:#06b6d4;">${goal.duration.toFixed(2)}</strong> <span class="text-muted">sec</span></td>
          <td style="text-align: right; font-weight: 700; color:#e4e4e7;">$${goal.total_cost.toFixed(5)}</td>
        `;
        reasoningBody.appendChild(tr);
      });
    }
    
    // Update Temporal System Chronology Timeline
    const timelineBody = document.getElementById('timeline-body');
    const timeline = telemetryData.temporal_timeline || [];
    
    timelineBody.innerHTML = '';
    if (timeline.length === 0) {
      timelineBody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-secondary); padding: 20px;">No temporal events logged yet.</td></tr>`;
    } else {
      timeline.forEach(event => {
        const tr = document.createElement('tr');
        const ts = formatIST(event.timestamp);
        
        let outcomeBadge = '';
        if (event.outcome === 'SUCCESS') {
          outcomeBadge = '<span class="badge-method meth-meas" style="background: rgba(16, 185, 129, 0.2); color: #10b981;">SUCCESS</span>';
        } else if (event.outcome === 'FAIL') {
          outcomeBadge = '<span class="badge-method meth-est" style="background: rgba(239, 68, 68, 0.2); color: #ef4444;">FAIL</span>';
        } else {
          outcomeBadge = `<span class="badge-method text-muted" style="background: rgba(255, 255, 255, 0.1);">${event.outcome || '-'}</span>`;
        }
        
        let metaHtml = '';
        if (event.metadata && Object.keys(event.metadata).length > 0) {
          try {
            const prettyMeta = JSON.stringify(event.metadata);
            metaHtml = `<pre style="margin:4px 0 0 0; font-size:0.68rem; color:var(--text-secondary); white-space: pre-wrap; word-break: break-all;">${prettyMeta}</pre>`;
          } catch(e) {}
        }
        
        tr.innerHTML = `
          <td class="text-muted">${ts}</td>
          <td><span class="badge-category badge-governance" style="background: rgba(99, 102, 241, 0.2); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3);">${event.event_category}</span></td>
          <td>
            <div class="text-highlight">${event.summary}</div>
            ${metaHtml}
          </td>
          <td>${outcomeBadge}</td>
          <td style="text-align: right; font-weight: 700; color: #a1a1aa;">${event.impact_score}</td>
        `;
        timelineBody.appendChild(tr);
      });
    }
    
    // Update Ledger Table with Search + Filters
    const searchQuery = document.getElementById('search-input').value.toLowerCase().trim();
    const ledgerBody = document.getElementById('ledger-body');
    ledgerBody.innerHTML = '';
    
    const filteredLedger = ledger.filter(row => {
      // 1. Filter by category tabs
      if (activeCategory === 'governance' && row.category !== 'governance') return false;
      if (activeCategory === 'pa' && row.category !== 'pa') return false;
      if (activeCategory === 'workers' && !['research', 'analysis', 'writing', 'execution'].includes(row.category)) return false;
      
      // 2. Filter by search input
      if (searchQuery) {
        const matchesQuery = 
          row.session_id.toLowerCase().includes(searchQuery) ||
          row.goal_id.toLowerCase().includes(searchQuery) ||
          (row.task_id && row.task_id.toLowerCase().includes(searchQuery)) ||
          row.event_type.toLowerCase().includes(searchQuery) ||
          (row.department && row.department.toLowerCase().includes(searchQuery)) ||
          (row.model && row.model.toLowerCase().includes(searchQuery)) ||
          (row.provider && row.provider.toLowerCase().includes(searchQuery));
        if (!matchesQuery) return false;
      }
      return true;
    });
    
    if (filteredLedger.length === 0) {
      ledgerBody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-secondary); padding: 30px;">No matching ledger records found.</td></tr>`;
      return;
    }
    
    filteredLedger.forEach(row => {
      const tr = document.createElement('tr');
      const ts = formatIST(row.timestamp);
      
      const isEstBadge = row.is_estimated ? 
        `<span class="badge-method meth-est">Estimated</span>` : 
        `<span class="badge-method meth-meas">Measured</span>`;
        
      const provClass = `prov-${row.provider}`;
      const provBadge = `<span class="badge-provider ${provClass}">${row.provider}</span>`;
      
      tr.innerHTML = `
        <td class="text-muted">${ts}</td>
        <td>
          <div class="text-highlight">${row.goal_id}</div>
          <div class="text-muted" style="font-size: 0.72rem;">Sess: ${row.session_id}</div>
        </td>
        <td><code style="background:rgba(255,255,255,0.06); padding:2px 6px; border-radius:4px; font-size:0.75rem;">${row.task_id || '-'}</code></td>
        <td>
          <span class="badge-category badge-${row.category}">${row.category}</span>
          <div style="margin-top: 4px;">${isEstBadge}</div>
        </td>
        <td>
          <strong style="color:rgba(255,255,255,0.85);">${row.event_type}</strong>
          <div class="text-muted" style="font-size:0.72rem; margin-top:4px;">${provBadge}${row.model}</div>
        </td>
        <td style="text-align: right;">
          <div class="text-highlight">${row.total_tokens.toLocaleString()}</div>
          <div class="text-muted" style="font-size: 0.72rem;">P: ${row.prompt_tokens.toLocaleString()} • C: ${row.completion_tokens.toLocaleString()}</div>
        </td>
        <td style="text-align: right; font-weight: 700; color:#e4e4e7;">$${row.cost.toFixed(5)}</td>
      `;
      ledgerBody.appendChild(tr);
    });
  }

  // Polling for pending actions on the dashboard
  async function checkDashboardPending() {
    try {
      const res = await fetch(`/api/chat/status?session_id=${dashboardSessionId}`);
      const data = await res.json();
      
      const container = document.getElementById('dashboard-auth-container');
      const details = document.getElementById('dashboard-auth-details');
      const typeSpan = document.getElementById('dashboard-auth-type');
      const imgContainer = document.getElementById('dashboard-auth-image-container');
      const imgEl = document.getElementById('dashboard-auth-img');
      const approveBtn = document.getElementById('dashboard-approve-btn');
      
      if (data.has_pending) {
        details.textContent = data.pending_details;
        typeSpan.textContent = data.pending_type;
        
        let approveCmd = "sendDashboardApproval('approve')";
        let cancelCmd = "sendDashboardApproval('cancel')";
        if (data.pending_type === 'post') {
          approveCmd = "sendDashboardApproval('approve post')";
          cancelCmd = "sendDashboardApproval('cancel post')";
        }
        
        approveBtn.setAttribute('onclick', approveCmd);
        
        if (data.image_url) {
          imgEl.src = data.image_url;
          imgContainer.style.display = 'block';
        } else {
          imgContainer.style.display = 'none';
          imgEl.src = '';
        }
        
        container.style.display = 'block';
      } else {
        container.style.display = 'none';
      }
    } catch (e) {
      console.error("Error checking dashboard pending:", e);
    }
  }

  window.sendDashboardApproval = async function(choice) {
    const triggerBtn = document.getElementById('trigger-btn');
    const triggerText = document.getElementById('trigger-text');
    const triggerSpinner = document.getElementById('trigger-spinner');
    const outWrapper = document.getElementById('execution-output-wrapper');
    const outDiv = document.getElementById('execution-output');
    
    triggerBtn.disabled = true;
    triggerText.textContent = "SENDING DECISION...";
    triggerSpinner.style.display = "block";
    outWrapper.style.display = "block";
    outDiv.textContent = "Submitting authorization decision to the engine...";
    
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify({
          message: choice,
          session_id: dashboardSessionId
        })
      });
      
      const resData = await response.json();
      triggerBtn.disabled = false;
      triggerText.textContent = "TRIGGER ACTION";
      triggerSpinner.style.display = "none";
      
      outDiv.textContent = resData.reply;
      
      checkDashboardPending();
      fetchTelemetry();
    } catch (e) {
      console.error(e);
      outDiv.textContent = "Error sending approval decision: " + e.message;
      triggerBtn.disabled = false;
      triggerText.textContent = "TRIGGER ACTION";
      triggerSpinner.style.display = "none";
    }
  };

  // Bind events and poll
  document.getElementById('search-input').addEventListener('input', updateUI);

  // Initial fetch and start interval
  loadAuthToken();
  fetchTelemetry();
  checkDashboardPending();
  setInterval(checkDashboardPending, 3000);
  setInterval(fetchTelemetry, 3000);
</script>
</body>
</html>"""
CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ARIA Web Chat</title>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  * { box-sizing: border-box; font-family: 'Plus Jakarta Sans', sans-serif; margin: 0; padding: 0; }
  body { background: #07070e; color: #f4f4f5; display: flex; flex-direction: column; height: 100vh; }
  #header { background: rgba(18, 18, 35, 0.8); padding: 15px 20px; border-bottom: 1px solid rgba(255,255,255,0.1); font-weight: 600; font-size: 18px; display: flex; justify-content: space-between; align-items: center; }
  #header-status { font-size: 12px; color: #10b981; background: rgba(16, 185, 129, 0.1); padding: 4px 8px; border-radius: 12px; }
  #chat { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 15px; }
  .msg { max-width: 80%; padding: 12px 16px; border-radius: 12px; line-height: 1.5; font-size: 15px; animation: fadeIn 0.3s ease; }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
  .msg.user { background: #6366f1; margin-left: auto; border-bottom-right-radius: 4px; }
  .msg.bot { background: rgba(255,255,255,0.05); margin-right: auto; border-bottom-left-radius: 4px; border: 1px solid rgba(255,255,255,0.1); }
  .msg.bot p { margin-bottom: 10px; }
  .msg.bot p:last-child { margin-bottom: 0; }
  .msg.bot pre { background: #000; padding: 12px; border-radius: 8px; overflow-x: auto; margin: 10px 0; border: 1px solid rgba(255,255,255,0.1); }
  .msg.bot code { font-family: monospace; font-size: 13px; color: #e2e8f0; }
  .msg.bot a { color: #60a5fa; }
  #input-area { display: flex; padding: 15px 20px; background: rgba(18, 18, 35, 0.8); border-top: 1px solid rgba(255,255,255,0.1); gap: 10px; }
  #input { flex: 1; padding: 12px 16px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2); background: rgba(0,0,0,0.2); color: #fff; font-size: 15px; outline: none; transition: border 0.3s; }
  #input:focus { border-color: #6366f1; }
  #send { padding: 12px 24px; border: none; background: #6366f1; color: white; border-radius: 8px; font-weight: 600; cursor: pointer; transition: background 0.3s; }
  #send:hover { background: #4f46e5; }
  #send:disabled { background: #475569; cursor: not-allowed; }
  .typing { display: flex; gap: 5px; align-items: center; padding: 10px; }
  .dot { width: 6px; height: 6px; background: #a1a1aa; border-radius: 50%; animation: bounce 1.4s infinite ease-in-out both; }
  .dot:nth-child(1) { animation-delay: -0.32s; }
  .dot:nth-child(2) { animation-delay: -0.16s; }
  @keyframes bounce { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

  /* Modal Overlay styling */
  .modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw;
    height: 100vh;
    background: rgba(7, 7, 14, 0.7);
    backdrop-filter: blur(8px);
    display: flex;
    justify-content: center;
    align-items: center;
    z-index: 1000;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.3s ease;
  }
  .modal-overlay.active {
    opacity: 1;
    pointer-events: auto;
  }
  .modal-card {
    background: #121223;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 16px;
    padding: 24px;
    width: 90%;
    max-width: 450px;
    box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
    transform: scale(0.9);
    transition: transform 0.3s ease;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .modal-overlay.active .modal-card {
    transform: scale(1);
  }
  .modal-title {
    font-weight: 600;
    font-size: 18px;
    color: #f4f4f5;
    border-bottom: 1px solid rgba(255, 255, 255, 0.1);
    padding-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .modal-image {
    width: 100%;
    max-height: 180px;
    object-fit: cover;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    margin-bottom: 8px;
  }
  .modal-body {
    font-size: 14px;
    color: #a1a1aa;
    line-height: 1.6;
    max-height: 250px;
    overflow-y: auto;
    background: rgba(0, 0, 0, 0.2);
    padding: 12px;
    border-radius: 8px;
    border: 1px solid rgba(255, 255, 255, 0.05);
    white-space: pre-wrap;
  }
  .modal-actions {
    display: flex;
    justify-content: flex-end;
    gap: 12px;
    margin-top: 8px;
  }
  .modal-btn {
    padding: 10px 20px;
    border: none;
    border-radius: 8px;
    font-weight: 600;
    font-size: 14px;
    cursor: pointer;
    transition: background 0.2s;
  }
  .modal-btn.approve {
    background: #10b981;
    color: white;
  }
  .modal-btn.approve:hover {
    background: #059669;
  }
  .modal-btn.cancel {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.2);
    color: #ef4444;
  }
  .modal-btn.cancel:hover {
    background: rgba(239, 68, 68, 0.2);
  }
</style>
</head>
<body>
  <div id="header">
    <div>ARIA Web Interface</div>
    <div style="display: flex; align-items: center; gap: 15px;">
      <button id="clear-btn" style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); color: #ef4444; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">Clear Chat</button>
      <div id="header-status">● Online</div>
    </div>
  </div>
  <div id="chat">
    <div class="msg bot">Hello! I am ARIA. How can I help you today?</div>
  </div>
  <div id="input-area">
    <input type="text" id="input" placeholder="Message ARIA..." autocomplete="off">
    <button id="send">Send</button>
  </div>
  
  <!-- Action Authorization Modal Popup Window -->
  <div id="auth-modal" class="modal-overlay">
    <div class="modal-card">
      <div class="modal-title">
        <span>🛡️ Action Authorization Required</span>
      </div>
      <div id="modal-image-container" style="display:none; text-align:center;">
        <img id="modal-img" class="modal-image" src="" alt="Post Preview">
      </div>
      <div id="modal-details" class="modal-body">
        Loading action details...
      </div>
      <div class="modal-actions">
        <button id="modal-cancel-btn" onclick="window.sendApproval('cancel')" class="modal-btn cancel">Cancel</button>
        <button id="modal-approve-btn" onclick="window.sendApproval('approve')" class="modal-btn approve">Approve</button>
      </div>
    </div>
  </div>

  <script>
    const chat = document.getElementById('chat');
    const input = document.getElementById('input');
    const sendBtn = document.getElementById('send');
    
    // Manage session ID persistent in localStorage
    let sessionId = localStorage.getItem('aria_session_id');
    if (!sessionId) {
      sessionId = 'web_session_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
      localStorage.setItem('aria_session_id', sessionId);
    }
    
    const renderer = new marked.Renderer();
    const linkRenderer = renderer.link;
    renderer.link = function(href, title, text) {
      const html = linkRenderer.call(renderer, href, title, text);
      return html.replace(/^<a /, '<a target="_blank" rel="noopener noreferrer" ');
    };
    marked.setOptions({ renderer: renderer, breaks: true });
    
    function appendMsg(text, sender, hasPending = false, pendingType = 'action', pendingDetails = '', imageUrl = '') {
      const d = document.createElement('div');
      d.className = 'msg ' + sender;
      if(sender === 'bot') {
        d.innerHTML = marked.parse(text);
        if (hasPending) {
          const btnContainer = document.createElement('div');
          btnContainer.className = 'approval-buttons';
          btnContainer.style.marginTop = '15px';
          btnContainer.style.display = 'flex';
          btnContainer.style.gap = '10px';
          
          let approveCmd = "sendApproval('approve')";
          let cancelCmd = "sendApproval('cancel')";
          if (pendingType === 'post') {
            approveCmd = "sendApproval('approve post')";
            cancelCmd = "sendApproval('cancel post')";
          }
          
          btnContainer.innerHTML = `
            <button onclick="${approveCmd}" style="padding: 8px 16px; border: none; background: #10b981; color: white; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px; transition: background 0.2s;">Approve</button>
            <button onclick="${cancelCmd}" style="padding: 8px 16px; border: none; background: #ef4444; color: white; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 14px; transition: background 0.2s;">Cancel</button>
          `;
          d.appendChild(btnContainer);

          // Open overlay modal window
          const modal = document.getElementById('auth-modal');
          const modalDetails = document.getElementById('modal-details');
          const imgContainer = document.getElementById('modal-image-container');
          const imgEl = document.getElementById('modal-img');
          if (modal && modalDetails) {
            modalDetails.textContent = pendingDetails || text.replace(/Reply with '1'.*/s, '').trim();
            const approveBtn = document.getElementById('modal-approve-btn');
            const cancelBtn = document.getElementById('modal-cancel-btn');
            approveBtn.setAttribute('onclick', `window.${approveCmd}`);
            cancelBtn.setAttribute('onclick', `window.${cancelCmd}`);
            
            if (imageUrl) {
              imgEl.src = imageUrl;
              imgContainer.style.display = 'block';
            } else {
              imgContainer.style.display = 'none';
              imgEl.src = '';
            }
            
            modal.classList.add('active');
          }
        }
      } else {
        d.textContent = text;
      }
      chat.appendChild(d);
      chat.scrollTo({ top: chat.scrollHeight, behavior: 'smooth' });
    }

    window.sendApproval = async function(choice) {
      const modal = document.getElementById('auth-modal');
      if (modal) modal.classList.remove('active');
      const containers = document.querySelectorAll('.approval-buttons');
      containers.forEach(c => c.remove());
      input.value = choice;
      sendMessage();
    };

    async function sendMessage() {
      const text = input.value.trim();
      if(!text) return;
      
      appendMsg(text, 'user');
      input.value = '';
      sendBtn.disabled = true;
      input.disabled = true;
      
      const typingId = 'typing-' + Date.now();
      const typing = document.createElement('div');
      typing.id = typingId;
      typing.className = 'msg bot typing';
      typing.innerHTML = '<div class="dot"></div><div class="dot"></div><div class="dot"></div>';
      chat.appendChild(typing);
      chat.scrollTo({ top: chat.scrollHeight, behavior: 'smooth' });

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({message: text, session_id: sessionId})
        });
        const data = await res.json();
        document.getElementById(typingId).remove();
        if(data.reply) {
          appendMsg(data.reply, 'bot', data.has_pending, data.pending_type, data.pending_details, data.image_url);
        } else {
          appendMsg('⚠️ Error: ' + JSON.stringify(data), 'bot');
        }
      } catch(e) {
        document.getElementById(typingId).remove();
        appendMsg('⚠️ Connection error to ARIA backend.', 'bot');
      } finally {
        sendBtn.disabled = false;
        input.disabled = false;
        input.focus();
      }
    }

    async function checkPendingStatus() {
      try {
        const res = await fetch(`/api/chat/status?session_id=${sessionId}`);
        const data = await res.json();
        const modal = document.getElementById('auth-modal');
        const modalDetails = document.getElementById('modal-details');
        const imgContainer = document.getElementById('modal-image-container');
        const imgEl = document.getElementById('modal-img');
        
        if (data.has_pending) {
          if (modal && modalDetails) {
            modalDetails.textContent = data.pending_details;
            const approveBtn = document.getElementById('modal-approve-btn');
            const cancelBtn = document.getElementById('modal-cancel-btn');
            
            let approveCmd = "sendApproval('approve')";
            let cancelCmd = "sendApproval('cancel')";
            if (data.pending_type === 'post') {
              approveCmd = "sendApproval('approve post')";
              cancelCmd = "sendApproval('cancel post')";
            }
            
            approveBtn.setAttribute('onclick', `window.${approveCmd}`);
            cancelBtn.setAttribute('onclick', `window.${cancelCmd}`);
            
            if (data.image_url) {
              imgEl.src = data.image_url;
              imgContainer.style.display = 'block';
            } else {
              imgContainer.style.display = 'none';
              imgEl.src = '';
            }
            
            modal.classList.add('active');
          }
        } else {
          if (modal) modal.classList.remove('active');
        }
      } catch (e) {
        console.error("Error checking pending status:", e);
      }
    }

    sendBtn.addEventListener('click', sendMessage);
    input.addEventListener('keypress', (e) => {
      if(e.key === 'Enter') sendMessage();
    });
    
    document.getElementById('clear-btn').addEventListener('click', async () => {
      if(confirm('Are you sure you want to clear chat history and start a new session?')) {
        try {
          await fetch('/api/chat', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({message: '/clear', session_id: sessionId})
          });
        } catch(e) {}
        
        sessionId = 'web_session_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
        localStorage.setItem('aria_session_id', sessionId);
        chat.innerHTML = '<div class="msg bot">Hello! I am ARIA. How can I help you today?</div>';
      }
    });

    // Check status on load and start auto-polling
    checkPendingStatus();
    setInterval(checkPendingStatus, 3000);

    input.focus();
  </script>
</body>
</html>"""


def get_telemetry_data(limit=100) -> dict:
    """Query execution_ledger database to extract global real-time aggregates, reasoning efficiency, and ledger records."""
    import sqlite3
    import json
    
    # Model-specific pricing per 1M tokens: (prompt, completion)
    PRICING_TABLE = {
        "gemini-2.5-pro": (1.25, 5.00),
        "gemini-2.5-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 5.00),
        "gemini-1.5-flash": (0.075, 0.30),
        "llama-3.3-70b-versatile": (0.59, 0.79),
        "llama-3.1-70b-versatile": (0.59, 0.79),
        "llama-3.1-8b-instant": (0.05, 0.08),
        "llama3-70b-8192": (0.59, 0.79),
        "llama3-8b-8208": (0.05, 0.08),
        "gpt-4o": (2.50, 10.00),
        "gpt-4o-mini": (0.150, 0.600),
        "o1-mini": (3.00, 12.00)
    }
    
    supported_models = [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "gpt-4o",
        "gpt-4o-mini",
        "o1-mini"
    ]
    
    model_mon = {m: {"latencies": [], "failures": 0, "rate_limited": False} for m in supported_models}
    
    # Containers for derived latency metrics
    event_latencies = {
        "INTENT_CLASSIFICATION": [],
        "PLANNING": [],
        "AUDIT_PRE": [],
        "EXECUTION": [],
        "AUDIT_POST": [],
        "PA_SYNTHESIS": [],
        "GOAL_COMPLETE": []
    }
    model_latencies = {}
    dept_latencies = {}
    workflow_latencies = {}
    
    def get_token_costs(model_name: str) -> tuple[float, float]:
        if not model_name:
            return 0.15 / 1_000_000, 0.60 / 1_000_000 # default fallback
        m_lower = model_name.lower().strip()
        for key, rates in PRICING_TABLE.items():
            if key in m_lower:
                return rates[0] / 1_000_000, rates[1] / 1_000_000
        return 0.15 / 1_000_000, 0.60 / 1_000_000

    def get_provider(model_name: str) -> str:
        if not model_name:
            return "unknown"
        m_lower = model_name.lower().strip()
        if "gemini" in m_lower:
            return "google"
        elif "gpt-" in m_lower or "o1-" in m_lower:
            return "openai"
        elif "/" in m_lower:
            return "openrouter"
        elif "llama" in m_lower or "mixtral" in m_lower or "gemma" in m_lower:
            return "groq"
        return "provider"

    aggregates = {
        "total_tokens": 0,
        "total_cost": 0.0,
        "categories": {
            "research": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0},
            "analysis": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0},
            "writing": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0},
            "execution": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0},
            "pa": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0},
            "governance": {"prompt": 0, "completion": 0, "total": 0, "cost": 0.0}
        }
    }
    
    ledger_rows = []
    goals_map = {}
    planner_constraint_violations = 0
    governance_rejections = 0
    recovery_invocations = 0
    retrieval_requests = 0
    retrieval_hits = 0
    retrieval_misses = 0
    retrieval_latencies = []
    retrieved_tokens = 0
    
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, session_id, goal_id, task_id, department, event_type, metadata, timestamp 
            FROM execution_ledger 
            ORDER BY event_id DESC
        """)
        all_rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for row in all_rows:
            ev_id, sess_id, g_id, t_id, dept, ev_type, meta_str, ts = row
            
            if ev_type == "PLANNER_CONSTRAINT_VIOLATION":
                planner_constraint_violations += 1
            elif ev_type in ("AUDIT_PRE_FAIL", "AUDIT_POST_FAIL"):
                governance_rejections += 1
            elif ev_type == "RECOVERY_REGISTERED":
                recovery_invocations += 1
            elif ev_type == "RAG_RETRIEVAL" and meta_str:
                try:
                    meta = json.loads(meta_str)
                    retrieval_requests += meta.get("retrieval_requests", 0) or 0
                    retrieval_hits += meta.get("retrieval_hits", 0) or 0
                    retrieval_misses += meta.get("retrieval_misses", 0) or 0
                    retrieval_latencies.append(meta.get("retrieval_latency_ms", 0.0) or 0.0)
                    retrieved_tokens += meta.get("retrieved_tokens", 0) or 0
                except Exception:
                    pass

            # Ensure timestamp is string formatted (converts datetime objects from postgres)
            if hasattr(ts, "isoformat"):
                ts = ts.isoformat()
            else:
                ts = str(ts)
            
            prompt = 0
            completion = 0
            total = 0
            latency = 0.0
            model_name = ""
            is_estimated = False
            
            meta = {}
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                    tokens = meta.get("tokens")
                    if tokens and isinstance(tokens, dict):
                        prompt = tokens.get("prompt", 0) or 0
                        completion = tokens.get("completion", 0) or 0
                        total = tokens.get("total", 0) or (prompt + completion)
                    
                    latency = float(meta.get("latency", 0.0))
                    model_name = meta.get("model", "")
                    is_estimated = bool(meta.get("is_estimated", False))
                except Exception:
                    pass
            
            # Extract latency_ms with fallbacks
            latency_ms = None
            if meta:
                latency_ms = meta.get("latency_ms")
                if latency_ms is None:
                    lat_sec = meta.get("latency")
                    if lat_sec is not None:
                        latency_ms = float(lat_sec) * 1000.0
                    else:
                        lat_sec_other = meta.get("latency_sec")
                        if lat_sec_other is not None:
                            latency_ms = float(lat_sec_other) * 1000.0
                
                # Fallback to start/end timestamp diff
                if latency_ms is None:
                    start_t = meta.get("event_start_time")
                    end_t = meta.get("event_end_time")
                    if start_t and end_t:
                        try:
                            from datetime import datetime
                            def parse_iso(ts_str):
                                ts_str = ts_str.strip().replace(' ', 'T')
                                if ts_str.endswith('Z'):
                                    ts_str = ts_str[:-1] + '+00:00'
                                return datetime.fromisoformat(ts_str)
                            diff = parse_iso(end_t) - parse_iso(start_t)
                            latency_ms = diff.total_seconds() * 1000.0
                        except Exception:
                            pass
            
            if latency_ms is None:
                latency_ms = latency * 1000.0 if latency > 0.0 else 0.0
                
            latency_ms = float(latency_ms)
            
            # Categorize event type
            canonical_event = None
            if ev_type == "INTENT_CLASSIFICATION":
                canonical_event = "INTENT_CLASSIFICATION"
            elif ev_type == "PLANNING":
                canonical_event = "PLANNING"
            elif ev_type in ("AUDIT_PRE_PASS", "AUDIT_PRE_FAIL", "AUDIT_PRE"):
                canonical_event = "AUDIT_PRE"
            elif ev_type in ("EXECUTION_DONE", "EXECUTION_FAIL", "EXECUTION_START"):
                canonical_event = "EXECUTION"
            elif ev_type in ("AUDIT_POST_PASS", "AUDIT_POST_FAIL", "AUDIT_POST"):
                canonical_event = "AUDIT_POST"
            elif ev_type == "PA_SYNTHESIS":
                canonical_event = "PA_SYNTHESIS"
            elif ev_type in ("GOAL_COMPLETED", "GOAL_FAILED"):
                canonical_event = "GOAL_COMPLETE"

            if canonical_event:
                event_latencies[canonical_event].append(latency_ms)
                
            # Model latency grouping
            if model_name:
                m_clean = model_name.strip()
                if m_clean:
                    if m_clean not in model_latencies:
                        model_latencies[m_clean] = []
                    model_latencies[m_clean].append(latency_ms)
                    
            # Department latency grouping
            dept_name = dept.lower().strip() if dept else None
            if not dept_name:
                if canonical_event == "PA_SYNTHESIS":
                    dept_name = "pa"
                elif canonical_event in ("PLANNING", "AUDIT_PRE", "AUDIT_POST"):
                    dept_name = "governance"
            if dept_name:
                if dept_name not in dept_latencies:
                    dept_latencies[dept_name] = []
                dept_latencies[dept_name].append(latency_ms)
                
            # Workflow / Goal tracking
            if g_id and g_id not in ("G-WALK", "G-PLAN"):
                if g_id not in workflow_latencies:
                    workflow_latencies[g_id] = {
                        "goal_id": g_id,
                        "query": "",
                        "latencies": [],
                        "completed": False,
                        "failed": False,
                        "ts": ts,
                        "goal_completed_latency": None
                    }
                q_val = ""
                if meta:
                    q_val = meta.get("query") or meta.get("goal") or ""
                if q_val and not workflow_latencies[g_id]["query"]:
                    workflow_latencies[g_id]["query"] = q_val
                    
                if ev_type == "GOAL_COMPLETED":
                    workflow_latencies[g_id]["completed"] = True
                    workflow_latencies[g_id]["goal_completed_latency"] = latency_ms
                elif ev_type == "GOAL_FAILED":
                    workflow_latencies[g_id]["failed"] = True
                    workflow_latencies[g_id]["goal_completed_latency"] = latency_ms
                
                workflow_latencies[g_id]["latencies"].append(latency_ms)
            
            # Extract metrics per model dynamically
            if model_name:
                m_lower = model_name.lower().strip()
                matched_model = None
                for sm in supported_models:
                    if sm in m_lower:
                        matched_model = sm
                        break
                if matched_model:
                    if latency > 0.0:
                        model_mon[matched_model]["latencies"].append(latency)
                    if ev_type in ("AUDIT_PRE_FAIL", "AUDIT_POST_FAIL", "PLANNING_FAIL") or "fail" in ev_type.lower():
                        model_mon[matched_model]["failures"] += 1
                    if meta_str:
                        try:
                            meta_lower = str(meta).lower()
                            if "rate_limit" in meta_lower or "429" in meta_lower or "rate limit" in meta_lower:
                                model_mon[matched_model]["rate_limited"] = True
                            if "error" in meta or "exception" in meta or meta.get("planner_status") == "FAILED":
                                model_mon[matched_model]["failures"] += 1
                        except Exception:
                            pass
            
            # Map categories
            category = "governance"
            if dept:
                ldept = dept.lower().strip()
                if ldept in ("research", "analysis", "writing", "execution", "pa"):
                    category = ldept
                    
            if ev_type in ("PLANNING", "AUDIT_PRE", "AUDIT_PRE_FAIL", "AUDIT_PRE_PASS", "AUDIT_POST", "AUDIT_POST_FAIL", "AUDIT_POST_PASS"):
                category = "governance"
                if ev_type == "PLANNING":
                    is_estimated = is_estimated or (total == 2300 and prompt == 1800) # fallback
                
            p_rate, c_rate = get_token_costs(model_name)
            cost = (prompt * p_rate) + (completion * c_rate)
            provider = get_provider(model_name)
            
            # Update aggregates
            aggregates["total_tokens"] += total
            aggregates["total_cost"] += cost
            
            if category in aggregates["categories"]:
                aggregates["categories"][category]["prompt"] += prompt
                aggregates["categories"][category]["completion"] += completion
                aggregates["categories"][category]["total"] += total
                aggregates["categories"][category]["cost"] += cost
                
            ledger_rows.append({
                "event_id": ev_id,
                "session_id": sess_id,
                "goal_id": g_id,
                "task_id": t_id or "",
                "department": dept or "",
                "category": category,
                "event_type": ev_type,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "cost": round(cost, 6),
                "timestamp": ts,
                "model": model_name or "default",
                "provider": provider,
                "is_estimated": is_estimated
            })
            
            # Reasoning efficiency goal aggregates
            if g_id not in goals_map:
                goals_map[g_id] = {
                    "goal_id": g_id,
                    "session_id": sess_id,
                    "query": "",
                    "total_tokens": 0,
                    "total_cost": 0.0,
                    "duration": 0.0,
                    "status": "ACTIVE",
                    "timestamp": ts
                }
                
            goals_map[g_id]["total_tokens"] += total
            goals_map[g_id]["total_cost"] += cost
            goals_map[g_id]["duration"] += latency
            
            if meta_str:
                try:
                    if "query" in meta and meta["query"] and not goals_map[g_id]["query"]:
                        goals_map[g_id]["query"] = meta["query"]
                    elif "goal" in meta and meta["goal"] and not goals_map[g_id]["query"]:
                        goals_map[g_id]["query"] = meta["goal"]
                except Exception:
                    pass
            
            if ev_type == "GOAL_CREATED" and meta_str:
                try:
                    goals_map[g_id]["status"] = meta.get("planner_status", "ACTIVE")
                except Exception:
                    pass
            elif ev_type in ("AUDIT_POST_FAIL", "AUDIT_PRE_FAIL"):
                goals_map[g_id]["status"] = "FAILED"
            elif ev_type == "AUDIT_POST_PASS" and t_id == "T-PA":
                goals_map[g_id]["status"] = "COMPLETED"
            elif ev_type == "PA_SYNTHESIS":
                goals_map[g_id]["status"] = "COMPLETED"
            
    except Exception as e:
        print(f"[DB TELEMETRY ERROR] get_telemetry_data failed: {e}", flush=True)
        
    aggregates["total_cost"] = round(aggregates["total_cost"], 5)
    for cat in aggregates["categories"]:
        aggregates["categories"][cat]["cost"] = round(aggregates["categories"][cat]["cost"], 5)
        
    reasoning_list = []
    for g_id, g_data in goals_map.items():
        if g_id == "G-WALK":
            continue
        if not g_data["query"]:
            g_data["query"] = f"Operations Swarm Task ({g_id})"
        g_data["total_cost"] = round(g_data["total_cost"], 5)
        g_data["duration"] = round(g_data["duration"], 2)
        reasoning_list.append(g_data)
        
    reasoning_list.sort(key=lambda x: x["timestamp"], reverse=True)
    
    # Compute derived latency metrics
    workflow_total_latencies = []
    for gid, info in workflow_latencies.items():
        if not info["query"]:
            info["query"] = f"Operations Swarm Task ({gid})"
        total_lat = info["goal_completed_latency"]
        if total_lat is None:
            total_lat = sum(info["latencies"]) if info["latencies"] else 0.0
        if total_lat > 0:
            workflow_total_latencies.append({
                "goal_id": gid,
                "query": info["query"],
                "total_latency_ms": round(total_lat, 2),
                "total_latency_sec": round(total_lat / 1000.0, 4),
                "timestamp": info["ts"]
            })
            
    top_10_slowest_workflows = sorted(workflow_total_latencies, key=lambda x: x["total_latency_ms"], reverse=True)[:10]
    
    model_averages = []
    for model, lats in model_latencies.items():
        avg_lat = sum(lats) / len(lats) if lats else 0.0
        model_averages.append({
            "model": model,
            "avg_latency_ms": round(avg_lat, 2),
            "avg_latency_sec": round(avg_lat / 1000.0, 4),
            "count": len(lats)
        })
    top_10_slowest_models = sorted(model_averages, key=lambda x: x["avg_latency_ms"], reverse=True)[:10]
    
    avg_latency_by_event = {}
    for ev, lats in event_latencies.items():
        avg_latency_by_event[ev] = round(sum(lats) / len(lats), 2) if lats else 0.0
        
    avg_latency_by_model = {}
    for m, lats in model_latencies.items():
        avg_latency_by_model[m] = round(sum(lats) / len(lats), 2) if lats else 0.0
        
    avg_latency_by_department = {}
    for d, lats in dept_latencies.items():
        avg_latency_by_department[d] = round(sum(lats) / len(lats), 2) if lats else 0.0
        
    avg_latency_by_workflow = round(sum(w["total_latency_ms"] for w in workflow_total_latencies) / len(workflow_total_latencies), 2) if workflow_total_latencies else 0.0
    
    planner_average_latency = avg_latency_by_event.get("PLANNING", 0.0)
    
    auditor_lats = event_latencies.get("AUDIT_PRE", []) + event_latencies.get("AUDIT_POST", [])
    auditor_average_latency = round(sum(auditor_lats) / len(auditor_lats), 2) if auditor_lats else 0.0
    
    execution_average_latency = avg_latency_by_event.get("EXECUTION", 0.0)
    
    avg_retrieval_latency_ms = round(sum(retrieval_latencies) / len(retrieval_latencies), 2) if retrieval_latencies else 0.0

    latency_metrics = {
        "avg_latency_by_event": avg_latency_by_event,
        "avg_latency_by_model": avg_latency_by_model,
        "avg_latency_by_department": avg_latency_by_department,
        "avg_latency_by_workflow": avg_latency_by_workflow,
        "latency_seconds": {ev: round(val / 1000.0, 4) for ev, val in avg_latency_by_event.items()},
        "planner_average_latency": planner_average_latency,
        "auditor_average_latency": auditor_average_latency,
        "execution_average_latency": execution_average_latency,
        "top_10_slowest_workflows": top_10_slowest_workflows,
        "top_10_slowest_models": top_10_slowest_models,
        "planner_constraint_violations": planner_constraint_violations,
        "governance_rejections": governance_rejections,
        "recovery_invocations": recovery_invocations,
        "retrieval_requests": retrieval_requests,
        "retrieval_hits": retrieval_hits,
        "retrieval_misses": retrieval_misses,
        "retrieval_latency_ms": avg_retrieval_latency_ms,
        "retrieved_tokens": retrieved_tokens
    }

    # Compile Model Matrix
    model_matrix = []
    for sm in supported_models:
        mon = model_mon[sm]
        avg_lat = sum(mon["latencies"]) / len(mon["latencies"]) if mon["latencies"] else 0.0
        
        status = "WORKING"
        if mon["rate_limited"]:
            status = "RATE_LIMITED"
        elif mon["failures"] > 2:
            status = "TIMEOUT_DEGRADED"
            
        limit_str = "8,192"
        provider = "groq"
        if "gemini-2.5" in sm or "gemini-1.5" in sm:
            provider = "google"
            limit_str = "2,097,152" if "pro" in sm else "1,048,576"
        elif "gpt-" in sm or "o1-" in sm:
            provider = "openai"
            limit_str = "128,000"
            
        model_matrix.append({
            "model": sm,
            "provider": provider,
            "token_limit": limit_str,
            "status": status,
            "avg_latency": round(avg_lat, 2) if avg_lat > 0 else None
        })
        
    return {
        "aggregates": aggregates,
        "temporal_timeline": get_temporal_events(limit=50),
        "ledger": ledger_rows[:limit],
        "reasoning_efficiency": reasoning_list[:5],
        "current_dept_model": CURRENT_DEPT_MODEL,
        "current_pa_model": CURRENT_PA_MODEL,
        "model_matrix": model_matrix,
        "latency_metrics": latency_metrics,
        "planner_constraint_violations": planner_constraint_violations,
        "governance_rejections": governance_rejections,
        "recovery_invocations": recovery_invocations,
        "retrieval_requests": retrieval_requests,
        "retrieval_hits": retrieval_hits,
        "retrieval_misses": retrieval_misses,
        "retrieval_latency_ms": avg_retrieval_latency_ms,
        "retrieved_tokens": retrieved_tokens
    }




class HealthHandler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        if self.path in ("/healthz", "/api/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
        elif self.path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        try:
            from urllib.parse import urlparse, parse_qs
            parsed_path = urlparse(self.path)
            path = parsed_path.path

            if path in ("/healthz", "/api/healthz"):
                body = json.dumps({
                    "status": "ok", "bot": "ARIA",
                    "features": ["memory", "web_search", "knowledge_base", "google_workspace"],
                    "google_configured": bool(is_google_configured()),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/api/telemetry", "/api/telemetry/"):
                try:
                    data = get_telemetry_data(limit=1000)
                    body = json.dumps(data).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    err = json.dumps({"status": "error", "message": str(e)}).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
            elif path in ("/", ""):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(STATUS_HTML.encode())
            elif path in ("/chat", "/chat/"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(CHAT_HTML.encode())
            elif path == "/api/chat/status":
                query_params = parse_qs(parsed_path.query)
                sid = query_params.get("session_id", ["web_anon"])[0]
                
                chat_id = get_persisted_chat_id()
                with _pending_actions_lock:
                    has_pending_action = sid in _pending_actions
                has_pending_post = bool(chat_id and chat_id in PENDING_POSTS)
                has_pending = has_pending_action or has_pending_post
                
                pending_type = "action"
                pending_details = ""
                image_url = ""
                
                if has_pending_action:
                    pending_type = "action"
                    with _pending_actions_lock:
                        p = _pending_actions.get(sid)
                    if p:
                        preview_fields = {k: v for k, v in p.get("params", {}).items() if k not in ("body", "content", "caption")}
                        fields_str = "\n".join(f"• {k.capitalize()}: {v}" for k, v in preview_fields.items())
                        body_preview = p.get("params", {}).get("body", p.get("params", {}).get("content", p.get("params", {}).get("caption", "")))
                        pending_details = f"Proposed Action: {p.get('action')}\n\n{fields_str}"
                        if body_preview:
                            pending_details += f"\n\nDraft Content:\n{body_preview}"
                        
                        img_path = p.get("params", {}).get("image_path", p.get("params", {}).get("file_path", ""))
                        if img_path:
                            import urllib.parse
                            image_url = f"/api/image?path={urllib.parse.quote(img_path)}"
                elif has_pending_post:
                    pending_type = "post"
                    draft = PENDING_POSTS.get(chat_id)
                    if draft:
                        pending_details = f"Proposed Facebook Post\n\n• Topic: {draft.get('custom_topic') or 'Daily Post'}\n\nDraft Caption:\n{draft.get('caption')}"
                        img_path = draft.get("image_path", "")
                        if img_path:
                            import urllib.parse
                            image_url = f"/api/image?path={urllib.parse.quote(img_path)}"
                
                body = json.dumps({
                    "has_pending": has_pending,
                    "pending_type": pending_type,
                    "pending_details": pending_details,
                    "image_url": image_url
                }).encode("utf-8")
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/image":
                query_params = parse_qs(parsed_path.query)
                img_path = query_params.get("path", [""])[0]
                if img_path and os.path.exists(img_path) and (img_path.lower().endswith(".jpg") or img_path.lower().endswith(".jpeg") or img_path.lower().endswith(".png")):
                    self.send_response(200)
                    if img_path.lower().endswith(".png"):
                        self.send_header("Content-Type", "image/png")
                    else:
                        self.send_header("Content-Type", "image/jpeg")
                    self._cors()
                    self.end_headers()
                    try:
                        with open(img_path, "rb") as f:
                            self.wfile.write(f.read())
                    except Exception as e:
                        print(f"[HTTP SERVER ERROR] Failed to serve image {img_path}: {e}", flush=True)
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError) as e:
            print(f"[HTTP SERVER WARNING] Client disconnected during GET {self.path}: {e}", flush=True)

    def do_POST(self):
        if self.path == "/api/models/switch":
            try:
                if API_CHAT_TOKEN:
                    auth_header = str(self.headers.get("Authorization", "")).strip()
                    api_key_header = str(self.headers.get("X-API-Key", "")).strip()
                    bearer = ""
                    if auth_header.lower().startswith("bearer "):
                        bearer = auth_header[7:].strip()
                    provided = api_key_header or bearer
                    if provided != API_CHAT_TOKEN:
                        err = json.dumps({"error": "unauthorized"}).encode()
                        self.send_response(401)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self._cors()
                        self.end_headers()
                        self.wfile.write(err)
                        return

                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
                role   = str(body.get("role", "")).strip().lower()
                model  = str(body.get("model", "")).strip()
                if role not in ("swarm", "pa") or not model:
                    raise ValueError("Invalid role or model parameter")

                global CURRENT_DEPT_MODEL, CURRENT_PA_MODEL, llm_dept, llm_pa
                if role == "swarm":
                    CURRENT_DEPT_MODEL = model
                    llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)
                    print(f"[API MODEL SWITCH] Swarm/Workers model switched to {model}", flush=True)
                elif role == "pa":
                    CURRENT_PA_MODEL = model
                    llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
                    print(f"[API MODEL SWITCH] Personal Assistant model switched to {model}", flush=True)

                response = json.dumps({"status": "success", "role": role, "model": model}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self._cors()
                self.end_headers()
                self.wfile.write(err)
            return

        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return

        try:
            if API_CHAT_TOKEN:
                auth_header = str(self.headers.get("Authorization", "")).strip()
                api_key_header = str(self.headers.get("X-API-Key", "")).strip()
                bearer = ""
                if auth_header.lower().startswith("bearer "):
                    bearer = auth_header[7:].strip()
                provided = api_key_header or bearer
                if provided != API_CHAT_TOKEN:
                    err = json.dumps({"error": "unauthorized"}).encode()
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
                    return

            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            msg    = str(body.get("message", "")).strip()
            sid    = str(body.get("session_id", "web_anon")).strip() or "web_anon"
            if not msg:
                raise ValueError("empty message")
            print(f"[WEB] session={sid[:16]} msg={msg[:80]}", flush=True)

            # Handle memory clearance via /clear over API
            if msg.lower() == "/clear":
                with _memory_lock:
                    if sid in _histories:
                        _histories[sid].clear()
                with _pending_actions_lock:
                    _pending_actions.pop(sid, None)
                
                try:
                    from .memory import FAILURES_PATH, FAILURES_TEST_PATH, _write_json_list
                except ImportError:
                    from memory import FAILURES_PATH, FAILURES_TEST_PATH, _write_json_list
                
                try:
                    _write_json_list(FAILURES_PATH, [])
                    _write_json_list(FAILURES_TEST_PATH, [])
                    conn, is_pg = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM system_memory WHERE key IN ('failures', 'failures_test')")
                    conn.commit()
                    cursor.close()
                    conn.close()
                except Exception as db_err:
                    print(f"[CLEAR ERROR] Database failures clear failed: {db_err}", flush=True)

                response = json.dumps({"reply": "Memory cleared for a fresh start.", "gear": "DYNAMIC", "has_pending": False}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
                return

            # Check for Facebook post approval/cancel from web
            chat_id = get_persisted_chat_id()
            with _pending_actions_lock:
                has_google_pending = sid in _pending_actions
            has_fb_pending = bool(chat_id and chat_id in PENDING_POSTS)

            is_fb_approve = msg.lower() in ("approve post", "post_approve")
            is_fb_cancel = msg.lower() in ("cancel post", "post_cancel")

            is_generic_approve = _is_approval_message(msg)
            is_generic_cancel = _is_reject_message(msg)

            # Route generic approve/cancel contextually
            target_fb_approve = is_fb_approve or (is_generic_approve and not has_google_pending and has_fb_pending)
            target_fb_cancel = is_fb_cancel or (is_generic_cancel and not has_google_pending and has_fb_pending)

            if has_fb_pending and (target_fb_approve or target_fb_cancel):
                if target_fb_approve:
                    draft = PENDING_POSTS.get(chat_id)
                    if draft:
                        try:
                            from .social_media import publish_to_facebook_page
                        except ImportError:
                            from social_media import publish_to_facebook_page
                        
                        ok, result_msg = publish_to_facebook_page(draft["image_path"], draft["caption"])
                        if ok:
                            PENDING_POSTS.pop(chat_id, None)
                            try:
                                from .memory import append_to_profile_ledger
                            except ImportError:
                                from memory import append_to_profile_ledger
                            append_to_profile_ledger("work_summaries", {
                                "task_name": "Daily FB Marketing Post",
                                "status": "SUCCESS",
                                "details": f"Message: {result_msg} | Topic: {draft.get('custom_topic')}",
                                "timestamp": datetime.now(timezone.utc).isoformat()
                            })
                            reply = f"Facebook post published successfully!\n\n{result_msg}"
                        else:
                            reply = f"Facebook post publishing failed:\n\n{result_msg}"
                        
                        response = json.dumps({"reply": reply, "gear": "DYNAMIC", "has_pending": False}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(response)))
                        self._cors()
                        self.end_headers()
                        self.wfile.write(response)
                        return

                if target_fb_cancel:
                    PENDING_POSTS.pop(chat_id, None)
                    reply = "Pending Facebook post draft cancelled."
                    response = json.dumps({"reply": reply, "gear": "DYNAMIC", "has_pending": False}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self._cors()
                    self.end_headers()
                    self.wfile.write(response)
                    return

            # Check for pending action in _pending_actions
            with _pending_actions_lock:
                pending = _pending_actions.get(sid)

            if pending and is_generic_approve:
                action = pending.get("action", "")
                params = resolve_action_params(pending.get("params", {}), research_text="")
                
                log_execution_ledger_event(
                    session_id=sid,
                    goal_id=pending.get("goal_id", "default"),
                    task_id=pending.get("task_id"),
                    department="execution",
                    event_type="APPROVAL_GRANTED",
                    state_before="WAITING",
                    state_after="RUNNING",
                    metadata={"by": "web_text", "action": action, "params": params}
                )
                
                ok, result_msg = execute_google_action(action, params)
                if ok:
                    with _pending_actions_lock:
                        _pending_actions.pop(sid, None)
                    reply = f"Action executed successfully.\n\n{result_msg}"
                else:
                    reply = f"Action execution failed.\n\n{result_msg}\n\nYou can type '1' / 'approve' again to retry, or '0' / 'cancel' to discard."
                
                # Check pending status
                with _pending_actions_lock:
                    has_pending_action = sid in _pending_actions
                has_pending_post = bool(chat_id and chat_id in PENDING_POSTS)
                has_pending = has_pending_action or has_pending_post
                
                pending_type = "action"
                pending_details = ""
                image_url = ""
                if has_pending_action:
                    pending_type = "action"
                    with _pending_actions_lock:
                        p = _pending_actions.get(sid)
                    if p:
                        preview_fields = {k: v for k, v in p.get("params", {}).items() if k not in ("body", "content", "caption")}
                        fields_str = "\n".join(f"• {k.capitalize()}: {v}" for k, v in preview_fields.items())
                        body_preview = p.get("params", {}).get("body", p.get("params", {}).get("content", p.get("params", {}).get("caption", "")))
                        pending_details = f"Proposed Action: {p.get('action')}\n\n{fields_str}"
                        if body_preview:
                            pending_details += f"\n\nDraft Content:\n{body_preview}"
                        
                        img_path = p.get("params", {}).get("image_path", p.get("params", {}).get("file_path", ""))
                        if img_path:
                            import urllib.parse
                            image_url = f"/api/image?path={urllib.parse.quote(img_path)}"
                elif has_pending_post:
                    pending_type = "post"
                    draft = PENDING_POSTS.get(chat_id)
                    if draft:
                        pending_details = f"Proposed Facebook Post\n\n• Topic: {draft.get('custom_topic') or 'Daily Post'}\n\nDraft Caption:\n{draft.get('caption')}"
                        img_path = draft.get("image_path", "")
                        if img_path:
                            import urllib.parse
                            image_url = f"/api/image?path={urllib.parse.quote(img_path)}"
                
                response = json.dumps({
                    "reply": reply, 
                    "gear": "DYNAMIC", 
                    "has_pending": has_pending,
                    "pending_type": pending_type,
                    "pending_details": pending_details,
                    "image_url": image_url
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
                return

            if pending and is_generic_cancel:
                log_execution_ledger_event(
                    session_id=sid,
                    goal_id=pending.get("goal_id", "default"),
                    task_id=pending.get("task_id"),
                    department="execution",
                    event_type="APPROVAL_DENIED",
                    state_before="WAITING",
                    state_after="CANCELLED",
                    metadata={"by": "web_text", "action": pending.get("action", "")}
                )
                with _pending_actions_lock:
                    _pending_actions.pop(sid, None)
                reply = "Pending action cancelled."
                response = json.dumps({"reply": reply, "gear": "DYNAMIC", "has_pending": False}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
                return

            # Normal path
            reply, gear_res, tokens = invoke_babu(msg, sid)
            print(f"[WEB OK] len={len(reply)} | Tokens: {tokens.get('total', 0)}", flush=True)
            
            # Check pending status
            with _pending_actions_lock:
                has_pending_action = sid in _pending_actions
            has_pending_post = bool(chat_id and chat_id in PENDING_POSTS)
            has_pending = has_pending_action or has_pending_post
            
            pending_type = "action"
            pending_details = ""
            image_url = ""
            if has_pending_action:
                pending_type = "action"
                with _pending_actions_lock:
                    p = _pending_actions.get(sid)
                if p:
                    preview_fields = {k: v for k, v in p.get("params", {}).items() if k not in ("body", "content", "caption")}
                    fields_str = "\n".join(f"• {k.capitalize()}: {v}" for k, v in preview_fields.items())
                    body_preview = p.get("params", {}).get("body", p.get("params", {}).get("content", p.get("params", {}).get("caption", "")))
                    pending_details = f"Proposed Action: {p.get('action')}\n\n{fields_str}"
                    if body_preview:
                        pending_details += f"\n\nDraft Content:\n{body_preview}"
                    
                    img_path = p.get("params", {}).get("image_path", p.get("params", {}).get("file_path", ""))
                    if img_path:
                        import urllib.parse
                        image_url = f"/api/image?path={urllib.parse.quote(img_path)}"
            elif has_pending_post:
                pending_type = "post"
                draft = PENDING_POSTS.get(chat_id)
                if draft:
                    pending_details = f"Proposed Facebook Post\n\n• Topic: {draft.get('custom_topic') or 'Daily Post'}\n\nDraft Caption:\n{draft.get('caption')}"
                    img_path = draft.get("image_path", "")
                    if img_path:
                        import urllib.parse
                        image_url = f"/api/image?path={urllib.parse.quote(img_path)}"

            response = json.dumps({
                "reply": reply, 
                "gear": "DYNAMIC", 
                "has_pending": has_pending,
                "pending_type": pending_type,
                "pending_details": pending_details,
                "image_url": image_url
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(response)))
            self._cors()
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(err)))
            self._cors()
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HEALTH] Chat API + status on port {PORT}", flush=True)
    server.serve_forever()


# â”€â”€ Autonomous Social Media Scheduler & State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_id.txt")
LAST_POST_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_post_date.txt")
LAST_PREVIEW_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_preview_date.txt")

# State Management for Social Post Previews
PENDING_POSTS = {}      # Map of chat_id (int) -> draft dict
WAITING_FOR_TOPIC = {}  # Map of chat_id (int) -> bool

def get_last_preview_date() -> str:
    """Read the last scheduled preview generation date."""
    if os.path.exists(LAST_PREVIEW_DATE_FILE):
        try:
            with open(LAST_PREVIEW_DATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_preview_date(date_str: str):
    """Write the last scheduled preview generation date."""
    try:
        with open(LAST_PREVIEW_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write last preview date: {e}", flush=True)

def get_post_keyboard() -> InlineKeyboardMarkup:
    """Generate the interactive control panel for social post reviews."""
    keyboard = [
        [
            InlineKeyboardButton("Approve & Publish", callback_data="post_approve"),
            InlineKeyboardButton("Change Topic", callback_data="post_change_topic"),
        ],
        [
            InlineKeyboardButton("Cancel Post", callback_data="post_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_action_approval_keyboard(session_id: str) -> InlineKeyboardMarkup:
    keyboard = [[
        InlineKeyboardButton("Approve", callback_data=f"action_approve|{session_id}"),
        InlineKeyboardButton("Cancel", callback_data=f"action_cancel|{session_id}")
    ]]
    return InlineKeyboardMarkup(keyboard)

async def generate_and_send_preview(chat_id: int, bot, custom_topic: str = None, reply_to_message_id: int = None):
    """Generate a high-fidelity social media draft and send it to the user for approval."""
    try:
        try:
            from .social_media import generate_social_post_draft
        except ImportError:
            from social_media import generate_social_post_draft
        draft = await asyncio.to_thread(generate_social_post_draft, custom_topic)
        draft["custom_topic"] = custom_topic
        
        # Cache the draft
        import time
        draft["scheduled_at"] = time.time()
        draft["is_auto_scheduled"] = False  # Explicitly mark manual ad-hoc preview
        PENDING_POSTS[chat_id] = draft
        
        # 1. Send the Proposed Caption & FLUX Prompt in a separate text message
        details_text = (
            f"📝 *Proposed Caption:*\n"
            f"```\n{escape_markdown(draft['caption'])}\n```\n\n"
            f"🎨 *FLUX Prompt:*\n"
            f"_{escape_markdown(draft['image_prompt'])}_"
        )
        
        await bot.send_message(
            chat_id=chat_id,
            text=details_text,
            parse_mode="Markdown",
            reply_to_message_id=reply_to_message_id
        )
        
        # 2. Send the image preview with interactive keyboard
        with open(draft["image_path"], "rb") as photo_file:
            caption_text = (
                f"📊 *ARIA Marketing Department - Post Preview*\n\n"
                f"Please review the graphic above and the proposed caption sent in the previous message.\n\n"
                f"Click Approve to publish directly to Facebook Page."
            )
            
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=caption_text,
                reply_markup=get_post_keyboard(),
                reply_to_message_id=reply_to_message_id
            )
            
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"Failed to generate post preview:\n{escape_markdown(str(e))}",
                reply_to_message_id=reply_to_message_id
            )
        except Exception as msg_err:
            print(f"[PREVIEW ERROR] Failed to report error to chat {chat_id}: {msg_err}", flush=True)

def get_persisted_chat_id() -> Optional[int]:
    """Load the user's Telegram chat ID from local state file or environment."""
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return int(content)
        except Exception:
            pass
            
    env_id = os.environ.get("TELEGRAM_USER_CHAT_ID")
    if env_id:
        try:
            return int(env_id)
        except ValueError:
            pass
    return None

def persist_chat_id(chat_id: int):
    """Save the user's Telegram chat ID to local state file."""
    try:
        with open(CHAT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(chat_id))
    except Exception as e:
        print(f"[CHAT_ID ERROR] Failed to write chat ID: {e}", flush=True)

def get_last_post_date() -> str:
    """Read the last success post date."""
    if os.path.exists(LAST_POST_DATE_FILE):
        try:
            with open(LAST_POST_DATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_post_date(date_str: str):
    """Write the last success post date."""
    try:
        with open(LAST_POST_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write last post date: {e}", flush=True)

async def scheduler_async_loop(application):
    """Background async event loop for the daily post checking."""
    print("[SCHEDULER] Autonomous social posting scheduler thread started.", flush=True)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    
    # Avoid triggering retroactively if the bot starts up/restarts after 9:00 AM IST
    try:
        now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
        if now_ist.hour >= 9:
            today_str = now_ist.strftime("%Y-%m-%d")
            if get_last_preview_date() != today_str:
                print(f"[SCHEDULER] Startup time {now_ist.strftime('%H:%M:%S')} is past 9:00 AM IST. Marking today ({today_str}) as previewed to prevent retroactive run.", flush=True)
                set_last_preview_date(today_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to run startup initialization: {e}", flush=True)
        
    while True:
        try:
            now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
            today_str = now_ist.strftime("%Y-%m-%d")
            
            # 1. Trigger if it is 9:00 AM IST or later, and we haven't sent a preview today yet
            if now_ist.hour >= 9 and get_last_preview_date() != today_str:
                chat_id = get_persisted_chat_id()
                if chat_id:
                    print(f"[SCHEDULER] Triggering scheduled daily post preview for {today_str}...", flush=True)
                    # Mark that we generated/sent preview for today immediately to avoid duplicate runs
                    set_last_preview_date(today_str)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="Scheduled Marketing Swarm engaged. Generating daily custom tech graphic and copywriting..."
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send starting notification: {err}", flush=True)

                    await generate_and_send_preview(chat_id, application.bot)
                    
                    # Store timestamp and auto-scheduled flag of preview generation in draft dict for auto-publish timeout
                    if chat_id in PENDING_POSTS:
                        import time
                        PENDING_POSTS[chat_id]["scheduled_at"] = time.time()
                        PENDING_POSTS[chat_id]["is_auto_scheduled"] = True
                        print(f"[SCHEDULER] Timestamped auto-scheduled pending post for chat {chat_id} at {today_str}", flush=True)
                else:
                    print("[SCHEDULER] It's time to post, but no Telegram chat ID is registered yet. Waiting for user interaction...", flush=True)
            
            # 2. Check for pending drafts that have timed out without user feedback (1 hour = 3600 seconds)
            import time
            now_ts = time.time()
            for p_chat_id in list(PENDING_POSTS.keys()):
                draft = PENDING_POSTS[p_chat_id]
                scheduled_at = draft.get("scheduled_at")
                is_auto_scheduled = draft.get("is_auto_scheduled", False)
                if is_auto_scheduled and scheduled_at and (now_ts - scheduled_at >= 3600):
                    print(f"[SCHEDULER] Auto-publishing timed-out post for chat {p_chat_id}...", flush=True)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=p_chat_id,
                            text="⏰ *No review response received within 1 hour. Automatically publishing the scheduled post to your Facebook Page...*",
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send timeout notification: {err}", flush=True)
                        
                    try:
                        from .social_media import publish_to_facebook_page
                    except ImportError:
                        from social_media import publish_to_facebook_page
                        
                    ok, res_msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
                    
                    # Log progress atomically
                    try:
                        try:
                            from .memory import append_to_profile_ledger
                        except ImportError:
                            from memory import append_to_profile_ledger
                        append_to_profile_ledger("work_summaries", {
                            "task_name": "Daily FB Marketing Post (Auto-Published)",
                            "status": "SUCCESS" if ok else "FAILED",
                            "details": f"Message: {res_msg} | Topic: {draft.get('custom_topic')}"
                        })
                    except Exception as e:
                        print(f"[SCHEDULER WARNING] Failed to write ledger: {e}", flush=True)
                        
                    if ok:
                        # Set last post date
                        set_last_post_date(today_str)
                        
                        # Clean up states
                        PENDING_POSTS.pop(p_chat_id, None)
                        WAITING_FOR_TOPIC.pop(p_chat_id, None)
                        
                        try:
                            await application.bot.send_message(
                                chat_id=p_chat_id,
                                text=f"✅ *Successfully auto-published to Facebook Page!*\n\n{escape_markdown(res_msg)}\n\n*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```",
                                parse_mode="Markdown"
                            )
                        except Exception as err:
                            print(f"[SCHEDULER ERROR] Failed to send success notification: {err}", flush=True)
                    else:
                        try:
                            await application.bot.send_message(
                                chat_id=p_chat_id,
                                text=(
                                    f"❌ *Failed to auto-publish to Facebook:*\n{escape_markdown(res_msg)}\n\n"
                                    f"You can reply with 'Approve and publish' to retry, or 'Cancel post' to discard."
                                ),
                                parse_mode="Markdown"
                            )
                        except Exception as err:
                            print(f"[SCHEDULER ERROR] Failed to send failure notification: {err}", flush=True)
                elif not is_auto_scheduled and scheduled_at and (now_ts - scheduled_at >= 3600):
                    print(f"[SCHEDULER] Auto-cancelling manual post for chat {p_chat_id} due to timeout...", flush=True)
                    try:
                        await application.bot.send_message(
                            chat_id=p_chat_id,
                            text="⏰ *Manual post draft review period has expired. Discarding the draft to clear pending states.*",
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send cancel notification: {err}", flush=True)
                    PENDING_POSTS.pop(p_chat_id, None)
                    WAITING_FOR_TOPIC.pop(p_chat_id, None)
            
        except Exception as e:
            print(f"[SCHEDULER ERROR] Exception in loop: {e}", flush=True)
            traceback.print_exc(file=sys.stdout)
            
        # Wake up and check every 15 minutes (900 seconds)
        await asyncio.sleep(900)

def start_social_scheduler(application):
    """Start the background thread for autonomous social media posting."""
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scheduler_async_loop(application))
        
    t = threading.Thread(target=run_loop, name="autonomous_scheduler", daemon=True)
    t.start()


def escape_markdown(text: str) -> str:
    """HARD FIX: Strip risky characters instead of escaping them to prevent Telegram parse errors."""
    return re.sub(r'([_*`\[\]])', '', str(text))


async def edit_callback_message(query, text: str, reply_markup=None):
    """Helper to edit a callback message safely whether it has media (caption) or is text-only."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[CALLBACK WARNING] Failed to edit callback message: {e}", flush=True)


async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force immediately generating and sending today's marketing post preview."""
    chat_id = update.effective_chat.id
    persist_chat_id(chat_id)
    
    custom_topic = " ".join(context.args) if context.args else None
    topic_str = f" for topic: '{custom_topic}'" if custom_topic else ""
    await update.message.reply_text(f"Generating marketing swarm preview{topic_str}... This takes about 15-20 seconds.")
    
    await generate_and_send_preview(chat_id, context.bot, custom_topic=custom_topic, reply_to_message_id=update.message.message_id)


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to promote a workflow pattern to a trusted template (E[Temp])."""
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "⚠️ **Syntax Error**\nUse: `/promote <template_signature> <goal_id>`\n"
            "E.g., `/promote research:execution:pa:post_to_facebook G-20260607-123456`"
        )
        return
        
    sig = args[0]
    goal_id = args[1]
    
    conn, is_pg = get_db_connection()
    goal_graph_json = None
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                "SELECT metadata FROM execution_ledger WHERE goal_id = %s AND event_type = 'PLANNING' ORDER BY event_id DESC LIMIT 1",
                (goal_id,)
            )
        else:
            cursor.execute(
                "SELECT metadata FROM execution_ledger WHERE goal_id = ? AND event_type = 'PLANNING' ORDER BY event_id DESC LIMIT 1",
                (goal_id,)
            )
        row = cursor.fetchone()
        if row:
            metadata_dict = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            graph_dict = metadata_dict.get("graph")
            if graph_dict:
                goal_graph_json = json.dumps(graph_dict)
        cursor.close()
    except Exception as e:
        print(f"[PROMOTE CMD ERROR] Failed to fetch goal graph from ledger: {e}", flush=True)
    finally:
        conn.close()
        
    if not goal_graph_json:
        await update.message.reply_text(
            f"❌ **Error**: Could not find a compiled GoalGraph for Goal ID `{goal_id}` in the ledger."
        )
        return

    import uuid
    from datetime import datetime, timezone
    template_id = f"T-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    
    conn, is_pg = get_db_connection()
    success = False
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                """
                INSERT INTO trusted_templates (
                    template_id, template_signature, goal_graph_json, status, promoted_from_goal_id, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (template_signature) DO UPDATE SET
                    goal_graph_json = EXCLUDED.goal_graph_json,
                    version = trusted_templates.version + 1,
                    status = 'ACTIVE',
                    promoted_from_goal_id = EXCLUDED.promoted_from_goal_id,
                    created_at = EXCLUDED.created_at
                """,
                (template_id, sig, goal_graph_json, "ACTIVE", goal_id, created_at)
            )
        else:
            cursor.execute(
                """
                INSERT OR REPLACE INTO trusted_templates (
                    template_id, template_signature, goal_graph_json, version, execution_count, success_count, consecutive_failures, status, promoted_from_goal_id, created_at
                ) VALUES (
                    ?, ?, ?,
                    COALESCE((SELECT version + 1 FROM trusted_templates WHERE template_signature = ?), 1),
                    0, 0, 0, 'ACTIVE', ?, ?
                )
                """,
                (template_id, sig, goal_graph_json, sig, goal_id, created_at)
            )
        conn.commit()
        cursor.close()
        success = True
    except Exception as e:
        print(f"[PROMOTE CMD ERROR] Failed to save trusted template: {e}", flush=True)
        await update.message.reply_text(f"❌ **Promotion Failed**: Database error: {e}")
    finally:
        conn.close()
        
    if success:
        await update.message.reply_text(
            f"✅ **E[Temp] Promotion Successful!**\n"
            f"• **Template ID**: `{template_id}`\n"
            f"• **Signature**: `{sig}`\n"
            f"• **Source Goal ID**: `{goal_id}`\n\n"
            f"ARIA has now compiled this workflow into muscle memory. Subsequent runs matching this signature will bypass dynamic planning and heavy auditing."
        )


# â”€â”€ Telegram handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def send_long_telegram_message(update: Update, text: str, reply_markup=None):
    """Split and send messages exceeding Telegram's 4096 character limit."""
    limit = 4000  # Safe boundary to prevent any BadRequest exception
    if len(text) <= limit:
        if reply_markup:
            await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text)
        return

    paragraphs = text.split("\n\n")
    current_chunk = ""
    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) + 2 > limit:
            if len(paragraph) > limit:
                if current_chunk:
                    await update.message.reply_text(current_chunk.strip())
                    current_chunk = ""
                sub_paragraphs = [paragraph[i:i+limit] for i in range(0, len(paragraph), limit)]
                for sub in sub_paragraphs[:-1]:
                    await update.message.reply_text(sub)
                current_chunk = sub_paragraphs[-1]
            else:
                await update.message.reply_text(current_chunk.strip())
                current_chunk = paragraph
        else:
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph

    if current_chunk:
        if reply_markup:
            await update.message.reply_text(current_chunk.strip(), reply_markup=reply_markup)
        else:
            await update.message.reply_text(current_chunk.strip())


# ------------------- Retire Template Command -------------------
async def cmd_retire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to retire a trusted template (set status to RETIRED)."""
    args = context.args
    if not args or len(args) < 1:
        await update.message.reply_text(
            "⚠️ **Syntax Error**\nUse: `/retire <template_signature>`\n"
            "E.g., `/retire research:execution:pa:post_to_facebook`"
        )
        return
    sig = args[0]
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                "UPDATE trusted_templates SET status = %s WHERE template_signature = %s",
                ("RETIRED", sig)
            )
        else:
            cursor.execute(
                "UPDATE trusted_templates SET status = ? WHERE template_signature = ?",
                ("RETIRED", sig)
            )
        if cursor.rowcount == 0:
            await update.message.reply_text(f"❌ No active template found with signature `{sig}`.")
        else:
            conn.commit()
            await update.message.reply_text(f"✅ Template `{sig}` marked as **RETIRED**.")
    except Exception as e:
        print(f"[RETIRE CMD ERROR] {e}", flush=True)
        await update.message.reply_text(f"❌ Failed to retire template: `{e}`")
    finally:
        conn.close()


async def run_babu(update: Update, msg: str, session_id: str):
    if update and update.effective_chat:
        persist_chat_id(update.effective_chat.id)
        
    stop_typing = asyncio.Event()

    async def keep_typing():
        while not stop_typing.is_set():
            try:
                await update.message.chat.send_action("typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        reply, gear, tokens = await asyncio.to_thread(invoke_babu, msg, session_id)
        print(f"[TG OK] gear={gear} len={len(reply)} | Tokens: {tokens['total']} (Prompt: {tokens['prompt']}, Comp: {tokens['completion']})", flush=True)
        # Append token usage footnote in Telegram
        if tokens and tokens.get("total", 0) > 0:
            reply += f"\n\n[Tokens: {tokens['total']}]"
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"ARIA error: {e}"
    finally:
        stop_typing.set()
        typing_task.cancel()

    with _pending_actions_lock:
        has_pending = session_id in _pending_actions
    if has_pending and "Action authorization required." in reply:
        await send_long_telegram_message(update, reply, reply_markup=get_action_approval_keyboard(session_id))
        return

    # Check for [IMAGE] tag to reply with a photo
    match = re.search(r'\[IMAGE\]\s*url=([^\s\n]+)(?:\s+caption=(.+))?', reply, re.DOTALL)
    if match:
        url = match.group(1)
        caption = match.group(2) if match.group(2) else ""
        await update.message.reply_photo(photo=url, caption=caption.strip())
        return

    await send_long_telegram_message(update, reply)


def tg_session(update: Update) -> str:
    return f"tg_{update.message.from_user.id}"


def transcribe_audio(file_path: str) -> str:
    """Use Groq's whisper-large-v3 model to transcribe audio files directly."""
    try:
        from groq import Groq
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key:
            return "[Error: GROQ_API_KEY is not configured]"
        
        client = Groq(api_key=groq_key)
        with open(file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), file.read()),
                model="whisper-large-v3",
                response_format="text"
            )
        return transcription.strip()
    except Exception as e:
        print(f"[TRANSCRIPTION ERROR] {e}", flush=True)
        return f"[Error transcribing audio: {e}]"


def classify_review_intent(text: str) -> Optional[str]:
    """Classify user's text message into social media review intents (approve, cancel, change_topic), robust to spelling mistakes."""
    text_clean = " ".join(str(text).lower().strip().split())
    if not text_clean:
        return None
        
    # Check exact digits or simple keywords first
    if text_clean in ("1", "one", "approve", "publish", "approve post", "publish post", "approve & publish", "approve and publish"):
        return "approve"
    if text_clean in ("0", "zero", "cancel", "cancel post", "discard", "discard post"):
        return "cancel"
    if text_clean in ("2", "two", "change", "change topic", "regenerate", "regenerate post"):
        return "change_topic"
        
    def distance(s1, s2):
        if len(s1) < len(s2):
            return distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]
        
    words = text_clean.split()
    
    # 1. Exact or substring checks (very fast)
    approve_phrases = ["approve and publish", "approve & publish", "approve post", "publish post", "publish the post", "approve draft", "publish draft", "yes", "confirm", "publish", "approve"]
    cancel_phrases = ["cancel post", "cancel draft", "discard post", "discard draft", "cancel", "discard"]
    change_phrases = ["change topic", "change the topic", "regenerate post", "regenerate draft", "change topic of post", "change details", "regenerate", "change"]
    
    for t in approve_phrases:
        if t in text_clean:
            return "approve"
    for t in cancel_phrases:
        if t in text_clean:
            return "cancel"
    for t in change_phrases:
        if t in text_clean:
            return "change_topic"
            
    # 2. Fuzzy match single words (distance threshold of 1 or 2 characters)
    for word in words:
        for target in ["approve", "publish", "confirm", "yes"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "approve"
        for target in ["cancel", "discard", "abort"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "cancel"
        for target in ["regenerate", "change", "topic"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "change_topic"
                
    # 3. Fuzzy match multi-word phrases (distance threshold of 2 or 3 characters)
    for target in approve_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 3:
            return "approve"
    for target in cancel_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 2:
            return "cancel"
    for target in change_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 3:
            return "change_topic"
            
    return None


def extract_text_from_document(file_path: str, max_chars: int = 15000) -> str:
    """Extract text content from a PDF or plain text document."""
    if not os.path.exists(file_path):
        return ""
    
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
                if len(text) > max_chars:
                    text = text[:max_chars] + "\n... [Content Truncated to Save Tokens] ..."
                    break
            return text.strip()
        except Exception as e:
            print(f"[PDF EXTRACTION ERROR] {e}", flush=True)
            return f"[Error extracting text from PDF: {e}]"
    
    # Text-like extensions
    text_extensions = {".txt", ".csv", ".md", ".json", ".py", ".html", ".xml", ".css", ".js", ".ini", ".yaml", ".yml", ".log"}
    if ext in text_extensions:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(max_chars)
                if len(content) >= max_chars:
                    content += "\n... [Content Truncated to Save Tokens] ..."
                return content.strip()
        except Exception as e:
            print(f"[TEXT EXTRACTION ERROR] {e}", flush=True)
            return f"[Error reading text document: {e}]"
            
    return "[Non-text document format. No content extracted to save tokens.]"


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_TELEGRAM_SUCCESS_TIME
    LAST_TELEGRAM_SUCCESS_TIME = time.time()
    chat_id = update.effective_chat.id
    if WAITING_FOR_TOPIC.get(chat_id):
        topic = update.message.text.strip() if update.message.text else ""
        if not topic:
            return
        
        WAITING_FOR_TOPIC.pop(chat_id, None)
        
        if topic.lower() == 'cancel':
            await update.message.reply_text("Topic change cancelled.")
            return
            
        topic_str = f" for topic: '{topic}'"
        await update.message.reply_text(f"Topic received: \"{topic}\".\nGenerating a new graphic and caption preview. This takes about 15-20 seconds.")
        await generate_and_send_preview(chat_id, context.bot, custom_topic=topic, reply_to_message_id=update.message.message_id)
        return

    # 1. Check if the message is a document/file attachment
    if update.message.document:
        print(f"[TG DOCUMENT] Received document from {update.message.from_user.id}", flush=True)
        try:
            doc = update.message.document
            tg_file = await context.bot.get_file(doc.file_id)
            
            temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
            os.makedirs(temp_dir, exist_ok=True)
            
            file_name = doc.file_name or f"doc_{doc.file_id}"
            file_name = os.path.basename(file_name)
            temp_path = os.path.join(temp_dir, file_name)
            
            print(f"[TG DOCUMENT] Downloading {file_name} to {temp_path}", flush=True)
            await tg_file.download_to_drive(temp_path)
            
            caption = update.message.caption or ""
            
            # Check if user explicitly asked to analyze, summarize, or read the document to save tokens
            analysis_keywords = {"analyse", "analyze", "summarize", "read", "content", "extract", "whats in", "what is in", "explain", "describe", "find in"}
            caption_lower = caption.lower()
            needs_analysis = any(kw in caption_lower for kw in analysis_keywords)
            
            if needs_analysis:
                print(f"[TG DOCUMENT] Analysis requested. Extracting text from {file_name}...", flush=True)
                extracted_text = extract_text_from_document(temp_path)
                rewritten_text = f"{caption} [Document Attached: {temp_path}] [Document Content:\n{extracted_text}\n]"
            else:
                print(f"[TG DOCUMENT] No analysis requested. Passing only path artifact to save tokens.", flush=True)
                rewritten_text = f"{caption} [Document Attached: {temp_path}]"
                
            print(f"[TG DOCUMENT OK] Rewritten query: '{rewritten_text}'", flush=True)
            await run_babu(update, rewritten_text, tg_session(update))
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await update.message.reply_text(f"Document processing error: {e}")
        return

    # 2. Check if the message is a voice note
    if update.message.voice:
        print(f"[TG VOICE] Received voice note from {update.message.from_user.id}", flush=True)
        await update.message.chat.send_action("record_voice")
        
        try:
            tg_file = await context.bot.get_file(update.message.voice.file_id)
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
                temp_path = tmp.name
            
            await tg_file.download_to_drive(temp_path)
            transcribed_text = await asyncio.to_thread(transcribe_audio, temp_path)
            
            try:
                os.remove(temp_path)
            except Exception:
                pass
                
            if not transcribed_text or transcribed_text.startswith("[Error"):
                await update.message.reply_text(f"Voice transcription failed:\n{transcribed_text}")
                return
                
            print(f"[TG VOICE OK] Transcribed: '{transcribed_text}'", flush=True)
            await update.message.reply_text(f"[Voice Command]: \"{transcribed_text}\"")
            await run_babu(update, transcribed_text, tg_session(update))
            
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await update.message.reply_text(f"Voice processing error: {e}")
        return

    # 2. Standard text processing
    msg = update.message.text
    if not msg:
        return
    session_id = tg_session(update)

    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending and _is_approval_message(msg):
        with _pending_actions_lock:
            pending = _pending_actions.get(session_id)
        if pending:
            action = pending.get("action", "")
            params = resolve_action_params(pending.get("params", {}), research_text="")
            
            # Log approval event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_GRANTED",
                state_before="WAITING",
                state_after="RUNNING",
                metadata={"by": "telegram_text", "action": action, "params": params}
            )
            
            ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
            if ok:
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                status = "Action executed successfully."
                await update.message.reply_text(f"{status}\n\n{result_msg}")
            else:
                status = "Action execution failed."
                await update.message.reply_text(
                    f"{status}\n\n{result_msg}\n\nYou can type '1' / 'approve' again to retry, or '0' / 'cancel' to discard.",
                    reply_markup=get_action_approval_keyboard(session_id)
                )
            return

    if pending and _is_reject_message(msg):
        with _pending_actions_lock:
            pending = _pending_actions.get(session_id)
        if pending:
            # Log rejection event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_DENIED",
                state_before="WAITING",
                state_after="CANCELLED",
                metadata={"by": "telegram_text", "action": pending.get("action", "")}
            )
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        await update.message.reply_text("Pending action cancelled.")
        return

    if pending and not msg.startswith("/"):
        await update.message.reply_text("You have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard.")
        return

    print(f"[TG MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)

    # Text-based Social Media Review Interceptor
    review_intent = classify_review_intent(msg)
    
    if review_intent == "approve" and chat_id in PENDING_POSTS:
        draft = PENDING_POSTS.get(chat_id)
        if draft:
            await update.message.reply_text("Interpreted text approval. Publishing to Facebook Page. Please wait...")
            try:
                from .social_media import publish_to_facebook_page
            except ImportError:
                from social_media import publish_to_facebook_page
                
            ok, res_msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
            
            # Log work progress atomically to profile
            try:
                try:
                    from .memory import append_to_profile_ledger
                except ImportError:
                    from memory import append_to_profile_ledger
                append_to_profile_ledger("work_summaries", {
                    "task_name": "Daily FB Marketing Post",
                    "status": "SUCCESS" if ok else "FAILED",
                    "details": f"Message: {res_msg} | Topic: {draft.get('custom_topic')}"
                })
            except Exception as e:
                print(f"[CALLBACK WARNING] Failed to write ledger: {e}", flush=True)
                
            if ok:
                # Set last post date if it was scheduled or today
                ist_tz = timezone(timedelta(hours=5, minutes=30))
                today_str = datetime.now(timezone.utc).astimezone(ist_tz).strftime("%Y-%m-%d")
                set_last_post_date(today_str)
                
                # Clean up pending states
                PENDING_POSTS.pop(chat_id, None)
                WAITING_FOR_TOPIC.pop(chat_id, None)
                
                await update.message.reply_text(
                    f"Successfully published to Facebook Page!\n\n{res_msg}\n\nCaption:\n{escape_markdown(draft['caption'])}"
                )
            else:
                await update.message.reply_text(
                    f"Failed to publish to Facebook:\n{escape_markdown(res_msg)}\n\nCaption:\n{escape_markdown(draft['caption'])}\n\nYou can reply with 'Approve and publish' to retry, or 'Cancel post' to discard."
                )
            return

    elif review_intent == "cancel" and chat_id in PENDING_POSTS:
        PENDING_POSTS.pop(chat_id, None)
        WAITING_FOR_TOPIC.pop(chat_id, None)
        await update.message.reply_text("Post draft cancelled.")
        return
        
    elif review_intent == "change_topic" and chat_id in PENDING_POSTS:
        WAITING_FOR_TOPIC[chat_id] = True
        await update.message.reply_text("Please reply with your new custom topic (e.g. Epf claims, Gst registration, Income tax returns) to regenerate the post.")
        return

    await run_babu(update, msg, session_id)


async def cmd_launch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /launch <complex question>")
        return
    await update.message.reply_text("Swarm engaged — planning and executing goal (~30s)...")
    await run_babu(update, "launch " + text, tg_session(update))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = tg_session(update)
    with _memory_lock:
        _histories[session_id].clear()
    with _pending_actions_lock:
        _pending_actions.pop(session_id, None)
        
    try:
        from .memory import FAILURES_PATH, FAILURES_TEST_PATH, _write_json_list
    except ImportError:
        from memory import FAILURES_PATH, FAILURES_TEST_PATH, _write_json_list
        
    try:
        _write_json_list(FAILURES_PATH, [])
        _write_json_list(FAILURES_TEST_PATH, [])
        
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM system_memory WHERE key IN ('failures', 'failures_test')")
        conn.commit()
        cursor.close()
        conn.close()
        rules_status = "and historical immune rules cleared "
    except Exception as db_err:
        print(f"[CLEAR ERROR] Database failures clear failed: {db_err}", flush=True)
        rules_status = "and rules clear attempted (with error) "
        
    await update.message.reply_text(f"Memory cleared {rules_status}for a fresh start.")



async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current active pending goals or actions and provide control buttons."""
    chat_id = update.effective_chat.id
    session_id = tg_session(update)
    
    pending_post = PENDING_POSTS.get(chat_id)
    
    with _pending_actions_lock:
        pending_action = _pending_actions.get(session_id)
        
    if not pending_post and not pending_action:
        await update.message.reply_text("✅ There are no active pending goals or actions to manage.")
        return
        
    # Build list of active items
    text_lines = ["📊 *Current Active Goals & Action Approvals*:\n"]
    keyboard = []
    
    if pending_post:
        topic = pending_post.get("custom_topic") or "Daily Scheduled Post"
        text_lines.append("📝 *Pending Social Media Post*:")
        text_lines.append(f"  • *Topic*: {escape_markdown(topic)}")
        text_lines.append(f"  • *Caption*: _{escape_markdown(pending_post.get('caption', '')[:120])}..._\n")
        
        keyboard.append([
            InlineKeyboardButton("Approve Post", callback_data="post_approve"),
            InlineKeyboardButton("Cancel Post", callback_data="post_cancel")
        ])
        
    if pending_action:
        action = pending_action.get("action", "Google Action")
        text_lines.append("🔧 *Pending Google Workspace Action*:")
        text_lines.append(f"  • *Action*: `{escape_markdown(action)}`")
        
        params = pending_action.get("params", {})
        param_desc = ", ".join(f"{k}: {v}" for k, v in params.items() if k not in ("body", "content"))
        if param_desc:
            text_lines.append(f"  • *Parameters*: _{escape_markdown(param_desc)}_")
            
        keyboard.append([
            InlineKeyboardButton("Approve Action", callback_data=f"action_approve|{session_id}"),
            InlineKeyboardButton("Cancel Action", callback_data=f"action_cancel|{session_id}")
        ])
        
    await update.message.reply_text(
        text="\n".join(text_lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    google_line = "\n- Send email, create calendar event, log to sheet - just ask naturally" if is_google_configured() else ""
    await update.message.reply_text(
        "ARIA - Multi-Agent AI Assistant\n\n"
        "Unified Agent Swarm:\n"
        "- Just send a message naturally! ARIA automatically decomposes your query, performs deep web research, writes drafts, and executes secure audited actions.\n"
        "- /launch <question> - Shortcut command to explicitly trigger the planner.\n\n"
        "Marketing Department:\n"
        "- /postnow - Instantly generate and post custom daily tech graphic & copy to Facebook Page\n\n"
        "Extras:\n"
        "- /goals - Show and manage active pending goals and actions\n"
        "- /clear - Reset conversation memory\n"
        "- /stats - Show runtime diagnostics\n"
        f"- /help - Show this menu{google_line}\n\n"
        "I remember your conversation and personalize drafts based on your user profile.",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        try:
            from .memory import get_runtime_stats
        except ImportError:
            from memory import get_runtime_stats
        stats = get_runtime_stats(limit=80)
        gears = stats.get("gear_counts", {})
        text = (
            "ARIA Runtime Stats\n\n"
            f"Routing events: {stats.get('routing_events', 0)}\n"
            f"Workflow events: {stats.get('workflow_events', 0)}\n"
            f"Detected actions: {stats.get('action_detected_count', 0)}\n"
            f"Average tokens: {stats.get('avg_tokens', 0)}\n"
            f"Average latency (s): {stats.get('avg_latency_seconds', 0)}\n"
            f"Workflow success rate: {stats.get('success_rate', 0)}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Stats unavailable: {e}")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global llm_pa, llm_dept, CURRENT_PA_MODEL, CURRENT_DEPT_MODEL

    # Real-time environment check
    groq_active = "🟢 ACTIVE" if os.environ.get("GROQ_API_KEY") else "🔴 NOT CONFIGURED"
    gemini_active = "🟢 ACTIVE" if os.environ.get("GEMINI_API_KEY") else "🔴 NOT CONFIGURED"
    openai_active = "🟢 ACTIVE" if os.environ.get("OPENAI_API_KEY") else "🔴 NOT CONFIGURED"
    openrouter_active = "🟢 ACTIVE" if os.environ.get("OPENROUTER_API_KEY") else "🔴 NOT CONFIGURED"

    args = context.args
    if not args:
        menu = (
            "🛡️ **ARIA Model Settings & Telemetry**\n\n"
            f"👤 **Current Assistant (PA) Model**: `{CURRENT_PA_MODEL}`\n"
            f"👥 **Current Swarm (Research) Model**: `{CURRENT_DEPT_MODEL}`\n\n"
            
            "⚙️ **Active Providers Configuration:**\n"
            f"- **Groq API**: {groq_active}\n"
            f"- **OpenRouter API**: {openrouter_active}\n"
            f"- **Gemini API (Native)**: {gemini_active}\n"
            f"- **OpenAI API (Native)**: {openai_active}\n\n"
            
            "✨ **Available Models to Switch:**\n"
            "--- *Groq Provider Models* ---\n"
            "1. `llama-3.3-70b-versatile` (Llama 3.3 - Best Quality)\n"
            "2. `llama-3.1-8b-instant` (Llama 3.1 8B - Fastest / Best Limits)\n"
            "3. `mixtral-8x7b-32768` (Mixtral 8x7B - Great Balance)\n"
            "4. `gemma2-9b-it` (Gemma 2 9B - Fast & Smart)\n"
            "5. `deepseek-r1-distill-llama-70b` (DeepSeek R1 - Deep Reasoning)\n\n"
            
            "--- *Gemini Native Models* ---\n"
            "6. `gemini-2.5-flash` (Gemini 2.5 Flash)\n\n"
            
            "--- *OpenRouter Provider Models* ---\n"
            "7. `google/gemini-2.5-flash` (Gemini 2.5 Flash via OpenRouter)\n"
            "8. `deepseek/deepseek-chat` (DeepSeek V3 via OpenRouter)\n"
            "9. `meta-llama/llama-3.3-70b-instruct` (Llama 3.3 via OpenRouter)\n\n"
            
            "--- *OpenAI Native Models* ---\n"
            "10. `gpt-4o-mini` (GPT-4o Mini)\n"
            "11. `gpt-4o` (GPT-4o flagship)\n\n"
            
            "🚀 **How to Switch:**\n"
            "- `/model <1-11>` - Change the main Personal Assistant model\n"
            "- `/model swarm <1-11>` - Change the underlying swarm/research model\n\n"
            "Tip: You can also specify any custom model string directly, e.g. `/model deepseek/deepseek-reasoner` or `/model swarm gemini-2.5-flash`"
        )
        await update.message.reply_text(menu, parse_mode="Markdown")
        return

    is_swarm = False
    choice = args[0]
    if choice.lower() == "swarm" and len(args) > 1:
        is_swarm = True
        choice = args[1]

    model_map = {
        "1": "llama-3.3-70b-versatile",
        "2": "llama-3.1-8b-instant",
        "3": "mixtral-8x7b-32768",
        "4": "gemma2-9b-it",
        "5": "deepseek-r1-distill-llama-70b",
        "6": "gemini-2.5-flash",
        "7": "google/gemini-2.5-flash",
        "8": "deepseek/deepseek-chat",
        "9": "meta-llama/llama-3.3-70b-instruct",
        "10": "gpt-4o-mini",
        "11": "gpt-4o"
    }

    selected_model = model_map.get(choice)
    if not selected_model:
        if choice in model_map.values() or "/" in choice or choice.startswith("gemini-") or choice.startswith("gpt-"):
            selected_model = choice
        else:
            await update.message.reply_text("Invalid choice. Use `/model` to see valid options or pass a valid model string.")
            return

    try:
        if is_swarm:
            CURRENT_DEPT_MODEL = selected_model
            llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)
            await update.message.reply_text(f"Swarm/Research model switched to: `{CURRENT_DEPT_MODEL}`")
        else:
            CURRENT_PA_MODEL = selected_model
            llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
            await update.message.reply_text(f"Main Personal Assistant model switched to: `{CURRENT_PA_MODEL}`")
    except Exception as e:
        await update.message.reply_text(f"Failed to switch model: {e}")


async def on_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback button clicks (Approve, Change Topic, Cancel) for social post reviews."""
    query = update.callback_query
    await query.answer()
    
    try:
        if not query.message:
            print("[CALLBACK ERROR] Callback query message is None", flush=True)
            return
            
        chat_id = query.message.chat.id
        data = query.data
        print(f"[CALLBACK UPDATE] received callback_data='{data}' for chat_id={chat_id}", flush=True)

        if data.startswith("action_approve|"):
            session_id = data.split("|", 1)[1].strip()
            with _pending_actions_lock:
                pending = _pending_actions.get(session_id)
            if not pending:
                await query.edit_message_text("No pending action found to approve.")
                return
            action = pending.get("action", "")
            params = resolve_action_params(pending.get("params", {}), research_text="")
            
            # Log approval event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_GRANTED",
                state_before="WAITING",
                state_after="RUNNING",
                metadata={"by": "telegram_callback", "action": action, "params": params}
            )
            
            ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
            if ok:
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                status = "Action executed successfully."
                await query.edit_message_text(f"{status}\n\n{result_msg}")
            else:
                status = "Action execution failed."
                await query.edit_message_text(
                    f"{status}\n\n{result_msg}\n\nYou can click Approve again to retry, or Cancel.",
                    reply_markup=get_action_approval_keyboard(session_id)
                )
            return

        if data.startswith("action_cancel|"):
            session_id = data.split("|", 1)[1].strip()
            with _pending_actions_lock:
                pending = _pending_actions.get(session_id)
            if pending:
                # Log rejection event
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=pending.get("goal_id", "default"),
                    task_id=pending.get("task_id"),
                    department="execution",
                    event_type="APPROVAL_DENIED",
                    state_before="WAITING",
                    state_after="CANCELLED",
                    metadata={"by": "telegram_callback", "action": pending.get("action", "")}
                )
            with _pending_actions_lock:
                _pending_actions.pop(session_id, None)
            await query.edit_message_text("Pending action cancelled.")
            return
        
        if data == "post_approve":
            draft = PENDING_POSTS.get(chat_id)
            if not draft:
                await edit_callback_message(query, "No pending post found to approve. Run /postnow to generate a new draft.")
                return
            
            await edit_callback_message(query, "Publishing to Facebook Page. Please wait.")
            
            try:
                from .social_media import publish_to_facebook_page
            except ImportError:
                from social_media import publish_to_facebook_page
            ok, msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
            
            # Log work progress atomically to profile
            try:
                try:
                    from .memory import append_to_profile_ledger
                except ImportError:
                    from memory import append_to_profile_ledger
                append_to_profile_ledger("work_summaries", {
                    "task_name": "Daily FB Marketing Post",
                    "status": "SUCCESS" if ok else "FAILED",
                    "details": f"Message: {msg} | Topic: {draft.get('custom_topic')}"
                })
            except Exception as e:
                print(f"[CALLBACK WARNING] Failed to write ledger: {e}", flush=True)
                
            if ok:
                # Set last post date if it was scheduled or today
                ist_tz = timezone(timedelta(hours=5, minutes=30))
                today_str = datetime.now(timezone.utc).astimezone(ist_tz).strftime("%Y-%m-%d")
                set_last_post_date(today_str)
                
                # Clean up pending states
                PENDING_POSTS.pop(chat_id, None)
                WAITING_FOR_TOPIC.pop(chat_id, None)
                
                await edit_callback_message(
                    query,
                    f"✅ Successfully published to Facebook Page!\n\n{escape_markdown(msg)}"
                )
                
                await query.message.reply_text(
                    text=f"📢 *Published Post Details:*\n\n{escape_markdown(msg)}\n\n*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```",
                    parse_mode="Markdown"
                )
            else:
                await edit_callback_message(
                    query,
                    "⚠️ Failed to publish to Facebook Page.",
                    reply_markup=get_post_keyboard() # Keep keyboard active so they can try again or change topic!
                )
                
                await query.message.reply_text(
                    text=(
                        f"❌ *Failed to publish to Facebook:*\n{escape_markdown(msg)}\n\n"
                        f"*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```\n\n"
                        f"You can click Approve again to retry, Change Topic, or Cancel."
                    ),
                    parse_mode="Markdown"
                )
                
        elif data == "post_change_topic":
            WAITING_FOR_TOPIC[chat_id] = True
            await query.message.reply_text("Please reply with your new custom topic (e.g. health and yoga, cybersecurity tips, computer repair services) to regenerate the post.")
            
        elif data == "post_cancel":
            PENDING_POSTS.pop(chat_id, None)
            WAITING_FOR_TOPIC.pop(chat_id, None)
            await edit_callback_message(query, "Post draft cancelled.")
            
    except Exception as e:
        print(f"[CALLBACK CRITICAL ERROR] Exception inside on_post_callback: {e}", flush=True)
        traceback.print_exc(file=sys.stdout)


CONFLICT_TIMESTAMPS = []

async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle unexpected errors in Telegram Bot. Exit immediately on Conflict to prevent infinite reconnection loop."""
    from telegram.error import Conflict
    if isinstance(context.error, Conflict):
        import time
        now = time.time()
        global CONFLICT_TIMESTAMPS
        # Clean up timestamps older than 60 seconds
        CONFLICT_TIMESTAMPS = [t for t in CONFLICT_TIMESTAMPS if now - t < 60]
        
        print("\n" + "="*80, flush=True)
        print("⚠️  CRITICAL CONFLICT DETECTED", flush=True)
        print("Another instance of this bot is already running and polling", flush=True)
        
        if len(CONFLICT_TIMESTAMPS) < 3:
            CONFLICT_TIMESTAMPS.append(now)
            print(f"Deployment rollover buffer: sleeping 15s before retrying (conflict count: {len(CONFLICT_TIMESTAMPS)}/3)...", flush=True)
            print("="*80 + "\n", flush=True)
            await asyncio.sleep(15)
            return
            
        print("To prevent a reconnection loop battle, this duplicate instance will now exit", flush=True)
        print("="*80 + "\n", flush=True)
        import os
        os._exit(1)
    else:
        print(f"[BOT ERROR] Handled exception: {context.error}", flush=True)


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def cleanup_corrupt_failures():
    try:
        from .memory import _read_json_list, _write_json_list, FAILURES_PATH
    except ImportError:
        from memory import _read_json_list, _write_json_list, FAILURES_PATH
        
    try:
        failures = _read_json_list(FAILURES_PATH)
        if failures:
            cleaned = []
            for f in failures:
                sig = f.get("failure_signature", "").upper()
                rule = f.get("active_anti_pattern_rule", "").lower()
                domain = f.get("domain", "").lower()
                
                is_bad_facebook_rule = (
                    "social media posting tasks" in rule or
                    "verification of page details" in rule or
                    "facebook page" in rule or
                    "page name and accessibility" in rule
                )
                if is_bad_facebook_rule or domain in ("governance.planning", "governance.classification"):
                    print(f"[CLEANUP] Removing corrupt failure rule: {f.get('failure_signature')}", flush=True)
                    continue
                cleaned.append(f)
            
            if len(cleaned) != len(failures):
                _write_json_list(FAILURES_PATH, cleaned)
                print(f"[CLEANUP SUCCESS] Cleared corrupt failures. Active rules remaining: {len(cleaned)}", flush=True)
    except Exception as e:
        print(f"[CLEANUP ERROR] Failed to clean failures: {e}", flush=True)







if __name__ == '__main__':
    from .bootstrap import bootstrap_brain
    bootstrap_brain()
