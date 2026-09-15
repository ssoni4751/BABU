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

# Modular imports
try:
    from .services import (
        get_db_connection,
        get_token_costs,
        log_temporal_event,
        get_temporal_events,
        log_execution_ledger_event,
        get_cached_search,
        store_cached_search,
        load_user_profile,
        get_current_profile,
        is_profile_relevant_query,
        is_private_data_query,
        get_user_profile_text,
        get_profile_fact_answer,
        is_action_status_query,
        search_profile,
        clean_search_query,
        search_knowledge,
        wikipedia_search,
        web_search,
        retrieve_system_memory_via_sql,
        retrieve_k0_memory,
        db_save_pending_action,
        db_delete_pending_action,
        DB_PATH
    )
    from .gateway import (
        is_pure_greeting,
        is_deterministic_faq_query,
        is_simple_query,
        requires_workspace_access,
        requires_web_search,
        is_system_aware_query,
        get_babu_age_string,
        get_dynamic_self_identity,
        get_system_health_dashboard,
        get_babu_self_context,
        has_multiple_tasks_or_requests
    )
    from .graph import (
        BabuState,
        intent_router,
        route_after_router,
        planner_node,
        task_executor_node,
        pa_node,
        action_node,
        task_manager_node,
        research_dept,
        department_synthesizer,
        workflow
    )
except ImportError:
    from services import (
        get_db_connection,
        get_token_costs,
        log_temporal_event,
        get_temporal_events,
        log_execution_ledger_event,
        get_cached_search,
        store_cached_search,
        load_user_profile,
        get_current_profile,
        is_profile_relevant_query,
        is_private_data_query,
        get_user_profile_text,
        get_profile_fact_answer,
        is_action_status_query,
        search_profile,
        clean_search_query,
        search_knowledge,
        wikipedia_search,
        web_search,
        retrieve_system_memory_via_sql,
        retrieve_k0_memory,
        db_save_pending_action,
        db_delete_pending_action,
        DB_PATH
    )
    from gateway import (
        is_pure_greeting,
        is_deterministic_faq_query,
        is_simple_query,
        requires_workspace_access,
        requires_web_search,
        is_system_aware_query,
        get_babu_age_string,
        get_dynamic_self_identity,
        get_system_health_dashboard,
        get_babu_self_context,
        has_multiple_tasks_or_requests
    )
    from graph import (
        BabuState,
        intent_router,
        route_after_router,
        planner_node,
        task_executor_node,
        pa_node,
        action_node,
        task_manager_node,
        research_dept,
        department_synthesizer,
        workflow
    )


DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "babu_checkpoint.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

REFUSAL_PRIVATE_DATA = "Information unavailable. No authoritative business records were found."

AUTHORITY_MEMORY = "AUTHORITY_MEMORY"
AUTHORITY_DATABASE = "AUTHORITY_DATABASE"
AUTHORITY_LEDGER = "AUTHORITY_LEDGER"
AUTHORITY_WEB = "AUTHORITY_WEB"
AUTHORITY_MODEL = "AUTHORITY_MODEL"

DATABASE_URL = os.environ.get("DATABASE_URL")


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
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS planned_graphs_cache (
                session_id TEXT NOT NULL,
                query_hash TEXT NOT NULL,
                goal_class TEXT NOT NULL,
                graph_hash TEXT NOT NULL,
                raw_query TEXT NOT NULL,
                goal_graph_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, query_hash)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pg_cache_session_hash ON planned_graphs_cache (session_id, query_hash);")
        
        # Truncate cache if exceeds 1000 entries
        try:
            cursor.execute("SELECT COUNT(*) FROM planned_graphs_cache;")
            if cursor.fetchone()[0] > 1000:
                cursor.execute("""
                    DELETE FROM planned_graphs_cache 
                    WHERE created_at < NOW() - INTERVAL '24 hours';
                """)
                print("[POSTGRES] Pruned planned_graphs_cache entries older than 24 hours.", flush=True)
        except Exception as prune_err:
            print(f"[POSTGRES PRUNE WARNING] Failed to prune planned_graphs_cache: {prune_err}", flush=True)
        
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
                ("LESSON-001", "LESSON", "Self-Awareness Hierarchy (K1-K7)", "Phase 3", "Lack of semantic boundary organization leading to retrieval confusion", "Formally register Knowledge Classes (K1-K7) inside runtime routing", "Organize self-awareness data: K1 Identity, K2 Runtime, K3 User, K4 Execution, K5 Architecture, K6 Domain, K7 External", "High-precision intent routing and scoped RAG retrieval", "Requires categorizing user queries into explicit knowledge classes", 8, None, "Active", "2026-06-17T09:00:00Z"),
                ("ADR-004", "ADR", "Swarm Codebase Modularization", "Phase 3", "Massive bot.py monolith (460KB) causing high memory usage, circular imports, and slow startup time", "Decompose bot.py into Layer 0 Gateway (gateway.py), Layer 4 Services (services.py), LangGraph Orchestration (graph.py), and a lean bot.py entrypoint", "Eliminate code duplication, fix circular import loops, and improve modular design", "Successful modularization with clean imports and identical system execution", "Functions are now imported across files, which requires maintaining correct import paths during refactoring", 9, None, "Active", "2026-06-21T12:00:00Z"),
                ("ADR-005", "ADR", "Class C Double-Confirmation Protection", "Phase 3", "Risk of accidental destructive actions (e.g. delete_document, delete_spreadsheet) being executed without user awareness or double confirmation", "Implement Stage 2 confirmation warning flow for Class C destructive actions", "Secure external mutating and destructive actions behind a warning gate", "Blocked accidental runs of destructive commands with clean interactive approval flows", "Requires the user to explicitly confirm Class C actions with confirm/2, increasing latency by one turn", 9, None, "Active", "2026-06-21T12:00:00Z")
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
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS planned_graphs_cache (
            session_id TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            goal_class TEXT NOT NULL,
            graph_hash TEXT NOT NULL,
            raw_query TEXT NOT NULL,
            goal_graph_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (session_id, query_hash)
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_pg_cache_session_hash ON planned_graphs_cache (session_id, query_hash);")
    
    # Truncate cache if exceeds 1000 entries
    try:
        cursor.execute("SELECT COUNT(*) FROM planned_graphs_cache;")
        if cursor.fetchone()[0] > 1000:
            cursor.execute("""
                DELETE FROM planned_graphs_cache 
                WHERE created_at < datetime('now', '-24 hours');
            """)
            print("[SQLITE] Pruned planned_graphs_cache entries older than 24 hours.", flush=True)
    except Exception as prune_err:
        print(f"[SQLITE PRUNE WARNING] Failed to prune planned_graphs_cache: {prune_err}", flush=True)
    
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
            ("LESSON-001", "LESSON", "Self-Awareness Hierarchy (K1-K7)", "Phase 3", "Lack of semantic boundary organization leading to retrieval confusion", "Formally register Knowledge Classes (K1-K7) inside runtime routing", "Organize self-awareness data: K1 Identity, K2 Runtime, K3 User, K4 Execution, K5 Architecture, K6 Domain, K7 External", "High-precision intent routing and scoped RAG retrieval", "Requires categorizing user queries into explicit knowledge classes", 8, None, "Active", "2026-06-17T09:00:00Z"),
            ("ADR-004", "ADR", "Swarm Codebase Modularization", "Phase 3", "Massive bot.py monolith (460KB) causing high memory usage, circular imports, and slow startup time", "Decompose bot.py into Layer 0 Gateway (gateway.py), Layer 4 Services (services.py), LangGraph Orchestration (graph.py), and a lean bot.py entrypoint", "Eliminate code duplication, fix circular import loops, and improve modular design", "Successful modularization with clean imports and identical system execution", "Functions are now imported across files, which requires maintaining correct import paths during refactoring", 9, None, "Active", "2026-06-21T12:00:00Z"),
            ("ADR-005", "ADR", "Class C Double-Confirmation Protection", "Phase 3", "Risk of accidental destructive actions (e.g. delete_document, delete_spreadsheet) being executed without user awareness or double confirmation", "Implement Stage 2 confirmation warning flow for Class C destructive actions", "Secure external mutating and destructive actions behind a warning gate", "Blocked accidental runs of destructive commands with clean interactive approval flows", "Requires the user to explicitly confirm Class C actions with confirm/2, increasing latency by one turn", 9, None, "Active", "2026-06-21T12:00:00Z")
        ]
        for rec in seed_records:
            cursor.execute("""
                INSERT INTO architecture_knowledge (record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, rec)
    conn.commit()
    return conn




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



def is_epoch_sealed(epoch_id: str) -> bool:
    conn = None
    cursor = None
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        placeholder = "%s" if is_pg else "?"
        cursor.execute(f"SELECT 1 FROM sealed_epochs WHERE epoch_id = {placeholder}", (epoch_id,))
        res = cursor.fetchone()
        return bool(res)
    except Exception as e:
        print(f"[DB ERROR] is_epoch_sealed failed: {e}", flush=True)
        return False
    finally:
        if conn:
            try:
                if cursor:
                    cursor.close()
                conn.close()
            except Exception:
                pass


def generate_contextual_minimum(session_id: str):
    """
    EGI Axiom T1 (Transition Contract): Generates a 'Contextual Minimum' summary 
    of the sealed epoch to prevent implicit Temporal Shadowing in the next epoch.
    """
    global _histories
    with _memory_lock:
        h = list(_histories[session_id])
        if not h:
            return
            
    # Normally we'd use the LLM to summarize h, but to avoid blocking DB transactions
    # and latency, we create a structured handoff note.
    # In a full LLM implementation, we would call Groq here.
    summary_text = "[CONTEXTUAL MINIMUM HANDOFF]\n"
    summary_text += "The previous epoch was sealed. Key interactions:\n"
    for msg in h[-3:]: # Take the last 3 exchanges as the contextual minimum
        role = msg.get("role", "user")
        text = msg.get("content", "")[:100] # Truncated
        summary_text += f"- {role}: {text}...\n"
        
    with _memory_lock:
        _histories[session_id].clear()
        _histories[session_id].append({"role": "system", "content": summary_text})
        print(f"[GOVERNANCE] Contextual Minimum generated for session '{session_id}'.", flush=True)


def seal_epoch(epoch_id: str):
    conn = None
    cursor = None
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
        print(f"[GOVERNANCE] Epoch '{epoch_id}' successfully sealed.", flush=True)
    except Exception as e:
        print(f"[DB ERROR] seal_epoch failed: {e}", flush=True)
    finally:
        if conn:
            try:
                if cursor:
                    cursor.close()
                conn.close()
            except Exception:
                pass


def get_last_goal_graph(session_id: str) -> Optional[dict]:
    conn = None
    cursor = None
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
        if res and res[0]:
            meta = json.loads(res[0])
            return meta.get("graph")
    except Exception as e:
        print(f"[DB ERROR] get_last_goal_graph failed: {e}", flush=True)
        return None
    finally:
        if conn:
            try:
                if cursor:
                    cursor.close()
                conn.close()
            except Exception:
                pass

# ── Part 3: State Execution Ledger Helpers ───────────────────────────────



# ── Part 2: Stateful Search Cache Helpers ───────────────────────────────────

import hashlib




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

CURRENT_PA_MODEL   = "openai/gpt-oss-120b"
CURRENT_DEPT_MODEL = "openai/gpt-oss-20b"

def build_llm(model_name: str, temp: float):
    """Dynamically construct ChatGroq, ChatGoogleGenerativeAI, or NVIDIA ChatOpenAI based on model name and available credentials."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")

    target_model = model_name.strip()

    # 1. NVIDIA NIM Support (Model name starting with 'nvidia/')
    if target_model.startswith("nvidia/"):
        clean_model = target_model.replace("nvidia/", "")
        if nvidia_key:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=clean_model,
                temperature=temp,
                api_key=nvidia_key,
                base_url="https://integrate.api.nvidia.com/v1",
                timeout=25.0
            )
        elif openrouter_key:
            print(f"[LLM FALLBACK] NVIDIA key missing. Routing '{target_model}' through OpenRouter.", flush=True)
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=clean_model,
                temperature=temp,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                timeout=25.0
            )
        else:
            fallback = "openai/gpt-oss-20b" if "8b" in target_model.lower() or "mini" in target_model.lower() else "openai/gpt-oss-120b"
            print(f"[LLM REDIRECT] NVIDIA & OpenRouter keys missing. Mapping '{target_model}' to Groq '{fallback}'.", flush=True)
            return ChatGroq(model=fallback, temperature=temp, api_key=groq_key)

    # 2. Google Gemini Native Support
    elif target_model.startswith("gemini-"):
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
                base_url="https://openrouter.ai/api/v1",
                timeout=25.0
            )
        else:
            fallback = "openai/gpt-oss-20b"
            print(f"[LLM REDIRECT] Both Gemini and OpenRouter keys missing. Mapping '{target_model}' to Groq '{fallback}'.", flush=True)
            return ChatGroq(model=fallback, temperature=temp, api_key=groq_key)

    # 3. OpenRouter Support (Any model containing '/' except nvidia or starting with 'openrouter/')
    elif target_model.startswith("openrouter/"):
        clean_model = target_model.replace("openrouter/", "")
        if not openrouter_key:
            raise ValueError("OPENROUTER_API_KEY is not configured in environment variables.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=clean_model,
            temperature=temp,
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1",
            timeout=25.0
        )

    # 4. Default: Primary Groq Support (GPT-OSS 120B / 20B)
    else:
        return ChatGroq(model=target_model, temperature=temp, api_key=groq_key)

llm_pa   = build_llm(CURRENT_PA_MODEL,   0.2)
llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)

# ---------------------------------------------------------------------------
# Provider-level auto-failover for rate limits (Groq API -> NVIDIA NIM -> Gemini)
# Discontinued: llama-3.3-70b-versatile, llama-3.1-8b-instant
# ---------------------------------------------------------------------------

_FALLBACK_CHAIN = {
    "openai/gpt-oss-120b": ["openai/gpt-oss-20b", "nvidia/meta/llama-3.3-70b-instruct", "gemini-2.5-flash"],
    "openai/gpt-oss-20b": ["openai/gpt-oss-120b", "nvidia/meta/llama-3.1-8b-instruct", "gemini-2.5-flash"],
    "groq/compound": ["openai/gpt-oss-120b", "nvidia/meta/llama-3.3-70b-instruct", "gemini-2.5-flash"],
    "groq/compound-mini": ["openai/gpt-oss-20b", "nvidia/meta/llama-3.1-8b-instruct", "gemini-2.5-flash"],
    "nvidia/meta/llama-3.3-70b-instruct": ["openai/gpt-oss-120b", "gemini-2.5-flash"],
    "nvidia/meta/llama-3.1-8b-instruct": ["openai/gpt-oss-20b", "gemini-2.5-flash"],
    "gemini-2.5-flash": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
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

USER_PROFILE_PATH = "/etc/secrets/user_profile.json" if os.path.exists("/etc/secrets/user_profile.json") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")
_profile_lock = threading.Lock()


USER_PROFILE = load_user_profile()


    
    

    

    

            
        

    
    
        
    

            
        















    
    
    
        

        
                    

                        
                
            

    
    



                
            
            
                


    
        
    
        





    
        
            
        
            
                

            
                
                
            




    
            


        
        
        



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


def _strip_history_boilerplate(text: str) -> str:
    """
    Strip swarm output boilerplate from a BABU response before storing in
    conversation history. Keeps only the core reply the user received.

    Strips:
    - Everything from '## Workflow Overview' onward (full swarm report)
    - '💡 *System Suggestion*' promote blocks
    - 'Swarm profile:' timing lines
    - 'Reply with ...' approval prompts
    - Markdown rule separators
    """
    import re as _re
    # Strip from major swarm report headers onward
    for sentinel in (
        "## Workflow Overview",
        "## Findings",
        "## Triggered Actions",
        "## System Suggestion",
        "Swarm Output Execution Feed",
        "Swarm profile:",
    ):
        idx = text.find(sentinel)
        if idx != -1:
            text = text[:idx].strip()

    # Strip standalone promote / suggestion blocks
    text = _re.sub(
        r'💡.*?`/promote[^`]+`',
        '',
        text,
        flags=_re.DOTALL
    ).strip()

    # Strip approval prompt lines
    text = _re.sub(
        r"Reply with '1' / 'approve'.*",
        '',
        text,
        flags=_re.DOTALL
    ).strip()

    # Strip trailing markdown rulers
    text = _re.sub(r'\n---+\s*$', '', text).strip()

    return text or "[response stored]"


def add_to_history(session_id: str, user_msg: str, babu_msg: str) -> None:
    """
    Append a user/babu turn to the in-memory history deque.
    The BABU response is stripped of swarm-report boilerplate before storage
    so that planner context does not get contaminated by previous goal reports.
    """
    compact_babu = _strip_history_boilerplate(babu_msg)
    # Hard cap: never store more than 280 chars per BABU turn in history.
    # The planner only uses history_snippet[:300] anyway — storing more is waste.
    if len(compact_babu) > 280:
        compact_babu = compact_babu[:277] + "…"
    with _memory_lock:
        _histories[session_id].append(("user", user_msg))
        _histories[session_id].append(("babu", compact_babu))


def compact_completed_session_history(session_id: str) -> None:
    """Wipe intermediate details from conversation history once the goal epoch is completed.

    Threshold tightened to 250 chars so short-but-noisy blocks
    (promote suggestions, approval prompts) are also compacted.
    """
    with _memory_lock:
        history = _histories[session_id]
        if not history:
            return

        compacted = deque(maxlen=20)
        for role, content in history:
            # Re-strip boilerplate in case anything slipped through add_to_history
            cleaned = _strip_history_boilerplate(content) if role == "babu" else content
            if len(cleaned) > 250:
                cleaned = cleaned[:200] + " … [compacted]"
            compacted.append((role, cleaned))
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





# â”€â”€ LangGraph state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€



_pending_actions_lock = threading.Lock()
_pending_actions: dict[str, dict] = {}





def db_load_pending_actions() -> dict[str, dict]:
    actions = {}
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("SELECT key, data FROM system_memory WHERE key LIKE 'pending_action:%'")
        else:
            cursor.execute("SELECT key, data FROM system_memory WHERE key LIKE 'pending_action:%'")
        rows = cursor.fetchall()
        for row in rows:
            key, val = row
            if key.startswith("pending_action:"):
                sid = key[len("pending_action:"):]
                try:
                    actions[sid] = json.loads(val)
                except Exception:
                    pass
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] db_load_pending_actions failed: {e}", flush=True)
    return actions


def sync_pending_actions():
    with _pending_actions_lock:
        db_actions = db_load_pending_actions()
        _pending_actions.clear()
        _pending_actions.update(db_actions)



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



    
        

    


def resolve_action_params(params: dict, research_text: str = "") -> dict:
    """Resolve profile placeholders and optional research placeholders."""
    import re
    profile = get_current_profile()
    details = profile.get("personal_details", {}) if profile else {}
    address_str = details.get("residential_address", {}).get("address", "") if isinstance(details.get("residential_address"), dict) else details.get("residential_address", "")
    
    # Helper to extract existing file path from context
    def _extract_existing_file_path(text: str) -> Optional[str]:
        if not text:
            return None
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
            elif research_text and research_text.strip():
                if k == "subject":
                    s_match = re.search(r'(?:\*\*Subject:\*\*|Subject:)\s*(.+?)(?:\n|$)', research_text, re.IGNORECASE)
                    if s_match:
                        resolved_params[k] = s_match.group(1).strip().strip("*").strip()
                    else:
                        first_line = research_text.strip().split("\n")[0].strip().strip("*").strip()
                        resolved_params[k] = first_line[:80] if len(first_line) > 5 else "Notice from BABU"
                elif k in ("body", "content"):
                    b_text = re.sub(r'^(?:\*\*Subject:\*\*|Subject:)[^\n]*\n*', '', research_text.strip(), flags=re.IGNORECASE).strip()
                    resolved_params[k] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", b_text if b_text else research_text.strip())
                else:
                    resolved_params[k] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", research_text.strip())
            else:
                resolved_params[k] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", "").strip() or ("Notice from BABU" if k == "subject" else "")
        else:
            resolved_params[k] = v

    # If we have an upstream file path but it wasn't explicitly resolved, set it
    if upstream_file_path:
        if "image_path" not in resolved_params or not resolved_params["image_path"]:
            resolved_params["image_path"] = upstream_file_path
        if "file_path" not in resolved_params or not resolved_params["file_path"]:
            resolved_params["file_path"] = upstream_file_path

    return resolved_params
def update_cached_graph_approval(session_id: str, pending: dict) -> bool:
    import json
    import hashlib
    import string
    try:
        from .services import get_db_connection
    except ImportError:
        from services import get_db_connection
        
    user_query = pending.get("user_query")
    task_id = pending.get("task_id")
    routing_metadata = pending.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet") or {}
    goal_class = intent_packet_dict.get("query_category", "COMMUNICATION")
    
    if not user_query or not task_id:
        return False
        
    normalized = user_query.lower().strip()
    normalized = "".join(c for c in normalized if c not in string.punctuation)
    normalized = " ".join(normalized.split())
    query_hash = hashlib.sha256(normalized.encode('utf-8')).hexdigest()
    
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                "SELECT goal_graph_json FROM planned_graphs_cache WHERE session_id = %s AND query_hash = %s AND goal_class = %s",
                (session_id, query_hash, goal_class)
            )
        else:
            cursor.execute(
                "SELECT goal_graph_json FROM planned_graphs_cache WHERE session_id = ? AND query_hash = ? AND goal_class = ?",
                (session_id, query_hash, goal_class)
            )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return False
            
        graph_dict = json.loads(row[0])
        updated = False
        for task in graph_dict.get("tasks", []):
            if task.get("task_id") == task_id:
                task.setdefault("context", {})["approved"] = True
                updated = True
                break
                
        if not updated:
            cursor.close()
            conn.close()
            return False
            
        updated_graph_json = json.dumps(graph_dict)
        if is_pg:
            cursor.execute(
                "UPDATE planned_graphs_cache SET goal_graph_json = %s, created_at = CURRENT_TIMESTAMP WHERE session_id = %s AND query_hash = %s AND goal_class = %s",
                (updated_graph_json, session_id, query_hash, goal_class)
            )
        else:
            cursor.execute(
                "UPDATE planned_graphs_cache SET goal_graph_json = ?, created_at = CURRENT_TIMESTAMP WHERE session_id = ? AND query_hash = ? AND goal_class = ?",
                (updated_graph_json, session_id, query_hash, goal_class)
            )
        conn.commit()
        cursor.close()
        return True
    except Exception as e:
        print(f"[APPROVAL CACHE UPDATE ERROR] {e}", flush=True)
        return False
    finally:
        conn.close()















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





    
    


        

                    
                    


            
    
    



        










        



    


    
    
                
            
            
                




# StateGraph is imported from .graph
babu_brain = workflow.compile(checkpointer=checkpointer) if checkpointer else workflow.compile()


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
            generate_contextual_minimum(session_id)
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
                            f"To compile this workflow into BABU's muscle memory (E[Temp]), approve by sending:\n"
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
<title>BABU — Cognitive Swarm Telemetry Control Panel</title>
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
        <h1>BABU COGNITIVE SWARM</h1>
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
    <p>BABU Engine &bull; Self-Correction Checkpoints &bull; Bipartite Auditor &bull; SQLite Ledger</p>
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
<title>BABU Web Chat</title>
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
    <div>BABU Web Interface</div>
    <div style="display: flex; align-items: center; gap: 15px;">
      <button id="clear-btn" style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.2); color: #ef4444; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s;">Clear Chat</button>
      <div id="header-status">● Online</div>
    </div>
  </div>
  <div id="chat">
    <div class="msg bot">Hello! I am BABU. How can I help you today?</div>
  </div>
  <div id="input-area">
    <input type="text" id="input" placeholder="Message BABU..." autocomplete="off">
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
    let sessionId = localStorage.getItem('babu_session_id');
    if (!sessionId) {
      sessionId = 'web_session_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
      localStorage.setItem('babu_session_id', sessionId);
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
        appendMsg('⚠️ Connection error to BABU backend.', 'bot');
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
        localStorage.setItem('babu_session_id', sessionId);
        chat.innerHTML = '<div class="msg bot">Hello! I am BABU. How can I help you today?</div>';
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
    
    all_rows = []
    conn = None
    cursor = None
    try:
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
            conn = None
            cursor = None
        except Exception as e:
            print(f"[DB TELEMETRY WARNING] Primary query failed: {e}. Falling back to SQLite.", flush=True)
            if conn:
                try:
                    if cursor:
                        cursor.close()
                    conn.close()
                except Exception:
                    pass
                conn = None
                cursor = None
            try:
                import sqlite3
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT event_id, session_id, goal_id, task_id, department, event_type, metadata, timestamp 
                    FROM execution_ledger 
                    ORDER BY event_id DESC
                """)
                all_rows = cursor.fetchall()
                cursor.close()
                conn.close()
                conn = None
                cursor = None
            except Exception as sqlite_err:
                print(f"[DB TELEMETRY ERROR] SQLite fallback failed: {sqlite_err}", flush=True)
                all_rows = []
        
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




CRM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Anshu Computer & Tax Consultancy — CRM Desk</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-primary: #0a0e17;
            --bg-secondary: #111827;
            --bg-card: rgba(17, 24, 39, 0.85);
            --border: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(255, 255, 255, 0.15);
            --text-primary: #f9fafb;
            --text-secondary: #9ca3af;
            --accent-blue: #3b82f6;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-purple: #8b5cf6;
            --accent-red: #ef4444;
            --radius-md: 12px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', sans-serif; background: var(--bg-primary); color: var(--text-primary); padding: 24px; min-height: 100vh; }
        .container { max-width: 1300px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
        .title-box h1 { font-size: 24px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 10px; }
        .title-box p { color: var(--text-secondary); font-size: 14px; margin-top: 4px; }
        .badge-live { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 20px; background: rgba(16, 185, 129, 0.15); color: #10b981; font-size: 12px; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-live::before { content: ''; width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981; }
        
        .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; backdrop-filter: blur(10px); }
        .stat-label { font-size: 13px; color: var(--text-secondary); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }
        .stat-value { font-size: 32px; font-weight: 700; margin-top: 8px; font-family: 'JetBrains Mono', monospace; }
        
        .section-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 20px; margin-bottom: 24px; backdrop-filter: blur(10px); }
        .section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
        
        .services-pills { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
        .service-pill { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border); padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500; }
        
        table { width: 100%; border-collapse: collapse; font-size: 14px; text-align: left; }
        th { padding: 12px 14px; background: rgba(255, 255, 255, 0.03); color: var(--text-secondary); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
        td { padding: 14px; border-bottom: 1px solid var(--border); vertical-align: middle; }
        tr:hover td { background: rgba(255, 255, 255, 0.02); }
        
        .badge { padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
        .badge-new { background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }
        .badge-appt { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
        .badge-conv { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
        .badge-contact { background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.4); }
        
        .channel-tag { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-secondary); }
        select.status-select { background: #1f2937; color: #fff; border: 1px solid var(--border); padding: 4px 8px; border-radius: 6px; font-size: 12px; cursor: pointer; }
        select.status-select:focus { outline: none; border-color: var(--accent-blue); }
        
        .btn-refresh { background: var(--accent-blue); color: #fff; border: none; padding: 8px 16px; border-radius: 8px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.2s; }
        .btn-refresh:hover { background: #2563eb; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title-box">
                <h1>🏢 Anshu Computer & Tax Consultancy</h1>
                <p>Client CRM & Business Operations Operating Desk</p>
            </div>
            <div style="display: flex; gap: 12px; align-items: center;">
                <span class="badge-live">CRM Active</span>
                <button class="btn-refresh" onclick="fetchCRMData()">Refresh Data</button>
            </div>
        </div>

        <div class="grid-stats">
            <div class="stat-card">
                <div class="stat-label">Total Inquiries</div>
                <div class="stat-value" id="stat-total" style="color: #60a5fa;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">New Prospects</div>
                <div class="stat-value" id="stat-new" style="color: #fbbf24;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Appointments</div>
                <div class="stat-value" id="stat-appt" style="color: #a78bfa;">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Converted Clients</div>
                <div class="stat-value" id="stat-conv" style="color: #34d399;">0</div>
            </div>
        </div>

        <div class="section-card">
            <div class="section-title">
                <span>📂 Service Breakdown</span>
            </div>
            <div class="services-pills" id="services-container">
                <div class="service-pill">Loading services...</div>
            </div>
        </div>

        <div class="section-card">
            <div class="section-title">
                <span>📋 Active Client Inquiries & Leads Roster</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Lead ID</th>
                            <th>Customer Name</th>
                            <th>Channel</th>
                            <th>Contact</th>
                            <th>Service</th>
                            <th>Urgency</th>
                            <th>Status</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody id="leads-tbody">
                        <tr><td colspan="8" style="text-align: center; color: var(--text-secondary);">Loading client leads...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        async function fetchCRMData() {
            try {
                const res = await fetch('/api/crm');
                const data = await res.json();
                
                // Update stats
                const s = data.summary || {};
                document.getElementById('stat-total').textContent = s.total_leads || 0;
                document.getElementById('stat-new').textContent = s.new || 0;
                document.getElementById('stat-appt').textContent = s.appointments || 0;
                document.getElementById('stat-conv').textContent = s.converted || 0;

                // Update services
                const svcContainer = document.getElementById('services-container');
                svcContainer.innerHTML = '';
                const svcs = data.by_service || {};
                if (Object.keys(svcs).length === 0) {
                    svcContainer.innerHTML = '<div class="service-pill">No categorized leads yet</div>';
                } else {
                    for (const [k, v] of Object.entries(svcs)) {
                        const pill = document.createElement('div');
                        pill.className = 'service-pill';
                        pill.innerHTML = `<strong>${k}</strong>: ${v} inquiries`;
                        svcContainer.appendChild(pill);
                    }
                }

                // Update table
                const tbody = document.getElementById('leads-tbody');
                tbody.innerHTML = '';
                const leads = data.leads || [];
                if (leads.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-secondary);">No client inquiries recorded yet.</td></tr>';
                    return;
                }

                leads.forEach(l => {
                    const tr = document.createElement('tr');
                    const badgeClass = l.status === 'APPOINTMENT_SCHEDULED' ? 'badge-appt' : (l.status === 'CONVERTED' ? 'badge-conv' : (l.status === 'CONTACTED' ? 'badge-contact' : 'badge-new'));
                    tr.innerHTML = `
                        <td style="font-family: monospace; font-size: 12px; color: var(--text-secondary);">${l.lead_id}</td>
                        <td><strong>${l.name}</strong></td>
                        <td><span class="channel-tag">${l.channel}</span></td>
                        <td>${l.contact_info}</td>
                        <td><span style="font-weight:600;">${l.service_category}</span></td>
                        <td>${(l.urgency_score * 100).toFixed(0)}%</td>
                        <td><span class="badge ${badgeClass}">${l.status}</span></td>
                        <td>
                            <select class="status-select" onchange="updateStatus('${l.lead_id}', this.value)">
                                <option value="NEW" ${l.status === 'NEW' ? 'selected' : ''}>NEW</option>
                                <option value="CONTACTED" ${l.status === 'CONTACTED' ? 'selected' : ''}>CONTACTED</option>
                                <option value="APPOINTMENT_SCHEDULED" ${l.status === 'APPOINTMENT_SCHEDULED' ? 'selected' : ''}>APPOINTMENT</option>
                                <option value="CONVERTED" ${l.status === 'CONVERTED' ? 'selected' : ''}>CONVERTED</option>
                                <option value="LOST" ${l.status === 'LOST' ? 'selected' : ''}>LOST</option>
                            </select>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {
                console.error("Failed to load CRM data:", err);
            }
        }

        async function updateStatus(leadId, newStatus) {
            try {
                const res = await fetch('/api/crm/lead/update', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ lead_id: leadId, status: newStatus })
                });
                if (res.ok) {
                    fetchCRMData();
                } else {
                    alert("Failed to update status.");
                }
            } catch (err) {
                console.error(err);
            }
        }

        fetchCRMData();
        setInterval(fetchCRMData, 10000);
    </script>
</body>
</html>
"""


class HealthHandler(BaseHTTPRequestHandler):

    def _is_authenticated(self) -> bool:
        """Verify API token for protected endpoints."""
        token = os.environ.get("API_CHAT_TOKEN", "").strip()
        if not token:
            # When API_CHAT_TOKEN is unset, restrict access to loopback clients only
            client_ip = self.client_address[0] if self.client_address else ""
            return client_ip in ("127.0.0.1", "::1", "localhost")
        auth_header = str(self.headers.get("Authorization", "")).strip()
        api_key_header = str(self.headers.get("X-API-Key", "")).strip()
        bearer = ""
        if auth_header.lower().startswith("bearer "):
            bearer = auth_header[7:].strip()
        provided = api_key_header or bearer
        import hmac
        return bool(provided and hmac.compare_digest(provided, token))

    def _cors(self):
        origin = self.headers.get("Origin") or "*"
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key")

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
                    "status": "ok", "bot": "BABU",
                    "version": "v8-dual-bot-architecture",
                    "features": ["memory", "web_search", "knowledge_base", "google_workspace", "public_client_desk"],
                    "google_configured": bool(is_google_configured()),
                    "public_bot_configured": bool(os.environ.get("TELEGRAM_PUBLIC_BOT_TOKEN")),
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(body)
            elif path in ("/api/telemetry", "/api/telemetry/"):
                if not self._is_authenticated():
                    err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
                    return
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
            elif path in ("/api/crm", "/api/crm/"):
                if not self._is_authenticated():
                    err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
                    return
                try:
                    try:
                        from .crm_service import get_crm_pipeline_data
                    except ImportError:
                        from crm_service import get_crm_pipeline_data
                    crm_data = get_crm_pipeline_data(limit=100)
                    body = json.dumps(crm_data, ensure_ascii=False).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
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
            elif path in ("/crm", "/crm/"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(CRM_HTML.encode())
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
                if not self._is_authenticated():
                    err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
                    return
                query_params = parse_qs(parsed_path.query)
                sid = query_params.get("session_id", ["web_anon"])[0]
                
                sync_pending_actions()
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
            elif path in ("/webhook/facebook", "/webhook/facebook/"):
                query_params = parse_qs(parsed_path.query)
                mode = query_params.get("hub.mode", [""])[0]
                token = query_params.get("hub.verify_token", [""])[0]
                challenge = query_params.get("hub.challenge", [""])[0]
                expected_token = os.environ.get("FACEBOOK_VERIFY_TOKEN", "your_secret_here")
                
                if mode == "subscribe" and token == expected_token:
                    print(f"[FACEBOOK WEBHOOK VERIFICATION SUCCESS] Verified challenge for token '{token}'", flush=True)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self._cors()
                    self.end_headers()
                    self.wfile.write(challenge.encode("utf-8"))
                else:
                    print(f"[FACEBOOK WEBHOOK VERIFICATION FAILED] Invalid token '{token}' (expected '{expected_token}') or mode '{mode}'", flush=True)
                    self.send_response(403)
                    self.end_headers()
            elif path == "/api/image":
                query_params = parse_qs(parsed_path.query)
                img_path = query_params.get("path", [""])[0]
                if img_path:
                    abs_img = os.path.abspath(img_path)
                    current_dir = os.path.abspath(os.path.dirname(__file__))
                    allowed_dirs = [
                        os.path.join(current_dir, "artifacts"),
                        os.path.join(current_dir, "temp"),
                        os.path.join(current_dir, "memory"),
                        current_dir,
                    ]
                    is_contained = any(
                        abs_img == ad or abs_img.startswith(ad + os.sep)
                        for ad in allowed_dirs
                    )
                    if is_contained and os.path.exists(abs_img) and abs_img.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        self.send_response(200)
                        if abs_img.lower().endswith(".png"):
                            self.send_header("Content-Type", "image/png")
                        elif abs_img.lower().endswith(".webp"):
                            self.send_header("Content-Type", "image/webp")
                        else:
                            self.send_header("Content-Type", "image/jpeg")
                        self._cors()
                        self.end_headers()
                        try:
                            with open(abs_img, "rb") as f:
                                self.wfile.write(f.read())
                        except Exception as e:
                            print(f"[HTTP SERVER ERROR] Failed to serve image {abs_img}: {e}", flush=True)
                    else:
                        self.send_response(403 if not is_contained else 404)
                        self.end_headers()
                else:
                    self.send_response(404)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
        except (BrokenPipeError, ConnectionResetError) as e:

            print(f"[HTTP SERVER WARNING] Client disconnected during GET {self.path}: {e}", flush=True)

    def do_POST(self):
        if self.path in ("/webhook/facebook", "/webhook/facebook/"):
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length)

                # Verify Meta webhook signature if app secret configured
                app_secret = os.environ.get("FACEBOOK_APP_SECRET", "").strip()
                hub_sig = self.headers.get("X-Hub-Signature-256", "").strip()
                if app_secret:
                    import hmac, hashlib
                    expected_sig = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
                    if not hub_sig or not hmac.compare_digest(hub_sig, expected_sig):
                        print("[FACEBOOK WEBHOOK ERROR] HMAC signature verification failed.", flush=True)
                        self.send_response(403)
                        self.end_headers()
                        return

                payload = json.loads(raw_body.decode("utf-8"))
                
                # Respond 200 OK instantly to Meta within 3s
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(json.dumps({"status": "EVENT_RECEIVED"}).encode("utf-8"))
                
                # Process webhook payload asynchronously in background thread
                try:
                    from .social_media import process_facebook_webhook_event
                except ImportError:
                    from social_media import process_facebook_webhook_event
                    
                threading.Thread(target=process_facebook_webhook_event, args=(payload,), daemon=True).start()
            except Exception as e:
                print(f"[FACEBOOK WEBHOOK POST ERROR] {e}", flush=True)
                self.send_response(200)
                self.end_headers()
            return

        elif self.path == "/api/crm/lead/update":
            if not self._is_authenticated():
                err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(err)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body   = json.loads(self.rfile.read(length))
                lead_id = body.get("lead_id")
                new_status = body.get("status")
                notes = body.get("notes")
                try:
                    from .crm_service import update_lead_stage
                except ImportError:
                    from crm_service import update_lead_stage
                success = update_lead_stage(lead_id, new_status, notes)
                resp = json.dumps({"status": "success" if success else "error"}).encode()
                self.send_response(200 if success else 400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self._cors()
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self._cors()
                self.end_headers()
                self.wfile.write(err)
            return

        elif self.path == "/api/models/switch":
            if not self._is_authenticated():
                err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self._cors()
                self.end_headers()
                self.wfile.write(err)
                return
            try:
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
                    print(f"[MODEL SWITCH API] Swarm model dynamically switched to: {CURRENT_DEPT_MODEL}", flush=True)
                else:
                    CURRENT_PA_MODEL = model
                    llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
                    print(f"[MODEL SWITCH API] PA model dynamically switched to: {CURRENT_PA_MODEL}", flush=True)

                response = json.dumps({
                    "status": "success",
                    "role": role,
                    "model": model,
                    "current_pa": CURRENT_PA_MODEL,
                    "current_swarm": CURRENT_DEPT_MODEL
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(err)))
                self._cors()
                self.end_headers()
                self.wfile.write(err)
            return

        elif self.path == "/api/public_chat":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                msg = str(body.get("message", "")).strip()
                client_name = str(body.get("client_name", "Customer")).strip()
                sid = str(body.get("session_id", "")).strip()
                
                if not sid:
                    import uuid
                    sid = f"web_{uuid.uuid4().hex[:12]}"

                if not msg:
                    raise ValueError("empty message")

                try:
                    from .crm_service import get_or_create_lead, ingest_lead
                    from .public_bot import evaluate_pragya_funnel
                except ImportError:
                    from crm_service import get_or_create_lead, ingest_lead
                    from public_bot import evaluate_pragya_funnel

                lead = get_or_create_lead(sid, client_name, "PUBLIC_WEB")
                
                # If the DB already has a real name, use it instead of the generic payload name
                db_name = lead.get("name", "")
                if db_name and not any(x in db_name.lower() for x in ("customer", "user", "client", "visitor", "website")):
                    client_name = db_name
                
                reply_text, updates = evaluate_pragya_funnel(msg, lead)
                
                # Apply updates from AI to CRM
                if updates:
                    try:
                        from .services import get_db_connection
                    except ImportError:
                        from services import get_db_connection
                    
                    conn, is_pg = get_db_connection()
                    if conn:
                        cur = conn.cursor()
                        # Extract updates
                        new_name = updates.get("name")
                        new_service = updates.get("service")
                        new_phone = updates.get("phone")
                        
                        # Process state (mode, datetime) into notes
                        notes = lead.get("notes", "") or ""
                        state = {}
                        try:
                            matches = list(re.finditer(r'\[PRAGYA_STATE:\s*({.*?})\]', notes))
                            if matches:
                                # Always use the LAST state block as it has the most updated data
                                last_match = matches[-1]
                                state = json.loads(last_match.group(1))
                                # Clean up notes by removing ALL state blocks
                                notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
                        except:
                            pass
                        
                        if "mode" in updates: state["mode"] = updates["mode"]
                        
                        # AI hallucination defense for datetime
                        dt_val = (
                            updates.get("datetime") or updates.get("date") or 
                            updates.get("time") or updates.get("appointment") or 
                            updates.get("appointment_time") or updates.get("Date & Time")
                        )
                        if dt_val: state["datetime"] = dt_val
                        
                        # AI hallucination defense for phone
                        ph_val = updates.get("phone") or updates.get("mobile") or updates.get("phone_number")
                        if ph_val: updates["phone"] = ph_val
                        
                        new_phone = updates.get("phone")
                        new_service = updates.get("service")
                        
                        new_notes = notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else notes
                        
                        # Build UPDATE query dynamically
                        set_clauses = []
                        params = []
                        if new_name and new_name.lower() not in ("customer", "user", "client"):
                            set_clauses.append("name = %s" if is_pg else "name = ?")
                            params.append(new_name)
                            client_name = new_name
                        if new_service and new_service != "MISSING":
                            set_clauses.append("service_category = %s" if is_pg else "service_category = ?")
                            params.append(new_service)
                        if new_phone:
                            set_clauses.append("contact_info = %s" if is_pg else "contact_info = ?")
                            params.append(new_phone)
                        
                        set_clauses.append("notes = %s" if is_pg else "notes = ?")
                        params.append(new_notes)
                        
                        if set_clauses:
                            params.append(lead["lead_id"])
                            query = f"UPDATE babu_leads SET {', '.join(set_clauses)} WHERE lead_id = {'%s' if is_pg else '?'}"
                            cur.execute(query, tuple(params))
                            conn.commit()
                        cur.close()
                        conn.close()
                        # Check if all 5 requirements are met
                        final_name = new_name or lead.get("name")
                        final_service = new_service or lead.get("service_category")
                        final_phone = new_phone or lead.get("contact_info")
                        final_mode = state.get("mode")
                        final_dt = state.get("datetime")
                        
                        is_complete = (
                            final_name and final_name.lower() not in ("customer", "user", "client", "visitor", "website", "website visitor") and
                            final_service and final_service not in ("MISSING", "Overview", "") and
                            final_phone and re.search(r'\b[6-9]\d{9}\b', str(final_phone)) and
                            final_mode and final_mode != "MISSING" and
                            final_dt and final_dt != "MISSING"
                        )
                        print(f"DEBUG is_complete: {is_complete} | name={final_name} | svc={final_service} | phone={final_phone} | mode={final_mode} | dt={final_dt}", flush=True)
                        
                        if is_complete and not "appointment booked" in notes.lower():
                            try:
                                from .crm_service import commit_crm_appointment
                            except ImportError:
                                from crm_service import commit_crm_appointment
                            
                            try:
                                from .crm_service import parse_ist_datetime
                            except ImportError:
                                from crm_service import parse_ist_datetime
                                
                            parsed_dt = parse_ist_datetime(final_dt, final_dt)
                            if parsed_dt.get("valid"):
                                date_val = parsed_dt.get("date_str")
                                time_val = parsed_dt.get("time_str")
                                res = commit_crm_appointment(
                                    lead_id=lead["lead_id"],
                                    date_str=date_val,
                                    time_str=time_val,
                                    purpose=f"{final_mode} Consultation for {final_service}",
                                    notes=f"Pragya automated booking"
                                )
                                if res.get("status") == "SUCCESS":
                                    try:
                                        from .crm_service import REQUIRED_DOCS_BY_SERVICE
                                    except ImportError:
                                        from crm_service import REQUIRED_DOCS_BY_SERVICE
                                        
                                    OFFICE_ADDRESS = "Kaushal Market, Rath Road, Orai, Uttar Pradesh"
                                    doc_checklist = REQUIRED_DOCS_BY_SERVICE.get(final_service, REQUIRED_DOCS_BY_SERVICE.get("General", ""))
                                    
                                    if "online" in final_mode.lower():
                                        reply_text = (
                                            f"🎉 धन्यवाद! आपका **ऑनलाइन** अपॉइंटमेंट {parsed_dt.get('display_date')} को {parsed_dt.get('display_time')} के लिए सफलतापूर्वक बुक हो गया है।\n\n"
                                            f"⚠️ **ज़रूरी सूचना:** ऑनलाइन प्रोसेस के दौरान OTP (वन-टाइम पासवर्ड) की आवश्यकता होगी। कृपया तय समय पर अपना मोबाइल फोन (नंबर {final_phone}) अपने पास रखें।"
                                        )
                                    else:
                                        reply_text = (
                                            f"🎉 धन्यवाद! आपका **ऑफिस विज़िट** अपॉइंटमेंट {parsed_dt.get('display_date')} को {parsed_dt.get('display_time')} के लिए सफलतापूर्वक बुक हो गया है।\n\n"
                                            f"📍 **पता:** {OFFICE_ADDRESS}\n\n"
                                            f"📄 **कृपया अपने साथ निम्नलिखित दस्तावेज़ (Documents) लाएँ:**\n{doc_checklist}"
                                        )
                                else:
                                    alt_slots = res.get('alternatives', [])
                                    alt_str = ", ".join(alt_slots) if alt_slots else "कोई अन्य समय"
                                    reply_text = f"⚠️ क्षमा करें, यह समय पहले से बुक है। कृपया {alt_str} में से कोई अन्य समय चुनें।"
                                    
                                    if "datetime" in state:
                                        del state["datetime"]
                                        clean_notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
                                        new_notes = clean_notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else clean_notes
                                        
                                        try:
                                            from .services import get_db_connection
                                        except ImportError:
                                            from services import get_db_connection
                                            
                                        f_conn, f_pg = get_db_connection()
                                        if f_conn:
                                            f_cur = f_conn.cursor()
                                            query = "UPDATE babu_leads SET notes = %s WHERE lead_id = %s" if f_pg else "UPDATE babu_leads SET notes = ? WHERE lead_id = ?"
                                            f_cur.execute(query, (new_notes, lead["lead_id"]))
                                            f_conn.commit()
                                            f_cur.close()
                                            f_conn.close()
                            else:
                                reply_text = parsed_dt.get("message", "⚠️ कृपया एक वैध दिन और समय बताएं।")
                                if "datetime" in state:
                                    del state["datetime"]
                                    clean_notes = re.sub(r'\[PRAGYA_STATE:\s*({.*?})\]', '', notes).strip()
                                    new_notes = clean_notes + f" [PRAGYA_STATE: {json.dumps(state)}]" if state else clean_notes
                                    
                                    try:
                                        from .services import get_db_connection
                                    except ImportError:
                                        from services import get_db_connection
                                        
                                    f_conn, f_pg = get_db_connection()
                                    if f_conn:
                                        f_cur = f_conn.cursor()
                                        query = "UPDATE babu_leads SET notes = %s WHERE lead_id = %s" if f_pg else "UPDATE babu_leads SET notes = ? WHERE lead_id = ?"
                                        f_cur.execute(query, (new_notes, lead["lead_id"]))
                                        f_conn.commit()
                                        f_cur.close()
                                        f_conn.close()

                
                # Finally ingest the interaction
                ingest_lead(
                    name=client_name, 
                    channel="PUBLIC_WEB", 
                    user_message=msg, 
                    assistant_reply=reply_text, 
                    contact_info=updates.get("phone") or lead.get("contact_info"), 
                    source_ref=sid, 
                    notes="Web widget conversation step"
                )

                response = json.dumps({"reply": reply_text, "session_id": sid}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self._cors()
                self.end_headers()
                self.wfile.write(response)
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(400)
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

        if not self._is_authenticated():
            err = json.dumps({"error": "unauthorized", "message": "Valid API token required"}).encode("utf-8")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err)))
            self._cors()
            self.end_headers()
            self.wfile.write(err)
            return

        try:

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
                db_delete_pending_action(sid)
                
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

            sync_pending_actions()
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
                    # Pop draft immediately to prevent concurrent duplicate execution
                    draft = PENDING_POSTS.pop(chat_id, None)
                    if draft:
                        try:
                            from .social_media import publish_to_facebook_page
                        except ImportError:
                            from social_media import publish_to_facebook_page
                        
                        ok, result_msg = publish_to_facebook_page(draft["image_path"], draft["caption"])
                        if ok:
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
                            # Re-insert on failure to allow retry
                            PENDING_POSTS[chat_id] = draft
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

            # Get pending first without popping
            with _pending_actions_lock:
                pending = _pending_actions.get(sid)

            if pending and is_generic_approve:
                action = pending.get("action", "")
                try:
                    from .auditor import get_service_class
                except ImportError:
                    from auditor import get_service_class
                is_class_c = (get_service_class(action) == "C") if action else False
                stage = pending.get("stage", "approval")
                
                if is_class_c and stage == "approval":
                    pending["stage"] = "confirmation"
                    with _pending_actions_lock:
                        _pending_actions[sid] = pending
                    db_save_pending_action(sid, pending)
                    reply = (
                        f"⚠️ WARNING: Destructive Class C action detected.\n"
                        f"Proposed action: **{action}**\n\n"
                        f"Are you sure you want to proceed? Reply with 'confirm' or '2' to execute, or '0' / 'cancel' to reject."
                    )
                else:
                    with _pending_actions_lock:
                        pending = _pending_actions.pop(sid, None)
                    if pending:
                        db_delete_pending_action(sid)
                        draft_txt = pending.get("draft_text", pending.get("research_text", ""))
                        params = resolve_action_params(pending.get("params", {}), research_text=draft_txt)
                        if action == "send_email":
                            b_val = str(params.get("body", "")).strip()
                            if not b_val or "[needs_research_context]" in b_val.lower() or "no research" in b_val.lower():
                                if draft_txt and draft_txt.strip():
                                    params["body"] = draft_txt.strip()
                                elif pending.get("user_query"):
                                    params["body"] = pending.get("user_query")
                            s_val = str(params.get("subject", "")).strip()
                            if not s_val or "[needs_research_context]" in s_val.lower() or "no research" in s_val.lower():
                                params["subject"] = "Notice from BABU"
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
                            log_execution_ledger_event(
                                session_id=sid,
                                goal_id=pending.get("goal_id", "default"),
                                task_id=pending.get("task_id"),
                                department="execution",
                                event_type="TASK_COMPLETED",
                                state_before="RUNNING",
                                state_after="COMPLETED",
                                metadata={"result": result_msg}
                            )
                            reply = f"Action executed successfully.\n\n{result_msg}"
                        else:
                            log_execution_ledger_event(
                                session_id=sid,
                                goal_id=pending.get("goal_id", "default"),
                                task_id=pending.get("task_id"),
                                department="execution",
                                event_type="TASK_FAILED",
                                state_before="RUNNING",
                                state_after="FAILED",
                                metadata={"error": result_msg}
                            )
                            with _pending_actions_lock:
                                _pending_actions[sid] = pending
                            db_save_pending_action(sid, pending)
                            reply = f"Action execution failed.\n\n{result_msg}\n\nYou can type '2' / 'confirm' again to retry, or '0' / 'cancel' to discard." if is_class_c else f"Action execution failed.\n\n{result_msg}\n\nYou can type '1' / 'approve' again to retry, or '0' / 'cancel' to discard."
                        
                        response = json.dumps({"reply": reply, "gear": "DYNAMIC", "has_pending": False}).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(response)))
                        self._cors()
                        self.end_headers()
                        self.wfile.write(response)
                        return
                
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
                db_delete_pending_action(sid)
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


# ─── Autonomous Social Media Scheduler & State ───────────────────────────────

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_id.txt")
LAST_POST_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_post_date.txt")
LAST_PREVIEW_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_preview_date.txt")
LAST_PREVIEW_EVENING_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_preview_evening_date.txt")

# State Management for Social Post Previews
PENDING_POSTS = {}      # Map of chat_id (int) -> draft dict
WAITING_FOR_TOPIC = {}  # Map of chat_id (int) -> bool

def get_last_preview_date_morning() -> str:
    """Read the last morning (9 AM) preview generation date."""
    if os.path.exists(LAST_PREVIEW_DATE_FILE):
        try:
            with open(LAST_PREVIEW_DATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_preview_date_morning(date_str: str):
    """Write the last morning (9 AM) preview generation date."""
    try:
        with open(LAST_PREVIEW_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write morning preview date: {e}", flush=True)

def get_last_preview_date_evening() -> str:
    """Read the last evening (6 PM) preview generation date."""
    if os.path.exists(LAST_PREVIEW_EVENING_FILE):
        try:
            with open(LAST_PREVIEW_EVENING_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_preview_date_evening(date_str: str):
    """Write the last evening (6 PM) preview generation date."""
    try:
        with open(LAST_PREVIEW_EVENING_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write evening preview date: {e}", flush=True)

# Compatibility aliases
get_last_preview_date = get_last_preview_date_morning
set_last_preview_date = set_last_preview_date_morning

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
    sync_pending_actions()
    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)
    
    stage = pending.get("stage", "approval") if pending else "approval"
    if stage == "confirmation":
        keyboard = [[
            InlineKeyboardButton("Confirm", callback_data=f"action_approve|{session_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"action_cancel|{session_id}")
        ]]
    else:
        keyboard = [[
            InlineKeyboardButton("Approve", callback_data=f"action_approve|{session_id}"),
            InlineKeyboardButton("Cancel", callback_data=f"action_cancel|{session_id}")
        ]]
    return InlineKeyboardMarkup(keyboard)

async def generate_and_send_preview(chat_id: int, bot, custom_topic: str = None, reply_to_message_id: int = None, language: str = "en"):
    """Generate a high-fidelity social media draft (1080x1350) and send it to the user for approval."""
    try:
        try:
            from .social_media import generate_social_post_draft
        except ImportError:
            from social_media import generate_social_post_draft
        draft = await asyncio.to_thread(generate_social_post_draft, custom_topic, language=language)
        draft["custom_topic"] = custom_topic
        draft["language"] = language
        
        # Cache the draft
        import time
        draft["scheduled_at"] = time.time()
        draft["is_auto_scheduled"] = False  # Explicitly mark manual ad-hoc preview
        PENDING_POSTS[chat_id] = draft
        
        # 1. Send the Proposed Caption & FLUX Prompt in a separate text message
        details_text = (
            f"📝 *Proposed Caption ({('Hindi' if language=='hi' else 'English')}):*\n"
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
                f"📊 *BABU Marketing Studio — 4:5 Feed Preview*\n\n"
                f"Language: **{('Hindi (हिंदी)' if language=='hi' else 'English')}** | Ratio: **4:5 (Mobile & PC Safe)**\n"
                f"Please review the graphic above and the caption sent in the previous message.\n\n"
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
                if content.isdigit() or (content.startswith('-') and content[1:].isdigit()):
                    return int(content)
        except Exception as e:
            print(f"[CHAT_ID WARNING] Failed to read {CHAT_ID_FILE}: {e}", flush=True)
            
    admin_id = os.environ.get("TELEGRAM_USER_CHAT_ID")
    if admin_id and (admin_id.isdigit() or (admin_id.startswith('-') and admin_id[1:].isdigit())):
        return int(admin_id)
        
    return None

def persist_chat_id(chat_id: int):
    """Save the chat ID to file so the scheduler knows where to send daily previews."""
    try:
        with open(CHAT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(chat_id))
    except Exception as e:
        print(f"[CHAT_ID WARNING] Failed to save {CHAT_ID_FILE}: {e}", flush=True)

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
    """Background loop that triggers daily morning 9:00 AM (English) and evening 6:00 PM (Hindi) previews."""
    print("[SCHEDULER] Autonomous Marketing Scheduler initialized. Checking time intervals every 15 minutes...", flush=True)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    
    # Check if startup is past scheduled hours
    try:
        now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
        today_str = now_ist.strftime("%Y-%m-%d")
        if now_ist.hour >= 9:
            if get_last_preview_date_morning() != today_str:
                set_last_preview_date_morning(today_str)
        if now_ist.hour >= 18:
            if get_last_preview_date_evening() != today_str:
                set_last_preview_date_evening(today_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to run startup initialization: {e}", flush=True)
        
    while True:
        try:
            now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
            today_str = now_ist.strftime("%Y-%m-%d")
            
            # 1. Morning Trigger (9:00 AM IST or later -> English Post)
            if now_ist.hour >= 9 and get_last_preview_date_morning() != today_str:
                chat_id = get_persisted_chat_id()
                if chat_id:
                    print(f"[SCHEDULER] Triggering Morning 9:00 AM English post preview for {today_str}...", flush=True)
                    set_last_preview_date_morning(today_str)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="🌅 **Morning Marketing Swarm (9:00 AM)** engaged. Generating daily English compliance graphic and copywriting..."
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send morning notification: {err}", flush=True)

                    await generate_and_send_preview(chat_id, application.bot, language="en")
                    
                    if chat_id in PENDING_POSTS:
                        import time
                        PENDING_POSTS[chat_id]["scheduled_at"] = time.time()
                        PENDING_POSTS[chat_id]["is_auto_scheduled"] = True
                else:
                    print("[SCHEDULER] Morning slot active, but no Telegram chat ID is registered yet.", flush=True)

            # 2. Evening Trigger (6:00 PM / 18:00 IST or later -> Hindi Post)
            if now_ist.hour >= 18 and get_last_preview_date_evening() != today_str:
                chat_id = get_persisted_chat_id()
                if chat_id:
                    print(f"[SCHEDULER] Triggering Evening 6:00 PM Hindi post preview for {today_str}...", flush=True)
                    set_last_preview_date_evening(today_str)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="🌇 **Evening Marketing Swarm (6:00 PM)** engaged. Generating daily Hindi (हिंदी) compliance graphic and copywriting..."
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send evening notification: {err}", flush=True)

                    await generate_and_send_preview(chat_id, application.bot, language="hi")
                    
                    if chat_id in PENDING_POSTS:
                        import time
                        PENDING_POSTS[chat_id]["scheduled_at"] = time.time()
                        PENDING_POSTS[chat_id]["is_auto_scheduled"] = True
                else:
                    print("[SCHEDULER] Evening slot active, but no Telegram chat ID is registered yet.", flush=True)
            
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
    """Force immediately generating and sending today's marketing post preview with language selection."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Social media publishing restricted to the authorized operator.")
        return
    chat_id = update.effective_chat.id
    persist_chat_id(chat_id)
    
    args = context.args
    if args:
        arg_text = " ".join(args).strip()
        lang = "hi" if any(w in arg_text.lower() for w in ["hindi", "हिंदी", "hinglish"]) else "en"
        lang_label = "Hindi (हिंदी)" if lang == "hi" else "English"
        await update.message.reply_text(f"🎨 Generating **{lang_label}** marketing post for: '_{escape_markdown(arg_text)}_'... (15-20s)", parse_mode="Markdown")
        await generate_and_send_preview(chat_id, context.bot, custom_topic=arg_text, reply_to_message_id=update.message.message_id, language=lang)
        return
        
    # Interactive language menu
    keyboard = [
        [
            InlineKeyboardButton("🇮🇳 Post in Hindi (हिंदी)", callback_data="post_lang|hindi"),
            InlineKeyboardButton("🇬🇧 Post in English", callback_data="post_lang|english")
        ],
        [
            InlineKeyboardButton("📂 Multi-Service Catalog Poster (4:5)", callback_data="post_lang|catalog")
        ]
    ]
    await update.message.reply_text(
        "📊 **BABU Marketing Studio**\n\n"
        "Please choose language or style for today's Facebook flyer (Mobile & PC 4:5 optimized):",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command to promote a workflow pattern to a trusted template (E[Temp])."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Template promotion restricted to the authorized operator.")
        return
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
            f"BABU has now compiled this workflow into muscle memory. Subsequent runs matching this signature will bypass dynamic planning and heavy auditing."
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
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Template retirement restricted to the authorized operator.")
        return
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
        import traceback
        tb_str = traceback.format_exc()
        traceback.print_exc(file=sys.stdout)
        reply = f"BABU error: {e}\n\nTraceback:\n{tb_str}"
    finally:
        stop_typing.set()
        typing_task.cancel()

    sync_pending_actions()
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


def is_telegram_operator(update: Update) -> bool:
    """Authorize administrative commands for the registered operator."""
    op_id = os.environ.get("TELEGRAM_USER_CHAT_ID", "").strip()
    if not op_id:
        return True
    user_id = str(update.effective_user.id if update.effective_user else (update.effective_chat.id if update.effective_chat else ""))
    return user_id == op_id


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global LAST_TELEGRAM_SUCCESS_TIME
    LAST_TELEGRAM_SUCCESS_TIME = time.time()

    if not is_telegram_operator(update):
        pub_bot = os.environ.get("PUBLIC_BOT_USERNAME", "Anshu4751_bot").strip("@")
        await update.message.reply_text(
            f"🔒 *Access Restricted*\n\n"
            f"This is the private executive assistant for *Shubham Swarnkar*.\n\n"
            f"For tax, PF, GST, or business consultancy services, please visit our official client desk:\n"
            f"👉 @{pub_bot}",
            parse_mode="Markdown"
        )
        return

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

    sync_pending_actions()
    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending:
        action = pending.get("action", "")
        try:
            from .auditor import get_service_class
        except ImportError:
            from auditor import get_service_class
        
        is_class_c = (get_service_class(action) == "C") if action else False
        stage = pending.get("stage", "approval")
        
        if is_class_c:
            if stage == "approval":
                if _is_approval_message(msg):
                    pending["stage"] = "confirmation"
                    with _pending_actions_lock:
                        _pending_actions[session_id] = pending
                    db_save_pending_action(session_id, pending)
                    warning_msg = (
                        f"⚠️ WARNING: Destructive Class C action detected.\n"
                        f"Proposed action: **{action}**\n\n"
                        f"Are you sure you want to proceed? Reply with 'confirm' or '2' to execute, or '0' / 'cancel' to reject."
                    )
                    await update.message.reply_text(warning_msg, reply_markup=get_action_approval_keyboard(session_id))
                    return
                elif _is_reject_message(msg):
                    with _pending_actions_lock:
                        _pending_actions.pop(session_id, None)
                    db_delete_pending_action(session_id)
                    await update.message.reply_text("Pending action cancelled.")
                    return
                else:
                    await update.message.reply_text("You have a pending action approval. Reply with '1' / 'approve' to approve, or '0' / 'cancel' to discard.")
                    return
            elif stage == "confirmation":
                t_clean = msg.lower().strip()
                is_confirm = t_clean in ("confirm", "2", "yes", "proceed")
                is_cancel = t_clean in ("0", "cancel", "reject", "stop", "no")
                
                if is_confirm:
                    with _pending_actions_lock:
                        pending = _pending_actions.pop(session_id, None)
                    if pending:
                        db_delete_pending_action(session_id)
                        draft_txt = pending.get("draft_text", pending.get("research_text", ""))
                        params = resolve_action_params(pending.get("params", {}), research_text=draft_txt)
                        if action == "send_email":
                            b_val = str(params.get("body", "")).strip()
                            if not b_val or "[needs_research_context]" in b_val.lower() or "no research" in b_val.lower():
                                if draft_txt and draft_txt.strip():
                                    params["body"] = draft_txt.strip()
                                elif pending.get("user_query"):
                                    params["body"] = pending.get("user_query")
                            s_val = str(params.get("subject", "")).strip()
                            if not s_val or "[needs_research_context]" in s_val.lower() or "no research" in s_val.lower():
                                params["subject"] = "Notice from BABU"
                        log_execution_ledger_event(
                            session_id=session_id,
                            goal_id=pending.get("goal_id", "default"),
                            task_id=pending.get("task_id"),
                            department="execution",
                            event_type="APPROVAL_GRANTED",
                            state_before="WAITING",
                            state_after="RUNNING",
                            metadata={"by": "telegram_text_confirm", "action": action, "params": params}
                        )
                        ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
                        if ok:
                            log_execution_ledger_event(
                                session_id=session_id,
                                goal_id=pending.get("goal_id", "default"),
                                task_id=pending.get("task_id"),
                                department="execution",
                                event_type="TASK_COMPLETED",
                                state_before="RUNNING",
                                state_after="COMPLETED",
                                metadata={"result": result_msg}
                            )
                            await update.message.reply_text(f"Action executed successfully.\n\n{result_msg}")
                        else:
                            log_execution_ledger_event(
                                session_id=session_id,
                                goal_id=pending.get("goal_id", "default"),
                                task_id=pending.get("task_id"),
                                department="execution",
                                event_type="TASK_FAILED",
                                state_before="RUNNING",
                                state_after="FAILED",
                                metadata={"error": result_msg}
                            )
                            with _pending_actions_lock:
                                _pending_actions[session_id] = pending
                            db_save_pending_action(session_id, pending)
                            await update.message.reply_text(
                                f"Action execution failed.\n\n{result_msg}\n\nYou can type '2' / 'confirm' again to retry, or '0' / 'cancel' to discard.",
                                reply_markup=get_action_approval_keyboard(session_id)
                            )
                        return
                elif is_cancel:
                    with _pending_actions_lock:
                        _pending_actions.pop(session_id, None)
                    db_delete_pending_action(session_id)
                    await update.message.reply_text("Pending action cancelled.")
                    return
                else:
                    await update.message.reply_text(
                        f"⚠️ WARNING: Destructive Class C action detected. Are you sure you want to proceed? Reply with 'confirm' or '2' to execute, or '0' / 'cancel' to reject.",
                        reply_markup=get_action_approval_keyboard(session_id)
                    )
                    return
        else:
            if _is_approval_message(msg):
                with _pending_actions_lock:
                    pending = _pending_actions.pop(session_id, None)
                if pending:
                    db_delete_pending_action(session_id)
                    draft_txt = pending.get("draft_text", pending.get("research_text", ""))
                    params = resolve_action_params(pending.get("params", {}), research_text=draft_txt)
                    if action == "send_email":
                        b_val = str(params.get("body", "")).strip()
                        if not b_val or "[needs_research_context]" in b_val.lower() or "no research" in b_val.lower():
                            if draft_txt and draft_txt.strip():
                                params["body"] = draft_txt.strip()
                            elif pending.get("user_query"):
                                params["body"] = pending.get("user_query")
                        s_val = str(params.get("subject", "")).strip()
                        if not s_val or "[needs_research_context]" in s_val.lower() or "no research" in s_val.lower():
                            params["subject"] = "Notice from BABU"
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
                        log_execution_ledger_event(
                            session_id=session_id,
                            goal_id=pending.get("goal_id", "default"),
                            task_id=pending.get("task_id"),
                            department="execution",
                            event_type="TASK_COMPLETED",
                            state_before="RUNNING",
                            state_after="COMPLETED",
                            metadata={"result": result_msg}
                        )
                        await update.message.reply_text(f"Action executed successfully.\n\n{result_msg}")
                    else:
                        log_execution_ledger_event(
                            session_id=session_id,
                            goal_id=pending.get("goal_id", "default"),
                            task_id=pending.get("task_id"),
                            department="execution",
                            event_type="TASK_FAILED",
                            state_before="RUNNING",
                            state_after="FAILED",
                            metadata={"error": result_msg}
                        )
                        with _pending_actions_lock:
                            _pending_actions[session_id] = pending
                        db_save_pending_action(session_id, pending)
                        await update.message.reply_text(
                            f"Action execution failed.\n\n{result_msg}\n\nYou can type '1' / 'approve' again to retry, or '0' / 'cancel' to discard.",
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
        db_delete_pending_action(session_id)
        await update.message.reply_text("Pending action cancelled.")
        return

    if pending and not msg.startswith("/"):
        await update.message.reply_text("You have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard.")
        return

    print(f"[TG MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)

    # Text-based Social Media Review Interceptor
    review_intent = classify_review_intent(msg)
    
    if review_intent == "approve" and chat_id in PENDING_POSTS:
        # Pop draft immediately to prevent concurrent duplicate execution
        draft = PENDING_POSTS.pop(chat_id, None)
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
                WAITING_FOR_TOPIC.pop(chat_id, None)
                
                await update.message.reply_text(
                    f"Successfully published to Facebook Page!\n\n{res_msg}\n\nCaption:\n{escape_markdown(draft['caption'])}"
                )
            else:
                # Re-insert on failure to allow retry
                PENDING_POSTS[chat_id] = draft
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
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Administrative action restricted to the authorized operator.")
        return
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /launch <complex question>")
        return
    await update.message.reply_text("Swarm engaged — planning and executing goal (~30s)...")
    await run_babu(update, "launch " + text, tg_session(update))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Administrative action restricted to the authorized operator.")
        return
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
        if is_pg:
            cursor.execute("DELETE FROM planned_graphs_cache WHERE session_id = %s", (session_id,))
        else:
            cursor.execute("DELETE FROM planned_graphs_cache WHERE session_id = ?", (session_id,))
        conn.commit()
        cursor.close()
        conn.close()
        rules_status = "and historical immune rules & plan cache cleared "
    except Exception as db_err:
        print(f"[CLEAR ERROR] Database failures clear failed: {db_err}", flush=True)
        rules_status = "and rules clear attempted (with error) "
        
    await update.message.reply_text(f"Memory cleared {rules_status}for a fresh start.")



async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current active pending goals or actions and provide control buttons."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Administrative action restricted to the authorized operator.")
        return
    chat_id = update.effective_chat.id
    session_id = tg_session(update)
    
    pending_post = PENDING_POSTS.get(chat_id)
    
    sync_pending_actions()
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
        "BABU - Multi-Agent AI Assistant\n\n"
        "Unified Agent Swarm:\n"
        "- Just send a message naturally! BABU automatically decomposes your query, performs deep web research, writes drafts, and executes secure audited actions.\n"
        "- /launch <question> - Shortcut command to explicitly trigger the planner.\n\n"
        "Business CRM Desk (Anshu Consultancy):\n"
        "- /crm - View active CRM sales & inquiry pipeline digest\n"
        "- /leads - View recent client inquiries and prospects\n"
        "- /followups - View scheduled appointments & pending follow-ups\n"
        "- /follow_lead <id> <date> <time> [purpose] - Schedule appointment/follow-up\n"
        "- /update_lead <id> <status> [notes] - Update lead status (e.g. CONVERTED)\n"
        "- /add_lead <name> <phone> [service] - Register a new client lead\n\n"
        "Marketing Department:\n"
        "- /postnow - Instantly generate and post custom daily tech graphic & copy to Facebook Page\n\n"
        "Extras:\n"
        "- /goals - Show and manage active pending goals and actions\n"
        "- /clear - Reset conversation memory\n"
        "- /stats - Show runtime diagnostics\n"
        "- /model - Switch active PA and Swarm models\n"
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
            "BABU Runtime Stats\n\n"
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
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Model configuration restricted to the authorized operator.")
        return
    global llm_pa, llm_dept, CURRENT_PA_MODEL, CURRENT_DEPT_MODEL

    # Real-time environment check
    groq_active = "🟢 ACTIVE" if os.environ.get("GROQ_API_KEY") else "🔴 NOT CONFIGURED"
    gemini_active = "🟢 ACTIVE" if os.environ.get("GEMINI_API_KEY") else "🔴 NOT CONFIGURED"
    nvidia_active = "🟢 ACTIVE" if os.environ.get("NVIDIA_API_KEY") else "🔴 NOT CONFIGURED"

    args = context.args
    if not args:
        menu = (
            "🛡️ **BABU Model Settings & Telemetry**\n\n"
            f"👤 **Current Assistant (PA) Model**: `{CURRENT_PA_MODEL}`\n"
            f"👥 **Current Swarm (Research) Model**: `{CURRENT_DEPT_MODEL}`\n\n"
            
            "⚙️ **Active Providers Hierarchy (Free Tier Chain):**\n"
            f"1. 🥇 **Groq Cloud API (Primary)**: {groq_active}\n"
            f"2. 🥈 **NVIDIA NIM API (Secondary Fallback)**: {nvidia_active}\n"
            f"3. 🥉 **Gemini API Native (Third Fallback)**: {gemini_active}\n\n"
            
            "✨ **Available Active Free Models to Switch:**\n"
            "--- *Primary Free Groq Provider Models* ---\n"
            "1. `groq/compound` (Groq Compound Model)\n"
            "2. `groq/compound-mini` (Groq Compound Mini)\n"
            "3. `openai/gpt-oss-120b` (Groq 120B Open Weights - Default PA)\n"
            "4. `openai/gpt-oss-20b` (Groq 20B Open Weights - Default Swarm)\n"
            "5. `qwen/qwen3.6-27b` (Alibaba Qwen 3.6 27B on Groq)\n\n"

            "--- *Secondary NVIDIA NIM Provider Models* ---\n"
            "6. `nvidia/meta/llama-3.1-8b-instruct` (Llama 3.1 8B via NVIDIA)\n"
            "7. `nvidia/meta/llama-3.3-70b-instruct` (Llama 3.3 70B via NVIDIA)\n"
            "8. `nvidia/deepseek-ai/deepseek-v4-pro` (DeepSeek V4 Pro via NVIDIA)\n"
            "9. `nvidia/moonshotai/kimi-k2.6` (Moonshot Kimi K2.6 via NVIDIA)\n"
            "10. `nvidia/qwen/qwen3-next-80b-a3b-instruct` (Alibaba Qwen 3 Next 80B via NVIDIA)\n\n"
            
            "--- *Third Gemini Native Models* ---\n"
            "11. `gemini-2.5-flash` (Gemini 2.5 Flash Native)\n\n"
            
            "🚀 **How to Switch:**\n"
            "- `/model <1-11>` - Change the main Personal Assistant model\n"
            "- `/model swarm <1-11>` - Change the underlying swarm/research model\n"
        )
        await update.message.reply_text(menu, parse_mode="Markdown")
        return

    is_swarm = False
    choice = args[0]
    if choice.lower() == "swarm" and len(args) > 1:
        is_swarm = True
        choice = args[1]

    model_map = {
        "1": "groq/compound",
        "2": "groq/compound-mini",
        "3": "openai/gpt-oss-120b",
        "4": "openai/gpt-oss-20b",
        "5": "qwen/qwen3.6-27b",
        "6": "nvidia/meta/llama-3.1-8b-instruct",
        "7": "nvidia/meta/llama-3.3-70b-instruct",
        "8": "nvidia/deepseek-ai/deepseek-v4-pro",
        "9": "nvidia/moonshotai/kimi-k2.6",
        "10": "nvidia/qwen/qwen3-next-80b-a3b-instruct",
        "11": "gemini-2.5-flash"
    }

    selected_model = model_map.get(choice)
    if not selected_model:
        if choice in model_map.values() or "/" in choice or choice.startswith("gemini-") or choice.startswith("nvidia/"):
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


async def cmd_crm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deliver an executive CRM sales & inquiry pipeline summary."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 CRM desk access restricted to the authorized operator.")
        return
    try:
        try:
            from .crm_service import format_telegram_crm_digest
        except ImportError:
            from crm_service import format_telegram_crm_digest
        text = format_telegram_crm_digest()
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"CRM digest unavailable: {e}")


async def cmd_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent client leads, inquiries, and scheduled appointments."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Client leads list restricted to the authorized operator.")
        return
    try:
        try:
            from .crm_service import get_crm_pipeline_data
        except ImportError:
            from crm_service import get_crm_pipeline_data
        data = get_crm_pipeline_data(limit=15)
        leads = data.get("leads", [])
        if not leads:
            await update.message.reply_text("📋 No client leads recorded in CRM yet. Use `/add_lead` to register a client.")
            return
        
        lines = ["📋 **ANSHU CONSULTANCY — RECENT CLIENT LEADS**\n"]
        for idx, l in enumerate(leads, 1):
            badge = "📅" if l["status"] == "APPOINTMENT_SCHEDULED" else ("🆕" if l["status"] == "NEW" else ("🤝" if l["status"] == "CONVERTED" else "⚡"))
            c_name = str(l.get('name', 'Customer')).replace('*', '').replace('_', '').replace('`', '')
            c_notes = str(l.get('notes', '')).replace('*', '').replace('_', '').replace('`', '')
            lines.append(f"{idx}. {badge} **{c_name}** [{l['service_category']}] — Status: `{l['status']}`")
            lines.append(f"   Channel: `{l['channel']}` | Contact: `{l['contact_info']}`")
            if c_notes:
                lines.append(f"   Notes: {c_notes}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Leads list unavailable: {e}")


async def cmd_add_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Register a new lead manually via Telegram."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Administrative lead entry restricted to the authorized operator.")
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text("Usage: `/add_lead <Name> <Phone/Contact> [Service (ITR/GST/PF/General)] [Notes]`", parse_mode="Markdown")
        return
    name = args[0]
    contact = args[1]
    service = args[2] if len(args) > 2 else "General"
    notes = " ".join(args[3:]) if len(args) > 3 else "Manual entry via Telegram"
    try:
        try:
            from .crm_service import ingest_lead
        except ImportError:
            from crm_service import ingest_lead
        res = ingest_lead(name=name, channel="Telegram Walk-in", user_message=f"Manual client registration for {service}", contact_info=contact, notes=notes)
        if res.get("status") == "SUCCESS":
            await update.message.reply_text(f"✅ Client lead registered successfully!\n• Lead ID: `{res.get('lead_id')}`\n• Name: **{name}**\n• Service: `{res.get('service_category')}`\n• Contact: `{contact}`", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Failed to register lead: {res.get('error')}")
    except Exception as e:
        await update.message.reply_text(f"Error adding lead: {e}")


async def cmd_followups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View active scheduled appointments and pending follow-ups, or schedule a new one."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Follow-ups management restricted to the authorized operator.")
        return
    args = context.args
    try:
        try:
            from .crm_service import get_crm_pipeline_data, commit_crm_appointment, parse_ist_datetime, check_slot_availability
        except ImportError:
            from crm_service import get_crm_pipeline_data, commit_crm_appointment, parse_ist_datetime, check_slot_availability

        # 1. Schedule a follow-up if args provided: /follow_lead <lead_id> <date> <time> [purpose]
        if args and len(args) >= 3:
            lead_id = args[0]
            date_expr = args[1]
            time_expr = args[2]
            purpose = " ".join(args[3:]) if len(args) > 3 else "Follow-up Consultation"

            dt_res = parse_ist_datetime(date_expr, time_expr)
            if not dt_res.get("valid"):
                await update.message.reply_text(f"❌ Invalid appointment slot: {dt_res.get('message', dt_res.get('reason'))}")
                return

            avail_ok, alt_slots = check_slot_availability(dt_res["date_str"], dt_res["time_str"])
            if not avail_ok:
                alt_str = ", ".join(alt_slots) if alt_slots else "another time between 11 AM - 6 PM"
                await update.message.reply_text(f"⚠️ Slot on {dt_res['display_date']} at {dt_res['display_time']} is already occupied.\nAvailable options: {alt_str}")
                return

            commit_res = commit_crm_appointment(lead_id, dt_res["date_str"], dt_res["time_str"], purpose=purpose, notes="Scheduled via Telegram")
            if commit_res.get("status") == "SUCCESS":
                await update.message.reply_text(
                    f"✅ **Appointment / Follow-up Scheduled!**\n"
                    f"• Lead ID: `{lead_id}`\n"
                    f"• Date: **{dt_res['display_date']}**\n"
                    f"• Time: **{dt_res['display_time']}**\n"
                    f"• Purpose: _{purpose}_",
                    parse_mode="Markdown"
                )
            else:
                await update.message.reply_text(f"❌ Failed to schedule: {commit_res.get('error')}")
            return

        # 2. View active followups list
        data = get_crm_pipeline_data(limit=20)
        followups = data.get("followups", [])
        if not followups:
            await update.message.reply_text("📅 No pending appointments or follow-ups in CRM right now.\nUse `/follow_lead <lead_id> <date> <time> [purpose]` to schedule one.", parse_mode="Markdown")
            return

        lines = ["📅 **SCHEDULED APPOINTMENTS & PENDING FOLLOW-UPS**\n"]
        for idx, f in enumerate(followups, 1):
            lines.append(f"{idx}. 🗓️ **{f.get('name', 'Customer')}** (Lead: `{f.get('lead_id')}`)")
            lines.append(f"   Slot: **{f.get('scheduled_date')}** | Action: `{f.get('proposed_action')}`")
            if f.get('draft_message'):
                lines.append(f"   Details: _{f['draft_message']}_")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Follow-ups unavailable: {e}")


async def cmd_update_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update lead funnel status (e.g. /update_lead <lead_id> <CONVERTED/CONTACTED/LOST> [notes])."""
    if not is_telegram_operator(update):
        await update.message.reply_text("🔒 Administrative action restricted to the authorized operator.")
        return
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: `/update_lead <Lead_ID> <Status> [Notes]`\n\nValid Statuses: `NEW`, `SERVICE_IDENTIFIED`, `CONTACTED`, `APPOINTMENT_SCHEDULED`, `CONVERTED`, `LOST`",
            parse_mode="Markdown"
        )
        return

    lead_id = args[0]
    new_status = args[1].upper()
    notes = " ".join(args[2:]) if len(args) > 2 else None

    valid_statuses = ("NEW", "SERVICE_IDENTIFIED", "CONTACTED", "APPOINTMENT_SCHEDULED", "CONVERTED", "LOST", "FOLLOW_UP_REQUIRED")
    if new_status not in valid_statuses:
        await update.message.reply_text(f"❌ Invalid status. Must be one of: {', '.join(valid_statuses)}")
        return

    try:
        try:
            from .crm_service import update_lead_funnel_stage
        except ImportError:
            from crm_service import update_lead_funnel_stage
        ok = update_lead_funnel_stage(lead_id, new_status, notes=notes)
        if ok:
            await update.message.reply_text(f"✅ Lead `{lead_id}` status updated to **{new_status}**.", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"❌ Could not update lead `{lead_id}`. Please check the Lead ID.")
    except Exception as e:
        await update.message.reply_text(f"Error updating lead: {e}")


async def on_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback button clicks (Approve, Change Topic, Cancel) for social post reviews."""
    query = update.callback_query
    if not is_telegram_operator(update):
        await query.answer("🔒 Restricted to the authorized operator.", show_alert=True)
        return
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
            sync_pending_actions()
            with _pending_actions_lock:
                pending = _pending_actions.get(session_id)
            if not pending:
                await query.edit_message_text("No pending action found to approve.")
                return
            
            action = pending.get("action", "")
            try:
                from .auditor import get_service_class
            except ImportError:
                from auditor import get_service_class
            
            is_class_c = (get_service_class(action) == "C") if action else False
            stage = pending.get("stage", "approval")
            
            if is_class_c and stage == "approval":
                pending["stage"] = "confirmation"
                with _pending_actions_lock:
                    _pending_actions[session_id] = pending
                db_save_pending_action(session_id, pending)
                
                warning_msg = (
                    f"⚠️ WARNING: Destructive Class C action detected.\n"
                    f"Proposed action: **{action}**\n\n"
                    f"Are you sure you want to proceed? Reply with 'confirm' or '2' to execute, or click Confirm below."
                )
                await query.edit_message_text(warning_msg, reply_markup=get_action_approval_keyboard(session_id))
                return
                
            # Otherwise, pop and execute!
            with _pending_actions_lock:
                pending = _pending_actions.pop(session_id, None)
            if pending:
                db_delete_pending_action(session_id)
                draft_txt = pending.get("draft_text", pending.get("research_text", ""))
                params = resolve_action_params(pending.get("params", {}), research_text=draft_txt)
                if action == "send_email":
                    b_val = str(params.get("body", "")).strip()
                    if not b_val or "[needs_research_context]" in b_val.lower() or "no research" in b_val.lower():
                        if draft_txt and draft_txt.strip():
                            params["body"] = draft_txt.strip()
                        elif pending.get("user_query"):
                            params["body"] = pending.get("user_query")
                    s_val = str(params.get("subject", "")).strip()
                    if not s_val or "[needs_research_context]" in s_val.lower() or "no research" in s_val.lower():
                        params["subject"] = "Notice from BABU"
                
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
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=pending.get("goal_id", "default"),
                        task_id=pending.get("task_id"),
                        department="execution",
                        event_type="TASK_COMPLETED",
                        state_before="RUNNING",
                        state_after="COMPLETED",
                        metadata={"result": result_msg}
                    )
                    status = "Action executed successfully."
                    await query.edit_message_text(f"{status}\n\n{result_msg}")
                else:
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=pending.get("goal_id", "default"),
                        task_id=pending.get("task_id"),
                        department="execution",
                        event_type="TASK_FAILED",
                        state_before="RUNNING",
                        state_after="FAILED",
                        metadata={"error": result_msg}
                    )
                    with _pending_actions_lock:
                        _pending_actions[session_id] = pending
                    db_save_pending_action(session_id, pending)
                    
                    status = "Action execution failed."
                    await query.edit_message_text(
                        f"{status}\n\n{result_msg}\n\nYou can click Confirm again to retry, or Cancel." if is_class_c else f"{status}\n\n{result_msg}\n\nYou can click Approve again to retry, or Cancel.",
                        reply_markup=get_action_approval_keyboard(session_id)
                    )
            return

        if data.startswith("action_cancel|"):
            session_id = data.split("|", 1)[1].strip()
            sync_pending_actions()
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
            db_delete_pending_action(session_id)
            await query.edit_message_text("Pending action cancelled.")
            return
        
        if data.startswith("post_lang|"):
            selected_lang = data.split("|")[1]
            if selected_lang == "hindi":
                await edit_callback_message(query, "🎨 Generating custom **Hindi (हिंदी)** marketing flyer... (15-20s)")
                await generate_and_send_preview(chat_id, context.bot, custom_topic=None, language="hi")
            elif selected_lang == "catalog":
                await edit_callback_message(query, "🎨 Generating **Multi-Service Catalog Poster (4:5)**... (15-20s)")
                await generate_and_send_preview(chat_id, context.bot, custom_topic="FORCE_CATALOG", language="en")
            else:
                await edit_callback_message(query, "🎨 Generating custom **English** marketing flyer... (15-20s)")
                await generate_and_send_preview(chat_id, context.bot, custom_topic=None, language="en")
            return

        if data == "post_approve":
            # Pop draft immediately to prevent concurrent duplicate execution
            draft = PENDING_POSTS.pop(chat_id, None)
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
                # Re-insert on failure to allow retry
                PENDING_POSTS[chat_id] = draft
                
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
    try:
        from .bootstrap import bootstrap_brain
    except ImportError:
        from bootstrap import bootstrap_brain
    bootstrap_brain()
