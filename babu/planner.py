"""
planner.py — BABU's Goal Decomposition Engine

Decomposes user goals into structured task DAGs (GoalGraph) using a single
LLM call. Supports three planning modes:

- plan_goal()        : Full LLM-based planning for SPRINT/LAUNCH gears
- build_walk_graph() : Trivial single-task graph for simple queries (no LLM)
- build_action_graph(): 2-task graph for detected actions (no LLM)

All plans produce a GoalGraph containing TaskDTO nodes with dependency edges.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

try:
    from .task_engine import TaskDTO, GoalGraph, TaskState, validate_dag
except ImportError:
    from task_engine import TaskDTO, GoalGraph, TaskState, validate_dag


# ---------------------------------------------------------------------------
# Department taxonomy
# ---------------------------------------------------------------------------

DEPARTMENTS: Dict[str, str] = {
    "information": "Quick general web search, Wikipedia lookup, simple data collection, or local profile lookup (default for standard queries)",
    "research": "Deep academic or comprehensive multi-source web research requiring strict citations, verifications, and source listing (ONLY when user explicitly requests research)",
    "analysis": "Data analysis, sentiment analysis, comparison, pattern recognition",
    "writing": "Report generation, content creation, summarization, formatting",
    "execution": "Google Workspace actions (email, calendar, sheets, docs),Post_to_facebook",
    "pa": "Direct user response synthesis",
}

# ---------------------------------------------------------------------------
# ADR-101 Tri-Domain & Capability Demand Action Registry
# ---------------------------------------------------------------------------
from dataclasses import dataclass, field
from typing import List, Optional, Set, Dict, Any, Union

DOMAIN_ACTIONS_REGISTRY: Dict[str, Set[str]] = {
    "USER": {
        "send_email", "search_profile", "search_gmail", "search_sheet", "create_doc", 
        "create_event", "create_task", "upload_to_drive", "copy_photos_to_drive", 
        "copy_contacts_to_drive", "delete_document", "delete_spreadsheet", "delete_event"
    },
    "SYSTEM": {
        "system_status", "system_diagnostics", "memory_stats", "model_info", "clear_memory"
    },
    "BUSINESS": {
        "read_facebook_comments", "read_facebook_posts", "reply_facebook_comment", 
        "post_to_facebook", "generate_image", "crm_query_leads", "crm_book_appointment", 
        "crm_update_lead", "crm_schedule_followup", "crm_cancel_appointment", "log_to_sheet", "send_slack"
    }
}

VALID_DOMAINS: Set[str] = {"USER", "SYSTEM", "BUSINESS"}


def derive_authorized_actions(domains: Set[str], candidate_actions: List[str]) -> List[str]:
    """
    Gatekeeper derivation: Filter candidate actions against the authorized domain registries.
    Invariant: An action is eligible only when its declared capability domain belongs to 
    the demand's authorized domain set.
    """
    authorized_registry = set()
    for d in (domains or set()):
        if d in DOMAIN_ACTIONS_REGISTRY:
            authorized_registry.update(DOMAIN_ACTIONS_REGISTRY[d])
            
    # Universal read-only lookup helpers
    universal_lookups = {"search_sheet", "search_gmail", "search_image", "web_search", "wikipedia_search"}
    authorized_registry.update(universal_lookups)
    
    return [act for act in candidate_actions if act in authorized_registry]


@dataclass
class DemandPacket:
    """Streamlined Capability Demand Packet (ADR-101) representing the core execution demand."""
    domains: set[str]                 # Authorized subset of {"USER", "SYSTEM", "BUSINESS"}
    execution_shape: str              # "CLASS_A" (LOOKUP) | "CLASS_B" (LOOKUP+ACTION) | "CLASS_C" (COMPOSED / MULTI-STEP)
    operations: set[str]              # {"LOOKUP"}, {"LOOKUP", "ACTION"}, {"LOOKUP", "ACTION", "COMPOSED"}
    candidate_actions: list[str]      # Proposed actions from intent classification
    allowed_actions: list[str]        # Strictly derived by Gatekeeper domain authorization
    approval_policy: str = "AUTO"     # "AUTO" | "APPROVAL_REQUIRED" | "DOUBLE_CONFIRMATION"
    risk_level: str = "LOW"           # "LOW" | "MEDIUM" | "HIGH"
    planning_required: bool = False
    confidence: float = 1.0
    query_category: str = "PUBLIC_INFORMATION"

    def to_dict(self) -> dict:
        return {
            "domains": list(self.domains),
            "execution_shape": self.execution_shape,
            "operations": list(self.operations),
            "candidate_actions": self.candidate_actions,
            "allowed_actions": self.allowed_actions,
            "approval_policy": self.approval_policy,
            "risk_level": self.risk_level,
            "planning_required": self.planning_required,
            "confidence": self.confidence,
            "query_category": self.query_category
        }


class IntentPacket:
    """Carries the output of the query intent classifier."""

    def __init__(
        self,
        lookup: bool = False,
        research: bool = False,
        generate: bool = False,
        execute: bool = False,
        websearch: bool = False,
        writer: bool = False,
        execution_mode: str = "READ_ONLY",
        confidence: float = 1.0,
        allowed_departments: Optional[list[str]] = None,
        allowed_actions: Optional[list[str]] = None,
        candidate_actions: Optional[list[str]] = None,
        tokens: Optional[dict] = None,
        model: Optional[str] = None,
        system_query: bool = False,
        query_category: str = "PUBLIC_INFORMATION",
        topology_source: str = "EXTERNAL",
        topology_mode: str = "LOOKUP",
        mutation_type: str = "NONE",
        domain: str = "USER",
        surface: str = "SYSTEM",
        planning_required: bool = False,
        demand_domains: Optional[set[str] | list[str]] = None,
        execution_shape: Optional[str] = None,
        approval_policy: Optional[str] = None,
        risk_level: Optional[str] = None,
    ) -> None:
        self.execution_mode = execution_mode
        self.confidence = confidence
        self.tokens = tokens or {"prompt": 0, "completion": 0, "total": 0}
        self.model = model
        self.system_query = system_query
        self.query_category = query_category
        self.topology_source = topology_source
        self.topology_mode = topology_mode
        self.mutation_type = mutation_type
        self.domain = domain
        self.surface = surface
        self.planning_required = planning_required

        # Resolve ADR-101 Authorized Domains Set (USER | SYSTEM | BUSINESS)
        if demand_domains:
            self.demand_domains = set(demand_domains) & VALID_DOMAINS
            if not self.demand_domains:
                self.demand_domains = {"USER"}
        else:
            resolved = set()
            if query_category == "PERSONAL_INFORMATION" or domain == "USER":
                resolved.add("USER")
            if query_category == "SYSTEM_INFORMATION" or domain in ("SYSTEM", "BABU_SYSTEM"):
                resolved.add("SYSTEM")
            if query_category == "BUSINESS_INFORMATION" or domain in ("BUSINESS", "META", "TAX_COMPLIANCE"):
                resolved.add("BUSINESS")
            if not resolved:
                # Default to USER for operator queries, BUSINESS for client queries
                resolved = {"USER"}
            self.demand_domains = resolved

        # Candidate actions from classifier
        if candidate_actions is not None:
            self.candidate_actions = candidate_actions
        elif allowed_actions is not None:
            self.candidate_actions = allowed_actions
        else:
            actions = []
            if execute:
                actions = [
                    "send_email", "create_event", "log_to_sheet", "create_doc", 
                    "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive", 
                    "send_slack", "create_task", "search_image", "search_gmail",
                    "post_to_facebook", "generate_image", "upload_to_drive",
                    "read_facebook_comments", "read_facebook_posts", "reply_facebook_comment",
                    "crm_query_leads", "crm_book_appointment", "crm_update_lead", "crm_schedule_followup"
                ]
            else:
                actions = ["search_sheet", "search_gmail", "read_facebook_comments", "read_facebook_posts"]
            self.candidate_actions = actions

        # Gatekeeper Authorizes Actions: candidate_actions -> domain authorization filter
        self.allowed_actions = derive_authorized_actions(self.demand_domains, self.candidate_actions)

        # Resolve ADR-101 Execution Shape: CLASS_A (LOOKUP), CLASS_B (LOOKUP+ACTION), CLASS_C (COMPOSED)
        if execution_shape:
            self.execution_shape = execution_shape
        else:
            if planning_required or len(self.demand_domains) > 1:
                self.execution_shape = "CLASS_C"
            elif execute or any(act in self.allowed_actions for act in ("send_email", "create_event", "create_doc", "post_to_facebook", "reply_facebook_comment", "crm_book_appointment", "crm_update_lead", "delete_document", "delete_spreadsheet")):
                self.execution_shape = "CLASS_B"
            else:
                self.execution_shape = "CLASS_A"

        # Resolve Risk Level & Mutation Approval Policy
        destructive_actions = {"delete_document", "delete_spreadsheet", "delete_event", "mass_update", "bulk_delete"}
        if risk_level:
            self.risk_level = risk_level
        else:
            if any(act in self.allowed_actions for act in destructive_actions):
                self.risk_level = "HIGH"
            elif self.execution_shape in ("CLASS_B", "CLASS_C") and self.execute:
                self.risk_level = "MEDIUM"
            else:
                self.risk_level = "LOW"

        if approval_policy:
            self.approval_policy = approval_policy
        else:
            if self.risk_level == "HIGH":
                self.approval_policy = "DOUBLE_CONFIRMATION"
            elif any(act in self.allowed_actions for act in ("reply_facebook_comment", "crm_book_appointment")):
                self.approval_policy = "AUTO"  # Autonomous Meta/CRM responses
            elif self.execute:
                self.approval_policy = "APPROVAL_REQUIRED"
            else:
                self.approval_policy = "AUTO"

        if allowed_departments is not None:
            self.allowed_departments = allowed_departments
        else:
            depts = ["pa"]
            if lookup or websearch or self.execution_shape == "CLASS_A":
                depts.extend(["information", "execution"])
            if research or self.execution_shape == "CLASS_C":
                depts.extend(["research", "information", "execution"])
            if generate or writer:
                depts.extend(["analysis", "writing"])
            if execute or self.execution_shape in ("CLASS_B", "CLASS_C"):
                depts.extend(["execution"])
            self.allowed_departments = list(dict.fromkeys(depts))

    @property
    def demand_packet(self) -> DemandPacket:
        ops = {"LOOKUP"}
        if self.execution_shape == "CLASS_B":
            ops.add("ACTION")
        elif self.execution_shape == "CLASS_C":
            ops.update({"ACTION", "COMPOSED"})
        return DemandPacket(
            domains=self.demand_domains,
            execution_shape=self.execution_shape,
            operations=ops,
            candidate_actions=self.candidate_actions,
            allowed_actions=self.allowed_actions,
            approval_policy=self.approval_policy,
            risk_level=self.risk_level,
            confidence=self.confidence,
            planning_required=self.planning_required,
            query_category=self.query_category
        )

    @property
    def lookup(self) -> bool:
        return "information" in self.allowed_departments

    @property
    def research(self) -> bool:
        return "research" in self.allowed_departments

    @property
    def generate(self) -> bool:
        return any(d in self.allowed_departments for d in ("analysis", "writing"))

    @property
    def execute(self) -> bool:
        mutating_actions = {
            "send_email", "create_event", "log_to_sheet", "create_doc", 
            "copy_photos_to_drive", "copy_contacts_to_drive", 
            "send_slack", "create_task", "post_to_facebook", "upload_to_drive",
            "reply_facebook_comment", "crm_book_appointment", "crm_update_lead", "crm_schedule_followup"
        }
        return any(act in self.allowed_actions for act in mutating_actions)

    @property
    def websearch(self) -> bool:
        return any(d in self.allowed_departments for d in ("research", "information"))

    @property
    def writer(self) -> bool:
        return "writing" in self.allowed_departments

    def to_dict(self) -> dict:
        return {
            "lookup": self.lookup,
            "research": self.research,
            "generate": self.generate,
            "execute": self.execute,
            "websearch": self.websearch,
            "writer": self.writer,
            "execution_mode": self.execution_mode,
            "confidence": self.confidence,
            "allowed_departments": self.allowed_departments,
            "allowed_actions": self.allowed_actions,
            "candidate_actions": self.candidate_actions,
            "tokens": self.tokens,
            "model": self.model,
            "system_query": self.system_query,
            "query_category": self.query_category,
            "topology_source": self.topology_source,
            "topology_mode": self.topology_mode,
            "mutation_type": self.mutation_type,
            "domain": self.domain,
            "surface": self.surface,
            "planning_required": self.planning_required,
            "demand_domains": list(self.demand_domains),
            "execution_shape": self.execution_shape,
            "approval_policy": self.approval_policy,
            "risk_level": self.risk_level,
            "demand_packet": self.demand_packet.to_dict()
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IntentPacket":
        """Construct IntentPacket from dictionary JSON payload safely."""
        allowed_depts = data.get("allowed_departments")
        allowed_actions = data.get("allowed_actions")

        if allowed_depts is None:
            allowed_depts = ["pa"]
            if data.get("lookup") or data.get("websearch"):
                allowed_depts.extend(["information", "execution"])
            if data.get("research"):
                allowed_depts.extend(["research", "information", "execution"])
            if data.get("generate") or data.get("writer"):
                allowed_depts.extend(["analysis", "writing"])
            if data.get("execute"):
                allowed_depts.extend(["execution"])
            allowed_depts = list(dict.fromkeys(allowed_depts))

        if allowed_actions is None:
            if data.get("execute"):
                allowed_actions = [
                    "send_email", "create_event", "log_to_sheet", "create_doc", 
                    "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive", 
                    "send_slack", "create_task", "search_image", "search_gmail",
                    "post_to_facebook", "generate_image", "upload_to_drive",
                    "read_facebook_comments", "read_facebook_posts", "reply_facebook_comment",
                    "crm_query_leads", "crm_book_appointment", "crm_update_lead", "crm_schedule_followup"
                ]
            else:
                allowed_actions = ["search_sheet", "search_gmail", "read_facebook_comments", "read_facebook_posts"]

        return cls(
            allowed_departments=allowed_depts,
            allowed_actions=allowed_actions,
            execution_mode=data.get("execution_mode", "READ_ONLY"),
            confidence=data.get("confidence", 1.0),
            tokens=data.get("tokens"),
            model=data.get("model"),
            system_query=data.get("system_query", False),
            query_category=data.get("query_category", "PUBLIC_INFORMATION"),
            topology_source=data.get("topology_source", "INTERNAL" if data.get("system_query") else "EXTERNAL"),
            topology_mode=data.get("topology_mode", "HYBRID" if data.get("execute") and data.get("lookup") else ("ACTION" if data.get("execute") else "LOOKUP")),
            mutation_type=data.get("mutation_type", "EXTERNAL" if data.get("execute") else "NONE"),
            domain=data.get("domain", "META" if "facebook" in str(allowed_actions) else ("GOOGLE" if any(act in str(allowed_actions) for act in ("gmail", "sheet", "drive", "event")) else "GENERAL")),
            surface=data.get("surface", "SYSTEM"),
            planning_required=data.get("planning_required", True if data.get("execute") and data.get("lookup") else False),
            demand_domains=data.get("demand_domains") or data.get("domains") or ([data.get("demand_domain")] if data.get("demand_domain") else None),
            execution_shape=data.get("execution_shape"),
            approval_policy=data.get("approval_policy"),
            risk_level=data.get("risk_level")
        )

INTENT_CLASSIFIER_SYSTEM_PROMPT: str = (
    "You are BABU's Topology-Aware Query Classifier. Your job is to classify the user's "
    "query into a structured IntentPacket JSON containing capability routing templates and execution topology demand.\n"
    "\n"
    "TOPOLOGY EXECUTION CLASSES:\n"
    "- topology_source: INTERNAL (BABU codebase, ADRs, knowledge base, system stats) | EXTERNAL (Meta Facebook, Google Workspace, DuckDuckGo) | BOTH\n"
    "- topology_mode: CHITCHAT (Greeting/help) | LOOKUP (Read-only retrieval) | ACTION (Direct mutation) | HYBRID (Read -> Reason -> Act composed workflow)\n"
    "- mutation_type: NONE (Read-only) | INTERNAL (Save config/template) | EXTERNAL (Post FB, Send Email, Create Task) | BOTH\n"
    "- domain: BABU_SYSTEM | META | GOOGLE | TAX_COMPLIANCE | GENERAL\n"
    "- surface: PAGE | INSTAGRAM | MESSENGER | GMAIL | CALENDAR | DOCS | SHEETS | TASKS | CODEBASE | DATABASE | SYSTEM\n"
    "- planning_required: true if HYBRID or multi-step workflow composition is needed; false for direct fast-track/lookup/single-action.\n"
    "\n"
    "QUERY CATEGORY DEFINITIONS:\n"
    "- COMMUNICATION: Set if query relates to sending emails, searching Gmail, or direct messaging.\n"
    "- WORKSPACE: Set if query relates to Google Workspace operations (Calendar meetings/events, Google Docs creation, Sheets logging, Drive).\n"
    "- BUSINESS_INFORMATION: Set if query relates to user's business context (Anshu Computer & Tax Consultancy), client records, services, customer claims (PF/GST/ITR), or Facebook marketing.\n"
    "- PERSONAL_INFORMATION: Set if query relates to personal details, family members, home address, personal phone/email.\n"
    "- SYSTEM_INFORMATION: Set if query relates to system itself (BABU), architecture, logs, ADRs, health/uptime.\n"
    "- CONVERSATION: Set if query is a greeting, casual chitchat, thank-you, or conversational pleasantry.\n"
    "- PUBLIC_INFORMATION: Set strictly if query is an external public knowledge inquiry, web search (via Tavily/web), Wikipedia research, public news, general facts, or definitions.\n"
    "\n"
    "DEPARTMENT DEFINITIONS:\n"
    "- information: General web search, information retrieval, quick facts, local profile/memory search.\n"
    "- research: Explicit deep research requiring multi-source verification and citations.\n"
    "- analysis: Data analysis, reasoning, or comparing data.\n"
    "- writing: Summarization, report drafting, email text, response formatting.\n"
    "- execution: Physical actions (email, calendar, docs, sheets, facebook) or Workspace search.\n"
    "- pa: Direct user response synthesis (always include 'pa').\n"
    "\n"
    "EXECUTION MODE DEFINITION:\n"
    "- READ_ONLY: Informational/research query. No mutations.\n"
    "- APPROVAL_REQUIRED: Mutation requested requiring user audit/approval.\n"
    "- AUTO_EXECUTE: Scheduled or background tasks.\n"
    "\n"
    "JSON SCHEMA:\n"
    "{\n"
    '  "topology_source": "INTERNAL | EXTERNAL | BOTH",\n'
    '  "topology_mode": "CHITCHAT | LOOKUP | ACTION | HYBRID",\n'
    '  "mutation_type": "NONE | INTERNAL | EXTERNAL | BOTH",\n'
    '  "domain": "BABU_SYSTEM | META | GOOGLE | TAX_COMPLIANCE | GENERAL",\n'
    '  "surface": "PAGE | INSTAGRAM | MESSENGER | GMAIL | CALENDAR | DOCS | SHEETS | TASKS | CODEBASE | DATABASE | SYSTEM",\n'
    '  "planning_required": true | false,\n'
    '  "allowed_departments": ["list", "of", "required", "departments"],\n'
    '  "allowed_actions": ["list", "of", "permitted", "actions"],\n'
    '  "execution_mode": "READ_ONLY | APPROVAL_REQUIRED | AUTO_EXECUTE",\n'
    '  "confidence": 0.0 to 1.0,\n'
    '  "system_query": true | false,\n'
    '  "query_category": "COMMUNICATION | WORKSPACE | BUSINESS_INFORMATION | PERSONAL_INFORMATION | SYSTEM_INFORMATION | CONVERSATION | PUBLIC_INFORMATION"\n'
    "}\n"
    "\n"
    "CRITICAL: Output ONLY valid raw JSON. No explanation, no markdown fences."
)

def classify_intent(query: str, history_text: str = "", model_name: str = "groq/compound-mini") -> IntentPacket:
    """Classify user query intent into a structured IntentPacket."""
    t = query.lower().strip()
    
    # 1. Rule-based fast-track bypass for greetings, short chitchat, and stats commands
    greetings = {"hi", "hello", "hey", "good morning", "good afternoon", "good evening", "how are you", "help", "clear", "stats", "model"}
    is_greeting = (
        t in greetings 
        or len(t) < 15 
        or any(t.startswith(g) for g in ("hi ", "hello ", "hey ", "good morning", "good afternoon", "good evening"))
    ) and not any(k in t for k in ("email", "mail", "post", "facebook", "doc", "sheet", "drive", "lead", "appointment", "event", "task", "pf", "itr", "gst", "tax", "report", "client", "customer", "delete"))

    if is_greeting:
        print("[INTENT CLASSIFIER] Fast-track classification: CHORE / GREETING", flush=True)
        return IntentPacket(
            allowed_departments=["information", "pa"],
            allowed_actions=["search_sheet", "search_gmail"],
            execution_mode="READ_ONLY",
            confidence=1.0,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            model="rules_engine",
            query_category="CONVERSATION",
            topology_source="INTERNAL",
            topology_mode="CHITCHAT",
            mutation_type="NONE",
            domain="USER",
            surface="SYSTEM",
            planning_required=False,
            demand_domains={"USER"},
            execution_shape="CLASS_A"
        )

    # 1b. Rule-based programmatic override: force lookup when query contains personal data references
    # The LLM (small model) frequently ignores this for email/phone/name/address patterns
    _personal_data_markers = (
        "my official mail", "my official email", "official mail", "official email",
        "my personal mail", "my personal email", "personal mail", "personal email",
        "my phone", "my number", "my mobile", "my address", "my name", "my nickname",
        "my email", "my mail", "to my mail", "to my email", "my mother",
        "my father", "my family", "my parents", "my brother", "my sister",
        "my sibling", "my profile", "who am i", "my boss", "leave", "boss",
    )
    _force_lookup = any(marker in t for marker in _personal_data_markers)

    # Deterministic governance must not disappear when an inference provider is
    # unavailable.  High-confidence execution classes are therefore identified
    # before the optional semantic classifier runs.
    detected_action = None
    _fb_markers = ("facebook", "faceook", "fb", "meta", "page")
    _has_fb = any(m in t for m in _fb_markers)
    if _has_fb and any(k in t for k in ("comment", "comments", "feed", "activity", "review", "reviews")):
        detected_action = "read_facebook_comments"
    elif _has_fb and any(k in t for k in ("post", "posts", "update", "updates", "wall")) and any(kw in t for kw in ("check", "read", "fetch", "get", "recent", "list", "latest", "show")):
        detected_action = "read_facebook_posts"
    elif "send" in t and ("email" in t or "mail" in t):
        detected_action = "send_email"
    elif "create" in t and "event" in t:
        detected_action = "create_event"
    elif "create" in t and ("document" in t or "doc" in t):
        detected_action = "create_doc"
    elif _has_fb and any(kw in t for kw in ("post", "publish", "share", "upload")):
        detected_action = "post_to_facebook"
    elif ("save" in t or "upload" in t) and ("drive" in t or "google drive" in t or "report" in t):
        detected_action = "upload_to_drive"

    if detected_action:
        is_read_action = detected_action in ("read_facebook_comments", "read_facebook_posts", "search_sheet", "search_gmail")
        is_fb_action = "facebook" in detected_action
        scheduled = any(marker in t for marker in ("scheduled", "daily", "automatically", "background"))
        try:
            from babu.bot import is_system_aware_query
        except ImportError:
            from bot import is_system_aware_query
        
        is_sys = is_system_aware_query(query)
        target_domain = "SYSTEM" if is_sys else ("BUSINESS" if is_fb_action else "USER")
        if detected_action in ("send_email", "search_gmail"):
            detected_category = "COMMUNICATION"
        elif detected_action in ("create_event", "create_doc", "upload_to_drive", "search_sheet"):
            detected_category = "WORKSPACE"
        elif is_sys:
            detected_category = "SYSTEM_INFORMATION"
        elif is_fb_action:
            detected_category = "BUSINESS_INFORMATION"
        elif _force_lookup:
            detected_category = "PERSONAL_INFORMATION"
        else:
            detected_category = "WORKSPACE"

        packet = IntentPacket(
            allowed_departments=["information", "writing", "execution", "pa"],
            allowed_actions=[detected_action],
            execution_mode="READ_ONLY" if is_read_action else ("AUTO_EXECUTE" if scheduled else "APPROVAL_REQUIRED"),
            confidence=0.95,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            model="rules_engine",
            query_category=detected_category,
            system_query=is_sys,
            topology_source="EXTERNAL",
            topology_mode="LOOKUP" if is_read_action else "ACTION",
            mutation_type="NONE" if is_read_action else "EXTERNAL",
            domain=target_domain,
            surface="PAGE" if is_fb_action else "SYSTEM",
            planning_required=False,
            demand_domains={target_domain},
            execution_shape="CLASS_A" if is_read_action else "CLASS_B",
            risk_level="LOW" if is_read_action else "MEDIUM",
            approval_policy="AUTO" if is_read_action else ("AUTO" if scheduled else "APPROVAL_REQUIRED")
        )
        if is_sys and not is_read_action:
            packet.execution_mode = "APPROVAL_REQUIRED"
        return packet

    if any(marker in t for marker in ("email", "emails", "gmail", "mail")) and any(marker in t for marker in ("check", "search", "find", "recent", "updates", "summarize")):
        return IntentPacket(
            allowed_departments=["information", "execution", "writing", "pa"],
            allowed_actions=["search_gmail"],
            execution_mode="READ_ONLY",
            confidence=0.9,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            model="rules_engine",
            query_category="COMMUNICATION",
        )

    # 1c. Ambiguous / Vague directive gate -> trigger low-confidence clarification
    _vague_patterns = ("take care of this", "handle it", "you know what to do", "do the thing", "do this thing", "take care of that")
    if any(p in t for p in _vague_patterns) and not any(k in t for k in ("email", "mail", "post", "facebook", "doc", "sheet", "drive", "lead", "appointment", "event", "task")):
        return IntentPacket(
            allowed_departments=["pa"],
            allowed_actions=[],
            execution_mode="READ_ONLY",
            confidence=0.4,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            model="clarification_gate",
            query_category="CONVERSATION",
            planning_required=False
        )

    # 2. LLM-based robust classification
    from langchain_core.messages import SystemMessage, HumanMessage
    try:
        try:
            from babu.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback

        # Dynamic loading and injection of governance classification negative constraints
        try:
            from memory import get_anti_pattern_rules
        except ImportError:
            from .memory import get_anti_pattern_rules
            
        gov_rules = get_anti_pattern_rules("governance.classification")
        system_prompt = INTENT_CLASSIFIER_SYSTEM_PROMPT
        if gov_rules:
            system_prompt += f"\n\n[CRITICAL HISTORICAL GOVERNANCE RULES]\n{gov_rules}"

        history_snippet = (history_text[:300] + "…") if len(history_text) > 300 else history_text
        user_content = f"User Query: {query}\n"
        if history_snippet:
            user_content += f"Recent History: {history_snippet}"

        response = invoke_with_fallback(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            model_name=model_name,
            temp=0.0,  # Highly deterministic
        )
        raw_text = response.content.strip()
        data = _extract_json(raw_text)
        packet = IntentPacket.from_dict(data)
        # Programmatic override: ensure lookup/information is present if personal data is referenced
        if _force_lookup and not packet.lookup:
            print("[INTENT CLASSIFIER] Programmatic override: forcing information department due to personal data reference in query.", flush=True)
            allowed_depts = list(packet.allowed_departments)
            if "information" not in allowed_depts:
                allowed_depts.append("information")
            if "execution" not in allowed_depts:
                allowed_depts.append("execution")
            allowed_actions = list(packet.allowed_actions)
            for act in ("search_sheet", "search_gmail"):
                if act not in allowed_actions:
                    allowed_actions.append(act)
            packet = IntentPacket(
                allowed_departments=allowed_depts,
                allowed_actions=allowed_actions,
                execution_mode=packet.execution_mode,
                confidence=packet.confidence,
                system_query=packet.system_query
            )
        
        # Programmatic cleanup: if execution_mode is READ_ONLY and the query does not ask to search workspace (sheets/gmail/doc),
        # remove 'execution' from allowed_departments and clear allowed_actions to prevent planner hallucination of actions.
        workspace_keywords = ["sheet", "spreadsheet", "gmail", "mail", "email", "doc", "document", "drive", "google"]
        has_workspace_ref = any(kw in query.lower() for kw in workspace_keywords)
        
        if packet.execution_mode == "READ_ONLY" and not has_workspace_ref:
            allowed_depts = [d for d in packet.allowed_departments if d != "execution"]
            if not allowed_depts:
                allowed_depts = ["information", "pa"]
            packet = IntentPacket(
                allowed_departments=allowed_depts,
                allowed_actions=[],
                execution_mode="READ_ONLY",
                confidence=packet.confidence,
                system_query=packet.system_query
            )
        try:
            from babu.services import extract_tokens
        except ImportError:
            from services import extract_tokens
        packet.tokens = extract_tokens(response)
        packet.model = model_name
        
        # Programmatic check for system query keyword matches using unified check
        try:
            from babu.bot import is_system_aware_query
        except ImportError:
            from bot import is_system_aware_query
            
        if is_system_aware_query(query):
            packet.system_query = True
            has_execution = "execution" in packet.allowed_departments and len(packet.allowed_actions) > 0
            if has_execution:
                packet.execution_mode = "APPROVAL_REQUIRED"  # Enforce approval check for safety
            else:
                packet.execution_mode = "READ_ONLY"
                packet.allowed_actions = []
                allowed_depts = [d for d in packet.allowed_departments if d != "execution"]
                if "pa" not in allowed_depts:
                    allowed_depts.append("pa")
                packet.allowed_departments = allowed_depts
            
        # Programmatic query category overrides
        lowered = query.lower()
        if packet.system_query or any(k in lowered for k in ("failures", "uptime", "upgrades", "upgrade", "adr", "tradeoff", "tradeoffs", "health dashboard")):
            packet.query_category = "SYSTEM_INFORMATION"
        elif any(k in lowered for k in ("anshu", "shubham", "swarnkar", "ash", "ssoni", "who am i", "who i am", "who i m", "who m i", "my father", "my mother", "my brother", "my sibling", "my parents", "my cousin", "my background", "my journey", "my education", "my career", "my email", "my phone", "my number", "my address", "my location", "where i live", "tell me about me", "my profile", "my biography", "my bio", "about me", "know about me")):
            packet.query_category = "PERSONAL_INFORMATION"
        elif any(k in lowered for k in ("email", "gmail", "mail", "inbox")) and any(k in lowered for k in ("send", "draft", "write", "check", "search", "read", "fetch", "reply", "to", "regarding")):
            packet.query_category = "COMMUNICATION"
        elif any(k in lowered for k in ("calendar", "meeting", "event", "schedule", "doc", "document", "sheet", "spreadsheet", "drive", "excel")):
            packet.query_category = "WORKSPACE"
        elif any(k in lowered for k in ("facebook", "faceook", "fb", "instagram", "insta", "post", "posts", "comment", "comments", "lead", "leads", "appointment", "followup", "client", "clients", "customer", "customers", "invoice", "invoices", "payment", "payments", "transaction", "transactions", "sales", "earnings", "revenue", "profit", "profits", "ledger", "ledgers", "pf claim", "pf claims", "uan consolidation", "kyc correction", "joint declaration", "gst registration", "gstr-1", "gstr-3b")):
            is_general = any(g in lowered for g in ("what is", "how to", "definition", "explain", "tutorial", "general process")) and not any(f in lowered for f in ("facebook", "faceook", "fb", "post", "lead", "appointment"))
            if not is_general:
                packet.query_category = "BUSINESS_INFORMATION"
            else:
                packet.query_category = "PUBLIC_INFORMATION"
        elif any(k in lowered for k in ("search", "who is", "what is", "when did", "where is", "how does", "news", "weather", "wikipedia", "tavily", "google", "history of", "explain", "current affairs")):
            packet.query_category = "PUBLIC_INFORMATION"
        elif getattr(packet, "query_category", "") in ("COMMUNICATION", "WORKSPACE", "BUSINESS_INFORMATION", "PERSONAL_INFORMATION", "SYSTEM_INFORMATION", "CONVERSATION", "PUBLIC_INFORMATION"):
            pass
        else:
            packet.query_category = "PUBLIC_INFORMATION"

        print(f"[INTENT CLASSIFIER] Classified: allowed_depts={packet.allowed_departments}, allowed_actions={packet.allowed_actions}, mode={packet.execution_mode}, conf={packet.confidence}, sys_query={packet.system_query}, query_category={packet.query_category}", flush=True)
        return packet
    except Exception as e:
        print(f"[INTENT CLASSIFIER] Failed to classify intent: {e}. Defaulting to READ_ONLY fallback.", flush=True)
        default_depts = ["information", "pa"] if _force_lookup else ["pa"]
        default_actions = ["search_sheet", "search_gmail"] if _force_lookup else []
        try:
            from babu.bot import is_system_aware_query
        except ImportError:
            from bot import is_system_aware_query
        is_sys = is_system_aware_query(query)
        packet = IntentPacket(
            allowed_departments=default_depts,
            allowed_actions=default_actions,
            execution_mode="READ_ONLY",
            confidence=0.75 if _force_lookup else 0.5,
            tokens={"prompt": 0, "completion": 0, "total": 0},
            model=model_name,
            system_query=is_sys
        )
        lowered = query.lower()
        if is_sys or any(k in lowered for k in ("failures", "uptime", "upgrades", "upgrade", "adr", "tradeoff", "tradeoffs", "health dashboard")):
            packet.query_category = "SYSTEM_INFORMATION"
        elif _force_lookup or any(k in lowered for k in ("anshu", "shubham", "swarnkar", "ash", "ssoni", "who am i", "who i am", "who i m", "who m i", "my father", "my mother", "my brother", "my sibling", "my parents", "my cousin", "my background", "my journey", "my education", "my career", "my email", "my phone", "my number", "my address", "my location", "where i live", "tell me about me", "my profile", "my biography", "my bio", "about me", "know about me")):
            packet.query_category = "PERSONAL_INFORMATION"
        elif any(k in lowered for k in ("email", "gmail", "mail", "inbox")):
            packet.query_category = "COMMUNICATION"
        elif any(k in lowered for k in ("calendar", "meeting", "event", "doc", "sheet", "drive")):
            packet.query_category = "WORKSPACE"
        elif any(k in lowered for k in ("facebook", "faceook", "fb", "instagram", "insta", "post", "posts", "comment", "comments", "lead", "leads", "appointment", "followup", "client", "clients", "customer", "customers", "invoice", "invoices", "payment", "payments", "transaction", "transactions", "sales", "earnings", "revenue", "profit", "profits", "ledger", "ledgers", "pf claim", "pf claims", "uan consolidation", "kyc correction", "joint declaration", "gst registration", "gstr-1", "gstr-3b")):
            is_general = any(g in lowered for g in ("what is", "how to", "definition", "explain", "tutorial", "general process")) and not any(f in lowered for f in ("facebook", "faceook", "fb", "post", "lead", "appointment"))
            if not is_general:
                packet.query_category = "BUSINESS_INFORMATION"
            else:
                packet.query_category = "PUBLIC_INFORMATION"
        else:
            packet.query_category = "PUBLIC_INFORMATION"
        return packet

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PLANNER_SYSTEM_PROMPT: str = (
    "You are BABU's Strategic Planner. Your ONLY job is to decompose user "
    "goals into structured task graphs.\n"
    "\n"
    "RULES:\n"
    "- Output ONLY valid JSON. No markdown, no explanation.\n"
    "- CRITICAL SYNTAX RULE: Do NOT include trailing commas before closing braces '}' or brackets ']' (never write ',}' or ',]'). Ensure all quotes are properly escaped.\n"
    "- Consult Runtime Index before selecting information source. Under no circumstances should you plan external web search/research for identity, system state, telemetry, configuration, or health. Use only internal/local resources.\n"
    "- GOAL CORRECTIONS: If the user query is a correction, typo fix, or modification of a previous goal in the recent conversation history (e.g. 'I meant monitoring, not monetary' or 'correct the topic to X'), you must identify the corrected goal topic and plan the task DAG for the corrected goal, not the incorrect one.\n"
    "- INTENT CONSTRAINTS: The system has pre-classified the user's intent boundaries. You must strictly obey these constraints:\n"
    "  * allowed_departments: You are ONLY allowed to create tasks for the departments listed in 'allowed_departments'. You may invoke helper departments (such as information, research, analysis, writing, pa) only when they are required to satisfy a permitted goal. Helper departments may not introduce new user goals or mutating actions. Any other department is strictly prohibited.\n"
    "  * allowed_actions: For 'execution' department tasks, you are ONLY allowed to plan the actions listed in 'allowed_actions'. Planning any other execution action is strictly prohibited.\n"
    "  * execution_mode: Read this setting carefully. If it is 'READ_ONLY', you must only plan read-only informational/research tasks and end with a 'pa' task; no draft or mutation actions are allowed. If it is 'APPROVAL_REQUIRED', you can create 'execution' tasks but they will go through an approval check. If it is 'AUTO_EXECUTE', you are allowed to plan automated background execution dispatches.\n"
    "  * CRITICAL: If you cannot satisfy the user's goal under these constraints (e.g., they requested sending an email but 'execution' is not in allowed_departments, or 'send_email' is not in allowed_actions), you MUST NOT plan any tasks. Instead, you MUST return a single JSON object indicating a conflict, using the conflict format described below.\n"
    "- CORRECT TASK SEQUENCING: If the goal requires multiple sequential steps or multiple execution actions (e.g. first research X, then write a report, then create a Google Doc, and finally send an email), you must establish strict dependency links (depends_on) between these tasks to ensure they execute in the correct chronological order (e.g. writing depends on research, Doc creation depends on writing, and email sending depends on Doc creation). If there are multiple execution department tasks, chain them sequentially (T_execution_N depends on T_execution_N-1) to ensure the user audits and approves them in the correct sequence.\n"
    "- Each task must have: task_id (T1, T2, ...), objective, department, depends_on (list of task_ids), priority (1=highest), compliance_checklist (list of strings), grant_profile_access (boolean), and optionally protocol (string) or action (string) with params (object).\n"
    "- protocol: For 'writing' department tasks, you MUST specify the writing protocol based on the task purpose. Valid protocols: 'research' (default, for detailed research analysis), 'email' (for concise email drafts), 'letter' (for formal letters), 'publishing' (for public blog/social posts), 'complaint' (for formal escalations), 'report' (for structured internal docs). This guides the writing style.\n"
    "- grant_profile_access: Set to true ONLY for the single, specific 'information' or 'research' task that requires access to the local user profile (family graph, business services, contact info) to fulfill the user's personal query. For all other tasks, this MUST be false. Do NOT grant profile access to multiple tasks to prevent token bloat and ensure security isolation.\n"
    "- compliance_checklist: A list of 2-3 specific, concrete criteria that the task's output must satisfy for the auditor to approve it (e.g., verifying specific factual items, formatting style, checking profile matches, or ensuring it is not a raw status message).\n"
    "  CRITICAL: For 'writing' tasks, ONLY include checklist items demanding sources, references, or research citations if there is an actual upstream 'research' or 'information' task in the dependency DAG. If the writing task is independent/self-contained with no upstream research, do NOT require sources or citations in the checklist.\n"
    "  CRITICAL: For any 'research' department task, you MUST always include compliance checklist items requiring: (1) source credibility and verifiability, (2) recency and evidence verification, (3) confidence assessment, and (4) explicit evidence citations (naming specific sections, documents, or reports where possible).\n"
    "  CRITICAL: For the 'information' department (used for general lookups, quick web searches, or simple profile searches), do NOT require academic-level citations or verifications. Checklists should only verify factual correctness and coverage.\n"
    "  CRITICAL: Use the 'information' department as the default for all standard queries,Profile lookup,Emaillookup,task lookup, google workspace lookup,quick web lookups, and general information checks. ONLY allocate the 'research' department when the user explicitly requests deep, formal, or comprehensive 'research' in their query.\n"
    "- COLLAPSE READ-ONLY CHAINS: Do NOT create separate multi-step tasks for searching/retrieving, analyzing, and drafting if they are all read-only helper tasks (i.e. no physical/external execution actions like sending emails or creating events). Instead, collapse these steps into a single consolidated worker task (e.g., a single 'information', 'research', or 'writing' task) that executes the lookup and generates the response, followed immediately by the final 'pa' task. This minimizes orchestrator latency.\n"
    "- Valid departments: information, research, analysis, writing, execution, pa\n"
    "- depends_on must reference existing task_ids only\n"
    "- Tasks with no dependencies get depends_on: []\n"
    "- The LAST task should synthesize/deliver the final result to the user and MUST belong to the 'pa' department.\n"
    "- CRITICAL: ONLY create an 'execution' department task if the user's request explicitly asks for a physical action (e.g. sending an email, logging to sheets, creating a document, scheduling a calendar event, generating an image, or performing read-only search/retrieval like search_sheet or search_gmail). Do NOT default to creating execution/action tasks for general informational, question-answering, or web research queries (e.g. 'get the weather update' or 'research cyber security trends'). Such requests should only use 'research', 'analysis', and 'writing' tasks, and end directly with a 'pa' task.\n"
    "- CRITICAL: If the user's query asks to check, retrieve, search, or find information inside their Google Sheets or Gmail, you MUST create an 'execution' department task using 'search_sheet' or 'search_gmail' action. Do NOT use a general 'research' department task for Sheets or Gmail retrieval, because general research cannot access Workspace data.\n"
    "- CRITICAL: If department is 'execution', you MUST specify 'action' and 'params' in that task's JSON object! You must dynamically select the most appropriate action from the list of valid actions based on the user's intent. Do not blindly default to 'send_email'.\n"
    "  Valid actions & parameters:\n"
    "    * send_email(to, subject, body, image_path) -- Use ONLY if user explicitly asked to send/mail an email. In 'params', specify 'to', 'subject', and optionally 'image_path'. If the user's query contains a bracketed document path (e.g. '[Document Attached: <path>]'), you MUST extract this exact path and set it as the 'image_path' parameter.\n"
    "    * create_event(title, date, time, duration, description) -- Use ONLY if user explicitly asked to schedule/create a calendar event.\n"
    "    * log_to_sheet(sheet_name, data) -- Use ONLY if user explicitly asked to log or add data to a spreadsheet/sheet.\n"
    "    * create_doc(title, content) -- Use ONLY if user explicitly asked to write/create/draft a separate document file.\n"
    "    * search_sheet(sheet_name, query) -- Use ONLY if user explicitly asked to query/search/find information inside a spreadsheet/sheet.\n"
    "    * search_gmail(query, max_results) -- Use ONLY if user explicitly asked to search or retrieve recent emails matching a query.\n"
    "    * post_to_facebook(caption, topic, image_path) -- Use ONLY if user explicitly asked to post/publish to Facebook Page. In 'params', specify 'caption', 'topic', or 'image_path'.\n"
    "    * generate_image(prompt) -- Use ONLY if user explicitly asked to generate, create, draw, design, paint or produce a new custom image using AI. In 'params', specify 'prompt'.\n"
    "  In 'params', use the placeholder '[NEEDS_RESEARCH_CONTEXT]' for parameters that depend on upstream findings (e.g. content: '[NEEDS_RESEARCH_CONTEXT]', body: '[NEEDS_RESEARCH_CONTEXT]', caption: '[NEEDS_RESEARCH_CONTEXT]', or image_path: '[NEEDS_RESEARCH_CONTEXT]').\n"
    "  CRITICAL: If a task (like send_email) is designed to transmit/report findings or content generated upstream, it MUST depend directly on the 'writing', 'analysis', or 'research' task that generated that content, NOT on intermediate execution tasks (like 'create_doc' or 'log_to_sheet') which only return a status confirmation message.\n"
    "- Keep tasks atomic — one clear objective each\n"
    "- Minimum 2 tasks for planned workflows\n"
    "- HISTORICAL FAILURE ADAPTATION: Read the [CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED] section carefully. If historical failures or anti-patterns exist for any department (e.g. writing, research, execution), you must actively adapt the task graph to avoid these failures:\n"
    "  * For writing/research citation or structure failures: You MUST explicitly include citation verifier tasks or add specific sub-tasks/dependencies (such as citation formatting and source verification tasks in case of research).\n"
    "  * You MUST explicitly address these constraints in the task objectives and the compliance checklists of the planned tasks to satisfy the quality verifications.\n"
    "\n"
    "Output format (Normal Plan):\n"
    "{\n"
    '  "goal": "brief goal description",\n'
    '  "tasks": [\n'
    '    {"task_id": "T1", "objective": "...", "department": "research", '
    '"depends_on": [], "priority": 1, "compliance_checklist": ["Verify search was done", "No empty results"], "grant_profile_access": true},\n'
    '    {"task_id": "T2", "objective": "...", "department": "writing", "protocol": "email", '
    '"depends_on": ["T1"], "priority": 2, "compliance_checklist": ["Verify draft is concise"], "grant_profile_access": false},\n'
    '    {"task_id": "T3", "objective": "...", "department": "pa", '
    '"depends_on": ["T2"], "priority": 3, "compliance_checklist": ["Verify findings are synthesized factually"], "grant_profile_access": false}\n'
    '  ]\n'
    "}\n"
    "\n"
    "Output format (Constraint Conflict):\n"
    "{\n"
    '  "conflict": {\n'
    '    "reason": "Clear explanation of why the user\'s goal cannot be achieved within the allowed departments or actions."\n'
    '  }\n'
    "}"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _close_truncated_json(text: str) -> str:
    """Safely close unclosed brackets, braces, and quotes if LLM output was truncated."""
    s = text.strip()
    # Remove hanging key/colon at the end e.g. ',"depends_on": ' or ',"depends_on":'
    s = re.sub(r',\s*"[^"]*"\s*:\s*$', '', s)
    s = re.sub(r'{\s*"[^"]*"\s*:\s*$', '{', s)
    s = re.sub(r',\s*$', '', s)

    in_string = False
    escape = False
    open_stack = []
    for char in s:
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if not in_string:
            if char in '{[':
                open_stack.append(char)
            elif char == '}' and open_stack and open_stack[-1] == '{':
                open_stack.pop()
            elif char == ']' and open_stack and open_stack[-1] == '[':
                open_stack.pop()

    if in_string:
        s += '"'

    for opener in reversed(open_stack):
        if opener == '{':
            s += '}'
        elif opener == '[':
            s += ']'

    return s


def _extract_json(text: str) -> dict:
    """Extract a JSON object from LLM output, tolerating markdown fences, surrounding text, trailing commas, single quotes, and truncations."""
    import ast
    cleaned = text.strip()

    # 1. Strip markdown code fences if present anywhere
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
        if match:
            cleaned = match.group(1).strip()
        else:
            cleaned = re.sub(r"^```[a-zA-Z]*\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned).strip()

    # 2. Extract outermost JSON object if conversational text surrounds it
    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        cleaned = cleaned[start_idx:end_idx + 1]

    # Attempt 1: Direct attempt with strict json.loads
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 2: Repair comments and trailing commas
    repaired = cleaned
    repaired = re.sub(r'//.*', '', repaired)
    repaired = re.sub(r'/\*.*?\*/', '', repaired, flags=re.DOTALL)
    for _ in range(3):
        repaired = re.sub(r',\s*([\]}])', r'\1', repaired)

    try:
        return json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 3: ast.literal_eval for Python dictionary syntax (single quotes, True/False/None)
    try:
        val = ast.literal_eval(repaired)
        if isinstance(val, dict):
            return val
    except Exception:
        pass

    # Attempt 4: Fix unquoted property names e.g. { task_id: "T1" }
    unquoted_repaired = re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', repaired)
    for _ in range(3):
        unquoted_repaired = re.sub(r',\s*([\]}])', r'\1', unquoted_repaired)
    try:
        return json.loads(unquoted_repaired)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 5: Replace single-quoted strings with double-quoted strings
    sq_repaired = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', repaired)
    sq_repaired = re.sub(r'\bTrue\b', 'true', sq_repaired)
    sq_repaired = re.sub(r'\bFalse\b', 'false', sq_repaired)
    sq_repaired = re.sub(r'\bNone\b', 'null', sq_repaired)
    for _ in range(3):
        sq_repaired = re.sub(r',\s*([\]}])', r'\1', sq_repaired)
    try:
        return json.loads(sq_repaired)
    except (json.JSONDecodeError, ValueError):
        pass

    # Attempt 6: Truncated JSON recovery
    try:
        closed = _close_truncated_json(repaired)
        return json.loads(closed)
    except Exception:
        pass

    # Fallback to raw json.loads to raise original/informative exception
    return json.loads(cleaned)


def _build_fallback_graph(
    query: str,
    goal_id: Optional[str] = None,
    goal_type: str = "NEW",
    planner_status: str = "FALLBACK",
    intent_packet: Optional[dict] = None
) -> GoalGraph:
    """Return a single-task fail-closed graph refusing execution due to planning ambiguity."""
    if not goal_id:
        now = datetime.now(timezone.utc)
        goal_id = f"G-{now.strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    objective = "Inform the user that the request could not be planned securely because the query is too ambiguous, lacks required context, or violates system safety boundaries. Refuse autonomous execution and request clarity."
    checklist = ["State query ambiguity clearly", "Refuse execution factually"]

    if planner_status == "AMBIGUOUS_QUERY":
        objective = "Politely explain to the user that their query is too vague, ambiguous, or lacks necessary details to plan safely. Ask the user to clarify exactly what objective they want BABU to achieve."
        checklist = ["Politely explain ambiguity", "Ask for specific clarification"]
    elif planner_status == "CONSTRAINT_CONFLICT":
        bounds_desc = "restricted permissions"
        if intent_packet:
            bounds_desc = f"allowed_departments={intent_packet.get('allowed_departments')}, allowed_actions={intent_packet.get('allowed_actions')}, mode={intent_packet.get('execution_mode')}"
        objective = f"Politely explain to the user that their query requires actions or departments that are not permitted under the current intent constraints (Current Boundaries: {bounds_desc}). Ask the user to clarify or request permission changes."
        checklist = ["Explain constraint conflict", "Ask for clarification or permission changes"]

    tasks = [
        TaskDTO(
            task_id="T1",
            objective=objective,
            department="pa",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            compliance_checklist=checklist,
        )
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        status="FAILED",
        created_at=now_iso,
        goal_type=goal_type,
        planner_status=planner_status,
        intent_packet=intent_packet,
    )


def _intent_packet_dict(intent_packet: Optional[IntentPacket | dict]) -> Optional[dict]:
    if intent_packet is None:
        return None
    if isinstance(intent_packet, dict):
        return intent_packet
    return intent_packet.to_dict()


def _has_gmail_lookup_intent(query: str, intent_packet: Optional[IntentPacket]) -> bool:
    text = query.lower()
    actions = set(intent_packet.allowed_actions if intent_packet else [])
    return (
        "search_gmail" in actions
        or (
            any(marker in text for marker in ("email", "emails", "gmail", "mail"))
            and any(marker in text for marker in ("check", "search", "find", "recent", "updates", "summarize"))
        )
    )


def _has_research_report_intent(query: str, intent_packet: Optional[IntentPacket]) -> bool:
    text = query.lower()
    return (
        "research" in text
        and any(marker in text for marker in ("report", "save", "changes", "summary", "brief"))
    ) or bool(intent_packet and intent_packet.research and any(marker in text for marker in ("report", "save", "write")))


def _build_gmail_lookup_graph(
    query: str,
    goal_id: Optional[str] = None,
    goal_type: str = "NEW",
    intent_packet: Optional[IntentPacket | dict] = None,
) -> GoalGraph:
    """Deterministic read-only Gmail lookup plan used when the LLM provider is unavailable."""
    if not goal_id:
        now = datetime.now(timezone.utc)
        goal_id = f"G-{now.strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()
    packet_dict = _intent_packet_dict(intent_packet)

    tasks = [
        TaskDTO(
            task_id="T1",
            objective="Search Gmail read-only for messages relevant to the user's request; do not send, delete, archive, or mutate any email.",
            department="execution",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            context={
                "action": "search_gmail",
                "params": {"query": query, "max_results": 10},
                "intent_packet": packet_dict,
            },
            token_budget=2000,
            compliance_checklist=[
                "Use only the read-only search_gmail action",
                "Do not perform mutating Gmail operations",
                "Return message metadata and snippets needed for synthesis",
            ],
        ),
        TaskDTO(
            task_id="T2",
            objective="Summarize the Gmail search results into concise user-facing findings and separate confirmed facts from missing evidence.",
            department="writing",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
            context={"intent_packet": packet_dict},
            token_budget=2500,
            compliance_checklist=[
                "Summarize only evidence returned by the Gmail search task",
                "Mention if no relevant messages were found",
                "Avoid inventing sender names, dates, or outcomes",
            ],
        ),
        TaskDTO(
            task_id="T3",
            objective="Report the summarized email findings to the user and disclose any limitations or missing access.",
            department="pa",
            depends_on=["T2"],
            priority=3,
            state=TaskState.PENDING,
            context={"intent_packet": packet_dict},
            token_budget=1500,
            compliance_checklist=[
                "Answer directly and briefly",
                "Do not imply any email was changed",
            ],
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        status="ACTIVE",
        created_at=now_iso,
        goal_type=goal_type,
        planner_status="SUCCESS",
        intent_packet=packet_dict,
        planning_tokens={"prompt": 0, "completion": 0, "total": 0},
    )


def _build_research_report_graph(
    query: str,
    goal_id: Optional[str] = None,
    goal_type: str = "NEW",
    intent_packet: Optional[IntentPacket | dict] = None,
) -> GoalGraph:
    """Deterministic research/report plan with explicit source and citation safeguards."""
    if not goal_id:
        now = datetime.now(timezone.utc)
        goal_id = f"G-{now.strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()
    packet_dict = _intent_packet_dict(intent_packet)

    tasks = [
        TaskDTO(
            task_id="T1",
            objective="Research the requested topic using credible, current sources; capture source titles, URLs, publication dates, and exact claims needed for citation.",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            context={"intent_packet": packet_dict},
            token_budget=4500,
            compliance_checklist=[
                "Use multiple credible sources where available",
                "Record source URLs and publication or access dates",
                "Separate verified findings from uncertain or unsupported claims",
            ],
        ),
        TaskDTO(
            task_id="T2",
            objective="Write a concise report from the research with explicit citations, references, and source-linked claims.",
            department="writing",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
            context={"intent_packet": packet_dict},
            token_budget=3500,
            compliance_checklist=[
                "Include a dedicated citation verification check",
                "List all references or sources used",
                "Ensure every material factual claim is traceable to a source",
                "Keep the report concise and avoid unsupported extrapolation",
            ],
        ),
        TaskDTO(
            task_id="T3",
            objective="Present the final report to the user, including limitations and source/reference notes.",
            department="pa",
            depends_on=["T2"],
            priority=3,
            state=TaskState.PENDING,
            context={"intent_packet": packet_dict},
            token_budget=1500,
            compliance_checklist=[
                "Mention citation/reference coverage",
                "Flag any unresolved uncertainty",
            ],
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        status="ACTIVE",
        created_at=now_iso,
        goal_type=goal_type,
        planner_status="SUCCESS",
        intent_packet=packet_dict,
        planning_tokens={"prompt": 0, "completion": 0, "total": 0},
    )


def get_allowed_boundaries(intent_packet_dict: dict) -> tuple[set[str], set[str]]:
    """Dynamically resolve allowed departments and allowed actions based on intent packet."""
    allowed_depts = intent_packet_dict.get("allowed_departments")
    allowed_actions = intent_packet_dict.get("allowed_actions")

    if allowed_depts is None:
        # Fallback to reconstructing from legacy boolean flags for backward compatibility
        allowed_depts = ["pa"]
        if intent_packet_dict.get("lookup") or intent_packet_dict.get("websearch"):
            allowed_depts.extend(["information", "execution"])
        if intent_packet_dict.get("research"):
            allowed_depts.extend(["research", "information", "execution"])
        if intent_packet_dict.get("generate") or intent_packet_dict.get("writer"):
            allowed_depts.extend(["analysis", "writing"])
        if intent_packet_dict.get("execute"):
            allowed_depts.extend(["execution"])
        allowed_depts = list(dict.fromkeys(allowed_depts))

    if allowed_actions is None:
        if intent_packet_dict.get("execute"):
            allowed_actions = [
                "send_email", "create_event", "log_to_sheet", "create_doc", 
                "search_sheet", "copy_photos_to_drive", "copy_contacts_to_drive", 
                "send_slack", "create_task", "search_image", "search_gmail",
                "post_to_facebook", "generate_image", "upload_to_drive"
            ]
        else:
            allowed_actions = ["search_sheet", "search_gmail"]

    allowed_depts_set = set(allowed_depts)
    allowed_depts_set.add("pa")
    # Dynamically allow all helper non-mutating departments so the planner can
    # utilize them for research, retrieval, reasoning, and synthesis as needed.
    allowed_depts_set.update({"information", "research", "analysis", "writing", "pa"})
    return allowed_depts_set, set(allowed_actions)


COMPILED_BRAIN_CONTEXT: Optional[str] = None

def compile_planner() -> str:
    """Load and compile the Layer A Institutional Brain context into memory."""
    global COMPILED_BRAIN_CONTEXT
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.isdir(os.path.join(current_dir, "brain")):
            brain_dir = os.path.join(current_dir, "brain")
        elif os.path.isdir(os.path.join(os.path.dirname(current_dir), "brain")):
            brain_dir = os.path.join(os.path.dirname(current_dir), "brain")
        else:
            brain_dir = os.path.join(current_dir, "brain")
        with open(os.path.join(brain_dir, "constitution.md"), "r", encoding="utf-8") as f:
            const_text = f.read()
        with open(os.path.join(brain_dir, "organization.md"), "r", encoding="utf-8") as f:
            org_text = f.read()
        with open(os.path.join(brain_dir, "doctrine.md"), "r", encoding="utf-8") as f:
            doc_text = f.read()
        with open(os.path.join(brain_dir, "capabilities.md"), "r", encoding="utf-8") as f:
            cap_text = f.read()
            
        COMPILED_BRAIN_CONTEXT = (
            f"\n\n[BRAIN LAYER A: CONSTITUTION]\n{const_text}"
            f"\n\n[BRAIN LAYER A: ORGANIZATION]\n{org_text}"
            f"\n\n[BRAIN LAYER A: DOCTRINE]\n{doc_text}"
            f"\n\n[BRAIN LAYER A: CAPABILITIES]\n{cap_text}"
        )
        print("[PLANNER] Compiled Layer A Brain successfully.", flush=True)
    except Exception as e:
        print(f"[PLANNER ERROR] Failed to compile Layer A Brain: {e}", flush=True)
        COMPILED_BRAIN_CONTEXT = ""
    return COMPILED_BRAIN_CONTEXT


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def plan_goal(
    query: str,
    gear: Optional[str] = None,
    history_text: str = "",
    profile_text: str = "",
    model_name: str = "openai/gpt-oss-120b",
    goal_id: Optional[str] = None,
    is_correction: bool = False,
    last_goal_text: Optional[str] = None,
    intent_packet: Optional[IntentPacket] = None,
    session_id: Optional[str] = None,
    awareness_report: Optional[dict] = None,
) -> GoalGraph:
    """Decompose *query* into a structured GoalGraph using a single LLM call.

    Parameters
    ----------
    query : str
        The user's natural-language goal.
    gear : Optional[str]
        Ignored (retained for backward compatibility).
    history_text : str, optional
        Recent conversation history (truncated to 500 chars internally).
    profile_text : str, optional
        User profile context to help the planner personalise tasks.
    model_name : str, optional
        Groq model identifier. Defaults to ``llama-3.1-8b-instant``.
    intent_packet : IntentPacket, optional
        The pre-classified intent boundaries.

    Returns
    -------
    GoalGraph
        A validated task DAG ready for dispatch.
    """
    from langchain_core.messages import SystemMessage, HumanMessage

    start = time.time()
    goal_type = "CORRECTION" if is_correction else "NEW"
    p_tokens = None

    # ADR-101 Execution Shape Fast-Track:
    # CLASS_A (LOOKUP): Pure read-only requests execute via synthetic single-task graph (0s LLM planning).
    # CLASS_B / CLASS_C: Pass through muscle memory / dynamic goal graph planner.
    if intent_packet and not is_correction:
        exec_shape = getattr(intent_packet, "execution_shape", "CLASS_A")
        planning_req = getattr(intent_packet, "planning_required", False)
        actions = getattr(intent_packet, "allowed_actions", [])
        
        if exec_shape == "CLASS_A" and not planning_req:
            if not actions:
                print(f"[PLANNER NODE] ADR-101 Fast-Track: Routing Class A pure lookup via build_walk_graph (0s LLM planning)", flush=True)
                return build_walk_graph(query, goal_id=goal_id)
            elif len(actions) == 1:
                act_name = actions[0]
                print(f"[PLANNER NODE] ADR-101 Fast-Track: Routing Class A single action '{act_name}' via build_action_graph (0s LLM planning)", flush=True)
                return build_action_graph(query, {"action": act_name, "params": {}}, goal_id=goal_id)

    # Build the user prompt ------------------------------------------------
    from datetime import timedelta
    now_utc_dt = datetime.now(timezone.utc)
    now_ist_dt = now_utc_dt + timedelta(hours=5, minutes=30)
    now_utc = f"{now_utc_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC / {now_ist_dt.strftime('%Y-%m-%d %H:%M:%S')} IST (Indian Standard Time)"
    # History snippet: strip boilerplate and cap at 300 chars.
    # add_to_history already strips before writing; this is a belt-and-suspenders
    # guard so swarm report headers never reach the planner prompt even in edge cases.
    try:
        from .bot import _strip_history_boilerplate as _strip_h
    except ImportError:
        try:
            from bot import _strip_history_boilerplate as _strip_h
        except ImportError:
            _strip_h = None
    _ht = _strip_h(history_text) if (_strip_h and history_text) else (history_text or "")
    history_snippet = (_ht[:300] + "…") if len(_ht) > 300 else _ht

    user_content_parts: List[str] = [
        f"Current datetime: {now_utc}",
        f"User query: {query}",
    ]
    if intent_packet:
        # Dynamically include non-mutating helper departments in the display list
        # so the planner knows it can utilize them for research/synthesis tasks.
        prompt_depts = set(intent_packet.allowed_departments)
        prompt_depts.update({"information", "research", "analysis", "writing", "pa"})
        prompt_depts_list = sorted(list(prompt_depts))
        user_content_parts.append(
            f"Pre-classified Intent Boundaries:\n"
            f"- allowed_departments: {prompt_depts_list}\n"
            f"- allowed_actions: {intent_packet.allowed_actions}\n"
            f"- execution_mode: {intent_packet.execution_mode}\n"
            f"- confidence: {intent_packet.confidence}"
        )
    if is_correction:
        user_content_parts.append("Goal correction mode: ACTIVE")
    if last_goal_text:
        user_content_parts.append(f"Last planned goal to correct: {last_goal_text}")
    if history_snippet:
        user_content_parts.append(f"Recent history: {history_snippet}")
    if profile_text:
        user_content_parts.append(f"User profile: {profile_text}")
    if awareness_report:
        user_content_parts.append(
            "Situation Report (advisory, non-authoritative):\n"
            + json.dumps(awareness_report, ensure_ascii=False, sort_keys=True)
        )

    try:
        try:
            from .temporal_reasoner import synthesize_temporal_reasoning_packet
        except ImportError:
            from temporal_reasoner import synthesize_temporal_reasoning_packet
        temp_packet = synthesize_temporal_reasoning_packet(query)
        if temp_packet and temp_packet.get("temporal_context_str"):
            user_content_parts.append(f"\n[TEMPORAL REASONING & COMPLIANCE CONTEXT]\n{temp_packet['temporal_context_str']}")
    except Exception as e:
        print(f"[PLANNER TEMPORAL WARNING] Failed to inject temporal context: {e}", flush=True)

    user_content = "\n".join(user_content_parts)

    target_model = model_name

    # Call LLM (with automatic provider failover on rate limits) ------------
    try:
        try:
            from babu.bot import invoke_with_fallback
        except ImportError:
            from bot import invoke_with_fallback

        # Dynamic loading and injection of governance planning negative constraints
        try:
            from memory import get_anti_pattern_rules_for_domains
        except ImportError:
            from .memory import get_anti_pattern_rules_for_domains
            
        planning_domains = [
            "governance.planning", 
            "department.research", 
            "department.analysis", 
            "department.writing", 
            "department.execution", 
            "department.pa"
        ]
        gov_planning_rules = get_anti_pattern_rules_for_domains(planning_domains)
        system_prompt = PLANNER_SYSTEM_PROMPT
        
        # Phase 4 - Compile Planner (Load Institutional Brain Context)
        global COMPILED_BRAIN_CONTEXT
        if COMPILED_BRAIN_CONTEXT is None:
            compile_planner()
        system_prompt += COMPILED_BRAIN_CONTEXT

        if gov_planning_rules:
            system_prompt += f"\n\n[CRITICAL HISTORICAL GOVERNANCE RULES]\n{gov_planning_rules}"

        response = invoke_with_fallback(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_content)],
            model_name=target_model,
            temp=0.1,
        )
        raw_text: str = response.content  # type: ignore[union-attr]
        
        # Extract planning tokens dynamically
        p_tokens = {"prompt": 0, "completion": 0, "total": 0}
        if response:
            usage_meta = getattr(response, "usage_metadata", None)
            if usage_meta:
                p_tokens["prompt"] = usage_meta.get("input_tokens", 0) or usage_meta.get("prompt_tokens", 0) or 0
                p_tokens["completion"] = usage_meta.get("output_tokens", 0) or usage_meta.get("completion_tokens", 0) or 0
                p_tokens["total"] = usage_meta.get("total_tokens", 0) or (p_tokens["prompt"] + p_tokens["completion"])
            else:
                metadata = getattr(response, "response_metadata", {})
                token_usage = metadata.get("token_usage")
                if token_usage:
                    p_tokens["prompt"] = token_usage.get("prompt_tokens", 0)
                    p_tokens["completion"] = token_usage.get("completion_tokens", 0)
                    p_tokens["total"] = token_usage.get("total_tokens", 0)
    except Exception as exc:
        print(f"[PLANNER] LLM call failed: {exc}")
        exc_str = str(exc).lower()
        if any(k in exc_str for k in ("429", "rate limit", "rate_limit_exceeded", "too many requests", "tpd", "tpm")):
            p_status = "RATE_LIMIT"
        elif any(k in exc_str for k in ("timeout", "connection refused", "network", "socket", "dns", "unreachable")):
            p_status = "NETWORK"
        else:
            p_status = "PROVIDER_ERROR"
        if _has_gmail_lookup_intent(query, intent_packet):
            print("[PLANNER] Provider unavailable; using deterministic Gmail lookup graph.", flush=True)
            return _build_gmail_lookup_graph(
                query,
                goal_id=goal_id,
                goal_type=goal_type,
                intent_packet=intent_packet,
            )
        if _has_research_report_intent(query, intent_packet):
            print("[PLANNER] Provider unavailable; using deterministic research/report graph.", flush=True)
            return _build_research_report_graph(
                query,
                goal_id=goal_id,
                goal_type=goal_type,
                intent_packet=intent_packet,
            )
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status=p_status, intent_packet=intent_packet.to_dict() if intent_packet else None)

    # Parse JSON -----------------------------------------------------------
    try:
        data = _extract_json(raw_text)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[PLANNER] JSON parse error: {exc}")
        print(f"[PLANNER] Raw LLM output: {raw_text[:300]}")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="JSON_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    # Check for planner-reported constraint conflict
    if "conflict" in data and isinstance(data["conflict"], dict):
        reason = data["conflict"].get("reason", "Constraint conflict reported by planner.")
        print(f"[PLANNER] Strategic Planner returned constraint conflict: {reason}")
        try:
            from .bot import log_execution_ledger_event
        except ImportError:
            from bot import log_execution_ledger_event
        try:
            log_execution_ledger_event(
                session_id=session_id or "default",
                goal_id=goal_id or "G-PLAN",
                task_id=None,
                department=None,
                event_type="PLANNER_CONSTRAINT_VIOLATION",
                metadata={"reason": reason, "query": query, "source": "planner_reported"}
            )
        except Exception as log_err:
            pass
        return _build_fallback_graph(
            query,
            goal_id=goal_id,
            goal_type=goal_type,
            planner_status="CONSTRAINT_CONFLICT",
            intent_packet=intent_packet.to_dict() if intent_packet else None
        )

    # Validate & build TaskDTOs -------------------------------------------
    goal_text: str = data.get("goal", query[:200])
    raw_tasks: List[dict] = data.get("tasks", [])

    if not raw_tasks:
        print("[PLANNER] LLM returned empty task list — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    valid_dept_names = set(DEPARTMENTS.keys())
    tasks: List[TaskDTO] = []
    seen_ids: set = set()

    for t in raw_tasks:
        task_id: str = t.get("task_id", "")
        department: str = t.get("department", "")
        objective: str = t.get("objective", "")
        depends_on: List[str] = t.get("depends_on", [])
        priority: int = int(t.get("priority", len(tasks) + 1))
        compliance_checklist: List[str] = list(t.get("compliance_checklist", []))

        # Skip tasks with unknown departments
        if department not in valid_dept_names:
            print(f"[PLANNER] Skipping task {task_id}: unknown department '{department}'")
            continue

        # Filter depends_on to only reference known task_ids
        depends_on = [dep for dep in depends_on if dep in seen_ids]

        # Determine initial state
        state = TaskState.READY if not depends_on else TaskState.PENDING

        # Extract action, params, and grant_profile_access if present in planner JSON
        task_context = {}
        if "action" in t:
            act = t["action"]
            if act in ("send_facebook_post", "facebook_post"):
                act = "post_to_facebook"
            task_context["action"] = act
            task_context["params"] = t.get("params", {})
        if "protocol" in t:
            task_context["protocol"] = t["protocol"]
        task_context["grant_profile_access"] = bool(t.get("grant_profile_access", False))
        task_context["intent_packet"] = intent_packet.to_dict() if intent_packet else None
        task_context["parent_goal"] = query

        # Dynamic per-department token budget allocation
        dept_budgets = {
            "research": 4500,
            "writing": 3500,
            "analysis": 3000,
            "execution": 2000,
            "pa": 3000
        }
        token_budget = dept_budgets.get(department, 1500)

        tasks.append(
            TaskDTO(
                task_id=task_id,
                objective=objective,
                department=department,
                depends_on=depends_on,
                priority=priority,
                state=state,
                context=task_context,
                token_budget=token_budget,
                compliance_checklist=compliance_checklist,
            )
        )
        seen_ids.add(task_id)

    if not tasks:
        print("[PLANNER] All tasks filtered out — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR", intent_packet=intent_packet.to_dict() if intent_packet else None)

    # ── Post-processing: Enforce template constraints programmatically ──
    if intent_packet:
        intent_dict = intent_packet.to_dict()
        allowed_depts, allowed_actions = get_allowed_boundaries(intent_dict)
        
        for t in tasks:
            # 1. Verify permitted department
            if t.department not in allowed_depts:
                print(f"[PLANNER] Programmatic Intent Restriction: Task '{t.task_id}' uses unauthorized department '{t.department}' for intent boundaries. Triggering fallback.", flush=True)
                try:
                    from .bot import log_execution_ledger_event
                except ImportError:
                    from bot import log_execution_ledger_event
                try:
                    log_execution_ledger_event(
                        session_id=session_id or "default",
                        goal_id=goal_id or "G-PLAN",
                        task_id=None,
                        department=None,
                        event_type="PLANNER_CONSTRAINT_VIOLATION",
                        metadata={"reason": f"unauthorized department '{t.department}' in task '{t.task_id}'", "query": query, "source": "post_validation"}
                    )
                except Exception as log_err:
                    pass
                return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="CONSTRAINT_CONFLICT", intent_packet=intent_dict)
            
            # 2. Verify permitted actions for execution tasks
            if t.department == "execution":
                action = t.context.get("action")
                if action not in allowed_actions:
                    print(f"[PLANNER] Programmatic Intent Restriction: Task '{t.task_id}' uses unauthorized execution action '{action}' for intent boundaries. Triggering fallback.", flush=True)
                    try:
                        from .bot import log_execution_ledger_event
                    except ImportError:
                        from bot import log_execution_ledger_event
                    try:
                        log_execution_ledger_event(
                            session_id=session_id or "default",
                            goal_id=goal_id or "G-PLAN",
                            task_id=None,
                            department=None,
                            event_type="PLANNER_CONSTRAINT_VIOLATION",
                            metadata={"reason": f"unauthorized action '{action}' in task '{t.task_id}'", "query": query, "source": "post_validation"}
                        )
                    except Exception as log_err:
                        pass
                    return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="CONSTRAINT_CONFLICT", intent_packet=intent_dict)

    # ── Post-processing: Enforce content dependencies for execution tasks ──
    content_task_ids = [t.task_id for t in tasks if t.department in ("writing", "analysis", "research")]
    if content_task_ids:
        preferred_dep = None
        for dept in ("writing", "analysis", "research"):
            matching = [t.task_id for t in tasks if t.department == dept]
            if matching:
                preferred_dep = matching[-1]
                break
        if preferred_dep:
            for t in tasks:
                if t.department == "execution":
                    action = t.context.get("action")
                    if action not in ("send_email", "create_doc", "post_to_facebook"):
                        continue  # Only propagate writing/analysis content to actions that consume text drafts
                    if preferred_dep not in t.depends_on and t.task_id != preferred_dep:
                        print(f"[PLANNER] Post-processing: adding dependency {preferred_dep} to execution task {t.task_id} to ensure context propagation.", flush=True)
                        t.depends_on.append(preferred_dep)
                        # Re-evaluate state since dependencies have changed
                        t.state = TaskState.PENDING

    # Build GoalGraph ------------------------------------------------------
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()


    graph = GoalGraph(
        goal_id=goal_id,
        goal=goal_text,
        tasks=tasks,
        created_at=now_iso,
        goal_type=goal_type,
        planner_status="SUCCESS",
        intent_packet=intent_packet.to_dict() if intent_packet else None,
        planning_tokens=p_tokens,
    )

    # Validate DAG (no cycles) ---------------------------------------------
    try:
        validate_dag(graph.tasks)
    except Exception as exc:
        print(f"[PLANNER] DAG validation failed: {exc} — using fallback")
        return _build_fallback_graph(query, goal_id=goal_id, goal_type=goal_type, planner_status="VALIDATION_ERROR")

    duration = round(time.time() - start, 2)
    print(f"[PLANNER] Generated {len(tasks)} tasks in {duration}s")
    return graph


def build_walk_graph(query: str, goal_id: Optional[str] = None) -> GoalGraph:
    """Return a trivial single-task GoalGraph for WALK-gear queries.

    No LLM call is made.  The sole task instructs the PA department to
    respond directly to the user.

    Parameters
    ----------
    query : str
        The user's query text.
    goal_id : Optional[str]
        Optional pre-generated goal ID.

    Returns
    -------
    GoalGraph
    """
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=[
            TaskDTO(
                task_id="T1",
                objective="Respond directly to user query",
                department="pa",
                depends_on=[],
                priority=1,
                state=TaskState.READY,
                token_budget=3000,
            )
        ],
        created_at=now_iso,
    )


def build_action_graph(query: str, detected_action: dict, goal_id: Optional[str] = None) -> GoalGraph:
    """Build a 2-task GoalGraph for WALK queries with a detected action.

    No LLM call is made.  Task T1 executes the action via the *execution*
    department; task T2 reports the result back to the user via *pa*.

    Parameters
    ----------
    query : str
        The user's query text.
    detected_action : dict
        Must contain ``"action"`` (str) and ``"params"`` (dict) keys
        describing the action to perform.
    goal_id : Optional[str]
        Optional pre-generated goal ID.

    Returns
    -------
    GoalGraph
    """
    if not goal_id:
        goal_id = f"G-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    now_iso = datetime.now(timezone.utc).isoformat()

    action_name: str = detected_action.get("action", "unknown_action")
    params: Dict[str, Any] = detected_action.get("params", {})
    try:
        from .auditor import get_service_class
    except ImportError:
        from auditor import get_service_class
    is_class_a = (get_service_class(action_name) == "A")

    tasks = [
        TaskDTO(
            task_id="T1",
            objective=f"Execute action: {action_name}",
            department="execution",
            depends_on=[],
            priority=1,
            state=TaskState.READY,
            context={"action": action_name, "params": params, "approved": is_class_a},
            token_budget=2000,
        ),
        TaskDTO(
            task_id="T2",
            objective="Report action result to user",
            department="pa",
            depends_on=["T1"],
            priority=2,
            state=TaskState.PENDING,
            token_budget=3000,
        ),
    ]

    return GoalGraph(
        goal_id=goal_id,
        goal=query[:200],
        tasks=tasks,
        created_at=now_iso,
    )
