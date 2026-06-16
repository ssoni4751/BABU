"""
test_orchestration.py — Comprehensive Test Suite for ARIA's DAG Orchestration Framework
"""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from aria.task_engine import TaskDTO, GoalGraph, TaskState, TaskEngine, validate_dag
from aria.planner import build_walk_graph, build_action_graph
from aria.departments import get_department_head, DepartmentHead

class TestTaskEngine(unittest.TestCase):
    
    def test_state_transitions(self):
        # 1. Trivial single-task graph
        t1 = TaskDTO(
            task_id="T1",
            objective="Retrieve latest news about space exploration",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.PENDING
        )
        
        goal = GoalGraph(
            goal_id="G1",
            goal="Space exploration analysis",
            tasks=[t1]
        )
        
        engine = TaskEngine(goal)
        
        # Verify that task without dependencies is promoted to READY on initialization
        ready = engine.get_ready_tasks()
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].task_id, "T1")
        self.assertEqual(ready[0].state, TaskState.READY)
        
        # Transition to RUNNING
        engine.mark_running("T1")
        self.assertEqual(t1.state, TaskState.RUNNING)
        
        # Transition to COMPLETED
        engine.mark_completed("T1", "NASA launched Artemis II successfully.")
        self.assertEqual(t1.state, TaskState.COMPLETED)
        self.assertEqual(t1.result, "NASA launched Artemis II successfully.")
        self.assertTrue(engine.is_goal_complete())

    def test_dependency_propagation(self):
        # T1 (no deps) -> T2 (depends on T1)
        t1 = TaskDTO(task_id="T1", objective="Research data", department="research", depends_on=[], priority=1)
        t2 = TaskDTO(task_id="T2", objective="Analyze data", department="analysis", depends_on=["T1"], priority=2)
        
        goal = GoalGraph(
            goal_id="G2",
            goal="Chained task test",
            tasks=[t1, t2]
        )
        
        engine = TaskEngine(goal)
        
        # T1 should be READY, T2 should be PENDING
        self.assertEqual(t1.state, TaskState.READY)
        self.assertEqual(t2.state, TaskState.PENDING)
        
        # Mark T1 as running and completed
        engine.mark_running("T1")
        engine.mark_completed("T1", "Research results.")
        
        # T1 should be COMPLETED, T2 should be READY
        self.assertEqual(t1.state, TaskState.COMPLETED)
        self.assertEqual(t2.state, TaskState.READY)
        
        # Mark T2 completed
        engine.mark_running("T2")
        engine.mark_completed("T2", "Analysis report.")
        
        self.assertTrue(engine.is_goal_complete())

    def test_cycle_detection(self):
        # T1 depends on T2, T2 depends on T1 (Cycle)
        t1 = TaskDTO(task_id="T1", objective="Task 1", department="research", depends_on=["T2"], priority=1)
        t2 = TaskDTO(task_id="T2", objective="Task 2", department="analysis", depends_on=["T1"], priority=2)
        
        with self.assertRaises(ValueError) as context:
            validate_dag([t1, t2])
            
        self.assertIn("Cycle detected", str(context.exception))

    def test_cascading_blocker(self):
        # T1 -> T2
        t1 = TaskDTO(task_id="T1", objective="Task 1", department="research", depends_on=[], priority=1)
        t2 = TaskDTO(task_id="T2", objective="Task 2", department="analysis", depends_on=["T1"], priority=2)
        
        goal = GoalGraph(goal_id="G3", goal="Failure cascade test", tasks=[t1, t2])
        engine = TaskEngine(goal)
        
        # Mark T1 failed with max retries exceeded
        t1.max_retries = 0
        engine.mark_running("T1")
        engine.mark_failed("T1", "Google API credentials expired.")
        
        # T1 should be FAILED, T2 should be BLOCKED due to T1 failure cascade
        self.assertEqual(t1.state, TaskState.FAILED)
        self.assertEqual(t2.state, TaskState.BLOCKED)
        self.assertTrue(engine.is_goal_blocked())

class TestPlannerGraphs(unittest.TestCase):
    
    def test_walk_graph(self):
        graph = build_walk_graph("Hello ARIA!")
        self.assertEqual(len(graph.tasks), 1)
        self.assertEqual(graph.tasks[0].task_id, "T1")
        self.assertEqual(graph.tasks[0].department, "pa")
        self.assertEqual(graph.tasks[0].state, TaskState.READY)
        
    def test_action_graph(self):
        action_payload = {
            "action": "create_calendar_event",
            "params": {"summary": "Meeting with Bob", "start_time": "2026-06-01T10:00:00Z"}
        }
        graph = build_action_graph("Schedule a meeting with Bob", action_payload)
        
        self.assertEqual(len(graph.tasks), 2)
        self.assertEqual(graph.tasks[0].task_id, "T1")
        self.assertEqual(graph.tasks[0].department, "execution")
        self.assertEqual(graph.tasks[0].context["action"], "create_calendar_event")
        
        self.assertEqual(graph.tasks[1].task_id, "T2")
        self.assertEqual(graph.tasks[1].department, "pa")
        self.assertEqual(graph.tasks[1].depends_on, ["T1"])

class TestDepartments(unittest.TestCase):
    
    def test_factory(self):
        research = get_department_head("research")
        self.assertEqual(research.name, "research")
        
        analysis = get_department_head("analysis")
        self.assertEqual(analysis.name, "analysis")
        
        writing = get_department_head("writing")
        self.assertEqual(writing.name, "writing")
        
        pa = get_department_head("pa")
        self.assertEqual(pa.name, "pa")

    def test_compression(self):
        head = DepartmentHead()
        raw = "Line 1\nLine 2\nLine 1\nLine 3"
        compressed = head.compress_result(raw)
        # Verify line deduplication
        self.assertEqual(compressed, "Line 1\nLine 2\nLine 3")

class TestBipartiteAuditor(unittest.TestCase):
    
    def test_pre_execution_gatekeeper_basic(self):
        from aria.auditor import PreExecutionGatekeeper
        gatekeeper = PreExecutionGatekeeper()
        
        # 1. Missing action payload
        task_no_action = TaskDTO(
            task_id="T1",
            objective="Send email",
            department="execution",
            depends_on=[],
            priority=1,
            context={}
        )
        passed, reason = gatekeeper.audit(task_no_action)
        self.assertFalse(passed)
        self.assertIn("no specified action payload", reason)
        
        # 2. Unsupported action
        task_unsupported = TaskDTO(
            task_id="T1",
            objective="Hack mainframe",
            department="execution",
            depends_on=[],
            priority=1,
            context={"action": "hack_mainframe"}
        )
        passed, reason = gatekeeper.audit(task_unsupported)
        self.assertFalse(passed)
        self.assertIn("Unsupported Workspace action", reason)

    @patch("aria.auditor.is_google_configured")
    def test_pre_execution_gatekeeper_google_config(self, mock_is_configured):
        from aria.auditor import PreExecutionGatekeeper
        gatekeeper = PreExecutionGatekeeper()
        
        task_valid = TaskDTO(
            task_id="T1",
            objective="Send an email to user",
            department="execution",
            depends_on=[],
            priority=1,
            context={"action": "send_email"}
        )
        
        # Google not configured
        mock_is_configured.return_value = False
        passed, reason = gatekeeper.audit(task_valid)
        self.assertFalse(passed)
        self.assertIn("credentials not configured", reason)
        
        # Google configured
        mock_is_configured.return_value = True
        passed, reason = gatekeeper.audit(task_valid)
        self.assertTrue(passed)
        self.assertEqual(reason, "")

    def test_post_execution_validator_deterministic(self):
        from aria.auditor import PostExecutionValidator
        validator = PostExecutionValidator()
        task = TaskDTO(task_id="T1", objective="Research things", department="research", depends_on=[], priority=1)
        
        # Empty result
        passed, reason = validator.audit(task, "")
        self.assertFalse(passed)
        self.assertIn("empty result", reason)
        
        # Worker error
        passed, reason = validator.audit(task, "This is [Worker error: Timeout]")
        self.assertFalse(passed)
        self.assertIn("Deterministic execution error", reason)
        
        # LLM error
        passed, reason = validator.audit(task, "This is [LLM error: Rate limit]")
        self.assertFalse(passed)
        self.assertIn("Deterministic execution error", reason)
        
        # Valid output
        passed, reason = validator.audit(task, "Search results: Python 3.12 is released.")
        self.assertTrue(passed)
        self.assertEqual(reason, "Search results: Python 3.12 is released.")

    def test_post_execution_validator_semantic_pass(self):
        from aria.auditor import PostExecutionValidator
        # Mock LLM to return JSON indicating passing audit
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"passed": true, "reason": "Looks good and factual."}'
        mock_llm.invoke.return_value = mock_response
        
        validator = PostExecutionValidator(llm=mock_llm)
        task = TaskDTO(task_id="T1", objective="Get count", department="research", depends_on=[], priority=1)
        
        passed, reason = validator.audit(task, "The count is 42.")
        self.assertTrue(passed)
        self.assertEqual(reason, "The count is 42.")
        
    def test_post_execution_validator_semantic_fail(self):
        from aria.auditor import PostExecutionValidator
        # Mock LLM to return JSON indicating failed audit (hallucination)
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"passed": false, "reason": "Claims action was executed when it was only planned."}'
        mock_llm.invoke.return_value = mock_response
        
        validator = PostExecutionValidator(llm=mock_llm)
        task = TaskDTO(task_id="T1", objective="Check email", department="research", depends_on=[], priority=1)
        
        passed, reason = validator.audit(task, "I have successfully logged into your email and sent 10 emails.")
        self.assertFalse(passed)
        self.assertIn("Claims action was executed", reason)

    def test_post_execution_validator_execution_bypass(self):
        from aria.auditor import PostExecutionValidator
        # If department is execution, LLM should not be called at all
        mock_llm = MagicMock()
        validator = PostExecutionValidator(llm=mock_llm)
        task = TaskDTO(task_id="T1", objective="Send email", department="execution", depends_on=[], priority=1)
        
        passed, reason = validator.audit(task, "SUCCESS: Email sent successfully.")
        self.assertTrue(passed)
        self.assertEqual(reason, "SUCCESS: Email sent successfully.")
        mock_llm.invoke.assert_not_called()

    def test_post_execution_validator_compliance_checklist(self):
        from aria.auditor import PostExecutionValidator
        # Verify the auditor evaluates custom checklists correctly
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"passed": false, "reason": "Checklist violated: Factual sources missing."}'
        mock_llm.invoke.return_value = mock_response
        
        validator = PostExecutionValidator(llm=mock_llm)
        task = TaskDTO(
            task_id="T1",
            objective="Environmental study",
            department="research",
            depends_on=[],
            priority=1,
            compliance_checklist=["Factual sources cited", "No generic claims"]
        )
        
        passed, reason = validator.audit(task, "Air pollution is a concern.")
        self.assertFalse(passed)
        self.assertEqual(reason, "Checklist violated: Factual sources missing.")
        
        # Verify system prompt contains checklist elements
        call_args = mock_llm.invoke.call_args[0][0]
        sys_msg = call_args[0].content
        self.assertIn("- [ ] Factual sources cited", sys_msg)
        self.assertIn("- [ ] No generic claims", sys_msg)

    def test_token_budget_governance(self):
        t1 = TaskDTO(task_id="T1", objective="Research", department="research", depends_on=[], priority=1, token_budget=1000)
        goal = GoalGraph(goal_id="G_budget", goal="Budget test", tasks=[t1], total_token_budget=5000)
        engine = TaskEngine(goal)
        
        is_ok, reason = engine.verify_token_budget("T1", 0, 4000)
        self.assertTrue(is_ok)
        
        is_ok, reason = engine.verify_token_budget("T1", 0, 6000)
        self.assertFalse(is_ok)
        self.assertIn("Goal-level token budget exhausted", reason)

        is_ok, reason = engine.verify_token_budget("T1", 1200, 4000)
        self.assertFalse(is_ok)
        self.assertIn("Task-level token budget exhausted", reason)

    def test_structured_schema_invariants(self):
        head = get_department_head("research")
        
        valid_json = '{"findings": "Clear skies", "sources": ["NASA"]}'
        head.validate_schema_invariants(valid_json)
        
        invalid_json = '{"findings": "Clear skies", "sources": ["NASA"'
        with self.assertRaises(ValueError) as ctx:
            head.validate_schema_invariants(invalid_json)
        self.assertIn("Worker returned malformed JSON output", str(ctx.exception))
        
        short_out = "12"
        with self.assertRaises(ValueError) as ctx:
            head.validate_schema_invariants(short_out)
        self.assertIn("extremely short output", str(ctx.exception))

    def test_fail_closed_ambiguity_refusal(self):
        from aria.planner import _build_fallback_graph
        graph = _build_fallback_graph("gibberish query")
        self.assertEqual(graph.status, "FAILED")
        self.assertEqual(len(graph.tasks), 1)
        self.assertEqual(graph.tasks[0].department, "pa")
        self.assertIn("ambiguous", graph.tasks[0].objective)

    def test_sqlite_epoch_sealing(self):
        from aria.bot import is_epoch_sealed, seal_epoch
        epoch = f"test_session_{datetime.now(timezone.utc).timestamp()}:G-test-epoch"
        self.assertFalse(is_epoch_sealed(epoch))
        seal_epoch(epoch)
        self.assertTrue(is_epoch_sealed(epoch))

    def test_autoimmune_confidence_decay(self):
        from aria.memory import log_execution_failure, register_successful_execution, get_anti_pattern_rules, FAILURES_PATH
        import json
        
        domain = "test.autoimmune_decay"
        method = "test_method"
        err = "Mock validation mismatch"
        
        # 1. Log failure
        success = log_execution_failure(domain, method, err)
        self.assertTrue(success)
        
        # Verify initial rule
        rules = get_anti_pattern_rules(domain)
        self.assertIn("CRITICAL DIRECTION", rules)
        
        # 2. Register success - first decay (1.0 -> 0.85)
        register_successful_execution(domain)
        
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures = json.load(f)
        
        entry = next((e for e in failures if e.get("domain") == domain), None)
        self.assertIsNotNone(entry)
        self.assertAlmostEqual(entry["confidence"], 0.85)
        self.assertEqual(entry["success_count"], 1)
        
        # 3. Success runs until pruned (threshold < 0.25)
        for _ in range(12):
            register_successful_execution(domain)
            
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures = json.load(f)
            
        entry_after = next((e for e in failures if e.get("domain") == domain), None)
        self.assertIsNone(entry_after, "Failed rule was not healed and pruned from failures.json")

    def test_intent_router_overrides(self):
        from aria.bot import intent_router, BabuState
        from langchain_core.messages import HumanMessage
        
        # Test 1: explicit /launch command should strip prefix
        state_launch = BabuState(
            messages=[HumanMessage(content="/launch Research the latest space tech advancements")],
            research_data=[],
            user_query="",
            history_text="",
            session_id="test_session",
            search_results="",
            action_result="",
            detected_action=None,
            active_goal={"goal_id": "G_test"},
            compressed_research="",
            routing_metadata={},
            pending_action_notice="",
            goal_graph=None,
            execution_log=[],
            final_brief="",
            tokens={"prompt": 0, "completion": 0, "total": 0}
        )
        
        res = intent_router(state_launch)
        self.assertEqual(res["user_query"], "Research the latest space tech advancements")
        
        # Test 2: explicit launch command (plain text) should strip prefix
        state_plain = BabuState(
            messages=[HumanMessage(content="launch research quantum computing")],
            research_data=[],
            user_query="",
            history_text="",
            session_id="test_session",
            search_results="",
            action_result="",
            detected_action=None,
            active_goal={"goal_id": "G_test"},
            compressed_research="",
            routing_metadata={},
            pending_action_notice="",
            goal_graph=None,
            execution_log=[],
            final_brief="",
            tokens={"prompt": 0, "completion": 0, "total": 0}
        )
        
        res_plain = intent_router(state_plain)
        self.assertEqual(res_plain["user_query"], "research quantum computing")

    @patch("requests.get")
    def test_wikipedia_search(self, mock_get):
        from aria.bot import wikipedia_search
        
        # Mock response for Wikipedia opensearch
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            "quantum",
            ["Quantum mechanics"],
            ["Quantum mechanics is a fundamental theory in physics..."],
            ["https://en.wikipedia.org/wiki/Quantum_mechanics"]
        ]
        mock_get.return_value = mock_response
        
        res = wikipedia_search("quantum")
        self.assertIn("Wikipedia: Quantum mechanics", res)
        self.assertIn("Source: https://en.wikipedia.org/wiki/Quantum_mechanics", res)


class TestExecutionLedger(unittest.TestCase):
    
    def test_log_event(self):
        import sqlite3
        import uuid
        from aria.bot import log_execution_ledger_event, DB_PATH
        
        session_id = f"test_session_{uuid.uuid4().hex[:6]}"
        goal_id = "G_test_ledger"
        
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_id,
            task_id="T1",
            department="research",
            event_type="TEST_EVENT",
            state_before="PENDING",
            state_after="RUNNING",
            metadata={"detail": "hello world"}
        )
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT session_id, goal_id, task_id, department, event_type, state_before, state_after, metadata FROM execution_ledger WHERE session_id = ?",
            (session_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        self.assertIsNotNone(row)
        self.assertEqual(row[0], session_id)
        self.assertEqual(row[1], goal_id)
        self.assertEqual(row[2], "T1")
        self.assertEqual(row[3], "research")
        self.assertEqual(row[4], "TEST_EVENT")
        self.assertEqual(row[5], "PENDING")
        self.assertEqual(row[6], "RUNNING")
        self.assertIn("hello world", row[7])

    @patch("aria.departments.get_department_head")
    @patch("aria.auditor.BipartiteAuditor")
    def test_executor_node_logging(self, mock_auditor_cls, mock_get_dept_head):
        import sqlite3
        import uuid
        from aria.bot import task_executor_node, BabuState, DB_PATH
        from aria.task_engine import TaskDTO, GoalGraph, TaskState
        from langchain_core.messages import HumanMessage
        
        # Mock BipartiteAuditor methods to pass
        mock_auditor = MagicMock()
        mock_auditor.audit_pre.return_value = (True, "Pre pass")
        mock_auditor.audit_post.return_value = (True, "Post pass")
        mock_auditor_cls.return_value = mock_auditor
        
        # Mock Department Head to return simple result
        mock_dept_head = MagicMock()
        mock_dept_head.dispatch.return_value = ("{'findings': 'some findings'}", {"prompt": 10, "completion": 5, "total": 15})
        mock_get_dept_head.return_value = mock_dept_head
        
        session_id = f"test_session_{uuid.uuid4().hex[:6]}"
        
        t1 = TaskDTO(
            task_id="T1",
            objective="Retrieve space exploration facts",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.PENDING
        )
        goal = GoalGraph(
            goal_id="G_exec_ledger_test",
            goal="Exec ledger test",
            tasks=[t1]
        )
        
        state = BabuState(
            messages=[HumanMessage(content="Space facts")],
            gear="LAUNCH",
            research_data=[],
            user_query="Space facts",
            history_text="",
            session_id=session_id,
            search_results="",
            action_result="",
            detected_action=None,
            active_goal={"goal_id": "G_exec_ledger_test"},
            compressed_research="",
            routing_metadata={},
            pending_action_notice="",
            goal_graph=goal.to_dict(),
            execution_log=[],
            final_brief="",
            tokens={"prompt": 0, "completion": 0, "total": 0}
        )
        
        # Run node
        task_executor_node(state)
        
        # Assert database rows exist
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_type, state_before, state_after FROM execution_ledger WHERE session_id = ? ORDER BY event_id ASC",
            (session_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        event_types = [r[0] for r in rows]
        self.assertIn("AUDIT_PRE", event_types)
        self.assertIn("AUDIT_PRE_PASS", event_types)
        self.assertIn("EXECUTION_START", event_types)
        self.assertIn("EXECUTION_DONE", event_types)
        self.assertIn("AUDIT_POST", event_types)
        self.assertIn("AUDIT_POST_PASS", event_types)

    @patch("aria.bot.is_simple_query")
    @patch("aria.planner.plan_goal")
    def test_planner_node_logging(self, mock_plan_goal, mock_is_simple):
        import sqlite3
        import uuid
        from aria.bot import planner_node, BabuState, DB_PATH
        from aria.task_engine import TaskDTO, GoalGraph, TaskState
        from langchain_core.messages import HumanMessage
        
        mock_is_simple.return_value = False
        
        t1 = TaskDTO(
            task_id="T1",
            objective="Retrieve space exploration facts",
            department="research",
            depends_on=[],
            priority=1,
            state=TaskState.PENDING
        )
        goal = GoalGraph(
            goal_id="G_plan_ledger_test",
            goal="Plan ledger test",
            tasks=[t1]
        )
        mock_plan_goal.return_value = goal
        
        session_id = f"test_session_{uuid.uuid4().hex[:6]}"
        state = BabuState(
            messages=[HumanMessage(content="Space facts")],
            gear="LAUNCH",
            research_data=[],
            user_query="Space facts",
            history_text="",
            session_id=session_id,
            search_results="",
            action_result="",
            detected_action=None,
            active_goal={"goal_id": "G_plan_ledger_test"},
            compressed_research="",
            routing_metadata={},
            pending_action_notice="",
            goal_graph=None,
            execution_log=[],
            final_brief="",
            tokens={"prompt": 0, "completion": 0, "total": 0}
        )
        
        # Run node
        planner_node(state)
        
        # Assert database rows exist
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT event_type, state_after FROM execution_ledger WHERE session_id = ? ORDER BY event_id ASC",
            (session_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][0], "INTENT_CLASSIFICATION")
        self.assertEqual(rows[0][1], "CLASSIFIED")
        self.assertEqual(rows[1][0], "GOAL_CREATED")
        self.assertEqual(rows[1][1], "ACTIVE")
        self.assertEqual(rows[2][0], "PLANNING")
        self.assertEqual(rows[2][1], "PLANNED")
        self.assertEqual(rows[3][0], "TEMPLATE_LOOKUP_TELEMETRY")


class TestGoalCorrection(unittest.TestCase):
    
    def test_goal_graph_type_serialization(self):
        # Test default goal_type is "NEW"
        g = GoalGraph(
            goal_id="G_test_type",
            goal="Test goal description",
            tasks=[]
        )
        self.assertEqual(g.goal_type, "NEW")
        
        # Test serialization preserves NEW
        d = g.to_dict()
        self.assertEqual(d["goal_type"], "NEW")
        
        # Test deserialization reconstructs NEW
        g2 = GoalGraph.from_dict(d)
        self.assertEqual(g2.goal_type, "NEW")
        
        # Test setting goal_type to CORRECTION
        g_corr = GoalGraph(
            goal_id="G_test_type_corr",
            goal="Test correction",
            tasks=[],
            goal_type="CORRECTION"
        )
        self.assertEqual(g_corr.goal_type, "CORRECTION")
        d_corr = g_corr.to_dict()
        self.assertEqual(d_corr["goal_type"], "CORRECTION")
        g_corr2 = GoalGraph.from_dict(d_corr)
        self.assertEqual(g_corr2.goal_type, "CORRECTION")

    def test_get_last_goal_graph_retrieval(self):
        import sqlite3
        import uuid
        from aria.bot import log_execution_ledger_event, get_last_goal_graph, DB_PATH
        
        session_id = f"test_corr_session_{uuid.uuid4().hex[:6]}"
        goal_id = "G_test_corr_123"
        
        # Log a mock planning event to the execution ledger database
        graph_mock = {
            "goal_id": goal_id,
            "goal": "Original target goal description",
            "tasks": [],
            "status": "COMPLETED",
            "created_at": "",
            "total_token_budget": 15000,
            "goal_type": "NEW"
        }
        
        log_execution_ledger_event(
            session_id=session_id,
            goal_id=goal_id,
            task_id=None,
            department=None,
            event_type="PLANNING",
            state_before=None,
            state_after="PLANNED",
            metadata={"query": "Original query", "graph": graph_mock}
        )
        
        # Retrieve using helper
        retrieved = get_last_goal_graph(session_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["goal_id"], goal_id)
        self.assertEqual(retrieved["goal"], "Original target goal description")
        self.assertEqual(retrieved["goal_type"], "NEW")


if __name__ == "__main__":
    unittest.main()
