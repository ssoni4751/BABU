import asyncio
import json
import os
import re
import sys
import threading
import traceback
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, List, Literal, Optional, TypedDict

# Force UTF-8 encoding for Windows standard streams to prevent emoji/unicode logging crashes
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from google_service import execute_google_action, is_google_configured
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

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

TELEGRAM_TOKEN  = os.environ["TELEGRAM_BOT_TOKEN"]
PORT            = int(os.environ.get("PORT", 8080))
GEMINI_KEY      = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY      = os.environ.get("OPENAI_API_KEY", "")

# Runtime state trackers (Global text configurations)
CURRENT_PA_MODEL   = "llama-3.3-70b-versatile"
CURRENT_DEPT_MODEL = "llama-3.1-8b-instant"

MAKE_WEBHOOK = os.environ.get("MAKE_WEBHOOK_URL", "")

def build_llm(model_name: str, temp: float):
    """Dynamically construct AI engines with updated stable Google routing string parsers."""
    if model_name.startswith("gemini-"):
        if not GEMINI_KEY:
            raise ValueError("GEMINI_API_KEY is not configured in Render environment variables.")
        
        # Explicitly targets the stable production endpoints to completely stop the legacy beta 404 crash
        return ChatGoogleGenerativeAI(
            model=model_name, 
            temperature=temp, 
            google_api_key=GEMINI_KEY
        )
    elif model_name.startswith("gpt-") or model_name.startswith("o1") or model_name.startswith("o3"):
        if not OPENAI_KEY:
            raise ValueError("OPENAI_API_KEY is not configured in Render environment variables.")
        return ChatOpenAI(model=model_name, temperature=temp, openai_api_key=OPENAI_KEY)
    else:
        return ChatGroq(model=model_name, temperature=temp)


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
        "and Google Workspace API automation (email via Gmail, Calendar events, Sheets logging, and more)."
    ),
    "models": (
        "Router and research agents default to llama-3.1-8b-instant. "
        "The Personal Assistant (PA) dynamically adapts based on model selectors. "
        "All fallback inference runs on Groq."
    ),
}

def search_knowledge(query: str) -> str:
    q = query.lower()
    hits = [v for k, v in KNOWLEDGE_BASE.items() if k in q or any(w in q for w in k.split())]
    return "\n".join(hits) if hits else ""


# ── Web search ─────────────────────────────────────────────────────────────

def web_search(query: str, max_results: int = 4) -> str:
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not ... if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"• {r['title']}\n  {r['body']}\n  Source: {r['href']}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"[Search unavailable: {e}]"


# ── Google Workspace automation actions map ──────────────────────────────────

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
}

ACTION_DETECTION_PROMPT = """Analyze the user message and decide if it requests an automation action.

Supported actions: send_email, create_event, log_to_sheet, create_doc, send_slack, create_task, copy_photos_to_drive, copy_contacts_to_drive, search_sheet

If an action is requested, reply with a JSON object ONLY (no other text):
{
  "action": "<action_name>",
  "params": {
    // Structural JSON Parameter details
  }
}

If NO action is requested, reply with exactly: NO_ACTION"""


def detect_action(message: str) -> Optional[dict]:
    try:
        router_llm = build_llm(CURRENT_DEPT_MODEL, 0.2)
        res = router_llm.invoke([
            SystemMessage(content=ACTION_DETECTION_PROMPT),
            HumanMessage(content=message),
        ])
        text = res.content.strip()
        if text == "NO_ACTION" or not text.startswith("{"):
            return None
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


# ── LangGraph state ────────────────────────────────────────────────────────

class AriaState(TypedDict):
    messages:       Annotated[list[BaseMessage], "Conversation"]
    gear:           Literal["WALK", "SPRINT", "LAUNCH"]
    research_data:  List[str]
    user_query:     str
    history_text:   str
    session_id:     str
    search_results: str
    action_result:  str


# ── Nodes ──────────────────────────────────────────────────────────────────

def intent_router(state: AriaState):
    query = state["messages"][-1].content
    for cmd in ("/launch", "!launch"):
        if cmd in query.lower():
            return {"gear": "LAUNCH", "user_query": query, "research_data": [], "search_results": "", "action_result": ""}
    if "/sprint" in query.lower():
        return {"gear": "SPRINT", "user_query": query, "research_data": [], "search_results": "", "action_result": ""}
    if "/walk" in query.lower():
        return {"gear": "WALK",   "user_query": query, "research_data": [], "search_results": "", "action_result": ""}

    routing_prompt = (
        "Classify this query into exactly one tier:\n"
        "- LAUNCH: deeply complex, strategic query\n"
        "- SPRINT: analytical or research question\n"
        "- WALK: casual chat or simple action request\n"
        "Reply with just one word: LAUNCH, SPRINT, or WALK."
    )
    router_llm = build_llm(CURRENT_DEPT_MODEL, 0.1)
    res = router_llm.invoke([HumanMessage(content=f"{routing_prompt}\n\nQuery: {query}")])
    raw = res.content.upper()
    gear = "LAUNCH" if "LAUNCH" in raw else ("SPRINT" if "SPRINT" in raw else "WALK")
    return {"gear": gear, "user_query": query, "research_data": [], "search_results": "", "action_result": ""}


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
    ("HISTORIAN",  "Draw on historical precedents and analogies. What patterns apply?"),
    ("FUTURIST",   "Extrapolate 5–10 year implications. What are the disruptive possibilities?"),
    ("SYNTHESIZER", "Read ALL prior agent reports. Resolve contradictions, surface consensus, and give the single most important takeaway."),
]


def action_node(state: AriaState):
    query      = state["user_query"]
    action_data = detect_action(query)
    if not action_data or "action" not in action_data:
        return {"action_result": ""}

    action = action_data["action"]
    params = action_data.get("params", {})
    print(f"[GOOGLE] Detected action={action} params={params}", flush=True)
    ok, msg = execute_google_action(action, params)
    print(f"[GOOGLE] Result: {msg}", flush=True)
    return {"action_result": msg}


def research_dept(state: AriaState):
    gear  = state["gear"]
    query = state["user_query"]

    if gear == "WALK":
        return {"research_data": [], "search_results": ""}

    print(f"[SEARCH] {query[:60]}", flush=True)
    search_ctx = web_search(query)
    kb_ctx     = search_knowledge(query)
    shared_ctx = ""
    if kb_ctx:
        shared_ctx += f"[ARIA Knowledge Base]\n{kb_ctx}\n\n"
    if search_ctx:
        shared_ctx += f"[Live Web Search Results]\n{search_ctx}"

    google_tool_desc = (
        "\n\n[Google Workspace Automation Tools available]\n"
        + "\n".join(f"• {k}: {v}" for k, v in MAKE_ACTIONS.items())
    )
    shared_ctx += google_tool_desc

    active_swarm_engine = build_llm(CURRENT_DEPT_MODEL, 0.7)

    def run_agent(name: str, role: str, extra_context: str = "") -> str:
        system = (
            f"You are part of the ARIA Research Swarm. Role — {name}: {role}\n\n"
            f"You have access to these tools:\n{shared_ctx}"
        )
        user_prompt = f"Principal's Query: {query}"
        if extra_context:
            user_prompt += f"\n\n--- Prior Research ---\n{extra_context}"
        res = active_swarm_engine.invoke([SystemMessage(content=system), HumanMessage(content=user_prompt)])
        return f"[{name}] {res.content}"

    if gear == "SPRINT":
        reports = [run_agent(name, role) for name, role in SPRINT_AGENTS]
        return {"research_data": reports, "search_results": search_ctx}

    r1     = [run_agent(name, role) for name, role in LAUNCH_ROUND_1]
    r1_ctx = "\n\n".join(r1)
    r2     = [run_agent(name, role, extra_context=r1_ctx) for name, role in LAUNCH_ROUND_2]
    return {"research_data": r1 + r2, "search_results": search_ctx}


def pa_node(state: AriaState):
    gear          = state["gear"]
    research      = "\n\n".join(state["research_data"])
    history       = state.get("history_text", "")
    action_result = state.get("action_result", "")

    if gear == "LAUNCH":
        style = (
            "Always open with [LAUNCH] on its own line.\n"
            "Synthesize into a structured briefing with ## headers.\n"
            "End with one concrete actionable recommendation."
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
        "create a calendar event, log to sheets, etc., confirm it was done."
    )

    manifesto = (
        f"YOU ARE ARIA — Personal Intelligence System. Gear: [{gear}]\n\n"
        f"Rules:\n"
        f"1. You are the sole interface. Never mention internal agents.\n"
        f"2. {style}\n"
        f"3. Use conversation history for context.\n"
        f"{google_ctx}"
    )

    parts = []
    if history:
        parts.append(f"[Conversation History]\n{history}")
    parts.append(f"User: {state['user_query']}")
    if action_result:
        parts.append(f"[Automation Result]\n{action_result}")
    if research:
        parts.append(f"[Internal Research]\n{research}")

    # Build the PA layer dynamically to honor user choice parameter updates instantly
    active_pa_engine = build_llm(CURRENT_PA_MODEL, 0.2)
    response = active_pa_engine.invoke([SystemMessage(content=manifesto), HumanMessage(content="\n\n".join(parts))])
    return {"messages": state["messages"] + [response]}


# ── Graph ──────────────────────────────────────────────────────────────────

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
aria_brain = workflow.compile()


# ── Core invoke helper ─────────────────────────────────────────────────────

def invoke_aria(message: str, session_id: str = "default") -> tuple[str, str]:
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
    })
    reply = output["messages"][-1].content
    gear  = output.get("gear", "WALK")
    add_to_history(session_id, message, reply)
    return reply, gear


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
  .gear strong{color:#a78bfa}
  .footer{margin-top:32px;color:#555;font-size:.8rem}
</style>
</head>
<body>
<div class="card">
  <div class="badge"><span class="dot"></span>LIVE</div>
  <h1>ARIA</h1>
  <p class="sub">Multi-Agent AI Assistant</p>
  <div class="gear"><strong>WALK</strong><span>Quick reply + Google automations via Workspace Actions</span></div>
  <div class="gear"><strong>SPRINT</strong><span>3-agent swarm + web search</span></div>
  <div class="gear launch"><strong>LAUNCH</strong><span>6-agent deep swarm + web search + synthesis</span></div>
  <p class="footer">Groq &bull; Gemini &bull; OpenAI &bull; LangGraph &bull; Workspace Tools</p>
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
                "features": ["memory", "web_search", "knowledge_base", "google_actions"],
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
            reply, gear = invoke_aria(msg, sid)
            print(f"[WEB OK] gear={gear} len={len(reply)}", flush=True)
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


# ── Telegram handlers ──────────────────────────────────────────────────────

async def run_aria(update: Update, msg: str, session_id: str):
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
        reply, gear = await asyncio.to_thread(invoke_aria, msg, session_id)
        print(f"[TG OK] gear={gear} len={len(reply)}", flush=True)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"⚠️ ARIA error: {e}"
    finally:
        stop_typing.set()
        typing_task.cancel()

    match = re.search(r'\[IMAGE\]\s*url=([^\s\n]+)(?:\s+caption=(.+))?', reply, re.DOTALL)
    if match:
        url = match.group(1)
        caption = match.group(2) if match.group(2) else ""
        await update.message.reply_photo(photo=url, caption=caption.strip())
        return

    await update.message.reply_text(reply)


def tg_session(update: Update) -> str:
    return f"tg_{update.message.from_user.id}"


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
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
    google_line = "\n• Google integrations fully configured" if is_google_configured() else ""
    await update.message.reply_text(
        "🤖 *ARIA — Multi-Agent AI Assistant*\n\n"
        "*Gears:*\n"
        "• /walk <msg> — Quick direct reply\n"
        "• /sprint <question> — 3-agent swarm + web search\n"
        "• /launch <question> — 6-agent deep swarm + web search\n\n"
        "*Extras:*\n"
        "• /clear — Reset conversation memory\n"
        f"• /help — Show this menu{google_line}\n\n"
        "Or just send a message — ARIA routes automatically.",
        parse_mode="Markdown",
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Renders system configuration matrix. Explicitly logs and prints active structural
    Personal Assistant (PA) and Swarm model tracker metrics back to your screen.
    """
    global CURRENT_PA_MODEL, CURRENT_DEPT_MODEL

    args = context.args
    if not args:
        menu = (
            "⚙️ *ARIA Engine Configuration Matrix*\n"
            "--------------------------------------\n"
            f"🤖 *Active Personal Assistant (PA):* `{CURRENT_PA_MODEL}`\n"
            f"👥 *Active Swarm Research Core:* `{CURRENT_DEPT_MODEL}`\n\n"
            "*Available Main Model Registers:*\n"
            "1️⃣ `llama-3.3-70b-versatile` (Groq Premium)\n"
            "2️⃣ `llama-3.1-8b-instant` (Groq Ultra-Fast)\n"
            "6️⃣ `gemini-1.5-flash` (Google High Capacity)\n"
            "7️⃣ `gemini-2.5-pro` (Google Elite Deep Analytics)\n"
            "8️⃣ `gpt-4o` (OpenAI Flagship Quality)\n"
            "9️⃣ `gpt-4o-mini` (OpenAI Efficient Smart Core)\n\n"
            "*Target Switch Execution:*\n"
            "• `/model <index>` - Swaps active Main Personal Assistant (PA)\n"
            "• `/model swarm <index>` - Swaps active underlying Swarm core\n\n"
            "💡 *Tip:* Use choice **6**, **7**, **8**, or **9** if Groq caps run low."
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
        "6": "gemini-1.5-flash",
        "7": "gemini-2.5-pro",
        "8": "gpt-4o",
        "9": "gpt-4o-mini"
    }

    selected_model = model_map.get(choice)
    if not selected_model:
        if choice in model_map.values():
            selected_model = choice
        else:
            await update.message.reply_text("❌ Invalid routing choice indicator index.")
            return

    if is_swarm:
        CURRENT_DEPT_MODEL = selected_model
        updated_text = (
            "✅ *Swarm Core Model Configuration Synchronized!*\n"
            "--------------------------------------\n"
            f"🤖 *Active Personal Assistant (PA):* `{CURRENT_PA_MODEL}`\n"
            f"👥 *Active Swarm Research Core:* `{CURRENT_DEPT_MODEL}`"
        )
        await update.message.reply_text(updated_text, parse_mode="Markdown")
    else:
        CURRENT_PA_MODEL = selected_model
        updated_text = (
            "✅ *Main Engine State Profile Updated Successfully!*\n"
            "--------------------------------------\n"
            f"🤖 *Active Personal Assistant (PA):* `{CURRENT_PA_MODEL}`\n"
            f"👥 *Active Swarm Research Core:* `{CURRENT_DEPT_MODEL}`"
        )
        await update.message.reply_text(updated_text, parse_mode="Markdown")


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    google_status = f"Google Workspace ({'active' if is_google_configured() else 'NOT configured'})"
    print(f"--- ARIA IS LIVE | WALK + SPRINT + LAUNCH ---", flush=True)
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("walk",   cmd_walk))
    bot.add_handler(CommandHandler("sprint", cmd_sprint))
    bot.add_handler(CommandHandler("launch", cmd_launch))
    bot.add_handler(CommandHandler("clear",  cmd_clear))
    bot.add_handler(CommandHandler("help",   cmd_help))
    bot.add_handler(CommandHandler("model",  cmd_model))
    bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_message))
    bot.run_polling(drop_pending_updates=True)
