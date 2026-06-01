import os
import sys
import unittest
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
from aria.planner import classify_intent, plan_goal, IntentPacket, _build_fallback_graph
from aria.auditor import PreExecutionGatekeeper

class TestIntentGovernance(unittest.TestCase):

    def test_01_intent_classification_lookup(self):
        """Test that lookup/information queries are classified as read-only lookups."""
        query = "Who is my mother and what are my family details?"
        packet = classify_intent(query)
        self.assertTrue(packet.lookup or packet.research)
        self.assertFalse(packet.execute)
        self.assertEqual(packet.execution_mode, "READ_ONLY")
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_02_intent_classification_mutation(self):
        """Test that action/email queries are classified as execute with approval required."""
        query = "Draft and send an email to my mother summarizing the tax reforms."
        packet = classify_intent(query)
        self.assertTrue(packet.execute)
        self.assertEqual(packet.execution_mode, "APPROVAL_REQUIRED")
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_03_intent_classification_auto_execute(self):
        """Test that background/automation tasks are classified as auto-execute."""
        query = "Run the daily scheduled tech compliance post to my Facebook Page."
        packet = classify_intent(query)
        self.assertTrue(packet.execute)
        self.assertEqual(packet.execution_mode, "AUTO_EXECUTE")
        self.assertGreaterEqual(packet.confidence, 0.65)

    def test_04_confidence_clarification_gate(self):
        """Test that ambiguous or vague queries trigger low confidence."""
        query = "Take care of this thing, you know."
        packet = classify_intent(query)
        self.assertLess(packet.confidence, 0.65)

        # Confirm the fallback graph is built with AMBIGUOUS_QUERY status
        fallback_graph = _build_fallback_graph(query, planner_status="AMBIGUOUS_QUERY", intent_packet=packet.to_dict())
        self.assertEqual(fallback_graph.planner_status, "AMBIGUOUS_QUERY")
        self.assertEqual(len(fallback_graph.tasks), 1)
        self.assertEqual(fallback_graph.tasks[0].task_id, "T1")
        self.assertIn("Politely explain to the user that their query is too vague", fallback_graph.tasks[0].objective)

    def test_05_auditor_gatekeeper_blocks_hallucination(self):
        """Test that PreExecutionGatekeeper blocks execution tasks if execute is false."""
        gatekeeper = PreExecutionGatekeeper()

        # Build an unauthorized task (execute = False)
        intent = IntentPacket(lookup=True, research=True, generate=True, execute=False, execution_mode="READ_ONLY")
        
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
        self.assertIn("execute' capability is disabled", reason)
        print(f"✅ Pre-execution gatekeeper successfully blocked task: {reason}")

    def test_06_auditor_gatekeeper_allows_authorized(self):
        """Test that PreExecutionGatekeeper allows execution tasks if execute is true."""
        # Ensure credentials bypass or check
        from aria.google_service import is_google_configured
        if not is_google_configured():
            # Temporarily mock to pass credentials check for this test
            import aria.google_service as gs
            original_func = gs.is_google_configured
            gs.is_google_configured = lambda: True
            
        try:
            gatekeeper = PreExecutionGatekeeper()
            intent = IntentPacket(lookup=True, research=True, generate=True, execute=True, execution_mode="APPROVAL_REQUIRED")
            
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
            self.assertTrue(passed, f"Gatekeeper failed unexpectedly: {reason}")
        finally:
            if not is_google_configured():
                gs.is_google_configured = original_func

if __name__ == "__main__":
    unittest.main()
