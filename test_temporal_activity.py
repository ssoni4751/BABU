import os
import sys
import json
import pytest
from datetime import datetime, timezone, timedelta

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(CURRENT_DIR, "babu"))

from services import get_daily_activity_summary, log_temporal_event, log_execution_ledger_event, USER_PROFILE_PATH
from bot import invoke_babu

def test_dob_and_time_awareness():
    """Verify BABU knows current IST time, DOB (May 27, 2026), and age."""
    # 1. Current Time query
    res_time, mode, tokens = invoke_babu("what is the current time in IST", session_id="test_time_session")
    assert "Indian Standard Time" in res_time or "IST" in res_time
    assert str(datetime.now().year) in res_time
    
    # 2. Age / DOB query
    res_dob, mode, tokens = invoke_babu("what is your date of birth and age", session_id="test_dob_session")
    assert "May 27, 2026" in res_dob
    assert "active for" in res_dob

def test_daily_activity_summary_yesterday_and_today():
    """Verify temporal activity summary from SQLite for today and yesterday."""
    now_utc = datetime.now(timezone.utc)
    now_ist = now_utc + timedelta(hours=5, minutes=30)
    yesterday_ist = now_ist - timedelta(days=1)
    
    # 1. Insert mock yesterday activity into babu_temporal_timeline
    log_temporal_event(
        event_category="SOCIAL_POST",
        summary="Published Facebook Corporate Flyer on PF Claim KYC Assistance",
        outcome="SUCCESS",
        metadata={"category": "pf", "mock": True}
    )
    
    log_temporal_event(
        event_category="RESEARCH",
        summary="India Tax Year 2026 Compliance Updates & Due Dates",
        outcome="COMPLETED",
        metadata={"topic": "tax_calendar", "mock": True}
    )
    
    # 2. Fetch activity summary for yesterday
    summary_yest = get_daily_activity_summary(relative_days=-1)
    assert summary_yest is not None
    assert "executive_text" in summary_yest
    assert summary_yest["target_date"] == yesterday_ist.strftime("%Y-%m-%d")
    
    # 3. Test graph invoke for 'what did you do yesterday'
    res_yest, mode, tokens = invoke_babu("what did you do yesterday", session_id="test_yest_session")
    assert "Activity Summary" in res_yest
    assert "Current IST Reference Time" in res_yest

def test_user_profile_untouched_during_temporal_queries():
    """Verify user_profile.json is never modified during temporal activity queries."""
    with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
        profile_before = json.load(f)
        
    invoke_babu("what did you do yesterday", session_id="test_untouched_1")
    invoke_babu("kal kya kaam kiya tha", session_id="test_untouched_2")
    invoke_babu("what is your date of birth", session_id="test_untouched_3")
    
    with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
        profile_after = json.load(f)
        
    assert profile_before == profile_after, "user_profile.json was modified during temporal queries!"
