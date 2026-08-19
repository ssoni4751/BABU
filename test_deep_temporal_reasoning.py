import os
import sys
import pytest
from datetime import datetime

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(CURRENT_DIR, "babu"))

from temporal_reasoner import (
    get_current_ist_datetime,
    get_indian_compliance_calendar,
    get_causal_temporal_context,
    synthesize_temporal_reasoning_packet
)
from services import log_temporal_event
from bot import get_telemetry_data

def test_indian_compliance_calendar():
    """Verify Indian statutory compliance calendar and financial year calculations."""
    cal = get_indian_compliance_calendar()
    assert "current_date" in cal
    assert "financial_year" in cal
    assert "assessment_year" in cal
    assert "upcoming_monthly_deadlines" in cal
    
    # Verify statutory deadlines structure
    deadlines = cal["upcoming_monthly_deadlines"]
    for d in deadlines:
        assert "category" in d
        assert "due_date" in d
        assert "description" in d
        assert "days_remaining" in d
        assert d["days_remaining"] >= 0

def test_causal_temporal_reasoning_packet():
    """Verify causal context extraction from babu_temporal_timeline."""
    # 1. Log a causal event with cause, effect, resolution
    log_temporal_event(
        event_category="COMPLIANCE_AUDIT",
        summary="Audit of GST GSTR-3B filing cadence",
        outcome="RESOLVED",
        cause="Client delayed invoice submission past 15th",
        effect="GSTR-3B risk of late fee",
        resolution="Automated WhatsApp reminder on 12th of month",
        confidence=0.95,
        metadata={"taxpayer": "Anshu Consultancy"}
    )
    
    # 2. Retrieve causal context
    causal_events = get_causal_temporal_context("GST filing audit", lookback_days=14)
    assert len(causal_events) > 0
    latest = causal_events[0]
    assert latest["category"] == "COMPLIANCE_AUDIT"
    assert latest["cause"] is not None
    assert latest["resolution"] is not None

    # 3. Synthesize full reasoning packet
    packet = synthesize_temporal_reasoning_packet("What is the next GST and PF deadline and how did we resolve previous invoice delays?")
    assert "temporal_context_str" in packet
    assert packet["is_compliance_sensitive"] is True
    assert "Active Compliance Deadlines" in packet["temporal_context_str"] or "Temporal Anchor" in packet["temporal_context_str"]

def test_telemetry_data_aggregation():
    """Verify telemetry dashboard data extraction is non-empty, fast, and structured."""
    telemetry = get_telemetry_data(limit=50)
    assert "aggregates" in telemetry
    assert "model_matrix" in telemetry
    assert "temporal_timeline" in telemetry
    
    # Model matrix must report supported models
    assert len(telemetry["model_matrix"]) >= 8
    models = [m["model"] for m in telemetry["model_matrix"]]
    assert "gemini-2.5-pro" in models
    assert "llama-3.3-70b-versatile" in models
