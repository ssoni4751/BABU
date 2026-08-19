"""
BABU Deep Temporal Reasoning Engine — Cognitive OS Layer 4 & Layer 5
Provides causal timeline analysis, Indian statutory compliance calendar awareness,
and temporal trajectory injection for Strategic Planning (L4) and Analysis (L5).
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

def get_current_ist_datetime() -> datetime:
    """Return the current datetime localized to Indian Standard Time (IST / UTC+5:30)."""
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def get_indian_compliance_calendar(ref_dt: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Evaluate Indian statutory tax, GST, PF, and corporate compliance deadlines
    relative to a given date in IST.
    """
    dt = ref_dt or get_current_ist_datetime()
    year = dt.year
    month = dt.month
    day = dt.day
    
    # Financial Year & Assessment Year Calculation (India: 1 April - 31 March)
    if month >= 4:
        fy = f"{year}-{str(year + 1)[-2:]}"
        ay = f"{year + 1}-{str(year + 2)[-2:]}"
    else:
        fy = f"{year - 1}-{str(year)[-2:]}"
        ay = f"{year}-{str(year + 1)[-2:]}"
        
    # Standard Statutory Recurring Monthly Deadlines
    monthly_deadlines = [
        {"day": 7, "type": "TDS/TCS", "description": "TDS/TCS deposit for previous month", "portal": "Income Tax"},
        {"day": 11, "type": "GST", "description": "GSTR-1 Outward Supplies return for previous month", "portal": "GST Portal"},
        {"day": 13, "type": "GST", "description": "GSTR-1 IFF (Invoice Furnishing Facility) for QRMP taxpayers", "portal": "GST Portal"},
        {"day": 15, "type": "EPFO", "description": "EPF Electronic Challan cum Return (ECR) & Payment", "portal": "EPFO Unified Portal"},
        {"day": 15, "type": "ESIC", "description": "ESIC monthly contribution deposit", "portal": "ESIC Portal"},
        {"day": 20, "type": "GST", "description": "GSTR-3B Summary Return & Tax Payment for previous month", "portal": "GST Portal"},
        {"day": 30, "type": "Income Tax", "description": "TDS Certificate issuance (Form 16B/16C/16D) if applicable", "portal": "TRACES"}
    ]
    
    # Upcoming Deadlines this month
    upcoming = []
    for d in monthly_deadlines:
        due_date_str = f"{year}-{month:02d}-{d['day']:02d}"
        days_left = d['day'] - day
        if days_left >= 0:
            upcoming.append({
                "due_date": due_date_str,
                "days_remaining": days_left,
                "category": d["type"],
                "description": d["description"],
                "urgency": "HIGH" if days_left <= 3 else ("MEDIUM" if days_left <= 7 else "NORMAL")
            })
            
    # Major Annual / Quarterly Benchmarks
    annual_benchmarks = [
        {"month": 6, "day": 15, "event": "Advance Tax 1st Installment (15%)"},
        {"month": 7, "day": 31, "event": "Non-Audit Income Tax Return (ITR) Filing Deadline for Individuals/Salaried/HUF"},
        {"month": 9, "day": 15, "event": "Advance Tax 2nd Installment (45%)"},
        {"month": 10, "day": 31, "event": "Audit Cases ITR & Tax Audit Report (TAR Form 3CA/3CB-3CD) Deadline"},
        {"month": 12, "day": 15, "event": "Advance Tax 3rd Installment (75%)"},
        {"month": 12, "day": 31, "event": "Belated / Revised ITR Filing Deadline for previous FY"},
        {"month": 3, "day": 15, "event": "Advance Tax 4th Installment (100%)"},
        {"month": 3, "day": 31, "event": "Financial Year Closing & Tax Planning Horizon"}
    ]
    
    active_annual = []
    for b in annual_benchmarks:
        try:
            bench_date = datetime(year, b["month"], b["day"])
            days_diff = (bench_date.date() - dt.date()).days
            if 0 <= days_diff <= 60:
                active_annual.append({
                    "event": b["event"],
                    "date": bench_date.strftime("%d %B %Y"),
                    "days_remaining": days_diff,
                    "urgency": "CRITICAL" if days_diff <= 7 else "UPCOMING"
                })
        except Exception:
            pass

    return {
        "current_date": dt.strftime("%Y-%m-%d"),
        "formatted_date": dt.strftime("%A, %d %B %Y"),
        "current_time_ist": dt.strftime("%I:%M %p IST"),
        "financial_year": f"FY {fy}",
        "assessment_year": f"AY {ay}",
        "day_of_month": day,
        "upcoming_monthly_deadlines": upcoming[:5],
        "active_annual_milestones": active_annual
    }

def get_causal_temporal_context(query: str, lookback_days: int = 14) -> List[Dict[str, Any]]:
    """
    Surgically retrieve high-impact causal timeline events (causes, effects, resolutions)
    from babu_temporal_timeline to prevent recurring anti-patterns and inform planning.
    """
    try:
        from services import get_db_connection
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        now_ist = get_current_ist_datetime()
        cutoff_date = (now_ist - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        
        if is_pg:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, cause, effect, resolution, impact_score
                FROM babu_temporal_timeline
                WHERE DATE(timestamp) >= %s
                ORDER BY event_id DESC
                LIMIT 10
            """, (cutoff_date,))
        else:
            cursor.execute("""
                SELECT event_id, timestamp, event_category, summary, outcome, cause, effect, resolution, impact_score
                FROM babu_temporal_timeline
                WHERE DATE(timestamp) >= ?
                ORDER BY event_id DESC
                LIMIT 10
            """, (cutoff_date,))
            
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
        
        causal_events = []
        for r in rows:
            ev_id, ts, cat, summ, out, cause, effect, resol, score = r
            causal_events.append({
                "id": ev_id,
                "date": str(ts)[:10],
                "category": cat,
                "summary": summ,
                "outcome": out,
                "cause": cause,
                "effect": effect,
                "resolution": resol,
                "impact_score": score or 1.0
            })
        return causal_events
    except Exception as e:
        print(f"[TEMPORAL REASONER ERROR] Failed to fetch causal context: {e}", flush=True)
        return []

def synthesize_temporal_reasoning_packet(query: str) -> Dict[str, Any]:
    """
    Synthesize a rich Temporal Reasoning Packet for Planner (L4) and Analysis (L5).
    Contains live calendar anchoring, statutory deadlines, and historical causal patterns.
    """
    compliance = get_indian_compliance_calendar()
    causal_history = get_causal_temporal_context(query, lookback_days=14)
    
    # Check if query is compliance/timeline sensitive
    q_lower = query.lower()
    is_compliance_query = any(k in q_lower for k in ("gst", "itr", "tax", "pf", "epfo", "deadline", "due date", "challan", "return", "advance tax", "ecr", "tar"))
    is_retrospective = any(k in q_lower for k in ("yesterday", "last week", "trend", "history", "previous", "earlier", "progress", "kal", "beeta"))
    
    temporal_summary_lines = [
        f"Temporal Anchor: {compliance['formatted_date']} ({compliance['current_time_ist']}) | {compliance['financial_year']} ({compliance['assessment_year']})"
    ]
    
    if is_compliance_query and compliance["upcoming_monthly_deadlines"]:
        temporal_summary_lines.append("Active Compliance Deadlines:")
        for d in compliance["upcoming_monthly_deadlines"][:3]:
            temporal_summary_lines.append(f"  • {d['category']} [{d['due_date']} - {d['days_remaining']} days left]: {d['description']}")
            
    if compliance["active_annual_milestones"]:
        temporal_summary_lines.append("Key Annual Milestones:")
        for m in compliance["active_annual_milestones"][:2]:
            temporal_summary_lines.append(f"  • {m['event']} [{m['date']} - {m['days_remaining']} days left]")
            
    if causal_history and (is_retrospective or len(causal_history) > 0):
        temporal_summary_lines.append(f"Recent Timeline Events ({len(causal_history)} logged in last 14d):")
        for ev in causal_history[:3]:
            res_str = f" (Resolution: {ev['resolution']})" if ev.get("resolution") else ""
            temporal_summary_lines.append(f"  • [{ev['date']}] {ev['category']}: {ev['summary']} -> {ev['outcome']}{res_str}")

    return {
        "calendar": compliance,
        "causal_history": causal_history,
        "is_compliance_sensitive": is_compliance_query,
        "is_retrospective": is_retrospective,
        "temporal_context_str": "\n".join(temporal_summary_lines)
    }
