BABU Runtime Index v2.5 — Governed Control Platform Standard

## Knowledge Class Hierarchy (K0-K7)

K0 WORKING MEMORY
├─ Session State → babu_k0_working_memory / LangGraph BabuState
├─ Transient Introspection → runtime self-context retention
├─ Recency Cache → Last session goal, execution results, status summary

K1 IDENTITY
├─ Private Persona → Pragya (Executive Companion)
├─ System Architecture → Project BABU (Governed Control Platform)
├─ Date of Birth → 2026-05-27
├─ Version → 4.0.0 (Dual-Bot Architecture)
├─ Creator → Anshu (Shubham Swarnkar)
├─ Operational Scope → Anshu Computer & Tax Consultancy / Personal Cognition

K2 RUNTIME
├─ Uptime → BOT_START_TIME / get_babu_age_string()
├─ Dual-Bot Telegram → Private Bot (Pragya / TELEGRAM_BOT_TOKEN), Public Desk (@Anshu4751_bot / TELEGRAM_PUBLIC_BOT_TOKEN)
├─ Active Memory → state, SQLite checkpoint DB (babu_checkpoint.db)
├─ Threads → autonomous_scheduler, web_dashboard_health_server, dual_bot_polling
├─ Failures Log → execution_ledger, failures.json (epistemic immune system)
├─ Multi-Model Swarm → Primary Groq, Secondary NVIDIA NIM / OpenRouter, Tertiary Gemini Native
├─ Live Telemetry → /api/telemetry, /api/healthz, /stats
├─ Goals → execution_ledger (GOAL_RECEIVED, PLANNING, EXECUTION)
├─ Timeline → babu_temporal_timeline

K3 USER & BUSINESS
├─ Facts → user_profile.json (owner info, contacts)
├─ Business Profile → business_profile.json (tax, compliance, GST, PF consultancy services & pricing)
├─ Preferences → system_memory
├─ Session Context → LangGraph state

K4 EXECUTION & SWARM
├─ 7-Layer Pipeline → Understand (L0/L1) → Classify (L2) → Govern (L3) → Plan (L4) → Execute (L5) → Verify (L6) → Respond
├─ Departments → information, research, analysis, writing, execution, pa
├─ Commercial Intake → public_bot.py (Stateful 4-category selection, mobile intake, IST slot anti-collision)
├─ CRM Subsystem → crm_service.py (babu_leads, babu_followups, transactional appointment commit, single HTML alert)
├─ Template Registry → trusted_templates (ACTIVE, DEMOTED, RETIRED)
├─ Allowed Actions → governance rules (e0/constitution.json, e0/policies.json)
├─ DAG Planner → planner.py (IntentCompiler, GoalGraph)

K5 ARCHITECTURE & GOVERNANCE
├─ ADRs → architecture_knowledge (record_type='ADR', ADR-001 through ADR-108)
├─ Dual-Bot Isolation → ADR-105 (Operator Lockdown & Public Desk Demarcation)
├─ Catalog & Funnel → ADR-106, ADR-107 (PF/Tax/GST/General & Stateful Slot Booking)
├─ Executive Identity → ADR-108 (Pragya Persona & Single CRM Booking Alert)
├─ Postmortems → architecture_knowledge (record_type='POSTMORTEM')
├─ Lessons → architecture_knowledge (record_type='LESSON')
├─ Tradeoffs → architecture_knowledge.tradeoff
├─ Impact Scores → architecture_knowledge.impact_score
├─ Evolution → architecture_knowledge ORDER BY phase
├─ Anti-patterns → failures.json (decaying confidence C = C_old * (1 - lambda * S))
├─ Modularization → Layered Modules (gateway.py, graph.py, planner.py, auditor.py, services.py, crm_service.py, public_bot.py, task_engine.py, departments.py)
├─ Service Classes → Class A (Auto-Approved Read), Class B (Single-Approval Mutation), Class C (Double-Confirmation Destructive)

K6 DOMAIN & RETRIEVAL
├─ Database Schemas → sealed_epochs, search_cache, execution_ledger, system_memory, trusted_templates, babu_temporal_timeline, architecture_knowledge
├─ Static Knowledge → System Information Index (System_Information_Index.md)
├─ Vector RAG → rag_storage.py (768-dim embeddings, Gemini text-embedding-004 -> Mock fallback)

K7 EXTERNAL
├─ Web Search → DuckDuckGo (ddgs / duckduckgo_search — free, fast)
├─ Current Events → Web Search only (tax news, EPFO updates, GST circulars)
├─ Social Publishing → Meta Facebook Graph API (social_media.py)

---

## Source Priority Ladder
0. Working Memory (recency cache, transient state) -> Introspection [K0]
1. Runtime State (health, telemetry, dashboard) -> Introspection [K2]
2. Config & Env Vars (active models, enabled services) -> Runtime config [K2]
3. User & Business Facts (user_profile.json, business_profile.json) -> Local Profile JSON [K3]
4. Architecture Knowledge (ADRs, tradeoffs, postmortems) -> DB architecture_knowledge / SII [K5]
5. RAG (internal docs, architecture walkthrough, engineering history) -> DB babu_knowledge [K6]
6. Model Knowledge (general knowledge, core programming/logic) -> LLM parametric knowledge
7. Web Search (current events, external lookup, weather, news) -> DuckDuckGo search API [K7]

## Source Selection Rules
- RULE: If answer exists internally, never search externally.
- RULE: Architecture history is a first-class knowledge source and must never be discarded.
- RULE: Never simulate success if an external service is unavailable; degrade truthfully.
- WHEN TO USE WEB:
  * current information required
  * answer absent from RAG / SII
  * answer absent from runtime
- WHEN NOT TO USE WEB:
  * identity [K1]
  * system state [K2]
  * telemetry [K2]
  * configuration [K2]
  * health [K2]
  * architecture decisions [K5]
  * tradeoffs [K5]
  * upgrades/evolution [K5]
