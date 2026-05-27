import asyncio
import json
import os
import re
import sys
import threading
import traceback
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, List, Literal, Optional, TypedDict

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

try:
    from .google_service import execute_google_action, is_google_configured
    from .social_media import run_autonomous_social_post
except ImportError:
    from google_service import execute_google_action, is_google_configured
    from social_media import run_autonomous_social_post
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
PORT            = int(os.environ.get("PORT", 8080))
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")
API_CHAT_TOKEN  = os.environ.get("API_CHAT_TOKEN", "").strip()

CURRENT_PA_MODEL   = "llama-3.1-8b-instant"
CURRENT_DEPT_MODEL = "llama-3.1-8b-instant"

def build_llm(model_name: str, temp: float):
    """Dynamically construct ChatGroq, ChatGoogleGenerativeAI, or ChatOpenAI based on model name, redirecting Gemini and 70b to Groq 8b to prevent rate limits."""
    target_model = model_name
    if "70b" in target_model or target_model.startswith("gemini-"):
        target_model = "llama-3.1-8b-instant"
        print(f"[LLM REDIRECT] Mapping model '{model_name}' to 'llama-3.1-8b-instant' to bypass rate limits.", flush=True)

    if model_name.startswith("gpt-"):
        if not OPENAI_KEY:
            raise ValueError("OPENAI_API_KEY is not configured in environment variables.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, temperature=temp, api_key=OPENAI_KEY)
    else:
        return ChatGroq(model=target_model, temperature=temp)

llm_pa   = build_llm(CURRENT_PA_MODEL,   0.2)
llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)

USER_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")

def load_user_profile() -> dict:
    if os.path.exists(USER_PROFILE_PATH):
        try:
            with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[PROFILE LOAD ERROR] {e}", flush=True)
    return {}

USER_PROFILE = load_user_profile()

def get_user_profile_text() -> str:
    """Return L1 Daily Profile Context (Core identity details for conversational awareness)."""
    if not USER_PROFILE:
        return ""
    
    details = USER_PROFILE.get("personal_details", {})
    business = USER_PROFILE.get("business_context", {})
    prefs = USER_PROFILE.get("preferences", {})
    
    name = details.get("full_name", "") or details.get("primary_nickname", "")
    nickname = details.get("primary_nickname", "")
    
    lines = ["[USER PROFILE & CONTEXT]"]
    if name:
        lines.append(f"  â€¢ User Name: {name} (Nickname: {nickname})" if nickname else f"  â€¢ User Name: {name}")
    if details.get("personal_email"):
        lines.append(f"  â€¢ Personal Email: {details.get('personal_email')}")
    if details.get("official_email"):
        lines.append(f"  â€¢ Official Email: {details.get('official_email')}")
    if business:
        lines.append(f"  â€¢ Business: {business.get('business_name', '')} ({business.get('classification', '')})")
    if prefs:
        lines.append(f"  â€¢ Timezone: {prefs.get('timezone', 'Asia/Kolkata')}")
        lines.append(f"  â€¢ Communication Style: {prefs.get('communication_style', 'Logical and warm')}")
        
    return "\n".join(lines)


def get_profile_fact_answer(query: str) -> str:
    """Deterministic profile answers for high-frequency identity questions."""
    if not USER_PROFILE:
        return ""

    q = (query or "").lower()
    details = USER_PROFILE.get("personal_details", {})
    business = USER_PROFILE.get("business_context", {})
    family = USER_PROFILE.get("family_graph", {})

    full_name = details.get("full_name", "") or details.get("primary_nickname", "")
    nickname = details.get("primary_nickname", "")

    if any(k in q for k in ("who am i", "my name", "who i am", "who am i?")):
        if full_name and nickname:
            return f"You are {full_name}, also known as {nickname}."
        if full_name:
            return f"You are {full_name}."
        return ""

    if any(k in q for k in ("where i work", "where do i work", "my work", "where i am working")):
        business_name = business.get("business_name", "")
        classification = business.get("classification", "")
        if business_name and classification:
            return f"You work at {business_name} ({classification})."
        if business_name:
            return f"You work at {business_name}."
        return ""

    if "father" in q:
        for relation, data in family.items():
            rel = relation.lower().replace("_", " ")
            if "father" in rel and not isinstance(data, (dict, list)):
                return f"Your father's name is {data}."
            if isinstance(data, dict):
                for key, value in data.items():
                    k = key.lower().replace("_", " ")
                    if "father" in k:
                        return f"Your father's name is {value}."
                    if "father" in rel and value:
                        return f"Your father's name is {value}."
        return ""

    return ""


def is_action_status_query(query: str) -> bool:
    q = (query or "").lower()
    markers = (
        "did you send",
        "have you sent",
        "was it sent",
        "is it sent",
        "action triggered",
        "triggered action",
        "mail sent",
        "email sent",
        "did you create",
        "was it created",
    )
    return any(m in q for m in markers)


def search_profile(query: str) -> str:
    """Perform a local directory search on L2 (Family Graph) and L3 (Legacy Memory) to retrieve specific context."""
    if not USER_PROFILE:
        return ""
    
    cleaned = clean_search_query(query)
    q = cleaned.lower().strip()
    
    # Programmatic Me/Myself/I override:
    # If the query is a general question asking about themselves, load the ENTIRE profile history and context!
    personal_pronouns = {"myself", "who am i", "my journey", "my background", "tell me about me", "my profile", "my biography", "my bio", "who is talk", "who is speak"}
    is_general_profile = any(p in q for p in personal_pronouns)
    
    # Programmatic Query vs Statement Classifier:
    # If the user is just sharing a conversational statement, diary entry, or thought, do NOT search the database!
    if not is_general_profile:
        question_starters = (
            "who", "what", "when", "where", "why", "how", "is", "are", "was", "were", 
            "can", "could", "should", "would", "do", "does", "did", "tell", "show", 
            "search", "google", "find", "get", "retrieve", "lookup", "which"
        )
        query_phrases = ["what's", "who's", "where's", "how's", "can you", "could you", "do you know"]
        
        is_inquiry = (
            q.endswith("?") 
            or q.startswith(question_starters) 
            or any(p in q for p in query_phrases)
            or len(q.split()) < 4  # Short keyphrase lookups (e.g. "father name") are treated as queries
        )
        if not is_inquiry:
            # Reassurance: Treated as a conversational statement or diary share. Skip database query!
            return ""

    if is_general_profile:
        results = []
        
        # Load L1 Daily Details
        details = USER_PROFILE.get("personal_details", {})
        if details:
            results.append("Personal Details:")
            for k, v in details.items():
                if v and not str(v).startswith("["):
                    results.append(f"  â€¢ {k.replace('_', ' ').title()}: {v}")
                    
        # Load L2 Family Graph Summary
        family = USER_PROFILE.get("family_graph", {})
        if family:
            results.append("Family structure:")
            for rel, d in family.items():
                if isinstance(d, dict):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in d.items() if v and not str(v).startswith("["))
                    if members:
                        results.append(f"  â€¢ {rel.replace('_', ' ').title()}: {members}")
                        
        # Load L3 Legacy Autobiographical History & Journey
        journey = USER_PROFILE.get("mindset_and_journey", {})
        for cat, det in journey.items():
            results.append(f"{cat.replace('_', ' ').title()} Background:")
            if isinstance(det, dict):
                for k, v in det.items():
                    results.append(f"  â€¢ {k.replace('_', ' ').title()}: {v}")
            else:
                results.append(f"  â€¢ {det}")
                
        edu_career = USER_PROFILE.get("education_and_career", {})
        for edu in edu_career.get("education", []):
            results.append(f"  â€¢ Education Record: {edu}")
        for emp in edu_career.get("employment_history", []):
            results.append(f"  â€¢ Employment Record: {emp}")
            
        return "[Local User Profile (Full Personal Directory Loaded)]\n" + "\n".join(results)

    results = []
    
    # Simple stop-words list to filter out conversational noise
    stopwords = {
        "which", "year", "i", "passed", "grade", "ecam", "exam", "my", "me", "in", "on", 
        "at", "to", "for", "of", "who", "when", "what", "is", "was", "are", "do", "you", 
        "know", "tell", "show", "did", "does", "have", "has", "had", "a", "an", "the", "about"
    }
    search_words = [w for w in q.split() if w not in stopwords and len(w) >= 1]
    
    # 1. Search L2: Family Graph
    family = USER_PROFILE.get("family_graph", {})
    for relation, details in family.items():
        relation_clean = relation.lower().replace("_", " ")
        if isinstance(details, dict):
            for member_key, member_val in details.items():
                member_key_clean = member_key.lower().replace("_", " ")
                # Match if key is in query, or query is in key, or any search word matches key/value
                if member_key_clean in q or q in member_key_clean or any(w in member_key_clean for w in search_words) or any(w in str(member_val).lower() for w in search_words):
                    results.append(f"â€¢ Family Connection ({relation.replace('_', ' ').title()} - {member_key.replace('_', ' ').title()}): {member_val}")
        elif isinstance(details, list):
            for item in details:
                if q in str(item).lower() or any(w in str(item).lower() for w in search_words):
                    results.append(f"â€¢ Family connection ({relation.replace('_', ' ').title()}): {item}")
        else:
            if relation_clean in q or q in relation_clean or any(w in str(details).lower() for w in search_words):
                results.append(f"â€¢ Family connection ({relation.replace('_', ' ').title()}): {details}")
                
    # 2. Search L3: Legacy & Autobiographical Memory
    edu_career = USER_PROFILE.get("education_and_career", {})
    for edu in edu_career.get("education", []):
        edu_str = str(edu).lower()
        if q in edu_str or any(w in edu_str for w in search_words):
            results.append(f"â€¢ Education Record: {edu}")
            
    for emp in edu_career.get("employment_history", []):
        emp_str = str(emp).lower()
        if q in emp_str or any(w in emp_str for w in search_words):
            results.append(f"â€¢ Employment Record: {emp}")
            
    journey = USER_PROFILE.get("mindset_and_journey", {})
    for category, details in journey.items():
        category_clean = category.lower().replace("_", " ")
        if isinstance(details, dict):
            for k, v in details.items():
                k_clean = k.lower().replace("_", " ")
                if k_clean in q or category_clean in q or q in k_clean or q in str(v).lower() or any(w in str(v).lower() for w in search_words):
                    results.append(f"â€¢ Background History ({category.replace('_', ' ').title()} - {k.replace('_', ' ').title()}): {v}")
        else:
            if category_clean in q or q in category_clean or q in str(details).lower() or any(w in str(details).lower() for w in search_words):
                results.append(f"â€¢ Background History ({category.replace('_', ' ').title()}): {details}")
                
    if results:
        return "[Local User Profile Matches]\n" + "\n".join(results)
    return ""

# â”€â”€ Knowledge base â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

KNOWLEDGE_BASE = {
    "aria": (
        "ARIA (Adaptive Research Intelligence Assistant) is a multi-agent AI system "
        "built on LangGraph + Groq/Llama. It routes every query to one of three gears: "
        "WALK (quick replies), SPRINT (3-agent swarm), or LAUNCH (6-agent deep dive). "
        "Available on Telegram and the web."
    ),
    "gears": (
        "WALK: single PA call for casual chat. "
        "SPRINT: Analyst + Skeptic + Strategist in sequence. "
        "LAUNCH: SPRINT + Historian + Futurist + Synthesizer across 2 rounds."
    ),
    "tools": (
        "Every ARIA agent has access to: live web search (DuckDuckGo), "
        "conversation memory (per-session history), the ARIA knowledge base, "
        "and Direct Google Workspace automation (email via Gmail, Calendar events, Sheets logging, and more)."
    ),
    "models": (
        "Router and research agents use llama-3.1-8b-instant (fast). "
        "The Personal Assistant (PA) uses llama-3.3-70b-versatile (highest quality). "
        "All inference runs on Groq free tier."
    ),
}

def clean_search_query(query: str) -> str:
    """Strip Telegram commands and conversational greetings to produce a high-quality search query."""
    # 1. Strip command prefixes like /sprint, /launch, /walk, !sprint, !launch, !walk
    cleaned = re.sub(r'^(?:/[a-zA-Z]+|![a-zA-Z]+)\s*', '', query, flags=re.IGNORECASE)
    cleaned = cleaned.strip()
    
    # 2. Strip conversational introductions/fillers
    patterns = [
        r'^(?:hi|hello|hey|yo|greetings|good\s+morning|good\s+afternoon|good\s+evening)\b[,!\s]*',
        r'^(?:please|kindly|could\s+you\s+please|can\s+you\s+tell\s+me|do\s+you\s+know)\b[,!\s]*',
        r'^(?:tell\s+me|find\s+out|search\s+for|look\s+up)\b[,!\s]*'
    ]
    for pattern in patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE).strip()
        
    return cleaned if cleaned else query


def search_knowledge(query: str) -> str:
    cleaned = clean_search_query(query)
    q = cleaned.lower()
    hits = [v for k, v in KNOWLEDGE_BASE.items() if k in q or any(w in q for w in k.split())]
    return "\n".join(hits) if hits else ""


# â”€â”€ Web search â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def web_search(query: str, max_results: int = 4) -> str:
    cleaned = clean_search_query(query)
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(cleaned, max_results=max_results))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"â€¢ {r['title']}\n  {r['body']}\n  Source: {r['href']}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Search unavailable: {e}]"


# â”€â”€ Direct Google Workspace automation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

# Actions ARIA can detect and trigger
MAKE_ACTIONS = {
    "send_email":       "Send an email via Gmail",
    "create_event":     "Create a Google Calendar event",
    "log_to_sheet":     "Log data to a Google Sheet",
    "create_doc":       "Create a Google Doc",
    "send_slack":       "Send a Slack message",
    "create_task":      "Create a task (Notion / Todoist / Sheets)",
    "copy_photos_to_drive": "Copy photos/videos from Google Photos to Google Drive",
    "copy_contacts_to_drive": "Fetch Google Contacts and write them to a Google Sheet in Google Drive",
    "search_sheet":     "Search for a query or name inside a specific Google Sheet (e.g. Contacts)",
    "search_image":     "Search the web for an image of a given topic and return it",
}


def _normalize_action_name(raw_action) -> str:
    """Reduce arbitrary action payloads to one supported action name."""
    if raw_action is None:
        return ""
    if isinstance(raw_action, list):
        raw_action = raw_action[0] if raw_action else ""
    action_text = str(raw_action).strip().lower()
    if not action_text:
        return ""

    # Handle payloads like "[send_email, create_doc]" or "send_email,create_doc"
    action_text = action_text.strip("[](){}")
    first = re.split(r"[,\s|;/]+", action_text)[0].strip()
    return first if first in MAKE_ACTIONS else ""


def sanitize_single_action_payload(payload: Optional[dict]) -> Optional[dict]:
    """Ensure at most one valid action proceeds per request."""
    if not payload or not isinstance(payload, dict):
        return None
    action_name = _normalize_action_name(payload.get("action"))
    if not action_name:
        return None
    params = payload.get("params", {})
    if not isinstance(params, dict):
        params = {}
    return {"action": action_name, "params": params}

ACTION_DETECTION_PROMPT = (
    "Detect if the user message requests a SINGLE, DIRECT automation action.\n"
    "Actions: send_email, create_event, log_to_sheet, create_doc, send_slack, "
    "create_task, copy_photos_to_drive, copy_contacts_to_drive, search_sheet, search_image\n\n"
    "CRITICAL RULES:\n"
    "- If the query is complex, has multiple steps, requires research, asks for a 'report', or is conversational, reply exactly: NO_ACTION\n"
    "- Do NOT classify goals requiring planning, web search, or synthesis as single actions. Reply NO_ACTION.\n"
    "- Only classify direct, single-action commands (e.g. 'send email to x', 'schedule y', 'search sheet z') as actions.\n\n"
    "If action found, reply with JSON ONLY containing 'action' and 'params' keys.\n"
    "Params by action: send_email(to,subject,body), create_event(title,date,time,duration,description), "
    "log_to_sheet(sheet_name,data), create_doc(title,content), send_slack(channel,message), "
    "create_task(title,due_date,notes), copy_photos_to_drive(category,folder_name), "
    "copy_contacts_to_drive(sheet_name), search_sheet(sheet_name,query), search_image(query)\n\n"
    "If NO action: reply exactly NO_ACTION"
)


ACTION_TRIGGER_WORDS = {
    "send", "mail", "email", "create", "schedule", "log", "post",
    "write", "book", "copy", "search", "find", "make", "add", "save"
}

def detect_action(message: str, history_text: str = "") -> Optional[dict]:
    """Two-stage detection: zero-token keyword gate, then LLM only if needed."""
    # Stage 1: Zero-token gate â€” skip LLM entirely for obvious non-actions
    msg_lower = message.lower()
    if not any(word in msg_lower for word in ACTION_TRIGGER_WORDS):
        return None
    # Stage 2: LLM detection â€” only reached if keyword gate passed
    try:
        content = ""
        profile_text = get_user_profile_text()
        if profile_text:
            content += f"{profile_text}\n\n"
        if history_text:
            content += f"[Recent Conversation History]\n{history_text}\n\n"
        content += f"User's Current Message: {message}"
        
        res = llm_dept.invoke([
            SystemMessage(content=ACTION_DETECTION_PROMPT),
            HumanMessage(content=content),
        ])
        text = res.content.strip()
        if text == "NO_ACTION" or not text.startswith("{"):
            return None
        # Extract JSON even if LLM adds extra text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
        return None
    except Exception:
        return None



# â”€â”€ Memory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_memory_lock = threading.Lock()
_histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))


def get_history_text(session_id: str) -> str:
    with _memory_lock:
        h = list(_histories[session_id])
    if not h:
        return ""
    return "\n".join(f"{role.upper()}: {content}" for role, content in h)


def add_to_history(session_id: str, user_msg: str, aria_msg: str) -> None:
    with _memory_lock:
        _histories[session_id].append(("user", user_msg))
        _histories[session_id].append(("aria", aria_msg))


def extract_tokens(res) -> dict:
    """Safely extract prompt, completion, and total tokens from an LLM response."""
    usage = {"prompt": 0, "completion": 0, "total": 0}
    if not res:
        return usage
        
    # 1. Try unified usage_metadata field (Standard in newer LangChain)
    usage_meta = getattr(res, "usage_metadata", None)
    if usage_meta:
        usage["prompt"] = usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0) or 0
        usage["completion"] = usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0) or 0
        usage["total"] = usage_meta.get("total_tokens", 0) or (usage["prompt"] + usage["completion"])
        return usage

    # 2. Try response_metadata -> token_usage (OpenAI / Groq)
    metadata = getattr(res, "response_metadata", {})
    token_usage = metadata.get("token_usage")
    if token_usage:
        usage["prompt"] = token_usage.get("prompt_tokens", 0)
        usage["completion"] = token_usage.get("completion_tokens", 0)
        usage["total"] = token_usage.get("total_tokens", 0)
        return usage

    return usage


def add_tokens(existing: dict, new: dict) -> dict:
    """LangGraph reducer to sum up cumulative token usage across swarm nodes."""
    if not existing:
        existing = {"prompt": 0, "completion": 0, "total": 0}
    if not new:
        return existing
    return {
        "prompt": existing.get("prompt", 0) + new.get("prompt", 0),
        "completion": existing.get("completion", 0) + new.get("completion", 0),
        "total": existing.get("total", 0) + new.get("total", 0)
    }


# â”€â”€ LangGraph state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class AriaState(TypedDict):
    messages:       Annotated[list[BaseMessage], "Conversation"]
    gear:           Literal["WALK", "SPRINT", "LAUNCH"]
    research_data:  List[str]
    user_query:     str
    history_text:   str
    session_id:     str
    search_results: str
    action_result:  str   # result of direct Google Workspace action if triggered
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


_pending_actions_lock = threading.Lock()
_pending_actions: dict[str, dict] = {}


def _is_approval_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"1", "yes", "approve", "approved", "ok", "confirm", "proceed"}


def _is_reject_message(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"0", "2", "no", "cancel", "reject", "stop"}


# â”€â”€ Nodes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def intent_router(state: AriaState):
    query = state["messages"][-1].content
    history_text = state.get("history_text", "")
    lowered = query.lower().strip()
    session_id = state.get("session_id", "default")

    manual_gear = "WALK"
    if lowered.startswith("/sprint") or lowered.startswith("!sprint"):
        manual_gear = "SPRINT"
    elif lowered.startswith("/launch") or lowered.startswith("!launch"):
        manual_gear = "LAUNCH"
    elif lowered.startswith("/walk") or lowered.startswith("!walk"):
        manual_gear = "WALK"

    detected_action = None
    pending_action_notice = ""

    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending and _is_approval_message(query):
        detected_action = pending
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
    elif pending and _is_reject_message(query):
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        pending_action_notice = "Pending action cancelled."
    elif pending:
        pending_action_notice = "You already have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard."
    else:
        detected_action = sanitize_single_action_payload(detect_action(query, history_text))
        if detected_action:
            with _pending_actions_lock:
                _pending_actions[session_id] = detected_action
            action_name = detected_action.get("action", "unknown_action")
            params = detected_action.get("params", {})
            preview = ", ".join(f"{k}={v}" for k, v in params.items()) if params else "no parameters"
            pending_action_notice = (
                f"Action authorization required.\n\n"
                f"Proposed action: {action_name}\n"
                f"Params: {preview}\n\n"
                "Reply with '1' / 'approve' to execute, or '0' / 'cancel' to reject."
            )
            detected_action = None

    try:
        try:
            from .memory import log_routing_decision
        except ImportError:
            from memory import log_routing_decision
        log_routing_decision(
            session_id=state.get("session_id", "default"),
            query=query,
            selected_gear=manual_gear,
            reason="command_override" if manual_gear != "WALK" else "walk_default",
            has_action=bool(detected_action),
        )
    except Exception as e:
        print(f"[ROUTING MEMORY WARNING] Failed to log routing decision: {e}", flush=True)

    import time
    tracker = state.get("execution_tracker") or {
        "start_time": time.time(),
        "research_duration": 0.0,
        "task_manager_duration": 0.0,
        "action_duration": 0.0
    }
    return {
        "gear": manual_gear,
        "user_query": query,
        "research_data": [],
        "search_results": "",
        "action_result": "",
        "detected_action": detected_action,
        "execution_tracker": tracker,
        "compressed_research": "",
        "routing_metadata": {
            "mode": "command_only",
            "reason": "walk_default" if manual_gear == "WALK" else "explicit_command",
        },
        "pending_action_notice": pending_action_notice,
        "tokens": {"prompt": 0, "completion": 0, "total": 0}
    }


def route_after_router(state: AriaState) -> str:
    notice = state.get("pending_action_notice", "")
    if notice:
        return "pending"
    
    gear = state["gear"]
    detected_action = state.get("detected_action")
    
    if gear == "WALK":
        if detected_action:
            return "walk_action"
        return "walk_direct"
    
    return "plan"


def planner_node(state: AriaState):
    """Decompose user goal into a structured GoalGraph."""
    try:
        from .planner import plan_goal, build_walk_graph, build_action_graph
    except ImportError:
        from planner import plan_goal, build_walk_graph, build_action_graph
        
    query = state["user_query"]
    gear = state["gear"]
    history_text = state.get("history_text", "")
    profile_text = get_user_profile_text()
    
    print(f"[PLANNER NODE] Planning goal for query: '{query[:50]}' with gear: {gear}", flush=True)
    
    if gear == "WALK":
        detected_action = state.get("detected_action")
        if detected_action:
            graph = build_action_graph(query, detected_action)
        else:
            graph = build_walk_graph(query)
    else:
        graph = plan_goal(query, gear, history_text, profile_text)
        
    return {"goal_graph": graph.to_dict()}


def task_executor_node(state: AriaState):
    """Executes the task DAG using TaskEngine and Department Heads."""
    import time
    try:
        from .task_engine import TaskEngine, GoalGraph, TaskState
        from .departments import get_department_head
    except ImportError:
        from task_engine import TaskEngine, GoalGraph, TaskState
        from departments import get_department_head
    
    start_time = time.time()
    
    graph_dict = state.get("goal_graph")
    if not graph_dict:
        try:
            from .planner import build_action_graph
        except ImportError:
            from planner import build_action_graph
        detected_action = state.get("detected_action")
        query = state["user_query"]
        if detected_action:
            graph = build_action_graph(query, detected_action)
            graph_dict = graph.to_dict()
        else:
            return {"action_result": "No executable action found.", "final_brief": "Execution failed: no action found."}
            
    goal_graph = GoalGraph.from_dict(graph_dict)
    engine = TaskEngine(goal_graph)
    
    print(f"[EXECUTOR] Executing goal DAG: {goal_graph.goal_id}", flush=True)
    
    execution_log = state.get("execution_log") or []
    
    # Extract search results, profile slice, etc. for departments
    search_ctx = ""
    kb_ctx = ""
    profile_ctx = ""
    
    has_research_task = any(t.department == "research" for t in goal_graph.tasks)
    if has_research_task:
        query = state["user_query"]
        search_ctx = web_search(query)
        kb_ctx = search_knowledge(query)
        profile_ctx = search_profile(query)
        
    shared_resources = {
        "web_search": search_ctx,
        "knowledge_base": kb_ctx,
        "profile_search": profile_ctx,
        "user_query": state["user_query"]
    }
    
    # Execution loop
    while not engine.is_goal_complete() and not engine.is_goal_blocked():
        ready_tasks = engine.get_ready_tasks()
        if not ready_tasks:
            break
            
        for task in ready_tasks:
            engine.mark_running(task.task_id)
            dept_head = get_department_head(task.department)
            
            # Inject upstream results into context
            completed_results = engine.get_completed_results()
            task.context["upstream_results"] = [
                {"task_id": tid, "result": res}
                for tid, res in completed_results.items()
                if tid in task.depends_on
            ]
            
            try:
                # Dispatch task to the department head
                result = dept_head.dispatch(task, shared_resources, llm_dept)
                engine.mark_completed(task.task_id, result)
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "result": result,
                    "status": "SUCCESS"
                })
            except Exception as e:
                err_msg = str(e)
                print(f"[EXECUTOR ERROR] Task {task.task_id} failed: {err_msg}", flush=True)
                engine.mark_failed(task.task_id, err_msg)
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": err_msg,
                    "status": "FAILED"
                })
                
    # Update tracker
    duration = round(time.time() - start_time, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["task_manager_duration"] = duration
    
    final_brief = engine.get_execution_summary()
    
    # Store action result if there was an execution task
    action_res = ""
    for entry in execution_log:
        if entry["department"] == "execution" and entry["status"] == "SUCCESS":
            action_res = entry.get("result", "")
            
    return {
        "goal_graph": engine.goal.to_dict(),
        "execution_log": execution_log,
        "final_brief": final_brief,
        "action_result": action_res,
        "execution_tracker": tracker
    }


SPRINT_AGENTS = [
    ("ANALYST",    "Hard data, stats, technical context only."),
    ("SKEPTIC",    "Challenge assumptions. Risks and blind spots only."),
    ("STRATEGIST", "Long-term implications and opportunities only."),
]

LAUNCH_ROUND_1 = [
    ("ANALYST",    "Hard data, stats, technical context. Be comprehensive."),
    ("SKEPTIC",    "Challenge every assumption. Risks and failure modes only."),
    ("STRATEGIST", "Long-term implications, second-order effects, opportunities."),
]

LAUNCH_ROUND_2 = [
    ("HISTORIAN",   "Historical precedents and analogies. What patterns apply?"),
    ("FUTURIST",    "5-10 year implications. Disruptive possibilities only."),
    ("SYNTHESIZER", "Read ALL prior reports. Resolve contradictions, surface consensus, give single most important takeaway."),
]


def action_node(state: AriaState):
    """Execute Google Workspace API actions after research runs, resolving research placeholders programmatically."""
    import time
    action_start = time.time()
    action_data = state.get("detected_action")
    if not action_data or "action" not in action_data:
        tracker = state.get("execution_tracker") or {}
        tracker["action_duration"] = 0.0
        return {"action_result": "", "execution_tracker": tracker}

    action = action_data["action"]
    params = action_data.get("params", {})
    
    # Grab compiled research context if any parameter needs it
    research_text = ""
    if state.get("research_data"):
        research_text += "Research Reports:\n" + "\n\n".join(state["research_data"]) + "\n\n"
    if state.get("search_results"):
        research_text += "Live Web Search Results:\n" + state["search_results"]
        
    resolved_params = resolve_action_params(params, research_text=research_text)

    print(f"[GOOGLE] Executing reordered action={action} params={resolved_params}", flush=True)
    ok, msg = execute_google_action(action, resolved_params)
    print(f"[GOOGLE] Result: {ok} - {msg}", flush=True)
    
    duration = round(time.time() - action_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["action_duration"] = duration
    return {"action_result": msg, "execution_tracker": tracker}


def resolve_action_params(params: dict, research_text: str = "") -> dict:
    """Resolve profile placeholders and optional research placeholders."""
    details = USER_PROFILE.get("personal_details", {})
    placeholder_map = {
        "my_official_email": details.get("official_email", ""),
        "my_personal_email": details.get("personal_email", ""),
        "my_mobile": details.get("mobile_number", ""),
        "my_mobile_number": details.get("mobile_number", ""),
        "my_name": details.get("full_name", ""),
        "my_address": details.get("residential_address", {}).get("address", "") if isinstance(details.get("residential_address"), dict) else details.get("residential_address", "")
    }

    resolved_params = {}
    for k, v in (params or {}).items():
        val_str = str(v).strip()
        if val_str in placeholder_map and placeholder_map[val_str]:
            resolved_params[k] = placeholder_map[val_str]
        elif "[NEEDS_RESEARCH_CONTEXT]" in val_str:
            resolved_params[k] = val_str.replace("[NEEDS_RESEARCH_CONTEXT]", research_text.strip() if research_text else "(No research context found)")
        else:
            resolved_params[k] = v
    return resolved_params


def task_manager_node(state: AriaState):
    """Verify research reports and state legitimacy before authorizing tool execution."""
    import time
    tm_start = time.time()
    detected = state.get("detected_action")
    detected = sanitize_single_action_payload(detected)
    active_goal = state.get("active_goal")
    
    if not active_goal:
        active_goal = {
            "goal_id": "goal_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "canonical_instruction": state["user_query"],
            "status": "NEW",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
    if not detected:
        print("[TASK MANAGER] No tool action detected. Bypassing validation.", flush=True)
        active_goal["status"] = "COMPLETED"
        
        duration = round(time.time() - tm_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["task_manager_duration"] = duration
        return {"active_goal": active_goal, "execution_tracker": tracker}
        
    print(f"[TASK MANAGER] Auditing pending action: {detected['action']}...", flush=True)
    
    action_name = detected["action"]
    if action_name not in MAKE_ACTIONS:
        active_goal["status"] = "BLOCKED"
        duration = round(time.time() - tm_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["task_manager_duration"] = duration
        return {
            "detected_action": None,
            "active_goal": active_goal,
            "action_result": f"Action blocked: unsupported action '{action_name}'.",
            "execution_tracker": tracker
        }
    tool_domain = f"action.{action_name}"
    # Fallback to general publisher domain if it is the Facebook publisher
    if action_name == "facebook_publish" or "facebook" in action_name:
        tool_domain = "social_media.facebook_publisher"
        
    try:
        from .memory import get_anti_pattern_rules
    except ImportError:
        from memory import get_anti_pattern_rules
    tool_rules = get_anti_pattern_rules(tool_domain)
    
    if tool_rules:
        print(f"[TASK MANAGER] Auditing constraints for {tool_domain}:\n{tool_rules}", flush=True)
        # If a persistent credential or OAuth block is logged, bypass execution to protect token limits
        if any(word in tool_rules.lower() for word in ["expired", "invalid", "malformed", "bypassed", "quota"]):
            print(f"[TASK MANAGER WARNING] Proactively bypassing '{action_name}' due to persistent historical failure.", flush=True)
            active_goal["status"] = "BYPASSED"
            
            duration = round(time.time() - tm_start, 2)
            tracker = state.get("execution_tracker") or {}
            tracker["task_manager_duration"] = duration
            return {
                "detected_action": None,
                "active_goal": active_goal,
                "action_result": f"Action bypassed by Task Manager due to persistent historical failures:\n{tool_rules}",
                "execution_tracker": tracker
            }
    
    # Validate context-informed parameters
    params = detected.get("params", {})
    has_placeholder = any("[NEEDS_RESEARCH_CONTEXT]" in str(v) for v in params.values())
    
    if has_placeholder:
        # Check if research successfully compiled reports
        has_research = len(state.get("research_data", [])) > 0 or len(state.get("search_results", "").strip()) > 0
        if not has_research:
            print("[TASK MANAGER WARNING] Action requires research context, but research_data is empty! Blocking execution.", flush=True)
            active_goal["status"] = "BLOCKED"
            
            duration = round(time.time() - tm_start, 2)
            tracker = state.get("execution_tracker") or {}
            tracker["task_manager_duration"] = duration
            return {
                "detected_action": None,
                "active_goal": active_goal,
                "action_result": "Action blocked by Task Manager: Missing required research context.",
                "execution_tracker": tracker
            }
        else:
            print("[TASK MANAGER SUCCESS] Research context validated. Authorizing action.", flush=True)
            active_goal["status"] = "VERIFIED"
    else:
        print("[TASK MANAGER SUCCESS] Action requires no research context. Authorizing directly.", flush=True)
        active_goal["status"] = "VERIFIED"
        
    duration = round(time.time() - tm_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["task_manager_duration"] = duration
    return {"active_goal": active_goal, "execution_tracker": tracker}


def research_dept(state: AriaState):
    import time
    research_start = time.time()
    gear = state["gear"]
    query = state["user_query"]

    if gear == "WALK":
        tracker = state.get("execution_tracker") or {}
        tracker["research_duration"] = 0.0
        return {"research_data": [], "search_results": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}, "execution_tracker": tracker}

    print(f"[SEARCH] {query[:60]}", flush=True)
    search_ctx = web_search(query)
    kb_ctx = search_knowledge(query)
    profile_ctx = search_profile(query)
    agent_tokens = []

    def build_task_dto(name: str, role: str, extra_context: str = "") -> dict:
        context = {
            "query": query,
            "datetime_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "constraints": ["be concise", "cite facts from context"],
            "web_search": search_ctx or "",
            "knowledge_base": kb_ctx or "",
        }
        if profile_ctx and any(k in query.lower() for k in ("my", "me", "profile", "family", "career", "education")):
            context["profile_slice"] = profile_ctx
        if extra_context:
            context["prior_round_context"] = extra_context
        return {
            "agent": name,
            "role": role,
            "objective": f"Produce the {name.lower()} perspective for this query.",
            "context": context,
        }

    def run_agent(name: str, role: str, extra_context: str = "") -> str:
        import gc
        if agent_tokens:
            time.sleep(1.2)

        try:
            from .memory import get_anti_pattern_rules
        except ImportError:
            from memory import get_anti_pattern_rules
        anti_patterns = get_anti_pattern_rules(f"swarm_agent.{name.lower()}")
        task_dto = build_task_dto(name, role, extra_context=extra_context)
        system = (
            f"ARIA Swarm [{name}]: {role}\n\n"
            "You receive a scoped task DTO. Use only the provided context and avoid speculation."
        )
        if anti_patterns:
            system += f"\n\n{anti_patterns}"

        user_prompt = f"Task DTO:\n{json.dumps(task_dto, ensure_ascii=False)}"
        res = llm_dept.invoke([SystemMessage(content=system), HumanMessage(content=user_prompt)])
        agent_tokens.append(extract_tokens(res))
        gc.collect()
        return f"[{name}] {res.content}"

    if gear == "SPRINT":
        reports = [run_agent(name, role) for name, role in SPRINT_AGENTS]
        total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        for t in agent_tokens:
            total_tokens["prompt"] += t["prompt"]
            total_tokens["completion"] += t["completion"]
            total_tokens["total"] += t["total"]

        duration = round(time.time() - research_start, 2)
        tracker = state.get("execution_tracker") or {}
        tracker["research_duration"] = duration
        return {"research_data": reports, "search_results": search_ctx, "tokens": total_tokens, "execution_tracker": tracker}

    r1 = [run_agent(name, role) for name, role in LAUNCH_ROUND_1]
    r1_ctx = "\n\n".join(r1)
    r2 = [run_agent(name, role, extra_context=r1_ctx) for name, role in LAUNCH_ROUND_2]
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for t in agent_tokens:
        total_tokens["prompt"] += t["prompt"]
        total_tokens["completion"] += t["completion"]
        total_tokens["total"] += t["total"]

    duration = round(time.time() - research_start, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["research_duration"] = duration
    return {"research_data": r1 + r2, "search_results": search_ctx, "tokens": total_tokens, "execution_tracker": tracker}


def deterministic_compress_reports(reports: List[str], max_chars: int = 2600) -> str:
    if not reports:
        return ""
    compressed = []
    seen = set()
    for idx, report in enumerate(reports, start=1):
        line = " ".join(str(report).split())
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        compressed.append(f"{idx}. {line}")
    merged = "\n".join(compressed)
    if len(merged) > max_chars:
        merged = merged[:max_chars].rsplit(" ", 1)[0] + " ..."
    return merged


def department_synthesizer(state: AriaState):
    reports = state.get("research_data", [])
    return {"compressed_research": deterministic_compress_reports(reports)}

def pa_node(state: AriaState):
    gear          = state["gear"]
    research      = state.get("final_brief") or state.get("compressed_research") or "\n\n".join(state.get("research_data", []))
    history       = state.get("history_text", "")
    action_result = state.get("action_result", "")
    user_query    = state["user_query"]
    pending_action_notice = state.get("pending_action_notice", "")

    if pending_action_notice:
        response = AIMessage(content=pending_action_notice)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Hard guard: if no action was executed this turn, never claim execution.
    if not action_result and is_action_status_query(user_query):
        response_text = "No action was executed in this turn."
        tracker = state.get("execution_tracker", {})
        if tracker and "start_time" in tracker:
            import time
            tot = round(time.time() - tracker["start_time"], 2)
            response_text += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
        response = AIMessage(content=response_text)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Deterministic short-circuit for basic profile facts in WALK mode.
    if gear == "WALK" and not action_result:
        direct_fact = get_profile_fact_answer(user_query)
        if direct_fact:
            tracker = state.get("execution_tracker", {})
            if tracker and "start_time" in tracker:
                import time
                tot = round(time.time() - tracker["start_time"], 2)
                direct_fact += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
            response = AIMessage(content=direct_fact)
            return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Dynamic L2/L3 profile retrieval fallback for the WALK gear:
    # If this is WALK gear and there's no research, query search_profile to fetch matching personal details!
    # This completely avoids running the 3-agent swarm (saving 2,500+ tokens) for simple personal queries.
    if gear == "WALK" and not research:
        profile_ctx = search_profile(state["user_query"])
        if profile_ctx and "[Local User Profile Matches]" in profile_ctx:
            research = profile_ctx

    if gear == "LAUNCH":
        style = "[LAUNCH]\nStructured briefing: ## headers. Cover overview, findings, risks, outlook. End with one concrete recommendation. Dense and precise."
    elif gear == "SPRINT":
        style = "[SPRINT]\nSynthesize concisely - lead with insight, not summary."
    else:
        style = "[WALK]\nBrief, warm, direct. Max two short paragraphs. Confirm any automation action clearly."

    profile_text = get_user_profile_text()
    profile_ctx = f"\n\nUser Profile:\n{profile_text}" if profile_text else ""
    google_tools = ", ".join(MAKE_ACTIONS.keys())
    google_ctx = f"\n\nGoogle Workspace active [{google_tools}]. Confirm any triggered actions clearly."

    try:
        from .memory import get_anti_pattern_rules
    except ImportError:
        from memory import get_anti_pattern_rules
    pa_rules = get_anti_pattern_rules("pa")
    
    # Inject live temporal awareness for PA synthesis
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y, %H:%M UTC")
    manifesto = (
        f"ARIA [{gear}]. Current date/time: {now_str}. Never reveal internal agents. {style}"
        f" Use history for context, never repeat it verbatim."
        f"{google_ctx}{profile_ctx}"
    )
    if not action_result:
        manifesto += "\n\nCRITICAL: Do not claim any action was executed/sent/created in this turn unless [Automation Result] is explicitly present."
    if pa_rules:
        manifesto += "\n\n" + pa_rules

    parts = []
    if history:
        parts.append(f"[Conversation History]\n{history}")
    parts.append(f"User: {state['user_query']}")
    if action_result:
        parts.append(f"[Automation Result]\n{action_result}")
    if research:
        parts.append(f"[Internal Research]\n{research}")

    response = llm_pa.invoke([SystemMessage(content=manifesto), HumanMessage(content="\n\n".join(parts))])
    
    # Programmatic safeguard: ensure [IMAGE] tag is preserved in the response if found in action_result
    if action_result and "[IMAGE]" in action_result:
        # Check if the response already contains the image tag
        if "[IMAGE]" not in response.content:
            # Extract the complete [IMAGE] tag line from action_result
            match = re.search(r'(\[IMAGE\]\s*url=[^\s\n]+(?:\s+caption=[^\n]+)?)', action_result)
            if match:
                response.content += "\n\n" + match.group(1)
                
    # â”€â”€ Performance Telemetry Footnote â”€â”€
    tracker = state.get("execution_tracker", {})
    if tracker and "start_time" in tracker:
        import time
        tot = round(time.time() - tracker["start_time"], 2)
        r = tracker.get("research_duration", 0.0)
        tm = tracker.get("task_manager_duration", 0.0)
        a = tracker.get("action_duration", 0.0)
        telemetry_footnote = f"\n\nSwarm profile: Research {r}s | Audit {tm}s | Action {a}s | Total {tot}s"
        response.content += telemetry_footnote
                
    token_stats = extract_tokens(response)
    try:
        try:
            from .memory import log_workflow_event
        except ImportError:
            from memory import log_workflow_event
        tracker = state.get("execution_tracker", {})
        log_workflow_event(
            session_id=state.get("session_id", "default"),
            gear=gear,
            sequence=["router", "planner", "executor", "pa"] if gear != "WALK" else ["router", "pa"],
            total_tokens=token_stats.get("total", 0),
            latency_seconds=tracker.get("research_duration", 0.0) + tracker.get("task_manager_duration", 0.0) + tracker.get("action_duration", 0.0),
            success=True,
            note=state.get("user_query", "")[:160],
        )
    except Exception as e:
        print(f"[WORKFLOW MEMORY WARNING] Failed to log workflow event: {e}", flush=True)

    return {"messages": state["messages"] + [response], "tokens": token_stats}


# â”€â”€ Graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

workflow = StateGraph(AriaState)
workflow.add_node("router",       intent_router)
workflow.add_node("planner",      planner_node)
workflow.add_node("executor",     task_executor_node)
workflow.add_node("pa",           pa_node)

workflow.set_entry_point("router")
workflow.add_conditional_edges("router", route_after_router, {
    "walk_direct": "pa",
    "walk_action": "executor",
    "plan": "planner",
    "pending": "pa",
})
workflow.add_edge("planner", "executor")
workflow.add_edge("executor", "pa")
workflow.add_edge("pa",           END)
aria_brain = workflow.compile()


# â”€â”€ Core invoke helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def invoke_aria(message: str, session_id: str = "default") -> tuple[str, str, dict]:
    history_text = get_history_text(session_id)
    output = aria_brain.invoke({
        "messages":       [HumanMessage(content=message)],
        "gear":           "WALK",
        "research_data":  [],
        "user_query":     message,
        "history_text":   history_text,
        "session_id":     session_id,
        "search_results": "",
        "action_result":  "",
        "detected_action": None,
        "active_goal":    None,
        "compressed_research": "",
        "routing_metadata": {},
        "pending_action_notice": "",
        "goal_graph":     None,
        "execution_log":  [],
        "final_brief":    "",
        "tokens":         {"prompt": 0, "completion": 0, "total": 0}
    })
    reply = output["messages"][-1].content
    gear  = output.get("gear", "WALK")
    tokens = output.get("tokens", {"prompt": 0, "completion": 0, "total": 0})
    add_to_history(session_id, message, reply)
    return reply, gear, tokens


# â”€â”€ Health / chat HTTP server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARIA â€” AI Assistant</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Segoe UI',sans-serif;background:#0f0f1a;color:#e0e0ff;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#1a1a2e;border:1px solid #2a2a4a;border-radius:16px;padding:48px 56px;text-align:center;max-width:500px;width:90%}
  .dot{width:14px;height:14px;background:#00e676;border-radius:50%;display:inline-block;margin-right:8px;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(0,230,118,.4)}50%{box-shadow:0 0 0 8px rgba(0,230,118,0)}}
  h1{font-size:2.4rem;font-weight:700;letter-spacing:2px;color:#a78bfa;margin:20px 0 8px}
  .sub{color:#888;font-size:.95rem;margin-bottom:32px}
  .badge{display:inline-flex;align-items:center;background:#0d2b1f;border:1px solid #00e676;color:#00e676;border-radius:24px;padding:6px 18px;font-size:.85rem;font-weight:600;margin-bottom:32px}
  .gear{background:#1e1e3a;border-radius:10px;padding:14px 18px;margin:8px 0;text-align:left}
  .gear.launch{background:#1e1028;border:1px solid #7c3aed}
  .gear strong{color:#a78bfa}.gear.launch strong{color:#c084fc}
  .gear span{color:#aaa;font-size:.88rem;margin-left:8px}
  .footer{margin-top:32px;color:#555;font-size:.8rem}
</style>
</head>
<body>
<div class="card">
  <div class="badge"><span class="dot"></span>LIVE</div>
  <h1>ARIA</h1>
  <p class="sub">Multi-Agent AI Assistant</p>
  <div class="gear"><strong>WALK</strong><span>Quick reply + Google automations via Google Workspace APIs</span></div>
  <div class="gear"><strong>SPRINT</strong><span>3-agent swarm + web search</span></div>
  <div class="gear launch"><strong>LAUNCH</strong><span>6-agent deep swarm + web search + synthesis</span></div>
  <p class="footer">Groq &bull; Llama 3 &bull; LangGraph &bull; Memory &bull; Web Search &bull; Google Workspace</p>
</div>
</body>
</html>"""


class HealthHandler(BaseHTTPRequestHandler):

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        if self.path in ("/healthz", "/api/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
        elif self.path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path in ("/healthz", "/api/healthz"):
            body = json.dumps({
                "status": "ok", "bot": "ARIA",
                "features": ["memory", "web_search", "knowledge_base", "google_workspace"],
                "google_configured": bool(is_google_configured()),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(STATUS_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        try:
            if API_CHAT_TOKEN:
                auth_header = str(self.headers.get("Authorization", "")).strip()
                api_key_header = str(self.headers.get("X-API-Key", "")).strip()
                bearer = ""
                if auth_header.lower().startswith("bearer "):
                    bearer = auth_header[7:].strip()
                provided = api_key_header or bearer
                if provided != API_CHAT_TOKEN:
                    err = json.dumps({"error": "unauthorized"}).encode()
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self._cors()
                    self.end_headers()
                    self.wfile.write(err)
                    return

            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            msg    = str(body.get("message", "")).strip()
            sid    = str(body.get("session_id", "web_anon")).strip() or "web_anon"
            if not msg:
                raise ValueError("empty message")
            print(f"[WEB] session={sid[:16]} msg={msg[:80]}", flush=True)
            reply, gear, tokens = invoke_aria(msg, sid)
            print(f"[WEB OK] gear={gear} len={len(reply)} | Tokens: {tokens['total']}", flush=True)
            response = json.dumps({"reply": reply, "gear": gear}).encode()
            self.send_response(200)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(response)))
            self._cors()
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            err = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(err)))
            self._cors()
            self.end_headers()
            self.wfile.write(err)

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HEALTH] Chat API + status on port {PORT}", flush=True)
    server.serve_forever()


# â”€â”€ Autonomous Social Media Scheduler & State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_id.txt")
LAST_POST_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_post_date.txt")
LAST_PREVIEW_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_preview_date.txt")

# State Management for Social Post Previews
PENDING_POSTS = {}      # Map of chat_id (int) -> draft dict
WAITING_FOR_TOPIC = {}  # Map of chat_id (int) -> bool

def get_last_preview_date() -> str:
    """Read the last scheduled preview generation date."""
    if os.path.exists(LAST_PREVIEW_DATE_FILE):
        try:
            with open(LAST_PREVIEW_DATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_preview_date(date_str: str):
    """Write the last scheduled preview generation date."""
    try:
        with open(LAST_PREVIEW_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write last preview date: {e}", flush=True)

def get_post_keyboard() -> InlineKeyboardMarkup:
    """Generate the interactive control panel for social post reviews."""
    keyboard = [
        [
            InlineKeyboardButton("Approve & Publish", callback_data="post_approve"),
            InlineKeyboardButton("Change Topic", callback_data="post_change_topic"),
        ],
        [
            InlineKeyboardButton("Cancel Post", callback_data="post_cancel")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_action_approval_keyboard(session_id: str) -> InlineKeyboardMarkup:
    keyboard = [[
        InlineKeyboardButton("Approve", callback_data=f"action_approve|{session_id}"),
        InlineKeyboardButton("Cancel", callback_data=f"action_cancel|{session_id}")
    ]]
    return InlineKeyboardMarkup(keyboard)

async def generate_and_send_preview(chat_id: int, bot, custom_topic: str = None, reply_to_message_id: int = None):
    """Generate a high-fidelity social media draft and send it to the user for approval."""
    try:
        try:
            from .social_media import generate_social_post_draft
        except ImportError:
            from social_media import generate_social_post_draft
        draft = await asyncio.to_thread(generate_social_post_draft, custom_topic)
        draft["custom_topic"] = custom_topic
        
        # Cache the draft
        PENDING_POSTS[chat_id] = draft
        
        # Send the image preview with interactive keyboard
        with open(draft["image_path"], "rb") as photo_file:
            caption_text = (
                f"ARIA Marketing Department - Post Preview\n\n"
                f"Proposed Caption:\n{escape_markdown(draft['caption'])}\n\n"
                f"FLUX Prompt: \"{escape_markdown(draft['image_prompt'])}\"\n\n"
                f"Please review the graphic and caption below. Click Approve to publish directly to Facebook."
            )
            
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_file,
                caption=caption_text,
                reply_markup=get_post_keyboard(),
                reply_to_message_id=reply_to_message_id
            )
            
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=f"Failed to generate post preview:\n{escape_markdown(str(e))}",
                reply_to_message_id=reply_to_message_id
            )
        except Exception as msg_err:
            print(f"[PREVIEW ERROR] Failed to report error to chat {chat_id}: {msg_err}", flush=True)

def get_persisted_chat_id() -> Optional[int]:
    """Load the user's Telegram chat ID from environment or local state file."""
    env_id = os.environ.get("TELEGRAM_USER_CHAT_ID")
    if env_id:
        try:
            return int(env_id)
        except ValueError:
            pass
            
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return int(content)
        except Exception:
            pass
    return None

def persist_chat_id(chat_id: int):
    """Save the user's Telegram chat ID to local state file."""
    try:
        with open(CHAT_ID_FILE, "w", encoding="utf-8") as f:
            f.write(str(chat_id))
    except Exception as e:
        print(f"[CHAT_ID ERROR] Failed to write chat ID: {e}", flush=True)

def get_last_post_date() -> str:
    """Read the last success post date."""
    if os.path.exists(LAST_POST_DATE_FILE):
        try:
            with open(LAST_POST_DATE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""

def set_last_post_date(date_str: str):
    """Write the last success post date."""
    try:
        with open(LAST_POST_DATE_FILE, "w", encoding="utf-8") as f:
            f.write(date_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to write last post date: {e}", flush=True)

async def scheduler_async_loop(application):
    """Background async event loop for the daily post checking."""
    print("[SCHEDULER] Autonomous social posting scheduler thread started.", flush=True)
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    
    while True:
        try:
            now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
            today_str = now_ist.strftime("%Y-%m-%d")
            
            # Trigger if it is 9:00 AM IST or later, and we haven't sent a preview today yet
            if now_ist.hour >= 9 and get_last_preview_date() != today_str:
                chat_id = get_persisted_chat_id()
                if chat_id:
                    print(f"[SCHEDULER] Triggering scheduled daily post preview for {today_str}...", flush=True)
                    # Mark that we generated/sent preview for today immediately to avoid duplicate runs
                    set_last_preview_date(today_str)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="Scheduled Marketing Swarm engaged. Generating daily custom tech graphic and copywriting..."
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send starting notification: {err}", flush=True)

                    await generate_and_send_preview(chat_id, application.bot)
                else:
                    print("[SCHEDULER] It's time to post, but no Telegram chat ID is registered yet. Waiting for user interaction...", flush=True)
            
        except Exception as e:
            print(f"[SCHEDULER ERROR] Exception in loop: {e}", flush=True)
            traceback.print_exc(file=sys.stdout)
            
        # Wake up and check every 15 minutes (900 seconds)
        await asyncio.sleep(900)

def start_social_scheduler(application):
    """Start the background thread for autonomous social media posting."""
    def run_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(scheduler_async_loop(application))
        
    t = threading.Thread(target=run_loop, daemon=True)
    t.start()


def escape_markdown(text: str) -> str:
    """HARD FIX: Strip risky characters instead of escaping them to prevent Telegram parse errors."""
    return re.sub(r'([_*`\[\]])', '', str(text))


async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force immediately generating and sending today's marketing post preview."""
    chat_id = update.effective_chat.id
    persist_chat_id(chat_id)
    
    custom_topic = " ".join(context.args) if context.args else None
    topic_str = f" for topic: '{custom_topic}'" if custom_topic else ""
    await update.message.reply_text(f"Generating marketing swarm preview{topic_str}... This takes about 15-20 seconds.")
    
    await generate_and_send_preview(chat_id, context.bot, custom_topic=custom_topic, reply_to_message_id=update.message.message_id)


# â”€â”€ Telegram handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def run_aria(update: Update, msg: str, session_id: str):
    if update and update.effective_chat:
        persist_chat_id(update.effective_chat.id)
        
    stop_typing = asyncio.Event()

    async def keep_typing():
        while not stop_typing.is_set():
            try:
                await update.message.chat.send_action("typing")
            except Exception:
                pass
            await asyncio.sleep(4)

    typing_task = asyncio.create_task(keep_typing())
    try:
        reply, gear, tokens = await asyncio.to_thread(invoke_aria, msg, session_id)
        print(f"[TG OK] gear={gear} len={len(reply)} | Tokens: {tokens['total']} (Prompt: {tokens['prompt']}, Comp: {tokens['completion']})", flush=True)
        # Append token usage footnote in Telegram
        if tokens and tokens.get("total", 0) > 0:
            reply += f"\n\n[Tokens: {tokens['total']}]"
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"ARIA error: {e}"
    finally:
        stop_typing.set()
        typing_task.cancel()

    with _pending_actions_lock:
        has_pending = session_id in _pending_actions
    if has_pending and "Action authorization required." in reply:
        await update.message.reply_text(reply, reply_markup=get_action_approval_keyboard(session_id))
        return

    # Check for [IMAGE] tag to reply with a photo
    match = re.search(r'\[IMAGE\]\s*url=([^\s\n]+)(?:\s+caption=(.+))?', reply, re.DOTALL)
    if match:
        url = match.group(1)
        caption = match.group(2) if match.group(2) else ""
        await update.message.reply_photo(photo=url, caption=caption.strip())
        return

    await update.message.reply_text(reply)


def tg_session(update: Update) -> str:
    return f"tg_{update.message.from_user.id}"


def transcribe_audio(file_path: str) -> str:
    """Use Groq's whisper-large-v3 model to transcribe audio files directly."""
    try:
        from groq import Groq
        groq_key = os.environ.get("GROQ_API_KEY")
        if not groq_key:
            return "[Error: GROQ_API_KEY is not configured]"
        
        client = Groq(api_key=groq_key)
        with open(file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(os.path.basename(file_path), file.read()),
                model="whisper-large-v3",
                response_format="text"
            )
        return transcription.strip()
    except Exception as e:
        print(f"[TRANSCRIPTION ERROR] {e}", flush=True)
        return f"[Error transcribing audio: {e}]"


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if WAITING_FOR_TOPIC.get(chat_id):
        topic = update.message.text.strip() if update.message.text else ""
        if not topic:
            return
        
        WAITING_FOR_TOPIC.pop(chat_id, None)
        
        if topic.lower() == 'cancel':
            await update.message.reply_text("Topic change cancelled.")
            return
            
        topic_str = f" for topic: '{topic}'"
        await update.message.reply_text(f"Topic received: \"{topic}\".\nGenerating a new graphic and caption preview. This takes about 15-20 seconds.")
        await generate_and_send_preview(chat_id, context.bot, custom_topic=topic, reply_to_message_id=update.message.message_id)
        return

    # 1. Check if the message is a voice note
    if update.message.voice:
        print(f"[TG VOICE] Received voice note from {update.message.from_user.id}", flush=True)
        await update.message.chat.send_action("record_voice")
        
        try:
            tg_file = await context.bot.get_file(update.message.voice.file_id)
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp:
                temp_path = tmp.name
            
            await tg_file.download_to_drive(temp_path)
            transcribed_text = await asyncio.to_thread(transcribe_audio, temp_path)
            
            try:
                os.remove(temp_path)
            except Exception:
                pass
                
            if not transcribed_text or transcribed_text.startswith("[Error"):
                await update.message.reply_text(f"Voice transcription failed:\n{transcribed_text}")
                return
                
            print(f"[TG VOICE OK] Transcribed: '{transcribed_text}'", flush=True)
            await update.message.reply_text(f"[Voice Command]: \"{transcribed_text}\"")
            await run_aria(update, transcribed_text, tg_session(update))
            
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await update.message.reply_text(f"Voice processing error: {e}")
        return

    # 2. Standard text processing
    msg = update.message.text
    if not msg:
        return
    session_id = tg_session(update)

    with _pending_actions_lock:
        pending = _pending_actions.get(session_id)

    if pending and _is_approval_message(msg):
        with _pending_actions_lock:
            pending = _pending_actions.pop(session_id, None)
        if pending:
            action = pending.get("action", "")
            params = resolve_action_params(pending.get("params", {}), research_text="")
            ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
            status = "Action executed successfully." if ok else "Action execution failed."
            await update.message.reply_text(f"{status}\n\n{result_msg}")
            return

    if pending and _is_reject_message(msg):
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        await update.message.reply_text("Pending action cancelled.")
        return

    if pending and not msg.startswith("/"):
        await update.message.reply_text("You have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard.")
        return

    print(f"[TG MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)
    await run_aria(update, msg, session_id)


async def cmd_walk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /walk <message>")
        return
    await run_aria(update, f"/walk {text}", tg_session(update))


async def cmd_sprint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /sprint <question>")
        return
    await run_aria(update, f"/sprint {text}", tg_session(update))


async def cmd_launch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /launch <complex question>")
        return
    await update.message.reply_text("LAUNCH engaged - 6-agent deep swarm + web search (~30s).")
    await run_aria(update, f"/launch {text}", tg_session(update))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _memory_lock:
        _histories[tg_session(update)].clear()
    await update.message.reply_text("Memory cleared. Fresh start.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    google_line = "\n- Send email, create calendar event, log to sheet - just ask naturally" if is_google_configured() else ""
    await update.message.reply_text(
        "ARIA - Multi-Agent AI Assistant\n\n"
        "Gears:\n"
        "- /walk <msg> - Quick direct reply\n"
        "- /sprint <question> - 3-agent swarm + web search\n"
        "- /launch <question> - 6-agent deep swarm + web search\n\n"
        "Marketing Department:\n"
        "- /postnow - Instantly generate and post custom daily tech graphic & copy to Facebook Page\n\n"
        "Extras:\n"
        "- /clear - Reset conversation memory\n"
        "- /stats - Show runtime diagnostics\n"
        f"- /help - Show this menu{google_line}\n\n"
        "Or just send a message - ARIA routes automatically.\n"
        "I remember your conversation and search the web for research queries.",
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        try:
            from .memory import get_runtime_stats
        except ImportError:
            from memory import get_runtime_stats
        stats = get_runtime_stats(limit=80)
        gears = stats.get("gear_counts", {})
        text = (
            "ARIA Runtime Stats\n\n"
            f"Routing events: {stats.get('routing_events', 0)}\n"
            f"Workflow events: {stats.get('workflow_events', 0)}\n"
            f"Gear usage: WALK={gears.get('WALK', 0)}, SPRINT={gears.get('SPRINT', 0)}, LAUNCH={gears.get('LAUNCH', 0)}\n"
            f"Detected actions: {stats.get('action_detected_count', 0)}\n"
            f"Average tokens: {stats.get('avg_tokens', 0)}\n"
            f"Average latency (s): {stats.get('avg_latency_seconds', 0)}\n"
            f"Workflow success rate: {stats.get('success_rate', 0)}"
        )
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"Stats unavailable: {e}")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global llm_pa, llm_dept, CURRENT_PA_MODEL, CURRENT_DEPT_MODEL

    args = context.args
    if not args:
        menu = (
            "ARIA Model Settings\n\n"
            f"- Current PA (Assistant) Model: `{CURRENT_PA_MODEL}`\n"
            f"- Current Swarm (Research) Model: `{CURRENT_DEPT_MODEL}`\n\n"
            "*Available Models to Switch:*\n"
            "1. `llama-3.3-70b-versatile` (Llama 3.3 - Best Quality)\n"
            "2. `llama-3.1-8b-instant` (Llama 3.1 8B - Fastest / Best Limits)\n"
            "3. `mixtral-8x7b-32768` (Mixtral 8x7B - Great Balance)\n"
            "4. `gemma2-9b-it` (Gemma 2 9B - Fast & Smart)\n"
            "5. `deepseek-r1-distill-llama-70b` (DeepSeek R1 - Deep Reasoning)\n"
            "6. `gemini-2.5-flash` (Gemini 2.5 Flash - Routes to Llama on Groq)\n"
            "7. `gpt-4o-mini` (GPT-4o Mini - Fast & Cheap OpenAI)\n"
            "8. `gpt-4o` (GPT-4o - Flagship OpenAI Intelligence)\n\n"
            "*How to Switch:*\n"
            "- `/model <1-8>` - Change the main Personal Assistant model\n"
            "- `/model swarm <1-8>` - Change the underlying swarm/research model\n\n"
            "Tip: If you encounter rate limits, switch models or let ARIA task manager auto-throttle and balance reasoning."
        )
        await update.message.reply_text(menu, parse_mode="Markdown")
        return

    is_swarm = False
    choice = args[0]
    if choice.lower() == "swarm" and len(args) > 1:
        is_swarm = True
        choice = args[1]

    model_map = {
        "1": "llama-3.3-70b-versatile",
        "2": "llama-3.1-8b-instant",
        "3": "mixtral-8x7b-32768",
        "4": "gemma2-9b-it",
        "5": "deepseek-r1-distill-llama-70b",
        "6": "gemini-2.5-flash",
        "7": "gpt-4o-mini",
        "8": "gpt-4o"
    }

    selected_model = model_map.get(choice)
    if not selected_model:
        if choice in [m for m in model_map.values()]:
            selected_model = choice
        else:
            await update.message.reply_text("Invalid choice. Use `/model` to see valid options.")
            return

    try:
        if is_swarm:
            CURRENT_DEPT_MODEL = selected_model
            llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)
            await update.message.reply_text(f"Swarm/Research model switched to: `{CURRENT_DEPT_MODEL}`")
        else:
            CURRENT_PA_MODEL = selected_model
            llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
            await update.message.reply_text(f"Main Personal Assistant model switched to: `{CURRENT_PA_MODEL}`")
    except Exception as e:
        await update.message.reply_text(f"Failed to switch model: {e}")


async def on_post_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback button clicks (Approve, Change Topic, Cancel) for social post reviews."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data.startswith("action_approve|"):
        session_id = data.split("|", 1)[1].strip()
        with _pending_actions_lock:
            pending = _pending_actions.pop(session_id, None)
        if not pending:
            await query.edit_message_text("No pending action found to approve.")
            return
        action = pending.get("action", "")
        params = resolve_action_params(pending.get("params", {}), research_text="")
        ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
        status = "Action executed successfully." if ok else "Action execution failed."
        await query.edit_message_text(f"{status}\n\n{result_msg}")
        return

    if data.startswith("action_cancel|"):
        session_id = data.split("|", 1)[1].strip()
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        await query.edit_message_text("Pending action cancelled.")
        return
    
    if data == "post_approve":
        draft = PENDING_POSTS.get(chat_id)
        if not draft:
            await query.edit_message_caption(caption="No pending post found to approve. Run /postnow to generate a new draft.")
            return
        
        await query.edit_message_caption(caption="Publishing to Facebook Page. Please wait.")
        
        try:
            from .social_media import publish_to_facebook_page
        except ImportError:
            from social_media import publish_to_facebook_page
        ok, msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
        
        # Log work progress atomically to profile
        try:
            try:
                from .memory import append_to_profile_ledger
            except ImportError:
                from memory import append_to_profile_ledger
            append_to_profile_ledger("work_summaries", {
                "task_name": "Daily FB Marketing Post",
                "status": "SUCCESS" if ok else "FAILED",
                "details": f"Message: {msg} | Topic: {draft.get('custom_topic')}"
            })
        except Exception as e:
            print(f"[CALLBACK WARNING] Failed to write ledger: {e}", flush=True)
            
        if ok:
            # Set last post date if it was scheduled or today
            ist_tz = timezone(timedelta(hours=5, minutes=30))
            today_str = datetime.now(timezone.utc).astimezone(ist_tz).strftime("%Y-%m-%d")
            set_last_post_date(today_str)
            
            # Clean up pending states
            PENDING_POSTS.pop(chat_id, None)
            WAITING_FOR_TOPIC.pop(chat_id, None)
            
            await query.edit_message_caption(
                caption=f"Successfully published to Facebook Page.\n\n{msg}\n\nCaption:\n{escape_markdown(draft['caption'])}"
            )
        else:
            await query.edit_message_caption(
                caption=f"Failed to publish to Facebook:\n{escape_markdown(msg)}\n\nCaption:\n{escape_markdown(draft['caption'])}\n\nYou can click Approve again to retry, Change Topic, or Cancel.",
                reply_markup=get_post_keyboard() # Keep keyboard active so they can try again or change topic!
            )
            
    elif data == "post_change_topic":
        WAITING_FOR_TOPIC[chat_id] = True
        await query.message.reply_text("Please reply with your new custom topic (e.g. health and yoga, cybersecurity tips, computer repair services) to regenerate the post.")
        
    elif data == "post_cancel":
        PENDING_POSTS.pop(chat_id, None)
        WAITING_FOR_TOPIC.pop(chat_id, None)
        await query.edit_message_caption(caption="Post draft cancelled.")


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    google_status = f"Google Workspace ({'active' if is_google_configured() else 'NOT configured'})"
    print(f"--- ARIA IS LIVE | Memory | Web Search | Knowledge Base | {google_status} | WALK + SPRINT + LAUNCH ---", flush=True)
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Start autonomous social media manager scheduler
    start_social_scheduler(bot)
    
    bot.add_handler(CommandHandler("walk",   cmd_walk))
    bot.add_handler(CommandHandler("sprint", cmd_sprint))
    bot.add_handler(CommandHandler("launch", cmd_launch))
    bot.add_handler(CommandHandler("clear",  cmd_clear))
    bot.add_handler(CommandHandler("help",   cmd_help))
    bot.add_handler(CommandHandler("stats",  cmd_stats))
    bot.add_handler(CommandHandler("model",  cmd_model))
    bot.add_handler(CommandHandler("postnow", cmd_postnow))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & (~filters.COMMAND), on_message))
    bot.add_handler(CallbackQueryHandler(on_post_callback))
    bot.run_polling(drop_pending_updates=True)


