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
import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "aria_checkpoint.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_durable_checkpoint_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sealed_epochs (
            epoch_id TEXT PRIMARY KEY,
            sealed_at TEXT
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            query_hash TEXT PRIMARY KEY,
            raw_query TEXT,
            distilled_results TEXT,
            sources TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS execution_ledger (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            goal_id TEXT NOT NULL,
            task_id TEXT,
            department TEXT,
            event_type TEXT NOT NULL,
            state_before TEXT,
            state_after TEXT,
            metadata TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    return conn

try:
    db_conn = init_durable_checkpoint_db()
    checkpointer = SqliteSaver(db_conn)
except Exception as e:
    print(f"[CHECKPOINTER WARNING] Failed to initialize SqliteSaver: {e}", flush=True)
    checkpointer = None

def is_epoch_sealed(epoch_id: str) -> bool:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM sealed_epochs WHERE epoch_id = ?", (epoch_id,))
        res = cursor.fetchone()
        conn.close()
        return bool(res)
    except Exception as e:
        print(f"[DB ERROR] is_epoch_sealed failed: {e}", flush=True)
        return False

def seal_epoch(epoch_id: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO sealed_epochs (epoch_id, sealed_at) VALUES (?, ?)",
            (epoch_id, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()
        print(f"[GOVERNANCE] Epoch '{epoch_id}' successfully sealed.", flush=True)
    except Exception as e:
        print(f"[DB ERROR] seal_epoch failed: {e}", flush=True)


def get_last_goal_graph(session_id: str) -> Optional[dict]:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT metadata FROM execution_ledger
            WHERE session_id = ? AND event_type = 'PLANNING'
            ORDER BY event_id DESC LIMIT 1
        """, (session_id,))
        res = cursor.fetchone()
        conn.close()
        if res and res[0]:
            meta = json.loads(res[0])
            return meta.get("graph")
    except Exception as e:
        print(f"[DB ERROR] get_last_goal_graph failed: {e}", flush=True)
    return None

# ── Part 3: State Execution Ledger Helpers ───────────────────────────────

def log_execution_ledger_event(session_id: str, goal_id: str, task_id: Optional[str], department: Optional[str], event_type: str, state_before: Optional[str] = None, state_after: Optional[str] = None, metadata: Optional[dict] = None):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        meta_str = json.dumps(metadata) if metadata else None
        cursor.execute("""
            INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (session_id, goal_id, task_id, department, event_type, state_before, state_after, meta_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] log_execution_ledger_event failed: {e}", flush=True)

# ── Part 2: Stateful Search Cache Helpers ───────────────────────────────────

import hashlib

def _query_hash(query: str) -> str:
    return hashlib.sha256(query.lower().strip().encode("utf-8")).hexdigest()

def get_cached_search(query: str, ttl_hours: float = 12.0) -> Optional[dict]:
    try:
        q_hash = _query_hash(query)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT distilled_results, sources, created_at 
            FROM search_cache 
            WHERE query_hash = ? AND (strftime('%s', 'now') - strftime('%s', created_at)) < ?
        """, (q_hash, ttl_hours * 3600))
        res = cursor.fetchone()
        conn.close()
        if res:
            return {"results": res[0], "sources": res[1]}
    except Exception as e:
        print(f"[DB ERROR] get_cached_search failed: {e}", flush=True)
    return None

def store_cached_search(query: str, distilled_results: str, sources: str):
    try:
        q_hash = _query_hash(query)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO search_cache (query_hash, raw_query, distilled_results, sources, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (q_hash, query, distilled_results, sources))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] store_cached_search failed: {e}", flush=True)

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
CURRENT_DEPT_MODEL = "llama-3.3-70b-versatile"

def build_llm(model_name: str, temp: float):
    """Dynamically construct ChatGroq, ChatGoogleGenerativeAI, or ChatOpenAI based on model name and available credentials."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    target_model = model_name.strip()

    # 1. Google Gemini Native Support
    if target_model.startswith("gemini-"):
        if gemini_key:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=target_model, temperature=temp, google_api_key=gemini_key)
        elif openrouter_key:
            or_model = f"google/{target_model}"
            print(f"[LLM FALLBACK] Gemini key missing. Routing '{target_model}' through OpenRouter as '{or_model}'.", flush=True)
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=or_model,
                temperature=temp,
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1"
            )
        else:
            fallback = "llama-3.1-8b-instant"
            print(f"[LLM REDIRECT] Both Gemini and OpenRouter keys missing. Mapping '{target_model}' to Groq '{fallback}'.", flush=True)
            return ChatGroq(model=fallback, temperature=temp, api_key=groq_key)

    # 2. OpenRouter Support (Any model containing '/' or starting with 'openrouter/')
    elif "/" in target_model or target_model.startswith("openrouter/"):
        clean_model = target_model.replace("openrouter/", "")
        if not openrouter_key:
            raise ValueError("OPENROUTER_API_KEY is not configured in environment variables.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=clean_model,
            temperature=temp,
            api_key=openrouter_key,
            base_url="https://openrouter.ai/api/v1"
        )

    # 3. OpenAI Native Support
    elif target_model.startswith("gpt-"):
        if not openai_key:
            raise ValueError("OPENAI_API_KEY is not configured in environment variables.")
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=target_model, temperature=temp, api_key=openai_key)

    # 4. Default: Groq Support
    else:
        return ChatGroq(model=target_model, temperature=temp, api_key=groq_key)

llm_pa   = build_llm(CURRENT_PA_MODEL,   0.2)
llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)

# ---------------------------------------------------------------------------
# Provider-level auto-failover for rate limits
# ---------------------------------------------------------------------------

# Map Groq models to their OpenRouter equivalents
_FALLBACK_CHAIN = {
    "llama-3.1-8b-instant":    ["meta-llama/llama-3.1-8b-instruct", "google/gemini-2.0-flash-001"],
    "llama-3.3-70b-versatile": ["meta-llama/llama-3.3-70b-instruct", "google/gemini-2.0-flash-001"],
}
_RATE_LIMIT_SIGNALS = ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")


def invoke_with_fallback(messages, model_name: str, temp: float):
    """Invoke an LLM with automatic provider failover on rate limits.

    Tries the primary model first. If it hits a 429/rate-limit error,
    automatically retries through alternative providers (OpenRouter → Gemini)
    before giving up. Non-rate-limit errors propagate immediately.

    Parameters
    ----------
    messages : list
        Langchain message list [SystemMessage, HumanMessage, ...].
    model_name : str
        Primary model identifier (e.g. 'llama-3.1-8b-instant').
    temp : float
        Temperature for generation.

    Returns
    -------
    LLM response object with .content and .response_metadata.
    """
    fallbacks = _FALLBACK_CHAIN.get(model_name, ["google/gemini-2.0-flash-001"])
    models_to_try = [model_name] + fallbacks

    last_exc = None
    for i, model in enumerate(models_to_try):
        try:
            llm = build_llm(model, temp)
            response = llm.invoke(messages)
            if i > 0:
                print(f"[LLM FAILOVER SUCCESS] '{model}' responded after primary '{model_name}' was rate-limited.", flush=True)
            return response
        except Exception as exc:
            exc_str = str(exc).lower()
            is_rate_limit = any(sig in exc_str for sig in _RATE_LIMIT_SIGNALS)
            if is_rate_limit and i < len(models_to_try) - 1:
                next_model = models_to_try[i + 1]
                print(f"[LLM FAILOVER] '{model}' rate-limited → switching to '{next_model}'", flush=True)
                last_exc = exc
                continue
            # Non-rate-limit error or last model in chain — propagate
            raise

    # Should not reach here, but safety net
    raise last_exc

USER_PROFILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_profile.json")
_profile_lock = threading.Lock()

def load_user_profile() -> dict:
    with _profile_lock:
        if os.path.exists(USER_PROFILE_PATH):
            try:
                with open(USER_PROFILE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[PROFILE LOAD ERROR] {e}", flush=True)
        return {}

USER_PROFILE = load_user_profile()

def get_current_profile() -> dict:
    """Dynamically load fresh user profile from disk to guarantee live state access."""
    return load_user_profile()

def is_profile_relevant_query(query: str) -> bool:
    """Determine if a query is related to the user's personal identity, family, business, or background."""
    if not query:
        return False
    q = query.lower()
    
    # User nicknames, names, and business identifiers
    personal_keywords = {
        "anshu", "shubham", "swarnkar", "ash", "ssoni", "computer", "consultancy", 
        "tax", "consultant", "consultants", "compliance", "e-governance", "csc",
        "who am i", "who i am", "my name", "my nickname", "my business", "my company", 
        "my work", "my job", "my shop", "my family", "my father", "my mother", 
        "my brother", "my sibling", "my parents", "my cousin", "my background", 
        "my journey", "my education", "my career", "my email", "my phone", 
        "my number", "my address", "my location", "where i live", "where do i live",
        "tell me about me", "my profile", "my biography", "my bio", "who is speaking",
        "who is talking", "about me", "know about me", "know about my", "pf", "itr", "gst",
        "orai", "jalaun"
    }
    
    # Check exact keyword matching or substring match
    return any(kw in q for kw in personal_keywords)

def get_user_profile_text(gear: str = "LAUNCH") -> str:
    """Return L1 Daily Profile Context, selectively retrieving context based on gear."""
    profile = get_current_profile()
    if not profile:
        return ""
    
    details = profile.get("personal_details", {})
    prefs = profile.get("preferences", {})
    nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
    
    if gear == "WALK":
        # Ultra-thin identity context for casual conversation
        lines = [
            "[USER PERSONALIZATION CONTEXT]",
            f"  • User Name: {nickname}",
            f"  • Tone Preference: {prefs.get('communication_style', 'Warm, brief, natural')}"
        ]
        return "\n".join(lines)
        
    name = details.get("full_name", "")
    business = profile.get("business_context", {})
    
    lines = ["[USER PROFILE & CONTEXT]"]
    if name:
        lines.append(f"  • User Name: {name} (Nickname: {nickname})" if nickname else f"  • User Name: {name}")
    if details.get("personal_email"):
        lines.append(f"  • Personal Email: {details.get('personal_email')}")
    if details.get("official_email"):
        lines.append(f"  • Official Email: {details.get('official_email')}")
    if details.get("mobile_number"):
        lines.append(f"  • Mobile Number: {details.get('mobile_number')}")
    if details.get("residential_address"):
        addr = details.get("residential_address", {})
        if isinstance(addr, dict):
            addr_str = f"{addr.get('address', '')}, {addr.get('city', '')}, {addr.get('state', '')}, {addr.get('country', '')}"
            lines.append(f"  • Residential Address: {addr_str.strip(', ')}")
        else:
            lines.append(f"  • Residential Address: {addr}")

    if business:
        lines.append("  • Business Details:")
        lines.append(f"    - Name: {business.get('business_name', '')}")
        lines.append(f"    - Type: {business.get('business_type', '') or business.get('legacy_name', '')}")
        if business.get("location"):
            lines.append(f"    - Location: {business.get('location', {}).get('office', '')}")
        if business.get("contact"):
            contact = business.get("contact", {})
            lines.append(f"    - Website: {contact.get('website', '')}")
            lines.append(f"    - Contact Email: {contact.get('email', '')}")
        if business.get("marketing_identity"):
            lines.append(f"    - Tagline: {business.get('marketing_identity', {}).get('tagline', '')}")
        if business.get("core_services"):
            lines.append("    - Core Services:")
            for srv_cat, srv_list in business.get("core_services", {}).items():
                lines.append(f"      * {srv_cat.replace('_', ' ').title()}: {', '.join(srv_list)}")
        if business.get("growth_focus"):
            lines.append(f"    - Growth Focus: {', '.join(business.get('growth_focus', []))}")
            
    if prefs:
        lines.append(f"  • Timezone: {prefs.get('timezone', 'Asia/Kolkata')}")
        lines.append(f"  • Communication Style: {prefs.get('communication_style', 'Logical and warm')}")
        
    # Append family graph summary
    family = profile.get("family_graph", {})
    if family:
        lines.append("  • Family Relations:")
        for rel_cat, rel_val in family.items():
            if isinstance(rel_val, dict):
                members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in rel_val.items() if v)
                lines.append(f"    - {rel_cat.replace('_', ' ').title()}: {members}")
            else:
                lines.append(f"    - {rel_cat.replace('_', ' ').title()}: {rel_val}")

    # Append mindset & journey highlights
    journey = profile.get("mindset_and_journey", {})
    if journey:
        lines.append("  • Mindset & Journey Highlights:")
        for k, v in journey.get("life_journey_highlights", {}).items():
            lines.append(f"    - {k.replace('_', ' ').title()}: {v}")
        cognitive = journey.get("cognitive_profile", {})
        if cognitive:
            lines.append("    - Cognitive Profile:")
            for ck, cv in cognitive.items():
                lines.append(f"      * {ck.replace('_', ' ').title()}: {cv}")

    return "\n".join(lines)


def get_profile_fact_answer(query: str) -> str:
    """Deterministic profile answers for high-frequency identity questions."""
    profile = get_current_profile()
    if not profile:
        return ""

    q = (query or "").lower()
    details = profile.get("personal_details", {})
    business = profile.get("business_context", {})
    family = profile.get("family_graph", {})

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

    if any(k in q for k in ("my business", "whats my business", "what is my business", "my company", "my shop", "about my business")):
        business_name = business.get("business_name", "")
        business_type = business.get("business_type", "") or business.get("classification", "")
        if business_name and business_type:
            return f"Your business is {business_name}, a {business_type}."
        if business_name:
            return f"Your business is {business_name}."
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


def search_profile(query: str, bypass_filter: bool = False) -> str:
    """Perform a local directory search on L1 (Personal Details/Business), L2 (Family Graph) and L3 (Legacy Memory) to retrieve specific context."""
    profile = get_current_profile()
    if not profile:
        return ""
    
    cleaned = clean_search_query(query)
    q = cleaned.lower().strip()
    
    # Programmatic Me/Myself/I override:
    # If the query is a general question asking about themselves, load the ENTIRE profile history and context!
    personal_pronouns = {
        "myself", "who am i", "my journey", "my background", "tell me about me", 
        "my profile", "my biography", "my bio", "who is talk", "who is speak",
        "user profile", "profile information", "gather user profile", "know about me",
        "about me", "personal details", "profile data"
    }
    is_general_profile = any(p in q for p in personal_pronouns)
    
    # Programmatic Query vs Statement Classifier:
    # If the user is just sharing a conversational statement, diary entry, or thought, do NOT search the database!
    if not bypass_filter and not is_general_profile:
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
        details = profile.get("personal_details", {})
        if details:
            results.append("Personal Details:")
            for k, v in details.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
                    
        # Load L1 Business Context
        business = profile.get("business_context", {})
        if business:
            results.append("Business Context:")
            for k, v in business.items():
                if v and not str(v).startswith("["):
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")

        # Load L2 Family Graph Summary
        family = profile.get("family_graph", {})
        if family:
            results.append("Family structure:")
            for rel, d in family.items():
                if isinstance(d, dict):
                    members = ", ".join(f"{k.replace('_', ' ').title()}: {v}" for k, v in d.items() if v and not str(v).startswith("["))
                    if members:
                        results.append(f"  • {rel.replace('_', ' ').title()}: {members}")
                        
        # Load L3 Legacy Autobiographical History & Journey
        journey = profile.get("mindset_and_journey", {})
        for cat, det in journey.items():
            results.append(f"{cat.replace('_', ' ').title()} Background:")
            if isinstance(det, dict):
                for k, v in det.items():
                    results.append(f"  • {k.replace('_', ' ').title()}: {v}")
            else:
                results.append(f"  • {det}")
                
        edu_career = profile.get("education_and_career", {})
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
        "know", "tell", "show", "did", "does", "have", "has", "had", "a", "an", "the", "about",
        "hi", "hello", "hey", "yo"  # Add conversational greetings to stopwords
    }
    search_words = [w for w in q.split() if w not in stopwords and len(w) >= 1]
    
    def matches_word(text: str) -> bool:
        t_lower = text.lower()
        return any(re.search(r'\b' + re.escape(w) + r'\b', t_lower) for w in search_words)

    # 1. Search L1: Personal Details
    details = profile.get("personal_details", {})
    if details:
        details_clean = str(details).lower()
        if "personal" in q or "email" in q or "contact" in q or "phone" in q or "mobile" in q or matches_word(details_clean):
            results.append(f"• Personal Details: Name: {details.get('full_name', '')} - Nickname: {details.get('primary_nickname', '')} - Email: {details.get('personal_email', '')} - Official Email: {details.get('official_email', '')} - Mobile: {details.get('mobile_number', '')}")

    # 2. Search L1: Business Context
    business = profile.get("business_context", {})
    if business:
        business_name = business.get("business_name", "").lower()
        business_type = business.get("business_type", "").lower()
        if "business" in q or "company" in q or "consultancy" in q or "anshu" in q or matches_word(business_name) or matches_word(business_type):
            results.append(f"• Business Name: {business.get('business_name', '')}")
            results.append(f"• Business Type: {business.get('business_type', '')}")
            if business.get("location"):
                results.append(f"• Business Location: {business.get('location', {}).get('office', '')}")
            if business.get("contact"):
                contact = business.get("contact", {})
                results.append(f"• Business Contact: Website: {contact.get('website', '')}, Email: {contact.get('email', '')}")
            if business.get("core_services"):
                results.append(f"• Business Services: {json.dumps(business.get('core_services', {}))}")
            if business.get("marketing_identity"):
                results.append(f"• Business Tagline: {business.get('marketing_identity', {}).get('tagline', '')}")
            if business.get("growth_focus"):
                results.append(f"• Business Growth Focus: {', '.join(business.get('growth_focus', []))}")

    # 3. Search L2: Family Graph
    family = profile.get("family_graph", {})
    for relation, details_val in family.items():
        relation_clean = relation.lower().replace("_", " ")
        if isinstance(details_val, dict):
            for member_key, member_val in details_val.items():
                member_key_clean = member_key.lower().replace("_", " ")
                # Match if key is in query, or query is in key, or any search word matches key/value as a whole word
                if member_key_clean in q or q in member_key_clean or matches_word(member_key_clean) or matches_word(str(member_val)):
                    results.append(f"• Family Connection ({relation.replace('_', ' ').title()} - {member_key.replace('_', ' ').title()}): {member_val}")
        elif isinstance(details_val, list):
            for item in details_val:
                if q in str(item).lower() or matches_word(str(item)):
                    results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {item}")
        else:
            if relation_clean in q or q in relation_clean or matches_word(str(details_val)):
                results.append(f"• Family connection ({relation.replace('_', ' ').title()}): {details_val}")
                
    # 4. Search L3: Legacy & Autobiographical Memory
    edu_career = profile.get("education_and_career", {})
    for edu in edu_career.get("education", []):
        edu_str = str(edu).lower()
        if q in edu_str or matches_word(edu_str):
            results.append(f"• Education Record: {edu}")
            
    for emp in edu_career.get("employment_history", []):
        emp_str = str(emp).lower()
        if q in emp_str or matches_word(emp_str):
            results.append(f"• Employment Record: {emp}")
            
    journey = profile.get("mindset_and_journey", {})
    for category, details_val in journey.items():
        category_clean = category.lower().replace("_", " ")
        if isinstance(details_val, dict):
            for k, v in details_val.items():
                k_clean = k.lower().replace("_", " ")
                if k_clean in q or category_clean in q or q in k_clean or q in str(v).lower() or matches_word(str(v)):
                    results.append(f"• Background History ({category.replace('_', ' ').title()} - {k.replace('_', ' ').title()}): {v}")
        else:
            if category_clean in q or q in category_clean or q in str(details_val).lower() or matches_word(str(details_val)):
                results.append(f"• Background History ({category.replace('_', ' ').title()}): {details_val}")
                
    if results:
        return "[Local User Profile Matches]\n" + "\n".join(results)
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


# ── Web search ────────────────────────────────────────────────────────────────

def wikipedia_search(query: str, max_results: int = 3) -> str:
    """Query Wikipedia MediaWiki API to fetch high-quality, structured summaries for research data.
    
    Complies with MediaWiki User-Agent guidelines for up to 200+ requests per minute.
    """
    cleaned = clean_search_query(query)
    if not cleaned:
        return "No search query provided."
    import requests
    url = "https://en.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": cleaned,
        "limit": max_results,
        "namespace": 0,
        "format": "json"
    }
    headers = {
        "User-Agent": "ARIA-Assistant/1.0 (ssoni4751@gmail.com) Python-Requests/2.0"
    }
    try:
        response = requests.get(url, params=params, headers=headers, timeout=5)
        if response.status_code != 200:
            return f"[Wikipedia search failed: status {response.status_code}]"
        
        data = response.json()
        if len(data) < 4:
            return "No Wikipedia matches found."
            
        titles = data[1]
        descriptions = data[2]
        urls = data[3]
        
        if not titles:
            return "No Wikipedia articles matched."
            
        lines = []
        for i in range(len(titles)):
            title = titles[i]
            desc = descriptions[i] if i < len(descriptions) else ""
            link = urls[i] if i < len(urls) else ""
            
            # If description is empty or short, fetch high-quality plain-text introduction extract
            if not desc or len(desc) < 30:
                extract_params = {
                    "action": "query",
                    "prop": "extracts",
                    "exintro": True,
                    "explaintext": True,
                    "redirects": 1,
                    "titles": title,
                    "format": "json"
                }
                ext_resp = requests.get(url, params=extract_params, headers=headers, timeout=3)
                if ext_resp.status_code == 200:
                    ext_data = ext_resp.json()
                    pages = ext_data.get("query", {}).get("pages", {})
                    for page_id, page_val in pages.items():
                        if "extract" in page_val and page_val["extract"]:
                            desc = page_val["extract"]
                            break
                            
            if not desc:
                desc = "No summary available."
                
            lines.append(f"• Wikipedia: {title} [Confidence: 0.95]\n  {desc}\n  Source: {link}")
            
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Wikipedia search unavailable: {e}]"


def web_search(query: str, max_results: int = 4) -> str:
    cleaned = clean_search_query(query)
    if not cleaned:
        return "No results found."

    # Check Stateful Search Cache (12-hour TTL)
    cached = get_cached_search(cleaned)
    if cached:
        print(f"[SEARCH CACHE HIT] Reusing cached search results for: '{cleaned[:40]}'", flush=True)
        return cached["results"]
    
    # 1. Fetch DuckDuckGo results
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(cleaned, max_results=max_results))
        ddg_lines = []
        if results:
            for r in results:
                href = r.get("href", "").lower()
                # Dynamic Source Confidence Weighting
                confidence = 0.50
                if any(ext in href for ext in (".edu", ".gov", ".org")):
                    confidence = 0.98 if any(ext in href for ext in (".edu", ".gov")) else 0.85
                elif any(news in href for news in ("reuters.com", "apnews.com", "bbc.co.uk", "nytimes.com", "cnn.com", "bloomberg.com")):
                    confidence = 0.85
                elif any(low in href for low in ("reddit.com", "medium.com", "blogspot.com", "twitter.com", "facebook.com", "x.com")):
                    confidence = 0.25
                ddg_lines.append(f"• {r['title']} [Confidence: {confidence}]\n  {r['body']}\n  Source: {r['href']}")
        ddg_text = "\n\n".join(ddg_lines)
    except Exception as e:
        ddg_text = f"[DuckDuckGo search unavailable: {e}]"

    # 2. Fetch Wikipedia results (max 2 for optimal token management)
    wiki_text = wikipedia_search(cleaned, max_results=2)

    # Merge results factually
    merged = []
    if wiki_text and not wiki_text.startswith("[") and "No Wikipedia" not in wiki_text:
        merged.append("[Wikipedia Research Matches]")
        merged.append(wiki_text)
    if ddg_text and not ddg_text.startswith("[") and "No results found" not in ddg_text:
        merged.append("[Web Search Results]")
        merged.append(ddg_text)
        
    if not merged:
        merged_text = "No web or Wikipedia results found."
    else:
        merged_text = "\n\n".join(merged)
        
    # Store in local SQLite search cache
    if merged and "No web" not in merged_text:
        store_cached_search(cleaned, merged_text, "Wikipedia, DuckDuckGo")
        
    return merged_text


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
        profile_text = get_user_profile_text() if is_profile_relevant_query(message) else ""
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


def compact_completed_session_history(session_id: str) -> None:
    """Wipe intermediate details from conversation history once the goal epoch is completed.
    
    Keeps only high-level requests and concise summaries to prevent context contamination.
    """
    with _memory_lock:
        history = _histories[session_id]
        if not history:
            return
            
        compacted = deque(maxlen=20)
        for role, content in history:
            if len(content) > 800:
                summary = content[:300] + "\n... [Intermediate details cleared upon successful audit validation] ...\n" + content[-200:]
                compacted.append((role, summary))
            else:
                compacted.append((role, content))
        _histories[session_id] = compacted


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

def should_escalate_to_workflow(text: str, history_text: str = "") -> bool:
    """Classify user query to separate conversational interactions from complex workflows.
    
    Returns True ONLY if the query explicitly requests swarm orchestration, or is detected
    as a dynamic correction/rerun of a previous orchestration workflow in history.
    """
    t = (text or "").lower().strip()
    
    # Check slash/exclamation command prefixes
    if t.startswith("/") or t.startswith("!"):
        cmd = t.removeprefix("/").removeprefix("!")
        if cmd.startswith("launch") or cmd.startswith("sprint") or cmd.startswith("postnow"):
            return True
        return False
        
    # Check raw keyword prefix triggers
    if t.startswith("launch") or t.startswith("sprint") or t.startswith("postnow"):
        return True
        
    # Dynamic Goal Correction & Rerun Detection:
    # If the user has a recent workflow in history, scan for corrective pattern keywords
    history_lower = (history_text or "").lower()
    has_recent_workflow = "launch" in history_lower or "sprint" in history_lower or "goal" in history_lower or "graph" in history_lower
    if has_recent_workflow:
        correction_keywords = {
            "meant", "instead", "no not", "correct to", "change to", "wrong", 
            "typo", "error", "re-run", "rerun", "relaunch", "run again",
            "correct the topic", "not monetary", "should be"
        }
        if any(kw in t for kw in correction_keywords):
            print(f"[ROUTER] Dynamic escalation: Detected goal correction pattern in query: '{text}'", flush=True)
            return True
        
    return False


def intent_router(state: AriaState):
    query = state["messages"][-1].content
    history_text = state.get("history_text", "")
    lowered = query.lower().strip()
    session_id = state.get("session_id", "default")

    # Dynamic Interaction Mode Classification
    is_workflow = should_escalate_to_workflow(query, history_text=history_text)
    manual_gear = "LAUNCH" if is_workflow else "WALK"

    # Strip command prefix overrides to keep the processed query clean
    clean_query = query
    if is_workflow:
        t_lower = query.lower().strip()
        if t_lower.startswith("launch"):
            clean_query = query[len("launch"):].strip()
        elif t_lower.startswith("sprint"):
            clean_query = query[len("sprint"):].strip()
        elif t_lower.startswith("/launch"):
            clean_query = query[len("/launch"):].strip()
        elif t_lower.startswith("/sprint"):
            clean_query = query[len("/sprint"):].strip()

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
    elif manual_gear == "WALK":
        # Direct action detection is disabled in conversational fast path to prevent contamination
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
        "user_query": clean_query,
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


def is_simple_query(text: str) -> bool:
    t = (text or "").lower().strip()
    # Remove common command prefixes
    t = t.removeprefix("/").removeprefix("!")
    
    # Common greetings, basic phrases, and stats
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    if t in greetings or len(t) < 15:
        return True
    return False


def planner_node(state: AriaState):
    """Decompose user goal into a structured GoalGraph."""
    try:
        from .planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
    except ImportError:
        from planner import plan_goal, build_walk_graph, classify_intent, _build_fallback_graph
        
    query = state["user_query"]
    history_text = state.get("history_text", "")
    profile_text = get_user_profile_text() if is_profile_relevant_query(query) else ""
    session_id = state.get("session_id", "default")
    
    active_goal = state.get("active_goal") or {}
    pre_goal_id = active_goal.get("goal_id")
    
    print(f"[PLANNER NODE] Planning goal for query: '{query[:50]}' (goal_id: {pre_goal_id})", flush=True)
    
    if is_simple_query(query):
        graph = build_walk_graph(query, goal_id=pre_goal_id)
    else:
        # 1. Intent Governance stage
        intent_packet = classify_intent(query, history_text, model_name=CURRENT_DEPT_MODEL)

        # 2. Bounded Governance Gate: Clarification fallback on low confidence
        if intent_packet.confidence < 0.65:
            print(f"[INTENT GOVERNANCE] Low confidence ({intent_packet.confidence} < 0.65) -> bypassing planner and returning AMBIGUOUS_QUERY fallback.", flush=True)
            graph = _build_fallback_graph(
                query,
                goal_id=pre_goal_id,
                goal_type="NEW",
                planner_status="AMBIGUOUS_QUERY",
                intent_packet=intent_packet.to_dict()
            )
        else:
            # Check if this query is a correction referencing a recent workflow
            is_correction = False
            last_goal_text = None
            last_graph = get_last_goal_graph(session_id)
            if last_graph:
                last_goal_text = last_graph.get("goal")
                # If the escalation router classified this as a workflow escalation, AND it doesn't explicitly start with a command trigger keyword,
                # it is a corrective query referencing the last goal.
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
                query,
                "LAUNCH",
                history_text,
                profile_text,
                model_name=CURRENT_DEPT_MODEL,
                goal_id=pre_goal_id,
                is_correction=is_correction,
                last_goal_text=last_goal_text,
                intent_packet=intent_packet,
            )
        
    # Log GOAL_CREATED lifecycle event
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
            "planner_status": graph.planner_status
        }
    )
        
    return {"goal_graph": graph.to_dict()}


def task_executor_node(state: AriaState):
    """Executes the task DAG using TaskEngine and Department Heads."""
    import time
    try:
        from .task_engine import TaskEngine, GoalGraph, TaskState
        from .departments import get_department_head
        from .auditor import BipartiteAuditor
    except ImportError:
        from task_engine import TaskEngine, GoalGraph, TaskState
        from departments import get_department_head
        from auditor import BipartiteAuditor
    
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
    auditor = BipartiteAuditor(llm=llm_dept)
    
    print(f"[EXECUTOR] Executing goal DAG: {goal_graph.goal_id}", flush=True)
    
    # Log DEPENDENCY_WAIT for all downstream tasks initially
    for task in goal_graph.tasks:
        if task.depends_on:
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="DEPENDENCY_WAIT",
                state_before="PENDING",
                state_after="PENDING",
                metadata={"depends_on": task.depends_on}
            )
            
    def track_cascading_blocks(action_fn, *args, **kwargs):
        pre_blocked = {t.task_id for t in engine.goal.tasks if t.state == TaskState.BLOCKED}
        action_fn(*args, **kwargs)
        post_blocked = {t.task_id for t in engine.goal.tasks if t.state == TaskState.BLOCKED}
        
        newly_blocked = post_blocked - pre_blocked
        for b_tid in newly_blocked:
            b_task = engine._task_map[b_tid]
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=b_tid,
                department=b_task.department,
                event_type="DEPENDENCY_BLOCKED",
                state_before="PENDING",
                state_after="BLOCKED",
                metadata={"blocked_by": args[0] if args else "unknown", "reason": "dependency failure propagation"}
            )
    
    execution_log = state.get("execution_log") or []
    
    shared_resources = {
        "user_query": state["user_query"]
    }
    
    # Execution loop
    while not engine.is_goal_complete() and not engine.is_goal_blocked():
        ready_tasks = engine.get_ready_tasks()
        if not ready_tasks:
            break
            
        for task in ready_tasks:
            # Token Budget Check
            accumulated_goal_tokens = state.get("tokens", {}).get("total", 0)
            accumulated_task_tokens = 0
            for entry in execution_log:
                accumulated_goal_tokens += entry.get("tokens", {}).get("total", 0)
                if entry.get("task_id") == task.task_id:
                    accumulated_task_tokens += entry.get("tokens", {}).get("total", 0)
                
            is_ok, budget_reason = engine.verify_token_budget(task.task_id, accumulated_task_tokens, accumulated_goal_tokens)
            if not is_ok:
                print(f"[EXECUTOR] Token budget exhausted: {budget_reason}", flush=True)
                track_cascading_blocks(engine.mark_cancelled, task.task_id, f"Token Budget Exhausted: {budget_reason}")
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": f"Token Budget Exhausted: {budget_reason}",
                    "status": "CANCELLED",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0}
                })
                continue
                
            engine.mark_running(task.task_id)
            
            # Layer 5 Bipartite Auditor: Pre-Execution Gatekeeper check
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_PRE",
                state_before="RUNNING",
                state_after="AUDITING_PRE",
                metadata={"objective": task.objective}
            )
            passed_pre, reason_pre = auditor.audit_pre(task)
            if not passed_pre:
                print(f"[EXECUTOR] Pre-execution audit blocked task {task.task_id}: {reason_pre}", flush=True)
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="AUDIT_PRE_FAIL",
                    state_before="AUDITING_PRE",
                    state_after="FAILED",
                    metadata={"objective": task.objective, "reason": reason_pre}
                )
                track_cascading_blocks(engine.mark_failed, task.task_id, f"Pre-execution Audit Blocked: {reason_pre}")
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": f"Pre-execution Audit Blocked: {reason_pre}",
                    "status": "FAILED"
                })
                continue
                
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=task.task_id,
                department=task.department,
                event_type="AUDIT_PRE_PASS",
                state_before="AUDITING_PRE",
                state_after="RUNNING",
                metadata={"objective": task.objective}
            )
                
            dept_head = get_department_head(task.department)
            
            # Inject upstream results into context
            completed_results = engine.get_completed_results()
            task.context["upstream_results"] = [
                {
                    "task_id": tid,
                    "result": res,
                    "department": engine._task_map[tid].department,
                    "objective": engine._task_map[tid].objective
                }
                for tid, res in completed_results.items()
                if tid in task.depends_on
            ]
            
            # If the task is an execution task, we MUST ask the user for approval
            # with the fully resolved parameters (including upstream findings!)
            if task.department == "execution":
                if not task.context.get("approved"):
                    action = task.context.get("action", "")
                    params = task.context.get("params", {})
                    
                    # Resolve params with upstream research/writing text
                    upstream_texts = [
                        item["result"] for item in task.context.get("upstream_results", [])
                    ]
                    upstream_text = "\n\n".join(upstream_texts) if upstream_texts else ""
                    
                    # Call execution department head _resolve_params method dynamically
                    resolved_params = dept_head._resolve_params(params, upstream_text)
                    
                    # Save in pending action lock
                    with _pending_actions_lock:
                        _pending_actions[session_id] = {
                            "action": action,
                            "params": resolved_params,
                            "task_id": task.task_id,
                            "goal_id": goal_graph.goal_id
                        }
                        
                    # Format a beautiful preview of the action plan!
                    preview_fields = {k: v for k, v in resolved_params.items() if k not in ("body", "content")}
                    fields_str = "\n".join(f"  • {k.capitalize()}: {v}" for k, v in preview_fields.items())
                    body_preview = resolved_params.get("body", resolved_params.get("content", ""))
                    
                    preview = fields_str
                    if body_preview:
                        preview += f"\n\n**Draft Content:**\n{body_preview}"
                        
                    pending_action_notice = (
                        f"Action authorization required.\n\n"
                        f"Proposed action: **{action}**\n"
                        f"{preview}\n\n"
                        "Reply with '1' / 'approve' to execute, or '0' / 'cancel' to reject."
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
                    
                    return {
                        "goal_graph": engine.goal.to_dict(),
                        "execution_log": execution_log,
                        "final_brief": pending_action_notice,
                        "action_result": "",
                        "execution_tracker": tracker,
                        "pending_action_notice": pending_action_notice
                    }
            
            try:
                # Dispatch task to the department head
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_START",
                    state_before="RUNNING",
                    state_after="RUNNING",
                    metadata={"objective": task.objective}
                )
                
                t_dispatch_start = time.time()
                result, task_tokens = dept_head.dispatch(task, shared_resources, llm_dept)
                latency = round(time.time() - t_dispatch_start, 2)
                
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_DONE",
                    state_before="RUNNING",
                    state_after="RUNNING",
                    metadata={
                        "objective": task.objective,
                        "latency": latency,
                        "tokens": task_tokens,
                        "result_preview": (result or "")[:500]
                    }
                )
                
                # Layer 5 Bipartite Auditor: Post-Execution Validator check
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="AUDIT_POST",
                    state_before="RUNNING",
                    state_after="AUDITING_POST",
                    metadata={"objective": task.objective}
                )
                passed_post, audit_result = auditor.audit_post(task, result)
                if not passed_post:
                    print(f"[EXECUTOR] Post-execution audit failed task {task.task_id}: {audit_result}", flush=True)
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=goal_graph.goal_id,
                        task_id=task.task_id,
                        department=task.department,
                        event_type="AUDIT_POST_FAIL",
                        state_before="AUDITING_POST",
                        state_after="FAILED",
                        metadata={"objective": task.objective, "audit_result": audit_result}
                    )
                    track_cascading_blocks(engine.mark_failed, task.task_id, f"Post-execution Audit Failed: {audit_result}")
                    execution_log.append({
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "department": task.department,
                        "error": f"Post-execution Audit Failed: {audit_result}",
                        "status": "FAILED",
                        "tokens": task_tokens
                    })
                    # Log failure to the immune system to learn from errors!
                    if goal_graph.goal_type != "CORRECTION":
                        try:
                            from .memory import log_execution_failure
                        except ImportError:
                            from memory import log_execution_failure
                        log_execution_failure(
                            domain=f"department.{task.department}",
                            method=task.objective,
                            exception_msg=f"Post-execution Audit Failed: {audit_result}",
                            goal=goal_graph.goal,
                            intent_packet=goal_graph.intent_packet
                        )
                    else:
                        print(f"[IMMUNE SYSTEM GATE] Bypassing failure logging for CORRECTION goal execution failure to prevent database noise.", flush=True)
                else:
                    log_execution_ledger_event(
                        session_id=session_id,
                        goal_id=goal_graph.goal_id,
                        task_id=task.task_id,
                        department=task.department,
                        event_type="AUDIT_POST_PASS",
                        state_before="AUDITING_POST",
                        state_after="COMPLETED",
                        metadata={"objective": task.objective, "audit_result": audit_result}
                    )
                    
                    # Track newly ready tasks unlocked by completing this task
                    pre_ready = {t.task_id for t in engine.get_ready_tasks()}
                    engine.mark_completed(task.task_id, audit_result)
                    post_ready = {t.task_id for t in engine.get_ready_tasks()}
                    
                    newly_ready = post_ready - pre_ready
                    for n_tid in newly_ready:
                        n_task = engine._task_map[n_tid]
                        log_execution_ledger_event(
                            session_id=session_id,
                            goal_id=goal_graph.goal_id,
                            task_id=n_tid,
                            department=n_task.department,
                            event_type="DEPENDENCY_SATISFIED",
                            state_before="PENDING",
                            state_after="READY",
                            metadata={"satisfied_by": task.task_id}
                        )
                        
                    execution_log.append({
                        "task_id": task.task_id,
                        "objective": task.objective,
                        "department": task.department,
                        "result": audit_result,
                        "status": "SUCCESS",
                        "tokens": task_tokens
                    })
                    # Register success to heal the immune system!
                    try:
                        from .memory import register_successful_execution
                    except ImportError:
                        from memory import register_successful_execution
                    register_successful_execution(domain=f"department.{task.department}")
                    if task.department == "execution" and task.context.get("action"):
                        register_successful_execution(domain=f"action.{task.context['action']}")
            except Exception as e:
                err_msg = str(e)
                print(f"[EXECUTOR ERROR] Task {task.task_id} failed: {err_msg}", flush=True)
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=goal_graph.goal_id,
                    task_id=task.task_id,
                    department=task.department,
                    event_type="EXECUTION_FAIL",
                    state_before="RUNNING",
                    state_after="FAILED",
                    metadata={"objective": task.objective, "error": err_msg}
                )
                track_cascading_blocks(engine.mark_failed, task.task_id, err_msg)
                execution_log.append({
                    "task_id": task.task_id,
                    "objective": task.objective,
                    "department": task.department,
                    "error": err_msg,
                    "status": "FAILED",
                    "tokens": {"prompt": 0, "completion": 0, "total": 0}
                })
                # Log failure to the immune system to learn from errors!
                if goal_graph.goal_type != "CORRECTION":
                    try:
                        from .memory import log_execution_failure
                    except ImportError:
                        from memory import log_execution_failure
                    log_execution_failure(
                        domain=f"department.{task.department}",
                        method=task.objective,
                        exception_msg=f"Task Execution Exception: {err_msg}",
                        goal=goal_graph.goal,
                        intent_packet=goal_graph.intent_packet
                    )
                else:
                    print(f"[IMMUNE SYSTEM GATE] Bypassing failure logging for CORRECTION goal execution failure to prevent database noise.", flush=True)
                
    # Update tracker
    duration = round(time.time() - start_time, 2)
    tracker = state.get("execution_tracker") or {}
    tracker["task_manager_duration"] = duration
    
    final_brief = engine.get_execution_summary()
    
    # Goal lifecycle outcomes logging
    if engine.is_goal_complete():
        is_graceful_recovery = (goal_graph.planner_status != "SUCCESS")
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=None,
            department=None,
            event_type="GOAL_COMPLETED",
            state_before="ACTIVE",
            state_after="COMPLETED",
            metadata={
                "latency_sec": duration, 
                "summary": final_brief[:1000],
                "graceful_recovery": is_graceful_recovery,
                "planner_status": goal_graph.planner_status
            }
        )
        if is_graceful_recovery:
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=goal_graph.goal_id,
                task_id=None,
                department=None,
                event_type="RECOVERY_REGISTERED",
                state_before="DEGRADED",
                state_after="COMPLETED",
                metadata={
                    "planner_status": goal_graph.planner_status,
                    "recovery_mechanism": "Conversational assistant fallback graph",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
            print(f"[EXECUTOR] GRACEFUL RECOVERY REGISTERED: Planner failed with status {goal_graph.planner_status}, but execution completed successfully.", flush=True)
    elif engine.is_goal_blocked() or not engine.is_goal_complete():
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_graph.goal_id,
            task_id=None,
            department=None,
            event_type="GOAL_FAILED",
            state_before="ACTIVE",
            state_after="FAILED",
            metadata={"latency_sec": duration, "error": "Goal execution blocked or stalled"}
        )
        
    # Store action result if there was an execution task
    action_res = ""
    for entry in execution_log:
        if entry["department"] == "execution" and entry["status"] == "SUCCESS":
            action_res = entry.get("result", "")
            
    node_tokens = {"prompt": 0, "completion": 0, "total": 0}
    for entry in execution_log:
        t = entry.get("tokens") or {"prompt": 0, "completion": 0, "total": 0}
        node_tokens["prompt"] += t.get("prompt", 0)
        node_tokens["completion"] += t.get("completion", 0)
        node_tokens["total"] += t.get("total", 0)
            
    return {
        "goal_graph": engine.goal.to_dict(),
        "execution_log": execution_log,
        "final_brief": final_brief,
        "action_result": action_res,
        "execution_tracker": tracker,
        "tokens": node_tokens
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
    profile = get_current_profile()
    details = profile.get("personal_details", {}) if profile else {}
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

    # Soft Continuity: Suppress conversational history for fresh greetings to avoid residual bias
    lowered_query = user_query.lower().strip().removeprefix("/").removeprefix("!")
    for char in "?!.,":
        lowered_query = lowered_query.replace(char, "")
    lowered_query = lowered_query.strip()
    
    greetings = {
        "hi", "hello", "hey", "how are you", "how's it going", "how you doing", 
        "how doing", "yo", "hi buddy", "hey buddy", "hello buddy", "good morning", 
        "good afternoon", "good evening"
    }
    is_fresh_greeting = lowered_query in greetings or any(lowered_query.startswith(g + " ") for g in greetings)
    
    if is_fresh_greeting or gear in ("SPRINT", "LAUNCH"):
        history = ""

    if pending_action_notice:
        response = AIMessage(content=pending_action_notice)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Hard guard: if no action was executed this turn, never claim execution.
    if not action_result and is_action_status_query(user_query):
        response_text = "No action was executed in this turn."
        if gear == "LAUNCH":
            tracker = state.get("execution_tracker", {})
            if tracker and "start_time" in tracker:
                import time
                tot = round(time.time() - tracker["start_time"], 2)
                response_text += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
        response = AIMessage(content=response_text)
        return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Deterministic short-circuit for basic profile facts.
    if not action_result:
        direct_fact = get_profile_fact_answer(user_query)
        if direct_fact:
            if gear == "LAUNCH":
                tracker = state.get("execution_tracker", {})
                if tracker and "start_time" in tracker:
                    import time
                    tot = round(time.time() - tracker["start_time"], 2)
                    direct_fact += f"\n\nSwarm profile: Research {tracker.get('research_duration', 0.0)}s | Audit {tracker.get('task_manager_duration', 0.0)}s | Action {tracker.get('action_duration', 0.0)}s | Total {tot}s"
            response = AIMessage(content=direct_fact)
            return {"messages": state["messages"] + [response], "tokens": {"prompt": 0, "completion": 0, "total": 0}}

    # Dynamic L2/L3 profile retrieval fallback (All gears including WALK):
    # If there's no research, query search_profile to fetch matching personal details!
    if not research and not action_result:
        profile_ctx = search_profile(state["user_query"])
        if profile_ctx and ("[Local User Profile Matches]" in profile_ctx or "[Local User Profile" in profile_ctx):
            research = profile_ctx

    if gear == "WALK":
        style = "[WALK]\nBrief, warm, direct. Max two short paragraphs. Confirm any automation action clearly."
    elif gear == "SPRINT":
        style = "[SPRINT]\nActionable, fast-paced summary. Direct bullets, immediate takeaway."
    else:
        style = "[LAUNCH]\nStructured briefing: ## headers. Cover overview, findings, risks, outlook. End with one concrete recommendation. Dense and precise."

    # Inject live temporal awareness for PA synthesis
    from datetime import datetime, timezone
    now_str = datetime.now(timezone.utc).strftime("%A, %d %B %Y, %H:%M UTC")

    # Tiered Prompt Architecture
    if gear == "WALK":
        # Ultra-thin manifesto for casual conversational mode
        profile = get_current_profile()
        details = profile.get("personal_details", {}) if profile else {}
        nickname = details.get("primary_nickname", "") or details.get("full_name", "Anshu")
        manifesto = (
            f"You are ARIA, a warm, direct, and helpful personal companion. Current date/time: {now_str}.\n"
            f"Style: Warm, brief, natural human dialogue. Max two short paragraphs. Do not mention internal details.\n"
            f"Recipient: You are talking directly to {nickname}.\n"
            f"CRITICAL: If the user asks about their personal details, family, business, career, or background, you MUST use the information provided in [Internal Research] (which is retrieved from the authoritative local user profile).\n"
            f"If the required personal/business/family information is NOT present in [Internal Research] or [Conversation History], DO NOT invent, infer, or hallucinate any details (such as occupation, business name, meetings, or clients). In such cases, politely and warmly state that you do not have that information in their profile yet."
        )
    else:
        # Full Workflow/Launch/Sprint Mode Prompt
        # Only inject the full user profile if the query is profile-relevant
        if is_profile_relevant_query(user_query):
            profile_text = get_user_profile_text(gear)
        else:
            # Otherwise, use ultra-thin context just for username and style warmness
            profile_text = get_user_profile_text("WALK")
        profile_ctx = f"\n\nUser Profile:\n{profile_text}" if profile_text else ""
        google_tools = ", ".join(MAKE_ACTIONS.keys())
        google_ctx = f"\n\nGoogle Workspace active [{google_tools}]. Confirm any triggered actions clearly."
        
        try:
            from .memory import get_anti_pattern_rules
        except ImportError:
            from memory import get_anti_pattern_rules
        pa_rules = get_anti_pattern_rules("pa")

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
    
    # Check for planner degradation and append warning card if active
    graph_dict = state.get("goal_graph")
    if graph_dict and graph_dict.get("planner_status", "SUCCESS") != "SUCCESS":
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
    
    # Programmatic safeguard: ensure [IMAGE] tag is preserved in the response if found in action_result
    if action_result and "[IMAGE]" in action_result:
        # Check if the response already contains the image tag
        if "[IMAGE]" not in response.content:
            # Extract the complete [IMAGE] tag line from action_result
            match = re.search(r'(\[IMAGE\]\s*url=[^\s\n]+(?:\s+caption=[^\n]+)?)', action_result)
            if match:
                response.content += "\n\n" + match.group(1)
                
    # ​​Performance Telemetry Footnote ​​
    if gear == "LAUNCH":
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
if checkpointer:
    aria_brain = workflow.compile(checkpointer=checkpointer)
else:
    aria_brain = workflow.compile()


# â”€â”€ Core invoke helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def invoke_aria(message: str, session_id: str = "default", goal_id: Optional[str] = None) -> tuple[str, str, dict]:
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    
    epoch_id = f"{session_id}:{goal_id}"
    
    if is_epoch_sealed(epoch_id):
        print(f"[GOVERNANCE REFUSAL] Epoch '{epoch_id}' is sealed. Execution blocked.", flush=True)
        return "This execution epoch is completed and permanently sealed. No further actions or mutations are permitted.", "WALK", {}
    
    history_text = get_history_text(session_id)
    
    config = {}
    if checkpointer:
        config = {"configurable": {"thread_id": epoch_id}}
        
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
        "active_goal":    {"goal_id": goal_id},
        "compressed_research": "",
        "routing_metadata": {},
        "pending_action_notice": "",
        "goal_graph":     None,
        "execution_log":  [],
        "final_brief":    "",
        "tokens":         {"prompt": 0, "completion": 0, "total": 0}
    }, config)
    
    reply = output["messages"][-1].content
    gear  = output.get("gear", "WALK")
    tokens = output.get("tokens", {"prompt": 0, "completion": 0, "total": 0})
    
    add_to_history(session_id, message, reply)
    
    goal_graph_dict = output.get("goal_graph")
    if goal_graph_dict:
        status = goal_graph_dict.get("status", "ACTIVE")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            seal_epoch(epoch_id)
            compact_completed_session_history(session_id)
            
    return reply, gear, tokens


# â”€â”€ Health / chat HTTP server â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

STATUS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ARIA — AI Assistant</title>
<style>
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Segoe UI',sans-serif;background:#0b0b14;color:#e2e8f0;min-height:100vh;display:flex;align-items:center;justify-content:center}
  .card{background:#131325;border:1px solid #282846;border-radius:20px;padding:48px 40px;text-align:center;max-width:480px;width:90%;box-shadow:0 10px 30px rgba(0,0,0,0.5)}
  .dot{width:12px;height:12px;background:#10b981;border-radius:50%;display:inline-block;margin-right:8px;animation:pulse 2s infinite}
  @keyframes pulse{0%,100%{box-shadow:0 0 0 0 rgba(16,185,129,.4)}50%{box-shadow:0 0 0 8px rgba(16,185,129,0)}}
  h1{font-size:2.5rem;font-weight:800;letter-spacing:4px;color:#a78bfa;margin:16px 0 4px;background:linear-gradient(to right,#a78bfa,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
  .sub{color:#71717a;font-size:.95rem;margin-bottom:32px;letter-spacing:1px;text-transform:uppercase}
  .badge{display:inline-flex;align-items:center;background:#064e3b;border:1px solid #10b981;color:#34d399;border-radius:24px;padding:6px 16px;font-size:.8rem;font-weight:600;margin-bottom:28px}
  .swarm{background:#1a1a36;border:1px solid #7c3aed;border-radius:12px;padding:18px;margin-bottom:24px;text-align:left;box-shadow:inset 0 1px 0 rgba(255,255,255,0.05)}
  .swarm strong{color:#c084fc;font-size:1.1rem;display:block;margin-bottom:6px}
  .swarm p{color:#94a3b8;font-size:.88rem;line-height:1.5}
  .features{text-align:left;margin:20px 0 32px;padding-left:4px}
  .feat{color:#94a3b8;font-size:.88rem;margin:10px 0;display:flex;align-items:center}
  .feat-dot{width:6px;height:6px;background:#c084fc;border-radius:50%;margin-right:12px;display:inline-block}
  .footer{border-top:1px solid #27272a;padding-top:24px;color:#52525b;font-size:.8rem;letter-spacing:0.5px}
</style>
</head>
<body>
<div class="card">
  <div class="badge"><span class="dot"></span>UNIFIED SWARM</div>
  <h1>ARIA</h1>
  <p class="sub">AI Swarm Assistant</p>
  <div class="swarm">
    <strong>Unified Swarm Engine</strong>
    <p>Dynamic DAG-based task planning, routing, and execution. Integrates multi-agent deep research, writing, and secure audited actions.</p>
  </div>
  <div class="features">
    <div class="feat"><span class="feat-dot"></span>Instant short-circuit for casual conversations</div>
    <div class="feat"><span class="feat-dot"></span>6-Agent deep swarm + web search for complex tasks</div>
    <div class="feat"><span class="feat-dot"></span>Secure audited execution of Google Workspace APIs</div>
  </div>
  <p class="footer">Groq &bull; Llama 3 &bull; LangGraph &bull; Self-Healing Memory</p>
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
        import time
        draft["scheduled_at"] = time.time()
        draft["is_auto_scheduled"] = False  # Explicitly mark manual ad-hoc preview
        PENDING_POSTS[chat_id] = draft
        
        # 1. Send the Proposed Caption & FLUX Prompt in a separate text message
        details_text = (
            f"📝 *Proposed Caption:*\n"
            f"```\n{escape_markdown(draft['caption'])}\n```\n\n"
            f"🎨 *FLUX Prompt:*\n"
            f"_{escape_markdown(draft['image_prompt'])}_"
        )
        
        await bot.send_message(
            chat_id=chat_id,
            text=details_text,
            parse_mode="Markdown",
            reply_to_message_id=reply_to_message_id
        )
        
        # 2. Send the image preview with interactive keyboard
        with open(draft["image_path"], "rb") as photo_file:
            caption_text = (
                f"📊 *ARIA Marketing Department - Post Preview*\n\n"
                f"Please review the graphic above and the proposed caption sent in the previous message.\n\n"
                f"Click Approve to publish directly to Facebook Page."
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
    """Load the user's Telegram chat ID from local state file or environment."""
    if os.path.exists(CHAT_ID_FILE):
        try:
            with open(CHAT_ID_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return int(content)
        except Exception:
            pass
            
    env_id = os.environ.get("TELEGRAM_USER_CHAT_ID")
    if env_id:
        try:
            return int(env_id)
        except ValueError:
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
    
    # Avoid triggering retroactively if the bot starts up/restarts after 9:00 AM IST
    try:
        now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
        if now_ist.hour >= 9:
            today_str = now_ist.strftime("%Y-%m-%d")
            if get_last_preview_date() != today_str:
                print(f"[SCHEDULER] Startup time {now_ist.strftime('%H:%M:%S')} is past 9:00 AM IST. Marking today ({today_str}) as previewed to prevent retroactive run.", flush=True)
                set_last_preview_date(today_str)
    except Exception as e:
        print(f"[SCHEDULER ERROR] Failed to run startup initialization: {e}", flush=True)
        
    while True:
        try:
            now_ist = datetime.now(timezone.utc).astimezone(ist_tz)
            today_str = now_ist.strftime("%Y-%m-%d")
            
            # 1. Trigger if it is 9:00 AM IST or later, and we haven't sent a preview today yet
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
                    
                    # Store timestamp and auto-scheduled flag of preview generation in draft dict for auto-publish timeout
                    if chat_id in PENDING_POSTS:
                        import time
                        PENDING_POSTS[chat_id]["scheduled_at"] = time.time()
                        PENDING_POSTS[chat_id]["is_auto_scheduled"] = True
                        print(f"[SCHEDULER] Timestamped auto-scheduled pending post for chat {chat_id} at {today_str}", flush=True)
                else:
                    print("[SCHEDULER] It's time to post, but no Telegram chat ID is registered yet. Waiting for user interaction...", flush=True)
            
            # 2. Check for pending drafts that have timed out without user feedback (1 hour = 3600 seconds)
            import time
            now_ts = time.time()
            for p_chat_id in list(PENDING_POSTS.keys()):
                draft = PENDING_POSTS[p_chat_id]
                scheduled_at = draft.get("scheduled_at")
                is_auto_scheduled = draft.get("is_auto_scheduled", False)
                if is_auto_scheduled and scheduled_at and (now_ts - scheduled_at >= 3600):
                    print(f"[SCHEDULER] Auto-publishing timed-out post for chat {p_chat_id}...", flush=True)
                    
                    try:
                        await application.bot.send_message(
                            chat_id=p_chat_id,
                            text="⏰ *No review response received within 1 hour. Automatically publishing the scheduled post to your Facebook Page...*",
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send timeout notification: {err}", flush=True)
                        
                    try:
                        from .social_media import publish_to_facebook_page
                    except ImportError:
                        from social_media import publish_to_facebook_page
                        
                    ok, res_msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
                    
                    # Log progress atomically
                    try:
                        try:
                            from .memory import append_to_profile_ledger
                        except ImportError:
                            from memory import append_to_profile_ledger
                        append_to_profile_ledger("work_summaries", {
                            "task_name": "Daily FB Marketing Post (Auto-Published)",
                            "status": "SUCCESS" if ok else "FAILED",
                            "details": f"Message: {res_msg} | Topic: {draft.get('custom_topic')}"
                        })
                    except Exception as e:
                        print(f"[SCHEDULER WARNING] Failed to write ledger: {e}", flush=True)
                        
                    if ok:
                        # Set last post date
                        set_last_post_date(today_str)
                        
                        # Clean up states
                        PENDING_POSTS.pop(p_chat_id, None)
                        WAITING_FOR_TOPIC.pop(p_chat_id, None)
                        
                        try:
                            await application.bot.send_message(
                                chat_id=p_chat_id,
                                text=f"✅ *Successfully auto-published to Facebook Page!*\n\n{escape_markdown(res_msg)}\n\n*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```",
                                parse_mode="Markdown"
                            )
                        except Exception as err:
                            print(f"[SCHEDULER ERROR] Failed to send success notification: {err}", flush=True)
                    else:
                        try:
                            await application.bot.send_message(
                                chat_id=p_chat_id,
                                text=(
                                    f"❌ *Failed to auto-publish to Facebook:*\n{escape_markdown(res_msg)}\n\n"
                                    f"You can reply with 'Approve and publish' to retry, or 'Cancel post' to discard."
                                ),
                                parse_mode="Markdown"
                            )
                        except Exception as err:
                            print(f"[SCHEDULER ERROR] Failed to send failure notification: {err}", flush=True)
                elif not is_auto_scheduled and scheduled_at and (now_ts - scheduled_at >= 3600):
                    print(f"[SCHEDULER] Auto-cancelling manual post for chat {p_chat_id} due to timeout...", flush=True)
                    try:
                        await application.bot.send_message(
                            chat_id=p_chat_id,
                            text="⏰ *Manual post draft review period has expired. Discarding the draft to clear pending states.*",
                            parse_mode="Markdown"
                        )
                    except Exception as err:
                        print(f"[SCHEDULER ERROR] Failed to send cancel notification: {err}", flush=True)
                    PENDING_POSTS.pop(p_chat_id, None)
                    WAITING_FOR_TOPIC.pop(p_chat_id, None)
            
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


async def edit_callback_message(query, text: str, reply_markup=None):
    """Helper to edit a callback message safely whether it has media (caption) or is text-only."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.edit_message_caption(caption=text, reply_markup=reply_markup)
        except Exception as e:
            print(f"[CALLBACK WARNING] Failed to edit callback message: {e}", flush=True)


async def cmd_postnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force immediately generating and sending today's marketing post preview."""
    chat_id = update.effective_chat.id
    persist_chat_id(chat_id)
    
    custom_topic = " ".join(context.args) if context.args else None
    topic_str = f" for topic: '{custom_topic}'" if custom_topic else ""
    await update.message.reply_text(f"Generating marketing swarm preview{topic_str}... This takes about 15-20 seconds.")
    
    await generate_and_send_preview(chat_id, context.bot, custom_topic=custom_topic, reply_to_message_id=update.message.message_id)


# â”€â”€ Telegram handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def send_long_telegram_message(update: Update, text: str, reply_markup=None):
    """Split and send messages exceeding Telegram's 4096 character limit."""
    limit = 4000  # Safe boundary to prevent any BadRequest exception
    if len(text) <= limit:
        if reply_markup:
            await update.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update.message.reply_text(text)
        return

    paragraphs = text.split("\n\n")
    current_chunk = ""
    for paragraph in paragraphs:
        if len(current_chunk) + len(paragraph) + 2 > limit:
            if len(paragraph) > limit:
                if current_chunk:
                    await update.message.reply_text(current_chunk.strip())
                    current_chunk = ""
                sub_paragraphs = [paragraph[i:i+limit] for i in range(0, len(paragraph), limit)]
                for sub in sub_paragraphs[:-1]:
                    await update.message.reply_text(sub)
                current_chunk = sub_paragraphs[-1]
            else:
                await update.message.reply_text(current_chunk.strip())
                current_chunk = paragraph
        else:
            if current_chunk:
                current_chunk += "\n\n" + paragraph
            else:
                current_chunk = paragraph

    if current_chunk:
        if reply_markup:
            await update.message.reply_text(current_chunk.strip(), reply_markup=reply_markup)
        else:
            await update.message.reply_text(current_chunk.strip())


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
        await send_long_telegram_message(update, reply, reply_markup=get_action_approval_keyboard(session_id))
        return

    # Check for [IMAGE] tag to reply with a photo
    match = re.search(r'\[IMAGE\]\s*url=([^\s\n]+)(?:\s+caption=(.+))?', reply, re.DOTALL)
    if match:
        url = match.group(1)
        caption = match.group(2) if match.group(2) else ""
        await update.message.reply_photo(photo=url, caption=caption.strip())
        return

    await send_long_telegram_message(update, reply)


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


def classify_review_intent(text: str) -> Optional[str]:
    """Classify user's text message into social media review intents (approve, cancel, change_topic), robust to spelling mistakes."""
    text_clean = " ".join(str(text).lower().strip().split())
    if not text_clean:
        return None
        
    # Check exact digits or simple keywords first
    if text_clean in ("1", "one", "approve", "publish", "approve post", "publish post", "approve & publish", "approve and publish"):
        return "approve"
    if text_clean in ("0", "zero", "cancel", "cancel post", "discard", "discard post"):
        return "cancel"
    if text_clean in ("2", "two", "change", "change topic", "regenerate", "regenerate post"):
        return "change_topic"
        
    def distance(s1, s2):
        if len(s1) < len(s2):
            return distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]
        
    words = text_clean.split()
    
    # 1. Exact or substring checks (very fast)
    approve_phrases = ["approve and publish", "approve & publish", "approve post", "publish post", "publish the post", "approve draft", "publish draft", "yes", "confirm", "publish", "approve"]
    cancel_phrases = ["cancel post", "cancel draft", "discard post", "discard draft", "cancel", "discard"]
    change_phrases = ["change topic", "change the topic", "regenerate post", "regenerate draft", "change topic of post", "change details", "regenerate", "change"]
    
    for t in approve_phrases:
        if t in text_clean:
            return "approve"
    for t in cancel_phrases:
        if t in text_clean:
            return "cancel"
    for t in change_phrases:
        if t in text_clean:
            return "change_topic"
            
    # 2. Fuzzy match single words (distance threshold of 1 or 2 characters)
    for word in words:
        for target in ["approve", "publish", "confirm", "yes"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "approve"
        for target in ["cancel", "discard", "abort"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "cancel"
        for target in ["regenerate", "change", "topic"]:
            max_dist = 2 if len(target) > 5 else 1
            if distance(word, target) <= max_dist:
                return "change_topic"
                
    # 3. Fuzzy match multi-word phrases (distance threshold of 2 or 3 characters)
    for target in approve_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 3:
            return "approve"
    for target in cancel_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 2:
            return "cancel"
    for target in change_phrases:
        if len(target) > 5 and distance(text_clean, target) <= 3:
            return "change_topic"
            
    return None


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
            pending = _pending_actions.get(session_id)
        if pending:
            action = pending.get("action", "")
            params = resolve_action_params(pending.get("params", {}), research_text="")
            
            # Log approval event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_GRANTED",
                state_before="WAITING",
                state_after="RUNNING",
                metadata={"by": "telegram_text", "action": action, "params": params}
            )
            
            ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
            if ok:
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                status = "Action executed successfully."
                await update.message.reply_text(f"{status}\n\n{result_msg}")
            else:
                status = "Action execution failed."
                await update.message.reply_text(
                    f"{status}\n\n{result_msg}\n\nYou can type '1' / 'approve' again to retry, or '0' / 'cancel' to discard.",
                    reply_markup=get_action_approval_keyboard(session_id)
                )
            return

    if pending and _is_reject_message(msg):
        with _pending_actions_lock:
            pending = _pending_actions.get(session_id)
        if pending:
            # Log rejection event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_DENIED",
                state_before="WAITING",
                state_after="CANCELLED",
                metadata={"by": "telegram_text", "action": pending.get("action", "")}
            )
        with _pending_actions_lock:
            _pending_actions.pop(session_id, None)
        await update.message.reply_text("Pending action cancelled.")
        return

    if pending and not msg.startswith("/"):
        await update.message.reply_text("You have a pending action approval. Reply with '1' / 'approve' to execute, or '0' / 'cancel' to discard.")
        return

    print(f"[TG MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)

    # Text-based Social Media Review Interceptor
    review_intent = classify_review_intent(msg)
    
    if review_intent == "approve" and chat_id in PENDING_POSTS:
        draft = PENDING_POSTS.get(chat_id)
        if draft:
            await update.message.reply_text("Interpreted text approval. Publishing to Facebook Page. Please wait...")
            try:
                from .social_media import publish_to_facebook_page
            except ImportError:
                from social_media import publish_to_facebook_page
                
            ok, res_msg = await asyncio.to_thread(publish_to_facebook_page, draft["image_path"], draft["caption"])
            
            # Log work progress atomically to profile
            try:
                try:
                    from .memory import append_to_profile_ledger
                except ImportError:
                    from memory import append_to_profile_ledger
                append_to_profile_ledger("work_summaries", {
                    "task_name": "Daily FB Marketing Post",
                    "status": "SUCCESS" if ok else "FAILED",
                    "details": f"Message: {res_msg} | Topic: {draft.get('custom_topic')}"
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
                
                await update.message.reply_text(
                    f"Successfully published to Facebook Page!\n\n{res_msg}\n\nCaption:\n{escape_markdown(draft['caption'])}"
                )
            else:
                await update.message.reply_text(
                    f"Failed to publish to Facebook:\n{escape_markdown(res_msg)}\n\nCaption:\n{escape_markdown(draft['caption'])}\n\nYou can reply with 'Approve and publish' to retry, or 'Cancel post' to discard."
                )
            return

    elif review_intent == "cancel" and chat_id in PENDING_POSTS:
        PENDING_POSTS.pop(chat_id, None)
        WAITING_FOR_TOPIC.pop(chat_id, None)
        await update.message.reply_text("Post draft cancelled.")
        return
        
    elif review_intent == "change_topic" and chat_id in PENDING_POSTS:
        WAITING_FOR_TOPIC[chat_id] = True
        await update.message.reply_text("Please reply with your new custom topic (e.g. Epf claims, Gst registration, Income tax returns) to regenerate the post.")
        return

    await run_aria(update, msg, session_id)


async def cmd_launch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /launch <complex question>")
        return
    await update.message.reply_text("Swarm engaged — planning and executing goal (~30s)...")
    await run_aria(update, "launch " + text, tg_session(update))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session_id = tg_session(update)
    with _memory_lock:
        _histories[session_id].clear()
    with _pending_actions_lock:
        _pending_actions.pop(session_id, None)
    await update.message.reply_text("Memory cleared. Fresh start.")


async def cmd_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current active pending goals or actions and provide control buttons."""
    chat_id = update.effective_chat.id
    session_id = tg_session(update)
    
    pending_post = PENDING_POSTS.get(chat_id)
    
    with _pending_actions_lock:
        pending_action = _pending_actions.get(session_id)
        
    if not pending_post and not pending_action:
        await update.message.reply_text("✅ There are no active pending goals or actions to manage.")
        return
        
    # Build list of active items
    text_lines = ["📊 *Current Active Goals & Action Approvals*:\n"]
    keyboard = []
    
    if pending_post:
        topic = pending_post.get("custom_topic") or "Daily Scheduled Post"
        text_lines.append("📝 *Pending Social Media Post*:")
        text_lines.append(f"  • *Topic*: {escape_markdown(topic)}")
        text_lines.append(f"  • *Caption*: _{escape_markdown(pending_post.get('caption', '')[:120])}..._\n")
        
        keyboard.append([
            InlineKeyboardButton("Approve Post", callback_data="post_approve"),
            InlineKeyboardButton("Cancel Post", callback_data="post_cancel")
        ])
        
    if pending_action:
        action = pending_action.get("action", "Google Action")
        text_lines.append("🔧 *Pending Google Workspace Action*:")
        text_lines.append(f"  • *Action*: `{escape_markdown(action)}`")
        
        params = pending_action.get("params", {})
        param_desc = ", ".join(f"{k}: {v}" for k, v in params.items() if k not in ("body", "content"))
        if param_desc:
            text_lines.append(f"  • *Parameters*: _{escape_markdown(param_desc)}_")
            
        keyboard.append([
            InlineKeyboardButton("Approve Action", callback_data=f"action_approve|{session_id}"),
            InlineKeyboardButton("Cancel Action", callback_data=f"action_cancel|{session_id}")
        ])
        
    await update.message.reply_text(
        text="\n".join(text_lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    google_line = "\n- Send email, create calendar event, log to sheet - just ask naturally" if is_google_configured() else ""
    await update.message.reply_text(
        "ARIA - Multi-Agent AI Assistant\n\n"
        "Unified Agent Swarm:\n"
        "- Just send a message naturally! ARIA automatically decomposes your query, performs deep web research, writes drafts, and executes secure audited actions.\n"
        "- /launch <question> - Shortcut command to explicitly trigger the planner.\n\n"
        "Marketing Department:\n"
        "- /postnow - Instantly generate and post custom daily tech graphic & copy to Facebook Page\n\n"
        "Extras:\n"
        "- /goals - Show and manage active pending goals and actions\n"
        "- /clear - Reset conversation memory\n"
        "- /stats - Show runtime diagnostics\n"
        f"- /help - Show this menu{google_line}\n\n"
        "I remember your conversation and personalize drafts based on your user profile.",
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

    # Real-time environment check
    groq_active = "🟢 ACTIVE" if os.environ.get("GROQ_API_KEY") else "🔴 NOT CONFIGURED"
    gemini_active = "🟢 ACTIVE" if os.environ.get("GEMINI_API_KEY") else "🔴 NOT CONFIGURED"
    openai_active = "🟢 ACTIVE" if os.environ.get("OPENAI_API_KEY") else "🔴 NOT CONFIGURED"
    openrouter_active = "🟢 ACTIVE" if os.environ.get("OPENROUTER_API_KEY") else "🔴 NOT CONFIGURED"

    args = context.args
    if not args:
        menu = (
            "🛡️ **ARIA Model Settings & Telemetry**\n\n"
            f"👤 **Current Assistant (PA) Model**: `{CURRENT_PA_MODEL}`\n"
            f"👥 **Current Swarm (Research) Model**: `{CURRENT_DEPT_MODEL}`\n\n"
            
            "⚙️ **Active Providers Configuration:**\n"
            f"- **Groq API**: {groq_active}\n"
            f"- **OpenRouter API**: {openrouter_active}\n"
            f"- **Gemini API (Native)**: {gemini_active}\n"
            f"- **OpenAI API (Native)**: {openai_active}\n\n"
            
            "✨ **Available Models to Switch:**\n"
            "--- *Groq Provider Models* ---\n"
            "1. `llama-3.3-70b-versatile` (Llama 3.3 - Best Quality)\n"
            "2. `llama-3.1-8b-instant` (Llama 3.1 8B - Fastest / Best Limits)\n"
            "3. `mixtral-8x7b-32768` (Mixtral 8x7B - Great Balance)\n"
            "4. `gemma2-9b-it` (Gemma 2 9B - Fast & Smart)\n"
            "5. `deepseek-r1-distill-llama-70b` (DeepSeek R1 - Deep Reasoning)\n\n"
            
            "--- *Gemini Native Models* ---\n"
            "6. `gemini-2.5-flash` (Gemini 2.5 Flash)\n\n"
            
            "--- *OpenRouter Provider Models* ---\n"
            "7. `google/gemini-2.5-flash` (Gemini 2.5 Flash via OpenRouter)\n"
            "8. `deepseek/deepseek-chat` (DeepSeek V3 via OpenRouter)\n"
            "9. `meta-llama/llama-3.3-70b-instruct` (Llama 3.3 via OpenRouter)\n\n"
            
            "--- *OpenAI Native Models* ---\n"
            "10. `gpt-4o-mini` (GPT-4o Mini)\n"
            "11. `gpt-4o` (GPT-4o flagship)\n\n"
            
            "🚀 **How to Switch:**\n"
            "- `/model <1-11>` - Change the main Personal Assistant model\n"
            "- `/model swarm <1-11>` - Change the underlying swarm/research model\n\n"
            "Tip: You can also specify any custom model string directly, e.g. `/model deepseek/deepseek-reasoner` or `/model swarm gemini-2.5-flash`"
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
        "7": "google/gemini-2.5-flash",
        "8": "deepseek/deepseek-chat",
        "9": "meta-llama/llama-3.3-70b-instruct",
        "10": "gpt-4o-mini",
        "11": "gpt-4o"
    }

    selected_model = model_map.get(choice)
    if not selected_model:
        if choice in model_map.values() or "/" in choice or choice.startswith("gemini-") or choice.startswith("gpt-"):
            selected_model = choice
        else:
            await update.message.reply_text("Invalid choice. Use `/model` to see valid options or pass a valid model string.")
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
    
    try:
        if not query.message:
            print("[CALLBACK ERROR] Callback query message is None", flush=True)
            return
            
        chat_id = query.message.chat.id
        data = query.data
        print(f"[CALLBACK UPDATE] received callback_data='{data}' for chat_id={chat_id}", flush=True)

        if data.startswith("action_approve|"):
            session_id = data.split("|", 1)[1].strip()
            with _pending_actions_lock:
                pending = _pending_actions.get(session_id)
            if not pending:
                await query.edit_message_text("No pending action found to approve.")
                return
            action = pending.get("action", "")
            params = resolve_action_params(pending.get("params", {}), research_text="")
            
            # Log approval event
            log_execution_ledger_event(
                session_id=session_id,
                goal_id=pending.get("goal_id", "default"),
                task_id=pending.get("task_id"),
                department="execution",
                event_type="APPROVAL_GRANTED",
                state_before="WAITING",
                state_after="RUNNING",
                metadata={"by": "telegram_callback", "action": action, "params": params}
            )
            
            ok, result_msg = await asyncio.to_thread(execute_google_action, action, params)
            if ok:
                with _pending_actions_lock:
                    _pending_actions.pop(session_id, None)
                status = "Action executed successfully."
                await query.edit_message_text(f"{status}\n\n{result_msg}")
            else:
                status = "Action execution failed."
                await query.edit_message_text(
                    f"{status}\n\n{result_msg}\n\nYou can click Approve again to retry, or Cancel.",
                    reply_markup=get_action_approval_keyboard(session_id)
                )
            return

        if data.startswith("action_cancel|"):
            session_id = data.split("|", 1)[1].strip()
            with _pending_actions_lock:
                pending = _pending_actions.get(session_id)
            if pending:
                # Log rejection event
                log_execution_ledger_event(
                    session_id=session_id,
                    goal_id=pending.get("goal_id", "default"),
                    task_id=pending.get("task_id"),
                    department="execution",
                    event_type="APPROVAL_DENIED",
                    state_before="WAITING",
                    state_after="CANCELLED",
                    metadata={"by": "telegram_callback", "action": pending.get("action", "")}
                )
            with _pending_actions_lock:
                _pending_actions.pop(session_id, None)
            await query.edit_message_text("Pending action cancelled.")
            return
        
        if data == "post_approve":
            draft = PENDING_POSTS.get(chat_id)
            if not draft:
                await edit_callback_message(query, "No pending post found to approve. Run /postnow to generate a new draft.")
                return
            
            await edit_callback_message(query, "Publishing to Facebook Page. Please wait.")
            
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
                
                await edit_callback_message(
                    query,
                    f"✅ Successfully published to Facebook Page!\n\n{escape_markdown(msg)}"
                )
                
                await query.message.reply_text(
                    text=f"📢 *Published Post Details:*\n\n{escape_markdown(msg)}\n\n*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```",
                    parse_mode="Markdown"
                )
            else:
                await edit_callback_message(
                    query,
                    "⚠️ Failed to publish to Facebook Page.",
                    reply_markup=get_post_keyboard() # Keep keyboard active so they can try again or change topic!
                )
                
                await query.message.reply_text(
                    text=(
                        f"❌ *Failed to publish to Facebook:*\n{escape_markdown(msg)}\n\n"
                        f"*Caption:*\n```\n{escape_markdown(draft['caption'])}\n```\n\n"
                        f"You can click Approve again to retry, Change Topic, or Cancel."
                    ),
                    parse_mode="Markdown"
                )
                
        elif data == "post_change_topic":
            WAITING_FOR_TOPIC[chat_id] = True
            await query.message.reply_text("Please reply with your new custom topic (e.g. health and yoga, cybersecurity tips, computer repair services) to regenerate the post.")
            
        elif data == "post_cancel":
            PENDING_POSTS.pop(chat_id, None)
            WAITING_FOR_TOPIC.pop(chat_id, None)
            await edit_callback_message(query, "Post draft cancelled.")
            
    except Exception as e:
        print(f"[CALLBACK CRITICAL ERROR] Exception inside on_post_callback: {e}", flush=True)
        traceback.print_exc(file=sys.stdout)


# â”€â”€ Entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    google_status = f"Google Workspace ({'active' if is_google_configured() else 'NOT configured'})"
    print(f"--- ARIA IS LIVE | Memory | Web Search | Knowledge Base | {google_status} | Unified Swarm ---", flush=True)
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    # Start autonomous social media manager scheduler
    start_social_scheduler(bot)
    
    bot.add_handler(CommandHandler("launch", cmd_launch))
    bot.add_handler(CommandHandler("clear",  cmd_clear))
    bot.add_handler(CommandHandler("goals",  cmd_goals))
    bot.add_handler(CommandHandler("help",   cmd_help))
    bot.add_handler(CommandHandler("stats",  cmd_stats))
    bot.add_handler(CommandHandler("model",  cmd_model))
    bot.add_handler(CommandHandler("postnow", cmd_postnow))
    bot.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & (~filters.COMMAND), on_message))
    bot.add_handler(CallbackQueryHandler(on_post_callback))
    bot.run_polling(drop_pending_updates=True)


