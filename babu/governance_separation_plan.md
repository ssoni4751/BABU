# BABU Governance Separation & Operating Layer Invbabunts — Implementation Plan

This plan outlines the concrete technical changes required to transition BABU into a strict **governance-oriented operating layer for bounded intelligence systems**, executing the recommendations from the approved Architecture Report.

---

## 1. Goal Description

Establish absolute boundaries between cognitive reasoning (replaceable intelligence processors) and execution authority (deterministic OS kernel). We will enforce four core invbabunts:
1. **Durable Epoch State & Seals**: Wire LangGraph's native `SqliteSaver` checkpointer and implement a thread-sealing mechanism to lock completed execution graphs permanently.
2. **Strict Token Governance Caps**: Programmatically track and block execution if a task or overall goal graph violates its token budget.
3. **Structured Worker Schema Invbabunts**: Validate output shapes programmatically in Department Heads before passing data upstream.
4. **Fail-Closed Ambiguity Routing**: Enforce strict refusal bounds in the planner if a request is structurally ambiguous or invalid.

---

## 2. User Review Required

> [!IMPORTANT]
> **Durable Checkpoints & Thread Mapping**
> Compiling the LangGraph workflow with `SqliteSaver` will actively record all state transitions, memory writes, and execution steps to the SQLite file `babu/memory/babu_checkpoint.db` at runtime.

> [!WARNING]
> **Epoch State Locking (Immutable Seals)**
> Once an execution epoch completes (marked as `COMPLETED` or `FAILED`), its associated `session_id`/`thread_id` will be sealed in a database table. Any future attempts to invoke execution or mutate state under that sealed ID will be blocked by the OS router, preventing state contamination or replay exploits.

> [!CAUTION]
> **Token Cap Failures**
> If a task worker consumes more than its scoped `token_budget` (e.g., 1,500 tokens), or if the cumulative run exceeds the `GoalGraph.total_token_budget` (e.g., 15,000 tokens), the Task Engine will immediately halt all pending tasks and raise a budget exhaustion exception.

---

## 3. Open Questions

> [!IMPORTANT]
> **Q1: Thread ID Granularity**
> Currently, the Telegram session is mapped as the `session_id`. If we seal a completed epoch, should we seal the *entire* conversation session (which would block the user from sending future messages in that chat) or should we generate unique `epoch_id` values (e.g., `EP-session_id-timestamp`) to seal individual goal workflows?
> *   **Recommendation**: Use a unique `epoch_id` (e.g., `session_id:goal_id`) as the LangGraph `thread_id` for state checkpoints so that casual conversation can continue in the main Telegram chat, but specific goal graphs remain permanently sealed.

> [!IMPORTANT]
> **Q2: Token Tracking Method**
> Groq API responses return exact token usage in `usage` fields. How should we track this across departments?
> *   **Recommendation**: Accumulate the token metrics returned by `ChatGroq` invocations directly into the LangGraph state `tokens` reducer, and verify the aggregate budget before executing each node.

---

## 4. Proposed Changes

### Component 1: Durable Checkpoints & Epoch Seals

#### [MODIFY] [bot.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/bot.py)
*   **Checkpointer Integration**:
    *   Import `SqliteSaver` from `langgraph.checkpoint.sqlite`.
    *   Add connection helper to initialize the SQLite database at `babu/memory/babu_checkpoint.db`.
    *   Compile the StateGraph using the checkpointer:
        ```python
        conn = sqlite3.connect("babu/memory/babu_checkpoint.db", check_same_thread=False)
        memory = SqliteSaver(conn)
        babu_brain = workflow.compile(checkpointer=memory)
        ```
*   **Thread Mapping & Seals**:
    *   In `invoke_babu()`, construct a compound epoch thread ID: `thread_id = f"{session_id}:{goal_id or 'direct'}"`.
    *   Pass this compound thread ID into the checkpointer config: `config = {"configurable": {"thread_id": thread_id}}`.
    *   Create a flat database table `sealed_epochs` in SQLite.
    *   If a session status checks as sealed, reject execution at the very entry point.
    *   Once a goal graph status transitions to `COMPLETED` or `FAILED` in the PA synthesis node, record the active `thread_id` into the `sealed_epochs` table.

---

### Component 2: Token Budget Governance

#### [MODIFY] [task_engine.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/task_engine.py)
*   Add token budget tracking logic inside the `TaskEngine` scheduler:
    *   Track cumulative token consumption.
    *   Add helper method `verify_token_budget(task: TaskDTO, current_total_tokens: int) -> bool` to check if executing the task would violate the global `total_token_budget` or the individual task's `token_budget`.
    *   If the budget is violated, mark the task state as `CANCELLED` with a clear budget exhaustion error message.

#### [MODIFY] [bot.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/bot.py)
*   In `task_executor_node`, pass the cumulative `tokens["total"]` from the LangGraph state dict to the `TaskEngine`.
*   If a task is marked `CANCELLED` due to budget exhaustion, halt the DAG scheduler loop immediately and route directly to the PA synthesis node to report the structural halt to the user.

---

### Component 3: Worker JSON Schema Invbabunts

#### [MODIFY] [departments.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/departments.py)
*   Establish strict output schema validation inside each `DepartmentHead.dispatch()` implementation:
    *   Verify that worker outputs can be successfully parsed into JSON if structured output is expected (e.g., in `ResearchHead` and `AnalysisHead`).
    *   Check for the presence of mandatory structural keys (e.g., `findings`, `sources`, `recommendations`) matching the department's schema invbabunts.
    *   If validation fails, raise a structural `ValueError` to trigger the Task Engine's built-in retry and cascade-blocking logic.

---

### Component 4: Fail-Closed Ambiguity Routing

#### [MODIFY] [planner.py](file:///c:/Users/LENOVO/.gemini/antigravity/scratch/Babu/babu/planner.py)
*   Enforce absolute boundaries on goal planning:
    *   Add a validation check to verify that the generated plan is topological and acyclic.
    *   If the LLM output fails to parse, contains circular dependencies, or is marked as highly ambiguous, do **NOT** generate a fallback execution graph.
    *   Instead, return a specialized `AmbiguityRefusalGraph` containing a single `pa` node with a structured refusal description (e.g., *"Request is ambiguous or lacks necessary context to orchestrate securely"*).
    *   This forces the system to fail closed, reporting the clarity boundaries back to the user without initiating unverified tasks.

---

## 5. Verification Plan

### Automated Tests
1.  **Orchestration Budget Test**:
    *   Add a test case in `test_orchestration.py` that mocks a worker consuming more than its allocated token budget, verifying that the task engine cancels execution immediately.
2.  **Schema Invbabunt Test**:
    *   Add a test case that sends malformed output from a worker, verifying that the department head intercepts it and triggers a validation failure.
3.  **Ambiguity Refusal Test**:
    *   Send an ambiguous, gibberish query to `planner.py`, verifying that it builds a fail-closed refusal graph without generating web searches or execution steps.
4.  **Epoch Seal Test**:
    *   Invoke `invoke_babu` with a specific `session_id`, seal the thread, and attempt a second invocation under the same thread config, verifying that the OS layer blocks the request.

Run the test suite to verify:
```powershell
.venv\Scripts\python.exe test_orchestration.py
.venv\Scripts\python.exe test_memory.py
```
