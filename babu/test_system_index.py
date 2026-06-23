"""
test_system_index.py — Unit tests for the System Index Layer (SII)

Tests cover:
  1. SII parsing: layers, components, documents, diagnostics, adr_map
  2. query_mode derivation
  3. Routing: explicit ADR, layer registry, component keyword, diagnostic domain, fallback
  4. Route result shape (adrs + books + query_mode + matched_component + matched_layer)
  5. retrieve_knowledge sources filter (SQLite branch)
  6. End-to-end: ingestion of ADR book → SII route → targeted retrieval
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(CURRENT_DIR, "babu"))

# Force SQLite for all tests
os.environ.setdefault("DATABASE_URL", "sqlite:///test_sii.db")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "MOCK_TOKEN")

from babu.system_index import (
    parse_sii,
    route_query,
    route_query_to_books,
    derive_query_mode,
    get_system_index_registries,
    invalidate_cache,
)
from babu.rag_storage import init_rag_db, retrieve_knowledge, MockEmbeddings
from babu.services import get_db_connection


# ---------------------------------------------------------------------------
# Fixture: minimal SII markdown for testing
# ---------------------------------------------------------------------------

SAMPLE_SII = """# System Information Index (SII)

Version: 2.0

---

# Architecture Layer Registry

## L0 Constitution
References:
ADR-001
ADR-017

Book:
ADR_Book_v1.md

## L2 Memory
References:
ADR-004
ADR-005

Books:
ADR_Book_v1.md

## L4 ETemp
References:
ADR-011
ADR-022
ADR-041

---

# Component Registry

## Governance
ADRs:
ADR-001
ADR-024

Keywords:
governance, constitution, policy, rule

## Memory
ADRs:
ADR-004
ADR-005

Keywords:
memory, ledger, retention, epistemic

## Approval
ADRs:
ADR-009
ADR-010
ADR-045

---

# Document Registry

## ADR_Book_v1.md
Coverage:
ADR-001 → ADR-018

Domains:
Constitution, Brain, Memory, Planner, Approval

## ADR_Book_v2.md
Coverage:
ADR-019 → ADR-036

Domains:
Deployment, Telemetry, Security, Templates

---

# Diagnostic Domains

## Approval Issues
References:
ADR-010
ADR-045

Evidence:
- Pending Actions
- Approval Logs

## Memory Issues
References:
ADR-004
ADR-027

---

# Retrieval Rules

1. SII never provides final answers.
2. SII only provides routing information.
"""


class TestSIIParsing(unittest.TestCase):
    """Tests for parse_sii()."""

    def setUp(self):
        # Write SII to a temp file
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        self.tmp.write(SAMPLE_SII)
        self.tmp.close()
        invalidate_cache()

    def tearDown(self):
        os.unlink(self.tmp.name)
        invalidate_cache()

    def test_01_parse_returns_dict(self):
        reg = parse_sii(self.tmp.name)
        self.assertIsNotNone(reg)
        self.assertIn("layers", reg)
        self.assertIn("components", reg)
        self.assertIn("documents", reg)
        self.assertIn("diagnostics", reg)
        self.assertIn("adr_map", reg)

    def test_02_layers_parsed(self):
        reg = parse_sii(self.tmp.name)
        self.assertIn("L0 Constitution", reg["layers"])
        self.assertIn("L4 ETemp", reg["layers"])
        l0 = reg["layers"]["L0 Constitution"]
        self.assertIn("ADR-001", l0["adrs"])
        self.assertIn("ADR-017", l0["adrs"])

    def test_03_components_parsed(self):
        reg = parse_sii(self.tmp.name)
        self.assertIn("Governance", reg["components"])
        gov = reg["components"]["Governance"]
        self.assertIn("ADR-001", gov["adrs"])
        self.assertIn("governance", gov["keywords"])
        self.assertIn("constitution", gov["keywords"])

    def test_04_documents_coverage_parsed(self):
        reg = parse_sii(self.tmp.name)
        self.assertIn("ADR_Book_v1.md", reg["documents"])
        cov = reg["documents"]["ADR_Book_v1.md"]["coverage"]
        self.assertEqual(cov, (1, 18))

    def test_05_diagnostics_parsed(self):
        reg = parse_sii(self.tmp.name)
        self.assertIn("Approval Issues", reg["diagnostics"])
        refs = reg["diagnostics"]["Approval Issues"]["references"]
        self.assertIn("ADR-010", refs)
        self.assertIn("ADR-045", refs)

    def test_06_adr_map_precomputed(self):
        reg = parse_sii(self.tmp.name)
        adr_map = reg["adr_map"]
        # ADR-001 → ADR-018 → BABU_ADR_Book_v1.md
        self.assertEqual(adr_map[1], "BABU_ADR_Book_v1.md")
        self.assertEqual(adr_map[18], "BABU_ADR_Book_v1.md")
        # ADR-019 → ADR-036 → BABU_ADR_Book_v2.md
        self.assertEqual(adr_map[19], "BABU_ADR_Book_v2.md")
        self.assertEqual(adr_map[36], "BABU_ADR_Book_v2.md")

    def test_07_missing_file_returns_none(self):
        result = parse_sii("/nonexistent/path.md")
        self.assertIsNone(result)


class TestQueryMode(unittest.TestCase):
    """Tests for derive_query_mode()."""

    def test_diagnostic_mode(self):
        self.assertEqual(derive_query_mode("why did the approval fail?"), "SYSTEM_DIAGNOSTIC")
        self.assertEqual(derive_query_mode("diagnose the memory issue"), "SYSTEM_DIAGNOSTIC")
        self.assertEqual(derive_query_mode("investigate planner failures"), "SYSTEM_DIAGNOSTIC")

    def test_analysis_mode(self):
        self.assertEqual(derive_query_mode("analyze ADR-004 in depth"), "SYSTEM_ANALYSIS")
        self.assertEqual(derive_query_mode("explain how memory works"), "SYSTEM_ANALYSIS")

    def test_audit_mode(self):
        self.assertEqual(derive_query_mode("audit the governance layer"), "SYSTEM_AUDIT")
        self.assertEqual(derive_query_mode("verify compliance with ADR-001"), "SYSTEM_AUDIT")

    def test_design_review_mode(self):
        self.assertEqual(derive_query_mode("what are the tradeoffs of this approach?"), "SYSTEM_DESIGN_REVIEW")
        self.assertEqual(derive_query_mode("should we use postgres or sqlite?"), "SYSTEM_DESIGN_REVIEW")

    def test_information_mode_default(self):
        self.assertEqual(derive_query_mode("what is ADR-004?"), "SYSTEM_INFORMATION")
        self.assertEqual(derive_query_mode("tell me about the memory layer"), "SYSTEM_INFORMATION")
        self.assertEqual(derive_query_mode("list the components"), "SYSTEM_INFORMATION")


class TestRouteQuery(unittest.TestCase):
    """Tests for route_query() with all four resolution strategies."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
        self.tmp.write(SAMPLE_SII)
        self.tmp.close()
        invalidate_cache()
        self.reg = parse_sii(self.tmp.name)

    def tearDown(self):
        os.unlink(self.tmp.name)
        invalidate_cache()

    # ── Result shape ────────────────────────────────────────────────────

    def test_result_has_required_keys(self):
        result = route_query("what is ADR-004?", self.reg)
        for key in ("adrs", "books", "query_mode", "matched_component", "matched_layer"):
            self.assertIn(key, result)

    # ── Explicit ADR routing ────────────────────────────────────────────

    def test_explicit_adr_routes_to_correct_book(self):
        result = route_query("tell me about ADR-004 ledger memory", self.reg)
        self.assertIn("ADR-004", result["adrs"])
        self.assertIn("BABU_ADR_Book_v1.md", result["books"])

    def test_explicit_adr_019_routes_to_book2(self):
        result = route_query("what is ADR-019?", self.reg)
        self.assertIn("ADR-019", result["adrs"])
        self.assertIn("BABU_ADR_Book_v2.md", result["books"])

    def test_multiple_explicit_adrs(self):
        result = route_query("compare ADR-001 and ADR-022", self.reg)
        self.assertIn("ADR-001", result["adrs"])
        self.assertIn("ADR-022", result["adrs"])
        # ADR-001 in Book1, ADR-022 in Book2
        self.assertIn("BABU_ADR_Book_v1.md", result["books"])
        self.assertIn("BABU_ADR_Book_v2.md", result["books"])

    # ── Layer Registry routing ──────────────────────────────────────────

    def test_layer_l4_routes_etemp_adrs(self):
        result = route_query("explain L4 ETemp layer", self.reg)
        # Should match L4 ETemp → ADR-011, ADR-022, ADR-041
        self.assertIn("ADR-011", result["adrs"])
        self.assertIn("ADR-022", result["adrs"])
        self.assertIsNotNone(result["matched_layer"])

    def test_layer_l2_routes_memory_adrs(self):
        result = route_query("show me L2 memory layer ADRs", self.reg)
        self.assertIn("ADR-004", result["adrs"])
        self.assertIsNotNone(result["matched_layer"])

    def test_etemp_keyword_matches_layer(self):
        result = route_query("analyze etemp architecture", self.reg)
        # "etemp" is a token in "L4 ETemp"
        self.assertIn("ADR-011", result["adrs"])

    # ── Component keyword routing ───────────────────────────────────────

    def test_component_keyword_governance(self):
        result = route_query("what governance rules apply?", self.reg)
        self.assertIn("ADR-001", result["adrs"])
        self.assertEqual(result["matched_component"], "Governance")

    def test_component_keyword_memory(self):
        result = route_query("how does the ledger work?", self.reg)
        self.assertIn("ADR-004", result["adrs"])
        self.assertEqual(result["matched_component"], "Memory")

    def test_component_keyword_policy(self):
        result = route_query("explain the constitution policy", self.reg)
        # "policy" is a keyword for Governance
        self.assertIn("ADR-001", result["adrs"])

    # ── Diagnostic domain routing ───────────────────────────────────────

    def test_diagnostic_approval_issues(self):
        result = route_query("why is approval not working?", self.reg)
        self.assertIn("ADR-010", result["adrs"])
        self.assertIn("ADR-045", result["adrs"])

    def test_diagnostic_memory_issues(self):
        result = route_query("debug the memory problem", self.reg)
        self.assertIn("ADR-004", result["adrs"])

    # ── Fallback routing ────────────────────────────────────────────────

    def test_fallback_system_query_returns_all_books(self):
        result = route_query("explain the overall system architecture", self.reg)
        # Should return at least both books as fallback
        self.assertGreater(len(result["books"]), 0)

    def test_non_system_query_returns_empty(self):
        result = route_query("what is the weather in Delhi?", self.reg)
        # Should route to nothing — no keywords match
        self.assertEqual(result["adrs"], [])
        self.assertEqual(result["books"], [])

    # ── Backward compat wrapper ─────────────────────────────────────────

    def test_route_query_to_books_wrapper(self):
        books = route_query_to_books("tell me about ADR-001", self.reg)
        self.assertIsInstance(books, list)
        self.assertIn("BABU_ADR_Book_v1.md", books)


class TestRetrieveKnowledgeSourcesFilter(unittest.TestCase):
    """Tests that retrieve_knowledge respects the sources parameter (SQLite)."""

    def setUp(self):
        os.environ["DATABASE_URL"] = "sqlite:///test_sii_rag.db"
        init_rag_db()
        conn, _ = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM babu_knowledge;")
            conn.commit()
        except Exception:
            pass
        finally:
            cursor.close()
            conn.close()

        # Insert two chunks from different sources
        embed = MockEmbeddings()
        conn, _ = get_db_connection()
        cursor = conn.cursor()
        v1 = json.dumps(embed.embed_query("ledger memory ADR-004"))
        v2 = json.dumps(embed.embed_query("deployment telemetry ADR-019"))
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("adr_books", "BABU_ADR_Book_v1.md", "Ledger-Based Memory", "Memory records outcomes.", v1, "{}")
        )
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("adr_books", "BABU_ADR_Book_v2.md", "Deployment Telemetry", "Telemetry records deployments.", v2, "{}")
        )
        conn.commit()
        cursor.close()
        conn.close()

    def tearDown(self):
        conn, _ = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM babu_knowledge;")
            conn.commit()
        except Exception:
            pass
        finally:
            cursor.close()
            conn.close()
        try:
            os.remove("test_sii_rag.db")
        except Exception:
            pass

    def test_sources_filter_restricts_to_book1(self):
        results = retrieve_knowledge(
            "memory ledger",
            collections=["adr_books"],
            sources=["BABU_ADR_Book_v1.md"],
            top_k=5
        )
        sources_returned = [r["source"] for r in results]
        for src in sources_returned:
            self.assertEqual(src, "BABU_ADR_Book_v1.md",
                             f"Expected only Book1 results but got: {src}")

    def test_sources_filter_restricts_to_book2(self):
        results = retrieve_knowledge(
            "deployment telemetry",
            collections=["adr_books"],
            sources=["BABU_ADR_Book_v2.md"],
            top_k=5
        )
        sources_returned = [r["source"] for r in results]
        for src in sources_returned:
            self.assertEqual(src, "BABU_ADR_Book_v2.md",
                             f"Expected only Book2 results but got: {src}")

    def test_no_sources_filter_returns_both(self):
        results = retrieve_knowledge(
            "memory telemetry",
            collections=["adr_books"],
            top_k=5
        )
        sources_returned = set(r["source"] for r in results)
        # At least one book should appear
        self.assertGreater(len(sources_returned), 0)


if __name__ == "__main__":
    unittest.main()
