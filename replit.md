# ARIA — Multi-Agent AI Assistant

ARIA is a multi-agent AI system accessible via Telegram and the web. It routes every query into one of three gears and gives all research agents live web search, conversation memory, and a knowledge base.

## Run & Operate

- `python babu/bot.py` — run the ARIA bot (Telegram + web chat API)
- `pnpm --filter @workspace/babu-web run dev` — run the status/chat web UI
- `pnpm run typecheck` — full typecheck across all packages

## Required Secrets

| Secret | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq API key for Llama LLMs (free at console.groq.com) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |

## Stack

- **Language:** Python 3.11
- **Bot framework:** python-telegram-bot
- **AI orchestration:** LangGraph + LangChain
- **LLM provider:** Groq (free tier — 6,000 req/day)
  - Router + Research agents: `llama-3.1-8b-instant` (fast)
  - PA (final response): `llama-3.3-70b-versatile` (smart)
- **Web search:** DuckDuckGo (duckduckgo_search, free, no API key)
- **Web UI:** React + Vite at `/` and `/chat`
- **Deployment:** Replit Reserved VM (always-on)

## Where things live

```
babu/
  bot.py               ← All ARIA logic + HTTP server (chat API + health)
artifacts/
  babu-web/            ← React status page (/) and chat UI (/chat)
  api-server/          ← Hosts deployment config (artifact.toml)
```

## Architecture — ARIA Agent Graph

```
User Message (Telegram or Web /api/chat)
        │
        ▼
  [intent_router]  ← decides WALK, SPRINT, or LAUNCH
        │
        ▼
  [research_dept]  ← uses web search + knowledge base
  │   SPRINT: ANALYST → SKEPTIC → STRATEGIST
  │   LAUNCH Round 1: ANALYST → SKEPTIC → STRATEGIST
  │   LAUNCH Round 2: HISTORIAN → FUTURIST → SYNTHESIZER
        │
        ▼
    [pa_node]    ← synthesizes with conversation memory
        │
        ▼
  Reply (Telegram message or /api/chat JSON response)
```

## Gear System

| Gear | Trigger | Behavior |
|---|---|---|
| WALK | Casual chat, or `/walk` | Single PA call — brief, direct |
| SPRINT | Research query, or `/sprint` | 3-agent swarm + web search → PA |
| LAUNCH | Complex query, or `/launch` | 6-agent 2-round swarm + web search → PA |

## Agent Tools (available to all research agents)

| Tool | Implementation |
|---|---|
| Web Search | DuckDuckGo via `duckduckgo_search` |
| Memory | Per-session conversation history (in-memory deque, max 20 turns) |
| Knowledge Base | Built-in ARIA knowledge dict with keyword search |

## Telegram Commands

- `/walk <msg>` — Force WALK gear
- `/sprint <question>` — Force SPRINT gear
- `/launch <question>` — Force LAUNCH gear (warns ~30s)
- `/clear` — Reset your conversation memory
- `/help` — Show command list

## Web Interface

- `/` — About page with gear/tool overview and "Start chatting" CTA
- `/chat` — Full chat interface; calls `POST /api/chat`
- `/api/chat` — JSON endpoint `{message, session_id}` → `{reply, gear}`
- `/api/healthz` — Health check

## User preferences

- Uses Groq free tier (no billing card needed)
- Deployed as Reserved VM for 24/7 uptime
- Do NOT use autoscale — polling needs a persistent process

## Gotchas

- Bot uses **polling** (not webhooks) — Reserved VM only, never autoscale
- Do NOT run the dev `ARIA Telegram Bot` workflow while deployed — causes polling conflict
- `GOOGLE_API_KEY` / `GEMINI_API_KEY` stored but unused (legacy)
- LAUNCH gear takes ~30-45s — `asyncio.to_thread` keeps the event loop alive
- Memory is in-process only — restarting the bot clears all session history
