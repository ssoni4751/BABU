# ARIA (BABU) ADR Book v4
## Source Code Blueprint & Production Architecture

Version: 4.0

---

# ADR-057 — Canonical Repository Structure

```text
aria/
├── babu/
│   ├── bot.py
│   ├── planner.py
│   ├── task_engine.py
│   ├── governance.py
│   ├── auditor.py
│   ├── memory.py
│   ├── departments.py
│   ├── workers.py
│   ├── rag_storage.py
│   ├── rag_ingestion.py
│   ├── google_service.py
│   ├── social_media.py
│   └── e0/
│       └── constitution.json
│
├── brain/
│   ├── doctrine.md
│   ├── organization.md
│   ├── capabilities.md
│   └── constitution.md
│
├── data/
│   ├── checkpoints.db
│   ├── ledger.db
│   └── templates.db
│
├── tests/
├── docs/
└── deployment/
```

---

# ADR-058 — Module Responsibilities

## bot.py

Responsibilities:

- Telegram polling
- Web dashboard events
- Approval handling
- Goal injection

---

## planner.py

Responsibilities:

- Context assembly
- Goal decomposition
- DAG creation

---

## task_engine.py

Responsibilities:

- DAG execution
- Task scheduling
- State updates

---

## governance.py

Responsibilities:

- Constitutional enforcement
- Permission checks
- Approval checks

---

## auditor.py

Responsibilities:

- Pre-execution review
- Post-execution review

---

# ADR-059 — LangGraph Node Blueprint

```text
START
 ↓
Intent Classifier
 ↓
Governance Node
 ↓
Template Lookup
 ↓
Planner
 ↓
Task Engine
 ↓
Department Node
 ↓
Auditor Node
 ↓
Execution Node
 ↓
Ledger Node
 ↓
END
```

---

# ADR-060 — Database DDL Reference

## execution_ledger

```sql
CREATE TABLE execution_ledger (
    event_id TEXT PRIMARY KEY,
    goal_id TEXT,
    task_id TEXT,
    action TEXT,
    status TEXT,
    timestamp TEXT,
    metadata TEXT
);
```

---

## trusted_templates

```sql
CREATE TABLE trusted_templates (
    template_id TEXT PRIMARY KEY,
    signature TEXT,
    executions INTEGER,
    success_rate REAL,
    status TEXT
);
```

---

# ADR-061 — API Layer Specification

Facebook Contract

Input:

```json
{
  "caption": "text",
  "image_path": "optional"
}
```

Output:

```json
{
  "success": true,
  "post_id": "123"
}
```

---

# ADR-062 — Department Interface

Input

```python
DepartmentTask
```

Output

```python
DepartmentResult
```

Required Fields

- result
- confidence
- metadata

---

# ADR-063 — Planner Interface

Input

```python
GoalRequest
```

Output

```python
TaskDAG
```

Guarantees

- Governance aware
- Memory aware
- Context aware

---

# ADR-064 — Memory Manager Specification

Memory Types

1. Working Memory
2. Operational Memory
3. Epistemic Memory

Responsibilities

- Storage
- Retrieval
- Promotion
- Retention

---

# ADR-065 — Retention Rules

Working Memory

TTL:
Active execution only

Operational Memory

TTL:
Permanent

Epistemic Memory

TTL:
Reviewable

---

# ADR-066 — E[Temp] Engine Blueprint

Pipeline

Execution
↓
Ledger Analysis
↓
Candidate Template
↓
Validation
↓
Trusted Template

---

# ADR-067 — Governance DSL

Example

```yaml
facebook_post:
  approval_required: true
  rate_limit: 10
  enabled: true
```

---

# ADR-068 — Configuration System

Sources

- config.json
- env variables
- constitution.json

Priority

Environment
→ Config
→ Defaults

---

# ADR-069 — Approval Subsystem

States

PENDING
APPROVED
REJECTED
EXPIRED

Transitions

PENDING → APPROVED
PENDING → REJECTED
PENDING → EXPIRED

---

# ADR-070 — Execution Queue Design

Queue Types

- Immediate
- Scheduled
- Retry

Priority

HIGH
MEDIUM
LOW

---

# ADR-071 — Error Taxonomy

Categories

- Governance Error
- Planning Error
- Department Error
- API Error
- System Error

---

# ADR-072 — Telemetry Data Model

Metrics

- latency_ms
- token_count
- cost_estimate
- success_rate
- template_hits

---

# ADR-073 — Dashboard Reference Architecture

Views

1. System Health
2. Goal Feed
3. Ledger
4. Templates
5. Telemetry
6. Governance

---

# ADR-074 — Security Controls

Controls

- Immutable Constitution
- Approval Gates
- Audit Logs
- Rate Limits

---

# ADR-075 — Production Deployment Topology

```text
Users
  ↓
Telegram/Web
  ↓
Render Deployment
  ↓
LangGraph Runtime
  ↓
SQLite/Supabase
  ↓
Facebook/Google APIs
```

---

# ADR-076 — Scaling Strategy

Phase 1

Single Runtime

Phase 2

Department Workers

Phase 3

Distributed Execution

Phase 4

Template Driven Kernel

---

# ADR-077 — Disaster Recovery

Requirements

- Daily backups
- Ledger recovery
- Template recovery
- Checkpoint replay

---

# ADR-078 — Engineering Laws

Law 1:
Execution must be recoverable.

Law 2:
Templates must be measurable.

Law 3:
Governance precedes planning.

Law 4:
Approval precedes impact.

Law 5:
Memory informs decisions.

Law 6:
Constitution governs everything.

---

# End of ADR Book v4
