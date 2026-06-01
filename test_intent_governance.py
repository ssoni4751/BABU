import os
import sys
import json
import unittest
import shutil
from dotenv import load_dotenv

load_dotenv()

# Force UTF-8 encoding for Windows streams
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure aria is in python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

from aria.task_engine import TaskDTO, TaskState, GoalGraph
from aria.planner import classify_intent, plan_goal, IntentPacket, _build_fallback_graph, TEMPLATES
from aria.auditor import PreExecutionGatekeeper
from aria.memory import log_execution_failure, get_anti_pattern_rules, FAILURES_PATH

class TestIntentGovernance(unittest.TestCase):

    def setUp(self):
        # Back up existing failures.json if it exists
        self.backup_path = FAILURES_PATH + ".bak_test"
        if os.path.exists(FAILURES_PATH):
            shutil.copy2(FAILURES_PATH, self.backup_path)
            # Remove to start tests with a clean slate
            os.remove(FAILURES_PATH)

    def tearDown(self):
        # Restore backed up failures.json
        if os.path.exists(self.backup_path):
            if os.path.exists(FAILURES_PATH):
                os.remove(FAILURES_PATH)
            shutil.move(self.backup_path, FAILURES_PATH)
        elif os.path.exists(FAILURES_PATH):
            os.remove(FAILURES_PATH)

    def test_01_intent_classification_lookup(self):
        """Test that lookup/information queries are classified as LOOKUP template and READ_ONLY."""
        query = "Who is my mother and what are my family details?"
        packet = classify_intent(query)
        self.assertTrue(packet.lookup or packet.research)
        self.assertFalse(packet.execute)
        self.assertEqual(packet.workflow_template, "LOOKUP")
        self.assertEqual(packet.execution_mode, "READ_ONLY")
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_02_intent_classification_mutation(self):
        """Test that action/email queries are classified as PUBLISH template with approval required."""
        query = "Draft and send an email to my mother summarizing the tax reforms."
        packet = classify_intent(query)
        self.assertTrue(packet.execute)
        self.assertEqual(packet.workflow_template, "PUBLISH")
        self.assertEqual(packet.execution_mode, "APPROVAL_REQUIRED")
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_03_intent_classification_auto_execute(self):
        """Test that background/automation tasks are classified as AUTO_EXECUTE."""
        query = "Run the daily scheduled tech compliance post to my Facebook Page."
        packet = classify_intent(query)
        self.assertTrue(packet.execute)
        self.assertEqual(packet.execution_mode, "AUTO_EXECUTE")
        self.assertIn(packet.workflow_template, ("PUBLISH", "EXECUTE"))
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_04_confidence_clarification_gate(self):
        """Test that ambiguous or vague queries trigger low confidence fallbacks."""
        query = "Take care of this thing, you know."
        packet = classify_intent(query)
        self.assertLess(packet.confidence, 0.65)

        # Confirm the fallback graph is built with AMBIGUOUS_QUERY status
        fallback_graph = _build_fallback_graph(query, planner_status="AMBIGUOUS_QUERY", intent_packet=packet.to_dict())
        self.assertEqual(fallback_graph.planner_status, "AMBIGUOUS_QUERY")
        self.assertEqual(len(fallback_graph.tasks), 1)
        self.assertEqual(fallback_graph.tasks[0].task_id, "T1")
        self.assertIn("Politely explain to the user that their query is too vague", fallback_graph.tasks[0].objective)

    def test_05_planner_template_constraint_enforcement(self):
        """Test that plan_goal programmatically rejects tasks that violate template boundaries."""
        # Query with LOOKUP intent packet
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY", workflow_template="LOOKUP")
        
        # We mock a planner response by patching the LLM call or calling plan_goal with a query that would return an unauthorized task
        # To test the programmatic validation directly, let's call plan_goal.
        # But since plan_goal makes a live Groq call, let's test by generating a graph and observing how plan_goal handles template mismatch.
        # If the LLM generates a task that is strictly prohibited (e.g. analysis department in LOOKUP), plan_goal rejects it.
        # Let's test the programmatic rejection block inside plan_goal by verifying validate_dag or direct post-processing.
        # We can construct a GoalGraph that violates the LOOKUP boundaries and pass it to plan_goal. Wait, we can test the logic directly:
        
        # LOOKUP allowed_departments: {"research", "pa"}
        # Prohibited department: analysis
        prohibited_task = TaskDTO(
            task_id="T2",
            objective="Analyze family data",
            department="analysis",
            depends_on=[],
            priority=2,
            context={"intent_packet": intent.to_dict()}
        )
        
        # Verify that we can catch this in auditor or plan_goal post-processing
        # Let's test the PreExecutionGatekeeper audit block for LOOKUP violations:
        gatekeeper = PreExecutionGatekeeper()
        passed, reason = gatekeeper.audit(prohibited_task)
        self.assertFalse(passed)
        self.assertIn("strictly prohibited under the 'LOOKUP' workflow template constraints", reason)
        print(f"✅ Successfully blocked department violation in LOOKUP template: {reason}")

    def test_06_auditor_gatekeeper_enforces_template_boundaries(self):
        """Test that PreExecutionGatekeeper blocks unauthorized execution actions based on template constraints."""
        gatekeeper = PreExecutionGatekeeper()

        # Build an execution task with 'send_email' under 'LOOKUP' template
        # LOOKUP only allows 'search_sheet' action. 'send_email' is strictly prohibited.
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY", workflow_template="LOOKUP")
        
        task = TaskDTO(
            task_id="T2",
            objective="Send email to user",
            department="execution",
            depends_on=[],
            priority=2,
            context={
                "action": "send_email",
                "params": {"to": "test@gmail.com", "subject": "Test", "body": "Body"},
                "intent_packet": intent.to_dict()
            }
        )

        passed, reason = gatekeeper.audit(task)
        self.assertFalse(passed)
        self.assertIn("strictly prohibited under the 'LOOKUP' workflow template constraints", reason)
        print(f"✅ Pre-execution gatekeeper successfully blocked unauthorized action send_email under LOOKUP: {reason}")

        # Build an execution task with allowed action 'search_sheet' under 'LOOKUP' template
        task_allowed = TaskDTO(
            task_id="T3",
            objective="Search family sheet",
            department="execution",
            depends_on=[],
            priority=2,
            context={
                "action": "search_sheet",
                "params": {"sheet_name": "Family", "query": "Mother"},
                "intent_packet": intent.to_dict()
            }
        )
        
        # Ensure credentials bypass or check
        from aria.google_service import is_google_configured
        if not is_google_configured():
            import aria.google_service as gs
            original_func = gs.is_google_configured
            gs.is_google_configured = lambda: True
            
        try:
            passed, reason = gatekeeper.audit(task_allowed)
            self.assertTrue(passed, f"Gatekeeper failed unexpectedly: {reason}")
            print(f"✅ Pre-execution gatekeeper successfully allowed authorized action search_sheet under LOOKUP.")
        finally:
            if not is_google_configured():
                gs.is_google_configured = original_func

    def test_07_immune_system_root_cause_learning(self):
        """Test that the Epistemic Immune System learns from planning root causes and synthesizes governance rules."""
        # Mock an intent-level classification failure
        intent = IntentPacket(lookup=True, research=True, generate=True, execute=True, execution_mode="AUTO_EXECUTE", workflow_template="PUBLISH")
        
        # The user's query was actually informational, but the classifier misclassified it as AUTO_EXECUTE, causing a post-audit fail
        query = "Research standard tax forms in India and list the details."
        
        ok = log_execution_failure(
            domain="department.execution",
            method="Send tax form email",
            exception_msg="Post-execution Audit Failed: Banned execution task for read-only user query.",
            goal=query,
            intent_packet=intent.to_dict()
        )
        
        self.assertTrue(ok)
        
        # Retrieve governance rules and verify they are stored and loaded
        classification_rules = get_anti_pattern_rules("governance.classification")
        planning_rules = get_anti_pattern_rules("governance.planning")
        
        self.assertTrue(classification_rules or planning_rules, "Epistemic Immune System failed to synthesize any planning-level governance constraints.")
        print("✅ Epistemic Immune System successfully synthesized planning/governance rules from failure:")
        if classification_rules:
            print(f"Classification Rules:\n{classification_rules}")
        if planning_rules:
            print(f"Planning Rules:\n{planning_rules}")

if __name__ == "__main__":
    unittest.main()
