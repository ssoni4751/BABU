# Project BABU v2 — Implementation Plan

This plan outlines the restructuring and upgrade of Project BABU to align with the **BABU v2 Vision Draft** specifications. We will achieve this by separating Intent, Cognition, Services, Authority, and Execution into isolated modular files, and upgrading the Layer 5 Authorization Engine to support Class A, Class B, and Class C service policies.

## User Review Required

> [!IMPORTANT]
> - **bot.py Restructuring**: We are decomposing `babu/bot.py` into `gateway.py` (Layer 0), `services.py` (Layer 4), `graph.py` (Orchestration Nodes), and a lean `bot.py` (Server & Callback Entry point). This is a significant modularization of the core entry points.
> - **Class C Destructive Service Protection**: We will define Class C destructive actions (e.g. `delete_document`, `delete_spreadsheet`, `delete_event`) and require a double-confirmation flow: `Draft -> Preview -> Approve -> Confirm -> Execute`.

---

## Proposed Changes

### Component 1: Codebase Modularization (Vision Layers 0, 4, and Orchestration)

To prevent code bloat and cleanly isolate concerns:
1. **[NEW] [gateway.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/gateway.py)**: Layer 0 Deterministic Gateway.
   - Extracts `is_pure_greeting`, `is_deterministic_faq_query`, `get_dynamic_self_identity`, `get_system_health_dashboard`, and `get_babu_age_string` from `bot.py`.
2. **[NEW] [services.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/services.py)**: Layer 4 Services.
   - Extracts `web_search`, `wikipedia_search`, `search_knowledge`, `search_profile`, and search caching/hash functions.
3. **[NEW] [graph.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/graph.py)**: LangGraph Orchestration.
   - Extracts `BabuState`, `intent_router`, `planner_node`, `task_executor_node`, `pa_node`, `route_after_router`, `workflow`, and `babu_brain`.
4. **[MODIFY] [bot.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/bot.py)**:
   - Imports modularized components from `gateway.py`, `services.py`, and `graph.py`.
   - Retains the Telegram command handlers, webhook listeners, `HealthHandler` HTTP server, and public `invoke_babu` entry point.

---

### Component 2: Layer 5 Authorization Engine & Class C Flows

1. **[MODIFY] [auditor.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/auditor.py)**:
   - Implement `get_service_class(action)` to categorize actions into:
     - **Class A (Read-only)**: `search_sheet`, `search_gmail`, `search_image`, `web_search`, `wikipedia_search` (Auto-Approved).
     - **Class C (Destructive)**: `delete_document`, `delete_spreadsheet`, `delete_event`, `mass_update`, `bulk_delete`.
     - **Class B (Mutating)**: All other actions (e.g. `send_email`, `post_to_facebook`) (Preview + User Approval).
   - Register Class C actions in the `PreExecutionGatekeeper` supported list.

2. **[MODIFY] [graph.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/graph.py)**:
   - Update `intent_router` and task execution to support the double-confirmation preview cycle for Class C actions:
     - **Stage 1 (Preview & Approval)**: User requests deletion -> System presents preview and asks to approve ("Reply with 'approve' or '1'").
     - **Stage 2 (Confirmation Warning)**: User sends approval -> System locks the task, transitions to `CONFIRMATION_REQUIRED` state, and presents a critical warning: *"⚠️ WARNING: Destructive Class C action detected. Are you sure you want to proceed? Reply with 'confirm' or '2' to execute."*
     - **Stage 3 (Execute)**: User sends confirmation -> Task dispatches and executes.

---

## Verification Plan

### Automated Tests
- Run all existing tests to verify that modularization does not cause import issues or regressions:
  `pytest -v`
- Add unit tests in `test_intent_governance.py` verifying that:
  - Class A actions are auto-approved.
  - Class B actions require approval.
  - Class C actions require approval followed by a warning-confirmation block before execution.

### Manual Verification
- We will mock the execution of a destructive Class C task to verify that it prompts with the double-confirmation warning before execution.
