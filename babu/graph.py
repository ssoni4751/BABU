import os
import re
import sys
import time
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Annotated, List, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

try:
    from .services import (
        get_db_connection,
        get_token_costs,
        log_temporal_event,
        get_temporal_events,
        is_profile_relevant_query,
        is_private_data_query,
        get_user_profile_text,
        get_profile_fact_answer,
        is_action_status_query,
        search_profile,
        clean_search_query,
        retrieve_system_memory_via_sql,
        retrieve_k0_memory,
        extract_tokens,
        add_tokens,
        log_execution_ledger_event,
        db_save_pending_action,
        db_delete_pending_action,
        get_current_profile
    )
    from .gateway import (
        is_pure_greeting,
        is_deterministic_faq_query,
        is_simple_query,
        requires_workspace_access,
        requires_web_search,
        is_system_aware_query,
        get_babu_age_string,
        get_dynamic_self_identity,
        get_system_health_dashboard,
        get_babu_self_context,
        has_multiple_tasks_or_requests
    )
except ImportError:
    from services import (
        get_db_connection,
        get_token_costs,
        log_temporal_event,
        get_temporal_events,
        is_profile_relevant_query,
        is_private_data_query,
        get_user_profile_text,
        get_profile_fact_answer,
        is_action_status_query,
        search_profile,
        clean_search_query,
        retrieve_system_memory_via_sql,
        retrieve_k0_memory,
        extract_tokens,
        add_tokens,
        log_execution_ledger_event,
        db_save_pending_action,
        db_delete_pending_action,
        get_current_profile
    )
    from gateway import (
        is_pure_greeting,
        is_deterministic_faq_query,
        is_simple_query,
        requires_workspace_access,
        requires_web_search,
        is_system_aware_query,
        get_babu_age_string,
        get_dynamic_self_identity,
        get_system_health_dashboard,
        get_babu_self_context,
        has_multiple_tasks_or_requests
    )

class BabuState(TypedDict):
    messages:       Annotated[list[BaseMessage], "Conversation"]
    research_data:  List[str]
    user_query:     str
    history_text:   str
    session_id:     str
    search_results: str
    action_result:  str
    tokens:         Annotated[dict, add_tokens]
    detected_action: Optional[dict]
    active_goal:    Optional[dict]
    execution_tracker: dict
    compressed_research: str
    routing_metadata: dict
    pending_action_notice: str
    goal_graph:     Optional[dict]
    execution_log:  list[dict]
    final_brief:    str
    knowledge_classes: Optional[List[str]]
    source_records: Optional[List[str]]
    conversation_reference: Optional[bool]
    is_deterministic_response: Optional[bool]
    awareness_report: Optional[dict]

def route_after_router(state: BabuState) -> str:
    notice = state.get("pending_action_notice", "")
    if notice:
        return "pending"
    
    query = state.get("user_query", "")
    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    
    query_category = None
    if intent_packet_dict:
        query_category = intent_packet_dict.get("query_category")
        
    is_faq = is_pure_greeting(query) or is_deterministic_faq_query(query)
    is_multi = has_multiple_tasks_or_requests(query, intent_packet_dict)
    if is_faq and not is_multi:
        print(f"[ROUTE AFTER ROUTER] Deterministic FAQ/greeting detected for query: '{query}'. Short-circuiting directly to PA node.", flush=True)
        return "pa"

    if query_category == "SYSTEM_INFORMATION" and is_faq and not is_multi:
        print(f"[ROUTE AFTER ROUTER] SYSTEM_INFORMATION FAQ query detected ('{query}'). Routing to PA short-circuit.", flush=True)
        return "pa"

    PRIVATE_QUERY_TYPES = ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION")
    if query_category in PRIVATE_QUERY_TYPES:
        print(f"[ROUTE AFTER ROUTER] Private category '{query_category}' detected. Disabling PA direct response bypass and forcing plan route.", flush=True)
        return "plan"

    return "plan"

def intent_router(state: BabuState):
    import time
    _router_t0 = time.time()
    
    query = state["messages"][-1].content
    history_text = state.get("history_text", "")
    lowered = query.lower().strip()
    session_id = state.get("session_id", "default")

    try:
        from .awareness import AwarenessEngine, inspect_services
        from .google_service import is_google_configured
        statuses = inspect_services(os.environ, is_google_configured())
        awareness_report = AwarenessEngine(statuses).create_report(
            query,
            constraints=("Human authority is supreme", "Governance precedes execution"),
        ).to_dict()
    except Exception as exc:
        awareness_report = {
            "objective": query,
            "known_risks": (f"Awareness inspection failed: {exc}",),
            "constraints": ("Human authority is supreme", "Governance precedes execution"),
            "confidence": 0.0,
        }

    clean_query = query
    t_lower = query.lower().strip()
    for prefix in ("/launch", "!launch", "launch", "/sprint", "!sprint", "sprint", "/walk", "!walk", "walk"):
        if t_lower.startswith(prefix):
            clean_query = query[len(prefix):].strip()
            break

    try:
        from .planner import classify_intent
    except ImportError:
        from planner import classify_intent

    try:
        from .bot import CURRENT_PA_MODEL, _pending_actions, _pending_actions_lock, _is_approval_message, _is_reject_message, sync_pending_actions
    except ImportError:
        from bot import CURRENT_PA_MODEL, _pending_actions, _pending_actions_lock, _is_approval_message, _is_reject_message, sync_pending_actions

    intent_packet = classify_intent(clean_query, history_text, model_name=CURRENT_PA_MODEL)
    ic_tokens = getattr(intent_packet, "tokens", None) or {"prompt": 0, "completion": 0, "total": 0}

    detected_action = None
    pending_action_notice = ""

    sync_pending_actions()
    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending:
        action_name = pending.get("action")
        try:
            from .auditor import get_service_class
        except ImportError:
            from auditor import get_service_class
            
        is_class_c = (get_service_class(action_name) == "C") if action_name else False
        stage = pending.get("stage", "approval")

        if is_class_c:
            if stage == "approval":
                if _is_approval_message(query):
                    pending["stage"] = "confirmation"
                    with _pending_actions_lock:
                        _pending_actions[session_id] = pending
                    db_save_pending_action(session_id, pending)
                    pending_action_notice = (
                        f"⚠️ WARNING: Destructive Class C action detected. "
                        f"Are you sure you want to proceed? "
                        f"Reply with 'confirm' or '2' to execute, or '0' / 'cancel' to reject."
                    )
                elif _is_reject_message(query):
                    with _pending_actions_lock:
                        _pending_actions.pop(session_id, None)
                    db_delete_pending_action(session_id)
                    pending_action_notice = "Pending action cancelled."
                else:
                    pending_action_notice = "Action authorization required. Reply with '1' / 'approve' to approve, or '0' / 'cancel' to reject."
            elif stage == "confirmation":
                t_clean = query.lower().strip()
                is_confirm = t_clean in ("confirm", "2", "yes", "proceed")
                is_cancel = t_clean in ("0", "cancel", "reject", "stop", "no")
                
                if is_confirm:
                    detected_action = pending
                    with _pending_actions_lock:
                        _pending_actions.pop(session_id, None)
                    db_delete_pending_action(session_id)
                elif is_cancel:
                    with _pending_actions_lock:
                        _pending_actions.pop(session_id, None)
                    db_delete_pending_action(session_id)
                    pending_action_notice = "Pending action cancelled."
                else:
                    pending_action_notice = (
                        f"⚠️ WARNING: Destructive Class C action detected. "
                        f"Are you sure you want to proceed? "
                        f"Reply with 'confirm' or '2' to execute, or '0' / 'cancel' to reject."
                    )
        else:
            if _is_approval_message(query):
                detected_action = pending
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                db_delete_pending_action(session_id)
            elif _is_reject_message(query):
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                db_delete_pending_action(session_id)
                pending_action_notice = "Pending action cancelled."
            else:
                pending_action_notice = "You already have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard."
    else:
        # Check if there is a pending action in session from external client logic
        pass

    try:
        try:
            from .memory import log_routing_decision
        except ImportError:
            from memory import log_routing_decision
        log_routing_decision(
            session_id=session_id,
            query=query,
            selected_gear="DYNAMIC",
            reason="routing_dispatch",
            has_action=bool(detected_action),
        )
    except Exception as e:
        print(f"[ROUTING MEMORY WARNING] Failed to log routing decision: {e}", flush=True)

    _router_duration = round(time.time() - _router_t0, 4)
    tracker = state.get("execution_tracker") or {
        "start_time": time.time(),
        "router_duration": 0.0,
        "planner_duration": 0.0,
        "executor_duration": 0.0,
        "pa_duration": 0.0,
        "governance_duration": 0.0,
        "task_latencies": [],
    }
    tracker["router_duration"] = _router_duration
    return {
        "user_query": clean_query,
        "research_data": [],
        "search_results": "",
        "action_result": "",
        "detected_action": detected_action,
        "execution_tracker": tracker,
        "compressed_research": "",
        "routing_metadata": {
            "mode": "command_only",
            "reason": "explicit_command",
            "intent_packet": intent_packet.to_dict() if intent_packet else None
        },
        "pending_action_notice": pending_action_notice,
        "awareness_report": awareness_report,
        "tokens": ic_tokens
    }

def planner_node(state: BabuState):
    """Decompose user goal into a structured GoalGraph."""
    import time
    from datetime import datetime, timezone
    try:
        from .planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
    except ImportError:
        from planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
        
    try:
        from .bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL, get_babu_self_context, get_last_goal_graph, should_escalate_to_workflow
    except ImportError:
        from bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL, get_babu_self_context, get_last_goal_graph, should_escalate_to_workflow

    query = state["user_query"]
    history_text = state.get("history_text", "")
    session_id = state.get("session_id", "default")
    
    active_goal = state.get("active_goal") or {}
    pre_goal_id = active_goal.get("goal_id")
    
    detected_action = state.get("detected_action")
    if detected_action:
        print(f"[PLANNER NODE] Detected pending action approval. Skipping planning to let executor handle it directly.", flush=True)
        return {"goal_graph": None}
        
    log_temporal_event(
        event_category="GOAL_RECEIVED",
        summary=f"Received goal: {query[:80]}",
        outcome="SUCCESS",
        metadata={"session_id": session_id, "goal_id": pre_goal_id}
    )
    
    is_profile = is_profile_relevant_query(query)
    is_system = is_system_aware_query(query) and not is_deterministic_faq_query(query)
    profile_text = get_user_profile_text() if is_profile else ""
    
    self_ctx = ""
    if is_system:
        self_ctx = get_babu_self_context(session_id)
        profile_text = (profile_text + "\n\n" + self_ctx).strip()
    
    sql_context = ""
    if is_system:
        sql_context = retrieve_system_memory_via_sql(query)
        if sql_context:
            profile_text = (profile_text + "\n\n" + sql_context).strip()
            
    retrieved = []
    sii_routing = {}
    if is_system:
        try:
            from .system_index import route_query as sii_route_query
        except ImportError:
            from system_index import route_query as sii_route_query

        sii_routing = sii_route_query(query)
        matched_books  = sii_routing.get("books", [])
        matched_adrs   = sii_routing.get("adrs", [])
        query_mode     = sii_routing.get("query_mode", "SYSTEM_INFORMATION")
        matched_comp   = sii_routing.get("matched_component")
        matched_layer  = sii_routing.get("matched_layer")

        print(
            f"[SII] Query routed | mode={query_mode} | "
            f"adrs={matched_adrs} | books={matched_books} | "
            f"component={matched_comp} | layer={matched_layer}",
            flush=True
        )

        try:
            from .rag_storage import retrieve_knowledge
        except ImportError:
            from rag_storage import retrieve_knowledge

        rag_start = time.time()

        if matched_books:
            retrieved = retrieve_knowledge(
                query,
                collections=["adr_books", "system_index", "babu_docs", "engineering_history"],
                sources=matched_books + ["System_Information_Index.md"],
            )
        else:
            retrieved = retrieve_knowledge(
                query,
                collections=["adr_books", "system_index", "babu_docs", "engineering_history"],
            )

        rag_end = time.time()
        rag_latency = round((rag_end - rag_start) * 1000, 2)

        hit = len(retrieved) > 0
        retrieved_tokens = sum(len(x["chunk_text"]) // 4 for x in retrieved) if hit else 0

        log_execution_ledger_event(
            session_id=session_id,
            goal_id=pre_goal_id or "G-PLAN",
            task_id=None,
            department=None,
            event_type="RAG_RETRIEVAL",
            state_before=None,
            state_after=None,
            metadata={
                "query": query,
                "system_query": True,
                "query_mode": query_mode,
                "matched_component": matched_comp,
                "matched_layer": matched_layer,
                "matched_adrs": matched_adrs,
                "matched_books": matched_books,
                "retrieval_requests": 1,
                "retrieval_hits": 1 if hit else 0,
                "retrieval_misses": 0 if hit else 1,
                "retrieval_latency_ms": rag_latency,
                "retrieved_tokens": retrieved_tokens,
                "collections_accessed": list(set(x["collection"] for x in retrieved)) if hit else [],
            }
        )
        log_temporal_event(
            event_category="RAG_RETRIEVAL",
            summary=f"[SII] {query_mode} | books={matched_books} | '{query[:50]}'",
            outcome="SUCCESS" if hit else "FAIL",
            metadata={"hits": len(retrieved), "latency_ms": rag_latency, "query_mode": query_mode}
        )

        if hit:
            context_str = f"\n\n=== SYSTEM_INDEX_DOCUMENT_CONTEXT [mode={query_mode}] ===\n"
            if matched_adrs:
                context_str += f"Resolved ADRs: {', '.join(matched_adrs)}\n"
            if matched_books:
                context_str += f"Evidence from: {', '.join(matched_books)}\n"
            context_str += "\n"
            for item in retrieved:
                context_str += f"[{item['collection']} / {item['source']} - {item['title']}]:\n{item['chunk_text']}\n\n"
            context_str += "=== END OF SYSTEM_INDEX_DOCUMENT_CONTEXT ===\n"
            profile_text = (profile_text + "\n" + context_str).strip()
            
    print(f"[PLANNER NODE] Planning goal for query: '{query[:50]}' (goal_id: {pre_goal_id})", flush=True)
    
    ic_start_time = time.time()
    ic_start_iso = datetime.now(timezone.utc).isoformat()
    
    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    if intent_packet_dict:
        try:
            from .planner import IntentPacket
        except ImportError:
            from planner import IntentPacket
        intent_packet = IntentPacket.from_dict(intent_packet_dict)
        print(f"[PLANNER NODE] Reusing pre-classified intent packet (category: {intent_packet.query_category})", flush=True)
    else:
        intent_packet = classify_intent(query, history_text, model_name=CURRENT_PA_MODEL)
        
    ic_end_time = time.time()
    ic_end_iso = datetime.now(timezone.utc).isoformat()
    ic_latency_ms = round((ic_end_time - ic_start_time) * 1000, 2)
    ic_latency_sec = round(ic_end_time - ic_start_time, 4)

    ic_tokens = getattr(intent_packet, "tokens", None) or {"prompt": 0, "completion": 0, "total": 0}
    ic_model = getattr(intent_packet, "model", None) or CURRENT_DEPT_MODEL or "unknown"
    p_ic, c_ic = get_token_costs(ic_model)
    ic_cost = (ic_tokens.get("prompt", 0) * p_ic) + (ic_tokens.get("completion", 0) * c_ic)
    
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=pre_goal_id or "G-PLAN",
        task_id=None,
        department=None,
        event_type="INTENT_CLASSIFICATION",
        state_before=None,
        state_after="CLASSIFIED",
        metadata={
            "query": query,
            "event_start_time": ic_start_iso,
            "event_end_time": ic_end_iso,
            "latency_ms": ic_latency_ms,
            "latency": ic_latency_sec,
            "tokens": ic_tokens,
            "cost": round(ic_cost, 6),
            "model": ic_model,
        }
    )

    plan_start_time = time.time()
    plan_start_iso = datetime.now(timezone.utc).isoformat()

    template = None
    is_compatible = False
    sig = ""
    
    template_lookup_attempted = True
    template_candidates_found = 0
    template_selected = None
    template_confidence = None
    template_rejected_reason = None
    template_execution_used = False
    template_tokens_saved = 0

    try:
        from .governance import check_constraint_compatibility
    except ImportError:
        from governance import check_constraint_compatibility
        
    flow_order = ["research", "information", "analysis", "writing", "execution", "pa"]
    depts = [d for d in flow_order if d in intent_packet.allowed_departments]
    sig = ":".join(depts)
    if "execution" in depts and intent_packet.allowed_actions:
        sorted_actions = sorted(intent_packet.allowed_actions)
        sig += ":" + ":".join(sorted_actions)

    print(f"[PLANNER NODE] Template signature built for lookup: {sig}", flush=True)
    
    conn, is_pg = get_db_connection()
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute(
                "SELECT COUNT(*) FROM trusted_templates WHERE template_signature = %s AND status = 'ACTIVE'",
                (sig,)
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM trusted_templates WHERE template_signature = ? AND status = 'ACTIVE'",
                (sig,)
            )
        template_candidates_found = cursor.fetchone()[0]
        
        if template_candidates_found > 0:
            if is_pg:
                cursor.execute(
                    "SELECT template_id, goal_graph_json, status FROM trusted_templates WHERE template_signature = %s AND status = 'ACTIVE'",
                    (sig,)
                )
            else:
                cursor.execute(
                    "SELECT template_id, goal_graph_json, status FROM trusted_templates WHERE template_signature = ? AND status = 'ACTIVE'",
                    (sig,)
                )
            row = cursor.fetchone()
            if row:
                template = {
                    "template_id": row[0],
                    "goal_graph_json": row[1],
                    "status": row[2]
                }
        cursor.close()
    except Exception as db_err:
        print(f"[PLANNER DB ERROR] Failed to query trusted_templates: {db_err}", flush=True)
        template_rejected_reason = f"Database query error: {db_err}"
    finally:
        conn.close()

    if not template and not template_rejected_reason:
        template_rejected_reason = "No ACTIVE template found for signature in database"

    if template:
        is_compatible = check_constraint_compatibility(query, template)
        if is_compatible:
            try:
                from .task_engine import GoalGraph
            except ImportError:
                from task_engine import GoalGraph
            
            try:
                graph_dict = json.loads(template["goal_graph_json"])
                graph_dict["goal_id"] = pre_goal_id
                graph_dict["goal"] = query
                graph = GoalGraph.from_dict(graph_dict)
                graph.planner_status = "TEMPLATE_MATCH"
                print(f"[PLANNER NODE] E[Temp] Muscle Memory Hit! Using template {template['template_id']} for signature {sig}", flush=True)
                
                template_selected = template["template_id"]
                template_confidence = 1.0
                template_execution_used = True
                template_tokens_saved = 2300
            except Exception as parse_err:
                print(f"[PLANNER NODE] Failed to load template goal graph: {parse_err}. Falling back to dynamic planner.", flush=True)
                template = None
                is_compatible = False
                template_rejected_reason = f"Parsing error: {parse_err}"
        else:
            template_rejected_reason = "Constraint compatibility checks failed (modifiers, negations, or slots mismatch)"

    if not template or not is_compatible:
        if intent_packet.confidence < 0.65:
            print(f"[INTENT GOVERNANCE] Low confidence ({intent_packet.confidence} < 0.65) -> bypassing planner and returning AMBIGUOUS_QUERY fallback.", flush=True)
            graph = _build_fallback_graph(
                query,
                goal_id=pre_goal_id,
                goal_type="NEW",
                planner_status="AMBIGUOUS_QUERY",
                intent_packet=intent_packet.to_dict()
            )
        elif intent_packet.lookup and not (intent_packet.research or intent_packet.generate or intent_packet.execute or requires_workspace_access(query) or requires_web_search(query)) and not has_multiple_tasks_or_requests(query, intent_packet.to_dict()):
            PRIVATE_QUERY_TYPES = ("BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION")
            if intent_packet.query_category in PRIVATE_QUERY_TYPES:
                print(f"[PLANNER NODE] Private query category '{intent_packet.query_category}' detected → Disabling fast-track simple lookup shortcut.", flush=True)
                graph = plan_goal(
                    query=query,
                    history_text=history_text,
                    profile_text=profile_text,
                    model_name=CURRENT_DEPT_MODEL,
                    goal_id=pre_goal_id,
                    is_correction=False,
                    last_goal_text=None,
                    intent_packet=intent_packet,
                    session_id=session_id,
                    awareness_report=state.get("awareness_report"),
                )
            else:
                print(f"[PLANNER NODE] Fast-tracking simple lookup/websearch query (lookup={intent_packet.lookup}, websearch={intent_packet.websearch}) directly to PA response", flush=True)
                graph = build_walk_graph(query, goal_id=pre_goal_id)
                graph.planner_status = "WALK"
        else:
            is_correction = False
            last_goal_text = None
            last_graph = get_last_goal_graph(session_id)
            if last_graph:
                last_goal_text = last_graph.get("goal")
                t_clean = query.lower().strip()
                has_command_trigger = (
                    t_clean.startswith("/") or 
                    t_clean.startswith("!") or 
                    t_clean.startswith("launch") or 
                    t_clean.startswith("sprint") or 
                    t_clean.startswith("postnow")
                )
                if should_escalate_to_workflow(query, history_text=history_text) and not has_command_trigger:
                    is_correction = True
                    print(f"[PLANNER NODE] Correction detected. Previous goal: '{last_goal_text}'", flush=True)

            graph = plan_goal(
                query=query,
                history_text=history_text,
                profile_text=profile_text,
                model_name=CURRENT_DEPT_MODEL,
                goal_id=pre_goal_id,
                is_correction=is_correction,
                last_goal_text=last_goal_text,
                intent_packet=intent_packet,
                session_id=session_id,
                awareness_report=state.get("awareness_report"),
            )

    plan_end_time = time.time()
    plan_end_iso = datetime.now(timezone.utc).isoformat()
    plan_latency_ms = round((plan_end_time - plan_start_time) * 1000, 2)
    plan_latency_sec = round(plan_end_time - plan_start_time, 4)
    
    if getattr(graph, "planner_status", "") in ("TEMPLATE_MATCH", "WALK", "FAST_TRACK"):
        graph.planning_tokens = {"prompt": 0, "completion": 0, "total": 0}
    else:
        graph.planning_tokens = getattr(graph, "planning_tokens", None) or {"prompt": 1800, "completion": 500, "total": 2300}
    
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="GOAL_CREATED",
        state_before="NONE",
        state_after="ACTIVE",
        metadata={
            "query": query,
            "goal": graph.goal,
            "planner_status": graph.planner_status
        }
    )
    
    if getattr(graph, "planner_status", "") in ("TEMPLATE_MATCH", "WALK", "FAST_TRACK"):
        plan_tokens = {"prompt": 0, "completion": 0, "total": 0}
        plan_cost = 0.0
        has_actual_tokens = True
    else:
        has_actual_tokens = bool(getattr(graph, "planning_tokens", None))
        plan_tokens = getattr(graph, "planning_tokens", None) or {"prompt": 1800, "completion": 500, "total": 2300}
        p_plan, c_plan = get_token_costs(CURRENT_DEPT_MODEL)
        plan_cost = (plan_tokens.get("prompt", 0) * p_plan) + (plan_tokens.get("completion", 0) * c_plan)
    
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="PLANNING",
        state_before=None,
        state_after="PLANNED",
        metadata={
            "query": query,
            "graph": graph.to_dict(),
            "planner_status": graph.planner_status,
            "tokens": plan_tokens,
            "cost": round(plan_cost, 6),
            "model": CURRENT_DEPT_MODEL,
            "is_estimated": not has_actual_tokens,
            "event_start_time": plan_start_iso,
            "event_end_time": plan_end_iso,
            "latency_ms": plan_latency_ms,
            "latency": plan_latency_sec
        }
    )
    
    tracker = state.get("execution_tracker") or {}
    tracker["planner_duration"] = plan_latency_sec
    
    total_planner_tokens = {
        "prompt": ic_tokens.get("prompt", 0) + plan_tokens.get("prompt", 0),
        "completion": ic_tokens.get("completion", 0) + plan_tokens.get("completion", 0),
        "total": ic_tokens.get("total", 0) + plan_tokens.get("total", 0)
    }
        
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=graph.goal_id,
        task_id=None,
        department=None,
        event_type="TEMPLATE_LOOKUP_TELEMETRY",
        metadata={
            "template_lookup_attempted": template_lookup_attempted,
            "template_candidates_found": template_candidates_found,
            "template_selected": template_selected,
            "template_confidence": template_confidence,
            "template_rejected_reason": template_rejected_reason,
            "template_execution_used": template_execution_used,
            "planner_tokens": plan_tokens,
            "template_tokens_saved": template_tokens_saved
        }
    )
    
    log_temporal_event(
        event_category="GOAL_PLANNED",
        summary=f"Goal planned using {graph.planner_status} strategy: {graph.goal[:80]}",
        outcome="SUCCESS",
        metadata={
            "session_id": session_id,
            "goal_id": graph.goal_id,
            "planner_status": graph.planner_status,
            "tasks_count": len(graph.tasks)
        }
    )
        
    routing_meta = state.get("routing_metadata") or {}
    routing_meta["sql_context"] = sql_context
    routing_meta["retrieved_rag"] = retrieved

    ret_dict = {
        "goal_graph": graph.to_dict(), 
        "execution_tracker": tracker, 
        "tokens": total_planner_tokens,
        "routing_metadata": routing_meta
    }
    if is_system:
        ret_dict["compressed_research"] = (self_ctx + "\n\n" + sql_context).strip()
    return ret_dict

def task_executor_node(state: BabuState):
    """Executes the task DAG using TaskEngine and Department Heads."""
    import time
    from datetime import datetime, timezone
    try:
        from .task_engine import TaskEngine, GoalGraph, TaskState
        from .departments import get_department_head
        from .auditor import BipartiteAuditor, get_service_class
    except ImportError:
        from task_engine import TaskEngine, GoalGraph, TaskState
        from departments import get_department_head
        from auditor import BipartiteAuditor, get_service_class
        
    try:
        from .bot import CURRENT_DEPT_MODEL, build_llm, _pending_actions, _pending_actions_lock, db_save_pending_action, get_action_approval_keyboard
    except ImportError:
        from bot import CURRENT_DEPT_MODEL, build_llm, _pending_actions, _pending_actions_lock, db_save_pending_action, get_action_approval_keyboard
    
    start_time = time.time()
    session_id = state.get("session_id", "default")
    
    graph_dict = state.get("goal_graph")
    if not graph_dict:
        try:
            from .planner import build_action_graph
        except ImportError:
            from planner import build_action_graph
        detected_action = state.get("detected_action")
        query = state["user_query"]
        active_goal = state.get("active_goal") or {}
        pre_goal_id = active_goal.get("goal_id")
        if detected_action:
            graph = build_action_graph(query, detected_action, goal_id=pre_goal_id)
            graph_dict = graph.to_dict()
        else:
            return {"action_result": "No executable action found.", "final_brief": "Execution failed: no action found."}
            
    goal_graph = GoalGraph.from_dict(graph_dict)
    engine = TaskEngine(goal_graph)
    llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.1)
    auditor = BipartiteAuditor(llm=llm_dept)
    
    is_template_match = (goal_graph.planner_status == "TEMPLATE_MATCH")
    if is_template_match:
        try:
            from .governance import micro_audit_dag
        except ImportError:
            from governance import micro_audit_dag
        passed_micro = micro_audit_dag(goal_graph.to_dict())
        if not passed_micro:
            print(f"[EXECUTOR] Micro-audit failed for template goal DAG: {goal_graph.goal_id}", flush=True)
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=None,
                department="governance",
                event_type="PLANNER_CONSTRAINT_VIOLATION",
                state_before="ACTIVE",
                state_after="FAILED",
                metadata={"reason": "Micro-audit failed: template constraints violated."}
            )
            return {"action_result": "Governance Refusal: Template constraint violation.", "final_brief": "Micro-audit failed."}
            
    execution_log = state.get("execution_log") or []
    
    while True:
        ready_tasks = engine.get_ready_tasks()
        task = ready_tasks[0] if ready_tasks else None
        if not task:
            break
            
        print(f"[EXECUTOR] Dispatching task '{task.task_id}' [{task.department.upper()}]: {task.objective[:80]}", flush=True)
        engine.mark_running(task.task_id)
        
        pre_start_time = time.time()
        pre_start_iso = datetime.now(timezone.utc).isoformat()

        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="AUDIT_PRE",
            state_before="READY",
            state_after="AUDITING_PRE",
            metadata={"objective": task.objective, "event_start_time": pre_start_iso}
        )
        
        # Pre-execution audit check
        passed, reason = auditor.audit_pre(task)
        
        pre_end_time = time.time()
        pre_end_iso = datetime.now(timezone.utc).isoformat()
        pre_latency_ms = round((pre_end_time - pre_start_time) * 1000, 2)
        pre_latency_sec = round(pre_end_time - pre_start_time, 4)
        
        if not passed:
            print(f"[EXECUTOR] Pre-execution audit FAILED for task '{task.task_id}': {reason}", flush=True)
            engine.mark_failed(task.task_id, reason)
            engine.goal.status = "FAILED"
            
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_PRE_FAIL",
                state_before="RUNNING",
                state_after="FAILED",
                metadata={
                    "reason": reason,
                    "event_start_time": pre_start_iso,
                    "event_end_time": pre_end_iso,
                    "latency_ms": pre_latency_ms,
                    "latency": pre_latency_sec
                }
            )
            
            # Synthesize governance rules from failure
            try:
                from .memory import log_execution_failure
            except ImportError:
                from memory import log_execution_failure
            try:
                log_execution_failure(
                    domain=f"department.{task.department}",
                    method=task.context.get("action", "unknown"),
                    exception_msg=f"Pre-execution Audit Failed: {reason}",
                    goal=goal_graph.goal,
                    intent_packet=task.context.get("intent_packet")
                )
            except Exception as e:
                print(f"[EPISTEMIC MEMORY ERROR] Failed to record audit failure: {e}", flush=True)
                
            return {
                "goal_graph": engine.goal.to_dict(),
                "execution_log": execution_log,
                "final_brief": f"Execution failed at pre-audit stage for task {task.task_id}: {reason}"
            }
            
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="AUDIT_PRE_PASS",
            state_before="AUDITING_PRE",
            state_after="RUNNING",
            metadata={
                "objective": task.objective,
                "event_start_time": pre_start_iso,
                "event_end_time": pre_end_iso,
                "latency_ms": pre_latency_ms,
                "latency": pre_latency_sec,
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "cost": 0.0,
                "model": "rules_engine"
            }
        )
            
        dept_head = get_department_head(task.department)
        completed_results = engine.get_completed_results()
        task.context["upstream_results"] = [
            {
                "task_id": tid,
                "result": get_department_head(engine._task_map[tid].department).compress_result_for_downstream(res),
                "department": engine._task_map[tid].department,
                "objective": engine._task_map[tid].objective
            }
            for tid, res in completed_results.items()
            if tid in task.depends_on
        ]
        
        if task.department == "execution":
            action = task.context.get("action", "")
            try:
                from .governance import get_constitution
            except ImportError:
                from governance import get_constitution
            mandatory_approvals = get_constitution("mandatory_human_approval", [])
            
            if action in mandatory_approvals:
                task.context["approved"] = False
            elif action in ("search_sheet", "search_gmail"):
                task.context["approved"] = True
                
            if not task.context.get("approved"):
                action = task.context.get("action", "")
                params = task.context.get("params", {})
                
                upstream_texts = [
                    item["result"] for item in task.context.get("upstream_results", [])
                ]
                upstream_text = "\n\n".join(upstream_texts) if upstream_texts else ""
                resolved_params = dept_head._resolve_params(params, upstream_text)
                
                # Default pending action state
                pending_action_data = {
                    "action": action,
                    "params": resolved_params,
                    "task_id": task.task_id,
                    "goal_id": goal_graph.goal_id,
                    "stage": "approval"  # Initial stage
                }
                
                with _pending_actions_lock:
                    _pending_actions[session_id] = pending_action_data
                db_save_pending_action(session_id, pending_action_data)
                    
                preview_fields = {k: v for k, v in resolved_params.items() if k not in ("body", "content", "caption")}
                fields_str = "\n".join(f"  • {k.capitalize()}: {v}" for k, v in preview_fields.items())
                body_preview = resolved_params.get("body", resolved_params.get("content", resolved_params.get("caption", "")))
                
                preview = fields_str
                if body_preview:
                    preview += f"\n\n**Draft Content:**\n{body_preview}"
                    
                is_c = (get_service_class(action) == "C")
                prompt_end = "Reply with '1' / 'approve' to approve, or '0' / 'cancel' to reject." if is_c else "Reply with '1' / 'approve' to execute, or '0' / 'cancel' to reject."
                
                pending_action_notice = (
                    f"Action authorization required.\n\n"
                    f"Proposed action: **{action}**\n"
                    f"{preview}\n\n"
                    f"{prompt_end}"
                )
                
                duration = round(time.time() - start_time, 2)
                tracker = state.get("execution_tracker") or {}
                tracker["task_manager_duration"] = duration
                
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="WAITING_FOR_APPROVAL",
                    state_before="RUNNING",
                    state_after="WAITING",
                    metadata={"action": action, "params": resolved_params}
                )
                
                node_tokens = {"prompt": 0, "completion": 0, "total": 0}
                for entry in execution_log:
                    t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
                    node_tokens["prompt"] += t.get("prompt", 0)
                    node_tokens["completion"] += t.get("completion", 0)
                    node_tokens["total"] += t.get("total", 0)
                    
                return {
                    "goal_graph": engine.goal.to_dict(),
                    "execution_log": execution_log,
                    "final_brief": pending_action_notice,
                    "execution_tracker": tracker,
                    "tokens": node_tokens
                }

        # Run worker
        worker_start_time = time.time()
        worker_start_iso = datetime.now(timezone.utc).isoformat()

        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="EXECUTION_START",
            state_before="RUNNING",
            state_after="EXECUTING",
            metadata={"objective": task.objective, "event_start_time": worker_start_iso}
        )
        
        try:
            run_method = getattr(dept_head, "run", None)
            dispatch_method = getattr(dept_head, "dispatch", None)
            worker_payload = run_method(task, llm_dept) if callable(run_method) else None
            if not (isinstance(worker_payload, tuple) and len(worker_payload) == 2):
                worker_payload = dispatch_method(task, {}, llm_dept) if callable(dispatch_method) else worker_payload
            worker_result, node_tokens = worker_payload
            error_msg = None
        except Exception as e:
            worker_result = ""
            node_tokens = {"prompt": 0, "completion": 0, "total": 0}
            error_msg = str(e)
            
        worker_end_time = time.time()
        worker_end_iso = datetime.now(timezone.utc).isoformat()
        worker_latency_ms = round((worker_end_time - worker_start_time) * 1000, 2)
        worker_latency_sec = round(worker_end_time - worker_start_time, 4)
        
        if error_msg:
            print(f"[EXECUTOR] Exception in task '{task.task_id}': {error_msg}", flush=True)
            engine.mark_failed(task.task_id, error_msg)
            engine.goal.status = "FAILED"
            
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="EXECUTION_FAIL",
                state_before="RUNNING",
                state_after="FAILED",
                metadata={
                    "error": error_msg,
                    "event_start_time": worker_start_iso,
                    "event_end_time": worker_end_iso,
                    "latency_ms": worker_latency_ms,
                    "latency": worker_latency_sec
                }
            )
            
            return {
                "goal_graph": engine.goal.to_dict(),
                "execution_log": execution_log,
                "final_brief": f"Execution failed at task {task.task_id} with error: {error_msg}"
            }
            
        p_dept, c_dept = get_token_costs(CURRENT_DEPT_MODEL)
        worker_cost = (node_tokens.get("prompt", 0) * p_dept) + (node_tokens.get("completion", 0) * c_dept)
        
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="EXECUTION_DONE",
            state_before="EXECUTING",
            state_after="EXECUTED",
            metadata={
                "objective": task.objective,
                "event_start_time": worker_start_iso,
                "event_end_time": worker_end_iso,
                "latency_ms": worker_latency_ms,
                "latency": worker_latency_sec,
                "tokens": node_tokens,
                "cost": round(worker_cost, 6),
                "model": CURRENT_DEPT_MODEL,
            }
        )

        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="EXECUTION",
            state_before="RUNNING",
            state_after="EXECUTED",
            metadata={
                "objective": task.objective,
                "tokens": node_tokens,
                "cost": round(worker_cost, 6),
                "model": CURRENT_DEPT_MODEL,
                "is_estimated": False,
                "event_start_time": worker_start_iso,
                "event_end_time": worker_end_iso,
                "latency_ms": worker_latency_ms,
                "latency": worker_latency_sec
            }
        )
        
        # Post-execution Validation check
        post_start_time = time.time()
        post_start_iso = datetime.now(timezone.utc).isoformat()

        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="AUDIT_POST",
            state_before="EXECUTED",
            state_after="AUDITING_POST",
            metadata={"objective": task.objective, "event_start_time": post_start_iso}
        )
        
        passed_post, post_reason = auditor.audit_post(task, worker_result)
        
        post_end_time = time.time()
        post_end_iso = datetime.now(timezone.utc).isoformat()
        post_latency_ms = round((post_end_time - post_start_time) * 1000, 2)
        post_latency_sec = round(post_end_time - post_start_time, 4)
        
        if not passed_post:
            print(f"[EXECUTOR] Post-execution audit FAILED for task '{task.task_id}': {post_reason}", flush=True)
            engine.mark_failed(task.task_id, post_reason)
            engine.goal.status = "FAILED"
            
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_POST_FAIL",
                state_before="RUNNING",
                state_after="FAILED",
                metadata={
                    "reason": post_reason,
                    "event_start_time": post_start_iso,
                    "event_end_time": post_end_iso,
                    "latency_ms": post_latency_ms,
                    "latency": post_latency_sec
                }
            )
            
            try:
                from .memory import log_execution_failure
            except ImportError:
                from memory import log_execution_failure
            try:
                log_execution_failure(
                    domain=f"department.{task.department}",
                    method=task.context.get("action", "unknown"),
                    exception_msg=f"Post-execution Audit Failed: {post_reason}",
                    goal=goal_graph.goal,
                    intent_packet=task.context.get("intent_packet")
                )
            except Exception as e:
                print(f"[EPISTEMIC MEMORY ERROR] Failed to record audit failure: {e}", flush=True)
                
            return {
                "goal_graph": engine.goal.to_dict(),
                "execution_log": execution_log,
                "final_brief": f"Execution failed: post-execution audit blocked task {task.task_id}."
            }
            
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=task.task_id,
            department=task.department,
            event_type="AUDIT_POST_PASS",
            state_before="EXECUTED",
            state_after="COMPLETED",
            metadata={
                "objective": task.objective,
                "event_start_time": post_start_iso,
                "event_end_time": post_end_iso,
                "latency_ms": post_latency_ms,
                "latency": post_latency_sec,
                "tokens": {"prompt": 0, "completion": 0, "total": 0},
                "cost": 0.0,
                "model": "rules_engine"
            }
        )
        
        # Complete task successfully
        engine.mark_completed(task.task_id, worker_result)
        
        task_latencies = state.get("execution_tracker", {}).get("task_latencies") or []
        task_latencies.append({
            "task_id": task.task_id,
            "department": task.department,
            "worker_ms": worker_latency_ms,
            "audit_pre_ms": pre_latency_ms,
            "audit_post_ms": post_latency_ms
        })
        state.get("execution_tracker", {})["task_latencies"] = task_latencies
        
        execution_log.append({
            "task_id": task.task_id,
            "department": task.department,
            "objective": task.objective,
            "result": worker_result,
            "tokens": node_tokens,
            "cost": worker_cost,
            "latency_ms": worker_latency_ms
        })

    engine.goal.status = "COMPLETED"
    
    total_node_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for entry in execution_log:
        t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        total_node_tokens["prompt"] += t.get("prompt", 0)
        total_node_tokens["completion"] += t.get("completion", 0)
        total_node_tokens["total"] += t.get("total", 0)
        
    duration = round(time.time() - start_time, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["executor_duration"] = duration
    
    final_brief = ""
    action_result = ""
    research_results = []
    
    for entry in execution_log:
        dept = entry["department"]
        res = entry["result"]
        if dept == "execution":
            action_result = res
        elif dept == "writing":
            final_brief = res
        elif dept == "research":
            research_results.append(res)
            
    if not final_brief:
        if action_result:
            final_brief = action_result
        elif research_results:
            final_brief = "\n\n".join(research_results)
        else:
            final_brief = "Goal executed successfully, but no department brief was generated."
            
    log_execution_ledger_event(
        session_id=session_id,
        goal_id=goal_graph.goal_id,
        task_id=None,
        department=None,
        event_type="GOAL_COMPLETED",
        state_before="ACTIVE",
        state_after="COMPLETED",
        metadata={
            "query": state["user_query"],
            "goal": goal_graph.goal,
            "final_brief_preview": final_brief[:300],
            "latency": duration
        }
    )
    
    return {
        "goal_graph": engine.goal.to_dict(),
        "execution_log": execution_log,
        "final_brief": final_brief,
        "action_result": action_result,
        "research_data": research_results,
        "execution_tracker": tracker,
        "tokens": total_node_tokens
    }

def pa_node(state: BabuState):
    import time
    try:
        from .bot import CURRENT_PA_MODEL, build_llm, retrieve_k0_memory, tg_application, LAST_TELEGRAM_SUCCESS_TIME, LAST_FB_SUCCESS_TIME, LAST_GOOGLE_SUCCESS_TIME, LAST_WEB_SUCCESS_TIME, BOT_START_TIME
    except ImportError:
        from bot import CURRENT_PA_MODEL, build_llm, retrieve_k0_memory, tg_application, LAST_TELEGRAM_SUCCESS_TIME, LAST_FB_SUCCESS_TIME, LAST_GOOGLE_SUCCESS_TIME, LAST_WEB_SUCCESS_TIME, BOT_START_TIME

    final_brief = state.get("final_brief")
    if final_brief and "Respond directly to user query" in final_brief:
        final_brief = None
    research      = final_brief or state.get("compressed_research") or "\n\n".join(state.get("research_data", []))
    history       = state.get("history_text", "")
    action_result = state.get("action_result", "")
    user_query    = state["user_query"]
    pending_action_notice = state.get("pending_action_notice", "")

    is_conversational = True
    has_tasks = False
    graph_dict = state.get("goal_graph")
    if graph_dict:
        tasks = graph_dict.get("tasks", [])
        if len(tasks) > 1:
            is_conversational = False
        if len(tasks) >= 1:
            has_tasks = True

    lowered_query = user_query.lower().strip().removeprefix("/").removeprefix("!")
    for char in "?!.,":
        lowered_query = lowered_query.replace(char, "")
    lowered_query = lowered_query.strip()
    
    intent_packet_dict = state.get("routing_metadata", {}).get("intent_packet")
    is_multi_request = has_multiple_tasks_or_requests(user_query, intent_packet_dict)
    
    # Deterministic FAQ short-circuits:
    if not is_multi_request and any(k in lowered_query for k in ("current time", "time in ist", "time here in ist", "what is the time", "what time is it")):
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        time_response = f"The current time in Indian Standard Time (IST) is **{now_ist.strftime('%I:%M %p (%A, %B %d, %Y)')}**."
        print(f"[PA NODE] Deterministic short-circuit for time query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=time_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    if not is_multi_request and any(k in lowered_query for k in ("how old are you", "how old you are", "your age", "what is your age", "date of birth of babu", "babu birth", "babu creation", "dob of babu")):
        age_str = get_babu_age_string()
        age_response = f"I am **Project BABU** (Behavioral Autonomous Bureaucratic Utility). My date of birth is **May 27, 2026**. I have been active for **{age_str}**!"
        print(f"[PA NODE] Deterministic short-circuit for age query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=age_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    if not is_multi_request and any(k in lowered_query for k in ("who are you", "tell me about yourself", "about yourself", "know about yourself", "describe yourself", "introduce yourself", "your identity", "what is your name")):
        identity_response = get_dynamic_self_identity()
        print(f"[PA NODE] Deterministic short-circuit for identity query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=identity_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}
        
    if not is_multi_request and any(k in lowered_query for k in ("system health", "status dashboard", "how are you doing", "what is your status", "health dashboard", "current state", "your current state", "what is your current state", "system status", "system status dashboard")):
        dashboard_response = get_system_health_dashboard()
        print(f"[PA NODE] Deterministic short-circuit for health dashboard query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=dashboard_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    if not is_multi_request and any(k in lowered_query for k in ("upgrades received", "recent upgrades", "what upgrades", "upgrades did you receive", "upgrades did you recieve", "upgrades in last", "upgrade received", "recent upgrade", "what upgrade", "upgrade did you receive", "upgrade did you recieve", "upgrade in last", "adr", "architecture decision", "tradeoff", "tradeoffs", "lessons learned", "evolution", "upgrades", "upgrade", "gemini", "dynamic imports", "runtime_index", "postmortem", "lesson", "incident", "impact_score", "highest impact", "largest impact", "biggest impact", "most impact", "supersedes", "solve", "evolve", "hierarchy")):
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT record_id, record_type, title, phase, problem, decision, reason, outcome, tradeoff, impact_score, supersedes, status, timestamp 
                FROM architecture_knowledge ORDER BY record_id ASC
            """)
            rows = cursor.fetchall()
            if rows:
                if any(k in lowered_query for k in ("largest impact", "biggest impact", "highest impact", "most impact", "largest architectural impact")):
                    sorted_by_impact = sorted(rows, key=lambda x: x[9] or 0, reverse=True)
                    highest = sorted_by_impact[0]
                    upgrades_response = (
                        f"### 📈 Highest Architectural Impact Upgrade\n"
                        f"The upgrade with the highest architectural impact score is **{highest[2]}** ({highest[0]}) with an **Impact Score of {highest[9]}**.\n\n"
                        f"- **Type:** {highest[1]}\n"
                        f"- **Problem:** {highest[4]}\n"
                        f"- **Decision:** {highest[5]}\n"
                        f"- **Reason:** {highest[6]}\n"
                        f"- **Outcome:** {highest[7]}\n"
                        f"- **Trade-off:** {highest[8]}"
                    )
                elif "tradeoff" in lowered_query:
                    specific_row = None
                    for r in rows:
                        if r[0].lower() in lowered_query or any(w in r[2].lower().split() for w in lowered_query.split() if len(w) > 3):
                            specific_row = r
                            break
                    if specific_row:
                        upgrades_response = (
                            f"### ⚖️ Tradeoffs for {specific_row[2]} ({specific_row[0]})\n"
                            f"For the decision to **{specific_row[5]}**, the tradeoffs are:\n"
                            f"- **Memory savings:** {specific_row[7]}\n"
                            f"- **vs:** {specific_row[8]}"
                        )
                    else:
                        lines = ["### ⚖️ Architectural Tradeoffs\nHere are the tradeoffs for BABU's major decisions:\n"]
                        for r in rows:
                            lines.append(f"- **{r[2]}** ({r[0]}): {r[8] or 'None'}")
                        upgrades_response = "\n".join(lines)
                elif any(k in lowered_query for k in ("evolve", "evolution", "timeline", "history")):
                    phases = {}
                    for r in rows:
                        ph = r[3] or "General"
                        if ph not in phases:
                            phases[ph] = []
                        phases[ph].append(f"  * **{r[2]}** ({r[0]} - {r[1]}): {r[4]} -> Decision: {r[5]}")
                    
                    lines = ["### 🚀 BABU ARCHITECTURAL EVOLUTION"]
                    for ph in sorted(phases.keys()):
                        lines.append(f"\n#### 📍 {ph}")
                        lines.extend(phases[ph])
                    upgrades_response = "\n".join(lines)
                else:
                    specific_row = None
                    for r in rows:
                        title_words = [w.lower() for w in r[2].lower().split() if len(w) > 3]
                        if r[0].lower() in lowered_query or any(w in lowered_query for w in title_words):
                            specific_row = r
                            break
                    
                    if specific_row:
                        upgrades_response = (
                            f"### 📑 {specific_row[1]}: {specific_row[2]} ({specific_row[0]})\n"
                            f"- **Problem Solved:** {specific_row[4]}\n"
                            f"- **Decision:** {specific_row[5]}\n"
                            f"- **Reason:** {specific_row[6]}\n"
                            f"- **Outcome:** {specific_row[7]}\n"
                            f"- **Tradeoff:** {specific_row[8] or 'None'}\n"
                            f"- **Impact Score:** {specific_row[9]}"
                        )
                    else:
                        lines = [
                            "### 🚀 BABU ARCHITECTURE KNOWLEDGE SYSTEM (AKS) & EVOLUTION",
                            "Here are the documented records tracking the system's key upgrades, trade-offs, and design evolutions:\n"
                        ]
                        for r in rows:
                            lines.append(
                                f"#### 📑 **{r[0]}: {r[2]}** ({r[3]}) - *{r[11]}* [Type: {r[1]}, Impact: {r[9]}]\n"
                                f"- **Problem:** {r[4]}\n"
                                f"- **Decision:** {r[5]}\n"
                                f"- **Reason:** {r[6]}\n"
                                f"- **Outcome:** {r[7]}\n"
                                f"- **Tradeoff:** {r[8]}\n"
                                f"- **Date:** {r[12]}\n"
                            )
                        upgrades_response = "\n".join(lines)
            else:
                upgrades_response = "No architecture knowledge records have been recorded in the database."
        except Exception as e:
            upgrades_response = f"Failed to retrieve upgrades from database: {e}"
        finally:
            cursor.close()
            conn.close()
        print(f"[PA NODE] Dynamic short-circuit for upgrades/ADR query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=upgrades_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    if not is_multi_request and any(k in lowered_query for k in ("your architecture", "tell me about your architecture", "how are you built", "how do you work")):
        arch_response = (
            "My architecture is a decentralized LangGraph-based swarm framework. It consists of:\n"
            "1. **Strategic Planner & Intent Classifier**: Decomposes user queries and enforces capability boundaries.\n"
            "2. **Task Engine**: Orchestrates execution DAGs topologically.\n"
            "3. **Cognitive Departments**: Five specialized heads (**Research**, **Information**, **Analysis**, **Writing**, and **Execution**).\n"
            "4. **Bipartite Auditor**: A dual-stage governance gatekeeper (`PreExecutionGatekeeper` and `PostExecutionValidator`) that ensures safety and compliance."
        )
        print(f"[PA NODE] Deterministic short-circuit for architecture query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=arch_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    if not is_multi_request and any(k in lowered_query for k in ("failures happened", "recent failures", "what are failures", "failures in last", "failures happened in last")):
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT timestamp, goal_id, task_id, event_type, metadata
                FROM execution_ledger
                WHERE event_type IN ('AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'EXECUTION_FAIL', 'PLANNER_CONSTRAINT_VIOLATION')
                ORDER BY event_id DESC
                LIMIT 5
            """)
            rows = cursor.fetchall()
            if rows:
                lines = ["Here are the recent system failures recorded in the execution ledger:\n"]
                for r in rows:
                    ts = r[0][:19] if r[0] else "Unknown Time"
                    goal = r[1]
                    task = r[2] or "N/A"
                    event = r[3]
                    details = r[4]
                    lines.append(f"• **{ts}** | Event: `{event}` | Goal: `{goal}` | Task: `{task}`\n  *Details*: {details}")
                failures_response = "\n".join(lines)
            else:
                failures_response = "No system failures have been recorded in the execution ledger."
        except Exception as e:
            failures_response = f"Failed to retrieve failures log from database: {e}"
        finally:
            cursor.close()
            conn.close()
            
        print(f"[PA NODE] Deterministic short-circuit for failures query: '{user_query}'", flush=True)
        return {"messages": state["messages"] + [AIMessage(content=failures_response)], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}
    
    greetings = {
        "hi", "hello", "hey", "how are you", "how's it going", "how you doing", 
        "how doing", "yo", "hi buddy", "hey buddy", "hello buddy", "good morning", 
        "good afternoon", "good evening"
    }
    is_fresh_greeting = lowered_query in greetings or any(lowered_query.startswith(g + " ") for g in greetings)
    
    if not is_multi_request and is_fresh_greeting and not action_result:
        profile = get_current_profile()
        details = profile.get("personal_details", {}) if profile else {}
        nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
        import random
        greeting_responses = [
            f"Hello {nickname}! How can I help you today?",
            f"Hi {nickname}! What can I do for you?",
            f"Hey {nickname}! How's it going?",
            f"Hello {nickname}! Hope you're having a great day. How can I assist you?",
        ]
        chosen_response = random.choice(greeting_responses)
        print(f"[PA NODE] Deterministic chitchat short-circuit for greeting: '{user_query}'", flush=True)
        response = AIMessage(content=chosen_response)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}, "is_deterministic_response": True}

    routing_metadata = state.get("routing_metadata") or {}
    intent_packet_dict = routing_metadata.get("intent_packet")
    category = None
    if intent_packet_dict:
        category = intent_packet_dict.get("query_category")
    if not category:
        try:
            from .planner import classify_intent
        except ImportError:
            from planner import classify_intent
        intent_packet = classify_intent(user_query, history, model_name=CURRENT_PA_MODEL)
        category = intent_packet.query_category

    sources = {}
    graph_dict = state.get("goal_graph")
    if graph_dict:
        tasks = graph_dict.get("tasks", [])
        for t in tasks:
            t_ctx = t.get("context", {})
            sc = t_ctx.get("scoped_context", {})
            t_sources = sc.get("sources", {})
            if isinstance(t_sources, dict):
                for k, v in t_sources.items():
                    if v:
                        sources[k] = v
            for k in ("profile_slice", "knowledge_base"):
                if sc.get(k):
                    sources[k] = sc[k]

    if category == "BUSINESS_INFORMATION":
        allowed_keys = ["AUTHORITY_MEMORY", "AUTHORITY_DATABASE", "AUTHORITY_LEDGER"]
        has_local_source = False
        for k in allowed_keys:
            if sources.get(k):
                has_local_source = True
                break
        for k in ("profile_slice", "knowledge_base"):
            if k in sources and sources[k]:
                has_local_source = True
                break
        
        if not has_local_source:
            refusal_msg = "Mere paas aapke actual client records ka access nahi hai."
            print(f"[PA NODE] Hard Refusal triggered: category is BUSINESS_INFORMATION with 0 local sources.", flush=True)
            return {"messages": state["messages"] + [AIMessage(content=refusal_msg)], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    is_private = is_private_data_query(user_query, category)

    if is_fresh_greeting or not is_conversational:
        history = ""

    if pending_action_notice:
        response = AIMessage(content=pending_action_notice)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    if not action_result and is_action_status_query(user_query):
        response_text = "No action was executed in this turn."
        if not is_conversational:
            tracker = state.get("execution_tracker", {})
            if tracker and "start_time" in tracker:
                tot = round(time.time() - tracker["start_time"], 2)
                response_text += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
        response = AIMessage(content=response_text)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    if not is_multi_request and not action_result:
        direct_fact = get_profile_fact_answer(user_query)
        if direct_fact:
            if not is_conversational:
                tracker = state.get("execution_tracker", {})
                if tracker and "start_time" in tracker:
                    tot = round(time.time() - tracker["start_time"], 2)
                    direct_fact += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
            response = AIMessage(content=direct_fact)
            return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    if not research and not action_result:
        profile_ctx = search_profile(state["user_query"])
        if profile_ctx and ("[Local User Profile Matches]" in profile_ctx or "[Local User Profile" in profile_ctx):
            research = profile_ctx

    if not research and not action_result and is_profile_relevant_query(user_query):
        full_profile = get_user_profile_text("FULL")
        if full_profile:
            research = full_profile
            print(f"[PA NODE] Injecting full user profile for profile-relevant conversational query.", flush=True)

    if is_conversational:
        style = "[CONVERSATIONAL]\nBrief, warm, direct. Max two short paragraphs. Confirm any automation action clearly."
    else:
        style = "[WORKFLOW]\nStructured briefing: ## headers. Cover overview, findings, risks, outlook. End with one concrete recommendation. Dense and precise."

    now_utc_dt = datetime.now(timezone.utc)
    now_ist_dt = now_utc_dt + timedelta(hours=5, minutes=30)
    now_str = f"{now_utc_dt.strftime('%A, %d %B %Y, %H:%M UTC')} / {now_ist_dt.strftime('%A, %d %B %Y, %H:%M')} IST (Indian Standard Time)"

    if is_conversational:
        profile = get_current_profile()
        details = profile.get("personal_details", {}) if profile else {}
        nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
        manifesto = (
            f"You are BABU, a warm, direct, and helpful personal companion. Current date/time: {now_str}.\n"
            f"Style: Warm, brief, natural human dialogue. Max two short paragraphs. Do not mention internal details.\n"
            f"Recipient: You are talking directly to {nickname}.\n"
            f"CRITICAL: If the user asks about their personal details, family, business, career, or background, you MUST use the information provided in [Internal Research] (which is retrieved from the authoritative local user profile).\n"
            f"If the required personal/business/family information is NOT present in [Internal Research] or [Conversation History], DO NOT invent, infer, or hallucinate any details. In such cases, politely and warmly state that you do not have that information in their profile yet."
        )
    else:
        if is_profile_relevant_query(user_query):
            profile_text = get_user_profile_text("FULL")
        else:
            profile_text = get_user_profile_text("THIN")
        profile_ctx = f"\n\nUser Profile:\n{profile_text}" if profile_text else ""
        
        try:
            from .bot import MAKE_ACTIONS
        except ImportError:
            try:
                from bot import MAKE_ACTIONS
            except ImportError:
                MAKE_ACTIONS = {}
                
        google_tools = ", ".join(MAKE_ACTIONS.keys())
        google_ctx = f"\n\nGoogle Workspace active [{google_tools}]. Confirm any triggered actions clearly."
        
        try:
            from .memory import get_anti_pattern_rules
        except ImportError:
            from memory import get_anti_pattern_rules
        pa_rules = get_anti_pattern_rules("pa")

        manifesto = (
            f"BABU. Current date/time: {now_str}. Never reveal internal agents. {style}"
            f" Use history for context, never repeat it verbatim."
            f"{google_ctx}{profile_ctx}"
        )
        if not action_result:
            manifesto += "\n\nCRITICAL: Do not claim any action was executed/sent/created in this turn unless [Automation Result] is explicitly present."
        if pa_rules:
            manifesto += "\n\n" + pa_rules

    if is_private:
        manifesto += (
            "\n\nCRITICAL EPISTEMIC DIRECTIVES (AUTHORITY LEVELS):\n"
            "- You must strictly answer based ONLY on the [AUTHORITATIVE SOURCES] provided in the context.\n"
            "- AUTHORITY_MEMORY represents the local user profile; AUTHORITY_DATABASE represents the local knowledge base; AUTHORITY_LEDGER represents the system's ledger.\n"
            "- Do NOT fabricate, assume, or generalize any business metrics, client details, transaction numbers, or claims not explicitly listed in these sources.\n"
            "- If the requested details are not present, refuse the query or state that you do not have access to these records."
        )

    parts = []
    if history:
        parts.append(f"[Conversation History]\n{history}")
    
    session_id = state.get("session_id", "default")
    k0_ctx = retrieve_k0_memory(session_id)
    if k0_ctx:
        parts.append(f"[K0 Working Memory]\n{k0_ctx}")

    parts.append(f"User: {state['user_query']}")
    if action_result:
        parts.append(f"[Automation Result]\n{action_result}")
    if research:
        parts.append(f"[Internal Research]\n{research}")
    if sources:
        sources_str = "\n".join(f"- {k}: {json.dumps(v, ensure_ascii=False)}" for k, v in sources.items())
        parts.append(f"[AUTHORITATIVE SOURCES]\n{sources_str}")

    pa_start_time = time.time()
    pa_start_iso = datetime.now(timezone.utc).isoformat()
    
    llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
    response = llm_pa.invoke([SystemMessage(content=manifesto), HumanMessage(content="\n\n".join(parts))])
    
    pa_end_time = time.time()
    pa_end_iso = datetime.now(timezone.utc).isoformat()
    pa_latency_ms = round((pa_end_time - pa_start_time) * 1000, 2)
    pa_latency_sec = round(pa_end_time - pa_start_time, 4)
    
    if graph_dict and graph_dict.get("planner_status", "SUCCESS") not in ("SUCCESS", "WALK", "TEMPLATE_MATCH"):
        p_status = graph_dict.get("planner_status")
        reason_map = {
            "RATE_LIMIT": "Planner rate-limited by Groq API limits (429)",
            "NETWORK": "Planner encountered network timeout or connectivity issues",
            "PROVIDER_ERROR": "Planner API provider returned an execution error",
            "JSON_ERROR": "Planner LLM output could not be parsed as valid JSON",
            "VALIDATION_ERROR": "Planner generated an invalid or cyclic task dependency graph"
        }
        reason_text = reason_map.get(p_status, "Planner encountered an unexpected exception")
        degradation_notice = (
            "\n\n---\n"
            "⚠️ **WORKFLOW STATUS: DEGRADED**\n"
            f"• **Reason**: {reason_text}\n"
            "• **Capability Impact**: Multi-agent research planning & automation pipelines are temporarily unavailable\n"
            "• **Fallback**: Active conversational assistant recovery mode"
        )
        response.content += degradation_notice
    
    if action_result and "[IMAGE]" in action_result:
        if "[IMAGE]" not in response.content:
            match = re.search(r'(\[IMAGE\]\s*url=[^\s\n]+(?:\s+caption=[^\n]+)?)', action_result)
            if match:
                response.content += "\n\n" + match.group(1)
                
    tracker = state.get("execution_tracker", {})
    tracker["pa_duration"] = pa_latency_sec
    if not is_conversational:
        if tracker and "start_time" in tracker:
            tot = round(time.time() - tracker["start_time"], 2)
            router_dur = tracker.get("router_duration", 0.0)
            planner_dur = tracker.get("planner_duration", 0.0)
            
            task_lats = tracker.get("task_latencies", [])
            workers_dur = round(sum(t.get("worker_ms", 0.0) for t in task_lats) / 1000.0, 2)
            audit_dur = round(sum(t.get("audit_pre_ms", 0.0) + t.get("audit_post_ms", 0.0) for t in task_lats) / 1000.0, 2)
            
            telemetry_footnote = f"\n\nSwarm profile: Router {router_dur}s | Planner {planner_dur}s | Workers {workers_dur}s | Audit {audit_dur}s | PA {pa_latency_sec}s | Total {tot}s"
            response.content += telemetry_footnote
                
    token_stats = extract_tokens(response)
    p_rate, c_rate = get_token_costs(CURRENT_PA_MODEL)
    pa_cost = (token_stats.get("prompt", 0) * p_rate) + (token_stats.get("completion", 0) * c_rate)
    try:
        g_id = graph_dict.get("goal_id") if graph_dict else "G-WALK"
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=g_id,
            task_id="T-PA",
            department="pa",
            event_type="PA_SYNTHESIS",
            state_before="RUNNING",
            state_after="COMPLETED",
            metadata={
                "query": user_query,
                "tokens": token_stats,
                "cost": round(pa_cost, 6),
                "response_preview": response.content[:300],
                "model": CURRENT_PA_MODEL,
                "is_estimated": False,
                "event_start_time": pa_start_iso,
                "event_end_time": pa_end_iso,
                "latency_ms": pa_latency_ms,
                "latency": pa_latency_sec
            }
        )
    except Exception as e:
        print(f"[PA TELEMETRY WARNING] Failed to log PA ledger event: {e}", flush=True)
        
    try:
        try:
            from .memory import log_workflow_event
        except ImportError:
            from memory import log_workflow_event
        log_workflow_event(
            session_id=session_id,
            gear="DYNAMIC",
            sequence=["router", "planner", "executor", "pa"] if not is_conversational else ["router", "pa"],
            total_tokens=token_stats.get("total", 0),
            latency_seconds=tracker.get("research_duration", 0.0) + tracker.get("task_manager_duration", 0.0) + tracker.get("action_duration", 0.0),
            success=True,
            note=user_query[:160],
        )
    except Exception as e:
        print(f"[WORKFLOW MEMORY WARNING] Failed to log workflow event: {e}", flush=True)

    return {"messages": state["messages"] + [response], "tokens": token_stats, "execution_tracker": tracker}

def action_node(state: BabuState):
    """Fallback/direct action node if needed."""
    try:
        from .bot import execute_google_action
    except ImportError:
        from bot import execute_google_action
    action_data = state.get("detected_action")
    if not action_data:
        return {"action_result": "No action detected."}
    action = action_data.get("action")
    params = action_data.get("params", {})
    ok, result = execute_google_action(action, params)
    return {"action_result": result}

def task_manager_node(state: BabuState):
    """Fallback/direct task manager node if needed."""
    return {}

def research_dept(state: BabuState):
    """Helper/Direct research department node if needed."""
    return {}

def department_synthesizer(state: BabuState):
    """Helper to compress research reports."""
    reports = state.get("research_data", [])
    try:
        from .departments import deterministic_compress_reports
    except ImportError:
        from departments import deterministic_compress_reports
    return {"compressed_research": deterministic_compress_reports(reports)}


workflow = StateGraph(BabuState)
workflow.add_node("router",       intent_router)
workflow.add_node("planner",      planner_node)
workflow.add_node("executor",     task_executor_node)
workflow.add_node("pa",           pa_node)

workflow.set_entry_point("router")
workflow.add_conditional_edges("router", route_after_router, {
    "plan": "planner",
    "pending": "pa",
    "pa": "pa",
})
workflow.add_edge("planner", "executor")
workflow.add_edge("executor", "pa")
workflow.add_edge("pa",           END)
