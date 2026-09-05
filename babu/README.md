# Project BABU — Governed Cognitive OS & Dual-Bot Platform

**Version:** 4.0.0 (Production Release)  
**Author:** Shubham Swarnkar (Anshu)  
**Operational Scope:** Personal Executive Cognition & Anshu Computer & Tax Consultancy, Orai  
**Deployment Platform:** Render Cloud (Web Service)  

---

## 🌟 Overview

**Project BABU** is a governed, stratified 9-layer Cognitive Operating System designed to unify private executive intelligence with public commercial customer management. Built on LangGraph, Groq open-weights (`openai/gpt-oss-120b` and `openai/gpt-oss-20b`), and a PostgreSQL/SQLite hybrid persistence tier, the platform delivers zero-hallucination execution, human-supreme governance, and automated client acquisition.

The system is hard-partitioned into a **Dual-Bot Architecture**:
1. **Pragya (Private Executive PA):** A high-cognition personal companion and administrative terminal running on `TELEGRAM_BOT_TOKEN`. Strictly locked to the business owner (`TELEGRAM_USER_CHAT_ID = 8832681666`).
2. **Public Client Desk (@Anshu4751_bot):** An automated front-desk digital assistant running on `TELEGRAM_PUBLIC_BOT_TOKEN` for **Anshu Computer & Tax Consultancy, Orai**, managing service discovery, 10-digit mobile intake, office appointment booking, and client document drop-off.

```
                     ┌────────────────────────────────────────────────────────┐
                     │                   BABU BACKEND (Render)                │
                     └───────────────────┬────────────────┬───────────────────┘
                                         │                │
                    Private Channel      │                │  Public Channel
                    (Owner Only)         │                │  (Prospective Clients)
                                         ▼                ▼
     ┌─────────────────────────────────────┐            ┌──────────────────────────────────────┐
     │      BOT 1: PRAGYA (EXECUTIVE)      │            │       BOT 2: PUBLIC CLIENT DESK      │
     │      (Personal Cognitive OS)        │            │        (@Anshu4751_bot)              │
     ├─────────────────────────────────────┤            ├──────────────────────────────────────┤
     │ Token: TELEGRAM_BOT_TOKEN           │            │ Token: TELEGRAM_PUBLIC_BOT_TOKEN     │
     │ Auth: STRICT (Owner Chat ID only)   │            │ Auth: OPEN to all clients / public   │
     │ Model: openai/gpt-oss-120b          │            │ Model: openai/gpt-oss-20b (Fast)     │
     ├─────────────────────────────────────┤            ├──────────────────────────────────────┤
     │ • Full Google Workspace execution   │            │ • 4 Core Categories (PF/Tax/GST/Gen) │
     │ • Autonomous planning & research    │            │ • Fast Aadhaar / License Intercept   │
     │ • Action approval & confirmation    │            │ • 10-Digit Mobile Intake & Freeze    │
     │ • Facebook post preview & publish   │            │ • Slot Availability Engine (11-6 IST)│
     │ • Full CRM pipeline management      │            │ • Client Document Drop (Passbooks)   │
     │ • Authoritative alerts from CRM     │            │ • Zero access to private tools/memory│
     └─────────────────────────────────────┘            └──────────────────┬───────────────────┘
                                                                           │
                                 Verified Database Commit                  │
                                 "🚨 NEW APPOINTMENT SCHEDULED!"           │
                                 ◀─────────────────────────────────────────┘
```

---

## 🏛️ 9-Layer Cognitive OS Architecture

The core engine follows a strict separation of concerns (*Understanding $\neq$ Authorization $\neq$ Execution*):

| Layer | Component | Module | Responsibility |
|---|---|---|---|
| **L1** | **Interface Gateway** | [`gateway.py`](file:///d:/Aria/gateway.py) | Dual-Bot Telegram ingestion, Facebook webhooks, health server, and zero-latency chitchat cache. |
| **L2** | **Intent Classification** | [`services.py`](file:///d:/Aria/services.py) | Topology-aware demand classification into 7 typed domains (`COMMUNICATION`, `WORKSPACE`, `BUSINESS`, `PERSONAL`, `SYSTEM`, `CONVERSATION`, `PUBLIC`). |
| **L3** | **Constitution & Governance** | `e0/` | Human-supreme authority, Class A/B/C action risk grading, and Epistemic Immune System. |
| **L4** | **Planning & Orchestration** | [`graph.py`](file:///d:/Aria/graph.py) | Topological DAG task planner with dependency resolution and plan cache poisoning defenses. |
| **L5** | **Department Swarm** | [`departments.py`](file:///d:/Aria/departments.py) | Specialized workers: Research, Information, Analysis, Writing, Execution, and PA. |
| **L6** | **Bipartite Auditor** | [`auditor.py`](file:///d:/Aria/auditor.py) | Pre-execution gatekeeper (parameter integrity) + Post-execution semantic validator. |
| **L7** | **Memory & Knowledge** | `memory/` | LangGraph SQLite checkpointer, RAG vector retrieval, and K0–K7 stratified memory hierarchy. |
| **L8** | **Commercial CRM Engine** | [`crm_service.py`](file:///d:/Aria/crm_service.py) | Independent commercial plane: leads (`babu_leads`), appointments (`babu_followups`), and slot anti-collision. |
| **L9** | **Self-Improvement** | [`governance.py`](file:///d:/Aria/governance.py) | Dynamic template promotion/retirement ($E[\text{Temp}]$), failure analysis, and ADR knowledge propagation. |

---

## 💼 Commercial CRM & Front-Desk Funnel

### 1. Authoritative Service Catalog
Derived from `business_profile.json`, the public bot offers 4 core categories:
1. 🏢 **PF Consultancy (Primary Specialization):** EPFO claim settlements (Form 19, 10C, 31), UAN activation/transfer, joint declarations, and KYC/DOB corrections.
2. 📑 **Tax Services:** Income Tax Return (ITR-1, 2, 4) filing, tax computations, refund status tracking, and notice resolution.
3. 📊 **GST Services:** New GST registration, monthly/quarterly return filings (GSTR-1, 3B), and department notice resolution.
4. 🌐 **General Services:** MSME Udyam registration, Jeevan Pramaan (digital life certificates for pensioners), passport applications, and PAN card services.

> [!NOTE]
> **Aadhaar Policy:** The firm does NOT provide Aadhaar card updates, ration cards, or driving license services. Inquiries containing these keywords are immediately caught by a deterministic fast-intercept and politely redirected to the 4 supported categories.

### 2. Stateful 4-Phase Lead Progression
```
[Inbound Inquiry]
       │
       ▼
[Phase 1: Service Selection]  ──► Client selects PF / Tax / GST / General ──► Service Frozen
       │
       ▼
[Phase 2: Contact Intake]     ──► Client sends 10-digit mobile number     ──► Contact Frozen
       │
       ▼
[Phase 3: Slot Validation]    ──► Client proposes day/time (Mon-Sat, 11 AM - 6 PM IST)
       │
       ▼
[Phase 4: Slot Anti-Collision]──► Check database for conflicting bookings:
                                  • If open: Commit atomically to DB
                                  • If busy: Propose alternate open slots
       │
       ▼
[Phase 5: Single CRM Alert]   ──► Single HTML card dispatched to Pragya Executive Bot
```

---

## 🎮 Command Console Reference

### Private Bot (Pragya — Operator Only)
* `/crm`: Open the interactive Anshu Consultancy CRM pipeline summary.
* `/leads`: Inspect recent customer inquiries, contact details, and scheduled meetings.
* `/add_lead <name> <phone>`: Manually register a new walk-in client.
* `/postnow`: Compose, preview, and publish a marketing post to the official Facebook Page.
* `/model`: Inspect active LLM models or trigger failover switching.
* `/stats`: Display runtime telemetry, uptime, and template performance.
* `/clear`: Reset conversational session memory.
* `/promote <sig> <goal_id>`: Promote a validated task DAG into muscle memory ($E[\text{Temp}]$).
* `/retire <sig>`: Demote or retire a legacy workflow template.

### Public Client Desk (@Anshu4751_bot — Open)
* `/start` / `/help`: Interactive welcome message with 4-category service selection keyboard.
* `/services`: Detailed catalog of PF, Tax, GST, and General compliance services with required document checklists.
* `/contact` / `/address`: Office address (Kaushal Market, Rath Road, Orai), helpline number, and working hours (Mon–Sat, 11:00 AM – 6:00 PM).

---

## ⚙️ Environment Configuration

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token for the Private Executive Bot (Pragya). |
| `TELEGRAM_USER_CHAT_ID` | Telegram chat ID of the authorized owner (`8832681666`). |
| `TELEGRAM_PUBLIC_BOT_TOKEN` | Bot token for the Public Client Desk Bot (`@Anshu4751_bot`). |
| `PUBLIC_BOT_USERNAME` | Username of the public bot (`Anshu4751_bot`). |
| `GROQ_API_KEY` | API key for primary open-weight inference (`gpt-oss-120b` & `gpt-oss-20b`). |
| `DATABASE_URL` | Optional PostgreSQL connection string (Render Postgres); falls back to SQLite. |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Meta Graph API token for automated social media marketing. |
| `FACEBOOK_APP_SECRET` | Secret used for HMAC-SHA256 inbound webhook signature verification. |
| `TAVILY_API_KEY` | Real-time web search grounding for external public information. |

---

## 🧪 Testing & Verification

Run the full automated test suite covering intent classification, CRM lead funnel, IST datetime validation, and slot anti-collision:

```bash
# Run CRM Subsystem and Slot Anti-Collision Suite (10 tests)
python -m unittest tests/test_crm_subsystem.py

# Run Intent Governance & ADR Compliance Suite (15 tests)
pytest tests/test_intent_governance.py

# Run Dual-Bot Operational Sanity Check
python scratch/test_dual_bot.py
```

---

## 🚀 Running Locally & in Production

### Local Startup
```bash
# Starts Web Dashboard, Private Executive Bot, and Public Client Desk Bot concurrently
python bootstrap.py
```

### Production Deployment (Render)
Pushes to branch `main` automatically trigger builds on Render. Both bots connect via non-conflicting polling loops, maintaining 24/7 uptime with automated container recovery.
