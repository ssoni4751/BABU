"""
task_engine.py — ARIA's Execution Kernel

This module is the core of ARIA's orchestration framework. It models work as
a directed acyclic graph (DAG) of TaskDTO objects, each carrying a narrow
objective, a department assignment, dependency edges, priority, and a scoped
token budget.

Key components
--------------
* **TaskState**  – 10-state lifecycle enum for every work unit.
* **TaskDTO**    – Immutable-ish dataclass representing a single task.
* **GoalGraph**  – Container that holds the full DAG plus goal-level metadata.
* **TaskEngine** – Stateful DAG scheduler: marks tasks READY when deps clear,
                   handles retries, cascading blocks, and completion checks.
* **validate_dag** – Kahn's-algorithm cycle detector; raises on invalid graphs.
"""

from __future__ import annotations

import enum
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# TaskState enum
# ---------------------------------------------------------------------------

class TaskState(enum.Enum):
    """Lifecycle states for a single task inside a GoalGraph."""

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# TaskDTO dataclass
# ---------------------------------------------------------------------------

@dataclass
class TaskDTO:
    """Universal work unit inside ARIA's orchestration framework.

    Parameters
    ----------
    task_id : str
        Unique identifier (e.g. ``"task-research-001"``).
    objective : str
        Natural-language description of what this task must accomplish.
    department : str
        Target department — one of ``'research'``, ``'analysis'``,
        ``'writing'``, ``'execution'``, ``'pa'``.
    depends_on : list[str]
        Task IDs that must complete before this task becomes READY.
    priority : int
        Lower number = higher priority.  Used for scheduling order.
    state : TaskState
        Current lifecycle state (default ``PENDING``).
    context : dict
        Scoped, narrow context dict passed into the executing agent.
    result : Optional[str]
        Textual result produced on completion.
    error : Optional[str]
        Error message captured on failure.
    retry_count : int
        How many times this task has been retried so far.
    max_retries : int
        Maximum retry attempts before marking as FAILED.
    created_at : str
        ISO-8601 UTC timestamp of creation.
    completed_at : str
        ISO-8601 UTC timestamp of completion.
    token_budget : int
        Soft cap on tokens the executing agent should consume.
    """

    task_id: str
    objective: str
    department: str
    depends_on: list[str]
    priority: int
    state: TaskState = TaskState.PENDING
    context: dict = field(default_factory=dict)
    result: Optional[str] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 2
    created_at: str = ""
    completed_at: str = ""
    token_budget: int = 1500
    compliance_checklist: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for ``json.dumps``."""
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "department": self.department,
            "depends_on": list(self.depends_on),
            "priority": self.priority,
            "state": self.state.value,
            "context": dict(self.context),
            "result": self.result,
            "error": self.error,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "token_budget": self.token_budget,
            "compliance_checklist": list(self.compliance_checklist),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskDTO":
        """Reconstruct a ``TaskDTO`` from a plain dict (e.g. loaded JSON)."""
        return cls(
            task_id=data["task_id"],
            objective=data["objective"],
            department=data["department"],
            depends_on=list(data.get("depends_on", [])),
            priority=int(data.get("priority", 5)),
            state=TaskState(data.get("state", "PENDING")),
            context=dict(data.get("context", {})),
            result=data.get("result"),
            error=data.get("error"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 2)),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at", ""),
            token_budget=int(data.get("token_budget", 1500)),
            compliance_checklist=list(data.get("compliance_checklist", [])),
        )


# ---------------------------------------------------------------------------
# GoalGraph dataclass
# ---------------------------------------------------------------------------

@dataclass
class GoalGraph:
    """DAG container — holds every task that serves a single user goal.

    Parameters
    ----------
    goal_id : str
        Unique identifier for this goal.
    goal : str
        Natural-language description of the overall goal.
    tasks : list[TaskDTO]
        Ordered list of tasks forming the DAG.
    status : str
        Aggregate status — ``'ACTIVE'``, ``'COMPLETED'``, ``'FAILED'``,
        or ``'CANCELLED'``.
    created_at : str
        ISO-8601 UTC creation timestamp.
    total_token_budget : int
        Soft ceiling on aggregate token usage across all tasks.
    """

    goal_id: str
    goal: str
    tasks: list[TaskDTO]
    status: str = "ACTIVE"
    created_at: str = ""
    total_token_budget: int = 15000
    goal_type: str = "NEW"
    planner_status: str = "SUCCESS"
    intent_packet: Optional[dict] = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a plain-dict representation suitable for ``json.dumps``."""
        return {
            "goal_id": self.goal_id,
            "goal": self.goal,
            "tasks": [t.to_dict() for t in self.tasks],
            "status": self.status,
            "created_at": self.created_at,
            "total_token_budget": self.total_token_budget,
            "goal_type": self.goal_type,
            "planner_status": self.planner_status,
            "intent_packet": self.intent_packet,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GoalGraph":
        """Reconstruct a ``GoalGraph`` from a plain dict."""
        return cls(
            goal_id=data["goal_id"],
            goal=data["goal"],
            tasks=[TaskDTO.from_dict(t) for t in data.get("tasks", [])],
            status=data.get("status", "ACTIVE"),
            created_at=data.get("created_at", ""),
            total_token_budget=int(data.get("total_token_budget", 15000)),
            goal_type=data.get("goal_type", "NEW"),
            planner_status=data.get("planner_status", "SUCCESS"),
            intent_packet=data.get("intent_packet"),
        )


# ---------------------------------------------------------------------------
# DAG validation (cycle detection via Kahn's algorithm)
# ---------------------------------------------------------------------------

def validate_dag(tasks: list[TaskDTO]) -> bool:
    """Verify that the dependency graph is a valid DAG (no cycles).

    Uses Kahn's topological-sort algorithm.  If every node is consumed the
    graph is acyclic and the function returns ``True``.

    Parameters
    ----------
    tasks : list[TaskDTO]
        The tasks whose ``depends_on`` edges form the graph.

    Returns
    -------
    bool
        ``True`` when the graph is a valid DAG.

    Raises
    ------
    ValueError
        If a cycle is detected, listing the task IDs involved.
    """
    task_ids: set[str] = {t.task_id for t in tasks}

    # Build adjacency list and in-degree map.
    # Edge semantics: dependency -> dependent  (dep must finish first).
    in_degree: dict[str, int] = {tid: 0 for tid in task_ids}
    dependents: dict[str, list[str]] = {tid: [] for tid in task_ids}

    for task in tasks:
        for dep_id in task.depends_on:
            if dep_id not in task_ids:
                raise ValueError(
                    f"Task '{task.task_id}' depends on unknown task '{dep_id}'"
                )
            dependents[dep_id].append(task.task_id)
            in_degree[task.task_id] += 1

    # Kahn's algorithm
    queue: deque[str] = deque(
        tid for tid, deg in in_degree.items() if deg == 0
    )
    visited_count = 0

    while queue:
        current = queue.popleft()
        visited_count += 1
        for child in dependents[current]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited_count != len(task_ids):
        cycle_members = sorted(
            tid for tid, deg in in_degree.items() if deg > 0
        )
        raise ValueError(
            f"Cycle detected in task DAG involving task(s): "
            f"{', '.join(cycle_members)}"
        )

    return True


# ---------------------------------------------------------------------------
# TaskEngine — stateful DAG scheduler
# ---------------------------------------------------------------------------

class TaskEngine:
    """Stateful DAG scheduler for a single :class:`GoalGraph`.

    The engine tracks which tasks are ready, running, completed, or blocked.
    It is **thread-safe** — all state mutations acquire ``self._lock``.

    Typical usage::

        engine = TaskEngine(goal_graph)
        while not engine.is_goal_complete():
            for task in engine.get_ready_tasks():
                engine.mark_running(task.task_id)
                result = await execute(task)
                engine.mark_completed(task.task_id, result)
    """

    def __init__(self, goal: GoalGraph) -> None:
        self.goal: GoalGraph = goal
        self._task_map: dict[str, TaskDTO] = {
            t.task_id: t for t in goal.tasks
        }
        self._lock = threading.Lock()
        self._initialize()

    # -- internal helpers ----------------------------------------------------

    def _initialize(self) -> None:
        """Mark every task that has zero unsatisfied dependencies as READY."""
        with self._lock:
            for task in self.goal.tasks:
                if task.state == TaskState.PENDING and not task.depends_on:
                    task.state = TaskState.READY

    def _propagate_ready(self) -> None:
        """Promote PENDING tasks whose dependencies are all COMPLETED."""
        for task in self.goal.tasks:
            if task.state == TaskState.PENDING:
                if all(
                    self._task_map[dep].state == TaskState.COMPLETED
                    for dep in task.depends_on
                ):
                    task.state = TaskState.READY

    def _cascade_block(self, failed_task_id: str) -> None:
        """Recursively BLOCK every downstream task that depends on *failed_task_id*."""
        for task in self.goal.tasks:
            if (
                failed_task_id in task.depends_on
                and task.state in (TaskState.PENDING, TaskState.READY)
            ):
                task.state = TaskState.BLOCKED
                task.error = f"Blocked: dependency {failed_task_id} failed"
                self._cascade_block(task.task_id)

    # -- public scheduling API -----------------------------------------------

    def get_ready_tasks(self) -> list[TaskDTO]:
        """Return all READY tasks sorted by ascending priority (lowest = most urgent)."""
        with self._lock:
            return sorted(
                [t for t in self.goal.tasks if t.state == TaskState.READY],
                key=lambda t: t.priority,
            )

    def mark_running(self, task_id: str) -> None:
        """Transition a task from READY to RUNNING.

        Raises
        ------
        KeyError
            If *task_id* is not in the goal graph.
        ValueError
            If the task is not in a READY state.
        """
        with self._lock:
            task = self._task_map[task_id]
            if task.state != TaskState.READY:
                raise ValueError(
                    f"Cannot mark task '{task_id}' as RUNNING; "
                    f"current state is {task.state.value}"
                )
            task.state = TaskState.RUNNING

    def mark_completed(self, task_id: str, result: str) -> None:
        """Record a successful result and propagate readiness downstream.

        Raises
        ------
        KeyError
            If *task_id* is not in the goal graph.
        """
        with self._lock:
            task = self._task_map[task_id]
            task.state = TaskState.COMPLETED
            task.result = result
            task.completed_at = datetime.now(timezone.utc).isoformat()
            self._propagate_ready()
            self._update_goal_status()

    def mark_failed(self, task_id: str, error: str) -> None:
        """Handle a task failure — retry if budget remains, else cascade-block.

        Raises
        ------
        KeyError
            If *task_id* is not in the goal graph.
        """
        with self._lock:
            task = self._task_map[task_id]
            task.error = error
            if task.retry_count < task.max_retries:
                task.retry_count += 1
                task.state = TaskState.RETRYING
                # Immediately re-queue as READY so the next scheduler tick
                # picks it up again.
                task.state = TaskState.READY
            else:
                task.state = TaskState.FAILED
                self._cascade_block(task_id)
                self._update_goal_status()

    def mark_cancelled(self, task_id: str, reason: str = "") -> None:
        """Cancel a task and cascade-block all dependents.

        Raises
        ------
        KeyError
            If *task_id* is not in the goal graph.
        """
        with self._lock:
            task = self._task_map[task_id]
            task.state = TaskState.CANCELLED
            task.error = reason or "Cancelled"
            self._cascade_block(task_id)
            self._update_goal_status()

    def verify_token_budget(self, task_id: str, accumulated_task_tokens: int, accumulated_goal_tokens: int) -> tuple[bool, str]:
        """Check if executing this task exceeds task-level or goal-level token budgets.

        Returns
        -------
        tuple[bool, str]
            (is_ok, error_reason)
        """
        with self._lock:
            task = self._task_map[task_id]
            # Check goal-level budget cap
            if self.goal.total_token_budget > 0 and accumulated_goal_tokens >= self.goal.total_token_budget:
                return False, f"Goal-level token budget exhausted ({accumulated_goal_tokens} >= {self.goal.total_token_budget})"
            # Check individual task soft budget cap
            if task.token_budget > 0 and accumulated_task_tokens >= task.token_budget:
                return False, f"Task-level token budget exhausted for task '{task_id}' (accumulated {accumulated_task_tokens} >= budget {task.token_budget})"
            return True, ""

    # -- goal-level queries --------------------------------------------------

    def is_goal_complete(self) -> bool:
        """Return ``True`` if every task in the graph is COMPLETED."""
        with self._lock:
            return all(
                t.state == TaskState.COMPLETED for t in self.goal.tasks
            )

    def is_goal_blocked(self) -> bool:
        """Return ``True`` if no further progress is possible.

        This is the case when every non-COMPLETED task is in a terminal
        failure state (FAILED, BLOCKED, or CANCELLED).
        """
        with self._lock:
            non_complete = [
                t for t in self.goal.tasks
                if t.state != TaskState.COMPLETED
            ]
            if not non_complete:
                return False
            return all(
                t.state
                in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED)
                for t in non_complete
            )

    def get_completed_results(self) -> dict[str, str]:
        """Return ``{task_id: result}`` for every COMPLETED task."""
        with self._lock:
            return {
                t.task_id: t.result or ""
                for t in self.goal.tasks
                if t.state == TaskState.COMPLETED
            }

    def get_execution_summary(self) -> str:
        """Produce a human-readable multi-line summary of all tasks."""
        with self._lock:
            lines: list[str] = []
            for t in sorted(self.goal.tasks, key=lambda x: x.priority):
                status = t.state.value
                result_preview = (t.result or "")[:200]
                lines.append(f"[{t.task_id}] {t.objective} — {status}")
                if result_preview:
                    lines.append(f"  Result: {result_preview}")
                if t.error:
                    lines.append(f"  Error: {t.error}")
            return "\n".join(lines)

    def get_token_usage(self) -> int:
        """Estimate total token usage (sum of budgets for completed tasks)."""
        with self._lock:
            return sum(
                t.token_budget
                for t in self.goal.tasks
                if t.state == TaskState.COMPLETED
            )

    # -- internal goal-status bookkeeping ------------------------------------

    def _update_goal_status(self) -> None:
        """Sync ``self.goal.status`` with the aggregate task states.

        Called internally after every state-changing operation (already
        inside ``self._lock``).
        """
        if all(t.state == TaskState.COMPLETED for t in self.goal.tasks):
            self.goal.status = "COMPLETED"
        elif self._is_goal_blocked_unlocked():
            self.goal.status = "FAILED"

    def _is_goal_blocked_unlocked(self) -> bool:
        """Non-locking variant of :meth:`is_goal_blocked` for internal use."""
        non_complete = [
            t for t in self.goal.tasks if t.state != TaskState.COMPLETED
        ]
        if not non_complete:
            return False
        return all(
            t.state
            in (TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED)
            for t in non_complete
        )
