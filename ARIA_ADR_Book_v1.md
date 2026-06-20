# ARIA (BABU) Architecture Decision Record (ADR) Book
Version: 1.0

---

# ADR-001 — Constitutional Governance Layer

## Status
Accepted

## Context
Agentic systems tend to drift when planning, memory, and execution are allowed to modify operational constraints.

## Decision
Introduce an immutable E0 Constitution enforced by a Governance Layer.

## Consequences
- Hard safety boundaries
- Deterministic governance
- Planner cannot override constitutional rules

---

# ADR-002 — Brain vs Memory Separation

## Status
Accepted

## Context
Knowledge and experience are fundamentally different data domains.

## Decision
Separate:

- Brain (what ARIA knows)
- Memory (what ARIA experienced)

## Consequences
- Cleaner reasoning
- Reduced hallucination
- Easier maintenance

---

# ADR-003 — RAG-Based Knowledge Retrieval

## Status
Accepted

## Decision
Store doctrine, capabilities, organization and operational knowledge in a RAG-accessible repository.

## Components
- doctrine.md
- capabilities.md
- organization.md
- constitution.md

---

# ADR-004 — Ledger-Based Memory

## Status
Accepted

## Decision
Memory records outcomes rather than instructions.

### Stores
- Successes
- Failures
- Corrections
- Anti-patterns
- Work summaries

## Consequences
Continuous operational improvement.

---

# ADR-005 — Operational vs Epistemic Memory

## Status
Proposed

## Operational Memory
Tracks execution.

Examples:
- Post published
- Email sent
- Approval granted

## Epistemic Memory
Tracks learned preferences.

Examples:
- Preferred writing style
- Successful strategies

---

# ADR-006 — Department Architecture

## Status
Accepted

Departments act as specialized execution units.

### Current Departments
- Writing
- Research
- Analysis
- Execution
- Auditor

## Benefits
- Modularity
- Scalability
- Easier testing

---

# ADR-007 — Auditor First Design

## Status
Accepted

All critical outputs pass through an Auditor before execution.

## Purpose
- Quality control
- Governance verification
- Risk reduction

---

# ADR-008 — DAG-Based Task Execution

## Status
Accepted

Goals are decomposed into DAGs.

Pipeline:

User Goal
→ Planner
→ DAG
→ Departments
→ Auditor
→ Execution

---

# ADR-009 — Approval Required Actions

## Status
Accepted

External-impact actions require approval.

Examples:
- Facebook posting
- Email sending
- Document publication

---

# ADR-010 — Atomic Approval Consumption

## Status
Accepted

Problem:
Double-click approvals caused duplicate execution.

Decision:
Consume approval via atomic pop.

Result:
First approval executes.
Subsequent approvals are ignored.

---

# ADR-011 — Trusted Template Layer (E[Temp])

## Status
In Progress

## Vision

Frequently repeated workflows become reusable templates.

Examples:
- Facebook posting
- Google document creation
- Email workflows

## Goal

Reduce:
- Latency
- Token usage
- Planning overhead

---

# ADR-012 — Planner as Exception Path

## Status
Target Architecture

Current:

Intent
→ Planner
→ Execution

Future:

Intent
→ Template Match?
→ Execute

Only novel goals invoke Planner.

---

# ADR-013 — Execution Kernel Model

## Status
Accepted

ARIA is modeled as an execution kernel.

| OS Concept | ARIA |
|------------|------|
| Kernel | Constitution |
| Scheduler | Planner |
| RAM | Pending Actions |
| Process Table | Goals |
| Drivers | API Integrations |
| Logs | Ledger |

---

# ADR-014 — Durable State Recovery

## Status
Accepted

SQLite checkpointing preserves state.

Benefits:
- Crash recovery
- Auditability
- Replay capability

---

# ADR-015 — Intent Governance

## Status
Accepted

Every goal must pass:

1. Intent Classification
2. Governance Validation
3. Planning
4. Execution

---

# ADR-016 — Future Multi-Agent Evolution

## Status
Vision

Future additions:

- Publishing Department
- Memory Department
- Context Department
- Optimization Department

---

# ADR-017 — System Layers

## Canonical Stack

L0 — Constitution

L1 — Brain

L2 — Memory

L3 — E[Temp]

L4 — Planner

L5 — Departments

L6 — Execution

---

# ADR-018 — Canonical Principle

ARIA operates under a strict hierarchy:

Constitution
→ Knowledge
→ Memory
→ Templates
→ Planning
→ Departments
→ Execution

No lower layer may override a higher layer.

---

# End of ADR Book
