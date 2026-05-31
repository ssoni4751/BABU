import os
import sys
import json
import threading
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# Ensure environment variables are loaded
load_dotenv()

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Core Paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE_PATH = os.path.join(CURRENT_DIR, "user_profile.json")
FAILURES_PATH = os.path.join(CURRENT_DIR, "memory", "failures.json")
LAYERED_MEMORY_DIR = os.path.join(CURRENT_DIR, "memory")
ROUTING_STATS_PATH = os.path.join(LAYERED_MEMORY_DIR, "routing", "routing_stats.json")
WORKFLOW_LOGS_PATH = os.path.join(LAYERED_MEMORY_DIR, "orchestration", "workflows.json")

# Thread Locks for Atomic Writing
PROFILE_LOCK = threading.Lock()
FAILURES_LOCK = threading.Lock()

# Load Keys
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")

def append_to_profile_ledger(category: str, entry_data: dict) -> bool:
    """
    Safely reads user_profile.json, appends a structured entry to the
    specified dynamic_memory_ledger category, and flushes it back to disk atomically.
    
    :param category: 'chat_summaries' or 'work_summaries'
    :param entry_data: A structured dictionary matching the ledger schema
    """
    if not os.path.exists(PROFILE_PATH):
        print(f"[MEMORY ERROR] Profile file not found at: {PROFILE_PATH}", flush=True)
        return False

    with PROFILE_LOCK:
        try:
            # 1. Read existing state
            with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                profile = json.load(f)

            # 2. Ensure target dynamic keys exist safely
            if "dynamic_memory_ledger" not in profile:
                profile["dynamic_memory_ledger"] = {}
            if category not in profile["dynamic_memory_ledger"]:
                profile["dynamic_memory_ledger"][category] = []

            # 3. Inject automatic system processing timestamps
            if "timestamp" not in entry_data:
                entry_data["timestamp"] = datetime.now(timezone.utc).isoformat()

            # 4. Append entry to the list
            profile["dynamic_memory_ledger"][category].append(entry_data)

            # 5. Atomic Writeback to prevent data corruption
            temp_path = PROFILE_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, ensure_ascii=False)
            
            os.replace(temp_path, PROFILE_PATH)
            print(f"[MEMORY RETENTION] Successfully logged new entry under '{category}'.", flush=True)
            return True

        except Exception as e:
            print(f"[MEMORY EXCEPTION] Failed to commit to profile ledger: {e}", flush=True)
            if os.path.exists(PROFILE_PATH + ".tmp"):
                try:
                    os.remove(PROFILE_PATH + ".tmp")
                except Exception:
                    pass
            return False


def log_execution_failure(domain: str, method: str, exception_msg: str) -> bool:
    """
    Auto-Immune Failure Logger. Captures a caught exception, automatically
    uses Groq to synthesize a strict anti-pattern rule, and
    appends it directly to failures.json in under 1 second.
    """
    # Gating infrastructure/rate limits/network errors to prevent immune auto-immune contamination
    msg_lower = exception_msg.lower()
    
    # Classify the failure type explicitly for future analytical lookups
    failure_type = "METHODOLOGY"
    if any(k in msg_lower for k in ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")):
        failure_type = "RATE_LIMIT"
    elif any(k in msg_lower for k in ("timeout", "connection refused", "network", "http error", "503", "502", "504", "socket", "dns", "urllib3", "requests.exceptions", "unreachable", "disconnected")):
        failure_type = "NETWORK"
    elif any(k in msg_lower for k in ("token budget", "token limit", "out of memory", "disk full", "no space", "filesystem", "permission denied")):
        failure_type = "RESOURCE"
    elif "audit" in msg_lower or "compliance" in msg_lower or "checklist" in msg_lower:
        failure_type = "AUDIT"
        
    is_infra_failure = failure_type in ("RATE_LIMIT", "NETWORK", "RESOURCE")
    if is_infra_failure:
        print(f"[IMMUNE SYSTEM GATE] Bypassing immune learning for {failure_type} exception: {exception_msg}", flush=True)
        return False

    if not GROQ_KEY:
        print("[IMMUNE SYSTEM ERROR] GROQ_API_KEY not configured. Bypassing failure logging.", flush=True)
        return False
        
    print(f"[IMMUNE SYSTEM] Activating diagnostic pass for domain '{domain}'...", flush=True)
    
    # 1. Define standard analysis prompt
    analysis_prompt = (
        f"Analyze this operational failure in ARIA's automated systems.\n\n"
        f"• Execution Domain: {domain}\n"
        f"• Attempted Method: {method}\n"
        f"• Exception Message: {exception_msg}\n\n"
        f"Provide a structured failure analysis. You must output a raw JSON object ONLY "
        f"(do not wrap in markdown ```json blocks) containing exactly these three keys:\n"
        f"{{\n"
        f"  \"observed_consequence\": \"A brief summary of what went wrong and why.\",\n"
        f"  \"active_anti_pattern_rule\": \"A strict, clear directive/rule instructing the bot what to NEVER attempt in the future to avoid this exact error (e.g., 'NEVER use parent User tokens to post directly, always query /me/accounts for Page Tokens first').\"\n"
        f"}}"
    )
    
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage, HumanMessage
        
        observed = exception_msg
        rule = f"CRITICAL DIRECTION: Avoid using methodology {method} under domain {domain} to prevent exception: {exception_msg}"
        
        try:
            # Call Groq's high-quality llama-3.3-70b-versatile for failures analysis
            llm = ChatGroq(model="llama-3.3-70b-versatile", api_key=GROQ_KEY, temperature=0.2)
            res = llm.invoke([
                SystemMessage(content="You are ARIA's self-correcting Epistemic Immune System. Distill system errors into highly actionable execution constraints."),
                HumanMessage(content=analysis_prompt)
            ])
            
            text = res.content.strip()
            # Clean markdown code blocks if the model wrapped it
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()
            
            data = json.loads(text)
            observed = data.get("observed_consequence", exception_msg)
            rule = data.get("active_anti_pattern_rule", rule)
        except Exception as groq_err:
            print(f"[IMMUNE SYSTEM WARNING] Groq failure analysis failed: {groq_err}. Falling back to rule-based anti-pattern generator.", flush=True)
        
        failure_entry = {
            "failure_signature": f"{domain.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "domain": domain,
            "attempted_methodology": method,
            "observed_consequence": observed,
            "active_anti_pattern_rule": rule,
            "confidence": 1.0,
            "decay_rate": 0.15,
            "success_count": 0,
            "ttl_sessions_remaining": 20,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "failure_type": failure_type
        }
        
        # 2. Append atomically to failures.json
        with FAILURES_LOCK:
            failures = []
            if os.path.exists(FAILURES_PATH):
                try:
                    with open(FAILURES_PATH, "r", encoding="utf-8") as f:
                        failures = json.load(f)
                except Exception:
                    pass
            
            failures.append(failure_entry)
            
            temp_path = FAILURES_PATH + ".tmp"
            # Ensure target directory exists
            os.makedirs(os.path.dirname(FAILURES_PATH), exist_ok=True)
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(failures, f, indent=2, ensure_ascii=False)
                
            os.replace(temp_path, FAILURES_PATH)
            print(f"[IMMUNE SYSTEM SUCCESS] Anti-pattern logged for domain '{domain}': \"{failure_entry['active_anti_pattern_rule']}\"", flush=True)
            return True
            
    except Exception as e:
        print(f"[IMMUNE SYSTEM EXCEPTION] Failure audit failed: {e}", flush=True)
        if os.path.exists(FAILURES_PATH + ".tmp"):
            try:
                os.remove(FAILURES_PATH + ".tmp")
            except Exception:
                pass
        return False


def register_successful_execution(domain: str) -> None:
    """Register successful method run to decay failure rules and heal the immune system."""
    if not os.path.exists(FAILURES_PATH):
        return
        
    with FAILURES_LOCK:
        try:
            with open(FAILURES_PATH, "r", encoding="utf-8") as f:
                failures = json.load(f)
                
            updated_failures = []
            healed_signatures = []
            
            for entry in failures:
                if entry.get("domain") == domain:
                    # Increment success tracking and decay confidence
                    entry["success_count"] = entry.get("success_count", 0) + 1
                    decay = entry.get("decay_rate", 0.15)
                    entry["confidence"] = max(0.0, entry.get("confidence", 1.0) * (1 - decay))
                    entry["ttl_sessions_remaining"] = entry.get("ttl_sessions_remaining", 20) - 1
                    
                    # Pruning threshold check
                    if entry["confidence"] < 0.25 or entry["ttl_sessions_remaining"] <= 0:
                        healed_signatures.append(entry.get("failure_signature"))
                        continue # Rule is wiped completely (healed!)
                        
                updated_failures.append(entry)
                
            if healed_signatures:
                print(f"[IMMUNE SYSTEM] Healed anti-pattern(s) from memory: {', '.join(healed_signatures)}", flush=True)
                
            # Atomic Writeback
            temp_path = FAILURES_PATH + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(updated_failures, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, FAILURES_PATH)
            
        except Exception as e:
            print(f"[IMMUNE SYSTEM ERROR] Failed to heal anti-patterns: {e}", flush=True)
            if os.path.exists(FAILURES_PATH + ".tmp"):
                try:
                    os.remove(FAILURES_PATH + ".tmp")
                except Exception:
                    pass


def get_anti_pattern_rules(domain: str) -> str:
    """Retrieve all logged anti-pattern rules for a specific domain to inject as negative constraints."""
    if not os.path.exists(FAILURES_PATH):
        return ""
        
    try:
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures = json.load(f)
            
        rules = []
        for entry in failures:
            if entry.get("domain") == domain and entry.get("confidence", 1.0) >= 0.25:
                rules.append(f"• Previously Failed Method: {entry.get('attempted_methodology')}\n  Observed Issue: {entry.get('observed_consequence')}\n  CRITICAL DIRECTION: {entry.get('active_anti_pattern_rule')}")
                
        if rules:
            return "[CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED]\n" + "\n\n".join(rules)
    except Exception as e:
        print(f"[IMMUNE SYSTEM] Failed to read failures.json: {e}", flush=True)
    return ""


def compress_context_payload(raw_text: str, context_topic: str = "general data") -> str:
    """
    Core Staged Context Compression. Condenses granular text blocks 
    using Groq's high-speed llama-3.1-8b-instant, protecting downstream agent bounds.
    """
    if not raw_text or not raw_text.strip():
        return ""
        
    if not GROQ_KEY:
        print("[COMPRESSOR WARNING] GROQ_API_KEY not configured. Bypassing compression.", flush=True)
        return raw_text
        
    print(f"[COMPRESSOR] Distilling {len(raw_text)} characters of raw {context_topic}...", flush=True)
    
    compression_prompt = (
        f"Condense the following raw {context_topic} into a highly concentrated, dense, "
        f"and structured executive summary briefing. Remove all repetitive formatting, "
        f"wordy descriptions, and pleasantries. Retain all core facts, data metrics, URLs, "
        f"dates, and specific operational actions.\n\n"
        f"RAW DATA:\n{raw_text}"
    )
    
    try:
        from langchain_groq import ChatGroq
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = ChatGroq(model="llama-3.1-8b-instant", api_key=GROQ_KEY, temperature=0.1)
        res = llm.invoke([
            SystemMessage(content="You are ARIA's high-speed context compressor. Distill bulk raw data into high-density operational briefs. Be extremely concise."),
            HumanMessage(content=compression_prompt)
        ])
        
        brief = res.content.strip()
        print(f"[COMPRESSOR SUCCESS] Distillation completed. Brief size: {len(brief)} characters (Saved ~{int((1 - len(brief)/len(raw_text))*100)}% tokens!).", flush=True)
        return brief
        
    except Exception as e:
        print(f"[COMPRESSOR ERROR] Distillation pass failed: {e}. Passing raw text.", flush=True)
        return raw_text


def _read_json_list(path: str) -> list:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_json_list(path: str, items: list) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        return True
    except Exception as e:
        print(f"[MEMORY WRITE ERROR] Failed to write {path}: {e}", flush=True)
        return False


def _is_expired(entry: dict) -> bool:
    ttl_days = int(entry.get("ttl_days", 10) or 10)
    last_used_raw = entry.get("last_used") or entry.get("timestamp")
    if not last_used_raw:
        return False
    try:
        last_used = datetime.fromisoformat(str(last_used_raw).replace("Z", "+00:00"))
        if not last_used.tzinfo:
            last_used = last_used.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > (last_used + timedelta(days=ttl_days))
    except Exception:
        return False


def prune_expired_memory_entries(default_ttl_days: int = 10) -> dict:
    """Prune stale layered memory entries using TTL metadata (default 10 days)."""
    stats = {"routing_removed": 0, "workflow_removed": 0}

    routing_items = _read_json_list(ROUTING_STATS_PATH)
    kept_routing = []
    for item in routing_items:
        if "ttl_days" not in item:
            item["ttl_days"] = default_ttl_days
        if _is_expired(item):
            stats["routing_removed"] += 1
        else:
            kept_routing.append(item)
    _write_json_list(ROUTING_STATS_PATH, kept_routing)

    workflow_items = _read_json_list(WORKFLOW_LOGS_PATH)
    kept_workflows = []
    for item in workflow_items:
        if "ttl_days" not in item:
            item["ttl_days"] = default_ttl_days
        if _is_expired(item):
            stats["workflow_removed"] += 1
        else:
            kept_workflows.append(item)
    _write_json_list(WORKFLOW_LOGS_PATH, kept_workflows)
    return stats


def log_routing_decision(session_id: str, query: str, selected_gear: str, reason: str, has_action: bool, ttl_days: int = 10) -> bool:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_used": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "query_preview": query[:180],
        "selected_gear": selected_gear,
        "reason": reason,
        "has_action": bool(has_action),
        "ttl_days": ttl_days,
        "confidence": 1.0,
    }
    items = _read_json_list(ROUTING_STATS_PATH)
    items.append(entry)
    ok = _write_json_list(ROUTING_STATS_PATH, items)
    if ok:
        prune_expired_memory_entries(default_ttl_days=ttl_days)
    return ok


def log_workflow_event(session_id: str, gear: str, sequence: list, total_tokens: int, latency_seconds: float, success: bool, note: str = "", ttl_days: int = 10) -> bool:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "last_used": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "gear": gear,
        "sequence": sequence,
        "total_tokens": int(total_tokens or 0),
        "latency_seconds": float(latency_seconds or 0.0),
        "success": bool(success),
        "note": note[:220],
        "ttl_days": ttl_days,
        "confidence": 1.0 if success else 0.5,
    }
    items = _read_json_list(WORKFLOW_LOGS_PATH)
    items.append(entry)
    ok = _write_json_list(WORKFLOW_LOGS_PATH, items)
    if ok:
        prune_expired_memory_entries(default_ttl_days=ttl_days)
    return ok


def get_runtime_stats(limit: int = 50) -> dict:
    """Return lightweight routing/workflow stats for diagnostics surfaces."""
    routing_items = _read_json_list(ROUTING_STATS_PATH)[-limit:]
    workflow_items = _read_json_list(WORKFLOW_LOGS_PATH)[-limit:]

    gear_counts = {"WALK": 0, "SPRINT": 0, "LAUNCH": 0}
    action_count = 0
    for item in routing_items:
        gear = str(item.get("selected_gear", "WALK")).upper()
        if gear in gear_counts:
            gear_counts[gear] += 1
        if item.get("has_action"):
            action_count += 1

    token_total = 0
    latency_total = 0.0
    success_total = 0
    for item in workflow_items:
        token_total += int(item.get("total_tokens", 0) or 0)
        latency_total += float(item.get("latency_seconds", 0.0) or 0.0)
        if item.get("success"):
            success_total += 1

    workflow_count = len(workflow_items)
    return {
        "routing_events": len(routing_items),
        "workflow_events": workflow_count,
        "gear_counts": gear_counts,
        "action_detected_count": action_count,
        "avg_tokens": round(token_total / workflow_count, 2) if workflow_count else 0,
        "avg_latency_seconds": round(latency_total / workflow_count, 2) if workflow_count else 0.0,
        "success_rate": round(success_total / workflow_count, 3) if workflow_count else 0.0,
    }
