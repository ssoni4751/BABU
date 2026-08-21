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

def get_selective_knowledge_slice(service_category: str) -> str:
    """Return only service-relevant business facts and document requirements."""
    general_header = (
        "Business: Anshu Computer & Tax Consultancy\n"
        "Office Address: Kaushal Market, Rath Road, Orai, Uttar Pradesh, India\n"
        "Working Hours: Monday to Saturday from 11:00 AM to 6:00 PM (Sunday Closed)\n"
        "WhatsApp / Call: +91 7217646673\n"
    )
    
    svc = (service_category or "General").upper()
    
    if "PF" in svc:
        return (
            f"{general_header}\n"
            "Service: PF Consultancy & Dispute Resolution\n"
            "Scope: PF Claim Settlement (Form 19, 10C, 31 Advance), UAN Consolidation, KYC/Name/DOB Correction, Joint Declaration, Ex-Employer disputes.\n"
            "Required Documents for PF:\n"
            "1. Aadhaar Card (linked with registered mobile for OTP)\n"
            "2. PAN Card\n"
            "3. Bank Passbook or Cancelled Cheque (with clear account & IFSC)\n"
            "4. UAN Number\n"
            "Pricing/Guidance: Highly affordable transparent charges based on case complexity."
        )
    elif "ITR" in svc or "TAX" in svc or "INCOME" in svc:
        return (
            f"{general_header}\n"
            "Service: Income Tax Return (ITR) Filing & Tax Advisory\n"
            "Scope: Salaried/Business ITR Filing, Tax Computation, AIS/TIS Review, Tax Notice Assistance.\n"
            "Required Documents for ITR:\n"
            "1. Form 16 (for salaried individuals)\n"
            "2. Bank Statements for the Financial Year\n"
            "3. PAN Card & Aadhaar Card\n"
            "4. Investment & Tax-saving proofs (80C, 80D, etc.)\n"
            "Pricing/Guidance: Fast same-day computation and verified e-verification."
        )
    elif "GST" in svc:
        return (
            f"{general_header}\n"
            "Service: GST Registration & Monthly Compliance\n"
            "Scope: New GST Registration, Monthly GSTR-1 & GSTR-3B Filings, Annual Returns, GST Notice Assistance.\n"
            "Required Documents for GST:\n"
            "1. PAN Card & Aadhaar Card of Proprietor/Partners\n"
            "2. Electricity Bill / Rent Agreement for Business Address\n"
            "3. Bank Account Details / Cancelled Cheque\n"
            "4. Passport-size Photo"
        )
    elif "PAN" in svc or "DOCUMENT" in svc or "CSC" in svc:
        return (
            f"{general_header}\n"
            "Service: PAN Card & Digital Documentation Services\n"
            "Scope: New PAN Card, PAN-Aadhaar Linking, Jeevan Pramaan (Life Certificate for Pensioners), Digital Citizen Services.\n"
            "Required Documents: Aadhaar Card with linked mobile number and 2 passport photos."
        )
    else:
        return (
            f"{general_header}\n"
            "Services Offered: 1) PF Consultancy (Claims/KYC/UAN), 2) Income Tax Filing (ITR), 3) GST Registration & Filing, 4) Digital/CSC Services.\n"
            "Appointment Policy: In-office visits at Kaushal Market, Orai between 11:00 AM and 6:00 PM (Mon-Sat)."
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
    Validates working window: Mon-Sat, 11:00 AM <= time <= 6:00 PM (18:00).
    """
    if not base_dt:
        base_dt = datetime.now(timezone.utc).astimezone(IST)
    
    date_clean = (date_text or "").strip().lower()
    time_clean = (time_text or "").strip().lower()
    
    if not date_clean and not time_clean:
        return {"valid": False, "reason": "NO_DATETIME_PROVIDED"}

    target_date = None
    target_time_str = None
    
    # 1. Parse Date Expression
    if any(k in date_clean for k in ("aaj", "today")):
        target_date = base_dt.date()
    elif any(k in date_clean for k in ("kal", "tomorrow")):
        target_date = (base_dt + timedelta(days=1)).date()
    elif any(k in date_clean for k in ("parso", "day after tomorrow", "day after")):
        target_date = (base_dt + timedelta(days=2)).date()
    else:
        # Weekday matching (e.g. somwar, monday, friday, etc.)
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
            if k in date_clean:
                matched_wd = v
                break
        
        if matched_wd is not None:
            curr_wd = base_dt.weekday()
            days_ahead = matched_wd - curr_wd
            if days_ahead <= 0:
                days_ahead += 7
            target_date = (base_dt + timedelta(days=days_ahead)).date()
        else:
            # Try ISO or standard numeric dates (YYYY-MM-DD or DD/MM/YYYY or DD-MM-YYYY)
            date_match = re.search(r'\b(\d{1,4})[/\-\.](\d{1,2})[/\-\.](\d{1,4})\b', date_clean)
            if date_match:
                p1, p2, p3 = date_match.groups()
                try:
                    if len(p1) == 4: # YYYY-MM-DD
                        target_date = datetime(int(p1), int(p2), int(p3)).date()
                    else: # DD-MM-YYYY
                        target_date = datetime(int(p3), int(p2), int(p1)).date()
                except Exception:
                    pass

    # Default to today if date not specified but time is specified
    if not target_date:
        if time_clean:
            target_date = base_dt.date()
        else:
            return {"valid": False, "reason": "UNRECOGNIZED_DATE"}

    # 2. Parse Time Expression (e.g. "2 baje", "2 pm", "14:00", "11:30 am", "shaam 4 baje", "6:00 pm")
    hour = None
    minute = 0
    
    match_time = re.search(r'\b(\d{1,2})(?::([0-5]\d))?\s*(am|pm|baje)?\b', time_clean)
    if match_time:
        raw_h = int(match_time.group(1))
        raw_m = int(match_time.group(2)) if match_time.group(2) else 0
        
        is_pm = "pm" in time_clean or any(k in time_clean for k in ("shaam", "dopehar", "afternoon", "evening"))
        is_am = "am" in time_clean
        
        if is_am:
            hour = 0 if raw_h == 12 else raw_h
        elif is_pm:
            hour = 12 if raw_h == 12 else (raw_h + 12 if raw_h < 12 else raw_h)
        elif raw_h in (1, 2, 3, 4, 5, 6):
            # In Indian business context, 1-6 without AM means 1 PM - 6 PM
            hour = raw_h + 12
        else:
            hour = raw_h
        minute = raw_m

    if hour is None:
        hour = 12
        minute = 0

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
        
        # 1. Update lead status in babu_leads
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

        # 2. Insert into babu_followups
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

        # 4. Dispatch Telegram Alert to Owner (strictly AFTER DB commit)
        lead_name = lead_row[0] if lead_row else "Client"
        channel = lead_row[1] if lead_row else "Facebook"
        contact_info = lead_row[2] if lead_row and lead_row[2] else "Not provided"
        service = lead_row[3] if lead_row else "Tax/PF Consultation"

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

    text = (
        "🚨 📅 **NEW APPOINTMENT SCHEDULED!**\n"
        "──────────────────────────────\n"
        f"👤 **Customer:** {lead_name}\n"
        f"💼 **Service:** `{service}`\n"
        f"🗓️ **Date:** {scheduled_date}\n"
        f"⏰ **Time Slot:** {scheduled_time} (Office: 11 AM - 6 PM)\n"
        f"📞 **Contact:** `{contact_info}`\n"
        f"🌐 **Channel:** {channel}\n"
        f"🆔 **Lead ID:** `{lead_id}`\n"
        "──────────────────────────────\n"
        "📍 *Venue: Kaushal Market, Rath Road, Orai*\n"
        "💡 *View and manage in CRM Desk: /crm*"
    )
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": owner_chat_id,
        "text": text,
        "parse_mode": "Markdown"
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


def extract_lead_intent_and_service(text: str) -> Dict[str, Any]:
    """Analyze inquiry text to detect service category, contact details, and appointment intent."""
    t_lower = text.lower()
    
    # 1. Service Category
    if any(k in t_lower for k in ("gst", "gstr", "eway", "e-way", "tax invoice", "lut")):
        service = "GST"
    elif any(k in t_lower for k in ("itr", "income tax", "tax return", "form 16", "26as", "ais", "tis", "tax audit")):
        service = "ITR"
    elif any(k in t_lower for k in ("pf", "epf", "epfo", "uan", "pension", "provident", "19", "10c", "31")):
        service = "PF"
    elif any(k in t_lower for k in ("pan", "tan", "aadhaar", "pan card")):
        service = "PAN/Documentation"
    elif any(k in t_lower for k in ("account", "tally", "bookkeep", "ledger", "balance sheet")):
        service = "Accounting"
    else:
        service = "General"

    # 2. Appointment Intent & Urgency
    is_appointment = any(k in t_lower for k in ("appointment", "book", "milna", "visit", "aana", "timing", "kab", "office", "consult", "kal", "parso"))
    is_urgent = any(k in t_lower for k in ("urgent", "today", "aaj", "notice", "penalty", "last date", "deadline", "emergency", "freeze", "stuck"))
    
    urgency_score = 0.9 if is_urgent else (0.75 if is_appointment else 0.5)

    # 3. Extract Phone / Contact if available
    phone_match = re.search(r'\b(?:(?:\+91|91|0)?[6-9]\d{9})\b', text)
    extracted_contact = phone_match.group(0) if phone_match else None

    return {
        "service_category": service,
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
            new_status = "SERVICE_IDENTIFIED" if (curr_status == "NEW" and service_cat != "General") else curr_status
            
            if is_pg:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        status = %s,
                        urgency_score = GREATEST(urgency_score, %s),
                        service_category = CASE WHEN service_category = 'General' THEN %s ELSE service_category END
                    WHERE lead_id = %s
                """, (new_status, urgency, service_cat, lead_id))
            else:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        status = ?,
                        urgency_score = MAX(urgency_score, ?),
                        service_category = CASE WHEN service_category = 'General' THEN ? ELSE service_category END
                    WHERE lead_id = ?
                """, (new_status, urgency, service_cat, lead_id))
        else:
            is_new = True
            date_str = datetime.now().strftime("%Y%m%d")
            rand_suffix = f"{random.randint(1000, 9999)}"
            lead_id = f"LEAD-{date_str}-{rand_suffix}"
            initial_status = "SERVICE_IDENTIFIED" if service_cat != "General" else "NEW"

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
