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

from google_service import execute_google_action, is_google_configured
from social_media import run_autonomous_social_post
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
PORT            = int(os.environ.get("PORT", 8080))
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")

CURRENT_PA_MODEL   = "llama-3.3-70b-versatile"
CURRENT_DEPT_MODEL = "llama-3.1-8b-instant"

def build_llm(model_name: str, temp: float):
    """Dynamically construct ChatGroq, ChatGoogleGenerativeAI, or ChatOpenAI with automatic Gemini fallback."""
    if model_name.startswith("gemini-"):
        if not GEMINI_KEY:
            raise ValueError("GEMINI_API_KEY is not configured in environment variables.")
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=model_name, temperature=temp, google_api_key=GEMINI_KEY)
    elif model_name.startswith("gpt-"):
        if not OPENAI_KEY:
            raise ValueError("OPENAI_API_KEY is not configured in environment variables.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model_name, temperature=temp, api_key=OPENAI_KEY)
    else:
        main_llm = ChatGroq(model=model_name, temperature=temp)
        if GEMINI_KEY:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                fallback_llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=temp, google_api_key=GEMINI_KEY)
                return main_llm.with_fallbacks(fallbacks=[fallback_llm])
            except Exception as e:
                print(f"[LLM BUILD WARNING] Could not bind Gemini fallback: {e}", flush=True)
                return main_llm
        return main_llm

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
        lines.append(f"  • User Name: {name} (Nickname: {nickname})" if nickname else f"  • User Name: {name}")
    if details.get("personal_email"):
        lines.append(f"  • Personal Email: {details.get('personal_email')}")
    if details.get("official_email"):
        lines.append(f"  • Official Email: {details.get('official_email')}")
    if business:
        lines.append(f"  • Business: {business.get('business_name', '')} ({business.get('classification', '')})")
    if prefs:
        lines.append(f"  • Timezone: {prefs.get('timezone', 'Asia/Kolkata')}")
        lines.append(f"  • Communication Style: {prefs.get('communication_style', 'Logical and warm')}")
        
    return "\n".join(lines)


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
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
        # Load L2 Family Graph Summary
        family = USER_PROFILE.get("family_graph", {})
        if family:
            results.append("Family structure:")
            for rel, d in family.items():
                if isinstance(d, dict):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in d.items() if v and not str(v).startswith("["))
                    if members:
                        results.append(f"  • {rel.replace('_', ' ').title()}: {members}")
                        
        # Load L3 Legacy Autobiographical History & Journey
        journey = USER_PROFILE.get("mindset_and_journey", {})
        for cat, det in journey.items():
            results.append(f"{cat.replace('_', ' ').title()} Background:")
            if isinstance(det, dict):
                for k, v in det.items():
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
            else:
                results.append(f"  • {det}")
                
        edu_career = USER_PROFILE.get("education_and_career", {})
        for edu in edu_career.get("education", []):
            results.append(f"  • Education Record: {edu}")
        for emp in edu_career.get("employment_history", []):
            results.append(f"  • Employment Record: {emp}")
            
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
                    results.append(f"• Family Connection ({relation.replace('_', ' ').title()} - {member_key.replace('_', ' ').title()}): {member_val}")
        elif isinstance(details, list):
            for item in details:
                if q in str(item).lower() or any(w in str(item).lower() for w in search_words):
                    results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {item}")
        else:
            if relation_clean in q or q in relation_clean or any(w in str(details).lower() for w in search_words):
                results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {details}")
                
    # 2. Search L3: Legacy & Autobiographical Memory
    edu_career = USER_PROFILE.get("education_and_career", {})
    for edu in edu_career.get("education", []):
        edu_str = str(edu).lower()
        if q in edu_str or any(w in edu_str for w in search_words):
            results.append(f"• Education Record: {edu}")
            
    for emp in edu_career.get("employment_history", []):
        emp_str = str(emp).lower()
        if q in emp_str or any(w in emp_str for w in search_words):
            results.append(f"• Employment Record: {emp}")
            
    journey = USER_PROFILE.get("mindset_and_journey", {})
    for category, details in journey.items():
        category_clean = category.lower().replace("_", " ")
        if isinstance(details, dict):
            for k, v in details.items():
                k_clean = k.lower().replace("_", " ")
                if k_clean in q or category_clean in q or q in k_clean or q in str(v).lower() or any(w in str(v).lower() for w in search_words):
                    results.append(f"• Background History ({category.replace('_', ' ').title()} - {k.replace('_', ' ').title()}): {v}")
        else:
            if category_clean in q or q in category_clean or q in str(details).lower() or any(w in str(details).lower() for w in search_words):
                results.append(f"• Background History ({category.replace('_', ' ').title()}): {details}")
                
    if results:
        return "[Local User Profile Matches]\n" + "\n".join(results)
    return ""

# ── Knowledge base ─────────────────────────────────────────────────────────

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
        "and Make.com automation (email via Gmail, Calendar events, Sheets logging, and more)."
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


# ── Web search ─────────────────────────────────────────────────────────────

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
            lines.append(f"• {r['title']}\n  {r['body']}\n  Source: {r['href']}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Search unavailable: {e}]"


# ── Make.com automation ────────────────────────────────────────────────────

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

ACTION_DETECTION_PROMPT = """Analyze the user message and decide if it requests an automation action.

Supported actions: send_email, create_event, log_to_sheet, create_doc, send_slack, create_task, copy_photos_to_drive, copy_contacts_to_drive, search_sheet, search_image

If an action is requested, reply with a JSON object ONLY (no other text):
{
  "action": "<action_name>",
  "params": {
    // For send_email: "to", "subject", "body"
    // For create_event: "title", "date", "time", "duration", "description"
    // For log_to_sheet: "sheet_name", "data" (dict of column->value)
    // For create_doc: "title", "content"
    // For send_slack: "channel", "message"
    // For create_task: "title", "due_date", "notes"
    // For copy_photos_to_drive: "category" (either 'DOCUMENTS' for ID cards/docs or 'VIDEO' for videos), "folder_name" (folder where files should be copied in Google Drive)
    // For copy_contacts_to_drive: "sheet_name" (name of Google Sheet to save contacts, e.g. "Contacts")
    // For search_sheet: "sheet_name" (name of Google Sheet to search, e.g. "Contacts"), "query" (term or name to search for, e.g. "Mama Jalaun")
    // For search_image: "query" (image topic or description, e.g. "cute puppy")
  }
}

If NO action is requested, reply with exactly: NO_ACTION"""


def detect_action(message: str, history_text: str = "") -> Optional[dict]:
    """Use LLM to detect if message contains an automation request, resolving context via history and user profile."""
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



# ── Memory ─────────────────────────────────────────────────────────────────

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


# ── LangGraph state ────────────────────────────────────────────────────────

class AriaState(TypedDict):
    messages:       Annotated[list[BaseMessage], "Conversation"]
    gear:           Literal["WALK", "SPRINT", "LAUNCH"]
    research_data:  List[str]
    user_query:     str
    history_text:   str
    session_id:     str
    search_results: str
    action_result:  str   # result of Make.com webhook if triggered
    tokens:         Annotated[dict, add_tokens]


# ── Nodes ──────────────────────────────────────────────────────────────────

def intent_router(state: AriaState):
    query = state["messages"][-1].content
    for cmd in ("/launch", "!launch"):
        if cmd in query.lower():
            return {"gear": "LAUNCH", "user_query": query, "research_data": [], "search_results": "", "action_result": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}}
    if "/sprint" in query.lower():
        return {"gear": "SPRINT", "user_query": query, "research_data": [], "search_results": "", "action_result": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}}
    if "/walk" in query.lower():
        return {"gear": "WALK",   "user_query": query, "research_data": [], "search_results": "", "action_result": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Programmatic search/research routing upgrade:
    # If the user asks for a web search or requests factual timelines/schedules, upgrade to SPRINT
    search_keywords = [
        "search the web", "search for", "look up", "google for", "web search",
        "latest updates", "news about", "ipl", "schedule for", "final date",
        "when is", "what is the date", "who is", "how many", "where is",
        "what's", "what is", "who are", "tell me about", "do you know", 
        "father", "mother", "brother", "sister", "grandfather", "grandmother",
        "education", "job", "career", "history", "school", "college"
    ]
    cleaned_q = query.lower()
    if any(kw in cleaned_q for kw in search_keywords):
        return {"gear": "SPRINT", "user_query": query, "research_data": [], "search_results": "", "action_result": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    routing_prompt = (
        "Classify this query into exactly one tier:\n"
        "- LAUNCH: multi-part, deeply complex, strategic, requires comprehensive analysis\n"
        "- SPRINT: factual, analytical, search, or research questions (asking for facts, dates, news, web lookups)\n"
        "- WALK: casual chat, greetings, simple conversational replies, or direct action requests (sending email, calendar creation, logging to sheets)\n"
        "Reply with just one word: LAUNCH, SPRINT, or WALK."
    )
    res = llm_dept.invoke([HumanMessage(content=f"{routing_prompt}\n\nQuery: {query}")])
    raw = res.content.upper()
    gear = "LAUNCH" if "LAUNCH" in raw else ("SPRINT" if "SPRINT" in raw else "WALK")
    return {"gear": gear, "user_query": query, "research_data": [], "search_results": "", "action_result": "", "tokens": extract_tokens(res)}


SPRINT_AGENTS = [
    ("ANALYST",    "Provide hard data, statistics, and technical context. Be thorough."),
    ("SKEPTIC",    "Challenge assumptions, identify risks, failure modes, and blind spots."),
    ("STRATEGIST", "Map long-term implications and strategic opportunities."),
]

LAUNCH_ROUND_1 = [
    ("ANALYST",    "Provide hard data, statistics, and technical context. Be comprehensive."),
    ("SKEPTIC",    "Challenge every assumption. Identify risks and failure modes."),
    ("STRATEGIST", "Map long-term implications, second-order effects, and opportunities."),
]

LAUNCH_ROUND_2 = [
    ("HISTORIAN",   "Draw on historical precedents and analogies. What patterns apply?"),
    ("FUTURIST",    "Extrapolate 5–10 year implications. What are the disruptive possibilities?"),
    ("SYNTHESIZER", "Read ALL prior agent reports. Resolve contradictions, surface consensus, and give the single most important takeaway."),
]


def action_node(state: AriaState):
    """Detect and execute Google Workspace API actions directly before research runs, resolving placeholders programmatically."""
    query        = state["user_query"]
    history_text = state.get("history_text", "")
    session_id   = state.get("session_id", "default")

    action_data = detect_action(query, history_text)
    if not action_data or "action" not in action_data:
        return {"action_result": ""}

    action = action_data["action"]
    params = action_data.get("params", {})
    
    # Programmatic resolution of L1 private contact details from user_profile.json
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
    for k, v in params.items():
        val_str = str(v).strip()
        if val_str in placeholder_map and placeholder_map[val_str]:
            resolved_params[k] = placeholder_map[val_str]
        else:
            resolved_params[k] = v

    print(f"[GOOGLE] Detected action={action} params={resolved_params}", flush=True)
    ok, msg = execute_google_action(action, resolved_params)
    print(f"[GOOGLE] Result: {msg}", flush=True)
    return {"action_result": msg}


def research_dept(state: AriaState):
    gear  = state["gear"]
    query = state["user_query"]

    if gear == "WALK":
        return {"research_data": [], "search_results": "", "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # ── Magnitude & Domain Triage ──
    q = query.lower()
    personal_keywords = {"my father", "my mother", "my brother", "my sibling", "my parents", "myself", "who am i", "my background", "my education", "my career", "my school", "my job"}
    is_personal = any(kw in q for kw in personal_keywords)
    
    search_keywords = {"search the web", "search for", "google for", "latest updates", "news about", "ipl", "weather in", "when is", "what is the date"}
    is_general_search = any(kw in q for kw in search_keywords)
    
    search_ctx = ""
    profile_ctx = ""
    kb_ctx = ""
    
    if is_personal:
        print("[TRIAGE] Routing to Personal Scoped Memory (Profile lookup only).", flush=True)
        profile_ctx = search_profile(query)
    elif is_general_search:
        print("[TRIAGE] Routing to General Scoped Memory (Web search only).", flush=True)
        search_ctx = web_search(query)
    else:
        print("[TRIAGE] Routing to Multi-Domain Research Memory.", flush=True)
        search_ctx = web_search(query)
        kb_ctx     = search_knowledge(query)
        profile_ctx = search_profile(query)

    # ── Staged Context Compression Gateway ──
    from memory import compress_context_payload
    compressed_search = compress_context_payload(search_ctx, "web search results")
    compressed_profile = compress_context_payload(profile_ctx, "personal profile details")

    shared_ctx = ""
    if kb_ctx:
        shared_ctx += f"[ARIA Knowledge Base]\n{kb_ctx}\n\n"
    if compressed_profile:
        shared_ctx += f"[User Personal Profile Matches (Distilled)]\n{compressed_profile}\n\n"
    if compressed_search:
        shared_ctx += f"[Live Web Search Results (Distilled)]\n{compressed_search}"

    google_tool_desc = (
        "\n\n[Google Workspace Automation Tools available]\n"
        + "\n".join(f"• {k}: {v}" for k, v in MAKE_ACTIONS.items())
    )
    shared_ctx += google_tool_desc

    agent_tokens = []

    def run_agent(name: str, role: str, extra_context: str = "") -> str:
        # Introduce a staggered jitter delay to smooth out API spikes on free-tier limits
        import time
        if agent_tokens:
            print(f"[SWARM JITTER] Staggering agent {name} (sleeping 1.2s)...", flush=True)
            time.sleep(1.2)
            
        # Retrieve logged anti-pattern failures to enforce anti-pattern rules (Failure Ledger)
        from memory import get_anti_pattern_rules
        anti_patterns = get_anti_pattern_rules(f"swarm_agent.{name.lower()}")
        
        profile_text = get_user_profile_text()
        system = (
            f"You are part of the ARIA Research Swarm. Role — {name}: {role}\n\n"
            f"You have access to these tools:\n{shared_ctx}"
        )
        if anti_patterns:
            system += f"\n\n{anti_patterns}"
        if profile_text:
            system += f"\n\n{profile_text}"
        user_prompt = f"Principal's Query: {query}"
        if extra_context:
            user_prompt += f"\n\n--- Prior Research ---\n{extra_context}"
        res = llm_dept.invoke([SystemMessage(content=system), HumanMessage(content=user_prompt)])
        agent_tokens.append(extract_tokens(res))
        return f"[{name}] {res.content}"

    if gear == "SPRINT":
        reports = [run_agent(name, role) for name, role in SPRINT_AGENTS]
        # Aggregate tokens
        total_tokens = {"prompt": 0, "completion": 0, "total": 0}
        for t in agent_tokens:
            total_tokens["prompt"] += t["prompt"]
            total_tokens["completion"] += t["completion"]
            total_tokens["total"] += t["total"]
        return {"research_data": reports, "search_results": search_ctx, "tokens": total_tokens}

    r1     = [run_agent(name, role) for name, role in LAUNCH_ROUND_1]
    r1_ctx = "\n\n".join(r1)
    r2     = [run_agent(name, role, extra_context=r1_ctx) for name, role in LAUNCH_ROUND_2]
    
    # Aggregate tokens
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for t in agent_tokens:
        total_tokens["prompt"] += t["prompt"]
        total_tokens["completion"] += t["completion"]
        total_tokens["total"] += t["total"]
    return {"research_data": r1 + r2, "search_results": search_ctx, "tokens": total_tokens}


def pa_node(state: AriaState):
    gear          = state["gear"]
    research      = "\n\n".join(state["research_data"])
    history       = state.get("history_text", "")
    action_result = state.get("action_result", "")

    # Dynamic L2/L3 profile retrieval fallback for the WALK gear:
    # If this is WALK gear and there's no research, query search_profile to fetch matching personal details!
    # This completely avoids running the 3-agent swarm (saving 2,500+ tokens) for simple personal queries.
    if gear == "WALK" and not research:
        profile_ctx = search_profile(state["user_query"])
        if profile_ctx and "[Local User Profile Matches]" in profile_ctx:
            research = profile_ctx

    if gear == "LAUNCH":
        style = (
            "Always open with [LAUNCH] on its own line.\n"
            "Synthesize into a structured briefing with ## headers.\n"
            "Cover: overview, key findings, risks, strategic outlook.\n"
            "End with one concrete actionable recommendation.\n"
            "Be dense and precise."
        )
    elif gear == "SPRINT":
        style = (
            "Always start with [SPRINT] on its own line.\n"
            "Synthesize concisely — lead with insight, not summary."
        )
    else:
        style = (
            "Always start with [WALK] on its own line.\n"
            "Be brief, warm, and direct. One or two short paragraphs max.\n"
            "If an automation action was taken, confirm it clearly to the user."
        )

    google_ctx = (
        "\n\nYou can trigger Google services directly. If the user asked to send an email, "
        "create a calendar event, log to sheets, etc., confirm it was done (or explain what happened)."
    )

    profile_text = get_user_profile_text()
    profile_ctx = f"\n\nUser Profile Context:\n{profile_text}" if profile_text else ""
    manifesto = (
        f"YOU ARE ARIA — Personal Intelligence System. Gear: [{gear}]\n\n"
        f"Rules:\n"
        f"1. You are the sole interface. Never mention internal agents.\n"
        f"2. {style}\n"
        f"3. Use conversation history for context but don't repeat it verbatim."
        f"{google_ctx}"
        f"{profile_ctx}"
    )

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
                
    return {"messages": state["messages"] + [response], "tokens": extract_tokens(response)}


# ── Graph ──────────────────────────────────────────────────────────────────

# Setup durable SQLite session checkpointer
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "aria_checkpoint.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    memory_checkpointer = SqliteSaver(conn)
    print(f"[SQLITE SESSIONS] Durable session state checker initialized at: {db_path}", flush=True)
except Exception as e:
    print(f"[SQLITE SESSIONS WARNING] SQLite checkpointer failed: {e}. Using in-memory fallback.", flush=True)
    from langgraph.checkpoint.memory import MemorySaver
    memory_checkpointer = MemorySaver()

workflow = StateGraph(AriaState)
workflow.add_node("router",   intent_router)
workflow.add_node("action",   action_node)
workflow.add_node("research", research_dept)
workflow.add_node("pa",       pa_node)
workflow.set_entry_point("router")
workflow.add_edge("router",   "action")
workflow.add_edge("action",   "research")
workflow.add_edge("research", "pa")
workflow.add_edge("pa",       END)
aria_brain = workflow.compile(checkpointer=memory_checkpointer)


# ── Core invoke helper ─────────────────────────────────────────────────────

def invoke_aria(message: str, session_id: str = "default") -> tuple[str, str, dict]:
    history_text = get_history_text(session_id)
    
    # Rolling Context Summarizer Check
    # If the history text exceeds ~3,000 tokens (~12,000 characters), compress it!
    if len(history_text) > 12000:
        print(f"[MEMORY COMPRESSION] Session '{session_id}' exceeds token budget. Summarizing...", flush=True)
        try:
            from memory import compress_context_payload, append_to_profile_ledger
            distilled_summary = compress_context_payload(history_text, f"conversation session {session_id}")
            
            # Commit compressed summary to dynamic ledger under chat_summaries
            append_to_profile_ledger("chat_summaries", {
                "session_topic": f"Auto-archived conversation history for session {session_id}",
                "key_takeaways": distilled_summary,
                "action_items": ["Autosaved context to prevent token degradation"]
            })
            
            # Clear active memory list for this session to flush context
            with _memory_lock:
                _histories[session_id].clear()
                
            # Re-read history_text (it will now be empty)
            history_text = ""
            print(f"[MEMORY COMPRESSION] Session '{session_id}' memory flushed cleanly.", flush=True)
        except Exception as e:
            print(f"[MEMORY COMPRESSION ERROR] Failed to compress session history: {e}", flush=True)
            
    # LangGraph checkpointer session configuration
    config = {"configurable": {"thread_id": session_id}}
    
    output = aria_brain.invoke({
        "messages":       [HumanMessage(content=message)],
        "gear":           "WALK",
        "research_data":  [],
        "user_query":     message,
        "history_text":   history_text,
        "session_id":     session_id,
        "search_results": "",
        "action_result":  "",
        "tokens":         {"prompt": 0, "completion": 0, "total": 0}
    }, config=config)
    reply = output["messages"][-1].content
    gear  = output.get("gear", "WALK")
    tokens = output.get("tokens", {"prompt": 0, "completion": 0, "total": 0})
    add_to_history(session_id, message, reply)
    
    # Asynchronously log telemetry to Google Sheets without blocking the reply speed
    try:
        from google_service import log_telemetry
        threading.Thread(
            target=log_telemetry, 
            args=(session_id, gear, tokens, True, message), 
            daemon=True
        ).start()
    except Exception as te:
        print(f"[TELEMETRY WARNING] Could not queue telemetry log: {te}", flush=True)
        
    return reply, gear, tokens


# ── Health / chat HTTP server ──────────────────────────────────────────────

STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARIA — AI Assistant</title>
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
  <div class="gear"><strong>WALK</strong><span>Quick reply + Google automations via Make.com</span></div>
  <div class="gear"><strong>SPRINT</strong><span>3-agent swarm + web search</span></div>
  <div class="gear launch"><strong>LAUNCH</strong><span>6-agent deep swarm + web search + synthesis</span></div>
  <p class="footer">Groq &bull; Llama 3 &bull; LangGraph &bull; Memory &bull; Web Search &bull; Make.com</p>
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
                "features": ["memory", "web_search", "knowledge_base", "make_automation"],
                "make_configured": bool(MAKE_WEBHOOK),
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


# ── Autonomous Social Media Scheduler & State ──────────────────────────────

CHAT_ID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_id.txt")
LAST_POST_DATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_post_date.txt")

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
            
            # Trigger if it is 9:00 AM IST or later, and we haven't posted today yet
            if now_ist.hour >= 9 and get_last_post_date() != today_str:
                chat_id = get_persisted_chat_id()
                if chat_id:
                    print(f"[SCHEDULER] Triggering scheduled daily post for {today_str}...", flush=True)
                    
                    # Check Google token health proactively
                    try:
                        from google_service import validate_google_token_health
                        validate_google_token_health(TELEGRAM_TOKEN, chat_id)
                    except Exception as he:
                        print(f"[SCHEDULER ERROR] Failed to run token health watchdog: {he}", flush=True)
                        
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="🤖 *Scheduled Marketing Swarm engaged!* Generating daily custom tech graphic and copywriting...",
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send starting notification: {err}", flush=True)

                    # Run posting workflow in thread pool
                    ok, msg, caption, img_path = await asyncio.to_thread(run_autonomous_social_post)
                    
                    if ok:
                        set_last_post_date(today_str)
                        print(f"[SCHEDULER SUCCESS] {msg}", flush=True)
                        try:
                            with open(img_path, "rb") as photo_file:
                                await application.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=photo_file,
                                    caption=f"✅ *Autonomous Daily Marketing Post Live!*\n\n{msg}\n\n📝 *Caption:* \n{caption}",
                                    parse_mode="Markdown"
                                )
                        except Exception as err:
                            print(f"[SCHEDULER ERROR] Failed to send success photo: {err}", flush=True)
                    else:
                        print(f"[SCHEDULER FAILED] {msg}", flush=True)
                        if img_path and os.path.exists(img_path):
                            set_last_post_date(today_str)  # Mark today as run to avoid loop failure spinning
                            try:
                                with open(img_path, "rb") as photo_file:
                                    await application.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=photo_file,
                                        caption=f"⚠️ *Scheduled Marketing Graphic Generated (Not Posted)*\n\nReason: {msg}\n\n📝 *Caption:* \n{caption}\n\n💡 _Tip: Configure FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN on Render!_",
                                        parse_mode="Markdown"
                                    )
                            except Exception as err:
                                print(f"[SCHEDULER ERROR] Failed to send fallback photo: {err}", flush=True)
                        else:
                            try:
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=f"❌ *Scheduled Marketing Swarm failed:*\n{msg}",
                                    parse_mode="Markdown"
                                )
                            except Exception as err:
                                print(f"[SCHEDULER ERROR] Failed to send failure notification: {err}", flush=True)
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

async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force immediately generating and posting today's marketing post."""
    await update.message.reply_text("🤖 *Starting autonomous marketing swarm...* Generating custom tech/business graphic and copy...", parse_mode="Markdown")
    try:
        ok, msg, caption, img_path = await asyncio.to_thread(run_autonomous_social_post)
        if ok:
            with open(img_path, "rb") as photo_file:
                await update.message.reply_photo(
                    photo=photo_file, 
                    caption=f"✅ *Successfully posted to Facebook Page!*\n\n{msg}\n\n📝 *Caption:* \n{caption}",
                    parse_mode="Markdown"
                )
        else:
            if img_path and os.path.exists(img_path):
                with open(img_path, "rb") as photo_file:
                    await update.message.reply_photo(
                        photo=photo_file,
                        caption=f"⚠️ *Marketing Graphic Generated (Not Posted)*\n\nReason: {msg}\n\n📝 *Caption:* \n{caption}\n\n💡 _Tip: Configure FACEBOOK_PAGE_ID and FACEBOOK_PAGE_ACCESS_TOKEN in Render settings to enable auto-posting!_",
                        parse_mode="Markdown"
                    )
            else:
                await update.message.reply_text(f"❌ *Failed to generate marketing post:*\n{msg}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ *Error running marketing swarm:*\n{e}", parse_mode="Markdown")


# ── Telegram handlers ──────────────────────────────────────────────────────

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
            reply += f"\n\n⚡ _[Tokens: {tokens['total']}]_"
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"⚠️ ARIA error: {e}"
    finally:
        stop_typing.set()
        typing_task.cancel()

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
                await update.message.reply_text(f"⚠️ Voice transcription failed:\n{transcribed_text}")
                return
                
            print(f"[TG VOICE OK] Transcribed: '{transcribed_text}'", flush=True)
            await update.message.reply_text(f"🎤 *[Voice Command]*: \"{transcribed_text}\"", parse_mode="Markdown")
            await run_aria(update, transcribed_text, tg_session(update))
            
        except Exception as e:
            traceback.print_exc(file=sys.stdout)
            await update.message.reply_text(f"⚠️ Voice processing error: {e}")
        return

    # 2. Standard text processing
    msg = update.message.text
    if not msg:
        return
    print(f"[TG MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)
    await run_aria(update, msg, tg_session(update))


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
    await update.message.reply_text("🚀 LAUNCH engaged — 6-agent deep swarm + web search. ~30s…")
    await run_aria(update, f"/launch {text}", tg_session(update))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _memory_lock:
        _histories[tg_session(update)].clear()
    await update.message.reply_text("🗑 Memory cleared. Fresh start.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    google_line = "\n• Send email, create calendar event, log to sheet — just ask naturally" if is_google_configured() else ""
    await update.message.reply_text(
        "🤖 *ARIA — Multi-Agent AI Assistant*\n\n"
        "*Gears:*\n"
        "• /walk <msg> — Quick direct reply\n"
        "• /sprint <question> — 3-agent swarm + web search\n"
        "• /launch <question> — 6-agent deep swarm + web search\n\n"
        "*Marketing Department:*\n"
        "• /postnow — Instantly generate and post custom daily tech graphic & copy to Facebook Page\n\n"
        "*Extras:*\n"
        "• /clear — Reset conversation memory\n"
        f"• /help — Show this menu{google_line}\n\n"
        "Or just send a message — ARIA routes automatically.\n"
        "I remember your conversation and search the web for research queries.",
        parse_mode="Markdown",
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global llm_pa, llm_dept, CURRENT_PA_MODEL, CURRENT_DEPT_MODEL

    args = context.args
    if not args:
        menu = (
            "🤖 *ARIA Model Settings*\n\n"
            f"• *Current PA (Assistant) Model:* `{CURRENT_PA_MODEL}`\n"
            f"• *Current Swarm (Research) Model:* `{CURRENT_DEPT_MODEL}`\n\n"
            "*Available Models to Switch:*\n"
            "1️⃣ `llama-3.3-70b-versatile` (Llama 3.3 - Best Quality)\n"
            "2️⃣ `llama-3.1-8b-instant` (Llama 3.1 8B - Fastest / Best Limits)\n"
            "3️⃣ `mixtral-8x7b-32768` (Mixtral 8x7B - Great Balance)\n"
            "4️⃣ `gemma2-9b-it` (Gemma 2 9B - Fast & Smart)\n"
            "5️⃣ `deepseek-r1-distill-llama-70b` (DeepSeek R1 - Deep Reasoning)\n"
            "6️⃣ `gemini-2.5-flash` (Gemini 2.5 Flash - Best Free Capacity!)\n"
            "7️⃣ `gpt-4o-mini` (GPT-4o Mini - Fast & Cheap OpenAI)\n"
            "8️⃣ `gpt-4o` (GPT-4o - Flagship OpenAI Intelligence)\n\n"
            "*How to Switch:*\n"
            "• `/model <1-8>` - Change the main Personal Assistant model\n"
            "• `/model swarm <1-8>` - Change the underlying swarm/research model\n\n"
            "💡 *Tip:* If Groq free tier is exhausted, switch your PA to **6** (Gemini 2.5 Flash) or **7** (GPT-4o Mini) for infinite capacity!"
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
            await update.message.reply_text("❌ Invalid choice. Use `/model` to see the list of valid options.")
            return

    try:
        if is_swarm:
            CURRENT_DEPT_MODEL = selected_model
            llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)
            await update.message.reply_text(f"✅ Swarm/Research model switched to: `{CURRENT_DEPT_MODEL}`")
        else:
            CURRENT_PA_MODEL = selected_model
            llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
            await update.message.reply_text(f"✅ Main Personal Assistant model switched to: `{CURRENT_PA_MODEL}`")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to switch model: {e}")


# ── Entry point ────────────────────────────────────────────────────────────

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
    bot.add_handler(CommandHandler("model",  cmd_model))
    bot.add_handler(CommandHandler("postnow", cmd_postnow))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & (~filters.COMMAND), on_message))
    bot.run_polling(drop_pending_updates=True)
