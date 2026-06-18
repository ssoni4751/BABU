BABU Runtime Index v2

## Knowledge Class Hierarchy (K0-K7)

K0 WORKING MEMORY
├─ Session State → babu_k0_working_memory
├─ Transient Introspection → runtime self-context retention
├─ Recency Cache → Last session goal, execution results, status summary

K1 IDENTITY
├─ System name → Project BABU
├─ Date of Birth → 2026-05-27
├─ Version → 3.5.0
├─ Creator → Anshu

K2 RUNTIME
├─ Uptime → BOT_START_TIME
├─ Active Memory → state, checkpoint DB
├─ Threads → autonomous_scheduler, web_dashboard_health_server
├─ Failures Log → execution_ledger (AUDIT_PRE_FAIL, EXECUTION_FAIL)
├─ Live Telemetry → /api/telemetry
├─ Goals → execution_ledger (GOAL_RECEIVED)
├─ Timeline → babu_temporal_timeline

K3 USER
├─ Facts → user_profile.json
├─ Preferences → system_memory
├─ Session Context → LangGraph state

K4 EXECUTION
├─ Departments → information, research, communication, action, social_media
├─ Template Registry → trusted_templates
├─ Allowed Actions → governance rules
├─ DAG Planner → planner.py

K5 ARCHITECTURE
├─ ADRs → architecture_knowledge (record_type='ADR')
├─ Postmortems → architecture_knowledge (record_type='POSTMORTEM')
├─ Lessons → architecture_knowledge (record_type='LESSON')
├─ Tradeoffs → architecture_knowledge.tradeoff
├─ Impact Scores → architecture_knowledge.impact_score
├─ Evolution → architecture_knowledge ORDER BY phase
├─ Anti-patterns → system_memory (anti_pattern_*)

K6 DOMAIN
├─ Database Schemas → sealed_epochs, search_cache, execution_ledger, system_memory, trusted_templates, babu_temporal_timeline, babu_knowledge, architecture_knowledge
├─ Static Knowledge → RAG babu_knowledge

K7 EXTERNAL
├─ Web Search → Tavily API
├─ Current Events → Web Search only

---

## Source Priority Ladder
0. Working Memory (recency cache, transient state) -> Introspection [K0]
1. Runtime State (health, telemetry, dashboard) -> Introspection [K2]
2. Config & Env Vars (active models, enabled services) -> Runtime config [K2]
3. User Memory (user facts, profile ledger) -> DB system_memory [K3]
4. Architecture Knowledge (ADRs, tradeoffs, postmortems) -> DB architecture_knowledge [K5]
5. RAG (internal docs, architecture walkthrough, engineering history) -> DB babu_knowledge [K6]
6. Model Knowledge (general knowledge, core programming/logic) -> LLM parametric knowledge
7. Web Search (current events, external lookup, weather, news) -> Tavily search API [K7]

## Source Selection Rules
- RULE: If answer exists internally, never search externally.
- RULE: Architecture history is a first-class knowledge source and must never be discarded.
- WHEN TO USE WEB:
  * current information required
  * answer absent from RAG
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
