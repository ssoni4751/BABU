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
from babu.bot import (
    get_babu_age_string,
    get_babu_self_context,
    invoke_babu,
    BabuState
)

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
    assert "decentralized LangGraph-based swarm" in res_arch
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
    assert "RECENT SYSTEM UPGRADES" in res_upgrades
    assert "Phase 1: Render Stability" in res_upgrades
    assert "Phase 2: Swarm Distillation" in res_upgrades
    assert "Phase 3: Real-time Telemetry" in res_upgrades
    assert tokens["prompt"] == 0
    assert tokens["completion"] == 0

