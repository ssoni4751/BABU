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

if __name__ == "__main__":
    unittest.main()
