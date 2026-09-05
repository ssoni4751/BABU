# BABU — Complete Execution Flow & Feature Map

---

## Visual Diagram

![BABU Execution Flow Diagram](C:/Users/LENOVO/.gemini/antigravity/brain/6d0c89f4-8e58-41cb-9549-ca0575e8a3a5/babu_execution_flow_1780836246308.png)

---

## Step-by-Step Execution Flow

### 1. Dual-Bot User Ingress (Telegram)
The platform operates two isolated Telegram bots running concurrently:

1. **Private Executive Agent — Pragya (`bot.py` / `TELEGRAM_BOT_TOKEN`):**
   * **Audience:** Strictly restricted to the owner (`TELEGRAM_USER_CHAT_ID = 8832681666`).
   * **Persona:** **Pragya (Project BABU Cognitive OS)**.
   * **Capabilities:** Full LangGraph 9-layer orchestration, Google Workspace mutations, Facebook publishing, system telemetry, memory, and CRM administration.
   * **Security:** Unauthorized senders trigger `is_telegram_operator()` rejection and are redirected to `@Anshu4751_bot`.

2. **Public Client Desk Bot (`public_bot.py` / `TELEGRAM_PUBLIC_BOT_TOKEN` / `@Anshu4751_bot`):**
   * **Audience:** Public clients and prospective customers of **Anshu Computer & Tax Consultancy, Orai**.
   * **Capabilities:** Service inquiry, stateful lead qualification, 10-digit mobile intake, office appointment booking (11 AM - 6 PM IST), and document intake.
   * **Isolation:** Zero access to Google Workspace, private memories, or administrative commands.

---

### 2. Private Bot Command Routing (Pragya)
The Executive Bot dispatches to the appropriate handler:

| Command | Handler | Purpose |
|---------|---------|---------|
| `/promote <sig> <goal_id>` | `cmd_promote` | Compile a workflow into a trusted template |
| `/retire <sig>` | `cmd_retire` | Permanently retire a template |
| `/launch` | `cmd_launch` | Launch the bot |
| `/clear` | `cmd_clear` | Clear conversation history |
| `/goals` | `cmd_goals` | Show active goals |
| `/stats` | `cmd_stats` | Show system statistics |
| `/model` | `cmd_model` | Switch LLM model |
| `/postnow` | `cmd_postnow` | Trigger social media post |
| `/crm` | `cmd_crm` | View Anshu Consultancy CRM pipeline digest |
| `/leads` | `cmd_leads` | View recent client inquiries and appointments |
| `/add_lead <name> <phone>` | `cmd_add_lead` | Register a new client lead |
| `/help` | `cmd_help` | Show help text |
| *(text/voice)* | `on_message` | Normal message → LangGraph Planner |

---

### 3. Public Client Desk Stateful CRM Funnel (`public_bot.py`)
```
Public Telegram Message / Callback
  │
  ├── 1. Aadhaar / Unsupported Service Fast-Intercept
  │      Detects: 'aadhaar', 'ration', 'driving license'
  │      → Polite rejection in Hindi + display 4 supported categories
  │
  ├── 2. Step 1: Service Category Selection & Freeze (freeze_lead_service)
  │      Options: [1] PF Consultancy (Primary)  [2] Tax Services (ITR)
  │               [3] GST Services              [4] General Services
  │      → Freezes service category on lead record, transitions to AWAITING_CONTACT
  │
  ├── 3. Step 2: Contact Intake & Freeze (freeze_lead_contact)
  │      Extracts: 10-digit mobile number [6-9]\d{9}
  │      → Freezes contact_info, transitions to AWAITING_APPOINTMENT_SLOT
  │      → Re-prompts client by name: "धन्यवाद {client_name} जी! कृपया पसंदीदा दिन व समय बताएं"
  │
  ├── 4. Step 3: Appointment Slot Parsing & Validation (parse_ist_datetime)
  │      Checks:
  │      - Explicit date + time expressions in IST (Asia/Kolkata)
  │      - Rejects phone-only inputs (avoids premature auto-booking)
  │      - Validates office window: Mon-Sat, 11:00 AM - 6:00 PM (Sunday Closed)
  │
  ├── 5. Step 4: Slot Anti-Collision Check (check_slot_availability)
  │      - Inspects babu_followups for conflicts at requested hour
  │      - If booked: Suggests alternative open slots (e.g. 11 AM, 3 PM)
  │
  └── 6. Step 5: Atomic Commit & Single Alert Dispatch
         - commit_crm_appointment():
             • Updates babu_leads status = 'APPOINTMENT_SCHEDULED'
             • INSERT INTO babu_followups (protected by DB partial unique index)
             • Records event to babu_temporal_timeline & execution_ledger
             • COMMIT Transaction
         - dispatch_telegram_appointment_alert():
             • Sends single verified HTML alert card to business owner on Telegram
         - Client Confirmation:
             • Warmly greets by name ({client_name} जी) with booking receipt & required documents
```

---

### 4. Inbound Social CRM Conversational Funnel (Facebook / Messenger)
```
Customer Message (Comment / DM)
  → Webhook Ingestion (process_facebook_webhook_event in social_media.py)
  → Retrieve Conversational State & Lead History (get_lead_by_source_ref in crm_service.py)
  → LLM Entity Extraction: service, date_expr, time_expr, intent, phone
  → Deterministic Business Validation (parse_ist_datetime):
      → Asia/Kolkata IST relative date resolution (kal/parso/somwar -> YYYY-MM-DD HH:MM)
      → Enforce Working Window: Mon-Sat, 11:00 AM <= time <= 6:00 PM (Sunday Closed)
  → Slot Availability & Conflict Check (check_slot_availability):
      → Check babu_followups for occupied slots (within +-30 mins)
      → If occupied: generate alternative available slots
  → CRM Transactional Commit (commit_crm_appointment):
      → Update babu_leads status = 'APPOINTMENT_SCHEDULED'
      → INSERT INTO babu_followups (protected by DB partial unique index)
      → COMMIT Transaction
  → Real-Time Telegram Alert (dispatch_telegram_appointment_alert):
      → Dispatched to owner strictly AFTER verified DB commit
  → Customer Reply:
      → Grounded with Selective Knowledge Slice (get_selective_knowledge_slice)
```

---

### 3. Promote Flow (`/promote`)
```
User → /promote sig goal_id
  → Validate args (signature + goal_id)
  → Fetch GoalGraph from execution_ledger
  → INSERT/UPDATE trusted_templates (status = ACTIVE)
  → Reply: ✅ Promotion Successful  OR  ❌ Error
```

### 4. Retire Flow (`/retire`)
```
User → /retire sig
  → Validate template signature
  → UPDATE trusted_templates SET status = 'RETIRED'
  → Reply: ✅ Template Retired  OR  ❌ No matching template
```

### 5. Message Handling (Planner / Execution Engine & Class C Gates)
```
User sends a message
  → Webhook/Polling in bot.py receives payload
  → Invokes invoke_babu()
  → Gateway short-circuit (is_pure_greeting / is_deterministic_faq_query in gateway.py)
      → IF MATCHED → Directly output greeting/FAQ via PA response (bypass LLM/Planning)
  → Checks for pending actions under session (sync_pending_actions() from services.py)
      → IF PENDING ACTION EXISTS → Check confirmation input (gateway.py _is_approval_message / _is_reject_message)
          → IF APPROVED →
              → IF Class C action (delete) and state == 'approval'
                  → Transition state to 'confirmation', prompt Warning card, lock task, exit (Stage 2 Gate)
              → IF Class C action (delete) and state == 'confirmation' and input == 'confirm'/'2'
                  → Execute action payload, delete pending DB entry, output result
              → IF Class B action (mutate)
                  → Execute action payload, delete pending DB entry, output result
          → IF REJECTED / CANCELLED → Delete pending DB entry, report cancellation
          → Exit loop
  → Build template signature and query trusted_templates match (services.py)
      → IF FOUND → Load goal_graph_json (skip Strategic Planner)
      → IF NONE  → Call Strategic Planner planner_node (graph.py)
  → Compile LangGraph state graph (graph.py) with checkpointer saver (bot.py)
  → Dispatch task DAG execution via task_executor_node (graph.py)
      → For each task in graph:
          → Pre-execution audit validation (auditor.py)
          → IF FAILED → Halt and trigger anti-pattern failure logging
          → IF Class B or Class C action (requires approval)
              → Create pending action DTO (stage: 'approval')
              → Save pending action to DB (services.py db_save_pending_action)
              → Render Inline Keyboard approval buttons (bot.py)
              → Send approval request cards to user, pause graph, exit
          → Run worker node (workers.py)
          → Post-execution audit verification (auditor.py)
  → Capture latency, tokens (services.py extract_tokens / add_tokens)
  → Update template metrics in DB and run template promotion checks (governance.py)
```

---

## Feature Summary

| Feature | Status | Description |
|---------|--------|-------------|
| **Dual-Bot Architecture** | ✅ Active | Private Executive Bot (Pragya) + Public Client Desk Bot (`@Anshu4751_bot`) running concurrently |
| **Operator Lockdown** | ✅ Active | Strict authentication (`TELEGRAM_USER_CHAT_ID = 8832681666`) with external user redirection |
| **Stateful CRM Lead Funnel** | ✅ Active | 4-category service selection & freeze, 10-digit phone freeze, slot intake & booking |
| **Fast Aadhaar Intercept** | ✅ Active | Instant polite Hindi guidance on unsupported Aadhaar, ration, and driving license inquiries |
| **Deterministic IST Engine** | ✅ Active | Asia/Kolkata date/time validation enforcing office window (Mon-Sat, 11 AM - 6 PM) |
| **Slot Anti-Collision Engine** | ✅ Active | Database-level unique constraint preventing double-booking; auto-suggests alternative slots |
| **Single CRM Booking Alert** | ✅ Active | Atomic commit to `babu_leads` & `babu_followups` before dispatching authoritative HTML card |
| **E0-A Constitution** | ✅ Active | Read-only safety rules loaded from `e0/constitution.json` |
| **E0-B Governance** | ✅ Active | Policies loaded from `e0/policies.json` — thresholds for demotion/promotion |
| **E[Temp] Trusted Templates** | ✅ Active | Compiled workflows stored in `trusted_templates` table |
| **Template Promotion** | ✅ Active | `/promote` command creates ACTIVE templates from successful goal executions |
| **Template Retirement** | ✅ Active | `/retire` command permanently marks templates as RETIRED |
| **Auto-Demotion** | ✅ Active | Governance demotes templates after consecutive failures or low success rate |
| **Auto-Retirement** | ✅ Active | Templates already DEMOTED that fail again are auto-retired |
| **Execution Metrics** | ✅ Active | `average_execution_time` and `average_token_cost` tracked per template |
| **Micro-Auditor** | ✅ Active | Deterministic, sub-5ms safety checks — no LLM calls |
| **Semantic Validator** | ✅ Active | LLM-based post-execution audit for non-trivial tasks |
| **Chitchat Short-Circuit** | ✅ Active | Greetings like "hi" bypass LLM entirely for instant response |
| **Profile-Aware Responses** | ✅ Active | User profile context injected for personalized answers |
| **Search Cache** | ✅ Active | Web search results cached with TTL to avoid redundant lookups |
| **LLM Failover** | ✅ Active | Auto-fallback across Groq → OpenRouter → Gemini on rate limits |
| **Social Media Posting** | ✅ Active | Autonomous marketing swarm for social media content |
| **Google Services** | ✅ Active | Gmail, Sheets, Drive integration via `google_service.py` |
| **Class C Confirmation Gates**| ✅ Active | Strict double-confirmation transition (`Draft -> Preview -> Approve -> Warning/Confirm -> Execute`) |

---

## Database Schema

### `trusted_templates`
| Column | Type | Description |
|--------|------|-------------|
| `template_id` | TEXT (PK) | Unique template identifier (e.g., `T-A1B2C3`) |
| `template_signature` | TEXT (UNIQUE) | Pattern signature (e.g., `writing:execution:pa:send_email`) |
| `goal_graph_json` | TEXT | Serialized GoalGraph for replay |
| `version` | INTEGER | Incremented on re-promotion |
| `execution_count` | INTEGER | Total number of executions |
| `success_count` | INTEGER | Successful executions |
| `consecutive_failures` | INTEGER | Consecutive failure counter |
| `status` | TEXT | `ACTIVE` / `DEMOTED` / `RETIRED` |
| `promoted_from_goal_id` | TEXT | Source goal that created this template |
| `promotion_epoch` | INTEGER | Epoch counter at promotion time |
| `average_execution_time` | REAL | Running average of execution latency (seconds) |
| `average_token_cost` | REAL | Running average of token cost (USD) |
| `last_used` | TEXT | ISO timestamp of last execution |
| `created_at` | TEXT | ISO timestamp of creation |

### `execution_ledger`
| Column | Type | Description |
|--------|------|-------------|
| `event_id` | INTEGER (PK) | Auto-incrementing event ID |
| `session_id` | TEXT | User session identifier |
| `goal_id` | TEXT | Goal identifier (e.g., `G-20260607-123456`) |
| `task_id` | TEXT | Task within the goal |
| `department` | TEXT | Department that handled the task |
| `event_type` | TEXT | `PLANNING`, `EXECUTION`, `SEALING`, etc. |
| `state_before` | TEXT | State before transition |
| `state_after` | TEXT | State after transition |
| `metadata` | TEXT | JSON metadata (GoalGraph, results, etc.) |
| `timestamp` | TIMESTAMP | Event timestamp |

### `babu_leads` (Commercial CRM)
| Column | Type | Description |
|--------|------|-------------|
| `lead_id` | TEXT (PK) | Unique lead identifier (e.g., `LEAD-20260905-1234`) |
| `source_ref` | TEXT | Originating user ID (`tg_123456`, `fb_789`) |
| `channel` | TEXT | Channel source (`PUBLIC_TELEGRAM`, `FACEBOOK`, `MANUAL`) |
| `name` | TEXT | Client full name / Telegram handle |
| `contact_info` | TEXT | Validated 10-digit mobile number |
| `service_category` | TEXT | Frozen category: `PF`, `Tax`, `GST`, `General` |
| `status` | TEXT | `AWAITING_SERVICE`, `AWAITING_CONTACT`, `AWAITING_APPOINTMENT_SLOT`, `APPOINTMENT_SCHEDULED` |

---

## Codebase Modularization & Layers

To maintain operational sanity, the code is structured as follows:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        BABU v2 ARCHITECTURE LAYERS                     │
└────────────────────────────────────────────────────────────────────────┘
     │
     ├─► [Gateway: gateway.py] ───────► Greetings, FAQs, identity, health metrics.
     │
     ├─► [Core Services: services.py] ► DB, cache, token extraction, temporal logs.
     │
     ├─► [CRM Engine: crm_service.py] ► Leads, stateful funnel, slot booking, alerts.
     │
     ├─► [Public Bot: public_bot.py] ─► Front-desk bot (@Anshu4751_bot) intake.
     │
     ├─► [Private Bot: bot.py] ───────► Telegram operator interface (Pragya).
     │
     ├─► [Orchestration: graph.py] ───► LangGraph state graph, intent router, nodes.
     │
     └─► [Entry Point: bootstrap.py] ─► Concurrent service boot (Web, Private & Public Bots).
```

> [!NOTE]
> **Trust Hierarchy**: E0 (Constitution) > E[Temp] (Templates) > Runtime > Workers.
> Experience may create templates. Experience may NOT modify E0. Constitution governs everything.
