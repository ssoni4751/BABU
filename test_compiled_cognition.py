import os
import json
import sqlite3
import pytest
from unittest.mock import MagicMock

# Save original env vars to prevent test pollution
_orig_groq = os.environ.get("GROQ_API_KEY")
_orig_tg = os.environ.get("TELEGRAM_BOT_TOKEN")

os.environ["TELEGRAM_BOT_TOKEN"] = "mock_token"
os.environ["GROQ_API_KEY"] = "mock_groq_key"

@pytest.fixture(scope="module", autouse=True)
def restore_env():
    yield
    if _orig_groq is not None:
        os.environ["GROQ_API_KEY"] = _orig_groq
    else:
        os.environ.pop("GROQ_API_KEY", None)
    if _orig_tg is not None:
        os.environ["TELEGRAM_BOT_TOKEN"] = _orig_tg
    else:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)

from babu import bot
from babu.governance import check_constraint_compatibility, micro_audit_dag, E0_B_Rules
from babu.task_engine import GoalGraph, TaskDTO, TaskState

def test_slot_compatibility():
    # Test queries with no negations pass compatibility checks
    assert check_constraint_compatibility("Write a blog post about AI", {}) is True
    assert check_constraint_compatibility("Summarize this paper", {}) is True

    # Test queries with negation markers fail compatibility checks
    assert check_constraint_compatibility("Write a report except for Section 2", {}) is False
    assert check_constraint_compatibility("Do not search sheets, just mail", {}) is False
    assert check_constraint_compatibility("Post unless it's rainy", {}) is False
    assert check_constraint_compatibility("Create document instead of email", {}) is False


def test_micro_auditor():
    # Create valid GoalGraph dictionary
    valid_graph = {
        "goal_id": "G-TEST-1",
        "goal": "Test goal",
        "tasks": [
            {
                "task_id": "T1",
                "objective": "Research something",
                "department": "research",
                "depends_on": [],
                "priority": 1
            },
            {
                "task_id": "T2",
                "objective": "Analyze data",
                "department": "analysis",
                "depends_on": ["T1"],
                "priority": 2
            }
        ]
    }
    assert micro_audit_dag(valid_graph) is True

    # Test missing essential parameters
    invalid_graph_1 = {
        "goal_id": "G-TEST-2",
        "tasks": [
            {
                "task_id": "T1",
                "objective": "",  # Empty objective
                "department": "research",
                "depends_on": []
            }
        ]
    }
    assert micro_audit_dag(invalid_graph_1) is False

    # Test execution task with invalid action boundary
    invalid_execution_graph = {
        "goal_id": "G-TEST-3",
        "tasks": [
            {
                "task_id": "T1",
                "objective": "Format files",
                "department": "execution",
                "depends_on": [],
                "priority": 1,
                "context": {"action": "delete_all_files"}  # Unsupported action
            }
        ]
    }
    assert micro_audit_dag(invalid_execution_graph) is False


def test_promotion_and_demotion_mechanics(monkeypatch):
    # Setup a clean temporary SQLite database for bot connections
    temp_db_path = os.path.join(os.path.dirname(__file__), "scratch", "temp_test_compiled_cognition.db")
    os.makedirs(os.path.dirname(temp_db_path), exist_ok=True)
    if os.path.exists(temp_db_path):
        try:
            os.remove(temp_db_path)
        except Exception:
            pass

    # Monkeypatch database path to use the clean temp db
    monkeypatch.setattr(bot, "DB_PATH", temp_db_path)
    
    # Initialize the temp DB
    conn = bot.init_durable_checkpoint_db()
    
    # Create test template signature
    sig = "research:analysis:pa"
    goal_graph = {
        "goal_id": "G-123",
        "goal": "research and analyze topic",
        "tasks": [
            {"task_id": "T1", "objective": "research topic", "department": "research", "depends_on": [], "priority": 1},
            {"task_id": "T2", "objective": "analyze findings", "department": "analysis", "depends_on": ["T1"], "priority": 2},
            {"task_id": "T3", "objective": "present results", "department": "pa", "depends_on": ["T2"], "priority": 3}
        ],
        "planner_status": "TEMPLATE_MATCH"
    }
    goal_graph_json = json.dumps(goal_graph)

    # Let's insert a template into trusted_templates in DEMOTED state or ACTIVE
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO trusted_templates (
            template_id, template_signature, goal_graph_json, status, promoted_from_goal_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("T-TEST", sig, goal_graph_json, "ACTIVE", "G-123", "2026-06-07T12:00:00Z")
    )
    conn.commit()
    cursor.close()

    # Define mock function for get_db_connection so bot.py uses temp_db
    def mock_get_db_connection():
        return sqlite3.connect(temp_db_path), False
        
    monkeypatch.setattr(bot, "get_db_connection", mock_get_db_connection)

    # Mock the LLM and invoke call behavior
    mock_brain = MagicMock()
    # Mocking output of brain execution
    mock_brain.invoke.return_value = {
        "messages": [MagicMock(content="Mocked response from ARIA")],
        "goal_graph": {
            "goal_id": "G-456",
            "goal": "research and analyze topic",
            "tasks": goal_graph["tasks"],
            "status": "FAILED", # Let's simulate a execution failure to test demotion
            "planner_status": "TEMPLATE_MATCH"
        },
        "tokens": {"prompt": 100, "completion": 50, "total": 150}
    }
    monkeypatch.setattr(bot, "babu_brain", mock_brain)

    # 1. Run once: template execution fails.
    # It should increment consecutive_failures to 1.
    bot.invoke_babu("research and analyze topic", session_id="test_sess_1", goal_id="G-456")
    
    # Check template metrics
    cursor = conn.cursor()
    cursor.execute("SELECT execution_count, success_count, consecutive_failures, status FROM trusted_templates WHERE template_signature = ?", (sig,))
    row = cursor.fetchone()
    assert row is not None
    # execution_count=1, success_count=0, consecutive_failures=1, status=ACTIVE
    assert row[0] == 1
    assert row[1] == 0
    assert row[2] == 1
    assert row[3] == "ACTIVE"
    cursor.close()

    # 2. Run twice: template execution fails again.
    # Demotion threshold = 2 consecutive failures. It should transition to DEMOTED.
    mock_brain.invoke.return_value["goal_graph"]["goal_id"] = "G-789"
    bot.invoke_babu("research and analyze topic", session_id="test_sess_1", goal_id="G-789")
    
    cursor = conn.cursor()
    cursor.execute("SELECT execution_count, success_count, consecutive_failures, status FROM trusted_templates WHERE template_signature = ?", (sig,))
    row = cursor.fetchone()
    assert row is not None
    # execution_count=2, success_count=0, consecutive_failures=2, status=DEMOTED
    assert row[0] == 2
    assert row[1] == 0
    assert row[2] == 2
    assert row[3] == "DEMOTED"
    cursor.close()

    # 3. Test Demoted template to RETIRED transition.
    # If a DEMOTED template fails again, it should move to RETIRED.
    mock_brain.invoke.return_value["goal_graph"]["goal_id"] = "G-101"
    bot.invoke_babu("research and analyze topic", session_id="test_sess_1", goal_id="G-101")
    
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM trusted_templates WHERE template_signature = ?", (sig,))
    row = cursor.fetchone()
    assert row[0] == "RETIRED"
    cursor.close()

    # Clean up temp db
    conn.close()
    if os.path.exists(temp_db_path):
        try:
            os.remove(temp_db_path)
        except Exception:
            pass
