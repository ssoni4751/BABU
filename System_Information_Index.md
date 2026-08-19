# System Information Index (SII)

Version: 2.0
Status: Canonical

## Purpose

The System Information Index (SII) is the authoritative runtime routing layer for all BABU system-related queries.

The SII contains:
- Topic mappings
- Component mappings
- ADR mappings
- Document mappings
- Diagnostic domains
- Retrieval instructions
- Reasoning instructions

The SII does not contain detailed architecture knowledge.
Detailed information must be retrieved through RAG.

---

# Query Modes

## SYSTEM_INFORMATION
Retrieve and explain system concepts.

## SYSTEM_ANALYSIS
Analyze architecture and implementation using retrieved evidence.

## SYSTEM_DIAGNOSTIC
Investigate failures using:
- ADRs
- Logs
- Ledger
- Telemetry

## SYSTEM_AUDIT
Compare implementation vs architecture.

## SYSTEM_DESIGN_REVIEW
Evaluate proposed changes and tradeoffs.

---

# Architecture Layer Registry

## L0 Constitution
References:
ADR-001
ADR-017
ADR-018

Book:
BABU_ADR_Book_v1.md

## L1 Brain
References:
ADR-002
ADR-003

Book:
BABU_ADR_Book_v1.md

## L2 Memory
References:
ADR-004
ADR-005
ADR-027
ADR-040
ADR-064
ADR-065

Books:
BABU_ADR_Book_v1.md
BABU_ADR_Book_v2.md
BABU_ADR_Book_v3.md
BABU_ADR_Book_v4.md

## L3 System Information Index
Reference:
System_Information_Index.md

## L4 ETemp
References:
ADR-011
ADR-022
ADR-023
ADR-041
ADR-042
ADR-066

## L5 Planner
References:
ADR-012
ADR-059
ADR-063

## L6 Departments
References:
ADR-006
ADR-046
ADR-062

## L7 Execution
References:
ADR-008
ADR-029
ADR-051
ADR-075

---

# Component Registry

## Governance
ADRs:
ADR-001
ADR-024
ADR-043
ADR-067

Keywords:
governance, constitution, policy, rule

## Planner
ADRs:
ADR-012
ADR-059
ADR-063

Keywords:
planner, planning, dag, workflow

## Memory
ADRs:
ADR-004
ADR-005
ADR-027
ADR-040
ADR-064
ADR-065

Keywords:
memory, ledger, retention, epistemic

## Auditor
ADRs:
ADR-007
ADR-026
ADR-047

## Approval
ADRs:
ADR-009
ADR-010
ADR-045
ADR-069

## ETemp
ADRs:
ADR-011
ADR-022
ADR-023
ADR-041
ADR-042
ADR-066

## Execution
ADRs:
ADR-008
ADR-029
ADR-051
ADR-075

## Telemetry
ADRs:
ADR-031
ADR-032
ADR-049
ADR-054
ADR-072

---

# Document Registry

## BABU_ADR_Book_v1.md
Coverage:
ADR-001 → ADR-018

Domains:
Constitution, Brain, Memory, Planner, Approval

## BABU_ADR_Book_v2.md
Coverage:
ADR-019 → ADR-036

Domains:
Deployment, Telemetry, Security, Templates

## BABU_ADR_Book_v3.md
Coverage:
ADR-037 → ADR-056

Domains:
Schemas, Contracts, APIs, Dashboard

## BABU_ADR_Book_v4.md
Coverage:
ADR-057 → ADR-078

Domains:
Repository, Database, LangGraph, Scaling

## BABU_ADR_Book_v5.md
Coverage:
ADR-079 → ADR-085

Domains:
Decoupled Architecture, 2-Way Webhook Engine, Automated Token Self-Healing, Unicode Safety, Meta Developer Live Publication

## BABU_ADR_Book_v6.md
Coverage:
ADR-086 → ADR-090

Domains:
Governed Control Plane Architecture, Meta Bidirectional Loop, Multi-Provider Failover, Truthful Degradation, Deterministic Fast-Track

## BABU_ADR_Book_v7.md
Coverage:
ADR-091 → ADR-095

Domains:
Topology-Aware Query Classification, Capability Demand Packet, Orchestration Bypass, Conversational State Inheritance, Telemetry Observability

## BABU_VISION_PLAN_2026.md
Domains:
Governed Agentic Control Platform, 7-Layer Architecture, Meta Bidirectional Loop, Multi-Provider Intelligence, Business Profile Grounding

## BABU_Manifesto_2026.md
Coverage:
25-Point System Architecture Manifesto (Living Architecture Standard)

Domains:
North Star Principles, 7-Layer Pipeline, Governed Control Plane, External World Connectivity, Resilience

## babu_execution_flow.md
Domains:
Architecture, Execution Flow, Feature Map, Class C Confirmation Gates, Trusted Templates

## babu_cognitive_os_architectural_blueprint.md
Domains:
Knowledge Graph, Distributed Cognitive OS Blueprint, Intent Compiler, Immune Confidence Decay, Memory Segmentation

---

# Diagnostic Domains

## Approval Issues
References:
ADR-010
ADR-045
ADR-069

Evidence:
- Pending Actions
- Approval Logs
- Execution Ledger

## Facebook Issues
References:
ADR-051
ADR-061

Evidence:
- API Logs
- Publishing Logs

## Governance Issues
References:
ADR-001
ADR-024
ADR-043
ADR-067

## Memory Issues
References:
ADR-027
ADR-040
ADR-064

## Planner Issues
References:
ADR-012
ADR-059
ADR-063

---

# Retrieval Rules

1. SII never provides final answers.
2. SII only provides routing information.
3. Retrieve detailed information through RAG.
4. Retrieve relevant ADRs before answering.
5. Diagnostics combine documentation, logs, telemetry and ledger data.
6. Analysis requires reasoning.
7. Retrieved documents are evidence, not conclusions.
8. Use Document Registry before RAG retrieval.

---

# End of System Information Index
