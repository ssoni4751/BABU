"""
system_index.py — System Information Index (SII) Layer

The SII is the authoritative runtime routing layer for all BABU system-related
queries. It parses System_Information_Index.md and provides:

  1. parse_sii()                  — Parses the SII markdown into structured registries.
  2. get_system_index_registries() — Cached accessor to the parsed registries.
  3. route_query()                — Returns {adrs, books, query_mode, matched_component}
                                    from a user query using:
                                    - Explicit ADR mentions  (ADR-004)
                                    - Layer Registry routing (L4, ETemp)
                                    - Component keyword routing (memory, governance)
                                    - Diagnostic domain routing (approval issues)
  4. derive_query_mode()          — Maps query verbs to SYSTEM_* query mode.

Routing determines evidence only. Reasoning behaviour is determined by query_mode.
"""

import os
import re
from typing import Optional, Dict, List, Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

RoutingResult = Dict[str, Any]  # {adrs, books, query_mode, matched_component, matched_layer}


# ---------------------------------------------------------------------------
# Query Mode derivation
# ---------------------------------------------------------------------------

# Verb → Query Mode mapping (order matters: more specific first)
_QUERY_MODE_RULES: List[tuple] = [
    # SYSTEM_DIAGNOSTIC: investigate failures / issues
    (["diagnose", "debug", "investigate", "troubleshoot", "why did", "root cause",
      "failure", "failed", "failing", "broken", "incident", "postmortem", "issue",
      "problem with", "not working"], "SYSTEM_DIAGNOSTIC"),

    # SYSTEM_AUDIT: compare implementation vs architecture
    (["audit", "verify", "compare", "compliance", "check if", "is it consistent",
      "does it match", "validate"], "SYSTEM_AUDIT"),

    # SYSTEM_DESIGN_REVIEW: evaluate proposed changes
    (["propose", "should we", "evaluate", "review this change", "tradeoff",
      "tradeoffs", "pros and cons", "recommend", "is it a good idea"], "SYSTEM_DESIGN_REVIEW"),

    # SYSTEM_ANALYSIS: deep structural analysis
    (["analyse", "analyze", "analysis", "explain how", "how does", "how do",
      "breakdown", "deep dive", "in depth", "strengths", "weaknesses"], "SYSTEM_ANALYSIS"),

    # SYSTEM_INFORMATION: default — explain / retrieve
    (["what is", "what are", "tell me about", "describe", "show me",
      "list", "explain", "who", "when", "where"], "SYSTEM_INFORMATION"),
]


def derive_query_mode(query: str) -> str:
    """
    Map a user query's verb/intent to a SYSTEM_* query mode.

    SYSTEM_INFORMATION    → Explain / retrieve a concept.
    SYSTEM_ANALYSIS       → Deep structural analysis with reasoning.
    SYSTEM_DIAGNOSTIC     → Investigate failures using ADRs, logs, telemetry.
    SYSTEM_AUDIT          → Compare implementation vs architecture.
    SYSTEM_DESIGN_REVIEW  → Evaluate proposed changes and tradeoffs.
    """
    q = query.lower()
    for verbs, mode in _QUERY_MODE_RULES:
        if any(v in q for v in verbs):
            return mode
    return "SYSTEM_INFORMATION"


# ---------------------------------------------------------------------------
# SII Parser
# ---------------------------------------------------------------------------

def parse_sii(filepath: str) -> Optional[dict]:
    """
    Parse System_Information_Index.md into structured registries.

    Returns a dict with keys:
      layers      — {layer_name: {adrs, book}}
      components  — {comp_name: {adrs, keywords}}
      documents   — {doc_name: {coverage:(lo,hi), domains}}
      diagnostics — {diag_name: {references, evidence}}
      adr_map     — {adr_num(int): book_filename} pre-computed coverage map
    """
    if not os.path.exists(filepath):
        print(f"[SII WARNING] System index file not found at: {filepath} — SII routing disabled, falling back to all collections.", flush=True)
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"[SII WARNING] Failed to read system index file: {e} — SII routing disabled.", flush=True)
        return None

    layers: dict = {}
    components: dict = {}
    documents: dict = {}
    diagnostics: dict = {}

    # Split on top-level headings (# but not ##)
    # We use a sentinel split to keep the first section
    sections = re.split(r'\n(?=# )', "\n" + content)

    for section in sections:
        lines = section.strip().splitlines()
        if not lines:
            continue
        header = lines[0].lstrip("#").strip()

        # ── Architecture Layer Registry ──────────────────────────────────
        if "Architecture Layer Registry" in header:
            current_layer = None
            mode = None
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("## "):
                    current_layer = line.lstrip("#").strip()
                    layers[current_layer] = {"adrs": [], "book": None}
                    mode = None
                elif re.match(r'^References?:', line, re.IGNORECASE):
                    mode = "adrs"
                elif re.match(r'^Books?:', line, re.IGNORECASE):
                    mode = "book"
                elif line and current_layer:
                    m = re.match(r"(ADR-\d+)", line, re.IGNORECASE)
                    if m and mode == "adrs":
                        layers[current_layer]["adrs"].append(m.group(1).upper())
                    elif mode == "book" and line.endswith(".md"):
                        # Store physical name (BABU_ prefix mapping applied later)
                        layers[current_layer]["book"] = line.strip()

        # ── Component Registry ───────────────────────────────────────────
        elif "Component Registry" in header:
            current_comp = None
            mode = None
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("## "):
                    current_comp = line.lstrip("#").strip()
                    components[current_comp] = {"adrs": [], "keywords": []}
                    mode = None
                elif re.match(r'^ADRs?:', line, re.IGNORECASE):
                    mode = "adrs"
                elif re.match(r'^Keywords?:', line, re.IGNORECASE):
                    mode = "keywords"
                elif line and current_comp:
                    if mode == "adrs":
                        m = re.match(r"(ADR-\d+)", line, re.IGNORECASE)
                        if m:
                            components[current_comp]["adrs"].append(m.group(1).upper())
                    elif mode == "keywords":
                        for kw in line.split(","):
                            kw = kw.strip().lower()
                            if kw:
                                components[current_comp]["keywords"].append(kw)

        # ── Document Registry ────────────────────────────────────────────
        elif "Document Registry" in header:
            current_doc = None
            mode = None
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("## "):
                    current_doc = line.lstrip("#").strip()
                    documents[current_doc] = {"coverage": None, "domains": []}
                    mode = None
                elif re.match(r'^Coverage:', line, re.IGNORECASE):
                    mode = "coverage"
                elif re.match(r'^Domains?:', line, re.IGNORECASE):
                    mode = "domains"
                elif line and current_doc:
                    if mode == "coverage":
                        nums = re.findall(r"ADR-(\d+)", line, re.IGNORECASE)
                        if len(nums) >= 2:
                            documents[current_doc]["coverage"] = (int(nums[0]), int(nums[1]))
                    elif mode == "domains":
                        for d in line.split(","):
                            d = d.strip()
                            if d:
                                documents[current_doc]["domains"].append(d)

        # ── Diagnostic Domains ───────────────────────────────────────────
        elif "Diagnostic Domains" in header:
            current_diag = None
            mode = None
            for line in lines[1:]:
                line = line.strip()
                if line.startswith("## "):
                    current_diag = line.lstrip("#").strip()
                    diagnostics[current_diag] = {"references": [], "evidence": []}
                    mode = None
                elif re.match(r'^References?:', line, re.IGNORECASE):
                    mode = "references"
                elif re.match(r'^Evidence:', line, re.IGNORECASE):
                    mode = "evidence"
                elif line and current_diag:
                    if mode == "references":
                        m = re.match(r"(ADR-\d+)", line, re.IGNORECASE)
                        if m:
                            diagnostics[current_diag]["references"].append(m.group(1).upper())
                    elif mode == "evidence":
                        item = line.lstrip("-").strip()
                        if item:
                            diagnostics[current_diag]["evidence"].append(item)

    # ── Pre-compute adr_map: {adr_num(int) → physical_book_filename} ────
    adr_map: dict[int, str] = {}
    for doc_name, doc_info in documents.items():
        cov = doc_info.get("coverage")
        if cov:
            physical = _to_physical_name(doc_name)
            for n in range(cov[0], cov[1] + 1):
                adr_map[n] = physical

    return {
        "layers": layers,
        "components": components,
        "documents": documents,
        "diagnostics": diagnostics,
        "adr_map": adr_map,
    }


# ---------------------------------------------------------------------------
# Name normalisation
# ---------------------------------------------------------------------------

def _to_physical_name(doc_name: str) -> str:
    """Map SII document names (ADR_Book_v1.md) → physical file names (BABU_ADR_Book_v1.md)."""
    if doc_name.startswith("ADR_Book_"):
        return "BABU_" + doc_name
    return doc_name


# ---------------------------------------------------------------------------
# Cached registry loader
# ---------------------------------------------------------------------------

_SII_CACHE: Optional[dict] = None


def get_system_index_registries(project_dir: Optional[str] = None) -> Optional[dict]:
    """
    Return (and cache at startup) the parsed SII registries.
    Cache structure:
      {layers, components, documents, diagnostics, adr_map}
    """
    global _SII_CACHE
    if _SII_CACHE is not None:
        return _SII_CACHE

    if not project_dir:
        # babu/system_index.py → parent is the project root
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_dir = os.path.dirname(current_dir)

    sii_path = os.path.join(project_dir, "System_Information_Index.md")
    _SII_CACHE = parse_sii(sii_path)
    if _SII_CACHE:
        print(
            f"[SII] Loaded System Information Index — "
            f"{len(_SII_CACHE['layers'])} layers, "
            f"{len(_SII_CACHE['components'])} components, "
            f"{len(_SII_CACHE['adr_map'])} ADRs mapped.",
            flush=True
        )
    return _SII_CACHE


def invalidate_cache() -> None:
    """Invalidate the SII cache (e.g., after the index file is updated)."""
    global _SII_CACHE
    _SII_CACHE = None


# ---------------------------------------------------------------------------
# Core routing
# ---------------------------------------------------------------------------

def route_query(query: str, registries: Optional[dict] = None) -> RoutingResult:
    """
    Route a user query to specific ADRs and books via the System Index Layer.

    Resolution order:
      1. Explicit ADR mentions         (ADR-004)
      2. Layer Registry                (L4, L2, ETemp, Planner)
      3. Component keyword matching    (memory, governance, auditor)
      4. Diagnostic domain matching    (approval issues, planner issues)
      5. Generic fallback              (all books when query is system-scoped)

    Returns:
      {
        "adrs":              ["ADR-004", "ADR-005"],       # resolved ADR IDs
        "books":             ["BABU_ADR_Book_v1.md"],      # physical book files
        "query_mode":        "SYSTEM_INFORMATION",         # behaviour hint
        "matched_component": "Memory",                     # first matched component
        "matched_layer":     "L2 Memory",                  # first matched layer
      }
    """
    if registries is None:
        registries = get_system_index_registries()

    result: RoutingResult = {
        "adrs": [],
        "books": [],
        "query_mode": derive_query_mode(query),
        "matched_component": None,
        "matched_layer": None,
    }

    if not registries:
        return result

    q = query.lower()
    adr_map: dict[int, str] = registries.get("adr_map", {})
    matched_adr_ids: set[str] = set()   # e.g. "ADR-004"
    matched_books: set[str] = set()

    # ── 1. Explicit ADR number mentions ─────────────────────────────────
    for m in re.finditer(r"ADR-(\d+)", query, re.IGNORECASE):
        num = int(m.group(1))
        matched_adr_ids.add(f"ADR-{num:03d}" if num < 100 else f"ADR-{num}")
        book = adr_map.get(num)
        if book:
            matched_books.add(book)

    # ── 2. Layer Registry matching ───────────────────────────────────────
    #    Matches "L4", "L2 Memory", "ETemp", "Planner" etc.
    for layer_name, layer_info in registries.get("layers", {}).items():
        # Build match tokens: "L4 ETemp" → ["l4", "etemp"]
        layer_tokens = [t.lower() for t in re.split(r'[\s]+', layer_name) if t]
        # Also match the numeric ID alone: "L4" matches "l4"
        if any(t in q for t in layer_tokens):
            for adr_str in layer_info.get("adrs", []):
                matched_adr_ids.add(adr_str.upper())
                m2 = re.search(r"ADR-(\d+)", adr_str, re.IGNORECASE)
                if m2:
                    book = adr_map.get(int(m2.group(1)))
                    if book:
                        matched_books.add(book)
            if result["matched_layer"] is None:
                result["matched_layer"] = layer_name
            # Also try the registered book for that layer
            layer_book = layer_info.get("book")
            if layer_book:
                matched_books.add(_to_physical_name(layer_book))

    # ── 3. Component keyword matching ────────────────────────────────────
    for comp_name, comp_info in registries.get("components", {}).items():
        hit = comp_name.lower() in q or any(kw in q for kw in comp_info.get("keywords", []))
        if hit:
            for adr_str in comp_info.get("adrs", []):
                matched_adr_ids.add(adr_str.upper())
                m3 = re.search(r"ADR-(\d+)", adr_str, re.IGNORECASE)
                if m3:
                    book = adr_map.get(int(m3.group(1)))
                    if book:
                        matched_books.add(book)
            if result["matched_component"] is None:
                result["matched_component"] = comp_name

    # ── 4. Diagnostic domain matching ────────────────────────────────────
    for diag_name, diag_info in registries.get("diagnostics", {}).items():
        diag_words = [w for w in re.split(r'\W+', diag_name.lower()) if len(w) > 3]
        if any(w in q for w in diag_words):
            for adr_str in diag_info.get("references", []):
                matched_adr_ids.add(adr_str.upper())
                m4 = re.search(r"ADR-(\d+)", adr_str, re.IGNORECASE)
                if m4:
                    book = adr_map.get(int(m4.group(1)))
                    if book:
                        matched_books.add(book)

    # ── 5. Fallback: generic system/architecture queries → all books ─────
    if not matched_books:
        system_kws = [
            "architecture", "codebase", "how do you work", "how does babu work",
            "design", "blueprint", "system", "adr", "doc",
        ]
        if any(kw in q for kw in system_kws):
            for doc_name in registries.get("documents", {}).keys():
                matched_books.add(_to_physical_name(doc_name))

    # Populate result
    result["adrs"] = sorted(matched_adr_ids)
    result["books"] = sorted(matched_books)
    return result


# ---------------------------------------------------------------------------
# Backward-compatible helper (used in existing bot.py call sites)
# ---------------------------------------------------------------------------

def route_query_to_books(query: str, registries: Optional[dict] = None) -> list[str]:
    """Thin wrapper returning only the books list from route_query()."""
    return route_query(query, registries).get("books", [])
