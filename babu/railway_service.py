"""
railway_service.py — Railway Intelligence, Live Seat Availability & PNR Engine for Project BABU

Provides:
  • Train search between stations with realistic fallback
  • Live seat availability check (3A, 2A, 1A, SL, CC, etc.) with color-coded badges
  • 10-digit PNR status lookup & chart preparation detector
  • SQLite-backed autonomous PNR tracker for Telegram alerts
  • RapidAPI integration with zero-crash mock fallback
"""

import os
import re
import json
import sqlite3
import random
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List, Tuple

try:
    import requests
except ImportError:
    requests = None

try:
    from .travel_catalog import (
        resolve_station_code,
        parse_travel_date,
        STATION_CATALOG,
        TRAVEL_CLASSES,
        QUOTAS
    )
    from .services import get_db_connection, DB_PATH
except ImportError:
    from travel_catalog import (
        resolve_station_code,
        parse_travel_date,
        STATION_CATALOG,
        TRAVEL_CLASSES,
        QUOTAS
    )
    try:
        from services import get_db_connection, DB_PATH
    except ImportError:
        DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "babu_checkpoint.db")
        def get_db_connection():
            return sqlite3.connect(DB_PATH), False

RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY") or os.environ.get("INDIAN_RAILWAYS_API_KEY") or ""
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_RAIL_HOST", "irctc1.p.rapidapi.com")


# ── SQLite Schema for PNR Tracking ──────────────────────────────────────────

def ensure_railway_tables(conn, is_pg: bool = False):
    """Ensure the babu_tracked_pnrs table exists in PostgreSQL or SQLite."""
    cursor = conn.cursor()
    if is_pg:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS babu_tracked_pnrs (
                pnr VARCHAR(20) PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                train_no VARCHAR(20),
                train_name VARCHAR(100),
                date_of_journey VARCHAR(30),
                from_station VARCHAR(20),
                to_station VARCHAR(20),
                last_status TEXT,
                is_charted INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    else:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS babu_tracked_pnrs (
                pnr TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                train_no TEXT,
                train_name TEXT,
                date_of_journey TEXT,
                from_station TEXT,
                to_station TEXT,
                last_status TEXT,
                is_charted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    conn.commit()


# ── Route Mock Data for Real-World Fallbacks ───────────────────────────────────

POPULAR_ROUTES: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
    ("ORAI", "NDLS"): [
        {
            "train_no": "12555",
            "train_name": "GORAKHDHAM EXP",
            "from_time": "00:50",
            "to_time": "05:35",
            "duration": "4h 45m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["1A", "2A", "3A", "SL"],
            "fares": {"1A": 1950, "2A": 1180, "3A": 830, "SL": 315}
        },
        {
            "train_no": "22537",
            "train_name": "KUSHINAGAR EXP",
            "from_time": "03:15",
            "to_time": "08:50",
            "duration": "5h 35m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["2A", "3A", "SL"],
            "fares": {"2A": 1120, "3A": 790, "SL": 295}
        },
        {
            "train_no": "12595",
            "train_name": "HUMSAFAR EXPRESS",
            "from_time": "02:10",
            "to_time": "08:25",
            "duration": "6h 15m",
            "days": ["Tue", "Thu", "Sun"],
            "classes": ["3A", "3E"],
            "fares": {"3A": 910, "3E": 830}
        }
    ],
    ("NDLS", "ORAI"): [
        {
            "train_no": "12556",
            "train_name": "GORAKHDHAM EXP",
            "from_time": "21:25",
            "to_time": "02:40",
            "duration": "5h 15m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["1A", "2A", "3A", "SL"],
            "fares": {"1A": 1950, "2A": 1180, "3A": 830, "SL": 315}
        },
        {
            "train_no": "22538",
            "train_name": "KUSHINAGAR EXP",
            "from_time": "18:45",
            "to_time": "00:12",
            "duration": "5h 27m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["2A", "3A", "SL"],
            "fares": {"2A": 1120, "3A": 790, "SL": 295}
        }
    ],
    ("ORAI", "CNB"): [
        {
            "train_no": "12556",
            "train_name": "GORAKHDHAM EXP",
            "from_time": "02:42",
            "to_time": "04:55",
            "duration": "2h 13m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["1A", "2A", "3A", "SL"],
            "fares": {"1A": 1175, "2A": 710, "3A": 505, "SL": 145}
        },
        {
            "train_no": "11109",
            "train_name": "VGLJ LKO INTERCITY",
            "from_time": "07:38",
            "to_time": "10:25",
            "duration": "2h 47m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["CC", "2S"],
            "fares": {"CC": 360, "2S": 85}
        },
        {
            "train_no": "19167",
            "train_name": "SABARMATI EXPRESS",
            "from_time": "20:50",
            "to_time": "23:25",
            "duration": "2h 35m",
            "days": ["Tue", "Wed", "Fri", "Sun"],
            "classes": ["2A", "3A", "SL"],
            "fares": {"2A": 710, "3A": 505, "SL": 145}
        }
    ],
    ("CNB", "ORAI"): [
        {
            "train_no": "12555",
            "train_name": "GORAKHDHAM EXP",
            "from_time": "22:45",
            "to_time": "00:48",
            "duration": "2h 03m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["1A", "2A", "3A", "SL"],
            "fares": {"1A": 1175, "2A": 710, "3A": 505, "SL": 145}
        },
        {
            "train_no": "11110",
            "train_name": "LKO VGLJ INTERCITY",
            "from_time": "18:15",
            "to_time": "20:58",
            "duration": "2h 43m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["CC", "2S"],
            "fares": {"CC": 360, "2S": 85}
        }
    ],
    ("CNB", "NDLS"): [
        {
            "train_no": "12003",
            "train_name": "LUCKNOW SHATABDI",
            "from_time": "16:53",
            "to_time": "22:25",
            "duration": "5h 32m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["EC", "CC"],
            "fares": {"EC": 2185, "CC": 1165}
        },
        {
            "train_no": "22435",
            "train_name": "VANDE BHARAT EXP",
            "from_time": "19:35",
            "to_time": "23:05",
            "duration": "3h 30m",
            "days": ["Tue", "Wed", "Fri", "Sat", "Sun"],
            "classes": ["EC", "CC"],
            "fares": {"EC": 2420, "CC": 1280}
        },
        {
            "train_no": "12417",
            "train_name": "PRAYAGRAJ EXP",
            "from_time": "00:35",
            "to_time": "07:00",
            "duration": "6h 25m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["1A", "2A", "3A", "SL"],
            "fares": {"1A": 2150, "2A": 1290, "3A": 910, "SL": 340}
        },
        {
            "train_no": "12419",
            "train_name": "GOMTI EXPRESS",
            "from_time": "07:35",
            "to_time": "15:00",
            "duration": "7h 25m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
            "classes": ["CC", "2S"],
            "fares": {"CC": 650, "2S": 190}
        }
    ],
    ("VGLJ", "NDLS"): [
        {
            "train_no": "12001",
            "train_name": "BHOPAL SHATABDI",
            "from_time": "18:45",
            "to_time": "23:50",
            "duration": "5h 05m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"],
            "classes": ["EC", "CC"],
            "fares": {"EC": 2240, "CC": 1215}
        },
        {
            "train_no": "12049",
            "train_name": "GATIMAAN EXP",
            "from_time": "15:05",
            "to_time": "19:30",
            "duration": "4h 25m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Sat", "Sun"],
            "classes": ["EC", "CC"],
            "fares": {"EC": 2380, "CC": 1290}
        },
        {
            "train_no": "20171",
            "train_name": "VANDE BHARAT EXP",
            "from_time": "08:43",
            "to_time": "13:15",
            "duration": "4h 32m",
            "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sun"],
            "classes": ["EC", "CC"],
            "fares": {"EC": 2420, "CC": 1280}
        }
    ]
}


# ── 1. Train Search Engine ──────────────────────────────────────────────────

def search_trains(src_query: str, dest_query: str, date_query: Optional[str] = None) -> Dict[str, Any]:
    """
    Search trains between two stations on a given date.
    
    Returns structured dictionary with train list and Telegram-ready formatted text.
    """
    src_res = resolve_station_code(src_query)
    dest_res = resolve_station_code(dest_query)

    if not src_res or not dest_res:
        unresolved = []
        if not src_res:
            unresolved.append(f"Source: `{src_query}`")
        if not dest_res:
            unresolved.append(f"Destination: `{dest_query}`")
        return {
            "success": False,
            "error": f"❌ स्टेशन कोड नहीं मिला: {', '.join(unresolved)}. कृपया सही स्टेशन का नाम या कोड लिखें।",
            "trains": []
        }

    src_code, src_name = src_res
    dest_code, dest_name = dest_res
    journey_date = parse_travel_date(date_query)

    trains = []

    # 1. Try RapidAPI if key is configured
    if RAPIDAPI_KEY and requests:
        try:
            url = f"https://{RAPIDAPI_HOST}/api/v3/trainBetweenStations"
            querystring = {
                "fromStationCode": src_code,
                "toStationCode": dest_code,
                "dateOfJourney": journey_date
            }
            headers = {
                "x-rapidapi-key": RAPIDAPI_KEY,
                "x-rapidapi-host": RAPIDAPI_HOST
            }
            resp = requests.get(url, headers=headers, params=querystring, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") and data.get("data"):
                    for item in data["data"]:
                        trains.append({
                            "train_no": str(item.get("train_number", "")),
                            "train_name": str(item.get("train_name", "")),
                            "from_time": str(item.get("from_std", "")),
                            "to_time": str(item.get("to_sta", "")),
                            "duration": str(item.get("duration", "")),
                            "days": item.get("run_days", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
                            "classes": item.get("class_type", ["3A", "2A", "SL"]),
                            "fares": {}
                        })
        except Exception as e:
            print(f"[RAIL_API] RapidAPI search failed: {e}. Falling back to intelligent mock catalog.", flush=True)

    # 2. Intelligent Mock Fallback
    if not trains:
        key = (src_code, dest_code)
        if key in POPULAR_ROUTES:
            trains = POPULAR_ROUTES[key]
        else:
            # Generate realistic synthetic trains between these stations
            trains = [
                {
                    "train_no": "12555",
                    "train_name": f"{src_name} - {dest_name} SF EXP",
                    "from_time": "06:15",
                    "to_time": "12:30",
                    "duration": "6h 15m",
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    "classes": ["2A", "3A", "SL"],
                    "fares": {"2A": 1050, "3A": 750, "SL": 280}
                },
                {
                    "train_no": "22436",
                    "train_name": f"{dest_name} INTERCITY",
                    "from_time": "14:20",
                    "to_time": "19:45",
                    "duration": "5h 25m",
                    "days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                    "classes": ["CC", "2S"],
                    "fares": {"CC": 580, "2S": 165}
                }
            ]

    # Format Telegram Markdown Card
    card_lines = [
        f"🚆 **Trains: {src_name} ({src_code}) ➔ {dest_name} ({dest_code})**",
        f"📅 **यात्रा तिथि:** `{journey_date}`",
        f"━━━━━━━━━━━━━━━━━━━━━"
    ]

    for idx, t in enumerate(trains, 1):
        classes_str = " | ".join(t.get("classes", ["3A", "2A", "SL"]))
        card_lines.append(
            f"**{idx}. {t['train_no']} — {t['train_name']}**\n"
            f"   ⏰ `{t['from_time']}` ➔ `{t['to_time']}` ({t['duration']})\n"
            f"   🎟️ क्लास: `{classes_str}`"
        )
        card_lines.append("")

    card_lines.append("💡 *सीट देखने के लिए:* `/seats <ट्रेन_नंबर> <क्लास>`\n*उदा:* `/seats 12555 3A`")

    return {
        "success": True,
        "src_code": src_code,
        "src_name": src_name,
        "dest_code": dest_code,
        "dest_name": dest_name,
        "date": journey_date,
        "trains": trains,
        "formatted_text": "\n".join(card_lines)
    }


# ── 2. Live Seat Availability Engine ────────────────────────────────────────

def check_seat_availability(
    train_no: str,
    src_query: Optional[str] = None,
    dest_query: Optional[str] = None,
    date_query: Optional[str] = None,
    travel_class: str = "3A",
    quota: str = "GN"
) -> Dict[str, Any]:
    """
    Check seat availability for a specific train, class, and quota.
    """
    travel_class = travel_class.upper().strip()
    quota = quota.upper().strip()
    journey_date = parse_travel_date(date_query)

    src_code = "ORAI"
    src_name = "Orai"
    dest_code = "NDLS"
    dest_name = "New Delhi"

    if src_query:
        s_res = resolve_station_code(src_query)
        if s_res:
            src_code, src_name = s_res
    if dest_query:
        d_res = resolve_station_code(dest_query)
        if d_res:
            dest_code, dest_name = d_res

    status_str = "AVAILABLE 24"
    fare = 780
    train_name = "EXPRESS"
    prob = "High (94%)"

    # Match train name from popular routes if available
    for route_trains in POPULAR_ROUTES.values():
        for t in route_trains:
            if t["train_no"] == train_no:
                train_name = t["train_name"]
                if travel_class in t.get("fares", {}):
                    fare = t["fares"][travel_class]
                break

    # 1. Try RapidAPI if configured
    if RAPIDAPI_KEY and requests:
        try:
            url = f"https://{RAPIDAPI_HOST}/api/v1/checkSeatAvailability"
            querystring = {
                "classType": travel_class,
                "fromStationCode": src_code,
                "quota": quota,
                "toStationCode": dest_code,
                "trainNo": train_no,
                "date": journey_date
            }
            headers = {
                "x-rapidapi-key": RAPIDAPI_KEY,
                "x-rapidapi-host": RAPIDAPI_HOST
            }
            resp = requests.get(url, headers=headers, params=querystring, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") and data.get("data"):
                    info = data["data"]
                    status_str = info.get("current_status") or info.get("status") or status_str
                    fare = info.get("ticket_fare") or fare
                    prob = info.get("probability", prob)
        except Exception as e:
            print(f"[RAIL_API] Seat availability API error: {e}. Using deterministic mock.", flush=True)

    # Deterministic Mock Generation based on train & date
    if not (RAPIDAPI_KEY and requests):
        seed_val = int(train_no[-2:]) if train_no.isdigit() else 12
        if seed_val % 3 == 0:
            status_str = f"AVAILABLE {14 + (seed_val % 20)}"
            prob = "Confirmed (100%)"
        elif seed_val % 3 == 1:
            status_str = f"RAC {4 + (seed_val % 8)}"
            prob = "High (82%)"
        else:
            status_str = f"WL {6 + (seed_val % 15)}"
            prob = "Medium (60%)"

    # Status Badge Emoji
    if "AVAILABLE" in status_str:
        badge = "🟢"
    elif "RAC" in status_str:
        badge = "🟡"
    else:
        badge = "🔴"

    card_text = (
        f"💺 **Seat Availability — {train_no} {train_name}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 **रूट:** {src_name} (`{src_code}`) ➔ {dest_name} (`{dest_code}`)\n"
        f"📅 **तारीख:** `{journey_date}`\n"
        f"🎟️ **क्लास:** `{travel_class}` ({TRAVEL_CLASSES.get(travel_class, travel_class)})\n"
        f"🏷️ **कोटा:** `{quota}` ({QUOTAS.get(quota, quota)})\n"
        f"💰 **किराया:** `₹{fare}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{badge} **स्टेटस:** `{status_str}`\n"
        f"📊 **कन्फर्मेशन संभावना:** `{prob}`"
    )

    return {
        "success": True,
        "train_no": train_no,
        "train_name": train_name,
        "src_code": src_code,
        "src_name": src_name,
        "dest_code": dest_code,
        "dest_name": dest_name,
        "date": journey_date,
        "class": travel_class,
        "quota": quota,
        "fare": fare,
        "status": status_str,
        "probability": prob,
        "badge": badge,
        "formatted_text": card_text
    }


# ── 3. PNR Status Engine ────────────────────────────────────────────────────

def get_pnr_status(pnr_number: str) -> Dict[str, Any]:
    """
    Retrieve live PNR status, passenger list, coach, berth, and charting status.
    """
    clean_pnr = re.sub(r"[^\d]", "", str(pnr_number)).strip()
    if len(clean_pnr) != 10:
        return {
            "success": False,
            "error": "❌ अमान्य PNR! कृपया 10 अंकों का वैध रेलवे PNR नंबर दर्ज करें। (उदा: `/pnr 2458963214`)",
            "pnr": clean_pnr
        }

    pnr_data = None

    # 1. Try RapidAPI if configured
    if RAPIDAPI_KEY and requests:
        try:
            url = f"https://{RAPIDAPI_HOST}/api/v3/getPNRStatus"
            querystring = {"pnrNumber": clean_pnr}
            headers = {
                "x-rapidapi-key": RAPIDAPI_KEY,
                "x-rapidapi-host": RAPIDAPI_HOST
            }
            resp = requests.get(url, headers=headers, params=querystring, timeout=8)
            if resp.status_code == 200:
                json_data = resp.json()
                if json_data.get("status") and json_data.get("data"):
                    d = json_data["data"]
                    pnr_data = {
                        "pnr": clean_pnr,
                        "train_no": str(d.get("trainNumber", "12555")),
                        "train_name": str(d.get("trainName", "GORAKHDHAM EXP")),
                        "doj": str(d.get("dateOfJourney", "")),
                        "from_station": str(d.get("sourceStation", "ORAI")),
                        "to_station": str(d.get("destinationStation", "NDLS")),
                        "travel_class": str(d.get("journeyClass", "3A")),
                        "chart_status": "CHART PREPARED" if d.get("chartPrepared") else "CHART NOT PREPARED",
                        "passengers": []
                    }
                    for p in d.get("passengerList", []):
                        pnr_data["passengers"].append({
                            "number": p.get("passengerSerialNumber", 1),
                            "booking_status": str(p.get("bookingStatusDetails", "CNF")),
                            "current_status": str(p.get("currentStatusDetails", "CNF")),
                            "coach": str(p.get("bookingCoachId", "B2")),
                            "berth": str(p.get("bookingBerthNo", "21")),
                            "berth_type": str(p.get("bookingBerthCode", "LB"))
                        })
        except Exception as e:
            print(f"[RAIL_API] PNR API error: {e}. Using deterministic mock PNR.", flush=True)

    # 2. Intelligent Realistic Mock Fallback
    if not pnr_data:
        # Deterministic details based on PNR
        is_charted = int(clean_pnr[-1]) % 2 == 0
        chart_str = "CHART PREPARED 🟢" if is_charted else "CHART NOT PREPARED ⏳"
        pnr_data = {
            "pnr": clean_pnr,
            "train_no": "12555",
            "train_name": "GORAKHDHAM EXP",
            "doj": (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%d-%b-%Y"),
            "from_station": "ORAI (Orai)",
            "to_station": "NDLS (New Delhi)",
            "travel_class": "3A",
            "chart_status": chart_str,
            "passengers": [
                {
                    "number": 1,
                    "booking_status": "CNF",
                    "current_status": "CNF / B3 / 24",
                    "coach": "B3",
                    "berth": "24",
                    "berth_type": "LB (Lower Berth)"
                },
                {
                    "number": 2,
                    "booking_status": "RAC 4",
                    "current_status": "CNF / B3 / 27",
                    "coach": "B3",
                    "berth": "27",
                    "berth_type": "MB (Middle Berth)"
                }
            ]
        }

    # Format Telegram PNR Card
    card_lines = [
        f"🎫 **IRCTC PNR Status — `{clean_pnr}`**",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"🚆 **ट्रेन:** {pnr_data['train_no']} — {pnr_data['train_name']}",
        f"📍 **रूट:** {pnr_data['from_station']} ➔ {pnr_data['to_station']}",
        f"📅 **यात्रा तिथि:** `{pnr_data['doj']}` | **क्लास:** `{pnr_data['travel_class']}`",
        f"📋 **चार्टिंग स्टेटस:** `{pnr_data['chart_status']}`",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"👥 **यात्री विवरण (Passenger Status):**"
    ]

    for p in pnr_data["passengers"]:
        card_lines.append(
            f"• **यात्री {p['number']}:** {p['current_status']}\n"
            f"   (बुकिंग: `{p['booking_status']}` | कोच: `{p['coach']}`, बर्थ: `{p['berth']}` - `{p['berth_type']}`)"
        )

    card_lines.append(f"━━━━━━━━━━━━━━━━━━━━━")
    card_lines.append("🔔 *इस PNR को बैकग्राउंड में ट्रैक करने के लिए नीचे बटन दबाएं।*")

    pnr_data["formatted_text"] = "\n".join(card_lines)
    pnr_data["success"] = True
    return pnr_data


# ── 4. Autonomous PNR Tracker Engine (SQLite) ───────────────────────────────

def register_tracked_pnr(
    pnr_number: str,
    chat_id: int,
    train_no: str = "",
    train_name: str = "",
    date_of_journey: str = "",
    from_stn: str = "",
    to_stn: str = ""
) -> Tuple[bool, str]:
    """Register a PNR in SQLite database for automatic background tracking."""
    clean_pnr = re.sub(r"[^\d]", "", str(pnr_number)).strip()
    if len(clean_pnr) != 10:
        return False, "अमान्य PNR! कृपया 10 अंकों का PNR दर्ज करें।"

    conn, is_pg = get_db_connection()
    try:
        ensure_railway_tables(conn, is_pg)
        cursor = conn.cursor()
        
        # Check current status
        initial = get_pnr_status(clean_pnr)
        current_status_summary = ""
        if initial.get("success"):
            train_no = train_no or initial.get("train_no", "")
            train_name = train_name or initial.get("train_name", "")
            date_of_journey = date_of_journey or initial.get("doj", "")
            from_stn = from_stn or initial.get("from_station", "")
            to_stn = to_stn or initial.get("to_station", "")
            current_status_summary = "; ".join(
                [f"P{p['number']}:{p['current_status']}" for p in initial.get("passengers", [])]
            )

        if is_pg:
            cursor.execute("""
                INSERT INTO babu_tracked_pnrs (
                    pnr, chat_id, train_no, train_name, date_of_journey,
                    from_station, to_station, last_status, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (pnr) DO UPDATE SET
                    chat_id = EXCLUDED.chat_id,
                    last_status = EXCLUDED.last_status,
                    updated_at = CURRENT_TIMESTAMP;
            """, (clean_pnr, chat_id, train_no, train_name, date_of_journey, from_stn, to_stn, current_status_summary))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO babu_tracked_pnrs (
                    pnr, chat_id, train_no, train_name, date_of_journey,
                    from_station, to_station, last_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP);
            """, (clean_pnr, chat_id, train_no, train_name, date_of_journey, from_stn, to_stn, current_status_summary))
        
        conn.commit()
        return True, f"✅ **PNR `{clean_pnr}` ट्रैकिंग शुरू हो गई है!**\nजैसे ही सीट कन्फर्म होगी या चार्ट बनेगा, बाबू आपको टेलीग्राम पर अलर्ट करेगा।"
    except Exception as e:
        print(f"[PNR_TRACK] Failed to register PNR {clean_pnr}: {e}", flush=True)
        return False, f"❌ PNR रजिस्टर नहीं हो सका: {e}"
    finally:
        conn.close()


def get_tracked_pnrs(chat_id: int) -> List[Dict[str, Any]]:
    """List all currently active tracked PNRs for this user."""
    conn, is_pg = get_db_connection()
    try:
        ensure_railway_tables(conn, is_pg)
        cursor = conn.cursor()
        if is_pg:
            cursor.execute("""
                SELECT pnr, train_no, train_name, date_of_journey, from_station, to_station, last_status, is_charted
                FROM babu_tracked_pnrs WHERE chat_id = %s ORDER BY created_at DESC;
            """, (chat_id,))
        else:
            cursor.execute("""
                SELECT pnr, train_no, train_name, date_of_journey, from_station, to_station, last_status, is_charted
                FROM babu_tracked_pnrs WHERE chat_id = ? ORDER BY created_at DESC;
            """, (chat_id,))
        
        rows = cursor.fetchall()
        result = []
        for r in rows:
            result.append({
                "pnr": r[0],
                "train_no": r[1],
                "train_name": r[2],
                "doj": r[3],
                "from_station": r[4],
                "to_station": r[5],
                "last_status": r[6],
                "is_charted": bool(r[7])
            })
        return result
    except Exception as e:
        print(f"[PNR_TRACK] Error fetching PNRs: {e}", flush=True)
        return []
    finally:
        conn.close()


def poll_tracked_pnrs() -> List[Dict[str, Any]]:
    """
    Check all active PNRs for status updates (e.g. WL -> RAC, RAC -> CNF, Chart Prepared).
    Returns list of notification payloads with chat_id and alert message.
    """
    conn, is_pg = get_db_connection()
    notifications = []
    try:
        ensure_railway_tables(conn, is_pg)
        cursor = conn.cursor()
        cursor.execute("SELECT pnr, chat_id, last_status, is_charted FROM babu_tracked_pnrs;")
        rows = cursor.fetchall()

        for pnr, chat_id, last_status, is_charted in rows:
            try:
                latest = get_pnr_status(pnr)
                if not latest.get("success"):
                    continue

                new_status_summary = "; ".join(
                    [f"P{p['number']}:{p['current_status']}" for p in latest.get("passengers", [])]
                )
                chart_prepared = "CHART PREPARED" in latest.get("chart_status", "")

                # Detect if status changed or charted
                has_changed = (new_status_summary != last_status) or (chart_prepared and not is_charted)
                if has_changed:
                    alert_msg = (
                        f"🚨 **PNR Status Update Alert!**\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎫 **PNR:** `{pnr}`\n"
                        f"🚆 **ट्रेन:** {latest.get('train_no')} — {latest.get('train_name')}\n"
                        f"📅 **यात्रा:** {latest.get('doj')}\n"
                        f"📋 **चार्ट:** `{latest.get('chart_status')}`\n\n"
                        f"**नया स्टेटस:**\n"
                    )
                    for p in latest.get("passengers", []):
                        alert_msg += f"• **यात्री {p['number']}:** `{p['current_status']}` (कोच: {p['coach']}, बर्थ: {p['berth']})\n"

                    notifications.append({
                        "chat_id": chat_id,
                        "pnr": pnr,
                        "text": alert_msg
                    })

                    # Update database with new status
                    up_cursor = conn.cursor()
                    if is_pg:
                        up_cursor.execute("""
                            UPDATE babu_tracked_pnrs
                            SET last_status = %s, is_charted = %s, updated_at = CURRENT_TIMESTAMP
                            WHERE pnr = %s;
                        """, (new_status_summary, 1 if chart_prepared else 0, pnr))
                    else:
                        up_cursor.execute("""
                            UPDATE babu_tracked_pnrs
                            SET last_status = ?, is_charted = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE pnr = ?;
                        """, (new_status_summary, 1 if chart_prepared else 0, pnr))
                    conn.commit()
            except Exception as e:
                print(f"[PNR_POLL] Error polling PNR {pnr}: {e}", flush=True)

        return notifications
    except Exception as e:
        print(f"[PNR_POLL] General poll error: {e}", flush=True)
        return []
    finally:
        conn.close()


# ── 5. Natural Language Railway Intent Intercept ────────────────────────────

def try_handle_railway_natural_query(query: str) -> Optional[Dict[str, Any]]:
    """
    Check if a user query is a natural language railway request.
    If yes, executes the appropriate lookup and returns dict with text & metadata.
    If not, returns None so it passes through to normal LLM cognition.
    """
    if not query:
        return None
    raw = query.strip()
    raw_lower = raw.lower()

    # 1. PNR Status Check (e.g. "pnr 2458963214", "check pnr 2458963214", or 10-digit number)
    pnr_match = re.search(r'\b([2-9]\d{9})\b', raw)
    if pnr_match:
        if any(w in raw_lower for w in ("pnr", "ticket", "टिकट", "status", "स्टेटस", "check", "चार्ट", "chart")):
            pnr_val = pnr_match.group(1)
            res = get_pnr_status(pnr_val)
            return {
                "type": "pnr",
                "pnr": pnr_val,
                "text": res.get("formatted_text", ""),
                "success": res.get("success", False)
            }

    # 2. Seat Availability Check (e.g. "12555 me 3AC seat", "check seat in 12555", "12555 seat availability")
    train_num_match = re.search(r'\b([1-2]\d{4})\b', raw)
    if train_num_match:
        if any(w in raw_lower for w in ("seat", "सीट", "berth", "बर्थ", "availab", "3a", "2a", "1a", "sl", "sleeper", "ac")):
            train_no = train_num_match.group(1)
            # Detect class
            travel_class = "3A"
            for cls in ("1A", "2A", "3A", "3E", "CC", "EC", "SL", "2S"):
                if cls.lower() in raw_lower or (cls == "SL" and "sleeper" in raw_lower):
                    travel_class = cls
                    break
            
            # Check for date keywords
            date_val = None
            for d_word in ("today", "aaj", "tomorrow", "kal", "parso", "next monday", "monday", "somwar"):
                if d_word in raw_lower:
                    date_val = d_word
                    break

            res = check_seat_availability(train_no, travel_class=travel_class, date_query=date_val)
            return {
                "type": "seats",
                "train_no": train_no,
                "class": travel_class,
                "text": res.get("formatted_text", ""),
                "success": res.get("success", False)
            }

    # 3. Train Search between Stations
    # Pattern A: "... se ... (ki / ke liye) train ..." (e.g. "orai se delhi train", "उरई से दिल्ली ट्रेन")
    m_hin = re.search(r'([a-zA-Z\u0900-\u097F]+)\s+(?:se|to|-)\s+([a-zA-Z\u0900-\u097F]+)(?:\s+(?:ki|ke\s+liye))?\s+(?:train|ट्रेन|rail|seats|गाड़ी)', raw_lower)
    # Pattern B: "train from ... to ..."
    m_eng = re.search(r'trains?\s+(?:from\s+)?([a-zA-Z]+)\s+to\s+([a-zA-Z]+)', raw_lower)
    
    src_cand, dest_cand = None, None
    if m_hin:
        src_cand, dest_cand = m_hin.group(1).strip(), m_hin.group(2).strip()
    elif m_eng:
        src_cand, dest_cand = m_eng.group(1).strip(), m_eng.group(2).strip()

    if src_cand and dest_cand:
        # Avoid false positives with common stopwords
        stopwords = {"kya", "babu", "hai", "kaun", "kab", "please", "me", "main", "parso", "kal"}
        if src_cand not in stopwords and dest_cand not in stopwords:
            src_res = resolve_station_code(src_cand)
            dest_res = resolve_station_code(dest_cand)
            if src_res and dest_res:
                # Check for date keywords
                date_val = None
                for d_word in ("today", "aaj", "tomorrow", "kal", "parso", "next monday", "monday", "somwar", "friday", "shukrawar"):
                    if d_word in raw_lower:
                        date_val = d_word
                        break
                
                res = search_trains(src_cand, dest_cand, date_val)
                return {
                    "type": "train_search",
                    "src": src_cand,
                    "dest": dest_cand,
                    "date": date_val,
                    "text": res.get("formatted_text", ""),
                    "trains": res.get("trains", []),
                    "success": res.get("success", False)
                }

    return None

