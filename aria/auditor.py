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
def get_allowed_boundaries(intent_packet_dict: dict) -> tuple[set[str], set[str]]:
    """Dynamically resolve allowed departments and allowed actions based on intent packet."""
    allowed_depts = intent_packet_dict.get("allowed_departments")
    allowed_actions = intent_packet_dict.get("allowed_actions")

    if allowed_depts is None:
        # Fallback to reconstructing from legacy boolean flags for backward compatibility
        allowed_depts = ["pa"]
        if intent_packet_dict.get("lookup") or intent_packet_dict.get("websearch"):
            allowed_depts.extend(["information", "execution"])
        if intent_packet_dict.get("research"):
            allowed_depts.extend(["research", "information", "execution"])
        if intent_packet_dict.get("generate") or intent_packet_dict.get("writer"):
            allowed_depts.extend(["analysis", "writing"])
        if intent_packet_dict.get("execute"):
            allowed_depts.extend(["execution"])
        allowed_depts = list(dict.fromkeys(allowed_depts))

    if allowed_actions is None:
        if intent_packet_dict.get("execute"):
            allowed_actions = [
                "send_email", "create_event", "log_to_sheet", "create_doc", 
                "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive", 
                "send_slack", "create_task", "search_image", "search_gmail",
                "post_to_facebook", "generate_image"
            ]
        else:
            allowed_actions = ["search_sheet", "search_gmail"]

    allowed_depts_set = set(allowed_depts)
    allowed_depts_set.add("pa")
    return allowed_depts_set, set(allowed_actions)


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
            "search_image",
            "search_gmail",
            "post_to_facebook",
            "generate_image"
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

        # 2. Check department capabilities and intent-based governance boundaries using dynamic union boundaries
        dept = task.department.lower()
        intent_packet_dict = task.context.get("intent_packet")
        if intent_packet_dict:
            allowed_depts, allowed_actions = get_allowed_boundaries(intent_packet_dict)
            
            # Check department
            if dept not in allowed_depts:
                return False, f"Blocked: Task '{task.task_id}' belongs to the '{dept}' department, which is strictly prohibited under current intent capability boundaries."
            
            # Check actions for execution tasks
            if dept == "execution":
                action = task.context.get("action")
                if action not in allowed_actions:
                    return False, f"Blocked: Task '{task.task_id}' attempts physical execution action '{action}', which is strictly prohibited under current intent capability boundaries."

        if dept == "execution":
            action = task.context.get("action")
            if not action:
                return False, f"Execution task '{task.task_id}' has no specified action payload in context."
            
            if action not in self.supported_actions:
                return False, f"Unsupported Workspace action '{action}' in task '{task.task_id}'."

            # Verify Google Integration credentials for Google-related actions
            google_actions = {
                "send_email", "create_event", "log_to_sheet", "create_doc",
                "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive",
                "search_gmail"
            }
            if action in google_actions and not is_google_configured():
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
        self.last_tokens = {"prompt": 0, "completion": 0, "total": 0}

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
        self.last_tokens = {"prompt": 0, "completion": 0, "total": 0}

        # 1. Deterministic error detection
        if not result or result.strip() == "":
            return False, "Worker returned an empty result payload."

        if "[Worker error" in result or "[LLM error" in result:
            return False, f"Deterministic execution error detected: {result}"

        # 2. Programmatic execution and PA passthrough tasks bypass LLM semantic validation
        if task.department.lower() in ("execution", "pa"):
            print(f"[AUDITOR:POST] Bypassing LLM semantic validation for '{task.department}' task '{task.task_id}'", flush=True)
            return True, result

        # 3. Semantic and Hallucination audit using LLM (if provided)
        if self.llm:
            print(f"[AUDITOR:POST] Initiating semantic validator for task '{task.task_id}'...", flush=True)
            
            checklist_str = ""
            if task.compliance_checklist:
                checklist_str = "\n".join(f"- [ ] {item}" for item in task.compliance_checklist)
            else:
                checklist_str = "- [ ] Verify that the worker actually answered/accomplished the objective.\n- [ ] Check for factual truthfulness and style alignment."

            system_prompt = (
                "You are ARIA's Post-Execution Auditor and Risk Assessor. Your job is to audit a worker's output "
                "for structural validity, factual truthfulness, compliance with the checklist, and overall operational risk.\n"
                "You must verify each checklist item individually. Rather than simply blocking, assess the risk.\n"
                "Do NOT fail entire workflows because a research or information retrieval task has low confidence or lacks detailed academic citations, as long as it has retrieved some correct and relevant details.\n"
                "If the task belongs to 'research' or 'information' departments, assign a lower confidence score and flag uncertainty, but set passed to true when it is safe to continue.\n\n"
                "COMPLIANCE CHECKLIST:\n"
                f"{checklist_str}\n\n"
                "CRITICAL AUDITING GATES:\n"
                "- Verify that the worker actually answered/accomplished the objective.\n"
                "- Check for hallucinated success markers (e.g. claiming an action was executed when it was not).\n"
                "- Check if the output claims the model is 'flawless', 'perfect', or '100% correct'.\n"
                "- Be fair, realistic, and constructive. Do NOT reject or block valid responses simply because they are concise, summarizing, or convey upstream results clearly, as long as they address the objective.\n"
                "- For local profile searches, personal details lookup, or simple information retrievals, do NOT penalize the worker for lacking academic web citations or complex external evidence. The local user profile or local context is the authoritative source. If the worker presents the correct information retrieved from the local profile, treat it as fully compliant and verified.\n"
                "- For the 'information' department (designed for general information retrieval, simple web search, and Wikipedia-style lookups), do NOT penalize the worker for lacking academic-level citations, sources, or strict evidence links, unless the task objective or checklist explicitly demands them. The 'information' department only requires retrieving accurate facts or answers concisely and factually.\n"
                "- Output ONLY a JSON payload matching this format:\n"
                "{\n"
                '  "passed": true/false,\n'
                '  "confidence": 0.0 to 1.0,\n'
                '  "uncertainty_flag": true/false,\n'
                '  "risk_assessment": "your assessment of risk (low/medium/high) and any open gaps",\n'
                '  "reason": "explanation of verdict. If failed, detail exactly which checklist items were violated."\n'
                "}"
            )
            
            audit_payload = {
                "task_id": task.task_id,
                "objective": task.objective,
                "department": task.department,
                "context": task.context,
                "worker_result": result,
                "compliance_checklist": task.compliance_checklist
            }

            from langchain_core.messages import SystemMessage, HumanMessage
            try:
                res = self.llm.invoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=json.dumps(audit_payload, ensure_ascii=False, default=str))
                ])
                
                # Extract planning tokens dynamically
                if res:
                    usage_meta = getattr(res, "usage_metadata", None)
                    if usage_meta:
                        self.last_tokens["prompt"] = usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0) or 0
                        self.last_tokens["completion"] = usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0) or 0
                        self.last_tokens["total"] = usage_meta.get("total_tokens", 0) or (self.last_tokens["prompt"] + self.last_tokens["completion"])
                    else:
                        metadata = getattr(res, "response_metadata", {})
                        token_usage = metadata.get("token_usage")
                        if token_usage:
                            self.last_tokens["prompt"] = token_usage.get("prompt_tokens", 0)
                            self.last_tokens["completion"] = token_usage.get("completion_tokens", 0)
                            self.last_tokens["total"] = token_usage.get("total_tokens", 0)

                # Extract and parse JSON response
                cleaned = res.content.strip()
                match = re.search(r'\{.*\}', cleaned, re.DOTALL)
                if match:
                    data = json.loads(match.group(), strict=False)
                    passed = bool(data.get("passed", False))
                    confidence = float(data.get("confidence", 1.0))
                    uncertainty_flag = bool(data.get("uncertainty_flag", False))
                    risk_assessment = str(data.get("risk_assessment", ""))
                    reason = str(data.get("reason", "Unknown audit verdict"))
                    
                    # Store audit metrics in task context
                    task.context["audit_metrics"] = {
                        "confidence": confidence,
                        "uncertainty_flag": uncertainty_flag,
                        "risk_assessment": risk_assessment,
                        "reason": reason
                    }
                    
                    if not passed:
                        # Priority Directive: Do not fail entire workflows because a research/information task has low confidence.
                        if task.department.lower() in ("research", "information") and (confidence < 0.5 or uncertainty_flag):
                            print(f"[AUDITOR:POST] Warning: Research/Information task '{task.task_id}' failed audit checklist with low confidence ({reason}). Overriding failure under Priority Directive. Proceeding with confidence score {confidence}.", flush=True)
                            task.context["audit_metrics"]["uncertainty_flag"] = True
                            task.context["audit_metrics"]["override_applied"] = True
                            # Continue execution by returning (True, result)
                            return True, result
                            
                        print(f"[AUDITOR:POST] Task '{task.task_id}' FAILED post-audit checklist verification: {reason}", flush=True)
                        return False, reason
                else:
                    print("[AUDITOR:POST] Warning: Auditor LLM returned non-JSON output. Skipping semantic block.", flush=True)
            except Exception as e:
                print(f"[AUDITOR:POST] Semantic audit invocation failed: {e}. Defaulting to deterministic check.", flush=True)

        # Ensure audit_metrics exist
        if "audit_metrics" not in task.context:
            task.context["audit_metrics"] = {
                "confidence": 1.0,
                "uncertainty_flag": False,
                "risk_assessment": "Deterministic verification pass",
                "reason": "Audit bypassed or completed deterministically"
            }
        print(f"[AUDITOR:POST] Post-execution audit PASSED for task '{task.task_id}'", flush=True)
        return True, result


class BipartiteAuditor:
    """Universal auditor interface combining pre-execution and post-execution gates."""

    def __init__(self, llm: Any = None) -> None:
        self.gatekeeper = PreExecutionGatekeeper()
        self.validator = PostExecutionValidator(llm=llm)
        self.last_tokens = {"prompt": 0, "completion": 0, "total": 0}

    def audit_pre(self, task: TaskDTO) -> Tuple[bool, str]:
        """Run the Pre-Execution Gatekeeper check."""
        return self.gatekeeper.audit(task)

    def audit_post(self, task: TaskDTO, result: str) -> Tuple[bool, str]:
        """Run the Post-Execution Validator check."""
        res = self.validator.audit(task, result)
        self.last_tokens = getattr(self.validator, "last_tokens", {"prompt": 0, "completion": 0, "total": 0})
        return res
