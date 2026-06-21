# BABU (BABU) ADR Book v3
## Engineering Specification & Reference Architecture

Version: 3.0

---

# ADR-037 — Canonical Goal Schema

```json
{
  "goal_id": "G-20260620-001",
  "source": "telegram",
  "query": "post hi to facebook",
  "intent": "post_to_facebook",
  "status": "pending",
  "created_at": "timestamp"
}
```

---

# ADR-038 — Task Schema

```json
{
  "task_id": "T1",
  "goal_id": "G-20260620-001",
  "department": "execution",
  "action": "post_to_facebook",
  "status": "queued"
}
```

---

# ADR-039 — Execution Ledger Schema

Table: execution_ledger

Fields:

- event_id
- goal_id
- task_id
- timestamp
- action
- department
- status
- duration_ms
- metadata_json

Indexes:

- goal_id
- timestamp
- status

---

# ADR-040 — Epistemic Memory Schema

Table: epistemic_memory

Fields:

- memory_id
- category
- observation
- confidence
- source
- created_at

Examples:

- User prefers concise posts
- Facebook posts perform better under 120 words

---

# ADR-041 — Template Registry Schema

Table: trusted_templates

Fields:

- template_id
- signature
- success_rate
- executions
- last_used
- status

States:

- candidate
- trusted
- suspended
- archived

---

# ADR-042 — Template Promotion Algorithm

Requirements

1. Success threshold met
2. Governance clean
3. No recent critical failures

Promotion:

candidate
→ trusted

---

# ADR-043 — Governance Policy Engine

Execution Policy

Intent
→ Validate
→ Authorize
→ Execute

Failure

Intent
→ Reject
→ Audit

---

# ADR-044 — State Machine Specification

Goal States

RECEIVED
CLASSIFIED
PLANNED
EXECUTING
WAITING_APPROVAL
APPROVED
EXECUTED
RECORDED
CLOSED

---

# ADR-045 — Approval Architecture

Pending Action Structure

```python
pending_actions = {
    approval_id: {
        "goal_id": "...",
        "task_id": "..."
    }
}
```

Atomic Removal

```python
action = pending_actions.pop(
    approval_id,
    None
)
```

---

# ADR-046 — Department Contract

Input

- Goal Context
- Memory Context
- Governance Context

Output

- Result
- Confidence
- Metadata

---

# ADR-047 — Auditor Contract

Checks

- Governance
- Quality
- Brand Compliance
- Formatting

Return

PASS | FAIL

---

# ADR-048 — Intent Packet Specification

```json
{
  "allowed_depts": ["execution"],
  "allowed_actions": ["post_to_facebook"],
  "mode": "APPROVAL_REQUIRED",
  "confidence": 0.95
}
```

---

# ADR-049 — Telemetry Events

Events

- GOAL_RECEIVED
- GOAL_PLANNED
- TASK_EXECUTED
- APPROVAL_GRANTED
- EXECUTION_FAILED

---

# ADR-050 — Dashboard Architecture

Modules

- Health
- Execution Feed
- Telemetry
- Approvals
- Ledger Explorer

---

# ADR-051 — API Contract Layer

Connectors

Facebook:
- create_post

Google:
- send_email
- create_doc
- create_event

---

# ADR-052 — Failure Recovery Model

On Failure

1. Record Error
2. Audit Error
3. Save Checkpoint
4. Notify User

---

# ADR-053 — Testing Standards

Required Tests

- Governance Tests
- Intent Tests
- Department Tests
- Execution Tests
- Template Tests

---

# ADR-054 — Observability Standards

Required Logs

- Planner Logs
- Department Logs
- Auditor Logs
- API Logs
- Governance Logs

---

# ADR-055 — Deployment Reference

Frontend

- Telegram
- Web Dashboard

Backend

- Python
- LangGraph

Storage

- SQLite
- Supabase

Hosting

- Render

---

# ADR-056 — Future Kernel Architecture

Target

Intent
→ Governance
→ Trusted Template
→ Execution

Planner becomes fallback.

---

# Appendix C — Engineering Principles

1. Every execution is auditable.
2. Every state is recoverable.
3. Every template is measurable.
4. Governance precedes execution.
5. Memory informs but does not govern.
6. Constitution remains supreme.

---

# End of ADR Book v3
