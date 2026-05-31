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
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT: str = (
    "You are ARIA's Strategic Planner. Your ONLY job is to decompose user "
    "goals into structured task graphs.\n"
    "\n"
    "RULES:\n"
    "- Output ONLY valid JSON. No markdown, no explanation.\n"
    "- GOAL CORRECTIONS: If the user query is a correction, typo fix, or modification of a previous goal in the recent conversation history (e.g. 'I meant monitoring, not monetary' or 'correct the topic to X'), you must identify the corrected goal topic and plan the task DAG for the corrected goal, not the incorrect one.\n"
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


def _build_fallback_graph(query: str, goal_id: Optional[str] = None, goal_type: str = "NEW") -> GoalGraph:
    """Return a single-task fail-closed graph refusing execution due to planning ambiguity."""
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    tasks = [
        TaskDTO(
            task_id="T1",
            objective="Inform the user that the request could not be planned securely because the query is too ambiguous, lacks required context, or violates system safety boundaries. Refuse autonomous execution and request clarity.",
            department="pa",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            compliance_checklist=["State query ambiguity clearly", "Refuse execution factually"],
        )
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        status="FAILED",
        created_at=now_iso,
        goal_type=goal_type,
    )


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

    Returns
    -------
    GoalGraph
        A validated task DAG ready for dispatch.
    """
    from langchain_groq import ChatGroq
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

    # Call LLM -------------------------------------------------------------
    try:
        llm = ChatGroq(
            model=target_model,
            temperature=0.1,
            api_key=os.environ.get("GROQ_API_KEY", ""),
        )
        response = llm.invoke([
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])
        raw_text: str = response.content  # type: ignore[union-attr]
    except Exception as exc:
        print(f"[PLANNER] LLM call failed: {exc}")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type)

    # Parse JSON -----------------------------------------------------------
    try:
        data = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[PLANNER] JSON parse error: {exc}")
        print(f"[PLANNER] Raw LLM output: {raw_text[:300]}")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type)

    # Validate & build TaskDTOs -------------------------------------------
    goal_text: str = data.get("goal", query[:200])
    raw_tasks: List[dict] = data.get("tasks", [])

    if not raw_tasks:
        print("[PLANNER] LLM returned empty task list — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type)

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
        return _build_fallback_graph(query, goal_type=goal_type)

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
    )

    # Validate DAG (no cycles) ---------------------------------------------
    try:
        validate_dag(graph.tasks)
    except Exception as exc:
        print(f"[PLANNER] DAG validation failed: {exc} — using fallback")
        return _build_fallback_graph(query, goal_type=goal_type)

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
