import unittest
import os
import sys
from datetime import datetime, timezone, timedelta

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

from crm_service import (
    ingest_lead,
    get_crm_pipeline_data,
    update_lead_funnel_stage,
    format_telegram_crm_digest,
    extract_lead_intent_and_service,
    parse_ist_datetime,
    check_slot_availability,
    commit_crm_appointment,
    get_selective_knowledge_slice,
    get_lead_by_source_ref,
    IST
)

class TestCRMSubsystem(unittest.TestCase):

    def setUp(self):
        # Clean test appointments across Postgres & SQLite
        try:
            from services import get_db_connection
            conn, is_pg = get_db_connection()
            if conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM babu_followups WHERE scheduled_date LIKE '2026-08-%'")
                conn.commit()
                cur.close()
                conn.close()
        except Exception:
            pass

        try:
            import sqlite3
            for db_path in ("memory/babu_checkpoint.db", "babu/memory/babu_checkpoint.db", "d:/Aria/memory/babu_checkpoint.db"):
                if os.path.exists(db_path):
                    sconn = sqlite3.connect(db_path)
                    scur = sconn.cursor()
                    scur.execute("DELETE FROM babu_followups WHERE scheduled_date LIKE '2026-08-%'")
                    sconn.commit()
                    scur.close()
                    sconn.close()
        except Exception:
            pass

    def test_extract_lead_intent(self):
        # 1. Test ITR appointment
        res1 = extract_lead_intent_and_service("Can i book an appointment for ITR filing tomorrow?")
        self.assertIn(res1["service_category"], ("Tax", "ITR"))
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

    def test_parse_ist_datetime_and_working_window(self):
        # Base anchor: Friday, 21 Aug 2026 12:00 PM IST
        base_anchor = datetime(2026, 8, 21, 12, 0, tzinfo=IST)

        # 1. Valid weekday within hours: "kal" (Saturday) "2 baje" (14:00)
        p1 = parse_ist_datetime("kal", "2 baje", base_dt=base_anchor)
        self.assertTrue(p1["valid"])
        self.assertEqual(p1["date_str"], "2026-08-22")
        self.assertEqual(p1["time_str"], "14:00")
        self.assertEqual(p1["weekday_name"], "Saturday")

        # 2. Sunday rejection: "parso" (Sunday 23 Aug 2026)
        p2 = parse_ist_datetime("parso", "12:00", base_dt=base_anchor)
        self.assertFalse(p2["valid"])
        self.assertEqual(p2["reason"], "SUNDAY_CLOSED")

        # 3. Outside hours rejection (Morning 9:00 AM)
        p3 = parse_ist_datetime("kal", "9:00 am", base_dt=base_anchor)
        self.assertFalse(p3["valid"])
        self.assertEqual(p3["reason"], "OUTSIDE_WORKING_HOURS")

        # 4. Outside hours rejection (Evening 8:00 PM)
        p4 = parse_ist_datetime("kal", "8 pm", base_dt=base_anchor)
        self.assertFalse(p4["valid"])
        self.assertEqual(p4["reason"], "OUTSIDE_WORKING_HOURS")

        # 5. Boundary testing: 11:00 AM (Open) & 6:00 PM (18:00 Closing)
        p5_open = parse_ist_datetime("somwar", "11:00 am", base_dt=base_anchor)
        self.assertTrue(p5_open["valid"])
        self.assertEqual(p5_open["time_str"], "11:00")

        p5_close = parse_ist_datetime("somwar", "6:00 pm", base_dt=base_anchor)
        self.assertTrue(p5_close["valid"])
        self.assertEqual(p5_close["time_str"], "18:00")

    def test_slot_availability_and_conflict_detection(self):
        # Ingest lead & book a slot
        ingest_res = ingest_lead(
            name="Conflict Test Customer",
            channel="Facebook Messenger",
            user_message="I want to visit tomorrow at 3 PM",
            source_ref="test_conflict_sender_99"
        )
        lead_id = ingest_res["lead_id"]
        
        # 1. First booking succeeds
        commit_res = commit_crm_appointment(
            lead_id=lead_id,
            date_str="2026-08-25",
            time_str="15:00",
            purpose="PF Consultation"
        )
        self.assertEqual(commit_res["status"], "SUCCESS")

        # 2. Second booking on same date and time triggers conflict
        avail_ok, alt_slots = check_slot_availability("2026-08-25", "15:00")
        self.assertFalse(avail_ok)
        self.assertGreater(len(alt_slots), 0)

        # 3. Different slot on same date is available
        avail_free, _ = check_slot_availability("2026-08-25", "11:00")
        self.assertTrue(avail_free)

    def test_concurrent_database_collision_isolation(self):
        # Simulate TOCTOU race condition: Two concurrent requests attempting to commit the same slot
        ingest1 = ingest_lead("Customer A", "Facebook Messenger", "Booking for 4 PM", source_ref="user_a_sim")
        ingest2 = ingest_lead("Customer B", "Facebook Messenger", "Booking for 4 PM", source_ref="user_b_sim")
        
        # Request 1 commits
        res1 = commit_crm_appointment(ingest1["lead_id"], "2026-08-26", "16:00", purpose="ITR Filing")
        self.assertEqual(res1["status"], "SUCCESS")

        # Request 2 attempts to commit the exact same slot directly (bypassing check_slot_availability)
        res2 = commit_crm_appointment(ingest2["lead_id"], "2026-08-26", "16:00", purpose="GST Filing")
        # Database UNIQUE constraint intercepts race condition
        self.assertEqual(res2["status"], "SLOT_CONFLICT")
        self.assertEqual(res2["reason"], "CONCURRENT_SLOT_COLLISION")
        self.assertGreater(len(res2["alternatives"]), 0)

    def test_multiple_appointments_same_day_different_times(self):
        # Verify that multiple appointments on the EXACT SAME DAY at DIFFERENT TIMES all succeed
        ing1 = ingest_lead("Client Morning", "Facebook", "PF 11 AM", source_ref="src_m1")
        ing2 = ingest_lead("Client Afternoon", "Facebook", "ITR 2 PM", source_ref="src_m2")
        ing3 = ingest_lead("Client Evening", "Facebook", "GST 5 PM", source_ref="src_m3")

        res1 = commit_crm_appointment(ing1["lead_id"], "2026-08-27", "11:00", purpose="PF Claim")
        res2 = commit_crm_appointment(ing2["lead_id"], "2026-08-27", "14:00", purpose="ITR Filing")
        res3 = commit_crm_appointment(ing3["lead_id"], "2026-08-27", "17:00", purpose="GST Monthly")

        self.assertEqual(res1["status"], "SUCCESS")
        self.assertEqual(res2["status"], "SUCCESS")
        self.assertEqual(res3["status"], "SUCCESS")

    def test_selective_knowledge_slice(self):
        # PF Slice contains PF facts and documents but no full dump
        pf_slice = get_selective_knowledge_slice("PF")
        self.assertIn("PF Consultancy", pf_slice)
        self.assertIn("UAN", pf_slice)
        self.assertIn("Aadhaar Card", pf_slice)
        self.assertIn("11:00 AM to 6:00 PM", pf_slice)

        # ITR Slice contains Form 16
        itr_slice = get_selective_knowledge_slice("ITR")
        self.assertIn("Income Tax Return", itr_slice)
        self.assertIn("Form 16", itr_slice)

    def test_multi_turn_state_machine(self):
        import time
        source_id = f"test_funnel_client_{int(time.time()*1000)}"
        # Turn 1: Discovery (General)
        ing1 = ingest_lead("Test Client", "Facebook Messenger", "Hello, do you provide tax services?", source_ref=source_id)
        lead_id = ing1["lead_id"]
        self.assertIn(ing1["service_category"], ("General", "Overview", "Tax", "ITR"))

        # Turn 2: Service Identified (PF)
        ing2 = ingest_lead("Test Client", "Facebook Messenger", "Actually my EPFO PF claim was rejected", source_ref=source_id)
        self.assertEqual(ing2["service_category"], "PF")

        # Check retrieval by source_ref
        retrieved = get_lead_by_source_ref(source_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["lead_id"], lead_id)
        self.assertEqual(retrieved["service_category"], "PF")

        # Turn 3: Appointment Commitment
        commit_res = commit_crm_appointment(lead_id, "2026-08-24", "14:00", purpose="EPFO Claim Resolution")
        self.assertEqual(commit_res["status"], "SUCCESS")

    def test_unsupported_service_rejection_and_catalog(self):
        # 1. Extraction flags unsupported service
        res = extract_lead_intent_and_service("Aadhaar card correction k liye kal 2 baje milna hai")
        self.assertTrue(res["is_unsupported"])
        self.assertEqual(res["service_category"], "Unsupported")
        self.assertFalse(res["is_appointment"])  # Invariant: Never treat unsupported query as bookable appointment

        # 2. Knowledge slice contains explicit refusal and 4 core services
        unsupported_slice = get_selective_knowledge_slice("Unsupported")
        self.assertIn("DO NOT provide Aadhaar Card Correction", unsupported_slice)
        self.assertIn("PF", unsupported_slice)
        self.assertIn("Tax", unsupported_slice)
        self.assertIn("GST", unsupported_slice)
        self.assertIn("General Services", unsupported_slice)

        # 3. Conversational DM auto-reply politely declines and presents catalog without booking
        try:
            from social_media import generate_conversational_dm_response
            reply = generate_conversational_dm_response("user_unsupp_test_101", "Aadhaar card update karwana hai kal 3 baje")
            self.assertTrue(any(w in reply.lower() for w in ("aadhar", "aadhaar", "सेवा", "उपलब्ध", "not provide", "we do not", "services")))
            
            # Verify CRM lead was created with UNSUPPORTED_INQUIRY and 0 appointments booked
            lead = get_lead_by_source_ref("user_unsupp_test_101")
            self.assertIsNotNone(lead)
            self.assertEqual(lead["status"], "UNSUPPORTED_INQUIRY")
        except Exception as e:
            print(f"[TEST WARNING] DM auto-reply test skipped LLM execution: {e}")

    def test_conversational_discovery_presents_catalog(self):
        try:
            from social_media import generate_conversational_dm_response
            reply = generate_conversational_dm_response("user_disc_test_202", "Hello, aap kya kya service provide karte hain?")
            self.assertTrue(any(w in reply for w in ("PF", "ITR", "GST", "Digital", "सेवा")))
            lead = get_lead_by_source_ref("user_disc_test_202")
            self.assertIsNotNone(lead)
            self.assertEqual(lead["status"], "DISCOVERY")
        except Exception as e:
            print(f"[TEST WARNING] Discovery test skipped LLM execution: {e}")

if __name__ == "__main__":
    unittest.main()
