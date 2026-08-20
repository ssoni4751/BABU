"""
==============================================================================
Project BABU — Independent Business CRM Subsystem (crm_service.py)
==============================================================================
Dedicated Business Plane for Anshu Computer & Tax Consultancy:
- Lead Ingestion & Deduplication across Facebook, Telegram & Walk-ins
- Pipeline Funnel & Stage Management (NEW, CONTACTED, APPOINTMENT, CONVERTED)
- Interaction History Logging
- Appointment Scheduling & Follow-up Tracker
- 2-Way Google Sheets Synchronization
- Cognition Bridge with BABU Swarm for Lead Intelligence
==============================================================================
"""

import os
import json
import time
import random
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

try:
    from .services import get_db_connection
except ImportError:
    from services import get_db_connection


# ----------------------------------------------------------------------
# 1. Lead Ingestion & Deduplication
# ----------------------------------------------------------------------

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
    is_appointment = any(k in t_lower for k in ("appointment", "book", "milna", "visit", "aana", "timing", "kab", "office", "consult"))
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
    """
    Ingest or update a business lead in the independent CRM database.
    Deduplicates by source_ref (e.g. sender_id) or contact_info.
    """
    extracted = extract_lead_intent_and_service(user_message)
    service_cat = extracted["service_category"]
    urgency = extracted["urgency_score"]
    if not contact_info and extracted["contact_info"]:
        contact_info = extracted["contact_info"]

    conn, is_pg = get_db_connection()
    if not conn:
        print("[CRM ERROR] Database connection unavailable.", flush=True)
        return {"status": "ERROR", "error": "Database unavailable"}

    lead_id = None
    is_new = False

    try:
        cursor = conn.cursor()
        
        # Check existing lead by source_ref or contact_info
        existing = None
        if source_ref:
            if is_pg:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE source_ref = %s LIMIT 1", (source_ref,))
            else:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE source_ref = ? LIMIT 1", (source_ref,))
            existing = cursor.fetchone()

        if not existing and contact_info:
            if is_pg:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE contact_info = %s LIMIT 1", (contact_info,))
            else:
                cursor.execute("SELECT lead_id, name, status, notes FROM babu_leads WHERE contact_info = ? LIMIT 1", (contact_info,))
            existing = cursor.fetchone()

        if existing:
            lead_id = existing[0]
            # Update updated_at and service category if general
            if is_pg:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        urgency_score = GREATEST(urgency_score, %s),
                        service_category = CASE WHEN service_category = 'General' THEN %s ELSE service_category END
                    WHERE lead_id = %s
                """, (urgency, service_cat, lead_id))
            else:
                cursor.execute("""
                    UPDATE babu_leads 
                    SET updated_at = CURRENT_TIMESTAMP,
                        urgency_score = MAX(urgency_score, ?),
                        service_category = CASE WHEN service_category = 'General' THEN ? ELSE service_category END
                    WHERE lead_id = ?
                """, (urgency, service_cat, lead_id))
        else:
            is_new = True
            date_str = datetime.now().strftime("%Y%m%d")
            rand_suffix = f"{random.randint(1000, 9999)}"
            lead_id = f"LEAD-{date_str}-{rand_suffix}"
            initial_status = "APPOINTMENT_SCHEDULED" if extracted["is_appointment"] else "NEW"

            if is_pg:
                cursor.execute("""
                    INSERT INTO babu_leads (
                        lead_id, name, channel, contact_info, service_category, 
                        status, urgency_score, estimated_value, notes, source_ref
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    lead_id, name, channel, contact_info, service_cat,
                    initial_status, urgency, 0.0, notes, source_ref
                ))
            else:
                cursor.execute("""
                    INSERT INTO babu_leads (
                        lead_id, name, channel, contact_info, service_category, 
                        status, urgency_score, estimated_value, notes, source_ref
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    lead_id, name, channel, contact_info, service_cat,
                    initial_status, urgency, 0.0, notes, source_ref
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
            """, (lead_id, channel, source_ref or "", name, user_message, assistant_reply, service_cat, meta))
        else:
            cursor.execute("""
                INSERT INTO babu_interactions (
                    lead_id, channel, sender_id, sender_name, user_message, assistant_reply, intent, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (lead_id, channel, source_ref or "", name, user_message, assistant_reply, service_cat, meta))

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
# 2. Pipeline Query & Reporting
# ----------------------------------------------------------------------

def get_crm_pipeline_data(limit: int = 50) -> Dict[str, Any]:
    """Retrieve full business pipeline summary, lead counts, and recent prospects."""
    conn, is_pg = get_db_connection()
    if not conn:
        return {
            "summary": {"total_leads": 0, "new": 0, "contacted": 0, "appointments": 0, "converted": 0},
            "by_service": {},
            "leads": [],
            "followups": []
        }

    try:
        cursor = conn.cursor()
        
        # 1. Pipeline Funnel Counts
        cursor.execute("SELECT status, count(*) FROM babu_leads GROUP BY status")
        status_counts = dict(cursor.fetchall())
        
        # 2. Service Breakdown
        cursor.execute("SELECT service_category, count(*) FROM babu_leads GROUP BY service_category")
        service_counts = dict(cursor.fetchall())
        
        # 3. Active Leads List
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

        # 4. Pending Follow-ups / Appointments
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
                "contacted": status_counts.get("CONTACTED", 0),
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
            "summary": {"total_leads": 0, "new": 0, "contacted": 0, "appointments": 0, "converted": 0},
            "by_service": {},
            "leads": [],
            "followups": []
        }


def update_lead_stage(lead_id: str, new_status: str, notes: Optional[str] = None) -> bool:
    """Update funnel stage for a lead."""
    conn, is_pg = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        if notes:
            if is_pg:
                cursor.execute("UPDATE babu_leads SET status = %s, notes = %s, updated_at = CURRENT_TIMESTAMP WHERE lead_id = %s", (new_status, notes, lead_id))
            else:
                cursor.execute("UPDATE babu_leads SET status = ?, notes = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?", (new_status, notes, lead_id))
        else:
            if is_pg:
                cursor.execute("UPDATE babu_leads SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE lead_id = %s", (new_status, lead_id))
            else:
                cursor.execute("UPDATE babu_leads SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ?", (new_status, lead_id))
        conn.commit()
        cursor.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[CRM ERROR] Failed to update lead stage: {e}", flush=True)
        return False


# ----------------------------------------------------------------------
# 3. Telegram CRM Executive Formatting
# ----------------------------------------------------------------------

def format_telegram_crm_digest() -> str:
    """Format an executive business briefing for Telegram."""
    data = get_crm_pipeline_data(limit=10)
    summ = data["summary"]
    
    lines = [
        "🏢 **ANSHU COMPUTER & TAX CONSULTANCY — CRM DESK**",
        "────────────────────────────────────────",
        f"📊 **Active Pipeline Overview**:",
        f"• Total Client Inquiries: **{summ['total_leads']}**",
        f"• 🆕 New Unaddressed: **{summ['new']}**",
        f"• 📅 Appointments: **{summ['appointments']}**",
        f"• 🤝 Converted Clients: **{summ['converted']}**\n",
        "📂 **Inquiries by Service Category**:"
    ]
    
    if data["by_service"]:
        for svc, cnt in sorted(data["by_service"].items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  • {svc}: **{cnt}** lead{'s' if cnt > 1 else ''}")
    else:
        lines.append("  • No categorized leads yet.")
        
    lines.append("\n📋 **Recent Prospects**:")
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
