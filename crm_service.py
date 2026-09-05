"""
==============================================================================
Project BABU — Independent Business CRM Subsystem (crm_service.py)
==============================================================================
Dedicated Business Plane for Anshu Computer & Tax Consultancy:
- Stateful Lead Ingestion & Deduplication (Facebook, Telegram, Walk-ins)
- Formal Funnel State Machine (NEW -> SERVICE_IDENTIFIED -> INFO_PROVIDED ->
  APPOINTMENT_PROPOSED -> APPOINTMENT_PENDING -> APPOINTMENT_SCHEDULED)
- Deterministic IST Datetime & Relative Date Parser (kal/parso/time slots)
- Working Hours Window Enforcer (Mon-Sat, 11:00 AM - 6:00 PM, Sunday Closed)
- Slot Conflict & Availability Checking (babu_followups)
- Transactional Commit-before-Alert Sequence
- Real-time Telegram Alert Dispatcher to Business Owner
- Selective Knowledge Slice Retrieval (ADR-091/092)
==============================================================================
"""

import os
import json
import time
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional, Tuple
import requests

try:
    from .services import get_db_connection
except ImportError:
    from services import get_db_connection

IST = timezone(timedelta(hours=5, minutes=30))

# ----------------------------------------------------------------------
# 1. Selective Knowledge Slice Retrieval (Zero Bulk Dumps - ADR-091/092)
# ----------------------------------------------------------------------

SUPPORTED_SERVICE_CATALOG: Dict[str, str] = {
    "PF": "PF Consultancy & Compliance Resolution (Primary Specialization: PF Claim Settlement Forms 19/10C/31, UAN Consolidation, KYC/DOB/Name Correction, Joint Declaration, Ex-Employer Cases)",
    "Tax": "Tax Consultancy & Income Tax Return (ITR-1, 2, 4 Filing, Tax Computation, AIS/TIS Review, Refund Status & Notice Assistance)",
    "GST": "GST Services & Compliance (New Registration, Monthly/Quarterly GSTR-1 & GSTR-3B Filings, LUT, Annual Return GSTR-9)",
    "General": "General Services (All other offered services differing from PF, Tax, and GST: MSME Udyam Registration, Jeevan Pramaan Digital Life Certificate, Passport Online, PAN Card, Sevayojan, and other digital citizen services)"
}

UNSUPPORTED_SERVICE_KEYWORDS: List[str] = [
    "aadhaar", "aadhar", "adhar", "uidai", "fingerprint", "biometric", "aadhaar update", "aadhaar correction",
    "ration card", "rashan card", "driving license", "dl renewal", "dl apply", "driving licence",
    "voter card correction", "voter id correction", "voter id update", "birth certificate", "death certificate"
]


def get_selective_knowledge_slice(service_category: str) -> str:
    """Return verified, authoritative business facts matching user_profile.json."""
    general_header = (
        "Business: Anshu Computer & Tax Consultancy\n"
        "Consultant: Shubham Swarnkar (शुभम स्वर्णकार जी / Anshu)\n"
        "Tagline: PF, Tax & Compliance Solutions\n"
        "Office Address: Kaushal Market, Rath Road, Orai, Uttar Pradesh, India\n"
        "Working Hours: Monday to Saturday from 11:00 AM to 6:00 PM (Sunday Closed)\n"
        "WhatsApp / Call: +91 7217646673\n"
        "Website: https://anshu-computer-and-tax-consultants.onrender.com\n"
    )
    
    svc = (service_category or "Overview").upper().strip()
    
    if "UNSUPPORTED" in svc or "OUT_OF_SCOPE" in svc:
        return (
            f"{general_header}\n"
            "SERVICE NOTICE (OUT OF SCOPE):\n"
            "We DO NOT provide Aadhaar Card Correction / Biometric Update, Ration Card, or Driving License services.\n\n"
            "Our 4 Authorized Service Categories:\n"
            "1. 🏢 PF Consultancy (Primary Specialization: PF Claim Settlement, UAN Consolidation, KYC/Name/DOB Correction, Joint Declaration, Ex-Employer Cases)\n"
            "2. 📑 Tax Services (ITR-1, 2, 4 Filing, Tax Computation, AIS/TIS Review, Refund Status, Notice Assistance)\n"
            "3. 📊 GST Services (New Registration, Monthly GSTR-1 & GSTR-3B Filings, LUT, Notice Assistance)\n"
            "4. 🌐 General Services (All other offered services differing from PF, Tax, and GST: MSME Udyam Registration, Life Certificate / Jeevan Pramaan, Passport, PAN Card, Sevayojan)\n"
            "Appointment Policy: In-office appointments can ONLY be booked for our 4 authorized service categories above."
        )
    elif "PF" in svc or "EPF" in svc or "UAN" in svc:
        return (
            f"{general_header}\n"
            "Category: PF Consultancy & Compliance Resolution (Primary Specialization)\n"
            "Scope:\n"
            "• PF Claim Settlement (Form 19 Final Settlement, Form 10C Pension Withdrawal, Form 31 Advance for illness/house/marriage)\n"
            "• UAN Consolidation & Transfer across multiple Member IDs\n"
            "• KYC Correction (Bank account, PAN, Aadhaar link, father name, date of birth / joining / exit correction)\n"
            "• Joint Declaration Form Attestation & assistance with non-cooperating ex-employers\n"
            "• EPFO Grievance Registration & Follow-up\n\n"
            "Required Documents for PF Services:\n"
            "1. Aadhaar Card (linked with registered mobile number for OTP)\n"
            "2. PAN Card\n"
            "3. Bank Passbook or Cancelled Cheque (with clearly printed Name, Account No & IFSC)\n"
            "4. UAN Number & Password (or Member ID)\n"
            "Pricing/Guidance: Highly affordable transparent charges based on case complexity."
        )
    elif "TAX" in svc or "ITR" in svc or "INCOME" in svc:
        return (
            f"{general_header}\n"
            "Category: Tax Services & Advisory (Income Tax Return - ITR)\n"
            "Scope:\n"
            "• Salaried Individuals (ITR-1 Sahaj) & Pensioners\n"
            "• Business / Professionals / Presumptive Taxation (ITR-4 Sugam)\n"
            "• Capital Gains, Shares, Mutual Funds & Property (ITR-2)\n"
            "• AIS / TIS Verification & Tax Computation\n"
            "• Income Tax Refund Status Tracking & Defective Notice Resolution\n\n"
            "Required Documents for Tax Services:\n"
            "1. Form 16 / Salary Slips (if salaried)\n"
            "2. Bank Statements for the full Financial Year\n"
            "3. PAN Card & Aadhaar Card\n"
            "4. Investment Proofs (LIC, PPF, Tuition fees, Health insurance 80D, Home loan interest, etc.)\n"
            "Pricing/Guidance: Fast same-day computation and verified e-verification."
        )
    elif "GST" in svc:
        return (
            f"{general_header}\n"
            "Category: GST Services & Compliance\n"
            "Scope:\n"
            "• New GST Registration (Proprietorship, Partnership, Private Limited)\n"
            "• Monthly & Quarterly Return Filing (GSTR-1, GSTR-3B)\n"
            "• Composition Scheme, LUT Filing for Exporters\n"
            "• Annual Return (GSTR-9) & GST Notice Clarifications\n\n"
            "Required Documents for GST Services:\n"
            "1. PAN Card & Aadhaar Card of Owner/Proprietor\n"
            "2. Business Electricity Bill / Rent Agreement / NOC\n"
            "3. Bank Account Proof / Cancelled Cheque\n"
            "4. Passport-size Photo & Business Trade Name"
        )
    elif svc in ("GENERAL_SPECIFIC", "GENERAL_SERVICES", "GENERAL", "DIGITAL", "MSME", "CSC", "UDYAM", "PASSPORT", "JEEVAN", "PAN") and svc not in ("OVERVIEW", "ALL", "DEFAULT", "GREETING"):
        return (
            f"{general_header}\n"
            "Category: General Services (All other offered services differing from PF, Tax, and GST)\n"
            "Scope:\n"
            "1. MSME Udyam Registration (for government business schemes, subsidy & bank loan benefits)\n"
            "2. Jeevan Pramaan (Biometric/Iris Digital Life Certificate for Central & State Pensioners)\n"
            "3. Passport Online Application & PSK Appointment Booking\n"
            "4. Sevayojan Employment Exchange Portal Registration\n"
            "5. PAN Card (New Application, Minor PAN, Correction & Instant e-PAN)\n"
            "6. Other Digital & E-Governance Citizen Online Applications\n\n"
            "Required Documents: Aadhaar Card (with mobile linked), PAN Card, relevant scheme/applicant details.\n"
            "Note: Aadhaar Card correction/biometrics, Ration Card, and Driving License are strictly NOT provided."
        )
    else:
        # Default / Greeting / Overview: Complete 4-category business catalog
        return (
            f"{general_header}\n"
            "Our 4 Main Service Categories:\n\n"
            "1. 🏢 PF Consultancy & Compliance Resolution (Primary Specialization):\n"
            "   • PF Advance (Form 31), Final Settlement (Form 19), Pension (Form 10C)\n"
            "   • UAN Consolidation, Transfer, KYC / Name / DOB / Father Name Correction\n"
            "   • Joint Declaration Form assistance & Ex-Employer dispute cases\n\n"
            "2. 📑 Tax Consultancy & Advisory (Income Tax Return - ITR):\n"
            "   • Salaried (ITR-1), Business (ITR-4), Capital Gains (ITR-2)\n"
            "   • Tax Computation, AIS/TIS Review, Refund Tracking & Notice Assistance\n\n"
            "3. 📊 GST Services & Compliance:\n"
            "   • New GST Registration, Monthly/Quarterly Return Filing (GSTR-1, GSTR-3B)\n"
            "   • Annual Returns (GSTR-9), LUT Filing, Notice Resolution\n\n"
            "4. 🌐 General Services (All other offered services differing from PF, Tax, and GST):\n"
            "   • MSME Udyam Registration, Jeevan Pramaan (Digital Life Certificate for Pensioners)\n"
            "   • Passport Online Application & Appointment Booking\n"
            "   • Sevayojan Registration, New / Correction PAN Card & other online applications\n\n"
            "Office Timings: Monday to Saturday, 11:00 AM to 6:00 PM (Sunday Closed) at Kaushal Market, Rath Road, Orai.\n"
            "Note: We DO NOT provide Aadhaar Card correction/biometrics, Ration Card, or Driving License services."
        )


# ----------------------------------------------------------------------
# 2. Deterministic IST Datetime & Relative Date Parser
# ----------------------------------------------------------------------

def parse_ist_datetime(
    date_text: Optional[str],
    time_text: Optional[str],
    base_dt: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Deterministically parses date and time expressions in Indian context (Hindi/English).
    Requires explicit date AND/OR explicit time signals.
    Validates working window: Mon-Sat, 11:00 AM <= time <= 6:00 PM (18:00).
    """
    if not base_dt:
        base_dt = datetime.now(timezone.utc).astimezone(IST)
    
    date_clean = (date_text or "").strip().lower()
    time_clean = (time_text or "").strip().lower()
    combined = f"{date_clean} {time_clean}".strip()
    
    if not combined:
        return {"valid": False, "reason": "NO_DATETIME_PROVIDED"}

    target_date = None
    explicit_date_found = False

    # 1. Explicit Date Expression Parsing
    if any(k in combined for k in ("aaj", "today")):
        target_date = base_dt.date()
        explicit_date_found = True
    elif any(k in combined for k in ("kal", "tomorrow")):
        target_date = (base_dt + timedelta(days=1)).date()
        explicit_date_found = True
    elif any(k in combined for k in ("parso", "day after tomorrow", "day after")):
        target_date = (base_dt + timedelta(days=2)).date()
        explicit_date_found = True
    else:
        weekday_map = {
            "somwar": 0, "monday": 0, "mon": 0,
            "mangalwar": 1, "tuesday": 1, "tue": 1,
            "budhwar": 2, "wednesday": 2, "wed": 2,
            "guruwar": 3, "brihaspatiwar": 3, "thursday": 3, "thu": 3,
            "shukrawar": 4, "friday": 4, "fri": 4,
            "shaniwar": 5, "saturday": 5, "sat": 5,
            "raviwar": 6, "itwar": 6, "sunday": 6, "sun": 6
        }
        matched_wd = None
        for k, v in weekday_map.items():
            if re.search(rf'\b{k}\b', combined):
                matched_wd = v
                break
        
        if matched_wd is not None:
            curr_wd = base_dt.weekday()
            days_ahead = matched_wd - curr_wd
            if days_ahead <= 0:
                days_ahead += 7
            target_date = (base_dt + timedelta(days=days_ahead)).date()
            explicit_date_found = True
        else:
            date_match = re.search(r'\b(\d{1,4})[/\-\.](\d{1,2})[/\-\.](\d{1,4})\b', combined)
            if date_match:
                p1, p2, p3 = date_match.groups()
                try:
                    if len(p1) == 4: # YYYY-MM-DD
                        target_date = datetime(int(p1), int(p2), int(p3)).date()
                    else: # DD-MM-YYYY
                        target_date = datetime(int(p3), int(p2), int(p1)).date()
                    explicit_date_found = True
                except Exception:
                    pass

    # 2. Explicit Time Expression Parsing
    hour = None
    minute = 0
    explicit_time_found = False

    # a) "2:30 pm", "2 pm", "11 am", "11:00 am"
    m_ampm = re.search(r'\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b', combined)
    # b) "2 baje", "11 baje", "12 baje", "2 बजे"
    m_baje = re.search(r'\b(\d{1,2})\s*(?:baje|बजे)\b', combined)
    # c) Standard HH:MM 24hr or 12hr e.g. "14:00", "11:30"
    m_colon = re.search(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', combined)
    # d) Prefix indicator e.g. "dopehar 2", "shaam 4", "subah 11", "at 3", "slot 2"
    m_prefix = re.search(r'\b(?:dopehar|shaam|subah|morning|afternoon|evening|slot|time|at)\s*(\d{1,2})\b', combined)

    if m_ampm:
        raw_h = int(m_ampm.group(1))
        minute = int(m_ampm.group(2)) if m_ampm.group(2) else 0
        ampm = m_ampm.group(3).lower()
        if ampm == "am":
            hour = 0 if raw_h == 12 else raw_h
        else:
            hour = 12 if raw_h == 12 else raw_h + 12
        explicit_time_found = True
    elif m_baje:
        raw_h = int(m_baje.group(1))
        minute = 0
        is_pm = any(k in combined for k in ("shaam", "dopehar", "afternoon", "evening", "pm"))
        if is_pm:
            hour = 12 if raw_h == 12 else (raw_h + 12 if raw_h < 12 else raw_h)
        elif raw_h in (1, 2, 3, 4, 5, 6):
            hour = raw_h + 12
        else:
            hour = raw_h
        explicit_time_found = True
    elif m_colon:
        hour = int(m_colon.group(1))
        minute = int(m_colon.group(2))
        explicit_time_found = True
    elif m_prefix:
        raw_h = int(m_prefix.group(1))
        minute = 0
        is_pm = any(k in combined for k in ("shaam", "dopehar", "afternoon", "evening", "pm"))
        if is_pm or raw_h in (1, 2, 3, 4, 5, 6):
            hour = 12 if raw_h == 12 else (raw_h + 12 if raw_h < 12 else raw_h)
        else:
            hour = raw_h
        explicit_time_found = True

    # Critical Guard: If NEITHER explicit date nor explicit time was stated, NOT A BOOKING REQUEST!
    if not explicit_date_found and not explicit_time_found:
        return {"valid": False, "reason": "NO_DATETIME_PROVIDED"}

    # If only date was found without a specific time
    if explicit_date_found and not explicit_time_found:
        display_date = target_date.strftime("%d %b %Y (%A)")
        return {
            "valid": False,
            "reason": "DATE_ONLY_NEED_TIME",
            "date_str": target_date.strftime("%Y-%m-%d"),
            "display_date": display_date,
            "message": f"आप {display_date} को किस समय आना चाहते हैं? (कार्यालय समय: 11:00 AM से 6:00 PM)"
        }

    # If only time was found without a date: default to today (if still within office hours) or tomorrow
    if not explicit_date_found and explicit_time_found:
        curr_hour = base_dt.hour
        if hour > curr_hour and base_dt.weekday() != 6:
            target_date = base_dt.date()
        else:
            target_date = (base_dt + timedelta(days=1)).date()
            if target_date.weekday() == 6: # Sunday closed -> Monday
                target_date = target_date + timedelta(days=1)

    target_time_str = f"{hour:02d}:{minute:02d}"

    # 3. Deterministic Working Window Validation
    # Office interval: 11:00 AM <= time <= 6:00 PM (18:00), Monday to Saturday
    weekday_idx = target_date.weekday()
    weekday_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][weekday_idx]
    
    if weekday_idx == 6: # Sunday
        return {
            "valid": False,
            "date_str": target_date.strftime("%Y-%m-%d"),
            "time_str": target_time_str,
            "weekday_name": weekday_name,
            "reason": "SUNDAY_CLOSED",
            "message": "Our consultancy is closed on Sundays. Please choose any time between Monday to Saturday from 11:00 AM to 6:00 PM."
        }

    # Time validation: 11:00 to 18:00
    time_float = hour + (minute / 60.0)
    if time_float < 11.0 or time_float > 18.0:
        return {
            "valid": False,
            "date_str": target_date.strftime("%Y-%m-%d"),
            "time_str": target_time_str,
            "weekday_name": weekday_name,
            "reason": "OUTSIDE_WORKING_HOURS",
            "message": f"Requested time ({target_time_str}) is outside office hours. Office timings are 11:00 AM to 6:00 PM (Mon-Sat)."
        }

    # Format user-friendly display
    dt_obj = datetime(target_date.year, target_date.month, target_date.day, hour, minute)
    display_date = dt_obj.strftime("%d %b %Y (%A)")
    display_time = dt_obj.strftime("%I:%M %p")

    return {
        "valid": True,
        "date_str": target_date.strftime("%Y-%m-%d"),
        "time_str": target_time_str,
        "display_date": display_date,
        "display_time": display_time,
        "weekday_name": weekday_name,
        "reason": "VALID_SLOT",
        "iso_timestamp": f"{target_date.strftime('%Y-%m-%d')} {target_time_str}"
    }


# ----------------------------------------------------------------------
# 3. Slot Conflict & Availability Engine
# ----------------------------------------------------------------------

def check_slot_availability(date_str: str, time_str: str) -> Tuple[bool, List[str]]:
    """
    Deterministic check against babu_followups to prevent double-booking.
    Returns (is_available, list_of_suggested_alternatives).
    """
    conn, is_pg = get_db_connection()
    if not conn:
        return True, []

    try:
        cursor = conn.cursor()
        query_date_pattern = f"{date_str}%"
        
        if is_pg:
            cursor.execute("""
                SELECT scheduled_date 
                FROM babu_followups 
                WHERE status = 'PENDING' AND scheduled_date LIKE %s
            """, (query_date_pattern,))
        else:
            cursor.execute("""
                SELECT scheduled_date 
                FROM babu_followups 
                WHERE status = 'PENDING' AND scheduled_date LIKE ?
            """, (query_date_pattern,))
            
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        booked_hours = set()
        for r in rows:
            dt_raw = str(r[0])
            # Check if time_str or same hour exists
            booked_hours.add(dt_raw)
            if time_str in dt_raw:
                # Slot is occupied! Generate 2 open alternative slots in working hours
                req_h = int(time_str.split(":")[0])
                alternatives = []
                for candidate_h in (req_h - 1, req_h + 1, req_h + 2, 12, 15):
                    if 11 <= candidate_h <= 17:
                        cand_str = f"{candidate_h:02d}:00"
                        if not any(cand_str in bh for bh in booked_hours):
                            cand_ampm = datetime.strptime(cand_str, "%H:%M").strftime("%I:%M %p")
                            if cand_ampm not in alternatives:
                                alternatives.append(cand_ampm)
                    if len(alternatives) >= 2:
                        break
                if not alternatives:
                    alternatives = ["11:00 AM", "03:00 PM"]
                return False, alternatives

        return True, []
    except Exception as e:
        print(f"[CRM CONFLICT CHECK ERROR] {e}", flush=True)
        return True, []


# ----------------------------------------------------------------------
# 4. Transactional Appointment Commitment & Telegram Alert
# ----------------------------------------------------------------------

def commit_crm_appointment(
    lead_id: str,
    date_str: str,
    time_str: str,
    purpose: str = "Client Consultation",
    notes: str = ""
) -> Dict[str, Any]:
    """
    Atomically commits appointment to babu_leads and babu_followups.
    Only dispatches Telegram Alert upon verified DB COMMIT.
    """
    conn, is_pg = get_db_connection()
    if not conn:
        return {"status": "ERROR", "error": "Database unavailable"}

    scheduled_stamp = f"{date_str} {time_str}"
    
    try:
        cursor = conn.cursor()
        
        # 1. Transactional Slot Anti-Collision Check
        slot_pattern = f"{date_str}%{time_str}%"
        if is_pg:
            cursor.execute("""
                SELECT followup_id FROM babu_followups 
                WHERE scheduled_date LIKE %s AND status = 'PENDING' AND proposed_action = 'IN_OFFICE_APPOINTMENT'
                FOR UPDATE
            """, (slot_pattern,))
        else:
            cursor.execute("""
                SELECT followup_id FROM babu_followups 
                WHERE scheduled_date LIKE ? AND status = 'PENDING' AND proposed_action = 'IN_OFFICE_APPOINTMENT'
            """, (slot_pattern,))
        existing_slot = cursor.fetchone()
        if existing_slot:
            print(f"[CRM CONCURRENCY COLLISION] Slot {scheduled_stamp} collided in DB", flush=True)
            conn.rollback()
            cursor.close()
            conn.close()
            avail_ok, alt_slots = check_slot_availability(date_str, time_str)
            if not alt_slots:
                alt_slots = ["11:00 AM", "03:00 PM"]
            return {
                "status": "SLOT_CONFLICT",
                "reason": "CONCURRENT_SLOT_COLLISION",
                "alternatives": alt_slots,
                "error": "This slot was just booked by another customer."
            }

        # 2. Update lead status in babu_leads
        if is_pg:
            cursor.execute("""
                UPDATE babu_leads 
                SET status = 'APPOINTMENT_SCHEDULED',
                    notes = COALESCE(notes, '') || %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = %s
                RETURNING name, channel, contact_info, service_category
            """, (f" | Appt: {scheduled_stamp} ({purpose})", lead_id))
            lead_row = cursor.fetchone()
        else:
            cursor.execute("""
                UPDATE babu_leads 
                SET status = 'APPOINTMENT_SCHEDULED',
                    notes = COALESCE(notes, '') || ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = ?
            """, (f" | Appt: {scheduled_stamp} ({purpose})", lead_id))
            cursor.execute("SELECT name, channel, contact_info, service_category FROM babu_leads WHERE lead_id = ?", (lead_id,))
            lead_row = cursor.fetchone()

        lead_name = lead_row[0] if lead_row and lead_row[0] else "Client"
        channel = lead_row[1] if lead_row and lead_row[1] else "CRM"
        contact_info = lead_row[2] if lead_row and lead_row[2] else "Direct"
        service = lead_row[3] if lead_row and lead_row[3] else "Consultation"

        # 3. Insert into babu_followups
        draft_msg = f"Confirmed appointment for {purpose} at Kaushal Market, Orai on {scheduled_stamp}."
        if is_pg:
            cursor.execute("""
                INSERT INTO babu_followups (lead_id, scheduled_date, proposed_action, draft_message, status, notes)
                VALUES (%s, %s, %s, %s, 'PENDING', %s)
                RETURNING followup_id
            """, (lead_id, scheduled_stamp, "IN_OFFICE_APPOINTMENT", draft_msg, notes))
            followup_id = cursor.fetchone()[0]
        else:
            cursor.execute("""
                INSERT INTO babu_followups (lead_id, scheduled_date, proposed_action, draft_message, status, notes)
                VALUES (?, ?, ?, ?, 'PENDING', ?)
            """, (lead_id, scheduled_stamp, "IN_OFFICE_APPOINTMENT", draft_msg, notes))
            followup_id = cursor.lastrowid

        # 3. COMMIT Transaction
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"[CRM TRANSACTION SUCCESS] Booked appointment {followup_id} for lead {lead_id} at {scheduled_stamp}", flush=True)

        # 4. Record into Babu Central Cognitive Plane (Timeline, K0 Memory & Ledger)
        try:
            conn_babu, is_pg_babu = get_db_connection()
            if conn_babu:
                cur_babu = conn_babu.cursor()
                session_id = f"crm_{channel.lower().replace(' ', '_')}_{lead_id}"
                goal_id = f"CRM-APPT-{int(time.time())}"
                summary_text = f"Confirmed appointment for {lead_name} ({service}) on {date_str} at {time_str}"
                meta_json = json.dumps({
                    "lead_id": lead_id,
                    "lead_name": lead_name,
                    "service": service,
                    "scheduled_datetime": scheduled_stamp,
                    "contact_info": contact_info,
                    "channel": channel,
                    "notes": notes
                })
                # 1. babu_temporal_timeline
                if is_pg_babu:
                    cur_babu.execute("""
                        INSERT INTO babu_temporal_timeline (event_category, summary, outcome, cause, effect, resolution, impact_score, confidence, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, ("APPOINTMENT_BOOKED", summary_text, "SUCCESS", f"Customer requested appointment on {channel}", f"Booked in-office slot {scheduled_stamp}", "Confirmed", 1.0, 1.0, meta_json))
                else:
                    cur_babu.execute("""
                        INSERT INTO babu_temporal_timeline (event_category, summary, outcome, cause, effect, resolution, impact_score, confidence, metadata)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, ("APPOINTMENT_BOOKED", summary_text, "SUCCESS", f"Customer requested appointment on {channel}", f"Booked in-office slot {scheduled_stamp}", "Confirmed", 1.0, 1.0, meta_json))

                # 2. execution_ledger
                if is_pg_babu:
                    cur_babu.execute("""
                        INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s)
                    """, (session_id, goal_id, f"T-APPT-{followup_id}", "crm", "APPOINTMENT_COMMITTED", meta_json))
                else:
                    cur_babu.execute("""
                        INSERT INTO execution_ledger (session_id, goal_id, task_id, department, event_type, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (session_id, goal_id, f"T-APPT-{followup_id}", "crm", "APPOINTMENT_COMMITTED", meta_json))

                conn_babu.commit()
                cur_babu.close()
                conn_babu.close()
        except Exception as babu_err:
            print(f"[BABU TIMELINE LOG WARNING] {babu_err}", flush=True)

        # 5. Dispatch Telegram Alert to Owner (strictly AFTER DB commit)
        dispatch_telegram_appointment_alert(
            lead_id=lead_id,
            lead_name=lead_name,
            service=service,
            scheduled_date=date_str,
            scheduled_time=time_str,
            contact_info=contact_info,
            channel=channel
        )

        return {
            "status": "SUCCESS",
            "followup_id": followup_id,
            "lead_id": lead_id,
            "scheduled_datetime": scheduled_stamp,
            "lead_name": lead_name,
            "service": service
        }
    except Exception as e:
        err_str = str(e).lower()
        if "unique" in err_str or "integrity" in err_str:
            print(f"[CRM CONCURRENCY COLLISION] Slot {scheduled_stamp} collided in DB: {e}", flush=True)
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except Exception:
                pass
            avail_ok, alt_slots = check_slot_availability(date_str, time_str)
            return {
                "status": "SLOT_CONFLICT",
                "reason": "CONCURRENT_SLOT_COLLISION",
                "alternatives": alt_slots,
                "error": "This slot was just booked by another customer."
            }
        else:
            try:
                conn.rollback()
                cursor.close()
                conn.close()
            except Exception:
                pass
            print(f"[CRM TRANSACTION ERROR] Failed to commit appointment: {e}", flush=True)
            return {"status": "ERROR", "error": str(e)}


def dispatch_telegram_appointment_alert(
    lead_id: str,
    lead_name: str,
    service: str,
    scheduled_date: str,
    scheduled_time: str,
    contact_info: str,
    channel: str
):
    """Send an instant, verified Telegram alert to the business owner."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    owner_chat_id = os.environ.get("TELEGRAM_USER_CHAT_ID")
    if not token or not owner_chat_id:
        print("[CRM TELEGRAM ALERT SKIPPED] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_USER_CHAT_ID", flush=True)
        return

    import html
    text = (
        "🚨 📅 <b>NEW APPOINTMENT SCHEDULED!</b>\n"
        "──────────────────────────────\n"
        f"👤 <b>Customer:</b> {html.escape(str(lead_name))}\n"
        f"💼 <b>Service:</b> <code>{html.escape(str(service))}</code>\n"
        f"🗓️ <b>Date:</b> {html.escape(str(scheduled_date))}\n"
        f"⏰ <b>Time Slot:</b> {html.escape(str(scheduled_time))} (Office: 11 AM - 6 PM)\n"
        f"📞 <b>Contact:</b> <code>{html.escape(str(contact_info))}</code>\n"
        f"🌐 <b>Channel:</b> {html.escape(str(channel))}\n"
        f"🆔 <b>Lead ID:</b> <code>{html.escape(str(lead_id))}</code>\n"
        "──────────────────────────────\n"
        "📍 <i>Venue: Kaushal Market, Rath Road, Orai</i>\n"
        "💡 <i>View and manage in CRM Desk: /crm</i>"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": owner_chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"[CRM TELEGRAM ALERT SENT] Successfully notified owner {owner_chat_id} of appointment for {lead_name}", flush=True)
        else:
            print(f"[CRM TELEGRAM ALERT FAILED] {res.text}", flush=True)
    except Exception as err:
        print(f"[CRM TELEGRAM ALERT ERROR] {err}", flush=True)


# ----------------------------------------------------------------------
# 5. Stateful Lead Retrieval & Funnel State Machine
# ----------------------------------------------------------------------

def get_lead_by_source_ref(source_ref: str) -> Optional[Dict[str, Any]]:
    """Retrieve full conversational state and existing lead history by sender ID."""
    conn, is_pg = get_db_connection()
    if not conn:
        return None

    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("""
                SELECT lead_id, name, channel, contact_info, service_category, status, urgency_score, notes, created_at, updated_at
                FROM babu_leads
                WHERE source_ref = %s
                LIMIT 1
            """, (str(source_ref),))
        else:
            cursor.execute("""
                SELECT lead_id, name, channel, contact_info, service_category, status, urgency_score, notes, created_at, updated_at
                FROM babu_leads
                WHERE source_ref = ?
                LIMIT 1
            """, (str(source_ref),))
            
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return None

        lead_id = row[0]
        
        # Fetch last 5 interactions for conversational memory
        if is_pg:
            cursor.execute("""
                SELECT user_message, assistant_reply, created_at
                FROM babu_interactions
                WHERE lead_id = %s
                ORDER BY interaction_id DESC
                LIMIT 5
            """, (lead_id,))
        else:
            cursor.execute("""
                SELECT user_message, assistant_reply, created_at
                FROM babu_interactions
                WHERE lead_id = ?
                ORDER BY interaction_id DESC
                LIMIT 5
            """, (lead_id,))
            
        history_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        history = [{"user": hr[0], "assistant": hr[1], "time": str(hr[2])[:19]} for hr in reversed(history_rows)]

        return {
            "lead_id": row[0],
            "name": row[1],
            "channel": row[2],
            "contact_info": row[3],
            "service_category": row[4],
            "status": row[5],
            "urgency_score": float(row[6] or 0.5),
            "notes": row[7] or "",
            "history": history
        }
    except Exception as e:
        print(f"[CRM FETCH ERROR] {e}", flush=True)
        return None


def update_lead_funnel_stage(lead_id: str, new_stage: str, notes: Optional[str] = None) -> bool:
    """Update formal funnel stage for a lead."""
    conn, is_pg = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if notes:
            if is_pg:
                cursor.execute("UPDATE babu_leads SET status = %s, notes = %s, updated_at = CURRENT_TIMESTAMP WHERE lead_id = %s", (new_stage, notes, lead_id))
            else:
                cursor.execute("UPDATE babu_leads SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?", (new_stage, notes, lead_id))
        else:
            if is_pg:
                cursor.execute("UPDATE babu_leads SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE lead_id = %s", (new_stage, lead_id))
            else:
                cursor.execute("UPDATE babu_leads SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?", (new_stage, lead_id))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[CRM STAGE ERROR] Failed to update lead stage: {e}", flush=True)
        return False


def get_or_create_lead(source_ref: str, name: str, channel: str = "PUBLIC_TELEGRAM") -> Dict[str, Any]:
    """Retrieve existing lead or create a new initial lead in AWAITING_SERVICE state."""
    existing = get_lead_by_source_ref(source_ref)
    if existing:
        return existing

    conn, is_pg = get_db_connection()
    if not conn:
        return {
            "lead_id": f"LEAD-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}",
            "name": name,
            "channel": channel,
            "contact_info": "",
            "service_category": "Overview",
            "status": "AWAITING_SERVICE",
            "urgency_score": 0.5,
            "notes": "Public Telegram Bot Lead"
        }

    try:
        cursor = conn.cursor()
        date_str = datetime.now().strftime("%Y%m%d")
        rand_suffix = f"{random.randint(1000, 9999)}"
        lead_id = f"LEAD-{date_str}-{rand_suffix}"

        if is_pg:
            cursor.execute("""
                INSERT INTO babu_leads (
                    lead_id, name, channel, contact_info, service_category, 
                    status, urgency_score, estimated_value, notes, source_ref
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (lead_id, name, channel, "", "Overview", "AWAITING_SERVICE", 0.5, 0.0, "Public Telegram Bot Lead", str(source_ref)))
        else:
            cursor.execute("""
                INSERT INTO babu_leads (
                    lead_id, name, channel, contact_info, service_category, 
                    status, urgency_score, estimated_value, notes, source_ref
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (lead_id, name, channel, "", "Overview", "AWAITING_SERVICE", 0.5, 0.0, "Public Telegram Bot Lead", str(source_ref)))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "lead_id": lead_id,
            "name": name,
            "channel": channel,
            "contact_info": "",
            "service_category": "Overview",
            "status": "AWAITING_SERVICE",
            "urgency_score": 0.5,
            "notes": "Public Telegram Bot Lead"
        }
    except Exception as e:
        print(f"[CRM GET_OR_CREATE ERROR] {e}", flush=True)
        return {
            "lead_id": f"LEAD-{datetime.now().strftime('%Y%m%d')}-{random.randint(1000, 9999)}",
            "name": name,
            "channel": channel,
            "contact_info": "",
            "service_category": "Overview",
            "status": "AWAITING_SERVICE",
            "urgency_score": 0.5,
            "notes": ""
        }


def freeze_lead_service(lead_id: str, service_category: str) -> bool:
    """Freeze the selected service category on the lead in CRM and advance stage."""
    conn, is_pg = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("SELECT contact_info FROM babu_leads WHERE lead_id = %s", (lead_id,))
        else:
            cursor.execute("SELECT contact_info FROM babu_leads WHERE lead_id = ?", (lead_id,))
        row = cursor.fetchone()
        has_phone = bool(row and row[0] and re.search(r'\b[6-9]\d{9}\b', str(row[0])))
        new_status = "AWAITING_APPOINTMENT" if has_phone else "AWAITING_CONTACT"

        if is_pg:
            cursor.execute("""
                UPDATE babu_leads 
                SET service_category = %s, status = %s, updated_at = CURRENT_TIMESTAMP 
                WHERE lead_id = %s
            """, (service_category, new_status, lead_id))
        else:
            cursor.execute("""
                UPDATE babu_leads 
                SET service_category = ?, status = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE lead_id = ?
            """, (service_category, new_status, lead_id))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[CRM FREEZE SERVICE] Lead {lead_id} service frozen to {service_category} (New Status: {new_status})", flush=True)
        return True
    except Exception as e:
        print(f"[CRM FREEZE SERVICE ERROR] {e}", flush=True)
        return False


def freeze_lead_contact(lead_id: str, phone: str) -> bool:
    """Freeze validated 10-digit mobile number in CRM and advance stage to AWAITING_APPOINTMENT."""
    conn, is_pg = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        clean_phone = phone.strip()
        match = re.search(r'\b(?:(?:\+91|0)?[6-9]\d{9})\b', clean_phone)
        if match:
            core_10 = match.group(0)[-10:]
            clean_phone = f"+91 {core_10}"

        if is_pg:
            cursor.execute("""
                UPDATE babu_leads 
                SET contact_info = %s, status = 'AWAITING_APPOINTMENT', updated_at = CURRENT_TIMESTAMP 
                WHERE lead_id = %s
            """, (clean_phone, lead_id))
        else:
            cursor.execute("""
                UPDATE babu_leads 
                SET contact_info = ?, status = 'AWAITING_APPOINTMENT', updated_at = CURRENT_TIMESTAMP 
                WHERE lead_id = ?
            """, (clean_phone, lead_id))
        conn.commit()
        cursor.close()
        conn.close()
        print(f"[CRM FREEZE CONTACT] Lead {lead_id} contact frozen to {clean_phone}", flush=True)
        return True
    except Exception as e:
        print(f"[CRM FREEZE CONTACT ERROR] {e}", flush=True)
        return False


REQUIRED_DOCS_BY_SERVICE: Dict[str, str] = {
    "PF": (
        "• आधार कार्ड (चालू मोबाइल नंबर लिंक होना चाहिए)\n"
        "• पैन कार्ड\n"
        "• बैंक पासबुक या कैंसिल चेक (नाम व IFSC साफ़ होना चाहिए)\n"
        "• UAN नंबर एवं पासवर्ड"
    ),
    "Tax": (
        "• फॉर्म 16 / सैलरी स्लिप्स (यदि वेतनभोगी हैं)\n"
        "• पूरे वित्तीय वर्ष का बैंक स्टेटमेंट\n"
        "• पैन कार्ड एवं आधार कार्ड\n"
        "• टैक्स बचत / निवेश के प्रमाण (80C, 80D आदि)"
    ),
    "GST": (
        "• मालिक/पार्टनर का पैन कार्ड एवं आधार कार्ड\n"
        "• व्यापार स्थल का बिजली बिल / किरायानामा / NOC\n"
        "• बैंक खाता विवरण / कैंसिल चेक\n"
        "• पासपोर्ट साइज फोटो एवं व्यापार का नाम"
    ),
    "General": (
        "• आधार कार्ड (मोबाइल लिंक)\n"
        "• पैन कार्ड\n"
        "• संबंधित योजना / आवेदन के आवश्यक विवरण"
    )
}


def extract_lead_intent_and_service(text: str) -> Dict[str, Any]:
    """Analyze inquiry text to detect service category, unsupported flags, contact details, and appointment intent."""
    t_lower = text.lower()
    
    # 0. Check for Unsupported / Out-of-Scope Services
    is_unsupported = any(k in t_lower for k in (
        "aadhaar", "aadhar", "adhar", "uidai", "biometric", "fingerprint", "aadhaar update", "aadhaar correction",
        "ration card", "rashan card", "driving license", "dl renewal", "dl apply", "driving licence",
        "voter card correction", "voter id correction", "voter id update", "birth certificate", "death certificate"
    ))
    
    # 1. 4 Main Categories: PF (Primary), Tax, GST, General (other offered services)
    if is_unsupported:
        service = "Unsupported"
    elif any(k in t_lower for k in (
        "pf", "epf", "epfo", "uan", "pension", "provident", "19", "10c", "31",
        "joint declaration", "claim", "passbook", "ex-employer", "pf related", "pf service"
    )):
        service = "PF"
    elif any(k in t_lower for k in (
        "tax", "itr", "income tax", "tax return", "form 16", "26as", "ais", "tis",
        "tax audit", "tax notice", "tax computation", "tax planning"
    )):
        service = "Tax"
    elif any(k in t_lower for k in ("gst", "gstr", "eway", "e-way", "tax invoice", "lut")):
        service = "GST"
    elif any(k in t_lower for k in (
        "udyam", "msme", "jeevan pramaan", "life certificate", "passport",
        "sevayojan", "pan card", "new pan", "pan apply", "general service", "other service", "csc"
    )):
        service = "General"
    else:
        # Default / Greeting / Unclassified query -> Overview of all 4 categories
        service = "Overview"

    # 2. Appointment Intent & Urgency
    is_appointment = any(k in t_lower for k in ("appointment", "book", "milna", "visit", "aana", "timing", "kab", "office", "consult", "kal", "parso")) and not is_unsupported
    is_urgent = any(k in t_lower for k in ("urgent", "today", "aaj", "notice", "penalty", "last date", "deadline", "emergency", "freeze", "stuck"))
    
    urgency_score = 0.9 if is_urgent else (0.75 if is_appointment else 0.5)

    # 3. Extract Phone / Contact if available
    phone_match = re.search(r'\b(?:(?:\+91|91|0)?[6-9]\d{9})\b', text)
    extracted_contact = phone_match.group(0) if phone_match else None

    return {
        "service_category": service,
        "is_unsupported": is_unsupported,
        "is_appointment": is_appointment,
        "urgency_score": urgency_score,
        "contact_info": extracted_contact
    }


def ingest_lead(
    name: str,
    channel: str,
    user_message: str,
    assistant_reply: str = "",
    contact_info: Optional[str] = None,
    source_ref: Optional[str] = None,
    notes: str = ""
) -> Dict[str, Any]:
    """Ingest or update a business lead in the independent CRM database."""
    extracted = extract_lead_intent_and_service(user_message)
    service_cat = extracted["service_category"]
    urgency = extracted["urgency_score"]
    if not contact_info and extracted["contact_info"]:
        contact_info = extracted["contact_info"]

    conn, is_pg = get_db_connection()
    if not conn:
        return {"status": "ERROR", "error": "Database unavailable"}

    lead_id = None
    is_new = False

    try:
        cursor = conn.cursor()
        
        # Check existing lead by source_ref or contact_info
        existing = None
        if source_ref:
            if is_pg:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE source_ref = %s LIMIT 1", (str(source_ref),))
            else:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE source_ref = ? LIMIT 1", (str(source_ref),))
            existing = cursor.fetchone()

        if not existing and contact_info:
            if is_pg:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE contact_info = %s LIMIT 1", (contact_info,))
            else:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE contact_info = ? LIMIT 1", (contact_info,))
            existing = cursor.fetchone()

        if existing:
            lead_id = existing[0]
            curr_status = existing[2]
            # Advance status from NEW to SERVICE_IDENTIFIED if service is matched
            new_status = "SERVICE_IDENTIFIED" if (curr_status == "NEW" and service_cat not in ("Overview", "Unclassified")) else curr_status
            
            if is_pg:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        status = %s,
                        urgency_score = GREATEST(urgency_score, %s),
                        service_category = CASE WHEN %s NOT IN ('Overview', 'Unclassified') THEN %s ELSE service_category END
                    WHERE lead_id = %s
                """, (new_status, urgency, service_cat, service_cat, lead_id))
            else:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        status = ?,
                        urgency_score = MAX(urgency_score, ?),
                        service_category = CASE WHEN ? NOT IN ('Overview', 'Unclassified') THEN ? ELSE service_category END
                    WHERE lead_id = ?
                """, (new_status, urgency, service_cat, service_cat, lead_id))
        else:
            is_new = True
            date_str = datetime.now().strftime("%Y%m%d")
            rand_suffix = f"{random.randint(1000, 9999)}"
            lead_id = f"LEAD-{date_str}-{rand_suffix}"
            initial_status = "SERVICE_IDENTIFIED" if service_cat not in ("Overview", "Unclassified") else "NEW"

            if is_pg:
                cursor.execute("""
                    INSERT INTO babu_leads (
                        lead_id, name, channel, contact_info, service_category, 
                        status, urgency_score, estimated_value, notes, source_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    lead_id, name, channel, contact_info, service_cat,
                    initial_status, urgency, 0.0, notes, str(source_ref or "")
                ))
            else:
                cursor.execute("""
                    INSERT INTO babu_leads (
                        lead_id, name, channel, contact_info, service_category, 
                        status, urgency_score, estimated_value, notes, source_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    lead_id, name, channel, contact_info, service_cat,
                    initial_status, urgency, 0.0, notes, str(source_ref or "")
                ))

        # Log the interaction record
        meta = json.dumps({
            "extracted_service": service_cat,
            "is_appointment": extracted["is_appointment"],
            "urgency": urgency
        })
        if is_pg:
            cursor.execute("""
                INSERT INTO babu_interactions (
                    lead_id, channel, sender_id, sender_name, user_message, assistant_reply, intent, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (lead_id, channel, str(source_ref or ""), name, user_message, assistant_reply, service_cat, meta))
        else:
            cursor.execute("""
                INSERT INTO babu_interactions (
                    lead_id, channel, sender_id, sender_name, user_message, assistant_reply, intent, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (lead_id, channel, str(source_ref or ""), name, user_message, assistant_reply, service_cat, meta))

        conn.commit()
        cursor.close()
        conn.close()
        print(f"[CRM INGESTION] {'Created new lead' if is_new else 'Updated existing lead'} {lead_id} ({name} | {service_cat})", flush=True)

        return {
            "status": "SUCCESS",
            "lead_id": lead_id,
            "is_new": is_new,
            "service_category": service_cat,
            "urgency_score": urgency
        }
    except Exception as e:
        print(f"[CRM ERROR] Ingestion failed: {e}", flush=True)
        return {"status": "ERROR", "error": str(e)}


# ----------------------------------------------------------------------
# 6. Pipeline Query & Reporting
# ----------------------------------------------------------------------

def get_crm_pipeline_data(limit: int = 50) -> Dict[str, Any]:
    """Retrieve full business pipeline summary, lead counts, and recent prospects."""
    conn, is_pg = get_db_connection()
    if not conn:
        return {
            "summary": {"total_leads": 0, "new": 0, "service_identified": 0, "appointments": 0, "converted": 0, "lost": 0},
            "by_service": {},
            "leads": [],
            "followups": []
        }

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT status, count(*) FROM babu_leads GROUP BY status")
        status_counts = dict(cursor.fetchall())
        
        cursor.execute("SELECT service_category, count(*) FROM babu_leads GROUP BY service_category")
        service_counts = dict(cursor.fetchall())
        
        if is_pg:
            cursor.execute("""
                SELECT lead_id, name, channel, contact_info, service_category, status, urgency_score, estimated_value, notes, created_at, updated_at
                FROM babu_leads
                ORDER BY updated_at DESC
                LIMIT %s
            """, (limit,))
        else:
            cursor.execute("""
                SELECT lead_id, name, channel, contact_info, service_category, status, urgency_score, estimated_value, notes, created_at, updated_at
                FROM babu_leads
                ORDER BY updated_at DESC
                LIMIT ?
            """, (limit,))
            
        lead_rows = cursor.fetchall()
        leads = []
        for r in lead_rows:
            leads.append({
                "lead_id": r[0],
                "name": r[1],
                "channel": r[2],
                "contact_info": r[3] or "Not provided",
                "service_category": r[4],
                "status": r[5],
                "urgency_score": round(float(r[6] or 0.5), 2),
                "estimated_value": float(r[7] or 0.0),
                "notes": r[8] or "",
                "created_at": str(r[9])[:19],
                "updated_at": str(r[10])[:19]
            })

        cursor.execute("""
            SELECT f.followup_id, f.lead_id, l.name, f.scheduled_date, f.proposed_action, f.draft_message, f.status
            FROM babu_followups f
            LEFT JOIN babu_leads l ON f.lead_id = l.lead_id
            WHERE f.status = 'PENDING'
            ORDER BY f.followup_id DESC
            LIMIT 20
        """)
        followups = []
        for f in cursor.fetchall():
            followups.append({
                "followup_id": f[0],
                "lead_id": f[1],
                "name": f[2] or "Customer",
                "scheduled_date": f[3],
                "proposed_action": f[4],
                "draft_message": f[5] or "",
                "status": f[6]
            })

        cursor.close()
        conn.close()

        total = sum(status_counts.values())
        return {
            "summary": {
                "total_leads": total,
                "new": status_counts.get("NEW", 0),
                "service_identified": status_counts.get("SERVICE_IDENTIFIED", 0),
                "appointments": status_counts.get("APPOINTMENT_SCHEDULED", 0),
                "converted": status_counts.get("CONVERTED", 0),
                "lost": status_counts.get("LOST", 0)
            },
            "by_service": service_counts,
            "leads": leads,
            "followups": followups
        }
    except Exception as e:
        print(f"[CRM ERROR] Failed to fetch pipeline data: {e}", flush=True)
        return {
            "summary": {"total_leads": 0, "new": 0, "service_identified": 0, "appointments": 0, "converted": 0, "lost": 0},
            "by_service": {},
            "leads": [],
            "followups": []
        }


def format_telegram_crm_digest() -> str:
    """Format an executive business briefing for Telegram."""
    data = get_crm_pipeline_data(limit=10)
    summ = data["summary"]
    
    lines = [
        "🏢 **ANSHU COMPUTER & TAX CONSULTANCY — CRM DESK**",
        "────────────────────────────────────────",
        f"📊 **Active Pipeline Overview**:",
        f"• Total Client Inquiries: **{summ['total_leads']}**",
        f"• 🆕 New Inquiries: **{summ['new']}**",
        f"• 🔍 Services Identified: **{summ['service_identified']}**",
        f"• 📅 Scheduled Appointments: **{summ['appointments']}**",
        f"• 🤝 Converted Clients: **{summ['converted']}**\n",
        "📂 **Inquiries by Service Category**:"
    ]
    
    if data["by_service"]:
        for svc, cnt in sorted(data["by_service"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {svc}: **{cnt}** lead{'s' if cnt > 1 else ''}")
    else:
        lines.append("  • No categorized leads yet.")
        
    lines.append("\n📋 **Recent Prospects & Appointments**:")
    if data["leads"]:
        for idx, l in enumerate(data["leads"][:5], 1):
            badge = "📅" if l["status"] == "APPOINTMENT_SCHEDULED" else ("🆕" if l["status"] == "NEW" else "⚡")
            lines.append(f"{idx}. {badge} **{l['name']}** ({l['channel']}) — `{l['service_category']}` [{l['status']}]")
            if l['contact_info'] and l['contact_info'] != "Not provided":
                lines.append(f"   📞 Contact: `{l['contact_info']}`")
    else:
        lines.append("No active leads recorded yet.")

    lines.append("\n💡 *Use `/leads` to browse prospects or `/add_lead` to register a client.*")
    return "\n".join(lines)
