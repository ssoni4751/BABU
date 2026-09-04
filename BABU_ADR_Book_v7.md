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

## ADR-096: Independent Commercial CRM Subsystem vs Internal System Telematics
* **Status:** Accepted / Live in Production
* **Context:** Conflating internal system telemetry (tokens, model latencies, DAG execution, K0–K7 memory) with commercial client customer relationship management (leads, inquiries, appointments, services) pollutes the architecture and confuses operator workflows.
* **Decision:** Enforced complete decoupling into two independent planes:
  * **Internal Flight Deck (`telematics.py` & `/api/telemetry` & `/`)**: System introspection, tokens, model benchmark latencies, audit trails.
  * **Commercial CRM Desk (`crm_service.py` & `/api/crm` & `/crm`)**: Commercial sales funnel, client leads table (`babu_leads`), interactions (`babu_interactions`), follow-ups (`babu_followups`), and Telegram CRM suite (`/crm`, `/leads`, `/add_lead`).
* **Consequences:** Clean separation of concerns. The CRM operates as an independent business desk while BABU's telemetry remains an unpolluted engineering cockpit.

---

## ADR-097: Bidirectional Facebook Webhook Persistent Memory & Lead Ingestion
* **Status:** Accepted / Live in Production
* **Context:** Inbound social media engagements (Facebook Page comments, Messenger DMs) previously generated stateless graph replies without recording memory or capturing commercial prospect details.
* **Decision:** Integrated inbound webhooks with the dual-plane memory infrastructure:
  1. **Working Memory & Ledger:** Interaction logged to `babu_k0_working_memory`, `execution_ledger`, and `babu_temporal_timeline`.
  2. **Commercial Lead Capture:** Queries with commercial/service intent (`ITR`, `GST`, `PF`, `PAN`, `Accounting`) automatically extract contact info and register/update prospects in `babu_leads`.
* **Consequences:** Inbound social traffic converts directly into structured CRM leads with zero manual operator overhead.

---

## ADR-099: Deterministic Conversational Sales Funnel & IST Datetime Slot Engine
* **Status:** Accepted / Live in Production
* **Context:** LLMs should not own appointment scheduling state or simulate booking confirmations without verified database transactions. Moreover, customers communicate in natural Hindi/English phrases ("kal 2 baje", "somwar shaam 4 baje") that require precise interpretation in `Asia/Kolkata` (IST) within strict working hours (11:00 AM to 6:00 PM Mon-Sat).
* **Decision:**
  1. **LLM Extraction + Deterministic Validation:** LLM extracts candidate entities (`service`, `date_expr`, `time_expr`, `intent`, `phone`); deterministic Python validator (`parse_ist_datetime`) resolves relative dates and strictly validates working interval ($11:00 \le \text{time} \le 18:00$, Sunday Closed).
  2. **Selective Knowledge Grounding (ADR-091/092):** Replaced bulk profile dumps with targeted K-slices (`K_PF`, `K_ITR`, `K_GST`, `K_GENERAL`).
  3. **Strict Commit-Before-Alert Sequence:** System executes DB transaction first; real-time Telegram alert to business owner is dispatched only upon verified DB commit.
* **Consequences:** Eliminates hallucinatory booking simulation, delivers airtight working window compliance, and notifies the owner with 100% truthful data.

---

## ADR-100: Database-Level Concurrency Isolation & Slot Anti-Collision
* **Status:** Accepted / Live in Production
* **Context:** In high-traffic social environments, two customers can request the same appointment time slot simultaneously (TOCTOU race condition). Application-level checks alone cannot prevent race conditions across parallel requests.
* **Decision:**
  1. **Database Partial Unique Index:** Enforced unique constraint on `babu_followups (scheduled_date) WHERE status = 'PENDING' AND proposed_action = 'IN_OFFICE_APPOINTMENT'` where `scheduled_date` stores composite `YYYY-MM-DD HH:MM`.
  2. **Transactional Integrity Error Rollback:** Catch `IntegrityError` at the database driver level, immediately roll back dirty transactions, query fresh alternate available slots, and gracefully return alternative options to the colliding client without triggering false Telegram alerts.
* **Consequences:** Absolute concurrency safety at the database tier. Multiple appointments on the same day at distinct hours succeed, while identical slot collisions are intercepted and resolved gracefully.

---

## ADR-101: Tri-Domain Execution Shape & Capability Demand Router
* **Status:** Accepted / Live in Production
* **Context:** Based on the notebook architectural blueprint and the core separation principle (*Understanding $\neq$ Authorization $\neq$ Execution*), the system needed a lean, deterministic demand model that hard-partitions execution capability domains and streamlines workflow shapes without dynamic planner bloat.
* **Decision:**
  1. **Strict Tri-Domain Partitioning:** Every operational demand authorizes one or more explicit domains: `{"USER", "SYSTEM", "BUSINESS"}`. No ambiguous pseudo-domains (e.g. `GENERAL`) are permitted in authorized sets.
  2. **Three Execution Shapes:**
     * **Class A (Pure LOOKUP):** Read-only single retrieval ($<0.05$s, zero LLM dynamic planning).
     * **Class B (LOOKUP + ACTION):** Fetch + linear mutation (governed by capability policy).
     * **Class C (COMPOSED / MULTI-STEP):** Composed DAG workflows or multi-domain operations.
  3. **Domain-Grounded Action Invariant:**
     $$\text{Authorized Actions} = \text{Candidate Actions} \cap \text{DOMAIN\_ACTIONS\_REGISTRY}[\text{Authorized Domains}] \cap \text{Universal Read Helpers}$$
     *The Gatekeeper deterministically rejects any action whose declared capability domain is not authorized by the DemandPacket.*
  4. **Policy-Driven Mutation Approvals:** Decoupled risk from execution shape:
     * `AUTO`: Autonomous lead qualification, Facebook comments/DMs, slot querying.
     * `APPROVAL_REQUIRED`: Operator-originated external mutating actions (email, tasks).
     * `DOUBLE_CONFIRMATION`: High-risk / destructive actions (`delete_document`, `bulk_delete`).
  5. **Classifier Demand vs. Gatekeeper Authority Separation:**
     * **Classifier:** Infers demand intent and proposes `candidate_actions`.
     * **Gatekeeper:** Deterministically enforces domain authorization and derives `allowed_actions`.
  6. **Closed-Loop Execution Lifecycle:** Execute action $\rightarrow$ Notify User $\rightarrow$ Auto-Save Template into Muscle Memory ($E[\text{Temp}]$) $\rightarrow$ Commit to Ledger & Timeline.
* **Consequences:** Eliminates tool hallucination, prevents cross-domain capability leakage, optimizes query latency, and ensures total operational auditability.

---

*BABU ADR Book Volume 7 — Published August 2026*

---

## ADR-102: Cloud Production Hardening, Render Runtime Alignment, Dual-Plane Security, and Model Matrix Consolidation
* **Status:** Accepted / Live in Production (Render Cloud Alignment)
* **Context:** BABU operates as a 24/7 governed agentic control plane deployed on Render web services. A full system audit revealed key production risks: public exposure of unauthenticated `/api/crm` and `/api/chat` endpoints on `.onrender.com`, missing Meta HMAC webhook signature validation, an approval bypass bug in `graph.py` auto-approving pre-detected mutating actions, ephemeral container filesystem wipes, SQLite schema lock contention on every connection, a boot-path resolution bug in `bootstrap.py`, and the need to formally finalize the model matrix around Groq open weights (`openai/gpt-oss-120b` and `openai/gpt-oss-20b`) while retiring legacy Llama models.
* **Decision:**
  1. **Model Matrix Consolidation & Discontinuation of Legacy Llama:**
      * Formally retire and remove `llama-3.3-70b-versatile` and `llama-3.1-8b-instant`.
      * Anchor `openai/gpt-oss-120b` via Groq API as the canonical high-cognition model powering both the **Strategic Planner (Layer 4)** and **Personal Assistant (PA / Layer 5)**, ensuring strict JSON schema adherence, zero syntax drift, and deep causal reasoning.
      * Anchor `openai/gpt-oss-20b` via Groq API as the canonical ultra-fast (<0.3s) Swarm Worker, Intent Router, and Social Webhook model.
      * Maintain secondary fallback to NVIDIA NIM and tertiary fallback to Google Gemini Native (`gemini-2.5-flash`).
  2. **Render Public Endpoint Hardening:**
     * Enforce mandatory Bearer token / API key (`API_CHAT_TOKEN`) validation on all HTTP endpoints (`/api/chat`, `/api/crm`, `/api/crm/lead/update`, `/api/models/switch`, `/api/telemetry`, `/api/chat/status`), returning `401 Unauthorized` on unauthenticated requests.
     * Confine `/api/image` to strict base directories (`artifacts/` and `temp/`) using `os.path.commonpath()` to eliminate arbitrary filesystem traversal risks.
     * Implement HMAC-SHA256 signature verification (`X-Hub-Signature-256`) against `FACEBOOK_APP_SECRET` on `POST /webhook/facebook` to block forged inbound Meta events.
  3. **Telegram Operator Console vs. Commercial Client Demarcation:**
     * Restrict administrative commands (`/crm`, `/leads`, `/update_lead`, `/postnow`, `/clear`, `/model`, `/promote`, `/retire`) strictly to `TELEGRAM_USER_CHAT_ID`.
     * Route unauthorized external users cleanly to the Anshu Computer & Tax Consultancy service qualification funnel without granting administrative control.
  4. **Constitutional Governance Invariant Restoration:**
     * Eliminate the detected action auto-approval override in `graph.py` (`task_executor_node`).
     * Re-establish the immutable rule: Every Class B (external mutating) and Class C (destructive) action requires explicit human operator approval regardless of how the action intent was detected.
     * Enforce fail-closed verification in `auditor.py` so that unexpected LLM timeouts or malformed audit payloads never silently pass unverified actions.
  5. **Render Ephemeral Disk Resilience & Database Concurrency:**
     * Correct `bootstrap.py` directory resolution (`ROOT = Path(__file__).resolve().parent`) and remove broken relative imports from `bot.py` so the service boots cleanly in the container root.
     * Decouple schema creation (`_ensure_sqlite_schema`) from `get_db_connection()` so DDL statements run only once at startup, eliminating `database is locked` contention across concurrent worker threads.
     * Increase SQLite busy timeout to 30 seconds.
* **Consequences:**
  * Complete hardening of the public Render cloud surface.
  * Absolute enforcement of constitutional human supremacy across all mutation channels.
  * Elimination of boot crashes, connection lockups, and unauthenticated data leaks.
  * Formalized model matrix anchored on Groq open weights (`gpt-oss-120b` and `gpt-oss-20b`).

---

## ADR-103: Intent Taxonomy Modernization, Elimination of the PUBLIC_INFORMATION Catch-All Black Hole, and Dedicated Search Grounding (Tavily & Wikipedia)
* **Status:** Accepted / Live in Production
* **Context:** A comprehensive review of the `IntentPacket` pipeline revealed a critical architectural defect: `classify_intent` relied on an overly narrow 4-bucket taxonomy (`BUSINESS_INFORMATION`, `PERSONAL_INFORMATION`, `SYSTEM_INFORMATION`, `PUBLIC_INFORMATION`). Any user instruction outside tax consultancy records or personal family queries (such as sending emails, booking calendar events, creating documents, logging sheets, or simple greetings) defaulted into `PUBLIC_INFORMATION`. This caused:
  1. False privacy assessments (`is_private_data_query` returned `False`, unblocking external web searches on private action context).
  2. Plan cache poisoning (`planned_graphs_cache` indexed action plans under `goal_class = 'PUBLIC_INFORMATION'`, replaying failed fallback graphs).
  3. Conversational action hallucination in `pa_node` where single-task refusal graphs bypassed anti-hallucination constraints and falsely claimed actions were executed.
* **Decision:**
  1. **Strict Isolation of `PUBLIC_INFORMATION`:**
     * `PUBLIC_INFORMATION` is strictly and exclusively reserved for external public knowledge inquiries, live web search (via Tavily Search API with DuckDuckGo fallback), and Wikipedia factual research.
  2. **Multi-Domain Intent Classification Taxonomy:**
     * Formalize dedicated typed intent categories:
       - `COMMUNICATION`: Email drafting, sending, and Gmail search.
       - `WORKSPACE`: Google Calendar events, Google Docs creation, Google Sheets logging, and Google Drive storage.
       - `BUSINESS_INFORMATION`: Anshu Computer & Tax Consultancy client records, tax filings (ITR, GST, PF), and Facebook marketing.
       - `PERSONAL_INFORMATION`: Personal identity, family graph, private phone, address, and personal profile details.
       - `SYSTEM_INFORMATION`: BABU internal architecture, code, logs, ADRs, health dashboard, and uptime telemetry.
       - `CONVERSATION`: Greetings, casual chitchat, thank-you pleasantries, and clarification gates.
       - `PUBLIC_INFORMATION`: Explicit web search (Tavily), Wikipedia lookup, and public facts/news.
  3. **Data Boundary Enforcement:**
     * Update `services.py:is_private_data_query()` to recognize `COMMUNICATION` and `WORKSPACE` as private data, strictly prohibiting external web searches for these domains.
     * Expand `graph.py:PRIVATE_QUERY_TYPES` to ensure private actions are never bypassed into simple lookup shortcuts.
  4. **Plan Cache Poisoning Prevention & Auto-Purge:**
     * Disallow writing error, fallback, or refusal graphs (`JSON_ERROR`, `FALLBACK`, `could not be planned securely`) into `planned_graphs_cache`.
     * On cache lookup, detect poisoned or refusal plans, purge them immediately from the database, and force a fresh strategic planning cycle.
  5. **Universal Anti-Hallucination Invariant:**
     * Enforce the anti-hallucination directive globally in `pa_node` across all dialogue modes (conversational, single-task, and multi-task). If `[Automation Result]` is missing, the model is strictly forbidden from claiming any external action was performed.
* **Consequences:**
  * Complete elimination of cache poisoning and plan collision under generic buckets.
  * Flawless classification of email, calendar, docs, sheets, and greetings.
  * Total prevention of PA action hallucinations.
  * Clean, grounded public information retrieval via Tavily API and Wikipedia.

---

## ADR-104: Direct Action Dispatch on Operator Approval, Elimination of Re-Planning Loops, and Parameter Integrity Verification
* **Status:** Accepted / Live in Production
* **Context:** When an operator authorized a pending action (e.g., sending an email, posting to Facebook, creating an event, deleting a document) by replying with `1` / `approve` / `confirm` or clicking an inline Telegram button, the system previously contained legacy code attempting to update `planned_graphs_cache` and calling `invoke_babu(user_query)`. This introduced severe defects:
  1. Re-invoking `invoke_babu(user_query)` restarted the entire LangGraph workflow from `router_node` -> `planner_node`. Because action and communication queries explicitly bypass the plan cache for safety (`is_action_query`), the 120B model was re-prompted to generate a brand new DAG from scratch. In this newly generated DAG, the execution task had `approved = False`, causing the system to pause for approval again and trap the operator in an infinite approval loop without ever sending the email or executing the action.
  2. If upstream drafting tasks had created a formatted message (with `**Subject:**` and `**Body:**`), re-planning caused loss or corruption of the drafted context, or in earlier versions injected fallback placeholder strings like `[needs_research_context]` or `(no research/analysis context found)`.
* **Decision:**
  1. **Direct Action Dispatch on Operator Approval:**
     * Completely eliminate `update_cached_graph_approval` and `invoke_babu(user_query)` from all approval channels in `bot.py` (Telegram text approval, Telegram Class C confirmation, Telegram inline callback query, and Web chat API).
     * Upon operator approval, `bot.py` immediately pops the pending action from memory and database, resolves the drafted parameters, and directly executes `execute_google_action(action, params)`.
  2. **Upstream Draft Preservation & Topological Ordering:**
     * In `graph.py`, ensure upstream read-only research and drafting tasks (e.g. `writing`, `analysis`, `research`) execute in topological order before pausing for mutating execution tasks, guaranteeing the draft is fully populated.
     * In `pa_node`, parse `**Subject:**` and `**Body:**` using regular expressions and synchronize them into `_pending_actions[session_id]["params"]` and persistent storage.
  3. **Deterministic Parameter Resolution (`resolve_action_params`):**
     * Ensure `resolve_action_params` in both `departments.py` and `bot.py` cleanly separates the subject line from the email body using regular expressions, preventing cross-contamination where the entire draft is placed into the subject or the subject line is duplicated into the body.
  4. **Safety Firewall at Dispatch Boundary:**
     * In `google_service.py:execute_google_action`, enforce a hard safety firewall that rejects execution if `body` contains unresolved placeholder tokens (e.g., `[needs_research_context]`, `(no research/analysis context found)`, `information unavailable`) or if recipient email fails RFC format validation.
  5. **Audit Ledger Lifecycle Tracking:**
     * Log `APPROVAL_GRANTED`, followed by `TASK_COMPLETED` or `TASK_FAILED` events directly into the execution ledger in `services.py`.
* **Consequences:**
  * Zero-latency, immediate dispatch of approved external workspace and communication actions.
  * Complete elimination of redundant 120B model re-planning cycles on approval.
  * Guaranteed parameter integrity: clean subject lines and complete body text without placeholders.
  * Strict compliance with constitutional governance invariants (Understanding $\neq$ Authorization $\neq$ Execution).

---

*BABU ADR Book Volume 7 — Updated September 2026*

