import unittest
import json
import os
import sys

# Setup paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)
sys.path.append(os.path.join(CURRENT_DIR, "aria"))

# Force test mode paths by setting sys.argv or environment
os.environ["TESTING"] = "true"

from aria.memory import FAILURES_PATH, get_anti_pattern_rules_for_domains
from aria.planner import plan_goal, DEPARTMENTS
from aria.task_engine import GoalGraph

class TestPlannerImmuneAdaptation(unittest.TestCase):

    def setUp(self):
        # Ensure failures_test.json is clean before each test
        if os.path.exists(FAILURES_PATH):
            try:
                os.remove(FAILURES_PATH)
            except Exception:
                pass

    def tearDown(self):
        # Clean up failures_test.json
        if os.path.exists(FAILURES_PATH):
            try:
                os.remove(FAILURES_PATH)
            except Exception:
                pass

    def test_get_anti_pattern_rules_for_domains(self):
        """Test retrieving multiple anti-pattern domains programmatically."""
        mock_failures = [
            {
                "failure_signature": "TEST_FAILURE_1",
                "domain": "department.writing",
                "attempted_methodology": "generate report in Research template",
                "observed_consequence": "violates checklist: lacks proper citation of sources",
                "active_anti_pattern_rule": "NEVER create writing tasks under any template without explicitly specifying constraints for source citation and report conciseness to ensure quality verification.",
                "confidence": 1.0,
                "decay_rate": 0.1,
                "timestamp": "2026-06-02T12:00:00+00:00"
            },
            {
                "failure_signature": "TEST_FAILURE_2",
                "domain": "governance.planning",
                "attempted_methodology": "route general query to auto execution",
                "observed_consequence": "triggered execution without permission",
                "active_anti_pattern_rule": "NEVER assign execution tasks to read-only queries.",
                "confidence": 0.9,
                "decay_rate": 0.1,
                "timestamp": "2026-06-02T12:00:00+00:00"
            }
        ]
        
        # Write to failures_test.json
        os.makedirs(os.path.dirname(FAILURES_PATH), exist_ok=True)
        with open(FAILURES_PATH, "w", encoding="utf-8") as f:
            json.dump(mock_failures, f)
            
        rules = get_anti_pattern_rules_for_domains(["department.writing", "governance.planning"])
        self.assertIn("[CRITICAL EXECUTION CONSTRAINTS - HISTORICAL FAILURES DETECTED]", rules)
        self.assertIn("NEVER create writing tasks under any template", rules)
        self.assertIn("NEVER assign execution tasks to read-only queries", rules)

    def test_planner_adaptation_with_injected_rules(self):
        """Verify that the planner dynamically adapts task checkpoints and checklists based on failures."""
        mock_failures = [
            {
                "failure_signature": "TEST_FAILURE_WRITING",
                "domain": "department.writing",
                "attempted_methodology": "writing report",
                "observed_consequence": "failed post-audit citation check and lacked proper source formatting",
                "active_anti_pattern_rule": "NEVER create writing tasks under a research goal without requiring a dedicated 'citation verification' check and explicitly listing all references.",
                "confidence": 1.0,
                "decay_rate": 0.1,
                "timestamp": "2026-06-02T12:00:00+00:00"
            }
        ]
        
        # Write failures
        os.makedirs(os.path.dirname(FAILURES_PATH), exist_ok=True)
        with open(FAILURES_PATH, "w", encoding="utf-8") as f:
            json.dump(mock_failures, f)
            
        # Decompose goal query
        query = "Research EPFO changes in 2026 and save the report"
        
        # Invoke planner
        print("\n[TEST] Running planner goal decomposition with injected writing citation failures...")
        graph = plan_goal(query)
        self.assertIsInstance(graph, GoalGraph)
        self.assertTrue(len(graph.tasks) >= 2)
        
        # Search for writing or research tasks and check objectives and checklist constraints
        has_citation_objective_or_checklist = False
        for t in graph.tasks:
            obj_lower = t.objective.lower()
            checklist_str = " ".join(t.compliance_checklist).lower()
            print(f"Task {t.task_id} [{t.department}]: objective='{t.objective}' checklist={t.compliance_checklist}")
            
            if "citation" in obj_lower or "citation" in checklist_str or "reference" in obj_lower or "reference" in checklist_str or "source" in obj_lower or "source" in checklist_str:
                has_citation_objective_or_checklist = True
                
        self.assertTrue(has_citation_objective_or_checklist, "Planner failed to inject citation or source constraint adaptations after writing failure!")
        print("✅ Success: Planner successfully adapted checklists or objectives to enforce citations.")

if __name__ == "__main__":
    unittest.main()
