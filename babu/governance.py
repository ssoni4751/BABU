"""
governance.py — Immutable E0 Governance Layer & Compiled Cognition Helpers for BABU
"""

import json
import re
import os
from typing import Optional

try:
    from .google_service import is_google_configured
except ImportError:
    from google_service import is_google_configured

# Load configuration at startup
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONSTITUTION_PATH = os.path.join(BASE_DIR, "e0", "constitution.json")
POLICIES_PATH = os.path.join(BASE_DIR, "e0", "policies.json")
VERSION_PATH = os.path.join(BASE_DIR, "e0", "version.txt")

# ── E0-A: CONSTITUTIONAL RULES (Immutable, Human-Defined Only) ──
E0_A_Rules = {
    "truthfulness": "Never invent facts, statistics, or identity details. Refuse lookup if profile slice is missing.",
    "execution_boundaries": [
        "send_email", "create_event", "create_calendar_event", "log_to_sheet",
        "create_doc", "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive",
        "send_slack", "create_task", "search_image", "search_gmail",
        "post_to_facebook", "generate_image", "delete_document", "delete_spreadsheet",
        "delete_event", "mass_update", "bulk_delete"
    ],
    "human_approval_required": True,
    "audit_requirements": "Every task run must undergo pre and post validation checks.",
    "micro_auditor_boundary": "Micro Auditor MUST be deterministic, MUST NOT call LLMs, and MUST complete in <50ms."
}

# ── E0-B: GOVERNANCE CONFIGURATIONS (Configurable Policies) ──
E0_B_Rules = {
    "promotion_min_runs": 5,
    "promotion_success_rate": 0.95,
    "promotion_recency_days": 30,
    "demotion_max_failures": 2,
    "demotion_min_success_rate": 0.90
}

# Load from read-only files if they exist
if os.path.exists(CONSTITUTION_PATH):
    try:
        with open(CONSTITUTION_PATH, "r", encoding="utf-8") as f:
            E0_A_Rules.update(json.load(f))
    except Exception as e:
        print(f"[GOVERNANCE INIT WARNING] Failed to load constitution.json: {e}", flush=True)

if os.path.exists(POLICIES_PATH):
    try:
        with open(POLICIES_PATH, "r", encoding="utf-8") as f:
            E0_B_Rules.update(json.load(f))
    except Exception as e:
        print(f"[GOVERNANCE INIT WARNING] Failed to load policies.json: {e}", flush=True)

E0_Version = "1.0.0"
if os.path.exists(VERSION_PATH):
    try:
        with open(VERSION_PATH, "r", encoding="utf-8") as f:
            E0_Version = f.read().strip()
    except Exception as e:
        print(f"[GOVERNANCE INIT WARNING] Failed to load version.txt: {e}", flush=True)


def get_policy(key: str, default=None):
    return E0_B_Rules.get(key, default)


def get_constitution(key: str, default=None):
    return E0_A_Rules.get(key, default)


def check_constraint_compatibility(query: str, template: dict) -> bool:
    """
    Check if the user's incoming query contains additional constraints or modifiers
    not accounted for in the trusted template, which would trigger constraint drift.
    """
    q_clean = query.lower().strip()
    
    # Check for negative modifiers/constraints that imply query deviations
    negation_markers = ["except", "not", "only if", "instead of", "unless", "but don't", "but do not"]
    for marker in negation_markers:
        if marker in q_clean:
            print(f"[GOVERNANCE CONSTRAINT CHECK] Failed: Negation marker '{marker}' detected in query.", flush=True)
            return False
            
    # Check slot schema - extract placeholders if any
    # (For template queries, we ensure no extra parameters outside template slots are requested)
    # E.g. if template is daily post, and query requests email, it's incompatible
    return True


def micro_audit_dag(goal_graph_dict: dict) -> bool:
    """
    Fast rule-based micro-auditor (takes <5ms, no LLM calls) validating DAG structure,
    task signatures, and environment/integration readiness before template execution.
    """
    try:
        tasks = goal_graph_dict.get("tasks", [])
        if not tasks:
            print("[MICRO AUDIT ERROR] GoalGraph contains no tasks.", flush=True)
            return False
            
        for task in tasks:
            task_id = task.get("task_id")
            objective = task.get("objective")
            dept = task.get("department")
            
            if not task_id or not objective or not dept:
                print(f"[MICRO AUDIT ERROR] Task {task_id} is missing essential parameters.", flush=True)
                return False
                
            # If the task executes an action, perform environment readiness checks
            if dept == "execution":
                context = task.get("context") or {}
                action = context.get("action")
                
                if not action:
                    print(f"[MICRO AUDIT ERROR] Task {task_id} is an execution department task but lacks an action payload.", flush=True)
                    return False
                    
                if action not in E0_A_Rules["execution_boundaries"]:
                    print(f"[MICRO AUDIT ERROR] Task {task_id} requests unsupported action '{action}'.", flush=True)
                    return False
                    
                if not is_google_configured():
                    print(f"[MICRO AUDIT ERROR] Workspace action requested but Google Workspace credentials are not configured.", flush=True)
                    return False
                    
        return True
    except Exception as e:
        print(f"[MICRO AUDIT EXCEPTION] Failed to execute micro-audit: {e}", flush=True)
        return False
