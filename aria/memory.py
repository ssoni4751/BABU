import os
import sys
import json
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any
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
# Dynamic Test Path isolation to avoid polluting failures.json during unit tests
is_testing = (
    "unittest" in sys.modules 
    or "pytest" in sys.modules 
    or any("test" in arg.lower() for arg in sys.argv)
    or os.environ.get("TESTING") == "true"
)

if is_testing:
    FAILURES_PATH = os.path.join(CURRENT_DIR, "memory", "failures_test.json")
else:
    FAILURES_PATH = os.path.join(CURRENT_DIR, "memory", "failures.json")
LAYERED_MEMORY_DIR = os.path.join(CURRENT_DIR, "memory")
ROUTING_STATS_PATH = os.path.join(LAYERED_MEMORY_DIR, "routing", "routing_stats.json")
WORKFLOW_LOGS_PATH = os.path.join(LAYERED_MEMORY_DIR, "orchestration", "workflows.json")

# Thread Locks for Atomic Writing
PROFILE_LOCK = threading.Lock()
FAILURES_LOCK = threading.Lock()
ROUTING_LOCK = threading.Lock()
WORKFLOW_LOCK = threading.Lock()

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


def send_immune_rule_email(new_rule: dict, all_rules: list) -> None:
    """
    Compiles and sends a structured, concise plain-text report of ARIA's active immune rules
    to the user's official email address. Executed asynchronously to avoid blocking.
    Gated to trigger ONLY on METHODOLOGY and AUDIT failure types.
    """
    # 0. Gate to prevent email notifications during active unit/integration tests
    import sys
    is_testing = (
        "unittest" in sys.modules 
        or "pytest" in sys.modules 
        or any("test" in arg.lower() for arg in sys.argv)
        or os.environ.get("TESTING") == "true"
    )
    if is_testing:
        print("[IMMUNE SYSTEM EMAIL GATE] Bypassing email alerts during active test execution.", flush=True)
        return

    failure_type = new_rule.get("failure_type", "METHODOLOGY")
    
    # 1. Gating to prevent operational/infrastructure noise from emailing
    if failure_type not in ("AUDIT", "METHODOLOGY"):
        print(f"[IMMUNE SYSTEM EMAIL GATE] Skipping email alert for operational/infrastructure failure type: {failure_type}", flush=True)
        return

    print("[IMMUNE SYSTEM EMAIL] Starting background email notification compile...", flush=True)
    try:
        try:
            from google_service import send_gmail
        except ImportError:
            try:
                from aria.google_service import send_gmail
            except ImportError as imp_err:
                print(f"[IMMUNE SYSTEM EMAIL ERROR] Could not import send_gmail: {imp_err}", flush=True)
                return

        # 2. Resolve recipient official email address
        recipient = "anshucomputerorai@gmail.com"
        try:
            if os.path.exists(PROFILE_PATH):
                with open(PROFILE_PATH, "r", encoding="utf-8") as pf:
                    prof_data = json.load(pf)
                    recipient = prof_data.get("personal_details", {}).get("official_email", recipient)
        except Exception as pf_err:
            print(f"[IMMUNE SYSTEM EMAIL WARNING] Failed to read user_profile.json for official email: {pf_err}", flush=True)

        # 3. Extract details of the new sealed rule
        domain_name = new_rule.get("domain", "unknown").upper()
        attempted_method = new_rule.get("attempted_methodology", "unknown")
        observed_consequence = new_rule.get("observed_consequence", "unknown")
        active_anti_pattern_rule = new_rule.get("active_anti_pattern_rule", "unknown")
        timestamp = new_rule.get("timestamp", "")
        goal = new_rule.get("goal", "Daily Autonomous Marketing Post")
        confidence = new_rule.get("confidence", 1.0)
        decay_rate = new_rule.get("decay_rate", 0.15)
        ttl = new_rule.get("ttl_sessions_remaining", 20)

        # 4. Group and summarize all active rules
        active_by_domain = {}
        active_count = 0
        for entry in all_rules:
            conf = entry.get("confidence", 1.0)
            if conf >= 0.25:
                active_count += 1
                dom = str(entry.get("domain", "general")).upper()
                if dom not in active_by_domain:
                    active_by_domain[dom] = []
                active_by_domain[dom].append(entry)

        domain_active_count = len(active_by_domain.get(domain_name, []))

        # 5. Construct concise email body
        subject = f"[ARIA IMMUNE] New Rule Sealed in [{domain_name}]"
        body = (
            "========================================================================\n"
            "🛡️ ARIA COGNITIVE IMMUNE SYSTEM — RULE SEALED\n"
            "========================================================================\n\n"
            "A new cognitive/reasoning anti-pattern rule has been successfully synthesized\n"
            "and committed to failures.json by the Epistemic Immune System.\n\n"
            "🎯 GOAL CONTEXT:\n"
            f"{goal}\n\n"
            "📂 EXECUTION DOMAIN:\n"
            f"{domain_name}\n\n"
            "🏷️ FAILURE TYPE:\n"
            f"{failure_type}\n\n"
            "⚙️ ATTEMPTED METHODOLOGY:\n"
            f"{attempted_method}\n\n"
            "🔍 ROOT CAUSE / CONSEQUENCE:\n"
            f"{observed_consequence}\n\n"
            "🚨 ACTIVE ANTI-PATTERN RULE:\n"
            f">>> {active_anti_pattern_rule} <<<\n\n"
            "🕒 TIMESTAMP:\n"
            f"{timestamp}\n\n"
            "📊 RULE TELEMETRY & STATS:\n"
            f"• Initial Confidence: {confidence}\n"
            f"• Decay Rate: {decay_rate}\n"
            f"• Initial TTL: {ttl} sessions\n"
            f"• Total Sealed Immunity Rules in History: {len(all_rules)}\n"
            f"• Total Active Reasoning Constraints (confidence >= 0.25): {active_count}\n"
            f"• Active Rules in [{domain_name}]: {domain_active_count}\n\n"
            "========================================================================\n"
            "ℹ️ ARIA cognitive OS automatically enforces these negative constraints\n"
            "during future plan-and-decouple execution graphs to eliminate errors.\n"
            "========================================================================\n"
        )

        # 6. Dispatch email
        ok, msg = send_gmail(to=recipient, subject=subject, body=body)
        if ok:
            print(f"[IMMUNE SYSTEM EMAIL SUCCESS] {msg}", flush=True)
        else:
            print(f"[IMMUNE SYSTEM EMAIL FAILED] {msg}", flush=True)

    except Exception as e:
        print(f"[IMMUNE SYSTEM EMAIL EXCEPTION] Failed to construct or send email notification: {e}", flush=True)


def consolidate_failures_semantic(new_entry: dict, existing_failures: list) -> tuple[bool, list]:
    """
    Check if the newly generated failure/rule is semantically similar to any existing rule in the same domain.
    If so, consolidates them by updating the existing rule's confidence and success count, returning True and the updated list.
    Otherwise, returns False and the original list.
    """
    if not existing_failures:
        return False, existing_failures

    # Filter existing rules by the same domain to keep prompt size tiny and focused
    same_domain_failures = [f for f in existing_failures if f.get("domain") == new_entry.get("domain")]
    if not same_domain_failures:
        return False, existing_failures

    # Construct a lightweight mapping of rules for the LLM
    rules_list = []
    for f in same_domain_failures:
        rules_list.append({
            "signature": f.get("failure_signature"),
            "rule": f.get("active_anti_pattern_rule")
        })

    prompt = (
        "You are ARIA's Epistemic Immune System memory compressor. Your job is to check if a new candidate rule "
        "is semantically equivalent to or covered by any existing rule in the same domain. "
        "Specifically, categorize the relationship between the candidate rule and existing rules using one of these outcomes:\n"
        "- \"EXACT_MATCH\": The candidate rule is semantically equivalent to or covers the exact same instructions as an existing rule.\n"
        "- \"COVERED_BY_EXISTING\": The candidate rule's instructions are fully covered or subsumed by a stronger, broader, or more descriptive existing rule.\n"
        "- \"NOVEL\": The candidate rule covers a completely different failure mode or contains novel security/planning constraints not present in the existing rules.\n\n"
        f"Candidate Rule: \"{new_entry['active_anti_pattern_rule']}\"\n\n"
        "Existing Rules:\n"
        f"{json.dumps(rules_list, indent=2)}\n\n"
        "Instructions:\n"
        "- Output a raw JSON object ONLY containing exactly two keys:\n"
        "  * \"outcome\": \"EXACT_MATCH\" | \"COVERED_BY_EXISTING\" | \"NOVEL\"\n"
        "  * \"matched_signature\": The \"signature\" string of the matched existing rule (if outcome is EXACT_MATCH or COVERED_BY_EXISTING), or null.\n"
        "- Output ONLY raw valid JSON. No explanation, no markdown JSON blocks."
    )

    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        try:
            from aria.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback
            
        res = invoke_with_fallback(
            [
                SystemMessage(content="You are ARIA's self-correcting Epistemic Immune System memory deduplicator."),
                HumanMessage(content=prompt)
            ],
            model_name="llama-3.1-8b-instant",  # Fast, cheap, and very capable of simple classification
            temp=0.0,
        )
        
        text = res.content.strip()
        # Clean markdown code blocks if the model wrapped it
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        
        data = json.loads(text)
        outcome = data.get("outcome", "NOVEL")
        matched_sig = data.get("matched_signature")
        
        if outcome in ("EXACT_MATCH", "COVERED_BY_EXISTING") and matched_sig:
            # Update the matched rule in the full failures list
            for f in existing_failures:
                if f.get("failure_signature") == matched_sig:
                    f["success_count"] = f.get("success_count", 0) + 1
                    f["confidence"] = min(1.0, f.get("confidence", 1.0) + 0.05)
                    f["last_reinforced"] = datetime.now(timezone.utc).isoformat()
                    f["timestamp"] = datetime.now(timezone.utc).isoformat()
                    print(f"[IMMUNE SYSTEM] Semantic similarity classified as {outcome}. Consolidated candidate rule into existing rule '{matched_sig}'. Updated confidence: {f['confidence']}", flush=True)
                    return True, existing_failures
    except Exception as e:
        print(f"[IMMUNE SYSTEM WARNING] Semantic deduplication check failed: {e}", flush=True)

    return False, existing_failures


def log_execution_failure(
    domain: str,
    method: str,
    exception_msg: str,
    goal: str = "Daily Autonomous Marketing Post",
    intent_packet: Optional[dict] = None
) -> bool:
    """
    Auto-Immune Failure Logger. Captures a caught exception, automatically
    uses Groq to synthesize a strict anti-pattern rule, and
    appends it directly to failures.json in under 1 second.
    """
    # Gating infrastructure/rate limits/network errors to prevent immune auto-immune contamination
    msg_lower = exception_msg.lower()
    
    # Classify the failure type explicitly for future analytical lookups
    failure_type = "METHODOLOGY"
    
    # Primary check: direct infrastructure signals in the exception message
    infra_rate = ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")
    infra_net = ("timeout", "connection refused", "network", "http error", "503", "502", "504", "socket", "dns", "urllib3", "requests.exceptions", "unreachable", "disconnected")
    infra_res = ("token budget", "token limit", "out of memory", "disk full", "no space", "filesystem", "permission denied")
    
    if any(k in msg_lower for k in infra_rate):
        failure_type = "RATE_LIMIT"
    elif any(k in msg_lower for k in infra_net):
        failure_type = "NETWORK"
    elif any(k in msg_lower for k in infra_res):
        failure_type = "RESOURCE"
    elif "audit" in msg_lower or "compliance" in msg_lower or "checklist" in msg_lower:
        failure_type = "AUDIT"
    # Secondary defense: detect repackaged worker/schema errors that masked the original infra signal
    elif "worker error" in msg_lower or "malformed json" in msg_lower:
        # These are downstream consequences of worker failures.
        # If any infra signal is buried inside the nested error text, gate it.
        all_infra = infra_rate + infra_net + infra_res
        if any(k in msg_lower for k in all_infra):
            failure_type = "RATE_LIMIT"  # Conservative: treat as infra
        else:
            # Even if we can't detect the original signal, a "malformed JSON" from a worker
            # is almost never a true methodology failure — it's a transient LLM output error.
            failure_type = "TRANSIENT_WORKER"
        
    is_infra_failure = failure_type in ("RATE_LIMIT", "NETWORK", "RESOURCE", "TRANSIENT_WORKER")
    if is_infra_failure:
        print(f"[IMMUNE SYSTEM GATE] Bypassing immune learning for {failure_type} exception: {exception_msg}", flush=True)
        return False

    if not GROQ_KEY:
        print("[IMMUNE SYSTEM ERROR] GROQ_API_KEY not configured. Bypassing failure logging.", flush=True)
        return False
        
    print(f"[IMMUNE SYSTEM] Activating diagnostic pass for domain '{domain}'...", flush=True)
    
    # 1. Define standard or governance analysis prompt
    if intent_packet:
        analysis_prompt = (
            f"Analyze this operational failure in ARIA's automated systems at the GOVERNANCE/PLANNING level.\n\n"
            f"• Goal text: {goal}\n"
            f"• Failed Task Objective: {method}\n"
            f"• Failure exception or audit result: {exception_msg}\n"
            f"• Assigned Department: {domain}\n"
            f"• Parent Intent Packet: {json.dumps(intent_packet, indent=2)}\n\n"
            f"Identify if this failure was due to planning template mismatch, intent misclassification, capability leak, or architectural constraint violations.\n"
            f"Provide a structured root cause analysis. You must output a raw JSON object ONLY "
            f"(do not wrap in markdown ```json blocks) containing exactly these three keys:\n"
            f"{{\n"
            f"  \"observed_consequence\": \"A brief summary of the planning/intent root cause of this failure (e.g., 'The intent classifier misclassified the query as LOOKUP instead of RESEARCH, leading to missing analysis tasks').\",\n"
            f"  \"active_anti_pattern_rule\": \"A strict, clear negative constraint instructing the strategic planner or intent classifier what to NEVER attempt in the future to avoid this error (e.g., 'NEVER classify queries containing search comparisons as LOOKUP; always route to RESEARCH' or 'NEVER create writing or analysis tasks under a LOOKUP template').\",\n"
            f"  \"target_domain\": \"governance.classification\" if the error was caused by classifier misclassification, or \"governance.planning\" if caused by planner/template decomposition errors, or standard \"{domain}\" if it is a task-level execution error.\n"
            f"}}"
        )
    else:
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
        from langchain_core.messages import SystemMessage, HumanMessage
        try:
            from aria.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback
        
        observed = exception_msg
        rule = f"CRITICAL DIRECTION: Avoid using methodology {method} under domain {domain} to prevent exception: {exception_msg}"
        
        try:
            # Dynamic LLM routing with auto-failover for failure analysis
            res = invoke_with_fallback(
                [
                    SystemMessage(content="You are ARIA's self-correcting Epistemic Immune System. Distill system errors into highly actionable execution constraints."),
                    HumanMessage(content=analysis_prompt)
                ],
                model_name="llama-3.3-70b-versatile",
                temp=0.2,
            )
            
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
            if intent_packet and "target_domain" in data:
                domain = data["target_domain"]
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
            "last_reinforced": datetime.now(timezone.utc).isoformat(),
            "failure_type": failure_type,
            "goal": goal
        }
        
        # 2. Append atomically using database-aware helpers
        with FAILURES_LOCK:
            failures = _read_json_list(FAILURES_PATH)
            
            # Semantic deduplication pass
            consolidated, updated_failures = consolidate_failures_semantic(failure_entry, failures)
            if consolidated:
                failures = updated_failures
                print(f"[IMMUNE SYSTEM CONSOLIDATED] Consolidated anti-pattern into existing rule signature.", flush=True)
            else:
                failures.append(failure_entry)
            
            _write_json_list(FAILURES_PATH, failures)
            print(f"[IMMUNE SYSTEM SUCCESS] Anti-pattern logged for domain '{domain}': \"{failure_entry['active_anti_pattern_rule']}\"", flush=True)
            
            # Dispatch background email notification
            try:
                email_thread = threading.Thread(
                    target=send_immune_rule_email,
                    args=(failure_entry, failures),
                    daemon=True
                )
                email_thread.start()
                print(f"[IMMUNE SYSTEM EMAIL] Dispatched email notification thread in background.", flush=True)
            except Exception as thread_err:
                print(f"[IMMUNE SYSTEM EMAIL ERROR] Failed to start email notification thread: {thread_err}", flush=True)

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
    with FAILURES_LOCK:
        try:
            failures = _read_json_list(FAILURES_PATH)
            if not failures:
                return
                
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
                
            _write_json_list(FAILURES_PATH, updated_failures)
            
        except Exception as e:
            print(f"[IMMUNE SYSTEM ERROR] Failed to heal anti-patterns: {e}", flush=True)


def get_anti_pattern_rules(domain: str) -> str:
    """Retrieve all logged anti-pattern rules for a specific domain to inject as negative constraints."""
    try:
        failures = _read_json_list(FAILURES_PATH)
        if not failures:
            return ""
            
        rules = []
        for entry in failures:
            if entry.get("domain") == domain and entry.get("confidence", 1.0) >= 0.25:
                rules.append(f"• Previously Failed Method: {entry.get('attempted_methodology')}\n  Observed Issue: {entry.get('observed_consequence')}\n  CRITICAL DIRECTION: {entry.get('active_anti_pattern_rule')}")
                
        if rules:
            return "[CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED]\n" + "\n\n".join(rules)
    except Exception as e:
        print(f"[IMMUNE SYSTEM] Failed to read failures: {e}", flush=True)
    return ""


def get_anti_pattern_rules_for_domains(domains: list) -> str:
    """Retrieve all logged anti-pattern rules for a list of domains to inject as negative constraints."""
    try:
        failures = _read_json_list(FAILURES_PATH)
        if not failures:
            return ""
            
        rules = []
        for entry in failures:
            if entry.get("domain") in domains and entry.get("confidence", 1.0) >= 0.25:
                rules.append(f"• Domain: {entry.get('domain')}\n  Previously Failed Method: {entry.get('attempted_methodology')}\n  Observed Issue: {entry.get('observed_consequence')}\n  CRITICAL DIRECTION: {entry.get('active_anti_pattern_rule')}")
                
        if rules:
            return "[CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED]\n" + "\n\n".join(rules)
    except Exception as e:
        print(f"[IMMUNE SYSTEM] Failed to read failures: {e}", flush=True)
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
        from langchain_core.messages import SystemMessage, HumanMessage
        try:
            from aria.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback
        
        res = invoke_with_fallback(
            [
                SystemMessage(content="You are ARIA's high-speed context compressor. Distill bulk raw data into high-density operational briefs. Be extremely concise."),
                HumanMessage(content=compression_prompt)
            ],
            model_name="llama-3.1-8b-instant",
            temp=0.1,
        )
        
        brief = res.content.strip()
        print(f"[COMPRESSOR SUCCESS] Distillation completed. Brief size: {len(brief)} characters (Saved ~{int((1 - len(brief)/len(raw_text))*100)}% tokens!).", flush=True)
        return brief
        
    except Exception as e:
        print(f"[COMPRESSOR ERROR] Distillation pass failed: {e}. Passing raw text.", flush=True)
        return raw_text


def _read_json_list(path: str) -> list:
    # Use basename of path as key, e.g. "failures" or "routing_stats"
    key = os.path.splitext(os.path.basename(path))[0]
    
    # Try fetching from database if possible
    try:
        from .bot import get_db_connection
    except ImportError:
        try:
            from bot import get_db_connection
        except ImportError:
            get_db_connection = None

    if get_db_connection and not is_testing:
        try:
            conn, is_pg = get_db_connection()
            cursor = conn.cursor()
            placeholder = "%s" if is_pg else "?"
            cursor.execute(f"SELECT data FROM system_memory WHERE key = {placeholder}", (key,))
            res = cursor.fetchone()
            cursor.close()
            conn.close()
            if res and res[0]:
                data = json.loads(res[0])
                return data if isinstance(data, list) else []
        except Exception:
            pass

    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_json_list(path: str, items: list) -> bool:
    file_write_ok = False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, path)
        file_write_ok = True
    except Exception as e:
        print(f"[MEMORY WRITE ERROR] Failed to write {path}: {e}", flush=True)

    # Sync to database permanently
    key = os.path.splitext(os.path.basename(path))[0]
    
    try:
        from .bot import get_db_connection
    except ImportError:
        try:
            from bot import get_db_connection
        except ImportError:
            get_db_connection = None

    if get_db_connection and not is_testing:
        try:
            conn, is_pg = get_db_connection()
            cursor = conn.cursor()
            data_str = json.dumps(items, ensure_ascii=False)
            if is_pg:
                cursor.execute("""
                    INSERT INTO system_memory (key, data) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data
                """, (key, data_str))
            else:
                cursor.execute("""
                    INSERT OR REPLACE INTO system_memory (key, data) VALUES (?, ?)
                """, (key, data_str))
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"[DB MEMORY WRITE ERROR] Failed to write {key} to DB: {e}", flush=True)

    return file_write_ok


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

    with ROUTING_LOCK:
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

    with WORKFLOW_LOCK:
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
    def _run():
        with ROUTING_LOCK:
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
            _write_json_list(ROUTING_STATS_PATH, items)

    threading.Thread(target=_run, daemon=True).start()
    return True


def log_workflow_event(session_id: str, gear: str, sequence: list, total_tokens: int, latency_seconds: float, success: bool, note: str = "", ttl_days: int = 10) -> bool:
    def _run():
        with WORKFLOW_LOCK:
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
            _write_json_list(WORKFLOW_LOGS_PATH, items)

    threading.Thread(target=_run, daemon=True).start()
    return True


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


# Run pruning once in a background thread on startup to keep data clean
try:
    threading.Thread(target=prune_expired_memory_entries, daemon=True).start()
except Exception as e:
    print(f"[MEMORY PRUNING] Failed to spawn background pruning thread: {e}", flush=True)

