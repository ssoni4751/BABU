"""
auditor.py — ARIA Bipartite Auditor Protocol (Layer 5)

This module implements the bipartite auditing protocol, bifurcated into:
1. Pre-Execution Gatekeeper: Deterministic, rules-based capability, credential,
   and schema checks without LLM invocation.
2. Post-Execution Validator: Semantic analysis, hallucination detection,
   and verification against negative constraint rules (anti-patterns) using LLM.
"""

import json
import re
from typing import Any, Tuple

try:
    from .task_engine import TaskDTO
    from .memory import get_anti_pattern_rules
    from .google_service import is_google_configured
except ImportError:
    from task_engine import TaskDTO
    from memory import get_anti_pattern_rules
    from google_service import is_google_configured


class PreExecutionGatekeeper:
    """Deterministic, rules-based checks. Executes at Layer 5 before task dispatch."""

    def __init__(self) -> None:
        # Supported Google Workspace execution actions
        self.supported_actions = {
            "send_email",
            "create_event",
            "log_to_sheet",
            "create_doc",
            "search_sheet",
            "copy_photos_to_drive",
            "copy_contacts_to_drive",
            "send_slack",
            "create_task",
            "search_image"
        }

    def audit(self, task: TaskDTO) -> Tuple[bool, str]:
        """Perform pre-execution validation on a task.
        
        Parameters
        ----------
        task : TaskDTO
            The task awaiting dispatch.
            
        Returns
        -------
        Tuple[bool, str]
            (True, "") if validation passes.
            (False, "reason") if the task fails validation and must be BLOCKED.
        """
        # 1. Validate basic task structure
        if not task.task_id or not task.objective:
            return False, "Malformed task: missing task_id or objective."

        # 2. Check department capabilities
        dept = task.department.lower()
        if dept == "execution":
            action = task.context.get("action")
            if not action:
                return False, f"Execution task '{task.task_id}' has no specified action payload in context."
            
            if action not in self.supported_actions:
                return False, f"Unsupported Workspace action '{action}' in task '{task.task_id}'."

            # Verify Google Integration credentials
            if not is_google_configured():
                return False, f"Google Workspace credentials not configured. Cannot run execution action '{action}'."

        # 3. Check anti-pattern gates before wasting tokens
        anti_pattern_domain = f"department.{dept}"
        if dept == "execution":
            action = task.context.get("action", "")
            anti_pattern_domain = f"action.{action}"

        anti_patterns = get_anti_pattern_rules(anti_pattern_domain)
        if anti_patterns:
            blockers = ("expired", "invalid", "malformed", "bypassed", "quota")
            if any(word in anti_patterns.lower() for word in blockers):
                return False, f"Task blocked due to persistent historical failures in domain '{anti_pattern_domain}': {anti_patterns}"

        print(f"[AUDITOR:PRE] Pre-execution check PASSED for task '{task.task_id}' ({task.department.upper()})", flush=True)
        return True, ""


class PostExecutionValidator:
    """Semantic validation and hallucination checks. Executes after worker completes."""

    def __init__(self, llm: Any = None) -> None:
        self.llm = llm

    def audit(self, task: TaskDTO, result: str) -> Tuple[bool, str]:
        """Perform post-execution validation on the raw worker output.
        
        Parameters
        ----------
        task : TaskDTO
            The completed task.
        result : str
            The raw text returned by the worker/department head.
            
        Returns
        -------
        Tuple[bool, str]
            (True, validated_result) if the result passes validation.
            (False, "failure reason") if a hallucination or failure is detected.
        """
        # 1. Deterministic error detection
        if not result or result.strip() == "":
            return False, "Worker returned an empty result payload."

        if "[Worker error" in result or "[LLM error" in result:
            return False, f"Deterministic execution error detected: {result}"

        # 2. Semantic and Hallucination audit using LLM (if provided)
        if self.llm:
            print(f"[AUDITOR:POST] Initiating semantic validator for task '{task.task_id}'...", flush=True)
            
            system_prompt = (
                "You are ARIA's Post-Execution Auditor. Your ONLY job is to audit a worker's output "
                "for structural validity, factual truthfulness, and to detect any hallucinated success.\n\n"
                "CRITICAL GATES:\n"
                "- Verify that the worker actually answered/accomplished the objective.\n"
                "- Check for hallucinated success markers (e.g. claiming an action was executed when it was not).\n"
                "- Check if the output claims the model is 'flawless', 'perfect', or '100% correct' (unrealistic AI claims).\n"
                "- Output ONLY a JSON payload matching this format:\n"
                "{\n"
                '  "passed": true/false,\n'
                '  "reason": "explanation of fail or pass"\n'
                "}"
            )
            
            audit_payload = {
                "task_id": task.task_id,
                "objective": task.objective,
                "department": task.department,
                "context": task.context,
                "worker_result": result
            }

            from langchain_core.messages import SystemMessage, HumanMessage
            try:
                res = self.llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=json.dumps(audit_payload, ensure_ascii=False, default=str))
                ])
                
                # Extract and parse JSON response
                cleaned = res.content.strip()
                match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    passed = bool(data.get("passed", False))
                    reason = str(data.get("reason", "Unknown audit verdict"))
                    
                    if not passed:
                        print(f"[AUDITOR:POST] Task '{task.task_id}' FAILED post-audit: {reason}", flush=True)
                        return False, reason
                else:
                    print("[AUDITOR:POST] Warning: Auditor LLM returned non-JSON output. Skipping semantic block.", flush=True)
            except Exception as e:
                print(f"[AUDITOR:POST] Semantic audit invocation failed: {e}. Defaulting to deterministic check.", flush=True)

        print(f"[AUDITOR:POST] Post-execution audit PASSED for task '{task.task_id}'", flush=True)
        return True, result


class BipartiteAuditor:
    """Universal auditor interface combining pre-execution and post-execution gates."""

    def __init__(self, llm: Any = None) -> None:
        self.gatekeeper = PreExecutionGatekeeper()
        self.validator = PostExecutionValidator(llm=llm)

    def audit_pre(self, task: TaskDTO) -> Tuple[bool, str]:
        """Run the Pre-Execution Gatekeeper check."""
        return self.gatekeeper.audit(task)

    def audit_post(self, task: TaskDTO, result: str) -> Tuple[bool, str]:
        """Run the Post-Execution Validator check."""
        return self.validator.audit(task, result)
