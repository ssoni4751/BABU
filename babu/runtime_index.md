BABU Runtime Index v1

SELF
├─ Identity → identity.py
├─ Health → dashboard.py
├─ Telemetry → execution_ledger

MEMORY
├─ User Facts → memory_manager
├─ Session Context → state
├─ Long-term Knowledge → RAG

KNOWLEDGE
├─ Internal Docs → RAG First
├─ Runtime State → Internal Only
├─ General Knowledge → Model
├─ Current Events → Web Search

EXECUTION
├─ Actions → tools/*
├─ Governance → gatekeeper
├─ Audit → validator

FAILURE
├─ Runtime Error → logs
├─ Goal Failure → execution_ledger
├─ Service Status → dashboard

---

## Source Priority Ladder
1. Runtime State (health, telemetry, dashboard) -> Introspection
2. Config & Env Vars (active models, enabled services) -> Runtime config
3. User Memory (user facts, profile ledger) -> DB system_memory
4. RAG (internal docs, architecture walkthrough, engineering history) -> DB babu_knowledge
5. Model Knowledge (general knowledge, core programming/logic) -> LLM parametric knowledge
6. Web Search (current events, external lookup, weather, news) -> Tavily search API

## Source Selection Rules
- RULE: If answer exists internally, never search externally.
- WHEN TO USE WEB:
  * current information required
  * answer absent from RAG
  * answer absent from runtime
- WHEN NOT TO USE WEB:
  * identity
  * system state
  * telemetry
  * configuration
  * health
