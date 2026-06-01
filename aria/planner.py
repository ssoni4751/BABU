"""
planner.py — ARIA's Goal Decomposition Engine

Decomposes user goals into structured task DAGs (GoalGraph) using a single
LLM call. Supports three planning modes:

- plan_goal()        : Full LLM-based planning for SPRINT/LAUNCH gears
- build_walk_graph() : Trivial single-task graph for simple queries (no LLM)
- build_action_graph(): 2-task graph for detected actions (no LLM)

All plans produce a GoalGraph containing TaskDTO nodes with dependency edges.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

try:
    from .task_engine import TaskDTO, GoalGraph, TaskState, validate_dag
except ImportError:
    from task_engine import TaskDTO, GoalGraph, TaskState, validate_dag


# ---------------------------------------------------------------------------
# Department taxonomy
# ---------------------------------------------------------------------------

DEPARTMENTS: Dict[str, str] = {
    "research": "Web search, knowledge base lookup, profile search, data collection",
    "analysis": "Data analysis, sentiment analysis, comparison, pattern recognition",
    "writing": "Report generation, content creation, summarization, formatting",
    "execution": "Google Workspace actions (email, calendar, sheets, docs)",
    "pa": "Direct user response synthesis",
}

# ---------------------------------------------------------------------------
# Intent Governance Layer & Packet Structures
# ---------------------------------------------------------------------------

from dataclasses import dataclass

TEMPLATES: Dict[str, Dict[str, Any]] = {
    "LOOKUP": {
        "allowed_departments": {"research", "pa", "execution"},
        "allowed_actions": {"search_sheet"},
        "default_mode": "READ_ONLY"
    },
    "RESEARCH": {
        "allowed_departments": {"research", "analysis", "writing", "pa"},
        "allowed_actions": set(),
        "default_mode": "READ_ONLY"
    },
    "PUBLISH": {
        "allowed_departments": {"research", "analysis", "writing", "execution", "pa"},
        "allowed_actions": {"send_email", "send_slack", "create_doc", "log_to_sheet"},
        "default_mode": "APPROVAL_REQUIRED"
    },
    "EXECUTE": {
        "allowed_departments": {"execution", "pa", "research"},
        "allowed_actions": {"create_event", "log_to_sheet", "create_doc", "copy_photos_to_drive", "copy_contacts_to_drive", "create_task"},
        "default_mode": "APPROVAL_REQUIRED"
    }
}

@dataclass
class IntentPacket:
    """Represents a classified query intent with strict execution capability limits and execution modes."""
    lookup: bool = False
    research: bool = False
    generate: bool = False
    execute: bool = False
    execution_mode: str = "READ_ONLY"  # "READ_ONLY", "APPROVAL_REQUIRED", "AUTO_EXECUTE"
    confidence: float = 1.0
    workflow_template: str = "LOOKUP"  # "LOOKUP", "RESEARCH", "PUBLISH", "EXECUTE"

    def to_dict(self) -> dict:
        return {
            "lookup": self.lookup,
            "research": self.research,
            "generate": self.generate,
            "execute": self.execute,
            "execution_mode": self.execution_mode,
            "confidence": self.confidence,
            "workflow_template": self.workflow_template
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntentPacket":
        return cls(
            lookup=data.get("lookup", False),
            research=data.get("research", False),
            generate=data.get("generate", False),
            execute=data.get("execute", False),
            execution_mode=data.get("execution_mode", "READ_ONLY"),
            confidence=data.get("confidence", 1.0),
            workflow_template=data.get("workflow_template", "LOOKUP")
        )

INTENT_CLASSIFIER_SYSTEM_PROMPT: str = (
    "You are ARIA's Intent Classifier. Your ONLY job is to classify the user's "
    "conversational intent into a structured IntentPacket JSON.\n"
    "\n"
    "CAPABILITIES DEFINITION:\n"
    "- lookup: True if finding facts, searching the web, checking knowledge base, or gathering profile context is required.\n"
    "- research: True if deep information gathering, multiple source evaluation, or cross-referencing is required.\n"
    "- generate: True if data analysis, content creation, report drafting, comparison, or synthesis is required.\n"
    "- execute: True if a physical mutation action (sending emails, creating Google Docs/Events, logging to sheets, publishing posts) is explicitly requested.\n"
    "\n"
    "EXECUTION MODE DEFINITION:\n"
    "- READ_ONLY: The query is informational or research-based. No changes, drafts, or execution actions allowed.\n"
    "- APPROVAL_REQUIRED: User requested a mutation action (e.g. email, document creation, sheet logging) that requires user audit and approval before dispatch.\n"
    "- AUTO_EXECUTE: User requested a highly structured, scheduled, or automated background task (like daily marketing posts) that does not need explicit user approval.\n"
    "\n"
    "WORKFLOW TEMPLATE DEFINITION:\n"
    "- LOOKUP: Simple queries asking to look up family details, facts, contact info, chitchat, or simple sheet lookups.\n"
    "- RESEARCH: Deeper search queries, comparison, text synthesis, or content writing that does not involve physical mutation or external updates.\n"
    "- PUBLISH: Query explicitly requests to post, draft, or transmit updates to external channels (e.g. Facebook posts, Slack, or emails).\n"
    "- EXECUTE: Programmatic transactional operations such as scheduling events, logging spreadsheet entries, or document creation.\n"
    "\n"
    "CONFIDENCE RATING:\n"
    "Provide a rating between 0.0 and 1.0 representing how clear and unambiguous the user query is. If the query is vague, nonsensical, or lacks required context (e.g., 'Take care of this thing', 'do it', or 'test'), rate the confidence below 0.65.\n"
    "\n"
    "JSON SCHEMA:\n"
    "{\n"
    '  "lookup": true | false,\n'
    '  "research": true | false,\n'
    '  "generate": true | false,\n'
    '  "execute": true | false,\n'
    '  "execution_mode": "READ_ONLY | APPROVAL_REQUIRED | AUTO_EXECUTE",\n'
    '  "confidence": 0.0 to 1.0,\n'
    '  "workflow_template": "LOOKUP | RESEARCH | PUBLISH | EXECUTE"\n'
    "}\n"
    "\n"
    "CRITICAL: Output ONLY valid raw JSON. No explanation, no markdown fences."
)

def classify_intent(query: str, history_text: str = "", model_name: str = "llama-3.1-8b-instant") -> IntentPacket:
    """Classify user query intent into a structured IntentPacket."""
    t = query.lower().strip()
    
    # 1. Rule-based fast-track bypass for greetings, short chitchat, and stats commands
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 15:
        print("[INTENT CLASSIFIER] Fast-track classification: CHORE", flush=True)
        return IntentPacket(lookup=False, research=False, generate=False, execute=False, execution_mode="READ_ONLY", confidence=1.0, workflow_template="LOOKUP")

    # 2. LLM-based robust classification
    from langchain_core.messages import SystemMessage, HumanMessage
    try:
        try:
            from aria.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback

        # Dynamic loading and injection of governance classification negative constraints
        try:
            from memory import get_anti_pattern_rules
        except ImportError:
            from .memory import get_anti_pattern_rules
            
        gov_rules = get_anti_pattern_rules("governance.classification")
        system_prompt = INTENT_CLASSIFIER_SYSTEM_PROMPT
        if gov_rules:
            system_prompt += f"\n\n[CRITICAL HISTORICAL GOVERNANCE RULES]\n{gov_rules}"

        history_snippet = (history_text[:300] + "…") if len(history_text) > 300 else history_text
        user_content = f"User Query: {query}\n"
        if history_snippet:
            user_content += f"Recent History: {history_snippet}"

        response = invoke_with_fallback(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            model_name=model_name,
            temp=0.0,  # Highly deterministic
        )
        raw_text = response.content.strip()
        data = _extract_json(raw_text)
        packet = IntentPacket.from_dict(data)
        print(f"[INTENT CLASSIFIER] Classified: lookup={packet.lookup}, research={packet.research}, gen={packet.generate}, exec={packet.execute}, mode={packet.execution_mode}, template={packet.workflow_template}, conf={packet.confidence}", flush=True)
        return packet
    except Exception as e:
        print(f"[INTENT CLASSIFIER] Failed to classify intent: {e}. Defaulting to READ_ONLY fallback.", flush=True)
        return IntentPacket(lookup=False, research=False, generate=False, execute=False, execution_mode="READ_ONLY", confidence=0.5, workflow_template="LOOKUP")

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT: str = (
    "You are ARIA's Strategic Planner. Your ONLY job is to decompose user "
    "goals into structured task graphs.\n"
    "\n"
    "RULES:\n"
    "- Output ONLY valid JSON. No markdown, no explanation.\n"
    "- GOAL CORRECTIONS: If the user query is a correction, typo fix, or modification of a previous goal in the recent conversation history (e.g. 'I meant monitoring, not monetary' or 'correct the topic to X'), you must identify the corrected goal topic and plan the task DAG for the corrected goal, not the incorrect one.\n"
    "- INTENT & TEMPLATE CONSTRAINTS: The system has pre-classified the user's intent boundaries and selected a WORKFLOW TEMPLATE. You must strictly obey these constraints:\n"
    "  * If lookup is false and research is false, you must NOT create any 'research' tasks.\n"
    "  * If generate is false, you must NOT create any 'analysis' or 'writing' tasks.\n"
    "  * If execute is false, you are STRICTLY FORBIDDEN from creating any 'execution' department tasks (e.g. sending emails or creating docs). Creating unauthorized execution tasks is a critical safety violation.\n"
    "  * workflow_template: Read this setting carefully and obey its strict bounds:\n"
    "    - LOOKUP: Only allow 'research', 'pa', and read-only 'execution' tasks. Permitted actions: 'search_sheet'. No writing, analysis, or mutating execution tasks are allowed.\n"
    "    - RESEARCH: Only allow 'research', 'analysis', 'writing', and 'pa' tasks. NO 'execution' tasks are allowed under any circumstances.\n"
    "    - PUBLISH: Allow 'research', 'analysis', 'writing', 'execution', and 'pa' tasks. Permitted execution actions: 'send_email', 'send_slack', 'create_doc', 'log_to_sheet'.\n"
    "    - EXECUTE: Only allow 'execution', 'pa', and 'research' tasks. Permitted execution actions: 'create_event', 'log_to_sheet', 'create_doc', 'copy_photos_to_drive', 'copy_contacts_to_drive', 'create_task'.\n"
    "  * execution_mode: Read this setting carefully. If it is 'READ_ONLY', you must only plan read-only informational/research tasks and end with a 'pa' task; no draft or mutation actions are allowed. If it is 'APPROVAL_REQUIRED', you can create 'execution' tasks but they will go through an approval check. If it is 'AUTO_EXECUTE', you are allowed to plan automated background execution dispatches.\n"
    "- CORRECT TASK SEQUENCING: If the goal requires multiple sequential steps or multiple execution actions (e.g. first research X, then write a report, then create a Google Doc, and finally send an email), you must establish strict dependency links (depends_on) between these tasks to ensure they execute in the correct chronological order (e.g. writing depends on research, Doc creation depends on writing, and email sending depends on Doc creation). If there are multiple execution department tasks, chain them sequentially (T_execution_N depends on T_execution_N-1) to ensure the user audits and approves them in the correct sequence.\n"
    "- Each task must have: task_id (T1, T2, ...), objective, department, "
    "depends_on (list of task_ids), priority (1=highest), compliance_checklist (list of strings), and grant_profile_access (boolean).\n"
    "- grant_profile_access: Set to true ONLY for the single, specific 'research' task that requires access to the local user profile (family graph, business services, contact info) to fulfill the user's personal query. For all other tasks, this MUST be false. Do NOT grant profile access to multiple tasks to prevent token bloat and ensure security isolation.\n"
    "- compliance_checklist: A list of 2-3 specific, concrete criteria that the task's output must satisfy for the auditor to approve it (e.g., verifying specific factual items, formatting style, checking profile matches, or ensuring it is not a raw status message).\n"
    "  CRITICAL: For 'writing' tasks that synthesize upstream 'research' findings, you MUST always include a checklist item requiring that all research citations, source links, or references are explicitly preserved and listed at the end of the report.\n"
    "- Valid departments: research, analysis, writing, execution, pa\n"
    "- depends_on must reference existing task_ids only\n"
    "- Tasks with no dependencies get depends_on: []\n"
    "- The LAST task should synthesize/deliver the final result to the user and MUST belong to the 'pa' department.\n"
    "- CRITICAL: ONLY create an 'execution' department task if the user's request explicitly asks for a physical mutation action (e.g. sending an email, logging to sheets, creating a document, or scheduling a calendar event). Do NOT default to creating execution/action tasks for informational, question-answering, or research queries (e.g. 'get the weather update' or 'research cyber security trends'). Such requests should only use 'research', 'analysis', and 'writing' tasks, and end directly with a 'pa' task.\n"
    "- CRITICAL: If department is 'execution', you MUST specify 'action' and 'params' in that task's JSON object! You must dynamically select the most appropriate action from the list of valid actions based on the user's intent. Do not blindly default to 'send_email'.\n"
    "  Valid actions & parameters:\n"
    "    * send_email(to, subject, body) -- Use ONLY if user explicitly asked to send/mail an email.\n"
    "    * create_event(title, date, time, duration, description) -- Use ONLY if user explicitly asked to schedule/create a calendar event.\n"
    "    * log_to_sheet(sheet_name, data) -- Use ONLY if user explicitly asked to log or add data to a spreadsheet/sheet.\n"
    "    * create_doc(title, content) -- Use ONLY if user explicitly asked to write/create/draft a separate document file.\n"
    "    * search_sheet(sheet_name, query) -- Use ONLY if user explicitly asked to query/search/find information inside a spreadsheet/sheet.\n"
    "  In 'params', use the placeholder '[NEEDS_RESEARCH_CONTEXT]' for parameters that depend on upstream findings (e.g. content: '[NEEDS_RESEARCH_CONTEXT]' or body: '[NEEDS_RESEARCH_CONTEXT]').\n"
    "  CRITICAL: If a task (like send_email) is designed to transmit/report findings or content generated upstream, it MUST depend directly on the 'writing', 'analysis', or 'research' task that generated that content, NOT on intermediate execution tasks (like 'create_doc' or 'log_to_sheet') which only return a status confirmation message.\n"
    "- Keep tasks atomic — one clear objective each\n"
    "- Minimum 2 tasks for SPRINT, 3-6 for LAUNCH\n"
    "\n"
    "Output format:\n"
    "{\n"
    '  "goal": "brief goal description",\n'
    '  "tasks": [\n'
    '    {"task_id": "T1", "objective": "...", "department": "research", '
    '"depends_on": [], "priority": 1, "compliance_checklist": ["Verify search was done", "No empty results"], "grant_profile_access": true},\n'
    '    {"task_id": "T2", "objective": "...", "department": "pa", '
    '"depends_on": ["T1"], "priority": 2, "compliance_checklist": ["Verify findings are synthesized factually"], "grant_profile_access": false}\n'
    "  ]\n"
    "}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract a JSON object from LLM output, tolerating markdown fences."""
    cleaned = text.strip()

    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = cleaned.index("\n")
        cleaned = cleaned[first_newline + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    cleaned = cleaned.strip()
    return json.loads(cleaned)


def _build_fallback_graph(
    query: str,
    goal_id: Optional[str] = None,
    goal_type: str = "NEW",
    planner_status: str = "FALLBACK",
    intent_packet: Optional[dict] = None
) -> GoalGraph:
    """Return a single-task fail-closed graph refusing execution due to planning ambiguity."""
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    objective = "Inform the user that the request could not be planned securely because the query is too ambiguous, lacks required context, or violates system safety boundaries. Refuse autonomous execution and request clarity."
    checklist = ["State query ambiguity clearly", "Refuse execution factually"]

    if planner_status == "AMBIGUOUS_QUERY":
        objective = "Politely explain to the user that their query is too vague, ambiguous, or lacks necessary details to plan safely. Ask the user to clarify exactly what objective they want ARIA to achieve."
        checklist = ["Politely explain ambiguity", "Ask for specific clarification"]

    tasks = [
        TaskDTO(
            task_id="T1",
            objective=objective,
            department="pa",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            compliance_checklist=checklist,
        )
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        status="FAILED",
        created_at=now_iso,
        goal_type=goal_type,
        planner_status=planner_status,
        intent_packet=intent_packet,
    )


def get_allowed_boundaries(intent_packet_dict: dict) -> tuple[set[str], set[str]]:
    """Dynamically resolve allowed departments and allowed actions based on intent packet capabilities union."""
    allowed_depts = set()
    allowed_actions = set()

    # 1. Base template-level allowed boundaries if specified
    workflow_template = intent_packet_dict.get("workflow_template", "LOOKUP")
    if workflow_template and "TEMPLATES" in globals():
        tmpl = TEMPLATES.get(workflow_template, {})
        allowed_depts.update(tmpl.get("allowed_departments", set()))
        allowed_actions.update(tmpl.get("allowed_actions", set()))

    # 2. Dynamic capability-level union (prevents rigid halting on combined intents)
    if intent_packet_dict.get("lookup", False) or intent_packet_dict.get("research", False):
        allowed_depts.update({"research", "pa"})
        allowed_actions.update({"search_sheet"})

    if intent_packet_dict.get("generate", False):
        allowed_depts.update({"analysis", "writing", "pa"})

    if intent_packet_dict.get("execute", False):
        allowed_depts.update({"execution", "pa"})
        # Allow all execution actions if execute capability is explicitly enabled
        all_actions = {
            "send_email", "create_event", "log_to_sheet", "create_doc", 
            "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive", 
            "send_slack", "create_task", "search_image"
        }
        allowed_actions.update(all_actions)

    return allowed_depts, allowed_actions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_goal(
    query: str,
    gear: str,
    history_text: str = "",
    profile_text: str = "",
    model_name: str = "llama-3.1-8b-instant",
    goal_id: Optional[str] = None,
    is_correction: bool = False,
    last_goal_text: Optional[str] = None,
    intent_packet: Optional[IntentPacket] = None,
) -> GoalGraph:
    """Decompose *query* into a structured GoalGraph using a single LLM call.

    Parameters
    ----------
    query : str
        The user's natural-language goal.
    gear : str
        Planning gear — ``"SPRINT"`` or ``"LAUNCH"``.
    history_text : str, optional
        Recent conversation history (truncated to 500 chars internally).
    profile_text : str, optional
        User profile context to help the planner personalise tasks.
    model_name : str, optional
        Groq model identifier. Defaults to ``llama-3.1-8b-instant``.
    intent_packet : IntentPacket, optional
        The pre-classified intent boundaries.

    Returns
    -------
    GoalGraph
        A validated task DAG ready for dispatch.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    start = time.time()
    goal_type = "CORRECTION" if is_correction else "NEW"

    # Build the user prompt ------------------------------------------------
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    history_snippet = (history_text[:500] + "…") if len(history_text) > 500 else history_text

    user_content_parts: List[str] = [
        f"Current datetime: {now_utc}",
        f"Gear: {gear}",
        f"User query: {query}",
    ]
    if intent_packet:
        user_content_parts.append(
            f"Pre-classified Intent Boundaries:\n"
            f"- lookup: {intent_packet.lookup}\n"
            f"- research: {intent_packet.research}\n"
            f"- generate: {intent_packet.generate}\n"
            f"- execute: {intent_packet.execute}\n"
            f"- execution_mode: {intent_packet.execution_mode}\n"
            f"- workflow_template: {intent_packet.workflow_template}\n"
            f"- confidence: {intent_packet.confidence}"
        )
    if is_correction:
        user_content_parts.append("Goal correction mode: ACTIVE")
    if last_goal_text:
        user_content_parts.append(f"Last planned goal to correct: {last_goal_text}")
    if history_snippet:
        user_content_parts.append(f"Recent history: {history_snippet}")
    if profile_text:
        user_content_parts.append(f"User profile: {profile_text}")

    user_content = "\n".join(user_content_parts)

    target_model = model_name

    # Call LLM (with automatic provider failover on rate limits) ------------
    try:
        try:
            from aria.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback

        # Dynamic loading and injection of governance planning negative constraints
        try:
            from memory import get_anti_pattern_rules
        except ImportError:
            from .memory import get_anti_pattern_rules
            
        gov_planning_rules = get_anti_pattern_rules("governance.planning")
        system_prompt = PLANNER_SYSTEM_PROMPT
        if gov_planning_rules:
            system_prompt += f"\n\n[CRITICAL HISTORICAL GOVERNANCE RULES]\n{gov_planning_rules}"

        response = invoke_with_fallback(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            model_name=target_model,
            temp=0.1,
        )
        raw_text: str = response.content  # type: ignore[union-attr]
    except Exception as exc:
        print(f"[PLANNER] LLM call failed: {exc}")
        exc_str = str(exc).lower()
        if any(k in exc_str for k in ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")):
            p_status = "RATE_LIMIT"
        elif any(k in exc_str for k in ("timeout", "connection refused", "network", "socket", "dns", "unreachable")):
            p_status = "NETWORK"
        else:
            p_status = "PROVIDER_ERROR"
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status=p_status, intent_packet=intent_packet.to_dict() if intent_packet else None)

    # Parse JSON -----------------------------------------------------------
    try:
        data = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[PLANNER] JSON parse error: {exc}")
        print(f"[PLANNER] Raw LLM output: {raw_text[:300]}")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="JSON_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    # Validate & build TaskDTOs -------------------------------------------
    goal_text: str = data.get("goal", query[:200])
    raw_tasks: List[dict] = data.get("tasks", [])

    if not raw_tasks:
        print("[PLANNER] LLM returned empty task list — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    valid_dept_names = set(DEPARTMENTS.keys())
    tasks: List[TaskDTO] = []
    seen_ids: set = set()

    for t in raw_tasks:
        task_id: str = t.get("task_id", "")
        department: str = t.get("department", "")
        objective: str = t.get("objective", "")
        depends_on: List[str] = t.get("depends_on", [])
        priority: int = int(t.get("priority", len(tasks) + 1))
        compliance_checklist: List[str] = list(t.get("compliance_checklist", []))

        # Skip tasks with unknown departments
        if department not in valid_dept_names:
            print(f"[PLANNER] Skipping task {task_id}: unknown department '{department}'")
            continue

        # Filter depends_on to only reference known task_ids
        depends_on = [dep for dep in depends_on if dep in seen_ids]

        # Determine initial state
        state = TaskState.READY if not depends_on else TaskState.PENDING

        # Extract action, params, and grant_profile_access if present in planner JSON
        task_context = {}
        if "action" in t:
            task_context["action"] = t["action"]
            task_context["params"] = t.get("params", {})
        task_context["grant_profile_access"] = bool(t.get("grant_profile_access", False))
        task_context["intent_packet"] = intent_packet.to_dict() if intent_packet else None

        # Dynamic per-department token budget allocation
        dept_budgets = {
            "research": 4500,
            "writing": 3500,
            "analysis": 3000,
            "execution": 2000,
            "pa": 3000
        }
        token_budget = dept_budgets.get(department, 1500)

        tasks.append(
            TaskDTO(
                task_id=task_id,
                objective=objective,
                department=department,
                depends_on=depends_on,
                priority=priority,
                state=state,
                context=task_context,
                token_budget=token_budget,
                compliance_checklist=compliance_checklist,
            )
        )
        seen_ids.add(task_id)

    if not tasks:
        print("[PLANNER] All tasks filtered out — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    # ── Post-processing: Enforce template constraints programmatically ──
    if intent_packet:
        intent_dict = intent_packet.to_dict()
        allowed_depts, allowed_actions = get_allowed_boundaries(intent_dict)
        tmpl_name = intent_packet.workflow_template
        
        for t in tasks:
            # 1. Verify permitted department
            if t.department not in allowed_depts:
                print(f"[PLANNER] Programmatic Template Restriction: Task '{t.task_id}' uses unauthorized department '{t.department}' for template '{tmpl_name}'. Triggering fallback.", flush=True)
                return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="TEMPLATE_VIOLATION", intent_packet=intent_dict)
            
            # 2. Verify permitted actions for execution tasks
            if t.department == "execution":
                action = t.context.get("action")
                if action not in allowed_actions:
                    print(f"[PLANNER] Programmatic Template Restriction: Task '{t.task_id}' uses unauthorized execution action '{action}' for template '{tmpl_name}'. Triggering fallback.", flush=True)
                    return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="TEMPLATE_VIOLATION", intent_packet=intent_dict)

    # ── Post-processing: Enforce content dependencies for execution tasks ──
    content_task_ids = [t.task_id for t in tasks if t.department in ("writing", "analysis", "research")]
    if content_task_ids:
        preferred_dep = None
        for dept in ("writing", "analysis", "research"):
            matching = [t.task_id for t in tasks if t.department == dept]
            if matching:
                preferred_dep = matching[-1]
                break
        if preferred_dep:
            for t in tasks:
                if t.department == "execution":
                    if preferred_dep not in t.depends_on and t.task_id != preferred_dep:
                        print(f"[PLANNER] Post-processing: adding dependency {preferred_dep} to execution task {t.task_id} to ensure context propagation.", flush=True)
                        t.depends_on.append(preferred_dep)
                        # Re-evaluate state since dependencies have changed
                        t.state = TaskState.PENDING

    # Build GoalGraph ------------------------------------------------------
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()


    graph = GoalGraph(
        goal_id=goal_id,
        goal=goal_text,
        tasks=tasks,
        created_at=now_iso,
        goal_type=goal_type,
        planner_status="SUCCESS",
        intent_packet=intent_packet.to_dict() if intent_packet else None,
    )

    # Validate DAG (no cycles) ---------------------------------------------
    try:
        validate_dag(graph.tasks)
    except Exception as exc:
        print(f"[PLANNER] DAG validation failed: {exc} — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR")

    duration = round(time.time() - start, 2)
    print(f"[PLANNER] Generated {len(tasks)} tasks in {duration}s")
    return graph


def build_walk_graph(query: str, goal_id: Optional[str] = None) -> GoalGraph:
    """Return a trivial single-task GoalGraph for WALK-gear queries.

    No LLM call is made.  The sole task instructs the PA department to
    respond directly to the user.

    Parameters
    ----------
    query : str
        The user's query text.
    goal_id : Optional[str]
        Optional pre-generated goal ID.

    Returns
    -------
    GoalGraph
    """
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=[
            TaskDTO(
                task_id="T1",
                objective="Respond directly to user query",
                department="pa",
                depends_on=[],
                priority=1,
                state=TaskState.READY,
                token_budget=3000,
            )
        ],
        created_at=now_iso,
    )


def build_action_graph(query: str, detected_action: dict, goal_id: Optional[str] = None) -> GoalGraph:
    """Build a 2-task GoalGraph for WALK queries with a detected action.

    No LLM call is made.  Task T1 executes the action via the *execution*
    department; task T2 reports the result back to the user via *pa*.

    Parameters
    ----------
    query : str
        The user's query text.
    detected_action : dict
        Must contain ``"action"`` (str) and ``"params"`` (dict) keys
        describing the action to perform.
    goal_id : Optional[str]
        Optional pre-generated goal ID.

    Returns
    -------
    GoalGraph
    """
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    action_name: str = detected_action.get("action", "unknown_action")
    params: Dict[str, Any] = detected_action.get("params", {})

    tasks = [
        TaskDTO(
            task_id="T1",
            objective=f"Execute action: {action_name}",
            department="execution",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            context={"action": action_name, "params": params},
            token_budget=2000,
        ),
        TaskDTO(
            task_id="T2",
            objective="Report action result to user",
            department="pa",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
            token_budget=3000,
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        created_at=now_iso,
    )
