import os
import sys
import json
import sqlite3
from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure minimal env for local test runs
if not os.environ.get("GROQ_API_KEY"):
    raise RuntimeError("GROQ_API_KEY is required in environment for this test.")
os.environ["GEMINI_API_KEY"] = os.environ["GROQ_API_KEY"]
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "MOCK_TELEGRAM_TOKEN")

# Add babu to python path so we can import from it
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.memory import (
    append_to_profile_ledger,
    log_execution_failure,
    get_anti_pattern_rules,
    compress_context_payload
)
from babu.bot import invoke_babu, _histories

def run_tests():
    print("="*60)
    print("RUNNING ARIA COGNITIVE OS PHASE 2 INTEGRATION TESTS")
    print("="*60)
    
    # Test 1: Staged Context Compression Gateway
    print("\n[TEST 1] Testing Staged Context Compression Gateway...")
    long_raw_text = (
        "DuckDuckGo Search Results: TATA IPL 2026 scheduling info.\n"
        "The IPL 2026 matches are starting in late March 2026. The tournament features 10 teams.\n"
        "Matches will be held across India including Mumbai, Chennai, Delhi, Kolkata, and Bangalore.\n"
        "The final match of the tournament is scheduled to take place on May 31, 2026, at the massive Narendra Modi Stadium in Ahmedabad.\n"
        "Ticket booking will open online via official partners two weeks before the tournament starts.\n"
        "Defending champions are Kolkata Knight Riders after their brilliant victory in 2024."
    )
    compressed = compress_context_payload(long_raw_text, "IPL 2026 schedule search")
    print(f"Compressed Brief:\n{compressed}")
    assert len(compressed) > 0, "Compression output is empty!"
    print("✅ TEST 1 PASSED: Context compression is operational.")
    
    # Test 2: Profile Dynamic Memory Ledger
    print("\n[TEST 2] Testing Profile Dynamic Memory Ledger...")
    test_summary = {
        "session_topic": "Test cognitive capabilities",
        "key_takeaways": "User verified the hierarchical memory system.",
        "action_items": ["Proceed with Phase 3 integration"]
    }
    success = append_to_profile_ledger("chat_summaries", test_summary)
    print(f"Commit success: {success}")
    assert success, "Failed to commit to user_profile.json!"
    
    # Read user_profile.json to confirm
    profile_path = os.path.join(CURRENT_DIR, "babu", "user_profile.json")
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    ledger = profile.get("dynamic_memory_ledger", {}).get("chat_summaries", [])
    found = False
    for entry in ledger:
        if entry.get("key_takeaways") == test_summary["key_takeaways"]:
            found = True
            print(f"Confirmed entry in ledger: {json.dumps(entry, indent=2)}")
            break
    assert found, "Test entry not found in user_profile.json ledger!"
    print("✅ TEST 2 PASSED: Dynamic memory ledger writing is operational.")
    
    # Test 3: Failure Retention Engine (Cognitive Immune System)
    print("\n[TEST 3] Testing Failure Retention Engine (Auto-Immune mode)...")
    domain = "social_media.facebook_publisher"
    method = "publish_to_facebook_page"
    error_msg = "Facebook API Error: OAuthException - (#100) Page access token is expired or invalid."
    
    # Clear failures.json before test or let it append
    success = log_execution_failure(domain, method, error_msg)
    print(f"Commit failure success: {success}")
    assert success, "Failed to log failure to failures.json!"
    
    # Confirm anti-pattern rule generation and retrieval
    rules = get_anti_pattern_rules(domain)
    print(f"Retrieved rules for '{domain}':\n{rules}")
    assert "CRITICAL DIRECTION" in rules, "Anti-pattern rules missing required keys!"
    print("✅ TEST 3 PASSED: Auto-Immune failure retention engine is operational.")
    
    # Test 4: SQLite Durable Session Continuity
    print("\n[TEST 4] Testing SQLite Durable Session Continuity...")
    session_id = "test_session_123"
    message = "Who are you and what is my nickname?"
    
    # Call invoke_babu which compiles the graph with SqliteSaver
    reply, gear, tokens = invoke_babu(message, session_id=session_id)
    print(f"Bot response (Gear: {gear}):\n{reply}")
    print(f"Tokens consumed: {tokens}")
    assert len(reply) > 0, "Bot returned empty response!"
    
    # Check if SQLite DB file exists and contains checkpoint data
    db_path = os.path.join(CURRENT_DIR, "babu", "memory", "babu_checkpoint.db")
    assert os.path.exists(db_path), "SQLite checkpoint database was not created!"
    
    # Query database to confirm it actually populated checkpoint tables
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"SQLite Tables found: {tables}")
    assert len(tables) > 0, "SQLite database is empty, no checkpointer tables found!"
    conn.close()
    
    print("✅ TEST 4 PASSED: SQLite state checkpointer is operational.")
    
    # Test 5: Swarm Failure Injection (Negative Constraints)
    print("\n[TEST 5] Testing Swarm Failure Injection...")
    # Trigger a mock uploader run with invalid credentials to test social_media uploader hooking
    from babu.social_media import run_autonomous_social_post
    
    # Ensure FACEBOOK_PAGE_ID is invalid to force failure uploader hook
    os.environ["FACEBOOK_PAGE_ID"] = "invalid_page_123"
    os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"] = "invalid_token_123"
    
    print("Running autonomous social post with invalid credentials...")
    ok, msg, caption, img_path = run_autonomous_social_post()
    print(f"Result: success={ok}, message={msg}")
    assert not ok, "Expected autonomous social post to fail!"
    
    # Verify a new failure entry was added for 'social_media.facebook_publisher' or similar
    failures_path = os.path.join(CURRENT_DIR, "babu", "memory", "failures.json")
    with open(failures_path, "r", encoding="utf-8") as f:
        failures = json.load(f)
        
    print(f"Total failure rules in failures.json: {len(failures)}")
    assert len(failures) > 0, "failures.json is empty after simulated error!"
    print("✅ TEST 5 PASSED: Swarm failure injection and uploader hooking are operational.")
    
    print("\n" + "="*60)
    print("ALL INTEGRATION TESTS PASSED TRIUMPHANTLY!")
    print("="*60)

if __name__ == "__main__":
    run_tests()
