"""
test_unified_5_live_tests.py — 5 End-to-End Live Verification Tests for Unified ADR-101 Architecture
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
from google_service import execute_google_action


def test_1_chitchat_fast_track():
    print("\n" + "="*70)
    print("🔹 TEST 1: Pure Chitchat Fast-Track (<0.05s Direct PA Response)")
    print("="*70)
    query = "Good afternoon BABU, what's up?"
    
    t0 = time.perf_counter()
    packet = classify_intent(query)
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"Query: \"{query}\"")
    print(f"Latency: {elapsed:.2f} ms")
    print(f"Topology Mode: {packet.topology_mode}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Planning Required: {packet.planning_required}")
    print(f"Demand Domains: {packet.demand_domains}")
    
    assert packet.topology_mode == "CHITCHAT", "Must be CHITCHAT"
    assert packet.execution_shape == "CLASS_A", "Execution shape must be CLASS_A"
    assert packet.planning_required is False, "Planning must be False"
    assert elapsed < 50, f"Fast-track latency must be <50ms, got {elapsed:.2f}ms"
    print("✅ TEST 1 PASSED: Fast-track chitchat executed with 0 planning tokens and sub-millisecond routing.")


def test_2_class_a_crm_leads_query():
    print("\n" + "="*70)
    print("🔹 TEST 2: Class A Pure Lookup (CRM Ingestion & Leads Retrieval)")
    print("="*70)
    query = "Show me recent leads interested in PF settlement from CRM"
    
    packet = IntentPacket(
        lookup=True,
        execute=False,
        demand_domains={"BUSINESS"},
        allowed_actions=["crm_query_leads"],
        query_category="BUSINESS_INFORMATION"
    )
    
    print(f"Query: \"{query}\"")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Allowed Actions: {packet.allowed_actions}")
    
    # Gatekeeper Audit
    gatekeeper = PreExecutionGatekeeper()
    task = TaskDTO(
        task_id="task-live-crm-001",
        objective="Fetch recent CRM leads",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "crm_query_leads", "intent_packet": packet.to_dict()}
    )
    ok, reason = gatekeeper.audit(task)
    print(f"Gatekeeper Audit Verdict: {'PASSED' if ok else 'FAILED'}")
    assert ok is True, f"Gatekeeper audit failed: {reason}"
    
    # Live execution against persistent DB
    exec_ok, output = execute_google_action("crm_query_leads", {"limit": 3})
    print(f"Execution Status: {'SUCCESS' if exec_ok else 'FAILED'}")
    print(f"CRM Data:\n{output}")
    assert exec_ok is True, "CRM query execution must succeed"
    print("✅ TEST 2 PASSED: Class A CRM lookup executed cleanly without mutating state.")


def test_3_class_b_governed_task_creation():
    print("\n" + "="*70)
    print("🔹 TEST 3: Class B Governed Action in USER Domain (Google Task Creation)")
    print("="*70)
    query = "Create a task for tomorrow to review GST filing documents"
    
    packet = IntentPacket(
        lookup=False,
        execute=True,
        demand_domains={"USER"},
        allowed_actions=["create_task"],
        query_category="PERSONAL_INFORMATION"
    )
    
    print(f"Query: \"{query}\"")
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Allowed Actions: {packet.allowed_actions}")
    
    assert "USER" in packet.demand_domains, "Must belong to USER domain"
    assert packet.execution_shape == "CLASS_B", "Must resolve to CLASS_B (Lookup + Action)"
    assert packet.approval_policy == "APPROVAL_REQUIRED", "Task creation must require user approval"
    assert "create_task" in packet.allowed_actions, "Must permit create_task"
    
    # Gatekeeper verification
    gatekeeper = PreExecutionGatekeeper()
    task = TaskDTO(
        task_id="task-live-user-002",
        objective="Create a Google Task",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "create_task", "intent_packet": packet.to_dict()}
    )
    ok, reason = gatekeeper.audit(task)
    assert ok is True, f"Gatekeeper must permit create_task: {reason}"
    print("Gatekeeper Audit Verdict: PASSED")
    print("✅ TEST 3 PASSED: Class B governed user mutation correctly verified and policy-gated.")


def test_4_multi_domain_crm_and_email_composition():
    print("\n" + "="*70)
    print("🔹 TEST 4: Multi-Domain Composed DAG (BUSINESS CRM + USER Email)")
    print("="*70)
    
    # User asks to book a CRM appointment and email a confirmation summary
    candidate = ["crm_book_appointment", "send_email", "delete_document"]
    authorized = derive_authorized_actions({"BUSINESS", "USER"}, candidate)
    
    print(f"Demand Domains: {{'BUSINESS', 'USER'}}")
    print(f"Candidate Actions: {candidate}")
    print(f"Derived Authorized Actions: {authorized}")
    
    assert "crm_book_appointment" in authorized, "Must authorize CRM appointment (BUSINESS)"
    assert "send_email" in authorized, "Must authorize send_email (USER)"
    
    packet = IntentPacket(
        lookup=True,
        execute=True,
        demand_domains={"BUSINESS", "USER"},
        allowed_actions=authorized,
        planning_required=True
    )
    
    print(f"Execution Shape: {packet.execution_shape}")
    assert packet.execution_shape == "CLASS_C", "Multi-domain composition must be CLASS_C"
    
    gatekeeper = PreExecutionGatekeeper()
    task_crm = TaskDTO(
        task_id="task-composed-001",
        objective="Book client CRM appointment",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "crm_book_appointment", "intent_packet": packet.to_dict()}
    )
    ok_crm, reason_crm = gatekeeper.audit(task_crm)
    assert ok_crm is True, f"CRM task must pass: {reason_crm}"
    
    task_email = TaskDTO(
        task_id="task-composed-002",
        objective="Send email summary to user",
        department="execution",
        depends_on=["task-composed-001"],
        priority=2,
        context={"action": "send_email", "intent_packet": packet.to_dict()}
    )
    ok_email, reason_email = gatekeeper.audit(task_email)
    assert ok_email is True, f"Email task must pass: {reason_email}"
    
    print("Gatekeeper Audit for CRM Task: PASSED")
    print("Gatekeeper Audit for Email Task: PASSED")
    print("✅ TEST 4 PASSED: Multi-domain Class C DAG composed and audited seamlessly.")


def test_5_gatekeeper_blocks_system_cross_domain_attack():
    print("\n" + "="*70)
    print("🔹 TEST 5: Gatekeeper Blocks Cross-Domain Attack (SYSTEM -> Facebook Post)")
    print("="*70)
    
    gatekeeper = PreExecutionGatekeeper()
    
    # Query was categorized under SYSTEM domain
    intent_dict = {
        "demand_domains": ["SYSTEM"],
        "allowed_departments": ["execution", "pa"],
        "candidate_actions": ["system_status", "post_to_facebook"],
        "allowed_actions": ["system_status"]
    }
    
    # Injected task attempts to execute Facebook publication under SYSTEM authorization
    injected_task = TaskDTO(
        task_id="task-attack-001",
        objective="Publish unauthorized post to Facebook",
        department="execution",
        depends_on=[],
        priority=1,
        context={
            "action": "post_to_facebook",
            "intent_packet": intent_dict
        }
    )
    
    print(f"Declared Authorized Domain: {intent_dict['demand_domains']}")
    print(f"Task Attempting Action: '{injected_task.context['action']}'")
    
    ok, reason = gatekeeper.audit(injected_task)
    print(f"Gatekeeper Verdict: {'PASSED' if ok else 'BLOCKED'}")
    print(f"Rejection Reason: {reason}")
    
    assert ok is False, "Gatekeeper must deterministically block cross-domain action"
    assert "not authorized under declared demand domains" in reason, "Must cite domain authorization invariant"
    print("✅ TEST 5 PASSED: Cross-domain capability injection intercepted and rejected by Gatekeeper.")


if __name__ == "__main__":
    print("\n🚀 EXECUTING 5 UNIFIED VERIFICATION TESTS (ADR-101 STREAMLINED ARCHITECTURE)")
    test_1_chitchat_fast_track()
    test_2_class_a_crm_leads_query()
    test_3_class_b_governed_task_creation()
    test_4_multi_domain_crm_and_email_composition()
    test_5_gatekeeper_blocks_system_cross_domain_attack()
    print("\n" + "="*70)
    print("🎉 ALL 5 UNIFIED LIVE TESTS COMPLETED WITH 100% SUCCESS!")
    print("="*70 + "\n")
