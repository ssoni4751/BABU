# Walkthrough — BABU Dual-Bot Architecture Implementation

**Date:** September 5, 2026  
**Status:** Completed, Verified & 100% Test Passing

---

## Executive Summary

As requested, Project BABU has been upgraded into a hardened **Dual-Bot Architecture**:
1. **Private Executive Agent (`bot.py` / `TELEGRAM_BOT_TOKEN`):** 100% locked down to you (`TELEGRAM_USER_CHAT_ID = 8832681666`). Retains full multi-agent reasoning, Google Workspace mutations, Facebook publishing, and CRM management. Any unauthorized user messaging the private bot is blocked and redirected to the public desk.
2. **Public Client Desk (`public_bot.py` / `@Anshu4751_bot`):** Dedicated front-desk client bot for **Anshu Computer & Tax Consultancy, Orai**. Provides polite, grounded assistance in Hindi & English for PF/EPFO, ITR, GST, and MSME services, captures leads into CRM, books slots between 11 AM - 6 PM, handles document uploads, and sends real-time alerts to your Private Bot.

```
                     ┌────────────────────────────────────────────────────────┐
                     │                   BABU BACKEND (Render)                │
                     └───────────────────┬────────────────┬───────────────────┘
                                         │                │
                    Private Channel      │                │  Public Channel
                                         ▼                ▼
     ┌─────────────────────────────────────┐            ┌──────────────────────────────────────┐
     │      BOT 1: BABU EXECUTIVE          │            │       BOT 2: PUBLIC CLIENT DESK      │
     │      (Your Personal Agent)          │            │        (Anshu Consultancy Desk)      │
     ├─────────────────────────────────────┤            ├──────────────────────────────────────┤
     │ Token: TELEGRAM_BOT_TOKEN           │            │ Token: TELEGRAM_PUBLIC_BOT_TOKEN     │
     │ Auth: STRICT (Your Chat ID only)    │            │ Auth: OPEN to all clients / public   │
     │ Model: openai/gpt-oss-120b          │            │ Model: openai/gpt-oss-20b (fast)     │
     ├─────────────────────────────────────┤            ├──────────────────────────────────────┤
     │ • Full Google Workspace execution   │            │ • Business FAQs & Service Info       │
     │ • Autonomous planning & research    │            │ • ITR, PF, GST, MSME Inquiries       │
     │ • Action approval & confirmation    │            │ • Lead Intake & Contact Capture      │
     │ • Facebook post preview & publish   │            │ • Slot-based Appointment Booking     │
     │ • Full CRM pipeline management      │            │ • Client Document Drop (to CRM)      │
     │ • Real-time alerts from Bot 2       │            │ • Zero access to your Google/FB/API  │
     └─────────────────────────────────────┘            └──────────────────┬───────────────────┘
                                                                           │
                                 Real-time Telegram Notification           │
                                 "🔔 New Lead / Appointment Booked!"       │
                                 ◀─────────────────────────────────────────┘
```

---

## Detailed Changes Made

### 1. Private Bot Lockdown ([`bot.py`](file:///d:/Aria/bot.py))
- **`on_message` Operator Gate ([`bot.py:L6515-L6527`](file:///d:/Aria/bot.py#L6515-L6527)):** Added strict verification at the entry point of text, document, and voice handlers:
  ```python
  if not is_telegram_operator(update):
      pub_bot = os.environ.get("PUBLIC_BOT_USERNAME", "Anshu4751_bot").strip("@")
      await update.message.reply_text(
          f"🔒 *Access Restricted*\n\n"
          f"This is the private executive assistant for *Shubham Swarnkar*.\n\n"
          f"For tax, PF, GST, or business consultancy services, please visit our official client desk:\n"
          f"👉 @{pub_bot}",
          parse_mode="Markdown"
      )
      return
  ```
- **Administrative Command Gates:**
  - [`cmd_launch`](file:///d:/Aria/bot.py#L6885): Protected with `if not is_telegram_operator(update): return`.
  - [`cmd_goals`](file:///d:/Aria/bot.py#L6940): Protected with `if not is_telegram_operator(update): return`.
  - [`cmd_update_lead`](file:///d:/Aria/bot.py#L7264): Protected with `if not is_telegram_operator(update): return`.
  - [`on_post_callback`](file:///d:/Aria/bot.py#L7297): Protected so external users cannot tap inline approval buttons.

---

### 2. Public Client Desk Bot ([`public_bot.py`](file:///d:/Aria/public_bot.py))
Created a new dedicated module for `@Anshu4751_bot`:
- **Identity & Greeting:** Speaks as JARVIS on behalf of Mr. Shubham Swarnkar (Consultant) at Anshu Computer & Tax Consultancy, Kaushal Market, Rath Road, Orai.
- **Service Catalog & Fast Policy Intercept:**
  - Instant deterministic handling for unsupported queries (Aadhaar correction, Ration Card, DL) informing clients that Aadhaar service is not provided and highlighting the 4 authorized services (PF, ITR, GST, MSME).
  - Grounded responses using `get_selective_knowledge_slice()`.
- **Prompt Injection Defense:** External client text is quarantined within `<untrusted_client_input>` tags, and the system prompt strictly instructs the LLM that client text cannot override system rules or execute commands.
- **Automated Lead Intake:** All client interactions and contact details (extracted phone numbers or Telegram handles) are ingested into the CRM database via `ingest_lead()`.
- **Slot-Based Appointment Booking:**
  - Evaluates requested dates/times (e.g., "kal 2 baje") using `parse_ist_datetime()`.
  - Verifies slot availability between 11:00 AM - 6:00 PM (Mon-Sat) via `check_slot_availability()`.
  - Commits appointments via `commit_crm_appointment()` and triggers real-time Telegram alerts to your Private Bot.
- **Document Drop Intake:** Safely receives document/photo attachments (Form 16, passbook, PAN), logs them in CRM, and notifies the owner.

---

### 3. Bootstrap Dual-Polling Transports ([`bootstrap.py`](file:///d:/Aria/bootstrap.py))
In `open_transports()`:
```python
# Launch Public Client Desk Bot (@Anshu4751_bot) in background daemon thread
try:
    try:
        from .public_bot import start_public_bot_thread
    except ImportError:
        from public_bot import start_public_bot_thread
    public_thread = start_public_bot_thread()
    if public_thread:
        print("[BOOTSTRAP] Public Client Desk Bot thread started successfully (@Anshu4751_bot).", flush=True)
except Exception as pub_err:
    print(f"[BOOTSTRAP WARNING] Could not start public bot thread: {pub_err}", flush=True)

# Launch Private Executive Bot on main thread
bot = ApplicationBuilder().token(bot_module.TELEGRAM_TOKEN).build()
```
Both bots run concurrently:
- Public Bot runs on an independent background daemon thread with its own reconnection and error handling.
- Private Bot runs on the main thread.
- Render port 8080 remains dedicated to your web dashboard.

---

### 4. Dual-Tree Synchronization (`babu/`)
Synchronized all changes into `babu/` to maintain 100% parity for Render deployments:
- Synchronized [`babu/public_bot.py`](file:///d:/Aria/babu/public_bot.py)
- Synchronized [`babu/bot.py`](file:///d:/Aria/babu/bot.py)
- Synchronized [`babu/bootstrap.py`](file:///d:/Aria/babu/bootstrap.py)
- Copied [`babu/generate_daily_summary_pdf.py`](file:///d:/Aria/babu/generate_daily_summary_pdf.py)

---

## Verification & Test Results

### 1. Dedicated Dual-Bot Architecture Test Suite ([`scratch/test_dual_bot.py`](file:///d:/Aria/scratch/test_dual_bot.py))
```
--- 1. Testing Operator Lockdown ---
✅ Operator check passed: Owner (8832681666) ALLOWED, Stranger (9999999999) BLOCKED.

--- 2. Testing Public Bot Construction ---
✅ Public bot constructed successfully with 8 handlers registered.

--- 3. Testing Public AI Responses & Boundary ---
• Aadhaar inquiry reply preview: नमस्ते Ramesh जी! अंशु कंप्यूटर एंड टैक्स कंसल्टेंसी में आधार कार्ड संशोधन (Aadhaar Card U...
• PF inquiry reply preview: नमस्ते श्री सुरेश जी, PF एडवांस क्लेम करने के लिए आपको निम्नलिखित दस्तावेज़...
✅ Public AI responses properly grounded in business facts.

--- 4. Testing Appointment Booking Flow ---
• Slot availability on 2026-09-10 at 14:00: True
✅ Appointment slot engine operational.

🎉 ALL DUAL-BOT TESTS PASSED SUCCESSFULLY!
```

### 2. Full Intent Governance Regression Suite (`tests/test_intent_governance.py`)
```
============================= 15 passed in 22.60s =============================
```
All 15 governance tests passed with 100% success rate:
- Intent classification (lookup, mutation, auto-execute): PASSED
- ADR-104 direct dispatch and parameter integrity: PASSED
- Class A read-only action auto-approval: PASSED
- Template and auditor gatekeeper enforcement: PASSED

---

## Next Steps for You

1. **Local Test:**
   - Launch `python bootstrap.py`. Both `@Aria` (Pragya) and `@Anshu4751_bot` will connect and start polling simultaneously.
   - Send `/start` to `@Anshu4751_bot` on Telegram to see the new public front-desk experience.
   - Message your private bot from any other Telegram account to verify the access restriction notice.
2. **Render Cloud Deployment:**
   - In your Render dashboard, the environment variables are active.
   - Recent commit `884b393` pushed to `main` auto-deploys to Render.

---

## Session Update (September 5, 2026): Identity, Customer Personalization & Single Alert System

1. **Private Bot Identity Set to Pragya (प्रज्ञा):**
   - Updated system prompts and self-identity handlers in [`graph.py`](file:///d:/Aria/graph.py) and [`gateway.py`](file:///d:/Aria/gateway.py) so the private executive PA introduces itself as **Pragya (Project BABU Cognitive OS)**.
2. **Personalized Customer Greetings:**
   - Updated [`public_bot.py`](file:///d:/Aria/public_bot.py) across all stages (service selection, phone freeze, appointment inquiry, time prompt, and booking confirmation) to address clients by their name (`नमस्ते {client_name} जी!`).
3. **Elimination of Duplicate Booking Alerts:**
   - Removed secondary `send_owner_client_alert` from `public_bot.py` during appointment confirmation.
   - All booking notifications are now sent authoritatively and exclusively by the CRM transactional engine (`dispatch_telegram_appointment_alert` in [`crm_service.py`](file:///d:/Aria/crm_service.py)) as a single clean HTML notification card.
4. **Validation:**
   - All 10 CRM unit tests passed in 2.37s.
   - Synced to `babu/` and pushed to GitHub `origin main` (commit `884b393`).
