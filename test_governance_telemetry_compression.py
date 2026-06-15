import os
import sys
import json
import pytest
from datetime import datetime, timezone

# Add aria to path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

from aria.bot import get_db_connection, get_telemetry_data, log_execution_ledger_event
from aria.departments import get_department_head
from aria.task_engine import TaskDTO, TaskState

def test_telemetry_governance_counters():
    """Verify that get_telemetry_data aggregates constraint violations, rejections, and recovery logs correctly."""
    session_id = f"test_gov_session_{int(datetime.now(timezone.utc).timestamp())}"
    goal_id = f"G-TEST-GOV-{int(datetime.now(timezone.utc).timestamp())}"

    # Log a planner constraint violation event
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id=None,
        department=None,
        event_type="PLANNER_CONSTRAINT_VIOLATION",
        metadata={"reason": "unauthorized department"}
    )

    # Log governance rejections (pre and post)
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T1",
        department="writing",
        event_type="AUDIT_PRE_FAIL",
        metadata={"reason": "unsafe content"}
    )
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id="T2",
        department="writing",
        event_type="AUDIT_POST_FAIL",
        metadata={"reason": "invalid syntax"}
    )

    # Log a recovery registered event
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_id,
        task_id=None,
        department=None,
        event_type="RECOVERY_REGISTERED",
        metadata={"recovery": "fallback"}
    )

    # Query telemetry data
    data = get_telemetry_data(limit=10)

    assert "planner_constraint_violations" in data
    assert "governance_rejections" in data
    assert "recovery_invocations" in data

    # Since we added mock entries, these must be >= the ones we just added
    assert data["planner_constraint_violations"] >= 1
    assert data["governance_rejections"] >= 2
    assert data["recovery_invocations"] >= 1

    # Also check the values inside latency_metrics dict
    metrics = data["latency_metrics"]
    assert metrics["planner_constraint_violations"] >= 1
    assert metrics["governance_rejections"] >= 2
    assert metrics["recovery_invocations"] >= 1

def test_context_compression_methods():
    """Verify that different department heads compress result strings correctly for downstream tasks."""
    # 1. Base DepartmentHead compression
    base_head = get_department_head("pa") # pa inherits default behavior
    short_text = "Hello world"
    assert base_head.compress_result_for_downstream(short_text) == short_text

    long_text = "A" * 1500
    compressed_base = base_head.compress_result_for_downstream(long_text)
    assert len(compressed_base) <= 1205
    assert compressed_base.endswith(" ...")

    # 2. ResearchHead compression
    research_head = get_department_head("research")
    research_output = "Key findings:\n- Fact 1: Web is large.\n- Fact 2: AI is growing.\n- Link: https://example.com/ai\n" + "Very long narrative detail that is not bulleted or URL-like and should be truncated to keep research context tight." * 10
    compressed_res = research_head.compress_result_for_downstream(research_output)
    
    # Check that URLs and bullets are preserved, and overall length is capped
    assert "https://example.com/ai" in compressed_res
    assert "- Fact 1" in compressed_res
    assert len(compressed_res) <= 1050

    # 3. InformationHead compression
    info_head = get_department_head("information")
    info_output = "Info detail page content... " * 100
    compressed_info = info_head.compress_result_for_downstream(info_output)
    assert len(compressed_info) <= 850
    assert "truncated" in compressed_info

    # 4. AnalysisHead compression (Plain text)
    analysis_head = get_department_head("analysis")
    analysis_text = "Conclusion: X equals Y.\n- Pattern detected: ascending trend.\n" + "Irrelevant background calculations..." * 50
    compressed_analysis = analysis_head.compress_result_for_downstream(analysis_text)
    assert "Conclusion: X equals Y." in compressed_analysis
    assert "Pattern detected" in compressed_analysis
    assert len(compressed_analysis) <= 1050

    # 5. AnalysisHead compression (JSON minification)
    analysis_json = json.dumps({"summary": "high growth", "data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "details": "A" * 2000})
    compressed_json_str = analysis_head.compress_result_for_downstream(analysis_json)
    # It should minify it (no whitespaces) and truncate it
    assert "{" in compressed_json_str
    assert ":" in compressed_json_str
    assert len(compressed_json_str) <= 1200

    # 6. WritingHead compression
    writing_head = get_department_head("writing")
    writing_output = "Draft report copy. " * 300
    compressed_write = writing_head.compress_result_for_downstream(writing_output)
    assert len(compressed_write) <= 2050
    assert "truncated" in compressed_write
