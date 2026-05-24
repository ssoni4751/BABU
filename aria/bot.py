# =========================
# ARIA FINAL STABLE BUILD
# =========================

import asyncio
import json
import os
import re
import sys
import threading
import traceback
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, List, Literal, Optional, TypedDict

# UTF-8 fix for Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()

from google_service import execute_google_action, is_google_configured

from langchain_groq import ChatGroq
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from langgraph.graph import END, StateGraph

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    SystemMessage,
    AIMessage,
)

from telegram import Update
from telegram.constants import ChatAction

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# =========================
# ENV
# =========================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

PORT = int(os.environ.get("PORT", 8080))

GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

CURRENT_PA_MODEL = "llama-3.3-70b-versatile"
CURRENT_DEPT_MODEL = "llama-3.1-8b-instant"

MAX_RESEARCH_CHARS = 12000

model_lock = threading.Lock()

# =========================
# MODEL BUILDER
# =========================

def build_llm(model_name: str, temp: float):

    if model_name.startswith("gemini-"):

        if not GEMINI_KEY:
            raise ValueError("Missing GEMINI_API_KEY")

        return ChatGoogleGenerativeAI(
            model=model_name,
            temperature=temp,
            google_api_key=GEMINI_KEY,
            timeout=60,
        )

    elif (
        model_name.startswith("gpt-")
        or model_name.startswith("o1")
        or model_name.startswith("o3")
    ):

        if not OPENAI_KEY:
            raise ValueError("Missing OPENAI_API_KEY")

        return ChatOpenAI(
            model=model_name,
            temperature=temp,
            openai_api_key=OPENAI_KEY,
            timeout=60,
        )

    else:

        return ChatGroq(
            model=model_name,
            temperature=temp,
            timeout=60,
        )


llm_pa = build_llm(CURRENT_PA_MODEL, 0.2)
llm_dept = build_llm(CURRENT_DEPT_MODEL, 0.7)

# =========================
# KNOWLEDGE BASE
# =========================

KNOWLEDGE_BASE = {
    "aria": (
        "ARIA is a multi-agent AI system built on LangGraph + Groq."
    ),
    "gears": (
        "WALK = casual replies. "
        "SPRINT = 3-agent analysis. "
        "LAUNCH = 6-agent deep reasoning."
    ),
}

def search_knowledge(query: str) -> str:

    q = query.lower()

    hits = []

    for k, v in KNOWLEDGE_BASE.items():

        if k in q:
            hits.append(v)

    return "\n".join(hits)

# =========================
# WEB SEARCH
# =========================

def web_search(query: str, max_results: int = 4) -> str:

    try:

        from ddgs import DDGS

        with DDGS() as ddgs:

            results = list(
                ddgs.text(query, max_results=max_results)
            )

        if not results:
            return "No results found."

        lines = []

        for r in results:

            lines.append(
                f"• {r['title']}\n"
                f"{r['body']}\n"
                f"Source: {r['href']}"
            )

        return "\n\n".join(lines)

    except Exception as e:

        return f"[Search unavailable: {e}]"

# =========================
# ACTIONS
# =========================

MAKE_ACTIONS = {
    "send_email": "Send email",
    "create_event": "Create calendar event",
    "log_to_sheet": "Log to Google Sheet",
    "create_doc": "Create Google Doc",
    "send_slack": "Send Slack message",
    "create_task": "Create task",
}

ACTION_DETECTION_PROMPT = """
Analyze user request.

Supported actions:
send_email
create_event
log_to_sheet
create_doc
send_slack
create_task

If action exists:
Return ONLY JSON.

{
  "action":"...",
  "params":{}
}

Otherwise:
NO_ACTION
"""

def detect_action(message: str) -> Optional[dict]:

    try:

        res = llm_dept.invoke([
            SystemMessage(content=ACTION_DETECTION_PROMPT),
            HumanMessage(content=message),
        ])

        text = str(res.content).strip()

        if text == "NO_ACTION":
            return None

        try:
            return json.loads(text)

        except json.JSONDecodeError:

            match = re.search(r"\{[\s\S]*\}", text)

            if match:
                return json.loads(match.group())

        return None

    except Exception as e:

        print(f"[ACTION ERROR] {e}")

        return None

# =========================
# MEMORY
# =========================

_memory_lock = threading.Lock()

_histories = defaultdict(lambda: deque(maxlen=20))

def get_history_text(session_id: str) -> str:

    with _memory_lock:

        hist = list(_histories[session_id])

    return "\n".join(
        f"{r.upper()}: {c}"
        for r, c in hist
    )

def add_to_history(session_id: str, user_msg: str, aria_msg: str):

    with _memory_lock:

        _histories[session_id].append(("user", user_msg))
        _histories[session_id].append(("aria", aria_msg))

# =========================
# STATE
# =========================

class AriaState(TypedDict):

    messages: Annotated[list[BaseMessage], "Conversation"]

    gear: Literal["WALK", "SPRINT", "LAUNCH"]

    research_data: List[str]

    user_query: str

    history_text: str

    session_id: str

    search_results: str

    action_result: str

# =========================
# ROUTER
# =========================

def intent_router(state: AriaState):

    query = state["messages"][-1].content

    q = query.lower()

    if "/launch" in q or "!launch" in q:

        gear = "LAUNCH"

    elif "/sprint" in q:

        gear = "SPRINT"

    elif "/walk" in q:

        gear = "WALK"

    else:

        prompt = (
            "Classify query:\n"
            "LAUNCH = complex strategic reasoning\n"
            "SPRINT = analytical/research\n"
            "WALK = simple chat\n\n"
            "Return only one word."
        )

        res = llm_dept.invoke([
            HumanMessage(content=f"{prompt}\n\nQuery:{query}")
        ])

        raw = str(res.content).upper()

        gear = (
            "LAUNCH"
            if "LAUNCH" in raw
            else "SPRINT"
            if "SPRINT" in raw
            else "WALK"
        )

    return {
        "gear": gear,
        "user_query": query,
        "research_data": [],
        "search_results": "",
        "action_result": "",
    }

# =========================
# AGENTS
# =========================

SPRINT_AGENTS = [
    ("ANALYST", "Provide technical analysis."),
    ("SKEPTIC", "Challenge assumptions."),
    ("STRATEGIST", "Provide strategic outlook."),
]

LAUNCH_ROUND_1 = [
    ("ANALYST", "Provide deep technical analysis."),
    ("SKEPTIC", "Challenge all assumptions."),
    ("STRATEGIST", "Provide long-term strategy."),
]

LAUNCH_ROUND_2 = [
    ("HISTORIAN", "Use historical analogies."),
    ("FUTURIST", "Predict future implications."),
    ("SYNTHESIZER", "Synthesize all reports."),
]

# =========================
# ACTION NODE
# =========================

def action_node(state: AriaState):

    query = state["user_query"]

    action_data = detect_action(query)

    if not action_data:

        return {"action_result": ""}

    action = action_data.get("action")

    params = action_data.get("params", {})

    try:

        ok, msg = execute_google_action(
            action,
            params,
        )

        return {
            "action_result": msg
        }

    except Exception as e:

        return {
            "action_result": f"Automation failed: {e}"
        }

# =========================
# RESEARCH NODE
# =========================

def run_agent(name, role, query, shared_ctx, extra=""):

    system = (
        f"You are {name}.\n"
        f"Role: {role}\n\n"
        f"Context:\n{shared_ctx}"
    )

    prompt = f"Query: {query}"

    if extra:
        prompt += f"\n\nPrior reports:\n{extra}"

    res = llm_dept.invoke([
        SystemMessage(content=system),
        HumanMessage(content=prompt),
    ])

    return f"[{name}]\n{res.content}"

def research_dept(state: AriaState):

    gear = state["gear"]

    query = state["user_query"]

    if gear == "WALK":

        return {
            "research_data": [],
            "search_results": "",
        }

    print(f"[SEARCH] {query[:80]}")

    search_ctx = web_search(query)

    kb_ctx = search_knowledge(query)

    shared_ctx = ""

    if kb_ctx:
        shared_ctx += f"[Knowledge]\n{kb_ctx}\n\n"

    if search_ctx:
        shared_ctx += f"[Search]\n{search_ctx}\n\n"

    if gear == "SPRINT":

        reports = []

        for name, role in SPRINT_AGENTS:

            reports.append(
                run_agent(
                    name,
                    role,
                    query,
                    shared_ctx,
                )
            )

        return {
            "research_data": reports,
            "search_results": search_ctx,
        }

    round1 = []

    for name, role in LAUNCH_ROUND_1:

        round1.append(
            run_agent(
                name,
                role,
                query,
                shared_ctx,
            )
        )

    r1ctx = "\n\n".join(round1)

    round2 = []

    for name, role in LAUNCH_ROUND_2:

        round2.append(
            run_agent(
                name,
                role,
                query,
                shared_ctx,
                r1ctx,
            )
        )

    return {
        "research_data": round1 + round2,
        "search_results": search_ctx,
    }

# =========================
# PA NODE
# =========================

def pa_node(state: AriaState):

    gear = state["gear"]

    history = state.get("history_text", "")

    action_result = state.get("action_result", "")

    research = "\n\n".join(
        state["research_data"]
    )

    if len(research) > MAX_RESEARCH_CHARS:

        research = (
            research[:MAX_RESEARCH_CHARS]
            + "\n...[truncated]"
        )

    if gear == "LAUNCH":

        style = (
            "Start with [LAUNCH].\n"
            "Structured strategic briefing."
        )

    elif gear == "SPRINT":

        style = (
            "Start with [SPRINT].\n"
            "Concise analytical synthesis."
        )

    else:

        style = (
            "Start with [WALK].\n"
            "Brief and direct."
        )

    system = (
        f"You are ARIA.\n"
        f"Gear={gear}\n\n"
        f"{style}"
    )

    parts = []

    if history:
        parts.append(
            f"[History]\n{history}"
        )

    parts.append(
        f"User:\n{state['user_query']}"
    )

    if action_result:
        parts.append(
            f"[Automation]\n{action_result}"
        )

    if research:
        parts.append(
            f"[Research]\n{research}"
        )

    response = llm_pa.invoke([
        SystemMessage(content=system),
        HumanMessage(content="\n\n".join(parts)),
    ])

    raw_reply = response.content

    if isinstance(raw_reply, list):

        reply = "\n".join(
            str(x)
            for x in raw_reply
        )

    else:

        reply = str(raw_reply)

    return {
        "messages": (
            state["messages"]
            + [AIMessage(content=reply)]
        )
    }

# =========================
# GRAPH
# =========================

workflow = StateGraph(AriaState)

workflow.add_node("router", intent_router)

workflow.add_node("action", action_node)

workflow.add_node("research", research_dept)

workflow.add_node("pa", pa_node)

workflow.set_entry_point("router")

workflow.add_edge("router", "action")

workflow.add_edge("action", "research")

workflow.add_edge("research", "pa")

workflow.add_edge("pa", END)

aria_brain = workflow.compile()

# =========================
# INVOKE
# =========================

def invoke_aria(
    message: str,
    session_id: str = "default",
):

    history_text = get_history_text(
        session_id
    )

    output = aria_brain.invoke({

        "messages": [
            HumanMessage(content=message)
        ],

        "gear": "WALK",

        "research_data": [],

        "user_query": message,

        "history_text": history_text,

        "session_id": session_id,

        "search_results": "",

        "action_result": "",
    })

    raw_reply = output["messages"][-1].content

    if isinstance(raw_reply, list):

        reply = "\n".join(
            str(x)
            for x in raw_reply
        )

    else:

        reply = str(raw_reply)

    gear = output.get("gear", "WALK")

    add_to_history(
        session_id,
        message,
        reply,
    )

    return reply, gear

# =========================
# HTTP SERVER
# =========================

STATUS_HTML = """
<html>
<body style="background:#111;color:white;font-family:sans-serif;padding:40px;">
<h1>ARIA ONLINE</h1>
<p>WALK • SPRINT • LAUNCH</p>
</body>
</html>
"""

class HealthHandler(BaseHTTPRequestHandler):

    def _cors(self):

        self.send_header(
            "Access-Control-Allow-Origin",
            "*",
        )

    def do_GET(self):

        if self.path == "/healthz":

            body = json.dumps({
                "status": "ok",
                "bot": "ARIA",
            }).encode()

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "application/json",
            )

            self._cors()

            self.end_headers()

            self.wfile.write(body)

            return

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/html",
        )

        self.end_headers()

        self.wfile.write(
            STATUS_HTML.encode()
        )

    def log_message(self, format, *args):
        pass

def start_health_server():

    server = ThreadingHTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    server.daemon_threads = True

    print(f"[HTTP] Port={PORT}")

    server.serve_forever()

# =========================
# TELEGRAM HELPERS
# =========================

async def send_long_message(
    update: Update,
    text: str,
):

    MAX = 4000

    for i in range(0, len(text), MAX):

        chunk = text[i:i + MAX]

        await update.message.reply_text(
            chunk
        )

async def run_aria(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    msg: str,
    session_id: str,
):

    stop_typing = asyncio.Event()

    async def keep_typing():

        while not stop_typing.is_set():

            try:

                await context.bot.send_chat_action(
                    chat_id=update.effective_chat.id,
                    action=ChatAction.TYPING,
                )

            except Exception:
                pass

            try:
                await asyncio.sleep(4)

            except asyncio.CancelledError:
                break

    typing_task = asyncio.create_task(
        keep_typing()
    )

    try:

        reply, gear = await asyncio.to_thread(
            invoke_aria,
            msg,
            session_id,
        )

        print(
            f"[TG OK] gear={gear}"
        )

    except Exception as e:

        traceback.print_exc()

        reply = f"ARIA error: {e}"

    finally:

        stop_typing.set()

        typing_task.cancel()

    match = re.search(
        r"\[IMAGE\]\s*url=([^\s\n]+)",
        reply,
    )

    if match:

        url = match.group(1)

        try:

            await update.message.reply_photo(
                photo=url
            )

            return

        except Exception:
            pass

    await send_long_message(
        update,
        reply,
    )

def tg_session(update: Update):

    return (
        f"tg_{update.message.from_user.id}"
    )

# =========================
# TELEGRAM COMMANDS
# =========================

async def on_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    msg = update.message.text

    await run_aria(
        update,
        context,
        msg,
        tg_session(update),
    )

async def cmd_walk(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = " ".join(context.args)

    if not text:

        await update.message.reply_text(
            "Usage: /walk <message>"
        )

        return

    await run_aria(
        update,
        context,
        f"/walk {text}",
        tg_session(update),
    )

async def cmd_sprint(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = " ".join(context.args)

    if not text:

        await update.message.reply_text(
            "Usage: /sprint <question>"
        )

        return

    await run_aria(
        update,
        context,
        f"/sprint {text}",
        tg_session(update),
    )

async def cmd_launch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = " ".join(context.args)

    if not text:

        await update.message.reply_text(
            "Usage: /launch <question>"
        )

        return

    await update.message.reply_text(
        "🚀 LAUNCH engaged..."
    )

    await run_aria(
        update,
        context,
        f"/launch {text}",
        tg_session(update),
    )

async def cmd_clear(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    with _memory_lock:

        _histories[
            tg_session(update)
        ].clear()

    await update.message.reply_text(
        "Memory cleared."
    )

async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "ARIA Commands:\n\n"
        "/walk\n"
        "/sprint\n"
        "/launch\n"
        "/clear\n"
        "/model"
    )

async def cmd_model(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    global llm_pa
    global llm_dept
    global CURRENT_PA_MODEL
    global CURRENT_DEPT_MODEL

    args = context.args

    if not args:

        await update.message.reply_text(
            f"PA={CURRENT_PA_MODEL}\n"
            f"SWARM={CURRENT_DEPT_MODEL}"
        )

        return

    model_map = {

        "1": "llama-3.3-70b-versatile",

        "2": "llama-3.1-8b-instant",

        "6": "gemini-1.5-flash",

        "7": "gemini-2.5-pro",

        "8": "gpt-4o",

        "9": "gpt-4o-mini",
    }

    is_swarm = False

    choice = args[0]

    if choice == "swarm":

        is_swarm = True

        choice = args[1]

    selected = model_map.get(choice)

    if not selected:

        await update.message.reply_text(
            "Invalid model."
        )

        return

    try:

        with model_lock:

            if is_swarm:

                CURRENT_DEPT_MODEL = selected

                llm_dept = build_llm(
                    selected,
                    0.7,
                )

            else:

                CURRENT_PA_MODEL = selected

                llm_pa = build_llm(
                    selected,
                    0.2,
                )

        await update.message.reply_text(
            f"Switched to {selected}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"Failed: {e}"
        )

# =========================
# MAIN
# =========================

async def main():

    # Start health server thread
    threading.Thread(
        target=start_health_server,
        daemon=True,
    ).start()

    print("=== ARIA ONLINE ===")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # Commands
    app.add_handler(
        CommandHandler("walk", cmd_walk)
    )

    app.add_handler(
        CommandHandler("sprint", cmd_sprint)
    )

    app.add_handler(
        CommandHandler("launch", cmd_launch)
    )

    app.add_handler(
        CommandHandler("clear", cmd_clear)
    )

    app.add_handler(
        CommandHandler("help", cmd_help)
    )

    app.add_handler(
        CommandHandler("model", cmd_model)
    )

    # Messages
    app.add_handler(
        MessageHandler(
            filters.TEXT & (~filters.COMMAND),
            on_message,
        )
    )

    print("[TG] Polling started")

    await app.initialize()

    await app.start()

    await app.updater.start_polling(
        drop_pending_updates=True
    )

    # Keep alive forever
    while True:
        await asyncio.sleep(3600)

# =========================
# ENTRY
# =========================

if __name__ == "__main__":

    try:

        asyncio.run(main())

    except KeyboardInterrupt:

        print("ARIA stopped.")