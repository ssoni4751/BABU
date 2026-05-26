import os
import sys
import json
import threading
from datetime import datetime, timezone
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

# Thread Locks for Atomic Writing
PROFILE_LOCK = threading.Lock()
FAILURES_LOCK = threading.Lock()

# Load Keys
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

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
    uses Gemini 2.5 Flash to synthesize a strict anti-pattern rule, and
    appends it directly to failures.json in under 1 second.
    """
    if not GEMINI_KEY:
        print("[IMMUNE SYSTEM ERROR] GEMINI_API_KEY not configured. Bypassing failure logging.", flush=True)
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
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage
        
        # Call Gemini 2.5 Flash for high-capacity, free failure distillation
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=GEMINI_KEY, temperature=0.2)
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
        
        failure_entry = {
            "failure_signature": f"{domain.upper()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "domain": domain,
            "attempted_methodology": method,
            "observed_consequence": data.get("observed_consequence", exception_msg),
            "active_anti_pattern_rule": data.get("active_anti_pattern_rule", f"Avoid using {method} due to error: {exception_msg}"),
            "timestamp": datetime.now(timezone.utc).isoformat()
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


def get_anti_pattern_rules(domain: str) -> str:
    """Retrieve all logged anti-pattern rules for a specific domain to inject as negative constraints."""
    if not os.path.exists(FAILURES_PATH):
        return ""
        
    try:
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures = json.load(f)
            
        rules = []
        for entry in failures:
            if entry.get("domain") == domain:
                rules.append(f"• Previously Failed Method: {entry.get('attempted_methodology')}\n  Observed Issue: {entry.get('observed_consequence')}\n  CRITICAL DIRECTION: {entry.get('active_anti_pattern_rule')}")
                
        if rules:
            return "[CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED]\n" + "\n\n".join(rules)
    except Exception as e:
        print(f"[IMMUNE SYSTEM] Failed to read failures.json: {e}", flush=True)
    return ""


def compress_context_payload(raw_text: str, context_topic: str = "general data") -> str:
    """
    Core Staged Compression Gateway. Condenses large, granular text blocks 
    (e.g., raw ddg searches) into highly concentrated briefs using Gemini 2.5 Flash,
    preserving Groq limits and protecting downstream agents' token bounds.
    """
    if not raw_text or not raw_text.strip():
        return ""
        
    if not GEMINI_KEY:
        print("[COMPRESSOR WARNING] GEMINI_API_KEY not configured. Bypassing compression.", flush=True)
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
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import SystemMessage, HumanMessage
        
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key=GEMINI_KEY, temperature=0.1)
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
