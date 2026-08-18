# BABU Architectural Decision Record (ADR) Book — Volume 7

**Canonical Reference for Topology-Aware Query Classification, Capability Demand Packets, Orchestration Bypass, and Conversational State Inheritance**

---

## ADR-091: Topology-Aware Query Classification & Capability Demand Packet Architecture
* **Status:** Accepted / Canonical
* **Context:** Conventional AI classifiers only extract topic domain (e.g. "Facebook" vs "Tax"). Based on the user's architectural sketch and Gemini discussion, classification must determine the **execution topology** of the request: What computational path does this query require?
* **Decision:** Formalized `CapabilityDemandPacket` structure embedded inside `IntentPacket`:
  * `topology_source`: `INTERNAL` (BABU codebase, ADRs, KB) | `EXTERNAL` (Meta, Google Workspace, Web) | `BOTH`
  * `topology_mode`: `CHITCHAT` | `LOOKUP` | `ACTION` | `HYBRID`
  * `mutation_type`: `NONE` (Read-only) | `INTERNAL` (Save config) | `EXTERNAL` (Post FB, Send Email, Create Task) | `BOTH`
  * `domain`: `BABU_SYSTEM` | `META` | `GOOGLE` | `TAX_COMPLIANCE` | `GENERAL`
  * `surface`: `PAGE` | `INSTAGRAM` | `MESSENGER` | `GMAIL` | `CALENDAR` | `DOCS` | `SHEETS` | `TASKS` | `CODEBASE` | `DATABASE` | `SYSTEM`
  * `planning_required`: `bool` (True for `HYBRID` multi-step workflows; False for fast-tracks, lookups, and direct actions).
* **Consequences:** Provides a precise representation of demand before governance, capability resolution, or planning begins.

---

## ADR-092: Dynamic Orchestration vs Deterministic Muscle Memory Routing
* **Status:** Accepted / Live in Production
* **Context:** Forcing every user request through heavy dynamic LLM planning (Layer 4) introduces unnecessary token bloat and latency.
* **Decision:** Implemented execution path routing based on `topology_mode`:
  * **Path A (CHITCHAT / Fast-Track):** Direct response (<0.05s). `planning_required = False`.
  * **Path B (INTERNAL_LOOKUP / EXTERNAL_LOOKUP):** Direct RAG / Knowledge Base retrieval. `planning_required = False`.
  * **Path C (DIRECT_ACTION):** Governance check $\rightarrow$ Known capability execution node (e.g. "Post this on Facebook"). `planning_required = False`.
  * **Path D (HYBRID / WORKFLOW):** Demand Packet $\rightarrow$ Governance $\rightarrow$ Dynamic LLM Task DAG Planner $\rightarrow$ Worker Nodes $\rightarrow$ Auditor $\rightarrow$ User. `planning_required = True`.
* **Consequences:** Optimizes system latency (<0.3s for routine commands) while preserving full multi-agent DAG planning for complex composed workflows.

---

## ADR-093: Explicit Conversation State Inheritance
* **Status:** Accepted / Canonical
* **Context:** In multi-turn customer chats (e.g. customer asking about PF pricing then asking "how long does it take?"), the second question must inherit active service context rather than restarting retrieval from the full knowledge base.
* **Decision:** Active operational state is explicitly preserved across session turns:
  `active_service`, `active_domain`, `customer_goal`, `last_retrieved_fact`, `pending_question`.
* **Consequences:** Ensures seamless contextual continuity for Facebook Messenger DMs and Telegram customer interactions.

---

## ADR-094: Command Console vs Governed Execution Layer Separation
* **Status:** Accepted / Canonical
* **Context:** Confusion between conversational UI and system execution leads to weak governance.
* **Decision:** Enforced architectural boundary: **JARVIS** is the command console UI; **BABU** is the governed execution kernel.
  Pipeline: $$\text{Human} \longrightarrow \text{JARVIS} \longrightarrow \text{BABU} \longrightarrow \text{Query Classification} \longrightarrow \text{Governance} \longrightarrow \text{Planning / Direct Routing} \longrightarrow \text{Capabilities} \longrightarrow \text{External Systems}$$
* **Consequences:** Separates surface presentation from system authorization, capability dispatch, and audit verification.

---

## ADR-095: Observability & Telemetry for Execution Topologies
* **Status:** Accepted / Live in Production
* **Context:** Performance and reliability must be measurable engineering data rather than vague impressions.
* **Decision:** Telemetry tracks topology mode (`CHITCHAT`, `LOOKUP`, `ACTION`, `HYBRID`), classifier latency, planner latency, worker latency, model selection, token usage, and audit verification outcomes.
* **Consequences:** Provides real-time telemetry metrics on system health and provider failover performance.

---

*BABU ADR Book Volume 7 — Published August 2026*
