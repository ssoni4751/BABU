"""
workers.py — BABU Narrow-Scope Workers

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

def extract_tokens(res) -> dict:
    usage = {"prompt": 0, "completion": 0, "total": 0}
    if not res or not hasattr(res, "response_metadata"):
        return usage
    meta = res.response_metadata or {}
    usage_meta = meta.get("token_usage") or meta.get("usage") or {}
    if usage_meta:
        usage["prompt"] = usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0) or 0
        usage["completion"] = usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0) or 0
        usage["total"] = usage_meta.get("total_tokens", 0) or (usage["prompt"] + usage["completion"])
    return usage

def run_worker(task: TaskDTO, scoped_context: dict, llm: Any) -> tuple[str, dict]:
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
    tuple[str, dict]
        The result string and token usage dictionary.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    try:
        from .bot import is_private_data_query, REFUSAL_PRIVATE_DATA
    except ImportError:
        from bot import is_private_data_query, REFUSAL_PRIVATE_DATA

    category = None
    intent_packet = task.context.get("intent_packet")
    if intent_packet and isinstance(intent_packet, dict):
        category = intent_packet.get("query_category")

    # Programmatic hard-refusal check: if category is BUSINESS_INFORMATION and local source count is 0
    if category == "BUSINESS_INFORMATION":
        has_action_data = bool(scoped_context.get("action_result") or scoped_context.get("research_briefing") or scoped_context.get("compressed_research"))
        sources = scoped_context.get("sources", {})
        allowed_keys = ["AUTHORITY_MEMORY", "AUTHORITY_DATABASE", "AUTHORITY_LEDGER"]
        has_local_source = has_action_data
        if not has_local_source:
            for k in allowed_keys:
                if sources.get(k):
                    has_local_source = True
                    break
            for k in ("profile_slice", "knowledge_base"):
                if scoped_context.get(k):
                    has_local_source = True
                    break
        
        is_client_record_query = any(k in task.objective.lower() for k in ("client name", "list of client", "who are my client", "client count", "my customer list"))
        if not has_local_source and is_client_record_query:
            refusal_msg = "Mere paas aapke actual client records ka access nahi hai."
            print(f"[WORKER:{task.department.upper()}] Hard Refusal triggered: category is BUSINESS_INFORMATION with 0 local sources.", flush=True)
            return refusal_msg, {"prompt": 0, "completion": 0, "total": 0}

    is_private = is_private_data_query(task.objective, category)

    system = (
        f"BABU Worker [{task.department.upper()}]: Execute the task below.\n"
        f"Use ONLY the provided context. Be extremely concise and factual. Do NOT assume, invent, or extrapolate facts.\n"
    )
    if is_private:
        system += (
            "\nCRITICAL EPISTEMIC DIRECTIVE:\n"
            "This task concerns private user/business data. Use ONLY the provided local context. "
            "Do NOT search the web, assume, estimate, or extrapolate. If the provided local context does not contain the specific requested records "
            "(such as actual client names, list of clients, specific transaction details, or PF claim details), you MUST strictly refuse to answer and output exactly one of:\n"
            f"- '{REFUSAL_PRIVATE_DATA}'\n"
            "- 'Mere paas aapke actual client records ka access nahi hai.' (if query is in Hindi/Hinglish or specifically requests client names)\n"
            "Do NOT fabricate any fake client names, client counts, or dates.\n"
        )
    if task.compliance_checklist:
        system += (
            f"\nCRITICAL: Your output MUST strictly satisfy and EXPLICITLY state/address the following compliance checklist items:\n"
            + "\n".join(f"- {item}" for item in task.compliance_checklist) + "\n"
            + "\nEnsure you explicitly address or state how these checklist items are met (e.g. mention the source of information, its recency, or confidence level if requested in the checklist) so that the post-execution auditor can easily verify them."
        )
    system += "\nOutput your final result directly."

    anti_patterns = get_anti_pattern_rules(f"department.{task.department}")
    if anti_patterns:
        system += f"\n\nCRITICAL ANTI-PATTERNS TO AVOID:\n{anti_patterns}"

    user_content = json.dumps(scoped_context, ensure_ascii=False, default=str)

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=user_content),
    ]

    try:
        print(f"[WORKER:{task.department.upper()}] Starting LLM invocation for task {task.task_id}", flush=True)

        # First, try the pre-built LLM passed by the department
        try:
            res = llm.invoke(messages)
            tokens = extract_tokens(res)
            return res.content.strip(), tokens
        except Exception as primary_exc:
            exc_str = str(primary_exc).lower()
            # If it's a rate limit, try auto-failover through alternative providers
            rate_signals = ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")
            if any(sig in exc_str for sig in rate_signals):
                print(f"[WORKER:{task.department.upper()}] Primary LLM rate-limited → attempting provider failover", flush=True)
                try:
                    try:
                        from babu.bot import invoke_with_fallback, CURRENT_DEPT_MODEL
                    except ImportError:
                        from bot import invoke_with_fallback, CURRENT_DEPT_MODEL
                    res = invoke_with_fallback(messages, model_name=CURRENT_DEPT_MODEL, temp=0.7)
                    tokens = extract_tokens(res)
                    return res.content.strip(), tokens
                except Exception:
                    # All providers exhausted — re-raise the original for proper taxonomy
                    raise primary_exc
            else:
                raise primary_exc

    except Exception as exc:
        exc_str = str(exc).lower()
        print(f"[WORKER:{task.department.upper()}] Invocation failed: {exc}", flush=True)
        # Re-raise infrastructure errors so the executor and immune system
        # can see the ORIGINAL error type (429, network, timeout) instead of
        # a masked "malformed JSON" from the schema validator downstream.
        infra_signals = ("429", "rate limit", "rate_limit_exceeded", "too many requests",
                         "tpd", "tpm", "timeout", "connection refused", "network",
                         "502", "503", "504", "socket", "dns", "unreachable")
        if any(sig in exc_str for sig in infra_signals):
            raise  # Preserve original exception for proper taxonomy classification
        return f"[Worker error: {exc}]", {"prompt": 0, "completion": 0, "total": 0}
