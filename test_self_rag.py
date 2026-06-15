import os
import sys
import json
import unittest
from unittest.mock import patch

# Ensure aria is in python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

from aria.rag_storage import init_rag_db, retrieve_knowledge, get_embeddings_model, MockEmbeddings
from aria.rag_ingestion import run_full_ingestion
from aria.bot import get_db_connection, get_telemetry_data, is_system_aware_query, requires_web_search, log_execution_ledger_event

class TestSelfRAG(unittest.TestCase):

    def setUp(self):
        # We ensure SQLite db is used for test
        os.environ["DATABASE_URL"] = "sqlite:///test_rag.db"
        # Initialize the database and schemas
        init_rag_db()
        
        # Clear database tables to ensure clean slate
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM aria_knowledge;")
            cursor.execute("DELETE FROM execution_ledger;")
            conn.commit()
        except Exception:
            pass
        finally:
            cursor.close()
            conn.close()

    def tearDown(self):
        # Clean up database tables and remove test database file if it exists
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM aria_knowledge;")
            cursor.execute("DELETE FROM execution_ledger;")
            conn.commit()
        except Exception:
            pass
        finally:
            cursor.close()
            conn.close()
        
        if os.path.exists("test_rag.db"):
            try:
                os.remove("test_rag.db")
            except Exception:
                pass

    def test_01_embedding_factory_and_storage(self):
        """Test that MockEmbeddings returns a deterministic unit vector and init_rag_db initializes tables."""
        model = get_embeddings_model()
        self.assertIsNotNone(model)
        
        # Test mock embedding normalized unit vector
        vector = model.embed_query("test query")
        self.assertEqual(len(vector), 768)
        norm = sum(x*x for x in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)
        
        # Test deterministic output
        vector2 = model.embed_query("test query")
        self.assertEqual(vector, vector2)

    def test_02_ingestion_and_retrieval(self):
        """Test that ingestion populates the DB and retrieval returns relevant matches with limits."""
        # Insert test chunks manually
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        # Get mock embeddings
        embed = MockEmbeddings()
        vec_template = embed.embed_query("tell me about aria templates and etemp")
        vec_governance = embed.embed_query("tell me about bipartite auditor governance")
        
        cursor.execute(
            "INSERT INTO aria_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("templates", "test_source", "etemp", "aria uses etemp templates to define agent parameters.", json.dumps(vec_template), "{}")
        )
        cursor.execute(
            "INSERT INTO aria_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("governance", "test_source", "bipartite auditor", "the bipartite auditor enforces PreExecutionGatekeeper and post-execution audit.", json.dumps(vec_governance), "{}")
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        # Retrieve templates query
        results = retrieve_knowledge("tell me about aria templates and etemp", top_k=5)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["title"], "etemp")
        self.assertIn("etemp templates", results[0]["chunk_text"])
        
        # Retrieve governance query
        results_gov = retrieve_knowledge("tell me about bipartite auditor governance", top_k=5)
        self.assertGreater(len(results_gov), 0)
        self.assertEqual(results_gov[0]["title"], "bipartite auditor")

    def test_03_query_routing_and_web_search_bypass(self):
        """Test that system aware queries are recognized and bypass web search requirement."""
        # System queries
        self.assertTrue(is_system_aware_query("Explain etemp parameters"))
        self.assertTrue(is_system_aware_query("Show me the bipartite auditor rules"))
        
        # Non-system query
        self.assertFalse(is_system_aware_query("What is the capital of France?"))
        
        # Web search bypass check
        self.assertFalse(requires_web_search("Explain how etemp works"))

    def test_04_telemetry_aggregation(self):
        """Test that RAG_RETRIEVAL events are logged and aggregated inside get_telemetry_data."""
        log_execution_ledger_event(
            session_id="test-session",
            goal_id="G-TEST-1",
            task_id=None,
            department=None,
            event_type="RAG_RETRIEVAL",
            state_before=None,
            state_after=None,
            metadata={
                "query": "test query",
                "retrieval_requests": 1,
                "retrieval_hits": 1,
                "retrieval_misses": 0,
                "retrieval_latency_ms": 15.5,
                "retrieved_tokens": 350,
                "collections_accessed": ["templates"]
            }
        )
        
        log_execution_ledger_event(
            session_id="test-session",
            goal_id="G-TEST-1",
            task_id=None,
            department=None,
            event_type="RAG_RETRIEVAL",
            state_before=None,
            state_after=None,
            metadata={
                "query": "test query 2",
                "retrieval_requests": 1,
                "retrieval_hits": 0,
                "retrieval_misses": 1,
                "retrieval_latency_ms": 5.0,
                "retrieved_tokens": 0,
                "collections_accessed": []
            }
        )
        
        # Compute telemetry
        telemetry = get_telemetry_data(limit=10)
        self.assertEqual(telemetry["retrieval_requests"], 2)
        self.assertEqual(telemetry["retrieval_hits"], 1)
        self.assertEqual(telemetry["retrieval_misses"], 1)
        self.assertAlmostEqual(telemetry["retrieval_latency_ms"], 10.25, places=1)
        self.assertEqual(telemetry["retrieved_tokens"], 350)
