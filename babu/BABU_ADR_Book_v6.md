# BABU Architectural Decision Record (ADR) Book — Volume 6

**Canonical Reference for Governing Control Plane, Bidirectional Meta Integration, Multi-Provider Failover, and Truthful Degradation**

---

## ADR-086: Adoption of BABU Manifesto as System Architecture Standard
* **Status:** Accepted / Canonical
* **Context:** BABU is a governed control plane between a human operator and the digital world, operating via the 7-layer pipeline: `User → Query Classification → Intent Classification → Governance → Planning / Direct Route → Execution → Verification / Response`.
* **Decision:** Formally adopt the 25-point BABU Manifesto (`BABU_Manifesto_2026.md`) as the system's North Star architectural doctrine.
* **Consequences:** All future features, integrations, and tools must comply with the Manifesto: Understand precisely, execute deliberately, never pretend, fail honestly, learn from operation, and preserve human authority.

---

## ADR-087: Meta Ecosystem Bidirectional Event-Driven Control Plane
* **Status:** Accepted / Live in Production
* **Context:** Conventional integrations perform outbound automation (`BABU → Meta`). The Manifesto directs expanding Meta integrations into a unified, bidirectional, event-driven control plane.
* **Decision:** Deployed live Webhook ingestion (`GET/POST /webhook/facebook`) handling `pages_messaging` DMs and `feed` comments, with automatic Private Reply fallback via `POST /me/messages`.
* **Consequences:** Converts isolated outbound publishing into a true closed-loop: `External World → BABU → Governance & Classification → BABU → External World`.

---

## ADR-088: Dynamic Multi-Provider Intelligence & Rate-Limit Resilience
* **Status:** Accepted / Live in Production
* **Context:** Single-provider dependence causes system fragility when rate-limits or 404/413 payload errors occur on free tiers.
* **Decision:** Established a 3-tier provider failover chain:
  1. **Primary**: Groq Cloud API (`groq/compound-mini`, `groq/compound`, `openai/gpt-oss-120b`, `qwen/qwen3.6-27b`)
  2. **Secondary**: NVIDIA NIM API (`nvidia/meta/llama-3.3-70b-instruct`)
  3. **Third**: Google Gemini Native API (`gemini-2.5-flash`)
  4. **OpenAI**: Native OpenAI completely removed.
* **Consequences:** Provider timeouts and quota limits are handled gracefully at runtime as recoverable infrastructure events.

---

## ADR-089: Truthful Degradation & Non-Simulation Principle
* **Status:** Accepted / Canonical
* **Context:** Conventional LLM apps simulate success or hallucinate outputs when API calls fail or credentials are missing.
* **Decision:** Enforce Truthful Degradation across all workers and responses. If a service endpoint fails or records are unavailable, BABU must state the limitation or error honestly rather than improvising or fabricating success.
* **Consequences:** Eliminates fake success markers, protects user trust, and provides accurate diagnostic telemetry.

---

## ADR-090: Deterministic Muscle Memory & Dynamic Planning Bypass
* **Status:** Accepted / Live in Production
* **Context:** Sending every request through expensive dynamic multi-agent LLM planning creates unnecessary latency and token consumption.
* **Decision:** Implement deterministic muscle memory fast-tracks:
  - Simple chitchat, greetings, status lookups, and direct commands bypass Layer 4 dynamic planning and route directly to capability handlers.
  - Complex multi-step requests invoke dynamic LLM DAG planning.
* **Consequences:** Drastically reduces average user response latency from ~4.5s to <0.3s for routine operational commands.

---

*BABU ADR Book Volume 6 — Published August 2026*
