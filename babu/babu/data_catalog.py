"""
BABU Runtime Data Catalog & SII Alignment Engine
Canonical index mapping Knowledge Classes (K0-K7) to physical stores, retention policies, and surgical getters.
Ensures zero bulk dumps: all fetches are targeted, slice-bounded, and lightweight.
"""

from typing import Dict, Any, List, Optional

# K0-K7 Canonical Runtime Knowledge Index (Aligned with System Information Index & Runtime Index)
RUNTIME_DATA_CATALOG: Dict[str, Dict[str, Any]] = {
    "K0_WORKING_MEMORY": {
        "class": "K0",
        "description": "Short-term conversational state & LangGraph execution checkpoints",
        "storage": "SQLite (babu_k0_working_memory, checkpoints, writes)",
        "retention": "24-hour sliding window / per-session",
        "privacy": "Class A (Internal Working State)",
        "fetch_method": "retrieve_k0_memory(session_id, limit=5)",
        "bulk_dump_allowed": False
    },
    "K1_IDENTITY": {
        "class": "K1",
        "description": "Owner personal identity, contact details, and family tree",
        "storage": "Protected JSON (user_profile.json)",
        "retention": "Permanent (Immutable at runtime)",
        "privacy": "Class C (Strictly Private - Non-Publishable)",
        "fetch_method": "get_current_profile() / get_profile_fact_answer(query)",
        "bulk_dump_allowed": False
    },
    "K2_RUNTIME_TELEMETRY": {
        "class": "K2",
        "description": "System uptime, token counts, inference costs, latencies & timeline events",
        "storage": "telematics.py & SQLite (execution_ledger, babu_temporal_timeline)",
        "retention": "Rolling historical ledger (Indexed by timestamp)",
        "privacy": "Class A (System Observability)",
        "fetch_method": "get_telemetry_snapshot() / get_daily_activity_summary(date_target)",
        "bulk_dump_allowed": False
    },
    "K3_BUSINESS": {
        "class": "K3",
        "description": "Anshu Computer & Tax Consultancy services, pricing, PF/GST/ITR FAQs",
        "storage": "Protected JSON (business_profile.json)",
        "retention": "Permanent (Business Knowledge Base)",
        "privacy": "Class A (Public Consultancy Facts)",
        "fetch_method": "get_business_profile_summary()",
        "bulk_dump_allowed": False
    },
    "K4_EXECUTION_CACHE": {
        "class": "K4",
        "description": "Pre-compiled DAG task plans and E[Temp] deterministic prompt templates",
        "storage": "SQLite (planned_graphs_cache, trusted_templates)",
        "retention": "LRU / Hit-rate promotion & demotion",
        "privacy": "Class A (Execution Optimization)",
        "fetch_method": "get_cached_plan(query_hash) / get_trusted_template(sig)",
        "bulk_dump_allowed": False
    },
    "K5_ARCHITECTURE_GOVERNANCE": {
        "class": "K5",
        "description": "Architectural Decision Records (ADR-001..ADR-095), trade-offs, immune anti-patterns",
        "storage": "SQLite (architecture_knowledge) & JSON (failures.json)",
        "retention": "Permanent Epistemic Truth (Decaying confidence for anti-patterns)",
        "privacy": "Class A (Architectural Governance)",
        "fetch_method": "get_architecture_record(record_id) / search_adr_knowledge(topic)",
        "bulk_dump_allowed": False
    },
    "K6_DOMAIN_RETRIEVAL": {
        "class": "K6",
        "description": "Vector document embeddings and System Information Index registry",
        "storage": "SQLite (babu_knowledge) & Markdown (System_Information_Index.md)",
        "retention": "Document Version Canonical",
        "privacy": "Class A (Domain Grounding)",
        "fetch_method": "rag_search(query, top_k=3)",
        "bulk_dump_allowed": False
    },
    "K7_EXTERNAL": {
        "class": "K7",
        "description": "Live external lookups (DuckDuckGo search, Meta Facebook Graph API)",
        "storage": "Live API / ephemeral search_cache",
        "retention": "12-hour TTL cache (search_cache)",
        "privacy": "Class B/C (External Action Gate)",
        "fetch_method": "duckduckgo_search(query) / publish_facebook_post()",
        "bulk_dump_allowed": False
    }
}

def get_runtime_data_catalog() -> Dict[str, Dict[str, Any]]:
    """Return the complete, structured data catalog index."""
    return RUNTIME_DATA_CATALOG

def resolve_knowledge_class_for_query(query: str) -> str:
    """
    Surgically identify which Knowledge Class (K0-K7) a query targets.
    Prevents cross-class data dumping.
    """
    q = (query or "").lower().strip()
    
    # 1. Telemetry / Uptime / Costs (K2)
    if any(k in q for k in ("uptime", "token cost", "tokens", "latency", "telemetry", "system health", "status dashboard", "stats")):
        return "K2_RUNTIME_TELEMETRY"
        
    # 2. Daily Temporal Activity (K2)
    if any(k in q for k in ("what did you do", "yesterday", "today", "kal kya kiya", "aaj kya kiya", "activity summary")):
        return "K2_RUNTIME_TELEMETRY"
        
    # 3. Personal Identity / Family / Private Facts (K1)
    if any(k in q for k in ("who am i", "my name", "my father", "my mother", "my wife", "my education", "my nickname")):
        return "K1_IDENTITY"
        
    # 4. Consultancy / Business / Pricing / ITR / GST / PF (K3)
    if any(k in q for k in ("consultancy", "anshu computer", "gst service", "pf claim", "itr filing", "tax service", "consultancy fee")):
        return "K3_BUSINESS"
        
    # 5. Architecture / ADRs / Tradeoffs / Immune Lessons (K5)
    if any(k in q for k in ("adr", "architecture", "tradeoff", "highest impact", "immune lesson", "anti-pattern", "evolution", "upgrades")):
        return "K5_ARCHITECTURE_GOVERNANCE"
        
    # 6. Default to Conversational Working Memory (K0)
    return "K0_WORKING_MEMORY"
