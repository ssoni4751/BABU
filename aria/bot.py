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
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
PORT = int(os.environ.get("PORT", 8080))

llm_pa = ChatGroq(model="llama-3.3-70b-versatile", temperature=0.2)
llm_dept = ChatGroq(model="llama-3.1-8b-instant", temperature=0.7)


class AriaState(TypedDict):
    messages: Annotated[list[BaseMessage], "Conversation"]
    gear: Literal["WALK", "SPRINT", "LAUNCH"]
    research_data: List[str]
    user_query: str


def intent_router(state: AriaState):
    query = state["messages"][-1].content

    if "/sprint" in query.lower():
        return {"gear": "SPRINT", "user_query": query, "research_data": []}
    if "/walk" in query.lower():
        return {"gear": "WALK", "user_query": query, "research_data": []}

    routing_prompt = (
        "If this query asks for facts, analysis, comparisons, or deep research, "
        "reply 'SPRINT'. If it is casual chat, reply 'WALK'."
    )
    res = llm_dept.invoke([HumanMessage(content=f"{routing_prompt}\n\nUser: {query}")])
    gear = "SPRINT" if "SPRINT" in res.content.upper() else "WALK"

    return {"gear": gear, "user_query": query, "research_data": []}


def research_dept(state: AriaState):
    if state["gear"] == "WALK":
        return {"research_data": []}

    query = state["user_query"]
    agents = [
        "ANALYST: Provide hard data and technical context.",
        "SKEPTIC: Challenge assumptions and identify risks.",
        "STRATEGIST: Connect this to long-term goals and 'so what?' implications.",
    ]

    reports = []
    for agent in agents:
        res = llm_dept.invoke([
            SystemMessage(content=f"You are part of the ARIA Swarm. Role: {agent}"),
            HumanMessage(content=f"Principal's Query: {query}"),
        ])
        reports.append(f"[{agent.split(':')[0]}] {res.content}")

    return {"research_data": reports}


def pa_node(state: AriaState):
    gear = state["gear"]
    research = "\n\n".join(state["research_data"])

    manifesto = f"""YOU ARE ARIA.
Current Gear: [{gear}]

Rules:
1. You are the sole interface.
2. Always start with the [{gear}] indicator.
3. If research is provided, synthesize it with precision.
4. If in WALK, be brief and direct."""

    prompt = (
        f"User: {state['user_query']}\n\nInternal Research:\n{research}"
        if research
        else state["user_query"]
    )
    response = llm_pa.invoke([SystemMessage(content=manifesto), HumanMessage(content=prompt)])
    return {"messages": state["messages"] + [response]}


workflow = StateGraph(AriaState)
workflow.add_node("router", intent_router)
workflow.add_node("research", research_dept)
workflow.add_node("pa", pa_node)
workflow.set_entry_point("router")
workflow.add_edge("router", "research")
workflow.add_edge("research", "pa")
workflow.add_edge("pa", END)
aria_brain = workflow.compile()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/healthz", "/api/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","bot":"ARIA"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[HEALTH] Server running on port {PORT}", flush=True)
    server.serve_forever()


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    print(f"[MSG] from {update.message.from_user.id}: {msg[:80]}", flush=True)
    await update.message.chat.send_action("typing")
    try:
        output = aria_brain.invoke({"messages": [HumanMessage(content=msg)]})
        reply = output["messages"][-1].content
        print(f"[OK] gear={output.get('gear','?')} reply_len={len(reply)}", flush=True)
    except Exception as e:
        traceback.print_exc(file=sys.stdout)
        reply = f"⚠️ ARIA error: {e}"
    await update.message.reply_text(reply)


if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    print("--- ARIA IS LIVE ON TELEGRAM (Groq/Llama) ---", flush=True)
    bot = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    bot.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), on_message))
    bot.run_polling()
