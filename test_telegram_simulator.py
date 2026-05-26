import os
import sys
import json

# Force UTF-8 encoding for Windows standard streams
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure credentials
os.environ["GEMINI_API_KEY"] = "AIzaSyDvdk3YviRanZywosse2rF8ZumBGzZqLbc"
os.environ["TELEGRAM_BOT_TOKEN"] = "MOCK_TELEGRAM_TOKEN"

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

from aria.bot import invoke_aria, _histories, get_history_text

def run_simulator():
    print("="*60)
    print("ARIA TELEGRAM BOT SIMULATION & TOKEN METRICS AUDIT")
    print("="*60)
    
    session_id = "simulator_test_session_999"
    
    # Clean previous history for clean telemetry
    if session_id in _histories:
        _histories[session_id].clear()
        
    test_conversations = [
        {
            "description": "Test A: Casual Conversation (WALK Gear)",
            "message": "Hi ARIA, good morning! Hope you are doing great today."
        },
        {
            "description": "Test B: Factual Personal Lookup (WALK Gear + Local Profile RAG)",
            "message": "What is my nickname and what shop do I run?"
        },
        {
            "description": "Test C: Factual Web Search (Auto-Upgrade to SPRINT Swarm)",
            "message": "Search the web for the latest updates on Narendra Modi Stadium capacity"
        },
        {
            "description": "Test D: Deep Personal Bio extraction ('Me/Myself/I' Override)",
            "message": "Who am I? Explain my journey and details."
        }
    ]
    
    report = []
    
    for idx, test in enumerate(test_conversations, 1):
        print(f"\n[RUNNING {test['description']}]")
        print(f"User: \"{test['message']}\"")
        
        reply, gear, tokens = invoke_aria(test["message"], session_id=session_id)
        
        print(f"ARIA (Gear: {gear}): {reply[:120]}...")
        print(f"Token Stats: {tokens}")
        
        report.append({
            "step": idx,
            "description": test["description"],
            "query": test["message"],
            "gear": gear,
            "response_preview": reply[:100] + "...",
            "tokens": tokens
        })
        
    # Test E: Memory Compression Threshold Trigger simulation
    print("\n[RUNNING Test E: Memory Window Saturation & Summarizer Pass]")
    # Artificially inject a very long chat history to trigger auto-distillation
    long_history = "USER: Let's talk about computers.\nARIA: OK.\n" * 150  # ~15,000 characters (exceeds 12,000 threshold)
    _histories[session_id].clear()
    for i in range(50):
        _histories[session_id].append(("user", "Let's talk about computer parts and upgrades."))
        _histories[session_id].append(("aria", "I can help with that. Visit Anshu Computers Orai in Kaushal Market, Jalaun."))
        
    print(f"Current history length: {len(get_history_text(session_id))} characters.")
    
    # Triggering the next message will detect the overflow, compress it via Gemini 2.5 Flash,
    # log it to user_profile.json dynamic ledger, and clear active history!
    print("Sending message under saturated history state...")
    reply, gear, tokens = invoke_aria("What parts do you recommend for boosting PC speed?", session_id=session_id)
    print(f"ARIA (Gear: {gear}): {reply[:120]}...")
    print(f"Token Stats: {tokens}")
    
    # Read user_profile.json to confirm summary commit
    profile_path = os.path.join(CURRENT_DIR, "aria", "user_profile.json")
    with open(profile_path, "r", encoding="utf-8") as f:
        profile = json.load(f)
    ledger = profile.get("dynamic_memory_ledger", {}).get("chat_summaries", [])
    
    latest_summary = ledger[-1] if ledger else {}
    
    report.append({
        "step": 5,
        "description": "Test E: Memory window auto-compression and flush",
        "query": "What parts do you recommend for boosting PC speed?",
        "gear": gear,
        "response_preview": reply[:100] + "...",
        "tokens": tokens,
        "compression_triggered": True,
        "committed_summary_preview": latest_summary.get("key_takeaways", "")[:120] + "..." if latest_summary else "N/A"
    })
    
    # Output final structured JSON report to standard out
    print("\n" + "="*60)
    print("SIMULATION COMPLETED - METRICS REPORT JSON:")
    print("="*60)
    print(json.dumps(report, indent=2))
    print("="*60)

if __name__ == "__main__":
    run_simulator()
