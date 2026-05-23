# ARIA — Multi-Agent Telegram AI Assistant

ARIA is a Telegram bot powered by a LangGraph multi-agent system. It automatically routes messages into two "gears" and uses a 3-agent research swarm for deep queries.

## Run & Operate

- `python aria/bot.py` — run the ARIA Telegram bot
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000, unused by bot)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages

## Required Secrets

| Secret | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq API key for Llama LLMs (free at console.groq.com) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `GOOGLE_API_KEY` | (unused — legacy Gemini key) |
| `GEMINI_API_KEY` | (unused — legacy Gemini key) |

## Stack

- **Language:** Python 3.11
- **Bot framework:** python-telegram-bot
- **AI orchestration:** LangGraph + LangChain
- **LLM provider:** Groq (free tier — 6,000 req/day)
  - Router + Research agents: `llama-3.1-8b-instant` (fast)
  - PA (final response): `llama-3.3-70b-versatile` (smart)
- **Deployment:** Replit Reserved VM (always-on)

## Where things live

```
aria/
  bot.py          ← All ARIA logic (router, research dept, PA node, Telegram handler)
artifacts/
  api-server/     ← Node.js API scaffold (unused by bot, hosts deployment config)
```

## Architecture — ARIA Agent Graph

```
User Message (Telegram)
        │
        ▼
  [intent_router]  ← decides WALK or SPRINT
        │
        ▼
  [research_dept]  ← SPRINT only: 3 agents in sequence
  │   ANALYST   — hard data & technical context
  │   SKEPTIC   — challenges assumptions & risks
  │   STRATEGIST — long-term implications
        │
        ▼
    [pa_node]    ← Personal Assistant synthesizes final reply
        │
        ▼
  Telegram Reply
```

## Gear System

| Gear | Trigger | Behavior |
|---|---|---|
| WALK | Casual chat, or `/walk` command | Single PA call — brief, direct |
| SPRINT | Research/analysis query, or `/sprint` command | 3-agent swarm → PA synthesis |

## User preferences

- Uses Groq free tier (no billing card needed)
- Deployed as Reserved VM for 24/7 uptime

## Gotchas

- Bot uses **polling** (not webhooks) — works fine on VM deployment, not on autoscale
- Do NOT use autoscale deployment — polling needs a persistent process
- `GOOGLE_API_KEY` / `GEMINI_API_KEY` are stored but unused (switched to Groq due to quota issues)
- LangGraph's retry logic can delay error responses by ~30s when rate-limited
