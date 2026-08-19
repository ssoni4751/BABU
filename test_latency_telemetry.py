import os
import sys
import json
import sqlite3
from datetime import datetime, timezone

# Add babu to python path so we can import from it
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(CURRENT_DIR, "babu"))

try:
    from services import get_db_connection, log_execution_ledger_event
    from bot import get_telemetry_data
except ImportError:
    from babu.services import get_db_connection, log_execution_ledger_event
    from babu.bot import get_telemetry_data

def test_latency_telemetry_calculation():
    print("="*60)
    print("TESTING LATENCY TELEMETRY CALCULATIONS")
    print("="*60)
    
    # 1. Clean / setup mock session events
    session_id = f"test_latency_session_{int(datetime.now(timezone.utc).timestamp())}"
    goal_id = f"G-TEST-{int(datetime.now(timezone.utc).timestamp())}"
    
    print(f"Logging mock events for session {session_id} and goal {goal_id}...")
    
    # Intent Classification (e.g. 350 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id=None,
        department=None,
        event_type="INTENT_CLASSIFICATION",
        metadata={
            "query": "Post a mock message",
            "model": "gemini-2.5-flash",
            "latency_ms": 350.0,
            "latency": 0.35
        }
    )
    
    # Planning (e.g. 1500 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id=None,
        department=None,
        event_type="PLANNING",
        metadata={
            "query": "Post a mock message",
            "model": "gemini-2.5-pro",
            "latency_ms": 1500.0,
            "latency": 1.5
        }
    )
    
    # Audit Pre (e.g. 200 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T1",
        department="writing",
        event_type="AUDIT_PRE_PASS",
        metadata={
            "objective": "Write a mock post",
            "model": "rules_engine",
            "latency_ms": 200.0,
            "latency": 0.2
        }
    )
    
    # Execution Worker (e.g. 3200 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T1",
        department="writing",
        event_type="EXECUTION_DONE",
        metadata={
            "objective": "Write a mock post",
            "model": "llama-3.3-70b-versatile",
            "latency_ms": 3200.0,
            "latency": 3.2,
            "tokens": {"prompt": 120, "completion": 80, "total": 200}
        }
    )
    
    # Audit Post (e.g. 400 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T1",
        department="writing",
        event_type="AUDIT_POST_PASS",
        metadata={
            "objective": "Write a mock post",
            "model": "gemini-2.5-flash",
            "latency_ms": 400.0,
            "latency": 0.4
        }
    )
    
    # PA Synthesis (e.g. 900 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T-PA",
        department="pa",
        event_type="PA_SYNTHESIS",
        metadata={
            "query": "Post a mock message",
            "model": "gemini-2.5-flash",
            "latency_ms": 900.0,
            "latency": 0.9
        }
    )
    
    # Goal Completed (overall duration: 6550 ms)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id=None,
        department=None,
        event_type="GOAL_COMPLETED",
        metadata={
            "event_start_time": datetime.now(timezone.utc).isoformat(),
            "event_end_time": datetime.now(timezone.utc).isoformat(),
            "latency_ms": 6550.0,
            "latency_sec": 6.55,
            "model": "gemini-2.5-pro",
            "tokens": {"prompt": 1000, "completion": 500, "total": 1500}
        }
    )
    
    # 2. Retrieve telemetry data
    print("Retrieving aggregated telemetry data...")
    data = get_telemetry_data(limit=10)
    
    assert "latency_metrics" in data, "latency_metrics key not found in get_telemetry_data result!"
    metrics = data["latency_metrics"]
    
    print("Validating derived metrics...")
    print(f"avg_latency_by_event: {metrics['avg_latency_by_event']}")
    print(f"avg_latency_by_model: {metrics['avg_latency_by_model']}")
    print(f"avg_latency_by_department: {metrics['avg_latency_by_department']}")
    print(f"avg_latency_by_workflow: {metrics['avg_latency_by_workflow']}")
    print(f"top_10_slowest_workflows: {len(metrics['top_10_slowest_workflows'])} workflows found")
    
    # Assert values
    assert metrics["avg_latency_by_event"].get("INTENT_CLASSIFICATION") > 0
    assert metrics["avg_latency_by_event"].get("PLANNING") > 0
    assert metrics["avg_latency_by_event"].get("AUDIT_PRE") > 0
    assert metrics["avg_latency_by_event"].get("EXECUTION") > 0
    assert metrics["avg_latency_by_event"].get("AUDIT_POST") > 0
    assert metrics["avg_latency_by_event"].get("PA_SYNTHESIS") > 0
    assert metrics["avg_latency_by_event"].get("GOAL_COMPLETE") > 0
    
    # Check model averaging
    assert "gemini-2.5-flash" in metrics["avg_latency_by_model"]
    assert "gemini-2.5-pro" in metrics["avg_latency_by_model"]
    assert "llama-3.3-70b-versatile" in metrics["avg_latency_by_model"]
    
    # Check department averages
    assert "writing" in metrics["avg_latency_by_department"]
    assert "pa" in metrics["avg_latency_by_department"]
    assert "governance" in metrics["avg_latency_by_department"]
    
    # Check specific dashboard averages
    assert metrics["planner_average_latency"] == metrics["avg_latency_by_event"]["PLANNING"]
    assert metrics["execution_average_latency"] == metrics["avg_latency_by_event"]["EXECUTION"]
    
    # Check slowest items
    assert len(metrics["top_10_slowest_workflows"]) > 0
    assert len(metrics["top_10_slowest_models"]) > 0
    
    print("\n✅ ALL LATENCY TELEMETRY CHECKS PASSED SUCCESSFULLY!")
    print("="*60)

if __name__ == "__main__":
    test_latency_telemetry_calculation()
