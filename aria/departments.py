"""
departments.py — ARIA Department Head Layer

Department Heads are the middle-management tier of ARIA's multi-agent
architecture.  Each head owns one domain and enforces strict context
scoping so that downstream workers receive *only* the data they need.

Responsibilities:
  • Validate tasks before dispatching to workers
  • Scope context narrowly (workers get ONLY what they need)
  • Dispatch workers and collect results
  • Compress results upward (summaries only flow up)
  • Maintain domain-specific anti-pattern awareness via the Immune System
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

try:
    from .task_engine import TaskDTO, TaskState
    from .memory import get_anti_pattern_rules
except ImportError:
    from task_engine import TaskDTO, TaskState
    from memory import get_anti_pattern_rules

# ── User-profile loader (for ExecutionHead placeholder resolution) ───────────

USER_PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "user_profile.json"
)
_profile_lock = threading.Lock()


def _load_profile() -> dict:
    """Load user_profile.json with thread-safe reads and graceful fallback."""
    with _profile_lock:
        try:
            with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}


# ── Base Department Head ─────────────────────────────────────────────────────


class DepartmentHead:
    """
    Abstract base for every ARIA department.

    Subclasses override ``scope_context`` (and optionally ``_run_worker`` /
    ``dispatch``) to customise behaviour per domain.
    """

    name: str = "base"

    # ── public API ───────────────────────────────────────────────────────

    def validate_task(self, task: TaskDTO) -> bool:
        """Check if *task* is well-formed for this department."""
        return bool(task.objective and task.department)

    def scope_context(self, task: TaskDTO, shared_resources: dict) -> dict:
        """Extract only the context this worker needs.  Override per department."""
        return {
            "objective": task.objective,
            "constraints": task.context.get("constraints", []),
        }

    def dispatch(self, task: TaskDTO, shared_resources: dict, llm: Any) -> str:
        """Validate → scope → run worker → compress.  Returns result string."""
        if not self.validate_task(task):
            raise ValueError(f"Invalid task for {self.name}: {task.task_id}")

        scoped = self.scope_context(task, shared_resources)
        print(
            f"[DEPT:{self.name}] Dispatching worker for task {task.task_id}",
            flush=True,
        )
        raw_result = self._run_worker(task, scoped, llm)
        compressed = self.compress_result(raw_result)
        print(
            f"[DEPT:{self.name}] Worker finished — raw {len(raw_result)} chars → "
            f"compressed {len(compressed)} chars",
            flush=True,
        )
        return compressed

    # ── internals ────────────────────────────────────────────────────────

    def _run_worker(self, task: TaskDTO, scoped_context: dict, llm: Any) -> str:
        """Delegate task execution to modular narrow-context worker."""
        try:
            from .workers import run_worker
        except ImportError:
            from workers import run_worker
        return run_worker(task, scoped_context, llm)

    def compress_result(self, raw: str, max_chars: int = 1500) -> str:
        """Deterministic compression: dedup lines, truncate."""
        if not raw:
            return ""

        lines = raw.split("\n")
        seen: set[str] = set()
        unique: list[str] = []
        for line in lines:
            normalized = " ".join(line.split()).lower()
            if normalized and normalized not in seen:
                seen.add(normalized)
                unique.append(line)

        result = "\n".join(unique)
        if len(result) > max_chars:
            result = result[:max_chars].rsplit(" ", 1)[0] + " ..."
        return result


# ── ResearchHead ─────────────────────────────────────────────────────────────


class ResearchHead(DepartmentHead):
    """Handles information-gathering tasks: web search, KB lookup, profile."""

    name: str = "research"

    def scope_context(self, task: TaskDTO, shared_resources: dict) -> dict:
        """
        Include query, web-search hits, KB hits, and an optional profile
        slice. Empty sources are dynamically fetched using the task objective
        to ensure precision and avoid raw user command pollution.
        """
        scoped: dict[str, Any] = {
            "objective": task.objective,
            "constraints": task.context.get("constraints", []),
        }

        # 1. Use the specific, atomic task objective as the search query
        search_query = task.objective
        scoped["query"] = search_query

        # 2. Dynamically import search helpers to avoid circular dependencies
        try:
            from .bot import web_search, search_knowledge, search_profile
        except ImportError:
            from bot import web_search, search_knowledge, search_profile

        # 3. Dynamic search execution
        print(f"[DEPT:research] Dynamically executing web search for objective: '{search_query}'", flush=True)
        web_hits = web_search(search_query)
        if web_hits and web_hits != "No results found.":
            scoped["web_search"] = web_hits

        kb_hits = search_knowledge(search_query)
        if kb_hits:
            scoped["knowledge_base"] = kb_hits

        profile_slice = search_profile(search_query)
        if profile_slice:
            scoped["profile_slice"] = profile_slice

        return scoped


# ── AnalysisHead ─────────────────────────────────────────────────────────────


class AnalysisHead(DepartmentHead):
    """
    Analyses data produced by upstream tasks.

    Does NOT include web search or profile — only data from prior tasks
    passed via ``task.context['upstream_results']``.
    """

    name: str = "analysis"

    def scope_context(self, task: TaskDTO, shared_resources: dict) -> dict:
        return {
            "objective": task.objective,
            "constraints": task.context.get("constraints", []),
            "upstream_results": task.context.get("upstream_results", []),
        }


# ── WritingHead ──────────────────────────────────────────────────────────────


class WritingHead(DepartmentHead):
    """
    Produces written deliverables from upstream analysis results.

    Includes the objective, upstream analysis outputs, and any formatting
    constraints specified in the task context.
    """

    name: str = "writing"

    def scope_context(self, task: TaskDTO, shared_resources: dict) -> dict:
        return {
            "objective": task.objective,
            "constraints": task.context.get("constraints", []),
            "upstream_results": task.context.get("upstream_results", []),
            "formatting": task.context.get("formatting", {}),
        }


# ── ExecutionHead ────────────────────────────────────────────────────────────


class ExecutionHead(DepartmentHead):
    """
    Pure-programmatic execution of Google Workspace actions.

    This head does NOT call an LLM.  It reads action details from the
    task context, resolves profile placeholders, checks anti-pattern rules,
    and delegates to ``execute_google_action``.
    """

    name: str = "execution"

    # noinspection PyMethodOverriding
    def dispatch(self, task: TaskDTO, shared_resources: dict, llm: Any) -> str:
        """Execute Google action directly — no LLM involved."""
        if not self.validate_task(task):
            raise ValueError(f"Invalid task for {self.name}: {task.task_id}")

        action: str = task.context.get("action", "")
        params: dict = task.context.get("params", {})

        if not action:
            msg = f"[DEPT:{self.name}] No action specified in task {task.task_id}"
            print(msg, flush=True)
            return msg

        # ── Anti-pattern check ───────────────────────────────────────────
        tool_domain = f"action.{action}"
        anti_patterns = get_anti_pattern_rules(tool_domain)
        if anti_patterns:
            print(
                f"[DEPT:{self.name}] Anti-pattern rules for {tool_domain}:\n"
                f"{anti_patterns}",
                flush=True,
            )
            blockers = ("expired", "invalid", "malformed", "bypassed", "quota")
            if any(word in anti_patterns.lower() for word in blockers):
                msg = (
                    f"Action '{action}' bypassed by ExecutionHead due to "
                    f"persistent historical failures:\n{anti_patterns}"
                )
                print(f"[DEPT:{self.name}] {msg}", flush=True)
                return msg

        # Collect upstream results to resolve research context
        upstream_list = task.context.get("upstream_results", [])
        
        # Filter out programmatic execution status updates if other content exists
        has_content_task = any(ur.get("department") in ("research", "analysis", "writing") for ur in upstream_list)
        
        upstream_texts = []
        for ur in upstream_list:
            if has_content_task and ur.get("department") == "execution":
                print(f"[DEPT:{self.name}] Filtering out execution status update task '{ur['task_id']}' to prevent raw output pollution.", flush=True)
                continue
            upstream_texts.append(ur['result'])
            
        upstream_text = "\n\n".join(upstream_texts) if upstream_texts else ""

        # ── Resolve profile and research placeholders ─────────────────────
        resolved_params = self._resolve_params(params, upstream_text)

        print(
            f"[DEPT:{self.name}] Executing action={action} "
            f"params={resolved_params}",
            flush=True,
        )

        # ── Execute via google_service ───────────────────────────────────
        try:
            try:
                from .google_service import execute_google_action
            except ImportError:
                from google_service import execute_google_action

            ok, result_msg = execute_google_action(action, resolved_params)
            status = "SUCCESS" if ok else "FAILED"
            print(f"[DEPT:{self.name}] {status}: {result_msg}", flush=True)
            return result_msg
        except Exception as exc:
            error_msg = f"ExecutionHead error for action '{action}': {exc}"
            print(f"[DEPT:{self.name}] {error_msg}", flush=True)
            return error_msg

    # ── placeholder resolution (mirrors bot.py resolve_action_params) ────

    @staticmethod
    def _resolve_params(params: dict, research_text: str = "") -> dict:
        """Replace profile placeholders (``my_official_email``, etc.) and upstream findings."""
        profile = _load_profile()
        details = profile.get("personal_details", {})

        residential = details.get("residential_address", "")
        if isinstance(residential, dict):
            address_str = residential.get("address", "")
        else:
            address_str = str(residential)

        placeholder_map = {
            "my_official_email": details.get("official_email", ""),
            "my_personal_email": details.get("personal_email", ""),
            "my_mobile": details.get("mobile_number", ""),
            "my_mobile_number": details.get("mobile_number", ""),
            "my_name": details.get("full_name", ""),
            "my_address": address_str,
        }

        resolved: dict[str, Any] = {}
        for key, value in (params or {}).items():
            val_str = str(value).strip()
            if val_str in placeholder_map and placeholder_map[val_str]:
                resolved[key] = placeholder_map[val_str]
            elif "[NEEDS_RESEARCH_CONTEXT]" in val_str:
                resolved[key] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", research_text.strip() if research_text else "(No research/analysis context found)")
            elif key in ("body", "content") and research_text:
                # Dynamically inject research findings if body/content is short or a placeholder,
                # ensuring the actual drafted work is sent instead of a generic subject line/summary.
                cleaned_research = research_text.strip()
                if cleaned_research and cleaned_research not in val_str:
                    if len(val_str) < 300:
                        resolved[key] = f"{val_str}\n\n{cleaned_research}"
                    else:
                        resolved[key] = value
                else:
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved



# ── PAHead (passthrough) ─────────────────────────────────────────────────────


class PAHead(DepartmentHead):
    """
    Personal-Assistant passthrough head.

    This head does NOT dispatch workers.  PA synthesis happens in bot.py's
    ``pa_node``; this class simply marks the task for that downstream node
    and returns the objective verbatim.
    """

    name: str = "pa"

    def dispatch(self, task: TaskDTO, shared_resources: dict, llm: Any) -> str:
        """Return the task objective as-is — PA synthesis is handled later."""
        print(
            f"[DEPT:{self.name}] Passthrough for task {task.task_id} — "
            "PA synthesis deferred to pa_node",
            flush=True,
        )
        return task.objective


# ── Factory ──────────────────────────────────────────────────────────────────

_heads: dict[str, DepartmentHead] = {
    "research": ResearchHead(),
    "analysis": AnalysisHead(),
    "writing": WritingHead(),
    "execution": ExecutionHead(),
    "pa": PAHead(),
}


def get_department_head(department: str) -> DepartmentHead:
    """
    Return the correct ``DepartmentHead`` for *department*.

    Falls back to a generic ``DepartmentHead`` if the name is unknown.
    """
    head = _heads.get(department, DepartmentHead())
    print(f"[DEPT] Resolved department '{department}' → {type(head).__name__}", flush=True)
    return head
