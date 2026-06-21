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

# Ensure babu is in python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "babu"))

from babu.task_engine import TaskDTO, TaskState, GoalGraph
from babu.planner import classify_intent, plan_goal, IntentPacket, _build_fallback_graph
from babu.auditor import PreExecutionGatekeeper
from babu.memory import log_execution_failure, get_anti_pattern_rules, FAILURES_PATH

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
        """Test that lookup/information queries are classified as lookup and READ_ONLY."""
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
        """Test that background/automation tasks are classified as AUTO_EXECUTE."""
        query = "Run the daily scheduled tech compliance post to my Facebook Page."
        packet = classify_intent(query)
        self.assertTrue(packet.execute)
        self.assertEqual(packet.execution_mode, "AUTO_EXECUTE")
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
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY", allowed_actions=[])
        
        # Prohibited task: execution department with mutating action send_email
        prohibited_task = TaskDTO(
            task_id="T2",
            objective="Send email to family",
            department="execution",
            depends_on=[],
            priority=2,
            context={"intent_packet": intent.to_dict(), "action": "send_email"}
        )
        
        # Verify that we can catch this in auditor or plan_goal post-processing
        # Let's test the PreExecutionGatekeeper audit block for LOOKUP violations:
        gatekeeper = PreExecutionGatekeeper()
        passed, reason = gatekeeper.audit(prohibited_task)
        self.assertFalse(passed)
        self.assertIn("strictly prohibited under current intent capability boundaries", reason)
        print(f"✅ Successfully blocked department violation in LOOKUP template: {reason}")

    def test_06_auditor_gatekeeper_enforces_template_boundaries(self):
        """Test that PreExecutionGatekeeper blocks unauthorized execution actions based on template constraints."""
        gatekeeper = PreExecutionGatekeeper()

        # Build an execution task with 'send_email' under 'LOOKUP' template
        # LOOKUP only allows 'search_sheet' action. 'send_email' is strictly prohibited.
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY")
        
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
        self.assertIn("strictly prohibited under current intent capability boundaries", reason)
        print(f"✅ Pre-execution gatekeeper successfully blocked unauthorized action send_email: {reason}")

        # Build an execution task with allowed action 'search_sheet'
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
        from babu.google_service import is_google_configured
        if not is_google_configured():
            import babu.google_service as gs
            original_func = gs.is_google_configured
            gs.is_google_configured = lambda: True
            
        try:
            passed, reason = gatekeeper.audit(task_allowed)
            self.assertTrue(passed, f"Gatekeeper failed unexpectedly: {reason}")
            print(f"✅ Pre-execution gatekeeper successfully allowed authorized action search_sheet.")
        finally:
            if not is_google_configured():
                gs.is_google_configured = original_func

    def test_07_immune_system_root_cause_learning(self):
        """Test that the Epistemic Immune System learns from planning root causes and synthesizes governance rules."""
        # Mock an intent-level classification failure
        intent = IntentPacket(lookup=True, research=True, generate=True, execute=True, execution_mode="AUTO_EXECUTE")
        
        # The user's query was actually informational, but the classifier misclassified it as AUTO_EXECUTE, causing a post-audit fail
        query = "Research standard tax forms in India and list the details."
        
        from unittest.mock import patch, MagicMock
        mock_response = MagicMock()
        mock_response.content = json.dumps({
            "observed_consequence": "The intent classifier misclassified the query as AUTO_EXECUTE instead of READ_ONLY, leading to inappropriate execution task planning.",
            "active_anti_pattern_rule": "NEVER classify queries requesting India tax form details as AUTO_EXECUTE; always verify read-only lookup boundaries.",
            "target_domain": "governance.classification"
        })
        
        with patch("babu.bot.invoke_with_fallback", return_value=mock_response):
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

    def test_08_auditor_allows_read_only_search_gmail_under_lookup(self):
        """Test that PreExecutionGatekeeper allows search_gmail even when execute=False."""
        gatekeeper = PreExecutionGatekeeper()
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY")

        task = TaskDTO(
            task_id="T2",
            objective="Retrieve recent emails",
            department="execution",
            depends_on=[],
            priority=2,
            context={
                "action": "search_gmail",
                "params": {"query": "flight", "max_results": 5},
                "intent_packet": intent.to_dict()
            }
        )

        from babu.google_service import is_google_configured
        if not is_google_configured():
            import babu.google_service as gs
            original_func = gs.is_google_configured
            gs.is_google_configured = lambda: True

        try:
            passed, reason = gatekeeper.audit(task)
            self.assertTrue(passed, f"Gatekeeper failed unexpectedly: {reason}")
            print("✅ Pre-execution gatekeeper successfully permitted read-only search_gmail under LOOKUP.")
        finally:
            if not is_google_configured():
                gs.is_google_configured = original_func

    def test_09_planner_allows_and_generates_search_gmail_tasks(self):
        """Test that the planner generates search_gmail tasks successfully without template violations."""
        query = "Check my recent emails for any flight updates and summarize them."
        
        # Classify the intent
        packet = classify_intent(query)
        self.assertTrue(packet.lookup or packet.research)
        self.assertFalse(packet.execute)
        self.assertEqual(packet.execution_mode, "READ_ONLY")
        
        # Generate plan using plan_goal
        graph = plan_goal(
            query=query,
            intent_packet=packet,
            model_name="llama-3.3-70b-versatile"
        )
        
        print("\nDEBUG test_09 tasks:")
        for t in graph.tasks:
            print(f"  Task {t.task_id}: dept={t.department}, action={t.context.get('action')}, params={t.context.get('params')}, obj={t.objective}")
        print()
        
        self.assertEqual(graph.planner_status, "SUCCESS")
        
        # Verify that there is at least one execution task with action='search_gmail'
        has_search_gmail = False
        for t in graph.tasks:
            if t.department == "execution" and t.context.get("action") == "search_gmail":
                has_search_gmail = True
                
        self.assertTrue(has_search_gmail, "Planner failed to generate search_gmail action task.")
        print("✅ Planner successfully classified, generated, and verified search_gmail task graph without template violation.")

    def test_10_immune_system_semantic_deduplication(self):
        """Test that the Epistemic Immune System semantically deduplicates similar rules instead of appending duplicates."""
        # 1. Clear failures.json before testing
        if os.path.exists(FAILURES_PATH):
            os.remove(FAILURES_PATH)
            
        domain = "department.execution"
        method = "publish_to_facebook_page"
        error_msg_1 = "Facebook API Error: OAuthException - (#100) Page access token is expired or invalid."
        
        # 2. Log first failure
        ok1 = log_execution_failure(domain, method, error_msg_1)
        self.assertTrue(ok1)
        
        # Verify first rule exists
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures_1 = json.load(f)
        self.assertEqual(len(failures_1), 1)
        first_sig = failures_1[0]["failure_signature"]
        self.assertEqual(failures_1[0].get("success_count", 0), 0)
        self.assertTrue("last_reinforced" in failures_1[0], "last_reinforced timestamp is missing from failure entry schema.")
        first_reinforced = failures_1[0]["last_reinforced"]
        
        # 3. Log a semantically identical failure with slightly different wording
        error_msg_2 = "Facebook API OAuthException: Token has expired or is invalid for the page."
        ok2 = log_execution_failure(domain, method, error_msg_2)
        self.assertTrue(ok2)
        
        # 4. Verify that no duplicate rule was added, and the success count has been updated
        with open(FAILURES_PATH, "r", encoding="utf-8") as f:
            failures_2 = json.load(f)
            
        self.assertEqual(len(failures_2), 1, "Semantic deduplicator failed to consolidate duplicate rule and appended a new one.")
        self.assertEqual(failures_2[0]["failure_signature"], first_sig)
        self.assertEqual(failures_2[0].get("success_count", 0), 1, "Success count was not incremented during consolidation.")
        self.assertTrue("last_reinforced" in failures_2[0], "last_reinforced timestamp is missing from consolidated failure entry.")
        print("✅ Epistemic Immune System successfully deduplicated, consolidated semantic identical failure rules, and updated last_reinforced timestamp.")

    def test_11_information_department_routing_and_auditing(self):
        """Test that standard queries route to the 'information' department instead of 'research',
        and that PreExecutionGatekeeper and PostExecutionValidator handle the 'information' department correctly without citation penalties.
        """
        # 1. Verify get_department_head factory returns InformationHead for "information"
        from babu.departments import get_department_head, InformationHead
        head = get_department_head("information")
        self.assertIsInstance(head, InformationHead)
        self.assertEqual(head.name, "information")

        # 2. Verify PreExecutionGatekeeper allows 'information' department
        gatekeeper = PreExecutionGatekeeper()
        intent = IntentPacket(lookup=True, research=False, generate=False, execute=False, execution_mode="READ_ONLY")
        task = TaskDTO(
            task_id="T1",
            objective="Retrieve general facts about Delhi",
            department="information",
            depends_on=[],
            priority=1,
            context={"intent_packet": intent.to_dict()}
        )
        passed, reason = gatekeeper.audit(task)
        self.assertTrue(passed, f"Gatekeeper failed unexpectedly: {reason}")

        # 3. Verify PostExecutionValidator allows 'information' tasks without academic citations
        from babu.auditor import PostExecutionValidator
        from unittest.mock import MagicMock
        from langchain_core.messages import SystemMessage
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(
            content='{"passed": true, "reason": "Passed info check."}'
        )
        validator_with_llm = PostExecutionValidator(llm=mock_llm)
        val_task = TaskDTO(
            task_id="T1",
            objective="Retrieve general facts about Delhi",
            department="information",
            depends_on=[],
            priority=1
        )
        passed, reason = validator_with_llm.audit(val_task, "Delhi is the capital of India.")
        self.assertTrue(passed)
        
        # Verify the system prompt included the information department citation bypass instruction
        system_msg = mock_llm.invoke.call_args[0][0][0]
        self.assertIsInstance(system_msg, SystemMessage)
        self.assertIn("information", system_msg.content)
        self.assertIn("academic-level citations", system_msg.content)
        print("✅ PreExecutionGatekeeper, get_department_head, and PostExecutionValidator correctly support information department with citation-free auditing rules.")

    def test_12_dynamic_department_routing_helper_departments(self):
        """Test that get_allowed_boundaries dynamically includes helper non-mutating departments
        (information, research, analysis, writing, pa) even if the intent packet lists
        a single predefined department like 'analysis'.
        """
        # 1. Create an intent packet with only 'analysis' and 'pa' as allowed departments
        # Specifying allowed_actions=[] prevents the constructor from auto-promoting
        # the packet to allow the 'execution' department due to default search actions.
        intent = IntentPacket(lookup=False, research=False, generate=True, execute=False, execution_mode="READ_ONLY", allowed_departments=["analysis", "pa"], allowed_actions=[])
        intent_dict = intent.to_dict()
        
        # 2. Resolve allowed boundaries using the modified function
        from babu.planner import get_allowed_boundaries as planner_get_bounds
        from babu.auditor import get_allowed_boundaries as auditor_get_bounds
        
        for get_bounds in (planner_get_bounds, auditor_get_bounds):
            allowed_depts, allowed_actions = get_bounds(intent_dict)
            # Verify that helper departments are dynamically added
            self.assertIn("information", allowed_depts)
            self.assertIn("research", allowed_depts)
            self.assertIn("analysis", allowed_depts)
            self.assertIn("writing", allowed_depts)
            self.assertIn("pa", allowed_depts)
            # Verify that mutating execution department is NOT allowed (since not in intent packet)
            self.assertNotIn("execution", allowed_depts)

        # 3. Verify that PreExecutionGatekeeper does not block helper departments
        gatekeeper = PreExecutionGatekeeper()
        for helper_dept in ("information", "research", "analysis", "writing", "pa"):
            task = TaskDTO(
                task_id="T1",
                objective=f"Perform {helper_dept} task",
                department=helper_dept,
                depends_on=[],
                priority=1,
                context={"intent_packet": intent_dict}
            )
            passed, reason = gatekeeper.audit(task)
            self.assertTrue(passed, f"Gatekeeper blocked helper department '{helper_dept}' unexpectedly: {reason}")

        # 4. Verify that execution tasks are still blocked
        task_exec = TaskDTO(
            task_id="T1",
            objective="Send email to user",
            department="execution",
            depends_on=[],
            priority=1,
            context={"intent_packet": intent_dict, "action": "send_email"}
        )
        passed, reason = gatekeeper.audit(task_exec)
        self.assertFalse(passed, "Gatekeeper allowed unauthorized execution task.")
        self.assertIn("strictly prohibited", reason)
        print("✅ Dynamic department routing verified successfully for helper and reasoning departments.")

if __name__ == "__main__":
    unittest.main()

