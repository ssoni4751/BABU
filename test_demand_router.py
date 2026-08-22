"""
test_demand_router.py - Unit tests for ADR-101 Tri-Domain Execution Shape & Capability Demand Router
"""

import unittest
from planner import (
    DOMAIN_ACTIONS_REGISTRY,
    VALID_DOMAINS,
    DemandPacket,
    IntentPacket,
    derive_authorized_actions,
    classify_intent
)
from auditor import PreExecutionGatekeeper, get_allowed_boundaries, get_service_class
from task_engine import TaskDTO, TaskState


class TestDemandRouterADR101(unittest.TestCase):

    def test_domain_authorized_action_derivation(self):
        """Verify that Gatekeeper derives authorized actions strictly by domain intersection."""
        # 1. Pure BUSINESS domain
        candidate = ["read_facebook_comments", "post_to_facebook", "send_email", "delete_document"]
        authorized = derive_authorized_actions({"BUSINESS"}, candidate)
        self.assertIn("read_facebook_comments", authorized)
        self.assertIn("post_to_facebook", authorized)
        self.assertNotIn("send_email", authorized)
        self.assertNotIn("delete_document", authorized)

        # 2. Pure USER domain
        candidate_user = ["send_email", "create_event", "read_facebook_comments", "crm_book_appointment"]
        authorized_user = derive_authorized_actions({"USER"}, candidate_user)
        self.assertIn("send_email", authorized_user)
        self.assertIn("create_event", authorized_user)
        self.assertNotIn("read_facebook_comments", authorized_user)
        self.assertNotIn("crm_book_appointment", authorized_user)

        # 3. Multi-domain composition: BUSINESS + USER
        candidate_multi = ["crm_book_appointment", "create_event", "system_status"]
        authorized_multi = derive_authorized_actions({"BUSINESS", "USER"}, candidate_multi)
        self.assertIn("crm_book_appointment", authorized_multi)
        self.assertIn("create_event", authorized_multi)
        self.assertNotIn("system_status", authorized_multi)

    def test_gatekeeper_rejects_cross_domain_action(self):
        """Verify that PreExecutionGatekeeper deterministically blocks cross-domain actions."""
        gatekeeper = PreExecutionGatekeeper()

        # Task belongs to USER domain, but attempts a BUSINESS action
        intent_dict = {
            "demand_domains": ["USER"],
            "allowed_departments": ["execution", "pa"],
            "allowed_actions": ["send_email", "search_gmail"],
            "candidate_actions": ["send_email", "post_to_facebook"]
        }

        task = TaskDTO(
            task_id="task-test-001",
            objective="Post to Facebook page",
            department="execution",
            depends_on=[],
            priority=1,
            context={
                "action": "post_to_facebook",
                "intent_packet": intent_dict
            }
        )

        ok, reason = gatekeeper.audit(task)
        self.assertFalse(ok)
        self.assertIn("not authorized under declared demand domains", reason)

    def test_gatekeeper_permits_authorized_domain_action(self):
        """Verify that PreExecutionGatekeeper permits authorized domain actions."""
        gatekeeper = PreExecutionGatekeeper()

        intent_dict = {
            "demand_domains": ["BUSINESS"],
            "allowed_departments": ["execution", "pa"],
            "allowed_actions": ["read_facebook_comments", "reply_facebook_comment"],
            "candidate_actions": ["read_facebook_comments", "reply_facebook_comment"]
        }

        task = TaskDTO(
            task_id="task-test-002",
            objective="Read recent Facebook comments",
            department="execution",
            depends_on=[],
            priority=1,
            context={
                "action": "read_facebook_comments",
                "intent_packet": intent_dict
            }
        )

        ok, reason = gatekeeper.audit(task)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_intent_packet_demand_packet_properties(self):
        """Verify DemandPacket properties, execution shapes, and risk policies."""
        # Class A: Pure Lookup
        packet_a = IntentPacket(
            lookup=True,
            execute=False,
            demand_domains={"BUSINESS"},
            query_category="BUSINESS_INFORMATION"
        )
        self.assertEqual(packet_a.execution_shape, "CLASS_A")
        self.assertEqual(packet_a.risk_level, "LOW")
        self.assertEqual(packet_a.approval_policy, "AUTO")
        self.assertIn("read_facebook_comments", packet_a.allowed_actions)

        # Class B: Lookup + Standard Action
        packet_b = IntentPacket(
            lookup=True,
            execute=True,
            allowed_actions=["send_email"],
            demand_domains={"USER"},
            query_category="PERSONAL_INFORMATION"
        )
        self.assertEqual(packet_b.execution_shape, "CLASS_B")
        self.assertEqual(packet_b.risk_level, "MEDIUM")
        self.assertEqual(packet_b.approval_policy, "APPROVAL_REQUIRED")

        # Class C: Destructive / Double Confirmation Action
        packet_c = IntentPacket(
            lookup=True,
            execute=True,
            allowed_actions=["delete_document"],
            demand_domains={"USER"},
            query_category="PERSONAL_INFORMATION"
        )
        self.assertEqual(packet_c.risk_level, "HIGH")
        self.assertEqual(packet_c.approval_policy, "DOUBLE_CONFIRMATION")

    def test_service_class_classification(self):
        """Verify service class classification A, B, and C."""
        self.assertEqual(get_service_class("search_sheet"), "A")
        self.assertEqual(get_service_class("read_facebook_comments"), "A")
        self.assertEqual(get_service_class("crm_query_leads"), "A")
        self.assertEqual(get_service_class("send_email"), "B")
        self.assertEqual(get_service_class("crm_book_appointment"), "B")
        self.assertEqual(get_service_class("delete_document"), "C")
        self.assertEqual(get_service_class("bulk_delete"), "C")
        self.assertEqual(get_service_class("clear_memory"), "C")


if __name__ == "__main__":
    unittest.main()
