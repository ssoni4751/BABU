import os
import sys
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure minimal env for local test runs
if not os.environ.get("GROQ_API_KEY"):
    # If not present in env, set a mock key to avoid crashes during unit tests
    os.environ["GROQ_API_KEY"] = "MOCK_GROQ_API_KEY"

os.environ["GEMINI_API_KEY"] = os.environ.get("GROQ_API_KEY")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "MOCK_TELEGRAM_TOKEN")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.planner import classify_intent
from babu.gateway import get_babu_age_string, get_babu_self_context
from babu.bot import invoke_babu
from babu.graph import BabuState

def test_babu_age_calculation():
    """Verify that get_babu_age_string returns a valid age string."""
    age_str = get_babu_age_string()
    assert isinstance(age_str, str)
    assert len(age_str) > 0
    # From May 27, 2026 to June 16, 2026 is 20 days
    assert "day" in age_str or "month" in age_str or "year" in age_str

def test_babu_self_context():
    """Verify that get_babu_self_context returns correct structural details."""
    ctx_str = get_babu_self_context()
    assert "Project BABU" in ctx_str
    assert "May 27, 2026" in ctx_str
    assert "Bipartite Auditor" in ctx_str
    assert "LangGraph-based" in ctx_str

def test_classify_intent_system_query():
    """Verify that system queries are classified with the system_query=True flag."""
    # We bypass LLM invocation if it's rule-based chitchat under 15 characters,
    # but let's test queries with system keywords.
    packet = classify_intent("tell me what failures happened in last 5 days")
    assert packet.system_query is True

    packet_age = classify_intent("what is the date of birth of babu and how old are you")
    assert packet_age.system_query is True

def test_deterministic_short_circuits():
    """Verify that high-frequency system/FAQ queries short-circuit and consume 0 tokens."""
    # Test Time Query
    res_time, mode, tokens = invoke_babu("what is the current time in IST", session_id="test_system_session")
    assert "Indian Standard Time" in res_time
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0
    
    # Test Age Query
    res_age, mode, tokens = invoke_babu("how old are you", session_id="test_system_session")
    assert "Project BABU" in res_age
    assert "May 27, 2026" in res_age
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

    # Test Identity Query
    res_identity, mode, tokens = invoke_babu("who are you", session_id="test_system_session")
    assert "Behavioral Autonomous Bureaucratic Utility" in res_identity
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

    # Test Architecture Query
    res_arch, mode, tokens = invoke_babu("tell me about your architecture", session_id="test_system_session")
    assert "LangGraph-based" in res_arch
    assert "Bipartite Auditor" in res_arch
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

    # Test Failures Query
    res_fail, mode, tokens = invoke_babu("what are failures happened in last 5 days", session_id="test_system_session")
    assert "failures" in res_fail or "No system failures" in res_fail
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

def test_deterministic_health_and_upgrades_short_circuits():
    """Verify that system health dashboard and upgrades queries short-circuit and consume 0 tokens."""
    # Test Health Dashboard Query
    res_health, mode, tokens = invoke_babu("system health dashboard", session_id="test_system_session")
    assert "SYSTEM HEALTH & SELF-AUDIT DASHBOARD" in res_health
    assert "Identity" in res_health
    assert "Capabilities" in res_health
    assert "Health" in res_health
    assert "Telemetry" in res_health
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

    # Test Upgrades Query (with typo)
    res_upgrades, mode, tokens = invoke_babu("what upgrades did you recieve in last 20 days", session_id="test_system_session")
    assert "ARCHITECTURE KNOWLEDGE SYSTEM" in res_upgrades
    assert "Dynamic Imports" in res_upgrades
    assert "Runtime Index" in res_upgrades
    assert "Gemini Embeddings" in res_upgrades
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

def test_adr_database_directly():
    """Verify that architecture_knowledge database table exists and contains seed data."""
    from babu.bot import get_db_connection
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM architecture_knowledge")
        count = cursor.fetchone()[0]
        assert count >= 5
    finally:
        cursor.close()
        conn.close()

def test_classify_intent_adr_queries():
    """Verify that queries about tradeoffs or ADRs are classified as system queries."""
    packet = classify_intent("what are the architectural tradeoffs in BABU?")
    assert packet.system_query is True

def test_tradeoffs_query_short_circuit():
    """Verify that tradeoff queries return architectural tradeoffs with 0 tokens."""
    res_trade, mode, tokens = invoke_babu("what are the tradeoffs in BABU?", session_id="test_system_session")
    assert "Architectural Tradeoffs" in res_trade
    assert "Dynamic Imports" in res_trade
    assert tokens["prompt"] == 0

def test_highest_impact_query_short_circuit():
    """Verify that highest impact query returns the upgrade with the highest impact score."""
    res_impact, mode, tokens = invoke_babu("which upgrade had the highest impact?", session_id="test_system_session")
    assert "Highest Architectural Impact Upgrade" in res_impact
    assert "Runtime Index" in res_impact
    assert tokens["prompt"] == 0


def test_migration_safe_babu_adr_to_architecture_knowledge():
    """Verify that migration-safe logic copies babu_adr data and renames it to babu_adr_backup."""
    import sqlite3
    import tempfile
    
    # Create a temporary database with a legacy babu_adr table
    tmp_db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(tmp_db)
    cursor = conn.cursor()
    
    # Create legacy babu_adr table with matching columns
    cursor.execute("""
        CREATE TABLE babu_adr (
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
    # Insert legacy test data
    cursor.execute("""
        INSERT INTO babu_adr (record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp)
        VALUES ('LEGACY-001', 'ADR', 'Legacy Migration Test', 'Phase 0', 'Testing migration', 'Test decision', 'Test reason', 'Test outcome', 'Test tradeoff', 5, NULL, 'Active', '2026-01-01T00:00:00Z');
    """)
    conn.commit()
    
    # Now create architecture_knowledge table and run migration logic (same as init_durable_checkpoint_db)
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
    
    # Migration logic
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='babu_adr';")
    has_legacy = cursor.fetchone() is not None
    assert has_legacy, "Legacy table should exist before migration"
    
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
    conn.commit()
    
    # Verify: architecture_knowledge has migrated data
    cursor.execute("SELECT COUNT(*) FROM architecture_knowledge WHERE record_id = 'LEGACY-001'")
    assert cursor.fetchone()[0] == 1, "Legacy record should be migrated to architecture_knowledge"
    
    # Verify: babu_adr_backup exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='babu_adr_backup';")
    assert cursor.fetchone() is not None, "babu_adr_backup table should exist after migration"
    
    # Verify: babu_adr no longer exists
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='babu_adr';")
    assert cursor.fetchone() is None, "babu_adr table should no longer exist after migration"
    
    # Verify: backup still has data
    cursor.execute("SELECT COUNT(*) FROM babu_adr_backup WHERE record_id = 'LEGACY-001'")
    assert cursor.fetchone()[0] == 1, "Backup table should still contain original data"
    
    cursor.close()
    conn.close()
    os.unlink(tmp_db)


def test_k_class_headers_in_sql_retriever():
    """Verify that retrieve_system_memory_via_sql uses K-class headers."""
    from babu.bot import retrieve_system_memory_via_sql
    
    # Query that should trigger K5 architecture context
    result = retrieve_system_memory_via_sql("what are the architecture tradeoffs?")
    assert "K5 - ARCHITECTURE KNOWLEDGE SYSTEM (AKS)" in result
    assert "ADR-001" in result or "Dynamic Imports" in result


def test_no_drop_table_in_init():
    """Verify that init functions do NOT contain DROP TABLE for architecture tables."""
    import inspect
    from babu.bot import init_durable_checkpoint_db
    source = inspect.getsource(init_durable_checkpoint_db)
    assert "DROP TABLE IF EXISTS babu_adr" not in source, "init_durable_checkpoint_db must not drop babu_adr"
    assert "DROP TABLE IF EXISTS architecture_knowledge" not in source, "init_durable_checkpoint_db must not drop architecture_knowledge"


def test_k0_working_memory_lifecycle():
    """Verify the database schema, persistence, and retrieval of K0 Working Memory."""
    from babu.services import get_db_connection, retrieve_k0_memory
    from babu.bot import save_k0_memory_entry
    from babu.gateway import get_babu_self_context
    
    # 1. Verify table exists in local db
    conn, is_pg = get_db_connection()
    cursor = conn.cursor()
    try:
        if is_pg:
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_name = 'babu_k0_working_memory'")
        else:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='babu_k0_working_memory'")
        row = cursor.fetchone()
        assert row is not None, "babu_k0_working_memory table should exist in database"
    finally:
        cursor.close()
        conn.close()

    # 2. Save mock data
    test_session = "test_k0_session_123"
    save_k0_memory_entry(test_session, "mock_goal_id", "K0 working memory test query", "Mock successful response from BABU", {"goal_graph": {"status": "SUCCESS"}})
    
    # 3. Retrieve and assert
    k0_ctx = retrieve_k0_memory(test_session)
    assert "K0 - CONVERSATIONAL WORKING MEMORY" in k0_ctx
    assert "K0 working memory test query" in k0_ctx
    assert "Mock successful response from BABU" in k0_ctx

    # 4. Check integration with get_babu_self_context
    self_ctx = get_babu_self_context(test_session)
    assert "K0 - CONVERSATIONAL WORKING MEMORY" in self_ctx
    assert "Mock successful response from BABU" in self_ctx


def test_governance_template_semantic_compatibility():
    """Verify that check_constraint_compatibility catches topic and intent mismatches."""
    from babu.governance import check_constraint_compatibility
    import json

    # 1. Compatible template: same topic, same intent
    template_1 = {
        "goal_graph_json": json.dumps({"goal": "what is system health status", "tasks": []})
    }
    assert check_constraint_compatibility("show my system health status dashboard", template_1) is True

    # 2. Incompatible template: different topics
    template_2 = {
        "goal_graph_json": json.dumps({"goal": "who is the prime minister of india", "tasks": []})
    }
    assert check_constraint_compatibility("show my system health status dashboard", template_2) is False

    # 3. Incompatible template: same topic, different intent (analysis vs simple status)
    template_3 = {
        "goal_graph_json": json.dumps({"goal": "what is system health status", "tasks": []})
    }
    assert check_constraint_compatibility("analyse my system health status dashboard", template_3) is False


def test_department_scoped_context_injection():
    """Verify that dispatch automatically injects system health and profile slice in task context."""
    from babu.departments import InformationHead
    from babu.task_engine import TaskDTO, TaskState

    head = InformationHead()
    task = TaskDTO(
        task_id="T-TEST-123",
        objective="analyse system health status",
        department="information",
        depends_on=[],
        priority=1,
        state=TaskState.READY,
        context={"parent_goal": "analyse system health status and send report"},
        token_budget=1000,
        compliance_checklist=[]
    )
    
    original_run_worker = head._run_worker
    captured_scoped = {}
    
    def mock_run_worker(t, scoped_context, llm):
        nonlocal captured_scoped
        captured_scoped.update(scoped_context)
        return "mocked result", {"prompt": 0, "completion": 0, "total": 0}
        
    head._run_worker = mock_run_worker
    try:
        head.dispatch(task, {}, None)
    finally:
        head._run_worker = original_run_worker

    assert "system_health_dashboard" in captured_scoped
    assert "SYSTEM HEALTH & SELF-AUDIT DASHBOARD" in captured_scoped["system_health_dashboard"]



