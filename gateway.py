import os
import re
import sys
import time
import json
import threading
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List

try:
    from .services import (
        get_db_connection,
        get_token_costs,
        get_temporal_events,
        is_google_configured,
        get_current_profile,
        get_user_profile_text,
        is_profile_relevant_query,
        is_private_data_query
    )
except ImportError:
    from services import (
        get_db_connection,
        get_token_costs,
        get_temporal_events,
        is_google_configured,
        get_current_profile,
        get_user_profile_text,
        is_profile_relevant_query,
        is_private_data_query
    )

def is_pure_greeting(text: str) -> bool:
    t = (text or "").lower().strip()
    t = t.removeprefix("/").removeprefix("!")
    for char in "?!.,":
        t = t.replace(char, "")
    t = t.strip()
    
    greetings = {
        "hi", "hello", "hey", "how are you", "how's it going", "how you doing", 
        "how doing", "yo", "hi buddy", "hey buddy", "hello buddy", "good morning", 
        "good afternoon", "good evening"
    }
    return t in greetings

def is_deterministic_faq_query(query: str) -> bool:
    t = (query or "").lower().strip()
    t = t.removeprefix("/").removeprefix("!")
    for char in "?!.,":
        t = t.replace(char, "")
    t = t.strip()
    
    faq_keywords = (
        "current time", "time in ist", "time here in ist", "what is the time", "what time is it",
        "how old are you", "how old you are", "your age", "what is your age", "date of birth", "dob of babu",
        "what did you do yesterday", "what did you do today", "what you did yesterday", "what you did today",
        "kal kya kiya", "kal kya kaam hua", "kal kya kaam kiya", "aaj kya kiya", "aaj kya kaam kiya", "yesterday tasks", "yesterdays tasks", "yesterday's tasks",
        "who are you", "tell me about yourself", "about yourself", "know about yourself", "about you", "tell me about you", "know about you",
        "describe yourself", "introduce yourself", "your identity", "what is your name",
        "your architecture", "tell me about your architecture", "how are you built", "how do you work",
        "failures happened", "recent failures", "what are failures", "failures in last", "failures happened in last",
        "system health", "status dashboard", "how are you doing", "what is your status", "health dashboard",
        "current state", "your current state", "what is your current state", "system status", "system status dashboard",
        "upgrades received", "recent upgrades", "what upgrades", "upgrades did you receive", "upgrades did you recieve", "upgrades in last",
        "upgrade received", "recent upgrade", "what upgrade", "upgrade did you receive", "upgrade did you recieve", "upgrade in last",
        "tradeoff", "tradeoffs", "architectural tradeoffs", "architectural tradeoff",
        "highest impact", "largest impact", "biggest impact", "most impact",
        "evolution", "evolve", "history", "timeline", "adr", "architecture decision",
        "solve", "incident", "postmortem", "lesson", "milestone"
    )
    return any(k in t for k in faq_keywords)

def is_simple_query(text: str) -> bool:
    t = (text or "").lower().strip()
    t = t.removeprefix("/").removeprefix("!")
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 15:
        return True
    return False

def requires_workspace_access(query: str) -> bool:
    t = query.lower()
    pattern = r'\b(mail|email|gmail|sheet|sheets|spreadsheet|spreadsheets|calendar|calendars|event|events|meeting|meetings|slack|contact|contacts|photos|drive)\b'
    return bool(re.search(pattern, t))

def get_babu_age_string() -> str:
    dob = datetime(2026, 5, 27, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    diff = now - dob
    days = diff.days
    if days < 0:
        return "recently launched"
    
    years = days // 365
    remaining_days = days % 365
    months = remaining_days // 30
    remaining_days = remaining_days % 30
    
    parts = []
    if years > 0:
        parts.append(f"{years} year" + ("s" if years > 1 else ""))
    if months > 0:
        parts.append(f"{months} month" + ("s" if months > 1 else ""))
    if remaining_days > 0 or not parts:
        parts.append(f"{remaining_days} day" + ("s" if remaining_days > 1 else ""))
        
    return " and ".join(parts) if len(parts) == 2 else ", ".join(parts)

def get_dynamic_self_identity() -> str:
    """Retrieve the operational identity of the system based on the Runtime Index."""
    try:
        from .bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL
    except ImportError:
        try:
            from bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL
        except ImportError:
            CURRENT_PA_MODEL = "openai/gpt-oss-120b"
            CURRENT_DEPT_MODEL = "openai/gpt-oss-20b"
            
    try:
        enabled_services = []
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            enabled_services.append("Telegram Interface")
        if os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN"):
            enabled_services.append("Facebook Publishing")
        if is_google_configured():
            enabled_services.append("Google Workspace")
        enabled_services.append("Web Dashboard")
        
        services_str = ", ".join(enabled_services) if enabled_services else "None"
        
        identity_text = (
            f"=== 👤 IDENTITY INDEX ===\n"
            f"**Name:** Pragya (Project BABU Cognitive OS)\n"
            f"**Version:** 4.0.0 (V2 Cognitive OS)\n"
            f"**Purpose:** A governed, stratified 9-layer Cognitive Operating System — built to automate research, analysis, writing, and Google Workspace execution with constitutional authority, human-supreme governance, and zero-hallucination auditing.\n\n"
            f"**Capabilities:**\n"
            f"- Active PA Model: `{CURRENT_PA_MODEL}`\n"
            f"- Active Department Model: `{CURRENT_DEPT_MODEL}`\n"
            f"- Enabled Services: {services_str}\n\n"
            f"**Architecture:**\n"
            f"LangGraph-based governed Cognitive OS with 9 stratified layers:\n"
            f"- Layer 1 — Interface Gateway: Telegram, Facebook, Web Dashboard ingestion.\n"
            f"- Layer 2 — Intent Classification: Rule-based + LLM routing, Class A/B/C action policies.\n"
            f"- Layer 3 — Constitution & Governance: Human-supreme authority, Epistemic Immune System.\n"
            f"- Layer 4 — Planning & Orchestration: Topological DAG planner, dependency resolution.\n"
            f"- Layer 5 — Department Execution: Research, Information, Analysis, Writing, Execution workers.\n"
            f"- Layer 6 — Bipartite Auditor: Pre-execution gatekeeper + Post-execution semantic validator.\n"
            f"- Layer 7 — Memory & Knowledge: SQLite checkpoint DB, RAG ingestion, Self-RAG feedback.\n"
            f"- Layer 8 — Telemetry & Observability: Latency tracking, failure logging, governance telemetry.\n"
            f"- Layer 9 — Self-Improvement: Anti-pattern learning, immune lesson propagation, ADR knowledge base.\n"
            f"- Cache: E[Temp] compiled templates for zero-latency short-circuit responses."
        )
        return identity_text
    except Exception as e:
        print(f"[DYNAMIC IDENTITY ERROR] {e}", flush=True)
        return "I am **Pragya** (Project BABU Cognitive OS), a governed multi-agent assistant. (Identity details currently unavailable)."

def is_system_aware_query(query: str) -> bool:
    """Determine if a query is related to BABU's codebase, architecture, templates, governance, or self-identity."""
    q = query.lower()
    keywords = {
        "etemp", "governance", "auditor", "anti-pattern", "failures", "telemetry",
        "self-awareness", "self-rag", "architecture", "codebase", "immune lesson",
        "why did this task fail", "why did my task fail", "why did task fail",
        "how does babu work", "how do you work", "bipartite auditor", "what governance rule",
        "what recurring problems", "what fixes were previously applied",
        "about yourself", "know about yourself", "describe yourself", "about you", "tell me about you", "know about you",
        "introduce yourself", "who are you", "what is your name", "your identity",
        "how old are you", "your age", "date of birth", "dob of babu",
        "adr", "architecture decision", "tradeoff", "lessons learned", "evolution",
        "architecture report", "system upgrades", "gemini chosen", "dynamic imports", "runtime_index",
        "status of last goal", "last goal status", "current goal", "pending action", "pending goal", "system status", "status of goal"
    }
    if any(kw in q for kw in keywords):
        return True
        
    if "you " in q or "your " in q or "you've" in q or "did you" in q:
        system_terms = ["upgrade", "update", "code", "system", "feature", "recieve", "receive", "new capability"]
        if any(term in q for term in system_terms):
            return True
            
    return False

def requires_web_search(query: str) -> bool:
    t = query.lower().strip()
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 10:
        return False
        
    if is_profile_relevant_query(query):
        if not any(kw in t for kw in ("search the web", "search google", "web search", "google search", "wikipedia", "search online")):
            return False

    if is_system_aware_query(query) or is_deterministic_faq_query(query):
        if not any(kw in t for kw in ("search the web", "search google", "web search", "google search", "wikipedia", "search online")):
            return False

    web_patterns = [
        r'\b(search|web|google|wikipedia|wiki|ddg|duckduckgo)\b',
        r'\b(weather|temperature|forecast|climate)\b',
        r'\b(news|headlines|current affairs|stock|price|market)\b',
        r'\b(match|matches|score|scores|cricket|football|sports|game|games)\b',
        r'\b(current|latest|recent|upcoming|newest|today|now)\b',
        r'\b(who is|who was|what is|what are|where is|when is|how to|why did)\b'
    ]
    return any(re.search(pat, t) for pat in web_patterns)

def get_babu_self_context(session_id: str = "default") -> str:
    try:
        from .bot import get_babu_age_string, retrieve_k0_memory
    except ImportError:
        try:
            from bot import get_babu_age_string, retrieve_k0_memory
        except ImportError:
            def retrieve_k0_memory(sid, limit=5): return ""
            
    age_str = get_babu_age_string()
    k0_ctx = retrieve_k0_memory(session_id)
    
    self_ctx = (
        f"You are Pragya (Project BABU Cognitive OS), Version 4.0.0 (V2 Cognitive OS).\n"
        f"Date of Birth: May 27, 2026.\n"
        f"System Age: {age_str}.\n"
        f"Operating Environment: Python {sys.version.split()[0]} on Windows.\n"
        f"Authoritative Knowledge: Automated tax, compliance (PF, GST, CSC services), and e-governance assistant.\n"
        f"Architecture: LangGraph-based governed Cognitive OS with 9 stratified layers, including "
        f"a Bipartite Auditor (Pre-Execution Gatekeeper + Post-Execution Semantic Validator), "
        f"Constitutional Governance with human-supreme authority, and Epistemic Immune System.\n"
    )
    if k0_ctx:
        self_ctx += f"\nRecent Context (K0):\n{k0_ctx}"
    return self_ctx

def has_multiple_tasks_or_requests(query: str, intent_packet_dict: Optional[dict] = None) -> bool:
    import re
    t = (query or "").lower().strip()
    
    if intent_packet_dict:
        allowed_depts = intent_packet_dict.get("allowed_departments", [])
        allowed_actions = intent_packet_dict.get("allowed_actions", [])
        
        mutating_actions = {
            "send_email", "create_event", "log_to_sheet", "create_doc", 
            "copy_photos_to_drive", "copy_contacts_to_drive", 
            "send_slack", "create_task", "post_to_facebook", "generate_image"
        }
        has_mutating = any(act in allowed_actions for act in mutating_actions)
        core_depts = [d for d in allowed_depts if d != "pa" and (d != "execution" or has_mutating)]
        if len(core_depts) > 1:
            return True
            
        active_mutating_actions = [act for act in allowed_actions if act in mutating_actions]
        if len(active_mutating_actions) > 1:
            return True
            
        has_execution = len(active_mutating_actions) > 0
        if has_execution and ("information" in allowed_depts or "research" in allowed_depts):
            return True

    conjunction_patterns = [r'\band\b', r'\balso\b', r'\bthen\b', r'\bplus\b', r'\balong with\b', r'\bas well as\b', r';']
    has_conjunction = any(re.search(pat, t) for pat in conjunction_patterns)
    
    if has_conjunction:
        parts = re.split(r'\band\b|\balso\b|\bthen\b|\bplus\b|\balong with\b|\bas well as\b|;', t)
        parts = [p.strip() for p in parts if p.strip()]

        pure_faq_keywords = (
            "who are you", "what are you", "what do you do", "what you do",
            "what you does", "what can you do", "what you can do",
            "tell me about yourself", "about yourself", "about you", "tell me about you",
            "describe yourself", "introduce yourself", "your identity", "what is your name",
            "how old are you", "your age", "date of birth", "dob", "age",
            "what is the time", "current time", "time in ist", "time",
            "what did you do yesterday", "what did you do today", "yesterday", "today",
            "your architecture", "how do you work", "how are you built",
            "your capabilities", "what you does best", "what you do best",
            "what do you do best", "your strength", "your strengths", "best at",
            "your specialty", "specialize", "specialise",
        )

        action_keywords = (
            "send", "email", "mail", "create", "event", "calendar", "log", "sheet",
            "spreadsheet", "document", "doc", "slack", "post", "facebook",
            "search", "find", "look up", "lookup", "research", "fetch", "get me",
        )

        faq_part_count = 0
        action_part_count = 0
        for part in parts:
            if len(part) < 4:
                continue
            is_faq_part = any(k in part for k in pure_faq_keywords)
            is_action_part = any(k in part for k in action_keywords)
            if is_faq_part and not is_action_part:
                faq_part_count += 1
            elif is_action_part:
                action_part_count += 1

        if action_part_count == 0 and faq_part_count >= 1:
            pass
        else:
            valid_requests_count = 0
            request_keywords = (
                "current time", "time in ist", "what time", "what is the time",
                "how old", "your age", "date of birth", "dob", "who are you", "about yourself",
                "system health", "status dashboard", "system status", "upgrades", "upgrade",
                "tradeoff", "highest impact", "evolution", "evolve", "history", "timeline",
                "send", "email", "mail", "create", "event", "calendar", "log", "sheet", "spreadsheet",
                "document", "doc", "slack", "post", "facebook", "search", "find", "look up", "lookup",
                "tell", "check", "show",
            )
            for part in parts:
                if len(part) >= 4 and any(k in part for k in request_keywords):
                    valid_requests_count += 1
            if valid_requests_count > 1:
                return True
            
    faq_types_present = set()
    if any(k in t for k in ("current time", "time in ist", "time here in ist", "what is the time", "what time is it")):
        faq_types_present.add("time")
    if any(k in t for k in ("how old are you", "how old you are", "your age", "what is your age", "date of birth", "dob of babu")):
        faq_types_present.add("age")
    if any(k in t for k in ("who are you", "tell me about yourself", "about yourself", "your identity", "what is your name")):
        faq_types_present.add("identity")
    if any(k in t for k in ("system health", "status dashboard", "health dashboard", "system status", "health status", "health")):
        faq_types_present.add("health")
    if any(k in t for k in ("upgrades", "upgrade", "adr", "tradeoff", "highest impact", "evolution", "evolve", "history", "timeline", "incident", "architecture", "built")):
        faq_types_present.add("system")
        
    if len(faq_types_present) > 1:
        return True

    has_faq = any(k in t for k in (
        "current time", "time in ist", "what is the time", "what time is it",
        "how old are you", "your age", "date of birth", "dob of babu",
        "who are you", "tell me about yourself", "your identity", "what is your name",
        "system health", "status dashboard", "system status",
        "upgrades", "upgrade", "tradeoff", "highest impact", "evolution", "history", "timeline"
    ))
    if has_faq:
        if requires_workspace_access(query) or requires_web_search(query):
            return True
            
    return False

def get_system_health_dashboard() -> str:
    """Generate a comprehensive real-time System Health & Self-Audit Dashboard."""
    try:
        from .bot import (
            LAST_TELEGRAM_SUCCESS_TIME,
            LAST_FB_SUCCESS_TIME,
            LAST_GOOGLE_SUCCESS_TIME,
            LAST_WEB_SUCCESS_TIME,
            BOT_START_TIME,
            tg_application,
            supported_models,
            model_mon
        )
    except ImportError:
        try:
            from bot import (
                LAST_TELEGRAM_SUCCESS_TIME,
                LAST_FB_SUCCESS_TIME,
                LAST_GOOGLE_SUCCESS_TIME,
                LAST_WEB_SUCCESS_TIME,
                BOT_START_TIME,
                tg_application,
                supported_models,
                model_mon
            )
        except ImportError:
            LAST_TELEGRAM_SUCCESS_TIME = LAST_FB_SUCCESS_TIME = LAST_GOOGLE_SUCCESS_TIME = LAST_WEB_SUCCESS_TIME = None
            BOT_START_TIME = time.time()
            tg_application = None
            supported_models = []
            model_mon = {}

    try:
        from .bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL
    except ImportError:
        try:
            from bot import CURRENT_PA_MODEL, CURRENT_DEPT_MODEL
        except ImportError:
            CURRENT_PA_MODEL = "openai/gpt-oss-120b"
            CURRENT_DEPT_MODEL = "openai/gpt-oss-20b"
            
    def parse_db_timestamp(ts_val):
        if not ts_val:
            return None
        if isinstance(ts_val, datetime):
            if ts_val.tzinfo is None:
                return ts_val.replace(tzinfo=timezone.utc)
            return ts_val
        try:
            cleaned = str(ts_val).strip().replace(" ", "T")
            if "." in cleaned:
                parts = cleaned.split(".")
                sec_part = parts[1]
                suffix = ""
                if sec_part.endswith("Z"):
                    suffix = "Z"
                    sec_part = sec_part[:-1]
                elif "+" in sec_part:
                    sec_part, suffix = sec_part.split("+", 1)
                    suffix = "+" + suffix
                elif "-" in sec_part:
                    sec_part, suffix = sec_part.split("-", 1)
                    suffix = "-" + suffix
                sec_part = sec_part[:6]
                cleaned = parts[0] + "." + sec_part + suffix
            if cleaned.endswith("Z"):
                cleaned = cleaned[:-1] + "+00:00"
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(ts_val).strip()[:19], fmt)
                    return dt.replace(tzinfo=timezone.utc)
                except Exception:
                    pass
            return None

    def format_last_success(last_time):
        if not last_time:
            return "Never"
        diff = time.time() - last_time
        if diff < 60:
            return "<1 min ago"
        mins = int(diff // 60)
        if mins < 60:
            return f"{mins} min ago"
        hours = int(mins // 60)
        if hours < 24:
            return f"{hours} hours ago"
        return f"{int(hours // 24)} days ago"

    try:
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(DISTINCT goal_id) FROM execution_ledger WHERE event_type = 'GOAL_COMPLETED'")
        completed_goals = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(DISTINCT goal_id) FROM execution_ledger WHERE event_type = 'GOAL_FAILED'")
        failed_goals = cursor.fetchone()[0] or 0
        
        total_goals = completed_goals + failed_goals
        success_rate = (completed_goals / total_goals * 100) if total_goals > 0 else 100.0
        
        cursor.execute("SELECT event_type, metadata FROM execution_ledger")
        rows = cursor.fetchall()
        
        total_tokens = 0
        total_cost = 0.0
        gov_blocks = 0
        last_failure = "None recently"
        
        for ev_type, meta_str in rows:
            if ev_type in ("AUDIT_PRE_FAIL", "AUDIT_POST_FAIL", "PLANNER_CONSTRAINT_VIOLATION"):
                gov_blocks += 1
            
            if ev_type in ("EXECUTION_FAIL", "AUDIT_PRE_FAIL", "AUDIT_POST_FAIL", "PLANNER_CONSTRAINT_VIOLATION"):
                if last_failure == "None recently":
                    last_failure = f"{ev_type}"
                    if meta_str:
                        try:
                            meta = json.loads(meta_str)
                            reason = meta.get("reason") or meta.get("error") or meta.get("details") or ""
                            if reason:
                                last_failure += f" ({reason[:60]})"
                        except Exception:
                            pass
            
            if meta_str:
                try:
                    meta = json.loads(meta_str)
                    tokens = meta.get("tokens")
                    if tokens and isinstance(tokens, dict):
                        prompt = tokens.get("prompt", 0) or 0
                        completion = tokens.get("completion", 0) or 0
                        total_t = tokens.get("total", 0) or (prompt + completion)
                        total_tokens += total_t
                        
                        model_name = meta.get("model", "")
                        p_rate, c_rate = get_token_costs(model_name)
                        total_cost += (prompt * p_rate) + (completion * c_rate)
                except Exception:
                    pass

        cursor.execute("""
            SELECT timestamp, goal_id, event_type 
            FROM execution_ledger 
            WHERE event_type IN ('GOAL_COMPLETED', 'GOAL_FAILED') 
            ORDER BY event_id DESC
        """)
        goal_rows = cursor.fetchall()
        
        goals_seen = {}
        for ts_str, gid, ev_type in goal_rows:
            if gid not in goals_seen:
                goals_seen[gid] = (ts_str, ev_type == "GOAL_COMPLETED")
                
        parsed_goals = []
        for gid, (ts_str, succ) in goals_seen.items():
            dt = parse_db_timestamp(ts_str)
            parsed_goals.append((dt, succ))
            
        parsed_goals.sort(key=lambda x: x[0] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        
        last_100 = parsed_goals[:100]
        completed_100 = sum(1 for dt, succ in last_100 if succ)
        total_100 = len(last_100)
        success_rate_100 = (completed_100 / total_100 * 100) if total_100 > 0 else 100.0
        
        now_utc = datetime.now(timezone.utc)
        last_24h = [g for g in parsed_goals if g[0] and (now_utc - g[0]).total_seconds() <= 86400]
        completed_24h = sum(1 for dt, succ in last_24h if succ)
        total_24h = len(last_24h)
        success_rate_24h = (completed_24h / total_24h * 100) if total_24h > 0 else 100.0

        cursor.execute("""
            SELECT event_type, timestamp, metadata 
            FROM execution_ledger 
            WHERE event_type IN ('EXECUTION_FAIL', 'AUDIT_PRE_FAIL', 'AUDIT_POST_FAIL', 'PLANNER_CONSTRAINT_VIOLATION') 
            ORDER BY event_id DESC LIMIT 1
        """)
        last_fail_row = cursor.fetchone()
        
        cursor.execute("""
            SELECT timestamp 
            FROM execution_ledger 
            WHERE event_type = 'GOAL_COMPLETED' 
            ORDER BY event_id DESC LIMIT 1
        """)
        last_comp_row = cursor.fetchone()
        
        last_failure_type = None
        last_failure_ts = None
        last_failure_reason = ""
        
        if last_fail_row:
            last_failure_type = last_fail_row[0]
            last_failure_ts = parse_db_timestamp(last_fail_row[1])
            if last_fail_row[2]:
                try:
                    meta = json.loads(last_fail_row[2])
                    last_failure_reason = meta.get("reason") or meta.get("error") or meta.get("details") or ""
                except Exception:
                    pass
        
        last_comp_ts = parse_db_timestamp(last_comp_row[0]) if last_comp_row else None
        
        is_unresolved = False
        if last_failure_ts:
            if not last_comp_ts or last_failure_ts > last_comp_ts:
                is_unresolved = True

        if is_unresolved:
            failures_status_str = "DEGRADED (Active issues detected)"
            failures_nodes_str = (
                f"- {last_failure_type or 'System Error'}: {last_failure_reason or 'No details'}\n"
                f"  * Status: Active (Unresolved)\n"
                f"  * Timestamp: {last_fail_row[1] if last_fail_row else 'Unknown'}"
            )
        else:
            failures_status_str = "All systems operational"
            failures_nodes_str = "- No active issues detected"
            if last_fail_row:
                failures_nodes_str += f"\n- Last failure: {last_failure_type} ({last_failure_reason})\n  * Status: Resolved (Subsequent goals succeeded)"

        cursor.execute("SELECT metadata FROM execution_ledger WHERE event_type = 'GOAL_RECEIVED' ORDER BY event_id DESC LIMIT 1")
        last_goal_row = cursor.fetchone()
        last_goal = "None"
        if last_goal_row and last_goal_row[0]:
            try:
                meta = json.loads(last_goal_row[0])
                last_goal = meta.get("query") or meta.get("goal") or "System awareness check"
            except Exception:
                pass
                
        cursor.execute("SELECT metadata FROM execution_ledger WHERE event_type = 'GOAL_COMPLETED' ORDER BY event_id DESC LIMIT 1")
        last_completed_row = cursor.fetchone()
        last_completed = "None"
        if last_completed_row and last_completed_row[0]:
            try:
                meta = json.loads(last_completed_row[0])
                last_completed = meta.get("query") or meta.get("goal") or "System awareness check"
            except Exception:
                pass

        cursor.close()
        conn.close()
        db_status = "ONLINE"
    except Exception as e:
        db_status = f"OFFLINE ({str(e)[:40]})"
        total_goals = completed_goals = failed_goals = gov_blocks = 0
        success_rate = success_rate_100 = success_rate_24h = 100.0
        total_tokens = 0
        total_cost = 0.0
        last_goal = last_completed = "Unknown (DB Offline)"
        last_failure = "Unknown (DB Offline)"
        failures_status_str = f"DB Connection Failure: {str(e)[:40]}"
        failures_nodes_str = f"- Error connecting to database: {str(e)}"

    telegram_configured = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    telegram_operational = False
    telegram_reason = "Not configured"
    if telegram_configured:
        if tg_application and getattr(tg_application, "running", False):
            try:
                token = os.environ.get("TELEGRAM_BOT_TOKEN")
                r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=1.5)
                if r.status_code == 200:
                    telegram_operational = True
                    telegram_reason = "Running & connected"
                    LAST_TELEGRAM_SUCCESS_TIME = time.time()
                else:
                    telegram_reason = f"API error (HTTP {r.status_code})"
            except Exception as e:
                telegram_reason = f"API unreachable: {str(e)[:25]}"
        else:
            telegram_reason = "Polling not active"

    fb_configured = bool(os.environ.get("FACEBOOK_PAGE_ID") and os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN"))
    fb_operational = False
    fb_reason = "Not configured"
    if fb_configured:
        try:
            page_id = os.environ.get("FACEBOOK_PAGE_ID")
            page_token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN")
            r = requests.get(f"https://graph.facebook.com/v19.0/{page_id}?access_token={page_token}", timeout=1.5)
            if r.status_code == 200:
                fb_operational = True
                fb_reason = "API authorized"
                LAST_FB_SUCCESS_TIME = time.time()
            else:
                fb_reason = f"API error (HTTP {r.status_code})"
        except Exception as e:
            fb_reason = f"API unreachable: {str(e)[:25]}"

    google_configured = is_google_configured()
    google_operational = False
    google_reason = "Not configured"
    if google_configured:
        try:
            from google_service import get_google_creds
            creds = get_google_creds()
            if creds and (creds.valid or creds.refresh_token):
                google_operational = True
                google_reason = "OAuth Authorized"
                LAST_GOOGLE_SUCCESS_TIME = time.time()
            else:
                google_reason = "Token expired/invalid"
        except Exception as e:
            google_reason = f"Auth check failed: {str(e)[:25]}"

    web_configured = True
    web_operational = False
    web_reason = "Server thread not active"
    for th in threading.enumerate():
        if th.name == "web_dashboard_health_server" and th.is_alive():
            web_operational = True
            web_reason = "Running"
            LAST_WEB_SUCCESS_TIME = time.time()
            break

    uptime_sec = time.time() - BOT_START_TIME
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    secs = int(uptime_sec % 60)
    uptime_parts = []
    if days > 0:
        uptime_parts.append(f"{days}d")
    if hours > 0:
        uptime_parts.append(f"{hours}h")
    if mins > 0:
        uptime_parts.append(f"{mins}m")
    uptime_parts.append(f"{secs}s")
    uptime_str = " ".join(uptime_parts)

    block_rate = (gov_blocks / total_goals * 100) if total_goals > 0 else 0.0
    age_str = get_babu_age_string()

    dashboard = (
        f"=== 📊 SYSTEM HEALTH & SELF-AUDIT DASHBOARD ===\n\n"
        f"**System Health Status**\n"
        f"- Overall Health: {failures_status_str}\n\n"
        f"**Identity**\n"
        f"- Name: Pragya (Project BABU)\n"
        f"- Version: 3.5.0\n"
        f"- System Age: {age_str}\n"
        f"- Current Process Uptime: {uptime_str}\n\n"
        f"**Capabilities**\n"
        f"- Active PA Model: `{CURRENT_PA_MODEL}`\n"
        f"- Active Department Model: `{CURRENT_DEPT_MODEL}`\n\n"
        f"**Services & Transport Layers**\n"
        f"- Telegram:\n"
        f"  * Configured: {'Yes' if telegram_configured else 'No'}\n"
        f"  * Operational: {'Yes' if telegram_operational else 'No'}\n"
        f"  * Reason: {telegram_reason}\n"
        f"  * Last Success: {format_last_success(LAST_TELEGRAM_SUCCESS_TIME)}\n"
        f"- Facebook Publishing:\n"
        f"  * Configured: {'Yes' if fb_configured else 'No'}\n"
        f"  * Operational: {'Yes' if fb_operational else 'No'}\n"
        f"  * Reason: {fb_reason}\n"
        f"  * Last Success: {format_last_success(LAST_FB_SUCCESS_TIME)}\n"
        f"- Google Workspace:\n"
        f"  * Configured: {'Yes' if google_configured else 'No'}\n"
        f"  * Operational: {'Yes' if google_operational else 'No'}\n"
        f"  * Reason: {google_reason}\n"
        f"  * Last Success: {format_last_success(LAST_GOOGLE_SUCCESS_TIME)}\n"
        f"- Web Dashboard:\n"
        f"  * Configured: Yes\n"
        f"  * Operational: {'Yes' if web_operational else 'No'}\n"
        f"  * Reason: {web_reason}\n"
        f"  * Last Success: {format_last_success(LAST_WEB_SUCCESS_TIME)}\n\n"
        f"**Telemetry & Goals**\n"
        f"- Goals Received: {total_goals}\n"
        f"- Completed Goals: {completed_goals}\n"
        f"- Failed Goals: {failed_goals}\n"
        f"- Governance Blocks: {gov_blocks}\n"
        f"- Block Rate: {block_rate:.1f}%\n"
        f"- Lifetime Success Rate: {success_rate:.1f}%\n"
        f"- Last 100 Goals Success Rate: {success_rate_100:.1f}%\n"
        f"- Last 24h Success Rate: {success_rate_24h:.1f}%\n"
        f"- Tokens Processed: {total_tokens:,}\n"
        f"- Cost Incurred: ${total_cost:,.4f}\n\n"
        f"**Recent Activity**\n"
        f"- Last Goal: {last_goal}\n"
        f"- Last Completed Goal: {last_completed}\n"
        f"- Last Failure: {last_failure}\n\n"
        f"**Failure Nodes**\n"
        f"- Status: {failures_status_str}\n"
        f"{failures_nodes_str}"
    )
    return dashboard
