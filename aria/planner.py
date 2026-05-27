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
    "- Each task must have: task_id (T1, T2, ...), objective, department, "
    "depends_on (list of task_ids), priority (1=highest)\n"
    "- Valid departments: research, analysis, writing, execution, pa\n"
    "- depends_on must reference existing task_ids only\n"
    "- Tasks with no dependencies get depends_on: []\n"
    "- The LAST task should synthesize/deliver the final result\n"
    "- For action requests (send email, create event, etc.), create an "
    "'execution' department task\n"
    "- Keep tasks atomic — one clear objective each\n"
    "- Minimum 2 tasks for SPRINT, 3-6 for LAUNCH\n"
    "\n"
    "Output format:\n"
    "{\n"
    '  "goal": "brief goal description",\n'
    '  "tasks": [\n'
    '    {"task_id": "T1", "objective": "...", "department": "...", '
    '"depends_on": [], "priority": 1},\n'
    "    ...\n"
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


def _build_fallback_graph(query: str) -> GoalGraph:
    """Return a minimal 2-task graph when LLM planning fails."""
    goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    tasks = [
        TaskDTO(
            task_id="T1",
            objective=f"Research information about: {query[:200]}",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
        ),
        TaskDTO(
            task_id="T2",
            objective="Synthesize research findings and respond to user",
            department="pa",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        created_at=now_iso,
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

    # Build the user prompt ------------------------------------------------
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    history_snippet = (history_text[:500] + "…") if len(history_text) > 500 else history_text

    user_content_parts: List[str] = [
        f"Current datetime: {now_utc}",
        f"Gear: {gear}",
        f"User query: {query}",
    ]
    if history_snippet:
        user_content_parts.append(f"Recent history: {history_snippet}")
    if profile_text:
        user_content_parts.append(f"User profile: {profile_text}")

    user_content = "\n".join(user_content_parts)

    target_model = model_name
    if "70b" in target_model:
        target_model = "llama-3.1-8b-instant"

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
        return _build_fallback_graph(query)

    # Parse JSON -----------------------------------------------------------
    try:
        data = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[PLANNER] JSON parse error: {exc}")
        print(f"[PLANNER] Raw LLM output: {raw_text[:300]}")
        return _build_fallback_graph(query)

    # Validate & build TaskDTOs -------------------------------------------
    goal_text: str = data.get("goal", query[:200])
    raw_tasks: List[dict] = data.get("tasks", [])

    if not raw_tasks:
        print("[PLANNER] LLM returned empty task list — using fallback")
        return _build_fallback_graph(query)

    valid_dept_names = set(DEPARTMENTS.keys())
    tasks: List[TaskDTO] = []
    seen_ids: set = set()

    for t in raw_tasks:
        task_id: str = t.get("task_id", "")
        department: str = t.get("department", "")
        objective: str = t.get("objective", "")
        depends_on: List[str] = t.get("depends_on", [])
        priority: int = int(t.get("priority", len(tasks) + 1))

        # Skip tasks with unknown departments
        if department not in valid_dept_names:
            print(f"[PLANNER] Skipping task {task_id}: unknown department '{department}'")
            continue

        # Filter depends_on to only reference known task_ids
        depends_on = [dep for dep in depends_on if dep in seen_ids]

        # Determine initial state
        state = TaskState.READY if not depends_on else TaskState.PENDING

        tasks.append(
            TaskDTO(
                task_id=task_id,
                objective=objective,
                department=department,
                depends_on=depends_on,
                priority=priority,
                state=state,
            )
        )
        seen_ids.add(task_id)

    if not tasks:
        print("[PLANNER] All tasks filtered out — using fallback")
        return _build_fallback_graph(query)

    # Build GoalGraph ------------------------------------------------------
    goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    graph = GoalGraph(
        goal_id=goal_id,
        goal=goal_text,
        tasks=tasks,
        created_at=now_iso,
    )

    # Validate DAG (no cycles) ---------------------------------------------
    try:
        validate_dag(graph.tasks)
    except Exception as exc:
        print(f"[PLANNER] DAG validation failed: {exc} — using fallback")
        return _build_fallback_graph(query)

    duration = round(time.time() - start, 2)
    print(f"[PLANNER] Generated {len(tasks)} tasks in {duration}s")
    return graph


def build_walk_graph(query: str) -> GoalGraph:
    """Return a trivial single-task GoalGraph for WALK-gear queries.

    No LLM call is made.  The sole task instructs the PA department to
    respond directly to the user.

    Parameters
    ----------
    query : str
        The user's query text.

    Returns
    -------
    GoalGraph
    """
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
            )
        ],
        created_at=now_iso,
    )


def build_action_graph(query: str, detected_action: dict) -> GoalGraph:
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

    Returns
    -------
    GoalGraph
    """
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
        ),
        TaskDTO(
            task_id="T2",
            objective="Report action result to user",
            department="pa",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        created_at=now_iso,
    )
