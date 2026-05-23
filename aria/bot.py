import asyncio
import os
import sys
import traceback
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Annotated, TypedDict, Literal, List

from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8080))

llm_pa = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.2)
llm_dept = ChatGroq(model="llama-3.1-8b-instant", temperature=0.7)

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
  .gear{background:#1e1e3a;border-radius:10px;padding:16px 20px;margin:8px 0;text-align:left}
  .gear.launch{background:#1e1028;border:1px solid #7c3aed}
  .gear strong{color:#a78bfa}
  .gear.launch strong{color:#c084fc}
  .gear span{color:#aaa;font-size:.88rem;margin-left:8px}
  .footer{margin-top:32px;color:#555;font-size:.8rem}
</style>
</head>
<body>
<div class="card">
  <div class="badge"><span class="dot"></span>LIVE</div>
  <h1>ARIA</h1>
  <p class="sub">Multi-Agent Telegram AI Assistant</p>
  <div class="gear"><strong>WALK</strong><span>Casual chat — quick, direct replies</span></div>
  <div class="gear"><strong>SPRINT</strong><span>Research mode — 3-agent swarm (Analyst + Skeptic + Strategist)</span></div>
  <div class="gear launch"><strong>LAUNCH</strong><span>Deep dive — 6-agent, 2-round swarm with cross-agent synthesis</span></div>
  <p class="footer">Powered by Groq &bull; Llama 3 &bull; LangGraph</p>
</div>
</body>
</html>"""


class AriaState(TypedDict):
    messages: Annotated[list[BaseMessage], "Conversation"]
    gear: Literal["WALK", "SPRINT", "LAUNCH"]
    research_data: List[str]
    user_query: str


# ── Nodes ──────────────────────────────────────────────────────────────────

def intent_router(state: AriaState):
    query = state["messages"][-1].content

    for cmd in ("/launch", "!launch"):
        if cmd in query.lower():
            return {"gear": "LAUNCH", "user_query": query, "research_data": []}
    if "/sprint" in query.lower():
        return {"gear": "SPRINT", "user_query": query, "research_data": []}
    if "/walk" in query.lower():
        return {"gear": "WALK", "user_query": query, "research_data": []}

    routing_prompt = (
        "Classify this query into exactly one tier:\n"
        "- LAUNCH: multi-part, deeply complex, strategic, requires comprehensive analysis or comparison of many factors\n"
        "- SPRINT: factual, analytical, or research question\n"
        "- WALK: casual chat, simple question, greeting\n"
        "Reply with just one word: LAUNCH, SPRINT, or WALK."
    )
    res = llm_dept.invoke([HumanMessage(content=f"{routing_prompt}\n\nQuery: {query}")])
    raw = res.content.upper()
    if "LAUNCH" in raw:
        gear = "LAUNCH"
    elif "SPRINT" in raw:
        gear = "SPRINT"
    else:
        gear = "WALK"
    return {"gear": gear, "user_query": query, "research_data": []}


SPRINT_AGENTS = [
    ("ANALYST",    "Provide hard data, statistics, and technical context."),
    ("SKEPTIC",    "Challenge assumptions, identify risks and blind spots."),
    ("STRATEGIST", "Connect findings to long-term goals and 'so what?' implications."),
]

LAUNCH_ROUND_1 = [
    ("ANALYST",    "Provide hard data, statistics, and technical context. Be comprehensive."),
    ("SKEPTIC",    "Challenge every assumption. Identify risks, failure modes, and blind spots."),
    ("STRATEGIST", "Map long-term implications, second-order effects, and strategic opportunities."),
]

LAUNCH_ROUND_2 = [
    ("HISTORIAN",    "Draw on historical precedents and analogies. What patterns from the past apply?"),
    ("FUTURIST",     "Extrapolate 5–10 year implications. What are the most disruptive possibilities?"),
    ("SYNTHESIZER",  "Read ALL prior agent reports and produce a unified insight: resolve contradictions, highlight consensus, and surface the single most important takeaway."),
]


def research_dept(state: AriaState):
    gear = state["gear"]
    query = state["user_query"]

    if gear == "WALK":
        return {"research_data": []}

    if gear == "SPRINT":
        reports = []
        for name, role in SPRINT_AGENTS:
            res = llm_dept.invoke([
                SystemMessage(content=f"You are part of the ARIA Research Swarm. Role — {name}: {role}"),
                HumanMessage(content=f"Principal's Query: {query}"),
            ])
            reports.append(f"[{name}] {res.content}")
        return {"research_data": reports}

    # LAUNCH — 2-round deep swarm
    round1_reports = []
    for name, role in LAUNCH_ROUND_1:
        res = llm_dept.invoke([
            SystemMessage(content=f"You are part of the ARIA Deep Swarm. Role — {name}: {role}"),
            HumanMessage(content=f"Principal's Query: {query}"),
        ])
        round1_reports.append(f"[{name}] {res.content}")

    round1_context = "\n\n".join(round1_reports)

    round2_reports = []
    for name, role in LAUNCH_ROUND_2:
        res = llm_dept.invoke([
            SystemMessage(content=f"You are part of the ARIA Deep Swarm, Round 2. Role — {name}: {role}"),
            HumanMessage(
                content=(
                    f"Principal's Query: {query}\n\n"
                    f"--- Round 1 Research ---\n{round1_context}"
                )
            ),
        ])
        round2_reports.append(f"[{name}] {res.content}")

    all_reports = round1_reports + round2_reports
    return {"research_data": all_reports}


def pa_node(state: AriaState):
    gear = state["gear"]
    research = "\n\n".join(state["research_data"])

    if gear == "LAUNCH":
        manifesto = f"""YOU ARE ARIA — Personal Intelligence Briefing System.
Current Gear: [LAUNCH] — Maximum depth. Six agents. Two rounds.

Rules:
1. You are the sole interface. Never mention internal agents or rounds by name.
2. Always open with [LAUNCH] on its own line.
3. Synthesize ALL research into a single, structured, comprehensive briefing.
4. Use clear sections with headers (##). Cover: overview, key findings, risks, strategic outlook.
5. End with one concrete, actionable recommendation.
6. Be dense and precise — this is a high-stakes deep dive."""
    elif gear == "SPRINT":
        manifesto = f"""YOU ARE ARIA.
Current Gear: [SPRINT]

Rules:
1. Always start with [SPRINT] on its own line.
2. Synthesize the research concisely — surface the most important signal.
3. Be direct. Lead with insight, not summary."""
    else:
        manifesto = """YOU ARE ARIA.
Current Gear: [WALK]

Rules:
1. Always start with [WALK] on its own line.
2. Be brief, warm, and direct. One or two short paragraphs max."""

    prompt = (
        f"User: {state['user_query']}\n\nInternal Research:\n{research}"
        if research else state["user_query"]
    )
    response = llm_pa.invoke([SystemMessage(content=manifesto), HumanMessage(content=prompt)])
    return {"messages": state["messages"] + [response]}


# ── Graph ──────────────────────────────────────────────────────────────────

workflow = StateGraph(AriaState)
workflow.add_node("router", intent_router)
workflow.add_node("research", research_dept)
workflow.add_node("pa", pa_node)
workflow.set_entry_point("router")
workflow.add_edge("router", "research")
workflow.add_edge("research", "pa")
workflow.add_edge("pa", END)
aria_brain = workflow.compile()


# ── Health server ──────────────────────────────────────────────────────────

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/healthz", "/api/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","bot":"ARIA"}')
        elif self.path in ("/", ""):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(STATUS_HTML.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HEALTH] Status page on port {PORT}", flush=True)
    server.serve_forever()


# ── Telegram handlers ──────────────────────────────────────────────────────

async def run_aria(update: Update, msg: str):
    # Keep sending "typing" every 4s while the brain runs in a background thread
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
        output = await asyncio.to_thread(
            aria_brain.invoke, {"messages": [HumanMessage(content=msg)]}
        )
        reply = output["messages"][-1].content
        print(f"[OK] gear={output.get('gear','?')} len={len(reply)}", flush=True)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"⚠️ ARIA error: {e}"
    finally:
        stop_typing.set()
        typing_task.cancel()

    await update.message.reply_text(reply)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    print(f"[MSG] {update.message.from_user.id}: {msg[:80]}", flush=True)
    await run_aria(update, msg)


async def cmd_walk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /walk <your message>")
        return
    await run_aria(update, f"/walk {text}")


async def cmd_sprint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /sprint <your question>")
        return
    await run_aria(update, f"/sprint {text}")


async def cmd_launch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usage: /launch <your complex question>")
        return
    print(f"[LAUNCH] {update.message.from_user.id}: {text[:80]}", flush=True)
    await update.message.reply_text("🚀 LAUNCH gear engaged — running 6-agent deep swarm. This takes ~30s…")
    await run_aria(update, f"/launch {text}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *ARIA — Multi-Agent AI Assistant*\n\n"
        "*Gears:*\n"
        "• /walk <msg> — Quick, direct reply\n"
        "• /sprint <question> — 3-agent research swarm\n"
        "• /launch <question> — 6-agent deep swarm (2 rounds)\n\n"
        "Or just send a message — ARIA routes automatically.",
        parse_mode="Markdown",
    )


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print("--- ARIA IS LIVE (Groq/Llama | WALK + SPRINT + LAUNCH) ---", flush=True)
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(CommandHandler("walk",   cmd_walk))
    bot.add_handler(CommandHandler("sprint", cmd_sprint))
    bot.add_handler(CommandHandler("launch", cmd_launch))
    bot.add_handler(CommandHandler("help",   cmd_help))
    bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_message))
    bot.run_polling(drop_pending_updates=True)
