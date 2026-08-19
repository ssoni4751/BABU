# BABU Governance Separation & Operating Layer Invariants — Historical Implementation Plan

> [!NOTE]
> **Status: COMPLETED & INTEGRATED INTO CANONICAL RUNTIME**  
> All requirements outlined in this historical implementation plan have been fully implemented in `graph.py`, `planner.py`, `task_engine.py`, `departments.py`, and `auditor.py`. The active architectural baseline is defined by [BABU Vision Plan 2026](file:///d:/Aria/BABU_VISION_PLAN_2026.md) and [BABU Manifesto 2026](file:///d:/Aria/BABU_Manifesto_2026.md).

This plan documents the architectural transition of BABU into a strict **governance-oriented operating layer for bounded intelligence systems**.

---

## 1. Goal Description

Establish absolute boundaries between cognitive reasoning (replaceable intelligence processors) and execution authority (deterministic OS kernel). Enforces four core invariants:
1. **Durable Epoch State & Seals**: Wire LangGraph's native `SqliteSaver` checkpointer and implement a thread-sealing mechanism to lock completed execution graphs permanently.
2. **Strict Token Governance Caps**: Programmatically track and block execution if a task or overall goal graph violates its token budget.
3. **Structured Worker Schema Invariants**: Validate output shapes programmatically in Department Heads before passing data upstream.
4. **Fail-Closed Ambiguity Routing**: Enforce strict refusal bounds in the planner if a request is structurally ambiguous or invalid.

---

## 2. Technical Invariants

> [!IMPORTANT]
> **Durable Checkpoints & Thread Mapping**
> Compiling the LangGraph workflow with `SqliteSaver` actively records all state transitions, memory writes, and execution steps to the SQLite file `babu/memory/babu_checkpoint.db` at runtime.

> [!WARNING]
> **Epoch State Locking (Immutable Seals)**
> Once an execution epoch completes (marked as `COMPLETED` or `FAILED`), its associated `session_id`/`thread_id` is sealed in a database table. Any future attempts to invoke execution or mutate state under that sealed ID will be blocked by the OS router, preventing state contamination or replay exploits.

> [!CAUTION]
> **Token Cap Failures**
> If a task worker consumes more than its scoped `token_budget` (e.g., 1,500 tokens), or if the cumulative run exceeds the `GoalGraph.total_token_budget` (e.g., 15,000 tokens), the Task Engine immediately halts all pending tasks and raises a budget exhaustion exception.

---

## 3. Design Decisions

> [!IMPORTANT]
> **Q1: Thread ID Granularity**
> Telegram sessions are mapped as `session_id`. When sealing an epoch, BABU uses a unique compound `epoch_id` (e.g., `session_id:goal_id`) as the LangGraph `thread_id` for state checkpoints so that casual conversation can continue in the main Telegram chat, but specific goal graphs remain permanently sealed.

> [!IMPORTANT]
> **Q2: Token Tracking Method**
> Accumulated token metrics returned by `ChatGroq` / provider invocations are passed directly into the LangGraph state `tokens` reducer, verifying the aggregate budget before executing each node.

---

## 4. Implemented Components

### Component 1: Durable Checkpoints & Epoch Seals

#### [bot.py](file:///d:/Aria/bot.py)
*   **Checkpointer Integration**:
    *   Imports `SqliteSaver` from `langgraph.checkpoint.sqlite`.
    *   Initializes the SQLite database at `babu/memory/babu_checkpoint.db`.
    *   Compiles the StateGraph using the checkpointer.
*   **Thread Mapping & Seals**:
    *   In `invoke_babu()`, constructs a compound epoch thread ID: `thread_id = f"{session_id}:{goal_id or 'direct'}"`.
    *   Passes this compound thread ID into the checkpointer config: `config = {"configurable": {"thread_id": thread_id}}`.
    *   Creates a flat database table `sealed_epochs` in SQLite.
    *   Once a goal graph status transitions to `COMPLETED` or `FAILED` in the PA synthesis node, records the active `thread_id` into the `sealed_epochs` table.

---

### Component 2: Token Budget Governance

#### [task_engine.py](file:///d:/Aria/task_engine.py)
*   Integrated token budget tracking logic inside the `TaskEngine` scheduler:
    *   Tracks cumulative token consumption across all tasks.
    *   `verify_token_budget(task: TaskDTO, current_total_tokens: int) -> bool` checks if executing the task would violate the global `total_token_budget` or the individual task's `token_budget`.
    *   If the budget is violated, marks the task state as `CANCELLED` with a clear budget exhaustion message.

#### [graph.py](file:///d:/Aria/graph.py)
*   In `task_executor_node`, passes the cumulative `tokens["total"]` from the LangGraph state dict to the `TaskEngine`.
*   If a task is marked `CANCELLED` due to budget exhaustion, halts the DAG scheduler loop immediately and routes directly to the PA synthesis node to report the halt.

---

### Component 3: Worker JSON Schema Invariants

#### [departments.py](file:///d:/Aria/departments.py)
*   Established strict output schema validation inside each `DepartmentHead.dispatch()` implementation:
    *   Verifies that worker outputs can be successfully parsed into JSON if structured output is expected (`ResearchHead` and `AnalysisHead`).
    *   Checks for the presence of mandatory structural keys (`findings`, `sources`, `recommendations`) matching the department's schema invariants.
    *   If validation fails, raises a structural `ValueError` to trigger the Task Engine's built-in retry and cascade-blocking logic.

---

### Component 4: Fail-Closed Ambiguity Routing

#### [planner.py](file:///d:/Aria/planner.py)
*   Enforces absolute boundaries on goal planning:
    *   Validation checks verify that the generated plan is topological and acyclic.
    *   If the LLM output fails to parse, contains circular dependencies, or is marked as highly ambiguous, does **NOT** generate a fallback execution graph.
    *   Returns a specialized `AmbiguityRefusalGraph` containing a single `pa` node with a structured refusal description (*"Request is ambiguous or lacks necessary context to orchestrate securely"*).
    *   This forces the system to fail closed, reporting clarity boundaries back to the user without initiating unverified tasks.

---

## 5. Verification & Test Alignment

All verification tests are maintained in the test suite:
- `test_orchestration.py` — Verifies token budget limits, epoch sealing, and DAG invariants.
- `test_intent_governance.py` — Verifies fail-closed intent routing and capability boundaries.
- `test_epistemic_boundary.py` — Verifies pre/post audit gate enforcement.
