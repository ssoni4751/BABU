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
        err = "Mock connection timeout"
        
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
        from aria.bot import intent_router, AriaState
        from langchain_core.messages import HumanMessage
        
        # Test 1: explicit /launch command should route to LAUNCH and strip prefix
        state_launch = AriaState(
            messages=[HumanMessage(content="/launch Research the latest space tech advancements")],
            gear="WALK",
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
        self.assertEqual(res["gear"], "LAUNCH")
        self.assertEqual(res["user_query"], "Research the latest space tech advancements")
        
        # Test 2: explicit launch command (plain text) should route to LAUNCH and strip prefix
        state_plain = AriaState(
            messages=[HumanMessage(content="launch research quantum computing")],
            gear="WALK",
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
        self.assertEqual(res_plain["gear"], "LAUNCH")
        self.assertEqual(res_plain["user_query"], "research quantum computing")

if __name__ == "__main__":
    unittest.main()
