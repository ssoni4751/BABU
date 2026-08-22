"""
test_adr101_local_scenarios.py — 5 Comprehensive End-to-End Test Scenarios for ADR-101
"""

import time
import json
from planner import (
    classify_intent,
    DemandPacket,
    IntentPacket,
    derive_authorized_actions,
    DOMAIN_ACTIONS_REGISTRY,
    VALID_DOMAINS
)
from auditor import PreExecutionGatekeeper, get_allowed_boundaries, get_service_class
from task_engine import TaskDTO, TaskState


def run_scenario_1_chitchat():
    print("\n" + "="*70)
    print("🔹 SCENARIO 1: Chitchat / Greeting Fast-Track Gate (<0.05s bypass)")
    print("="*70)
    query = "Hi BABU, good morning! How are you?"
    
    t0 = time.perf_counter()
    packet = classify_intent(query)
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"Query: \"{query}\"")
    print(f"Latency: {elapsed:.2f} ms")
    print(f"Topology Mode: {packet.topology_mode}")
    print(f"Planning Required: {packet.planning_required}")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Confidence: {packet.confidence}")
    
    assert packet.topology_mode == "CHITCHAT", "Must be CHITCHAT"
    assert packet.planning_required is False, "Planning must NOT be required"
    assert packet.execution_shape == "CLASS_A", "Execution shape must be CLASS_A"
    assert elapsed < 50, f"Fast-track latency must be <50ms, got {elapsed:.2f}ms"
    print("✅ SCENARIO 1 PASSED: Fast-track chitchat responded instantly with 0 planner tokens.")


def run_scenario_2_class_a_business_lookup():
    print("\n" + "="*70)
    print("🔹 SCENARIO 2: Class A Pure LOOKUP in BUSINESS Domain (Zero Mutation)")
    print("="*70)
    query = "Check our latest Facebook comments from prospective clients"
    
    t0 = time.perf_counter()
    packet = classify_intent(query)
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"Query: \"{query}\"")
    print(f"Latency: {elapsed:.2f} ms")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Allowed Actions: {packet.allowed_actions}")
    
    assert "BUSINESS" in packet.demand_domains, "Must contain BUSINESS domain"
    assert packet.execution_shape == "CLASS_A", "Must be Class A (Read-only Lookup)"
    assert packet.risk_level == "LOW", "Risk level must be LOW"
    assert packet.approval_policy == "AUTO", "Read operations must have AUTO approval policy"
    assert "read_facebook_comments" in packet.allowed_actions, "Must allow read_facebook_comments"
    # Ensure no unauthorized mutating actions leaked
    assert "delete_document" not in packet.allowed_actions, "Must not allow delete_document"
    assert "send_email" not in packet.allowed_actions, "Must not allow send_email"
    print("✅ SCENARIO 2 PASSED: Pure Class A Business lookup resolved with strictly bounded read actions.")


def run_scenario_3_class_b_user_action():
    print("\n" + "="*70)
    print("🔹 SCENARIO 3: Class B LOOKUP + ACTION in USER Domain (Governed Mutation)")
    print("="*70)
    query = "Send email to client regarding meeting tomorrow"
    
    t0 = time.perf_counter()
    packet = classify_intent(query)
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"Query: \"{query}\"")
    print(f"Latency: {elapsed:.2f} ms")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Allowed Actions: {packet.allowed_actions}")
    
    assert "USER" in packet.demand_domains, "Must contain USER domain"
    assert packet.execution_shape in ("CLASS_B", "CLASS_C"), "Must be Class B (Lookup + Action)"
    assert packet.approval_policy == "APPROVAL_REQUIRED", "Operator email mutation must require approval"
    assert "send_email" in packet.allowed_actions, "Must allow send_email"
    print("✅ SCENARIO 3 PASSED: Governed Class B User mutation requires explicit approval.")


def run_scenario_4_class_c_destructive_double_confirm():
    print("\n" + "="*70)
    print("🔹 SCENARIO 4: High-Risk / Destructive Action with DOUBLE_CONFIRMATION Policy")
    print("="*70)
    
    packet = IntentPacket(
        lookup=True,
        execute=True,
        allowed_actions=["delete_document"],
        demand_domains={"USER"},
        query_category="PERSONAL_INFORMATION"
    )
    
    print(f"Simulated Intent: Delete Document")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Allowed Actions: {packet.allowed_actions}")
    
    assert packet.risk_level == "HIGH", "Destructive action must have HIGH risk level"
    assert packet.approval_policy == "DOUBLE_CONFIRMATION", "Destructive action must enforce DOUBLE_CONFIRMATION"
    print("✅ SCENARIO 4 PASSED: High-risk destructive action correctly demands 2-step confirmation.")


def run_scenario_5_gatekeeper_deterministic_cross_domain_block():
    print("\n" + "="*70)
    print("🔹 SCENARIO 5: Layer 5 Gatekeeper Deterministic Rejection of Cross-Domain Injection")
    print("="*70)
    
    gatekeeper = PreExecutionGatekeeper()
    
    # Adversarial / Mismatched intent packet: Query authorized ONLY for BUSINESS
    intent_dict = {
        "demand_domains": ["BUSINESS"],
        "allowed_departments": ["execution", "pa"],
        "candidate_actions": ["read_facebook_comments", "delete_document", "send_email"],
        "allowed_actions": ["read_facebook_comments"]
    }
    
    # Task attempts to execute delete_document on personal Google Drive
    injected_task = TaskDTO(
        task_id="task-adversarial-001",
        objective="Delete personal tax report document",
        department="execution",
        depends_on=[],
        priority=1,
        context={
            "action": "delete_document",
            "intent_packet": intent_dict
        }
    )
    
    print(f"Declared Demand Domains: {intent_dict['demand_domains']}")
    print(f"Task Attempting Action: '{injected_task.context['action']}'")
    
    ok, reason = gatekeeper.audit(injected_task)
    print(f"Gatekeeper Decision: {'PASSED' if ok else 'BLOCKED'}")
    print(f"Audit Verdict Reason: {reason}")
    
    assert ok is False, "Gatekeeper MUST reject cross-domain capability mismatch"
    assert "not authorized under declared demand domains" in reason, "Rejection must cite ADR-101 Domain Invariant"
    print("✅ SCENARIO 5 PASSED: Cross-domain capability leakage deterministically intercepted and blocked by Gatekeeper.")


if __name__ == "__main__":
    print("\n🚀 RUNNING 5 END-TO-END VERIFICATION SCENARIOS FOR ADR-101")
    run_scenario_1_chitchat()
    run_scenario_2_class_a_business_lookup()
    run_scenario_3_class_b_user_action()
    run_scenario_4_class_c_destructive_double_confirm()
    run_scenario_5_gatekeeper_deterministic_cross_domain_block()
    print("\n" + "="*70)
    print("🎉 ALL 5 ADR-101 LOCAL TEST SCENARIOS COMPLETED WITH 100% SUCCESS!")
    print("="*70 + "\n")
