# BABU (BABU) Architecture Decision Record Book v2
## Advanced System Design, Governance, Memory & Deployment

Version: 2.0

---

# ADR-019 — Component Architecture

## Status
Accepted

### Core Components

1. Constitution Engine
2. Governance Layer
3. Intent Classifier
4. Planner
5. Task Engine
6. Departments
7. Auditor
8. Memory System
9. E[Temp] Registry
10. API Connectors
11. Orchestrator

### Component Flow

User
→ Orchestrator
→ Intent
→ Governance
→ Planner / E[Temp]
→ Departments
→ Auditor
→ Execution

---

# ADR-020 — Execution Ledger Schema

## Status
Accepted

### Execution Event

Fields:

- event_id
- goal_id
- task_id
- timestamp
- department
- action
- status
- confidence
- execution_time
- metadata

### Benefits

- Auditing
- Analytics
- Replay
- Template promotion

---

# ADR-021 — State Transition Model

## Status
Accepted

Goal Lifecycle

RECEIVED
→ CLASSIFIED
→ PLANNED
→ EXECUTING
→ WAITING_APPROVAL
→ APPROVED
→ EXECUTED
→ RECORDED
→ CLOSED

Failure Path

EXECUTING
→ FAILED
→ AUDITED
→ CLOSED

---

# ADR-022 — E[Temp] Promotion Policy

## Status
Accepted

Promotion Requirements

- Minimum Runs: configurable
- Success Rate threshold
- Governance compliance
- No recent failures

Promotion Flow

Execution
→ Ledger Review
→ Candidate
→ Approved Template

---

# ADR-023 — E[Temp] Demotion Policy

## Status
Accepted

Triggers

- Repeated failures
- Governance violations
- Outdated workflow

Demotion Flow

Template
→ Review
→ Suspended
→ Archived

---

# ADR-024 — Governance Decision Tree

## Status
Accepted

Checks

1. Intent Valid?
2. Action Allowed?
3. Approval Required?
4. Rate Limit Safe?
5. Constitutional Compliance?

Result

PASS
or
BLOCK

---

# ADR-025 — Intent Classification Architecture

## Status
Accepted

Outputs

- Query Category
- Departments
- Actions
- Confidence
- Approval Mode

Example

"Post hi to Facebook"

→ Execution
→ Writing
→ post_to_facebook
→ Approval Required

---

# ADR-026 — Auditor Framework

## Status
Accepted

Pre-Execution Review

Checks

- Governance
- Formatting
- Brand consistency
- Safety

Post-Execution Review

Checks

- Success
- Failure Cause
- Metrics

---

# ADR-027 — Memory Architecture

## Status
Accepted

### Operational Memory

Stores

- Actions
- Tasks
- Results

### Epistemic Memory

Stores

- Preferences
- Lessons
- Anti-patterns

### Working Memory

Stores

- Pending actions
- Active goals

---

# ADR-028 — Pending Action Manager

## Status
Accepted

Purpose

Prevent duplicate execution.

Implementation

Atomic Pop

Example

Approve
→ Remove pending action
→ Execute

Second approval
→ Ignore

---

# ADR-029 — Deployment Architecture

## Status
Accepted

Current Stack

Frontend:
- Telegram
- Web Dashboard

Backend:
- Python
- LangGraph

Hosting:
- Render

Database:
- SQLite
- Supabase

Models:
- Gemini
- Groq
- Llama

---

# ADR-030 — API Connector Architecture

## Status
Accepted

Supported Connectors

- Facebook
- Google Docs
- Google Calendar
- Gmail
- Sheets

Future

- WhatsApp
- LinkedIn
- X/Twitter

---

# ADR-031 — Telemetry Framework

## Status
Accepted

Metrics

- Token Usage
- Latency
- Success Rate
- Approval Rate
- Template Hits
- Template Misses

---

# ADR-032 — Observability Architecture

## Status
Accepted

Logs

- Goal Logs
- Task Logs
- Department Logs
- Governance Logs
- API Logs

Dashboard

- Health Status
- Execution Status
- Error Tracking

---

# ADR-033 — Security Model

## Status
Accepted

Principles

- Fail Closed
- Approval Before Impact
- Immutable Constitution
- Audit Everything

---

# ADR-034 — Multi-Agent Expansion

## Status
Vision

Potential Departments

- Publishing
- Finance
- CRM
- Analytics
- Memory
- Context

---

# ADR-035 — Autonomous Execution Levels

## Status
Accepted

Level 0
Information Only

Level 1
Draft Generation

Level 2
Approval Required

Level 3
Trusted Automation

Level 4
Fully Autonomous Template

---

# ADR-036 — Future Execution Kernel Vision

## Status
Strategic

Target Flow

Intent
→ Governance
→ E[Temp]
→ Execution

Planner only for novel tasks.

This transforms BABU from a chatbot into an operational execution kernel.

---

# Appendix A — Canonical Layer Stack

L0 Constitution

L1 Brain

L2 Memory

L3 Trusted Templates

L4 Planner

L5 Departments

L6 Execution

---

# Appendix B — Design Laws

Law 1:
Constitution overrides everything.

Law 2:
Knowledge is not memory.

Law 3:
Memory is not authority.

Law 4:
Templates outperform planners for repeated work.

Law 5:
Every execution must be auditable.

Law 6:
No lower layer may override a higher layer.

---

# End of ADR Book v2
