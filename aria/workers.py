"""
workers.py — ARIA Narrow-Scope Workers

Narrow-scope workers receive a microscopic context and execute a single task.
This keeps the worker LLM context clean, token-efficient, and highly focused.
"""

import json
from typing import Any
try:
    from .task_engine import TaskDTO
    from .memory import get_anti_pattern_rules
except ImportError:
    from task_engine import TaskDTO
    from memory import get_anti_pattern_rules

def run_worker(task: TaskDTO, scoped_context: dict, llm: Any) -> str:
    """Execute a single task using the provided narrow context.
    
    Parameters
    ----------
    task : TaskDTO
        The task to execute.
    scoped_context : dict
        Narrow context containing only relevant information.
    llm : Any
        Langchain-compatible LLM instance.
        
    Returns
    -------
    str
        The result of task execution.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    system = (
        f"ARIA Worker [{task.department.upper()}]: Execute the task below.\n"
        f"Use ONLY the provided context. Be extremely concise and factual. Do NOT assume, invent, or extrapolate facts.\n"
        f"Output your final result directly."
    )

    anti_patterns = get_anti_pattern_rules(f"department.{task.department}")
    if anti_patterns:
        system += f"\n\nCRITICAL ANTI-PATTERNS TO AVOID:\n{anti_patterns}"

    user_content = json.dumps(scoped_context, ensure_ascii=False, default=str)

    try:
        print(f"[WORKER:{task.department.upper()}] Starting LLM invocation for task {task.task_id}", flush=True)
        res = llm.invoke([
            SystemMessage(content=system),
            HumanMessage(content=user_content)
        ])
        return res.content.strip()
    except Exception as exc:
        print(f"[WORKER:{task.department.upper()}] Invocation failed: {exc}", flush=True)
        return f"[Worker error: {exc}]"
