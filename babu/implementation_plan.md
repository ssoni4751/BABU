# Project BABU v2 — Implementation Plan (Updated: June 21, 2026)

## Overview

Project BABU has been fully upgraded from a legacy multi-agent swarm architecture to a
**governed, stratified 9-layer Cognitive Operating System (V2)** as specified in the
BABU v2 Vision Draft. This plan documents all completed work and the current
production-ready state.

---

## Completed Work

### Phase 1 — Full ARIA to BABU Identity Rename [COMPLETE]

All references to the old project name (ARIA) have been eliminated across the
entire codebase with case-preserving, total replacement.

| Variant | Replacement | Scope |
|---|---|---|
| ARIA | BABU | All .py, .md, .txt, .html, .json, .env files |
| Aria | Babu | Filenames, session transcripts, web artifacts |
| aria | babu | Database names, router keys, flow diagrams |

- Files/dirs renamed: 27 files + 2 directories (aria-web/ -> babu-web/)
- Files with content updated: 56 source/doc files
- Stale DB removed: aria_checkpoint.db (superseded by babu_checkpoint.db)
- .venv damage reverted: 15 third-party library files restored after accidental rename

---

### Phase 2 — V2 Cognitive OS Architecture Upgrade [COMPLETE]

#### babu/gateway.py upgrades
- get_dynamic_self_identity() now describes 9-layer Cognitive OS (not old swarm)
- get_babu_self_context() includes birth date (May 27, 2026), Bipartite Auditor, LangGraph strings
- Added missing import threading (fixed NameError in get_system_health_dashboard)
- Version bumped to 4.0.0 (V2 Cognitive OS)

#### 9-Layer Architecture
| Layer | Name | Responsibility |
|---|---|---|
| 1 | Interface Gateway | Telegram, Facebook, Web Dashboard ingestion |
| 2 | Intent Classification | Rule-based + LLM routing, Class A/B/C policies |
| 3 | Constitution & Governance | Human-supreme authority, Epistemic Immune System |
| 4 | Planning & Orchestration | Topological DAG planner, dependency resolution |
| 5 | Department Execution | Research, Information, Analysis, Writing, Execution |
| 6 | Bipartite Auditor | Pre-execution gatekeeper + Post-execution validator |
| 7 | Memory & Knowledge | SQLite/PostgreSQL checkpoint, RAG, Self-RAG |
| 8 | Telemetry & Observability | Latency tracking, failure logging, governance telemetry |
| 9 | Self-Improvement | Anti-pattern learning, immune lessons, ADR knowledge base |

---

### Phase 3 — Codebase Modularization (bot.py -> 4 modules) [COMPLETE]

The monolithic bot.py (460KB) was decomposed into:

| File | Layer | Responsibility |
|---|---|---|
| gateway.py | Layer 1 | Deterministic FAQ, greetings, self-identity, health dashboard |
| services.py | Layer 7 | DB, token costs, profile lookups, knowledge search, cache |
| graph.py | Orchestration | BabuState, all LangGraph nodes, workflow compile |
| bot.py | Entry Point | Telegram handlers, HTTP server, invoke_babu, callbacks |

Import strategy: All graph.py -> bot.py imports are lazy (inside function bodies)
to prevent circular dependency graph -> bot -> graph.

---

### Phase 4 — Class A / B / C Authorization Engine [COMPLETE]

| Class | Type | Policy |
|---|---|---|
| Class A | Read-only | Auto-approved, 0 tokens overhead |
| Class B | Mutating | User approval required before execution |
| Class C | Destructive | Double-confirmation: Approve -> Warning -> Confirm -> Execute |

---

### Phase 5 — Bug Fixes [COMPLETE]

| File | Fix |
|---|---|
| babu/graph.py | Added get_current_profile to services import (NameError on every greeting) |
| babu/graph.py | Removed erroneous top-level from .bot import block (circular import) |
| babu/gateway.py | Added import threading (NameError in get_system_health_dashboard) |
| babu_checkpoint.db | Deleted malformed DB, recreates fresh on next startup |

---

### Phase 6 — Environment Variables Updated [COMPLETE]

.env updated with full credentials (git-ignored, never committed):
- DATABASE_URL (Supabase PostgreSQL)
- GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY4
- OPENAI_API_KEY, OPENAI_API_KEY_2
- TAVILY_API_KEY
- GOOGLE_CREDENTIALS_JSON, GOOGLE_TOKEN_JSON
- All existing keys retained (Telegram, Groq, Facebook, OpenRouter)

---

### Phase 7 — Knowledge Base Documentation [COMPLETE]

All 15 BABU knowledge documents updated and committed to git:
BABU_ADR_Book_v1-v4, BABU_VISION_PLAN_2026, babu_cognitive_os_architectural_blueprint,
babu_execution_flow, governance_separation_plan, orchestration_architecture_report,
retrieval_and_auditability_assessment, model_architecture_report,
babu_immune_rules_report, babu_latency_analysis_report,
brain/constitution, brain/doctrine, brain/capabilities, brain/organization,
System_Information_Index

---

### Phase 8 — Repository Cleanup [COMPLETE]

Deleted from repo:
- All 4 PDF files (686 KB)
- 20 generated social-media images from babu/temp/
- Entire scratch/ folder (60+ one-off debug scripts, temp DBs)
- temp/ folder (bot_backup.py, social_media_backup.py)
- attached_assets/ (screenshots, paste snippets)
- backup_progress.py, generate_diagram_pdf.py (legacy root scripts)
- All __pycache__/ directories

---

## Current Git State

| Commit | Description |
|---|---|
| adc0f6f | fix: add missing get_current_profile import to graph.py |
| 104e0f7 | chore: remove all legacy/unused files and PDFs |
| 099fcb5 | feat: complete ARIA->BABU rename + V2 Cognitive OS upgrade |
| aa214d7 | Refactor bot.py into Layered Modules and Implement Class C Flow |

Remote: https://github.com/ssoni4751/ARIA.git -> main branch

---

## Open / Remaining Items

- 28 test failures: Mostly integration tests requiring live API keys (Groq, Gemini)
  or live PostgreSQL. All pure unit tests pass cleanly.
- GitHub repo name: Still shows as ARIA on GitHub UI. Rename in GitHub Settings
  -> Repository name -> BABU (manual step, no code impact).
- Supabase migration: DATABASE_URL is set but migrate_sqlite.py has not been
  run yet in production to move data from SQLite to PostgreSQL.

## 13. SQLite Schema Initialization & Robust Imports

- Added `_ensure_sqlite_schema` helper in `services.py` to automatically create all required SQLite tables on first connection.
- Integrated schema creation into `get_db_connection` for both custom SQLite URLs and the default DB path.
- Made `requests` import optional and added safe fallback for `is_google_configured` to prevent import errors when optional dependencies are missing.
- Verified via script that all tables (`sealed_epochs`, `search_cache`, `execution_ledger`, `system_memory`, `trusted_templates`, `babu_temporal_timeline`, `architecture_knowledge`, `babu_k0_working_memory`, etc.) are now present.

These changes resolve the earlier `sqlite3.OperationalError` and improve resilience of the codebase.
