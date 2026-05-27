import os
import sys

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Configure API Key for local test
if not os.environ.get("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY is required in environment for this test.")

# Add aria to python path so we can import from it
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

from aria.social_media import fetch_india_tech_trends, generate_daily_post
from aria.google_service import log_telemetry

def run_telemetry_tests():
    print("="*60)
    print("RUNNING ARIA COGNITIVE OS PHASE 3 INTEGRATION TESTS")
    print("="*60)
    
    # Test 1: Real-time India Tech Trend Scraping & Compression
    print("\n[TEST 1] Testing DuckDuckGo Scraping & Staged Compression of Tech Trends...")
    trends = fetch_india_tech_trends()
    print(f"\nDistilled Tech Trends Context:\n{trends}")
    assert len(trends) > 0, "Failed to scrape or compress India tech trends!"
    print("\n✅ TEST 1 PASSED: Real-time tech trends layer is operational.")
    
    # Test 2: Dynamic Copywriting Ingestion
    print("\n[TEST 2] Testing Dynamic Copywriting Ingestion via Groq...")
    caption, img_prompt, card_title, card_tips = generate_daily_post()
    print(f"\n✨ Dynamic Trend-Aware Caption:\n{caption}")
    print(f"\n🎨 Matching Image Prompt:\n{img_prompt}")
    assert len(caption) > 0, "Caption generation failed!"
    assert len(img_prompt) > 0, "Image prompt generation failed!"
    print("\n✅ TEST 2 PASSED: Tech trend copywriting integration is operational.")
    
    # Test 3: Master Telemetry Sheets Logging Failsafe
    print("\n[TEST 3] Testing Master Telemetry Sheets Logger Failsafe...")
    mock_tokens = {"prompt": 1200, "completion": 350, "total": 1550}
    
    # This should exit gracefully with informative message if credentials are not configured
    print("Attempting to log mock telemetry row...")
    success, message = log_telemetry(
        session_id="test_telemetry_session",
        gear="SPRINT",
        tokens=mock_tokens,
        success=True,
        query="Verify live telemetry logger capability"
    )
    
    print(f"Status: success={success}, message={message}")
    # Failsafe check: if not success, it should be due to missing google credentials/token
    if not success:
        assert "not configured" in message or "credentials.json not found" in message or "expired" in message, f"Unexpected telemetry failure message: {message}"
        print("ℹ️ INFO: Telemetry logger correctly handles unconfigured environments gracefully.")
    else:
        print("🚀 SUCCESS: Telemetry logged to Google Sheet successfully!")
        
    print("\n" + "="*60)
    print("ALL PHASE 3 INTEGRATION TESTS PASSED TRIUMPHANTLY!")
    print("="*60)

if __name__ == "__main__":
    run_telemetry_tests()
