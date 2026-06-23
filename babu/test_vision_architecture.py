from pathlib import Path

import pytest

from babu.awareness import AwarenessEngine, SERVICE_REGISTRY, ServiceStatus, inspect_services
from babu.bootstrap import BootstrapState, initialize_institution, open_transports
from babu.planner import classify_intent


def test_service_registry_declares_operational_contracts():
    assert {"telegram", "facebook", "email", "google_workspace", "scheduler", "web_search"} <= set(SERVICE_REGISTRY)
    for service in SERVICE_REGISTRY.values():
        assert service.capabilities
        assert service.failure_modes
        assert service.fallbacks


def test_awareness_reports_state_without_authorizing_or_executing():
    engine = AwarenessEngine((
        ServiceStatus("email", False, "OAuth unavailable"),
        ServiceStatus("scheduler", True, "available"),
    ))
    report = engine.create_report("Send monthly summary", constraints=("approval required",))
    assert report.available_services == ("scheduler",)
    assert report.unavailable_services == ("email",)
    assert "Service unavailable: email" in report.known_risks[0]
    assert report.constraints == ("approval required",)


def test_bootstrap_implements_constitutional_phase_order_without_transports():
    state = initialize_institution(initialize_storage=False)
    assert [phase.split(":", 1)[0] for phase in state.completed_phases] == [f"Phase {n}" for n in range(8)]
    assert state.documents["constitution"]
    assert state.documents["root_index"]
    assert state.startup_report is not None


def test_transport_gate_rejects_incomplete_bootstrap():
    with pytest.raises(RuntimeError, match="bootstrap is incomplete"):
        open_transports(BootstrapState(completed_phases=["Phase 0: Load Constitution"]))


def test_intent_governance_is_deterministic_when_providers_are_offline():
    lookup = classify_intent("Who is my mother and what are my family details?")
    assert lookup.lookup and not lookup.execute
    assert lookup.execution_mode == "READ_ONLY"
    assert lookup.confidence >= 0.65

    mutation = classify_intent("Draft and send an email to my mother summarizing tax reforms.")
    assert mutation.execute
    assert mutation.execution_mode == "APPROVAL_REQUIRED"

    scheduled = classify_intent("Run the daily scheduled tech compliance post to my Facebook Page.")
    assert scheduled.execute
    assert scheduled.execution_mode == "AUTO_EXECUTE"
