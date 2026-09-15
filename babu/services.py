import os
import sqlite3
import json
import hashlib
import threading
import re
import time
from datetime import datetime, timezone, timedelta
try:
    import requests
except Exception:
    requests = None
from typing import Optional, List, Any, Tuple, Dict

try:
    from .google_service import is_google_configured
except Exception:
    try:
        from google_service import is_google_configured
    except Exception:
        def is_google_configured():
            return False

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "babu_checkpoint.db")
DATABASE_URL = os.environ.get("DATABASE_URL")
REFUSAL_PRIVATE_DATA = "Information unavailable. No authoritative business records were found."

try:
    from .telematics import (
        get_token_costs,
        calculate_inference_cost,
        extract_tokens,
        add_tokens,
        get_telemetry_snapshot,
        get_uptime_summary,
        BOT_START_TIME,
        PRICING_TABLE
    )
    from .data_catalog import (
        RUNTIME_DATA_CATALOG,
        get_runtime_data_catalog,
        resolve_knowledge_class_for_query
    )
except ImportError:
    from telematics import (
        get_token_costs,
        calculate_inference_cost,
        extract_tokens,
        add_tokens,
        get_telemetry_snapshot,
        get_uptime_summary,
        BOT_START_TIME,
        PRICING_TABLE
    )
    from data_catalog import (
        RUNTIME_DATA_CATALOG,
        get_runtime_data_catalog,
        resolve_knowledge_class_for_query
    )

def _ensure_sqlite_schema(conn):
    """Create required SQLite tables if they do not exist."""
    cursor = conn.cursor()
    # sealed_epochs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sealed_epochs (
            epoch_id TEXT PRIMARY KEY,
            sealed_at TEXT
        );
    """)
    # search_cache
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query_hash TEXT PRIMARY KEY,
            raw_query TEXT,
            distilled_results TEXT,
            sources TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # execution_ledger
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
    # system_memory
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_memory (
            key TEXT PRIMARY KEY,
            data TEXT
        );
    """)
    # trusted_templates
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
    # babu_temporal_timeline
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
    # architecture_knowledge
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
    # babu_k0_working_memory
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
    # babu_knowledge (RAG storage fallback)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            source TEXT NOT NULL,
            title TEXT NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_collection ON babu_knowledge (collection);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_babu_knowledge_source ON babu_knowledge (source);")

    # Business CRM Independent Plane
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_leads (
            lead_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            channel TEXT NOT NULL,
            contact_info TEXT,
            service_category TEXT NOT NULL DEFAULT 'General',
            status TEXT NOT NULL DEFAULT 'NEW',
            urgency_score REAL DEFAULT 0.5,
            estimated_value REAL DEFAULT 0.0,
            notes TEXT,
            source_ref TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_interactions (
            interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id TEXT NOT NULL,
            channel TEXT NOT NULL,
            sender_id TEXT,
            sender_name TEXT,
            user_message TEXT NOT NULL,
            assistant_reply TEXT NOT NULL,
            intent TEXT,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS babu_followups (
            followup_id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id TEXT NOT NULL,
            scheduled_date TEXT NOT NULL,
            proposed_action TEXT NOT NULL,
            draft_message TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON babu_leads (status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_category ON babu_leads (service_category);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_interactions_lead ON babu_interactions (lead_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_lead_status ON babu_followups (lead_id, status);")
    try:
        cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_appointment_slot ON babu_followups (scheduled_date) WHERE status = 'PENDING' AND proposed_action = 'IN_OFFICE_APPOINTMENT';")
    except Exception:
        pass

    # Bootstrap initial seed data for empty databases (e.g. fresh Render deployments)
    try:
        cursor.execute("SELECT COUNT(*) FROM babu_temporal_timeline")
        if cursor.fetchone()[0] == 0:
            initial_milestones = [
                ("SYSTEM_BOOT", "Project BABU Cognitive OS Initialized (v3.5.0)", "SUCCESS", 1.0, "Fresh deployment startup", "All 6 department workers online", "System operational", 1.0, '{"version": "3.5.0"}'),
                ("IDENTITY_GROUNDING", "Identity & Temporal Awareness Anchored (DOB: May 27, 2026)", "SUCCESS", 1.0, "DOB calibration", "IST clock synchronized", "Operational", 1.0, '{"dob": "2026-05-27"}'),
                ("COMPLIANCE_CADENCE", "Indian Statutory Compliance Horizon Activated", "SUCCESS", 1.0, "Statutory cadence setup", "Monitoring GST (11th/20th), PF (15th), and ITR deadlines", "Active monitoring", 0.95, '{"cadence": "monthly"}'),
                ("ARCHITECTURE_INDEXING", "Indexed 95 Architectural Decision Records (ADRs 001-095)", "SUCCESS", 1.0, "Knowledge layer sync", "Zero bulk dumps enforced across K0-K7 classes", "Indexed in SQLite", 1.0, '{"adrs": 95}'),
                ("MARKETING_CAMPAIGN", "Tax & PF Consultancy Marketing Engine Initialized", "SUCCESS", 1.0, "Flyer template verification", "Autonomous posting ready", "Operational", 0.9, '{"channel": "Facebook"}')
            ]
            for cat, summ, out, score, cause, effect, resol, conf, meta in initial_milestones:
                cursor.execute("""
                    INSERT INTO babu_temporal_timeline (event_category, summary, outcome, impact_score, cause, effect, resolution, confidence, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cat, summ, out, score, cause, effect, resol, conf, meta))

        cursor.execute("SELECT COUNT(*) FROM execution_ledger")
        if cursor.fetchone()[0] == 0:
            initial_ledger = [
                ("boot_session", "G-BOOT", "T-INIT", "pa", "SYSTEM_STARTUP", "STARTING", "READY", '{"tokens": {"prompt": 1250, "completion": 420, "total": 1670}, "model": "gemini-2.5-pro", "latency": 0.25}'),
                ("boot_session", "G-BOOT", "T-ROUTER", "pa", "INTENT_CLASSIFICATION", "READY", "OPTIMAL", '{"tokens": {"prompt": 850, "completion": 180, "total": 1030}, "model": "llama-3.1-8b-instant", "latency": 0.08}'),
                ("boot_session", "G-BOOT", "T-AUDIT", "pa", "AUDIT_PRE_PASS", "EVALUATING", "PASSED", '{"tokens": {"prompt": 600, "completion": 120, "total": 720}, "model": "gemini-2.5-flash", "latency": 0.12}')
            ]
            for sess, gid, tid, dept, ev_type, sb, sa, meta in initial_ledger:
                cursor.execute("""
                    INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (sess, gid, tid, dept, ev_type, sb, sa, meta))
    except Exception as seed_err:
        print(f"[DB BOOTSTRAP WARNING] Failed to seed initial database records: {seed_err}", flush=True)

    conn.commit()
    cursor.close()

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
        "The Strategic Planner and Personal Assistant (PA) use openai/gpt-oss-120b (Groq API open weights, highest reasoning fidelity). "
        "Swarm workers and social webhooks use openai/gpt-oss-20b (Groq API open weights, ultra-fast <0.3s). "
        "Legacy Llama models (llama-3.3-70b-versatile and llama-3.1-8b-instant) are discontinued. "
        "All primary inference runs on Groq API with secondary failover to NVIDIA NIM and Google Gemini."
    ),
}

USER_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")
_profile_lock = threading.Lock()
_PG_FAILED = False
_PG_LAST_RETRY = 0
_PG_RETRY_INTERVAL = 300  # Try reconnecting to PostgreSQL at most once every 5 minutes if it failed
_SQLITE_SCHEMA_INITIALIZED = False
_schema_lock = threading.Lock()

def _ensure_sqlite_schema_once(conn):
    """Run schema creation once per process to eliminate lock contention on every query."""
    global _SQLITE_SCHEMA_INITIALIZED
    if not _SQLITE_SCHEMA_INITIALIZED:
        with _schema_lock:
            if not _SQLITE_SCHEMA_INITIALIZED:
                _ensure_sqlite_schema(conn)
                _SQLITE_SCHEMA_INITIALIZED = True

def get_db_connection():
    global _PG_FAILED, _PG_LAST_RETRY
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        if db_url.startswith("postgres://") or db_url.startswith("postgresql://"):
            now = time.time()
            if not _PG_FAILED or (now - _PG_LAST_RETRY > _PG_RETRY_INTERVAL):
                try:
                    import psycopg2
                    url = db_url
                    if url.startswith("postgres://"):
                        url = url.replace("postgres://", "postgresql://", 1)
                    conn = psycopg2.connect(url, connect_timeout=5)
                    conn.set_client_encoding('UTF8')
                    _PG_FAILED = False
                    return conn, True
                except Exception as e:
                    if not _PG_FAILED:
                        print(f"[DB WARNING] PostgreSQL unavailable; falling back to SQLite at {DB_PATH}: {e}", flush=True)
                    _PG_FAILED = True
                    _PG_LAST_RETRY = now
        elif db_url.startswith("sqlite:///"):
            path = db_url.replace("sqlite:///", "", 1)
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            conn = sqlite3.connect(path, timeout=30.0)
            _ensure_sqlite_schema_once(conn)
            return conn, False
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    _ensure_sqlite_schema_once(conn)
    return conn, False

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

def get_daily_activity_summary(date_target: Optional[str] = None, relative_days: Optional[int] = None) -> dict:
    """
    Retrieve activity summary from babu_temporal_timeline and execution_ledger for a given date in IST.
    Supports relative_days (0 for today, -1 for yesterday, etc.) or specific date strings.
    """
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    
    if relative_days is not None:
        target_dt = now_ist + timedelta(days=relative_days)
    elif date_target:
        dt_clean = date_target.lower().strip()
        if dt_clean in ("yesterday", "kal", "beeta kal", "previous day"):
            target_dt = now_ist - timedelta(days=1)
        elif dt_clean in ("today", "aaj", "current day"):
            target_dt = now_ist
        else:
            # Try parsing YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY
            parsed = False
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d"):
                try:
                    target_dt = datetime.strptime(date_target.strip(), fmt)
                    parsed = True
                    break
                except Exception:
                    pass
            if not parsed:
                target_dt = now_ist - timedelta(days=1) if "yesterday" in dt_clean or "kal" in dt_clean else now_ist
    else:
        target_dt = now_ist

    target_date_str = target_dt.strftime("%Y-%m-%d")
    display_date = target_dt.strftime("%A, %B %d, %Y")
    
    summary = {
        "target_date": target_date_str,
        "display_date": display_date,
        "is_today": (target_date_str == now_ist.strftime("%Y-%m-%d")),
        "is_yesterday": (target_date_str == (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")),
        "social_posts": [],
        "research_tasks": [],
        "governance_events": [],
        "general_tasks": [],
        "total_events": 0,
        "executive_text": ""
    }
    
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        # 1. Fetch from babu_temporal_timeline
        if is_pg:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, metadata
                FROM babu_temporal_timeline
                WHERE DATE(timestamp) = %s
                ORDER BY event_id ASC
            """, (target_date_str,))
        else:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, metadata
                FROM babu_temporal_timeline
                WHERE DATE(timestamp) = ? OR timestamp LIKE ?
                ORDER BY event_id ASC
            """, (target_date_str, f"{target_date_str}%"))
            
        temporal_rows = cursor.fetchall()
        
        # 2. Fetch from execution_ledger
        if is_pg:
            cursor.execute("""
                SELECT event_id, session_id, goal_id, task_id, department, event_type, metadata, timestamp
                FROM execution_ledger
                WHERE DATE(timestamp) = %s
                ORDER BY event_id ASC
            """, (target_date_str,))
        else:
            cursor.execute("""
                SELECT event_id, session_id, goal_id, task_id, department, event_type, metadata, timestamp
                FROM execution_ledger
                WHERE DATE(timestamp) = ? OR timestamp LIKE ?
                ORDER BY event_id ASC
            """, (target_date_str, f"{target_date_str}%"))
            
        ledger_rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        for r in temporal_rows:
            ev_id, ts, cat, summ, out, meta_str = r
            entry = {"id": ev_id, "time": str(ts), "category": cat, "summary": summ, "outcome": out}
            summary["total_events"] += 1
            if cat in ("SOCIAL_POST", "MARKETING", "FACEBOOK"):
                summary["social_posts"].append(entry)
            elif cat in ("RESEARCH", "SEARCH", "COMPLIANCE"):
                summary["research_tasks"].append(entry)
            elif cat in ("GOVERNANCE", "IMMUNE_LESSON", "AUDIT", "FAILURES"):
                summary["governance_events"].append(entry)
            else:
                summary["general_tasks"].append(entry)
                
        for r in ledger_rows:
            ev_id, sess_id, g_id, t_id, dept, ev_type, meta_str, ts = r
            if ev_type in ("GOAL_COMPLETE", "EXECUTION_SUCCESS", "EXECUTION_COMPLETE"):
                summary["total_events"] += 1
                entry = {"id": ev_id, "time": str(ts), "goal_id": g_id, "department": dept, "event_type": ev_type}
                if dept == "writing" or "social" in (g_id or "").lower():
                    summary["social_posts"].append(entry)
                elif dept == "research":
                    summary["research_tasks"].append(entry)
                else:
                    summary["general_tasks"].append(entry)

        # Build structured executive text
        day_label = "Yesterday" if summary["is_yesterday"] else ("Today" if summary["is_today"] else target_date_str)
        text_lines = [
            f"### 📅 Activity Summary for {day_label} ({display_date})\n",
            f"**Current IST Reference Time:** {now_ist.strftime('%I:%M %p, %d %b %Y')}\n"
        ]
        
        if summary["total_events"] == 0:
            text_lines.append(f"No autonomous tasks or ledger events were recorded for **{display_date}**.")
        else:
            if summary["social_posts"]:
                text_lines.append(f"**📱 Social Media & Marketing ({len(summary['social_posts'])} action{'s' if len(summary['social_posts']) > 1 else ''})**")
                for item in summary["social_posts"][:5]:
                    summ = item.get("summary") or f"Post execution for goal {item.get('goal_id', '')}"
                    out = item.get("outcome") or "Completed"
                    text_lines.append(f"- ✅ **{summ}** ({out})")
                text_lines.append("")
                
            if summary["research_tasks"]:
                text_lines.append(f"**🔍 Research & Compliance ({len(summary['research_tasks'])} task{'s' if len(summary['research_tasks']) > 1 else ''})**")
                for item in summary["research_tasks"][:5]:
                    summ = item.get("summary") or f"Research topic under goal {item.get('goal_id', '')}"
                    text_lines.append(f"- 🔎 {summ}")
                text_lines.append("")
                
            if summary["governance_events"]:
                text_lines.append(f"**⚙️ Governance, Audits & Immune Health ({len(summary['governance_events'])} event{'s' if len(summary['governance_events']) > 1 else ''})**")
                for item in summary["governance_events"][:5]:
                    summ = item.get("summary") or f"Audit event {item.get('id', '')}"
                    text_lines.append(f"- 🛡️ {summ}")
                text_lines.append("")
                
            if summary["general_tasks"]:
                text_lines.append(f"**📋 System Operations & Goals ({len(summary['general_tasks'])} event{'s' if len(summary['general_tasks']) > 1 else ''})**")
                for item in summary["general_tasks"][:5]:
                    summ = item.get("summary") or f"Completed goal {item.get('goal_id', '')} in {item.get('department', 'general')}"
                    text_lines.append(f"- ⚡ {summ}")
                text_lines.append("")
                
            text_lines.append(f"**Total Activities Logged:** {summary['total_events']}")
            
        summary["executive_text"] = "\n".join(text_lines)
        return summary
    except Exception as e:
        print(f"[TEMPORAL SUMMARY ERROR] Failed to generate activity summary: {e}", flush=True)
        summary["executive_text"] = f"Unable to fetch historical activity for {display_date} due to a database query issue: {e}"
        return summary

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

BUSINESS_PROFILE_PATH = os.path.join(os.path.dirname(__file__), "business_profile.json")

def load_business_profile() -> dict:
    if os.path.exists(BUSINESS_PROFILE_PATH):
        try:
            with open(BUSINESS_PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[BUSINESS PROFILE LOAD ERROR] {e}", flush=True)
    return {}

def get_business_profile_text() -> str:
    """Format decoupled public business facts and client FAQs for public customer support (Facebook/Web)."""
    bp = load_business_profile()
    if not bp:
        return ""
    
    lines = [
        f"Business Name: {bp.get('business_name')}",
        f"Business Type: {bp.get('business_type')}",
        f"Office Location: {bp.get('location', {}).get('office')}",
        f"Contact Phone/WhatsApp: {bp.get('contact', {}).get('whatsapp')}",
        f"Contact Email: {bp.get('contact', {}).get('email')}",
        "\nCore Services Offered:",
        f"- PF Consultancy: {', '.join(bp.get('core_services', {}).get('pf_consultancy', []))}",
        f"- Income Tax Services: {', '.join(bp.get('core_services', {}).get('tax_services', []))}",
        f"- GST Services: {', '.join(bp.get('core_services', {}).get('gst_services', []))}",
        f"- CSC E-Governance: {', '.join(bp.get('core_services', {}).get('csc_e_governance_services', []))}",
        "\nOfficial Client FAQs & Pricing Guidelines:"
    ]
    
    for faq in bp.get("client_faqs", []):
        lines.append(f"Q: {faq.get('question')}\nA: {faq.get('answer')}\n")
        
    return "\n".join(lines)

def get_current_profile() -> dict:
    return load_user_profile()

def is_profile_relevant_query(query: str) -> bool:
    """Determine if a query is relevant to the user's personal details, business, or family graph."""
    q = query.lower()
    public_knowledge_prefixes = ("what is ", "what are ", "how to ", "explain ", "definition of ", "tell me about ", "general ")
    if q.startswith(public_knowledge_prefixes) and not any(marker in q for marker in (" my ", " me ", " mine", "my ")):
        return False
    keywords = {
        "mother", "father", "wife", "son", "daughter", "brother", "sister", "family", "parent",
        "spouse", "mbabuge", "marriage", "uncle", "aunty", "nephew", "niece", "cousin",
        "nickname", "name", "email", "phone", "address", "birthday", "dob", "birth", "age",
        "company", "business", "bussiness", "busines", "client", "gst", "pf", "tax", "consultancy", "consultant",
        "myself", "about me", "who am i", "my journey", "my background", "my profile", "my biography",
        "who is talk", "who is speak", "shubham", "swarnkar", "anshu", "personal", "transaction", "my", "me"
    }
    return any(kw in q for kw in keywords)

def is_private_data_query(query: str, category: Optional[str] = None) -> bool:
    if category in ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "COMMUNICATION", "WORKSPACE"):
        return True
    if category in ("PUBLIC_INFORMATION", "CONVERSATION"):
        return False
    return is_profile_relevant_query(query)

def get_user_profile_text(profile_type: str = "FULL") -> str:
    """Load and format the local user profile details from JSON as raw context for LLM verification."""
    profile = get_current_profile()
    if not profile:
        return ""
        
    lines = []
    
    # 1. Daily Details (L1)
    details = profile.get("personal_details", {})
    if details:
        lines.append("=== L1 DAILY DETAILS ===")
        for k, v in details.items():
            if v and not str(v).startswith("["):
                lines.append(f"{k.upper()}: {v}")
        lines.append("")
                
    # 2. Business Context (L1)
    business = profile.get("business_context", {})
    if business:
        lines.append("=== L1 BUSINESS CONTEXT ===")
        for k, v in business.items():
            if v and not str(v).startswith("["):
                lines.append(f"{k.upper()}: {v}")
        lines.append("")
        
    # 3. Family Graph (L2)
    family = profile.get("family_graph", {})
    if family:
        lines.append("=== L2 FAMILY GRAPH ===")
        for relation, data in family.items():
            if isinstance(data, dict):
                members = ", ".join(f"{k}: {v}" for k, v in data.items() if v and not str(v).startswith("["))
                if members:
                    lines.append(f"{relation.upper()}: {members}")
        lines.append("")
        
    # 4. Mindset & Journey (L3)
    journey = profile.get("mindset_and_journey", {})
    if journey and profile_type == "FULL":
        lines.append("=== L3 MINDSET & AUTOBIOGRAPHICAL JOURNEY ===")
        for cat, det in journey.items():
            lines.append(f"[{cat.upper()}]:")
            if isinstance(det, dict):
                for k, v in det.items():
                    lines.append(f"  • {k}: {v}")
            else:
                lines.append(f"  {det}")
        lines.append("")
        
    # 5. Education & Career (L3)
    edu_career = profile.get("education_and_career", {})
    if edu_career and profile_type == "FULL":
        lines.append("=== L3 EDUCATION & CAREER ===")
        for edu in edu_career.get("education", []):
            lines.append(f"• EDUCATION: {edu}")
        for emp in edu_career.get("employment_history", []):
            lines.append(f"• EMPLOYMENT: {emp}")
        lines.append("")
        
    return "\n".join(lines).strip()

def get_profile_fact_answer(query: str) -> str:
    """Analyze high-frequency questions about user details and return direct factual short-circuits."""
    profile = get_current_profile()
    if not profile:
        return ""
        
    q = query.lower().strip()
    
    # 1. Nickname
    if any(k in q for k in ("your name", "my nickname", "what is my name", "who am i", "who i am", "my name", "what do you call me")):
        details = profile.get("personal_details", {})
        nickname = details.get("primary_nickname", "") or details.get("full_name", "")
        if nickname:
            return f"Your name is **{nickname}** (registered in your profile as {details.get('full_name', '')})."
            
    # 2. Father details
    if "father" in q:
        family = profile.get("family_graph", {})
        father = family.get("father", {})
        name = father.get("name") or father.get("full_name")
        occ = father.get("occupation") or father.get("profession")
        if name:
            ans = f"Your father is **{name}**."
            if occ and not occ.startswith("["):
                ans += f" He works as a **{occ}**."
            return ans
            
    # 3. Mother details
    if "mother" in q:
        family = profile.get("family_graph", {})
        mother = family.get("mother", {})
        name = mother.get("name") or mother.get("full_name")
        occ = mother.get("occupation") or mother.get("profession")
        if name:
            ans = f"Your mother is **{name}**."
            if occ and not occ.startswith("["):
                ans += f" She is a **{occ}**."
            return ans
            
    # 4. Wife details
    if "wife" in q:
        family = profile.get("family_graph", {})
        wife = family.get("wife", {})
        name = wife.get("name") or wife.get("full_name")
        occ = wife.get("occupation") or wife.get("profession")
        if name:
            ans = f"Your wife is **{name}**."
            if occ and not occ.startswith("["):
                ans += f" She is a **{occ}**."
            return ans
            
    # 5. GST/PF/CSC details
    if any(k in q for k in ("gst", "pf", "csc", "pan", "tax", "consultancy", "business details")):
        business = profile.get("business_context", {})
        comp = business.get("company_name", "")
        gst = business.get("gst_number", "")
        pf = business.get("pf_registration", "")
        if comp:
            res = f"Your business consultancy is **{comp}**."
            if gst and not gst.startswith("["):
                res += f" GST ID: `{gst}`."
            if pf and not pf.startswith("["):
                res += f" PF Registration: `{pf}`."
            return res

    return ""

def is_action_status_query(query: str) -> bool:
    t = query.lower()
    return any(k in t for k in ("status of last", "last goal status", "current goal", "pending action", "pending goal", "status of goal"))

def search_profile(query: str, bypass_filter: bool = False) -> str:
    """Perform a local directory search on L1 (Personal Details/Business), L2 (Family Graph) and L3 (Legacy Memory) to retrieve specific context."""
    profile = get_current_profile()
    if not profile:
        return ""
    
    cleaned = clean_search_query(query)
    q = cleaned.lower().strip()
    
    personal_pronouns = {
        "myself", "who am i", "who i am", "my journey", "my background", "tell me about me", 
        "my profile", "my biography", "my bio", "who is talk", "who is speak",
        "user profile", "profile information", "gather user profile", "know about me",
        "about me", "personal details", "profile data"
    }
    is_general_profile = any(p in q for p in personal_pronouns)
    
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
            or len(q.split()) < 4
        )
        if not is_inquiry:
            return ""

    if is_general_profile:
        results = []
        details = profile.get("personal_details", {})
        if details:
            results.append("Personal Details:")
            for k, v in details.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
        business = profile.get("business_context", {})
        if business:
            results.append("Business Context:")
            for k, v in business.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")

        family = profile.get("family_graph", {})
        if family:
            results.append("Family structure:")
            for rel, d in family.items():
                if isinstance(d, dict):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in d.items() if v and not str(v).startswith("["))
                    if members:
                        results.append(f"  • {rel.replace('_', ' ').title()}: {members}")
                        
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
    stopwords = {
        "which", "year", "i", "passed", "grade", "ecam", "exam", "my", "me", "in", "on", 
        "at", "to", "for", "of", "who", "when", "what", "is", "was", "are", "do", "you", 
        "know", "tell", "show", "did", "does", "have", "has", "had", "a", "an", "the", "about",
        "hi", "hello", "hey", "yo"
    }
    search_words = [re.sub(r'[^a-zA-Z0-9]', '', w) for w in q.split()]
    search_words = [w for w in search_words if w not in stopwords and len(w) >= 1]
    
    def matches_word(text: str) -> bool:
        t_lower = text.lower()
        return any(re.search(r'\b' + re.escape(w) + r'\b', t_lower) for w in search_words)

    details = profile.get("personal_details", {})
    if details:
        details_clean = str(details).lower()
        if any(w in details_clean for w in search_words):
            results.append("Personal details found in profile:")
            for k, v in details.items():
                if matches_word(str(k)) or matches_word(str(v)):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
    business = profile.get("business_context", {})
    if business:
        bus_clean = str(business).lower()
        if any(w in bus_clean for w in search_words):
            results.append("Business context found in profile:")
            for k, v in business.items():
                if matches_word(str(k)) or matches_word(str(v)):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
    family = profile.get("family_graph", {})
    if family:
        fam_clean = str(family).lower()
        if any(w in fam_clean for w in search_words):
            results.append("Family links found in profile:")
            for relation, rel_data in family.items():
                if matches_word(relation):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in rel_data.items() if v and not str(v).startswith("["))
                    results.append(f"  • {relation.title()}: {members}")
                elif isinstance(rel_data, dict):
                    for k, v in rel_data.items():
                        if matches_word(str(k)) or matches_word(str(v)):
                            results.append(f"  • {relation.title()} - {k.replace('_', ' ').title()}: {v}")
                            
    journey = profile.get("mindset_and_journey", {})
    if journey:
        j_clean = str(journey).lower()
        if any(w in j_clean for w in search_words):
            results.append("Background history found in profile:")
            for cat, det in journey.items():
                if isinstance(det, dict):
                    for k, v in det.items():
                        if matches_word(str(k)) or matches_word(str(v)):
                            results.append(f"  • {cat.replace('_', ' ').title()} [{k.replace('_', ' ').title()}]: {v}")
                else:
                    if matches_word(cat) or matches_word(str(det)):
                        results.append(f"  • {cat.replace('_', ' ').title()}: {det}")

    edu_career = profile.get("education_and_career", {})
    if edu_career:
        for edu in edu_career.get("education", []):
            if matches_word(str(edu)):
                results.append(f"  • Education Record: {edu}")
        for emp in edu_career.get("employment_history", []):
            if matches_word(str(emp)):
                results.append(f"  • Employment Record: {emp}")
                
    if results:
        return "[Local User Profile Matches]\n" + "\n".join(results)
    return ""

def clean_search_query(query: str) -> str:
    """Strip Telegram commands and conversational greetings to produce a high-quality search query."""
    t = query.strip()
    
    # 1. Strip slash and exclamation commands
    if t.startswith("/") or t.startswith("!"):
        t = re.sub(r'^(?:/[a-zA-Z0-9_]+|![a-zA-Z0-9_]+)\s*', '', t)
        
    # 2. Strip direct conversational leading verb triggers (without destroying query words like 'post office', 'check bounce', etc.)
    t = re.sub(r'(?i)^\b(?:launch|sprint|walk|postnow|websearch|wikipedia)\b\s*', '', t)
    t = re.sub(r'(?i)^\b(?:search\s+(?:for|online\s+for)?|find\s+out\s+about|look\s+up)\b\s*', '', t)
    
    # 3. Strip conversational query suffixes/boilerplate
    t = re.sub(r'(?i)\b(?:please|plz|kindly|could you|can you|tell me|show me|details of|details for|status of)\b\s*', '', t)
    
    cleaned = t.strip()
    
    # Common OCR/transcription autocorrection hacks
    cleaned = re.sub(r'\b(?:bussinesses|bussinesses|businesses)\b', 'businesses', cleaned, flags=re.IGNORECASE)
        
    return cleaned if cleaned else query

def search_knowledge(query: str) -> str:
    cleaned = clean_search_query(query)
    q = cleaned.lower()
    hits = [v for k, v in KNOWLEDGE_BASE.items() if k in q or any(w in q for w in k.split())]
    return "\n".join(hits) if hits else ""

def wikipedia_search(query: str, max_results: int = 3) -> str:
    cleaned = clean_search_query(query)
    if not cleaned:
        return "No search query provided."
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

    cached = get_cached_search(cleaned)
    if cached:
        print(f"[SEARCH CACHE HIT] Reusing cached search results for: '{cleaned[:40]}'", flush=True)
        return cached["results"]
    
    ddg_text = ""
    tavily_key = os.environ.get("TAVILY_API_KEY")
    if tavily_key:
        print(f"[SEARCH] Querying Tavily Search API for: '{cleaned[:40]}'", flush=True)
        try:
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
            
    if not ddg_text:
        try:
            from ddgs import DDGS
            with DDGS() as ddgs:
                results = list(ddgs.text(cleaned, max_results=max_results))
            ddg_lines = []
            if results:
                for r in results:
                    href = r.get("href", "").lower()
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

    wiki_text = wikipedia_search(cleaned, max_results=2)

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
        
    if merged and "No web" not in merged_text:
        store_cached_search(cleaned, merged_text, "Wikipedia, DuckDuckGo")
        
    return merged_text

def retrieve_system_memory_via_sql(query: str) -> str:
    """Retrieve system memory context directly from database tables using SQL instead of RAG."""
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    context_parts = []
    q_lower = query.lower()
    
    if any(k in q_lower for k in ("who is", "profile", "identity", "about me", "preferences", "interest")):
        try:
            profile_text = get_user_profile_text("FULL")
            if profile_text:
                context_parts.append(f"=== K3 - USER IDENTITY & PROFILES ===\n{profile_text}")
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch profile: {e}", flush=True)

    if any(k in q_lower for k in ("goal", "query", "run", "request", "task list", "dag")):
        try:
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
            
    if any(k in q_lower for k in ("fail", "error", "reject", "violation", "why did", "problem", "warn")):
        try:
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
            
    if any(k in q_lower for k in ("timeline", "happen", "change", "unresolved", "recent", "chronology", "status", "history")):
        try:
            cursor.execute("""
                SELECT timestamp, event_category, summary, outcome
                FROM babu_temporal_timeline
                ORDER BY event_id DESC
                LIMIT 10
            """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K2 - SYSTEM TEMPORAL TIMELINE ===\n"
                for r in rows:
                    part += f"[{r[0]}] {r[1]}: {r[2]} -> {r[3] or 'COMPLETED'}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch timeline: {e}", flush=True)

    if any(k in q_lower for k in ("anti-pattern", "anti pattern", "rule", "rules", "policy", "policies")):
        try:
            cursor.execute("SELECT key, data FROM system_memory WHERE key LIKE '%anti%' OR key LIKE '%rule%' ORDER BY key LIMIT 10")
            rows = cursor.fetchall()
            if rows:
                part = "=== K4 - GOVERNANCE RULES & LESSONS ===\n"
                for r in rows:
                    part += f"{r[0]}: {r[1]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch system rules: {e}", flush=True)

    if any(k in q_lower for k in ("template", "templates", "etemp", "trusted")):
        try:
            cursor.execute("SELECT template_id, template_signature, status, execution_count, success_count FROM trusted_templates ORDER BY template_id LIMIT 10")
            rows = cursor.fetchall()
            if rows:
                part = "=== K4 - TRUSTED EXECUTION TEMPLATES ===\n"
                for r in rows:
                    part += f"{r[0]} | {r[1]} | {r[2]} | executions={r[3]} | successes={r[4]}\n"
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch templates: {e}", flush=True)

    if any(k in q_lower for k in ("adr", "architecture", "tradeoff", "postmortem", "lesson", "evolution", "upgrade", "milestone", "impact")):
        try:
            cursor.execute("""
                SELECT record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, status, timestamp
                FROM architecture_knowledge
                ORDER BY impact_score DESC, record_id ASC
                LIMIT 8
            """)
            rows = cursor.fetchall()
            if rows:
                part = "=== K5 - ARCHITECTURE KNOWLEDGE SYSTEM (AKS) ===\n"
                for r in rows:
                    part += (
                        f"[{r[0]}] {r[2]} ({r[3] or 'General'}) | Type: {r[1]} | Status: {r[10]} | Impact: {r[9]}\n"
                        f"- Problem: {r[4]}\n"
                        f"- Decision: {r[5]}\n"
                        f"- Reason: {r[6]}\n"
                        f"- Outcome: {r[7]}\n"
                        f"- Tradeoff: {r[8]}\n"
                        f"- Timestamp: {r[11]}\n"
                    )
                context_parts.append(part)
        except Exception as e:
            print(f"[SQL MEMORY ERROR] Failed to fetch architecture knowledge: {e}", flush=True)

    cursor.close()
    conn.close()
    return "\n\n".join(context_parts).strip()


def retrieve_k0_memory(session_id: str, limit: int = 5) -> str:
    from datetime import datetime
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


def db_save_pending_action(session_id: str, action_dict: dict):
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        key = f"pending_action:{session_id}"
        val = json.dumps(action_dict)
        if is_pg:
            cursor.execute("""
                INSERT INTO system_memory (key, data) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data
            """, (key, val))
        else:
            cursor.execute("INSERT OR REPLACE INTO system_memory (key, data) VALUES (?, ?)", (key, val))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] db_save_pending_action failed: {e}", flush=True)


def db_delete_pending_action(session_id: str):
    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        key = f"pending_action:{session_id}"
        if is_pg:
            cursor.execute("DELETE FROM system_memory WHERE key = %s", (key,))
        else:
            cursor.execute("DELETE FROM system_memory WHERE key = ?", (key,))
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] db_delete_pending_action failed: {e}", flush=True)
