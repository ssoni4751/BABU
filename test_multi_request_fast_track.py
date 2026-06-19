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
    os.environ["GROQ_API_KEY"] = "MOCK_GROQ_API_KEY"

os.environ["GEMINI_API_KEY"] = os.environ.get("GROQ_API_KEY")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "MOCK_TELEGRAM_TOKEN")

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.bot import (
    has_multiple_tasks_or_requests,
    route_after_router,
    BabuState
)
from babu.planner import classify_intent

def test_multi_request_detection():
    """Verify that has_multiple_tasks_or_requests correctly flags queries with multiple distinct tasks."""
    # 1. Multi-request queries with coordinate conjunctions and distinct action/FAQ keywords
    assert has_multiple_tasks_or_requests("what is the current time in IST and send an email to user") is True
    assert has_multiple_tasks_or_requests("how old are you and tell me about your architecture") is True
    assert has_multiple_tasks_or_requests("what upgrades did you receive and also show health status") is True
    assert has_multiple_tasks_or_requests("tell me about yourself; what time is it?") is True
    assert has_multiple_tasks_or_requests("system health dashboard and upgrades") is True
    
    # 2. Multi-request queries based on intent packet allowed actions / departments
    packet_dict_multi = {
        "allowed_departments": ["execution", "information"],
        "allowed_actions": ["send_email"]
    }
    assert has_multiple_tasks_or_requests("do this and do that", packet_dict_multi) is True

    packet_dict_actions = {
        "allowed_departments": ["execution"],
        "allowed_actions": ["send_email", "send_slack"]
    }
    assert has_multiple_tasks_or_requests("email this and slack that", packet_dict_actions) is True

    # 3. Single-request queries (should NOT be flagged)
    assert has_multiple_tasks_or_requests("what is the current time in IST") is False
    assert has_multiple_tasks_or_requests("how old are you") is False
    assert has_multiple_tasks_or_requests("who are you") is False
    assert has_multiple_tasks_or_requests("system health") is False
    assert has_multiple_tasks_or_requests("what upgrades did you receive in last 20 days") is False
    assert has_multiple_tasks_or_requests("tell me about your architecture") is False
    assert has_multiple_tasks_or_requests("hello") is False

def test_multi_request_routes_to_plan():
    """Verify that routing for multi-request queries avoids the PA short-circuit and goes to plan node."""
    query = "what is the current time in IST and send an email to ssoni4751@gmail.com"
    intent_packet = classify_intent(query)
    
    state = BabuState(
        messages=[],
        query=query,
        routing_metadata={"intent_packet": intent_packet.to_dict()},
        goal_graph=None,
        active_department=None,
        task_execution_ledger={},
        dynamic_context="",
        audit_trail=[],
        promoted_slots=[],
        demoted_slots=[],
        confidence_clarification=None,
        tokens={"prompt": 0, "completion": 0, "total": 0},
        is_deterministic_response=False,
        pending_action_notice=""
    )
    
    next_node = route_after_router(state)
    assert next_node == "plan", f"Expected to route to 'plan' for multi-request query, but got '{next_node}'"
