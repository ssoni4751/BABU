import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

# Ensure babu is in python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.rag_storage import init_rag_db, retrieve_knowledge, get_embeddings_model, MockEmbeddings
from babu.rag_ingestion import run_full_ingestion
from babu.services import get_db_connection, log_execution_ledger_event
from babu.gateway import is_system_aware_query, requires_web_search
from babu.bot import get_telemetry_data

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
            cursor.execute("DELETE FROM babu_knowledge;")
            cursor.execute("DELETE FROM execution_ledger;")
            cursor.execute("DELETE FROM babu_temporal_timeline;")
            cursor.execute("DELETE FROM system_memory;")
            cursor.execute("DELETE FROM trusted_templates;")
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
            cursor.execute("DELETE FROM babu_knowledge;")
            cursor.execute("DELETE FROM execution_ledger;")
            cursor.execute("DELETE FROM babu_temporal_timeline;")
            cursor.execute("DELETE FROM system_memory;")
            cursor.execute("DELETE FROM trusted_templates;")
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
        vec_template = embed.embed_query("tell me about babu templates and etemp")
        vec_governance = embed.embed_query("tell me about bipartite auditor governance")
        
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("templates", "test_source", "etemp", "babu uses etemp templates to define agent parameters.", json.dumps(vec_template), "{}")
        )
        cursor.execute(
            "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
            ("governance", "test_source", "bipartite auditor", "the bipartite auditor enforces PreExecutionGatekeeper and post-execution audit.", json.dumps(vec_governance), "{}")
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        # Retrieve templates query
        results = retrieve_knowledge("tell me about babu templates and etemp", top_k=5)
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

    def test_05_postgres_branch_mocked(self):
        """Mock is_pg as True to test the SQL queries in the Postgres code branch."""
        conn_mock = MagicMock()
        cursor_mock = MagicMock()
        conn_mock.cursor.return_value = cursor_mock
        
        # Setup mock fetchall returning a valid tuple of length 7
        cursor_mock.fetchall.return_value = [
            (1, "templates", "test_source", "etemp", "babu uses etemp templates.", json.dumps({"key": "val"}), 0.85)
        ]
        
        with patch("babu.bot.get_db_connection") as mock_conn:
            mock_conn.return_value = (conn_mock, True)
            
            # 1. Test retrieve_knowledge with collections
            results = retrieve_knowledge("tell me about babu templates", collections=["templates"], top_k=3)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["title"], "etemp")
            
            # Check cursor execution parameters
            cursor_mock.execute.assert_called()
            
            # 2. Test retrieve_knowledge without collections
            results_all = retrieve_knowledge("tell me about babu templates", top_k=3)
            self.assertEqual(len(results_all), 1)

    def test_06_retrieve_system_memory_via_sql(self):
        """Test retrieve_system_memory_via_sql retrieves profile, goals, failures, timeline, rules, templates."""
        from babu.services import retrieve_system_memory_via_sql
        
        # Insert test records
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            # Log a goal received event
            cursor.execute(
                "INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-session", "G-TEST-GOAL", None, None, "GOAL_RECEIVED", None, None, json.dumps({"query": "my test goal"}))
            )
            
            # Log a failure event
            cursor.execute(
                "INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("test-session", "G-TEST-GOAL", "T1", "research", "PLANNER_CONSTRAINT_VIOLATION", None, None, json.dumps({"error": "unallowed dept"}))
            )
            
            # Log a temporal timeline event
            cursor.execute(
                "INSERT INTO babu_temporal_timeline (event_category, summary, outcome, cause, effect, resolution, confidence) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("TASK_COMPLETED", "Completed T1", "SUCCESS", "Finished", "Downstream ready", "None", 0.95)
            )
            
            # Log system memory anti-pattern rule
            cursor.execute(
                "INSERT INTO system_memory (key, data) VALUES (?, ?)",
                ("anti_pattern_test", "do not repeat queries")
            )
            
            # Log trusted template
            cursor.execute(
                "INSERT INTO trusted_templates (template_id, template_signature, status, execution_count, success_count) VALUES (?, ?, ?, ?, ?)",
                ("temp_123", "lookup_sig", "PROMOTED", 5, 5)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
        
        # Verify retrieve_system_memory_via_sql returns goals
        res_goals = retrieve_system_memory_via_sql("what are my recent goals?")
        self.assertIn("G-TEST-GOAL", res_goals)
        
        # Verify retrieve_system_memory_via_sql returns failures
        res_fails = retrieve_system_memory_via_sql("why did the system fail?")
        self.assertIn("PLANNER_CONSTRAINT_VIOLATION", res_fails)
        
        # Verify retrieve_system_memory_via_sql returns timeline
        res_timeline = retrieve_system_memory_via_sql("what is the temporal timeline?")
        self.assertIn("Completed T1", res_timeline)
        
        # Verify retrieve_system_memory_via_sql returns rules
        res_rules = retrieve_system_memory_via_sql("tell me the anti-pattern rules")
        self.assertIn("do not repeat queries", res_rules)
        
        # Verify retrieve_system_memory_via_sql returns templates
        res_temps = retrieve_system_memory_via_sql("what are the trusted templates?")
        self.assertIn("temp_123", res_temps)

    def test_07_rag_text_search_fallback(self):
        """Test RAG text search fallback when vector similarity yields no matches."""
        # Insert a chunk manually
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO babu_knowledge (collection, source, title, chunk_text, embedding, metadata) VALUES (?, ?, ?, ?, ?, ?)",
                ("babu_docs", "test_source", "codebase doc", "this document explains the backend architecture of the babu scheduler.", "[]", "{}")
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
        
        # We query for 'backend architecture' using retrieve_knowledge.
        # Since we put embedding '[]', its cosine similarity with the query's MockEmbeddings will be 0.0 or fail.
        # The text search fallback should match 'architecture' in the chunk_text and return it!
        results = retrieve_knowledge("tell me about the backend architecture", collections=["babu_docs"], top_k=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "codebase doc")
        self.assertIn("backend architecture", results[0]["chunk_text"])

    def test_08_auditor_risk_assessor_override(self):
        """Test that for research/information departments, post-execution auditor failure is overridden."""
        from babu.auditor import PostExecutionValidator
        from babu.task_engine import TaskDTO, TaskState
        
        # Construct research task
        task = TaskDTO(
            task_id="T2",
            objective="research competitors",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.RUNNING
        )
        
        # Mock LLM to return passed: false
        mock_llm = MagicMock()
        mock_res = MagicMock()
        mock_res.content = json.dumps({
            "passed": False,
            "confidence": 0.4,
            "uncertainty_flag": True,
            "risk_assessment": "low citation density",
            "reason": "Missing secondary sources link"
        })
        mock_llm.invoke.return_value = mock_res
        
        validator = PostExecutionValidator(llm=mock_llm)
        passed, result = validator.audit(task, "Found 3 competitors.")
        
        # Verify passed is True (failure overridden under Priority Directive!)
        self.assertTrue(passed)
        self.assertEqual(result, "Found 3 competitors.")
        self.assertTrue(task.context["audit_metrics"]["uncertainty_flag"])
        self.assertTrue(task.context["audit_metrics"]["override_applied"])
        self.assertEqual(task.context["audit_metrics"]["confidence"], 0.4)
