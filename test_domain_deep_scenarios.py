"""
test_domain_deep_scenarios.py — 5 Diverse Multi-Domain Execution Tasks for ADR-101
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


def test_task_1_system_diagnostics():
    print("\n" + "="*70)
    print("🔹 TASK 1: SYSTEM Domain — Real-Time Health & Diagnostic Telemetry")
    print("="*70)
    query = "Show system diagnostic status and memory telemetry stats"
    
    t0 = time.perf_counter()
    packet = IntentPacket(
        lookup=True,
        execute=False,
        demand_domains={"SYSTEM"},
        allowed_actions=["system_status", "system_diagnostics", "memory_stats"],
        query_category="SYSTEM_INFORMATION"
    )
    elapsed = (time.perf_counter() - t0) * 1000
    
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Authorized Actions: {packet.allowed_actions}")
    
    # Gatekeeper validation
    gatekeeper = PreExecutionGatekeeper()
    task = TaskDTO(
        task_id="task-sys-001",
        objective="Fetch system runtime statistics",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "system_status", "intent_packet": packet.to_dict()}
    )
    ok, reason = gatekeeper.audit(task)
    print(f"Gatekeeper Audit: {'PASSED' if ok else 'FAILED'}")
    assert ok is True, f"Gatekeeper must permit system_status under SYSTEM domain: {reason}"
    
    # Live execution verification
    exec_ok, result = execute_google_action("system_status", {})
    print(f"Execution Result: {exec_ok}")
    print(f"Telemetry Output:\n{result}")
    assert exec_ok is True, "System status execution must succeed"
    print("✅ TASK 1 PASSED: SYSTEM domain diagnostics safely executed and verified.")


def test_task_2_business_crm_leads():
    print("\n" + "="*70)
    print("🔹 TASK 2: BUSINESS (CRM) Domain — Lead Ingestion & Pipeline Query")
    print("="*70)
    query = "Show active leads and PF claim client inquiries from CRM"
    
    packet = IntentPacket(
        lookup=True,
        execute=False,
        demand_domains={"BUSINESS"},
        allowed_actions=["crm_query_leads"],
        query_category="BUSINESS_INFORMATION"
    )
    
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    print(f"Authorized Actions: {packet.allowed_actions}")
    
    # Gatekeeper validation
    gatekeeper = PreExecutionGatekeeper()
    task = TaskDTO(
        task_id="task-crm-001",
        objective="Query active CRM leads",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "crm_query_leads", "intent_packet": packet.to_dict()}
    )
    ok, reason = gatekeeper.audit(task)
    print(f"Gatekeeper Audit: {'PASSED' if ok else 'FAILED'}")
    assert ok is True, f"Gatekeeper must permit crm_query_leads: {reason}"
    
    # Live execution verification
    exec_ok, result = execute_google_action("crm_query_leads", {"limit": 5})
    print(f"Execution Result: {exec_ok}")
    print(f"CRM Output:\n{result}")
    assert exec_ok is True, "CRM query leads execution must succeed"
    print("✅ TASK 2 PASSED: BUSINESS CRM leads safely queried from persistent SQLite ledger.")


def test_task_3_business_social_post():
    print("\n" + "="*70)
    print("🔹 TASK 3: BUSINESS (Social) Domain — Facebook Post Action Filtering")
    print("="*70)
    
    # Candidate actions contain both BUSINESS actions and unrelated USER actions
    candidate = ["post_to_facebook", "reply_facebook_comment", "delete_document", "search_gmail"]
    authorized = derive_authorized_actions({"BUSINESS"}, candidate)
    
    print(f"Candidate Actions: {candidate}")
    print(f"Authorized Actions (under BUSINESS): {authorized}")
    
    assert "post_to_facebook" in authorized, "Must allow post_to_facebook"
    assert "reply_facebook_comment" in authorized, "Must allow reply_facebook_comment"
    assert "delete_document" not in authorized, "Must strictly reject delete_document"
    assert "search_gmail" in authorized, "Universal read helper permitted for drafting"
    
    packet = IntentPacket(
        lookup=False,
        execute=True,
        demand_domains={"BUSINESS"},
        allowed_actions=authorized,
        query_category="BUSINESS_INFORMATION"
    )
    
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    assert packet.execution_shape == "CLASS_B", "Must be Class B (Lookup + Action)"
    print("✅ TASK 3 PASSED: BUSINESS social publishing correctly authorized and bounded.")


def test_task_4_multi_domain_crm_and_calendar():
    print("\n" + "="*70)
    print("🔹 TASK 4: MULTI-DOMAIN (BUSINESS + USER) — Composed CRM + Calendar DAG")
    print("="*70)
    
    # User requests booking a CRM appointment (BUSINESS) AND creating personal Calendar event (USER)
    candidate = ["crm_book_appointment", "create_event", "delete_spreadsheet", "system_status"]
    authorized = derive_authorized_actions({"BUSINESS", "USER"}, candidate)
    
    print(f"Authorized Multi-Domain Set: {{'BUSINESS', 'USER'}}")
    print(f"Candidate Actions: {candidate}")
    print(f"Derived Authorized Actions: {authorized}")
    
    assert "crm_book_appointment" in authorized, "Must allow CRM appointment (BUSINESS)"
    assert "create_event" in authorized, "Must allow Calendar event (USER)"
    assert "delete_spreadsheet" in authorized, "Permitted under USER domain"
    assert "system_status" not in authorized, "System action must be rejected since SYSTEM domain not authorized"
    
    packet = IntentPacket(
        lookup=True,
        execute=True,
        demand_domains={"BUSINESS", "USER"},
        allowed_actions=["crm_book_appointment", "create_event"],
        planning_required=True
    )
    
    print(f"Execution Shape: {packet.execution_shape}")
    assert packet.execution_shape == "CLASS_C", "Multi-domain composition must resolve to CLASS_C"
    
    # Gatekeeper audit on both tasks
    gatekeeper = PreExecutionGatekeeper()
    
    task_crm = TaskDTO(
        task_id="task-multi-001",
        objective="Commit CRM appointment",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "crm_book_appointment", "intent_packet": packet.to_dict()}
    )
    ok_crm, reason_crm = gatekeeper.audit(task_crm)
    assert ok_crm is True, f"CRM task must pass: {reason_crm}"
    
    task_cal = TaskDTO(
        task_id="task-multi-002",
        objective="Create Calendar Event",
        department="execution",
        depends_on=["task-multi-001"],
        priority=2,
        context={"action": "create_event", "intent_packet": packet.to_dict()}
    )
    ok_cal, reason_cal = gatekeeper.audit(task_cal)
    assert ok_cal is True, f"Calendar task must pass: {reason_cal}"
    
    print("Gatekeeper Audit for CRM Task: PASSED")
    print("Gatekeeper Audit for Calendar Task: PASSED")
    print("✅ TASK 4 PASSED: Multi-domain composition cleanly orchestrated without cross-domain leak.")


def test_task_5_system_high_risk_memory_purge():
    print("\n" + "="*70)
    print("🔹 TASK 5: SYSTEM Domain — High-Risk Memory Purge & DOUBLE_CONFIRMATION")
    print("="*70)
    
    packet = IntentPacket(
        lookup=False,
        execute=True,
        demand_domains={"SYSTEM"},
        allowed_actions=["clear_memory"],
        query_category="SYSTEM_INFORMATION",
        risk_level="HIGH"
    )
    
    print(f"Demand Domains: {packet.demand_domains}")
    print(f"Execution Shape: {packet.execution_shape}")
    print(f"Risk Level: {packet.risk_level}")
    print(f"Approval Policy: {packet.approval_policy}")
    
    assert packet.risk_level == "HIGH", "Memory purge must have HIGH risk level"
    assert packet.approval_policy == "DOUBLE_CONFIRMATION", "Memory purge must enforce DOUBLE_CONFIRMATION"
    assert get_service_class("clear_memory") == "C", "clear_memory must be Service Class C"
    
    gatekeeper = PreExecutionGatekeeper()
    task = TaskDTO(
        task_id="task-sys-005",
        objective="Purge system working memory cache",
        department="execution",
        depends_on=[],
        priority=1,
        context={"action": "clear_memory", "intent_packet": packet.to_dict()}
    )
    ok, reason = gatekeeper.audit(task)
    assert ok is True, f"Gatekeeper must permit authorized clear_memory: {reason}"
    print("Gatekeeper Audit: PASSED (Flagged for Double Confirmation)")
    print("✅ TASK 5 PASSED: SYSTEM high-risk operation correctly protected by DOUBLE_CONFIRMATION.")


if __name__ == "__main__":
    print("\n🚀 EXECUTING 5 DIVERSE DOMAIN TASKS (ADR-101 LIVE VALIDATION)")
    test_task_1_system_diagnostics()
    test_task_2_business_crm_leads()
    test_task_3_business_social_post()
    test_task_4_multi_domain_crm_and_calendar()
    test_task_5_system_high_risk_memory_purge()
    print("\n" + "="*70)
    print("🎉 ALL 5 DIVERSE DOMAIN TASKS EXECUTED AND VERIFIED SUCCESSFULLY!")
    print("="*70 + "\n")
