# BABU Architectural Decision Record (ADR) Book - Volume 5 (August 2026)

## 📌 Executive Summary
Volume 5 documents the production integration of BABU's 2-Way Social Media Engine, Meta Facebook Webhook Listener, Decoupled Business Context Architecture, Automated Token Self-Healing Engine, and Meta Developer Portal Live Publication.

---

## 🏛️ Key Architectural Decisions & Upgrades

### 1. Decoupled Business Context (`business_profile.json`)
* **Decision**: Decoupled public business knowledge base (`business_profile.json`) from private personal identity (`user_profile.json`).
* **Rationale**: Ensures public customer auto-replies (Facebook Comments & DMs) only access public consultancy FAQs, contact details, and location without leaking private owner data or personal credentials.
* **Files**: `business_profile.json`, `services.py` (`load_business_profile()`, `get_business_profile_text()`).

### 2. 2-Way Meta Webhook Engine (`/webhook/facebook`)
* **Decision**: Implemented high-throughput asynchronous HTTP GET handshake & POST event dispatchers in `bot.py` (`HealthHandler`).
* **Capabilities**:
  * **Handshake Verification**: `GET /webhook/facebook` handles Meta `hub.challenge` verification.
  * **Messenger DMs**: `pages_messaging` integration for 24/7 AI conversation.
  * **Post Comments**: `feed` webhook listener processes real-time post comments.
  * **Private DM Fallback**: Dispatches private Messenger replies using `POST /me/messages` with `recipient: {"comment_id": cid}` when public replies are restricted.

### 3. Automated Token Self-Healing & Never-Expiring Token System
* **Decision**: Converted Facebook Page Access Tokens into permanent Never-Expiring Page Tokens (`expires_at: 0`) and added `auto_refresh_facebook_token()` runtime self-healing.
* **Mechanism**:
  * Exchanged short-lived tokens via `GET /oauth/access_token?grant_type=fb_exchange_token` using `FACEBOOK_APP_SECRET`.
  * Automated Page re-subscription via `POST /{page_id}/subscribed_apps` (`subscribed_fields='messages,feed'`).
  * Secured secrets by reading exclusively from Render environment variables (`os.environ.get("FACEBOOK_APP_SECRET")`).

### 4. International Character & Unicode Safe Logging
* **Decision**: Implemented safe ASCII/UTF-8 character string encoding across Webhook event loggers.
* **Rationale**: Prevents server/console crashes (`UnicodeEncodeError`) when processing Hindi/Devanagari names (e.g. `सत्येंद्र राजपूत कुौंदा`) or international customer names.

### 5. Meta Developer App Live Publication (`JARVIS`)
* **Decision**: Configured Meta App `JARVIS` (ID `947606281427456`) under Business Portfolio `907719349095015` and published it to Live Mode.
* **Assets**: Generated & hosted official `privacy_policy.html` and 1024x1024 App Icon on GitHub Gist.

---

## 📈 System Metrics & Status
* **Status**: 100% Operational & Live on Render (`babu-tf49.onrender.com`).
* **Outbound Marketing**: Automated daily 3D flyer creation, Facebook Timeline publishing, Google Drive backup.
* **Inbound Customer Support**: 24/7 AI Messenger DMs & Post Comment Auto-Reply.
