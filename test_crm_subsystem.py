import unittest
import os
import sys
import json

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

from crm_service import ingest_lead, get_crm_pipeline_data, update_lead_stage, format_telegram_crm_digest, extract_lead_intent_and_service

class TestCRMSubsystem(unittest.TestCase):

    def test_extract_lead_intent(self):
        # 1. Test ITR appointment
        res1 = extract_lead_intent_and_service("Can i book an appointment for ITR filing tomorrow?")
        self.assertEqual(res1["service_category"], "ITR")
        self.assertTrue(res1["is_appointment"])
        self.assertGreaterEqual(res1["urgency_score"], 0.7)

        # 2. Test GST notice with phone number
        res2 = extract_lead_intent_and_service("Received urgent GST notice, call me on 9876543210 please")
        self.assertEqual(res2["service_category"], "GST")
        self.assertEqual(res2["contact_info"], "9876543210")
        self.assertGreaterEqual(res2["urgency_score"], 0.8)

        # 3. Test PF claim
        res3 = extract_lead_intent_and_service("My PF withdrawal Form 19 is stuck")
        self.assertEqual(res3["service_category"], "PF")

    def test_ingest_and_pipeline(self):
        # Ingest a test lead
        res = ingest_lead(
            name="Rishabh K Swarnakar",
            channel="Facebook Comment",
            user_message="Can i book an appointment sir?",
            assistant_reply="Thank you for reaching out! We can schedule an appointment for your ITR, GST, or PF needs.",
            source_ref="test_fb_user_123"
        )
        self.assertEqual(res["status"], "SUCCESS")
        self.assertTrue(res.get("lead_id", "").startswith("LEAD-"))

        # Check pipeline data
        pipeline = get_crm_pipeline_data(limit=10)
        self.assertIn("summary", pipeline)
        self.assertIn("leads", pipeline)
        self.assertGreaterEqual(pipeline["summary"]["total_leads"], 1)

        # Update lead stage
        lead_id = res["lead_id"]
        ok = update_lead_stage(lead_id, "CONTACTED", "Called customer, confirmed interest in GST")
        self.assertTrue(ok)

        # Test Telegram formatting
        digest = format_telegram_crm_digest()
        self.assertIn("ANSHU COMPUTER & TAX CONSULTANCY", digest)
        self.assertIn("Active Pipeline Overview", digest)

if __name__ == "__main__":
    unittest.main()
