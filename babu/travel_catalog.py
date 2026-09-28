"""
travel_catalog.py — Railway Station Directory & Fuzzy Matcher for Project BABU

Provides comprehensive station code lookup, regional priority mapping (Bundelkhand & UP),
fuzzy station resolution, date parsing in English and Hindi, and railway class definitions.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Dict, List

# Primary Railway Stations Catalog (Code -> (Full English Name, Hindi Name, City/Aliases))
STATION_CATALOG: Dict[str, Tuple[str, str, List[str]]] = {
    # Bundelkhand & Uttar Pradesh Arterial Stations
    "ORAI": ("Orai", "उरई", ["orai", "urai"]),
    "CNB": ("Kanpur Central", "कानपुर सेंट्रल", ["kanpur", "cawnpore", "kanpur central", "cnb"]),
    "VGLJ": ("Virangana Lakshmibai Jhansi", "वीरांगना लक्ष्मीबाई झाँसी", ["jhansi", "vglj", "virangana lakshmibai", "jhansi jn"]),
    "LKO": ("Lucknow Charbagh NR", "लखनऊ चारबाग", ["lucknow", "lko", "charbagh", "lucknow charbagh"]),
    "LJN": ("Lucknow Junction NER", "लखनऊ जंक्शन", ["ljn", "lucknow jn", "lucknow junction"]),
    "BNDA": ("Banda Jn", "बाँदा जंक्शन", ["banda", "bnda", "baanda"]),
    "PRYJ": ("Prayagraj Jn", "प्रयागराज जंक्शन", ["prayagraj", "allahabad", "pryj", "ald"]),
    "GWL": ("Gwalior Jn", "ग्वालियर जंक्शन", ["gwalior", "gwl"]),
    "BPL": ("Bhopal Jn", "भोपाल जंक्शन", ["bhopal", "bpl"]),
    "RKMP": ("Rani Kamlapati", "रानी कमलापति", ["habibganj", "rkmp", "rani kamlapati"]),
    "AGC": ("Agra Cantt", "आगरा कैंट", ["agra", "agra cantt", "agc"]),
    "AF": ("Agra Fort", "आगरा फोर्ट", ["agra fort", "af"]),
    "ETW": ("Etawah Jn", "इटावा जंक्शन", ["etawah", "etw"]),
    "ALJN": ("Aligarh Jn", "अलीगढ़ जंक्शन", ["aligarh", "aljn"]),
    "MZP": ("Mirzapur", "मिर्ज़ापुर", ["mirzapur", "mzp"]),
    "BSB": ("Varanasi Jn", "वाराणसी जंक्शन", ["varanasi", "banaras", "bsb", "kashi"]),
    "DDU": ("Pt Deen Dayal Upadhyaya Jn", "पं दीन दयाल उपाध्याय जंक्शन", ["mughalsarai", "ddu", "deen dayal upadhyaya"]),
    "GKP": ("Gorakhpur Jn", "गोरखपुर जंक्शन", ["gorakhpur", "gkp"]),
    "BE": ("Bareilly Jn", "बरेली जंक्शन", ["bareilly", "be"]),
    "MB": ("Moradabad Jn", "मुरादाबाद जंक्शन", ["moradabad", "mb"]),
    "MTC": ("Meerut City", "मेरठ सिटी", ["meerut", "mtc"]),
    "SRE": ("Saharanpur Jn", "सहारनपुर जंक्शन", ["saharanpur", "sre"]),
    "TDL": ("Tundla Jn", "टूंडला जंक्शन", ["tundla", "tdl"]),
    "FTP": ("Fatehpur", "फ़तेहपुर", ["fatehpur", "ftp"]),
    "MKP": ("Manikpur Jn", "मानिकपुर जंक्शन", ["manikpur", "mkp"]),
    "CKTD": ("Chitrakutdham Karwi", "चित्रकूटधाम कर्वी", ["chitrakoot", "karwi", "cktd", "chitrakootdham"]),
    "MAKR": ("Bina Jn", "बीना जंक्शन", ["bina", "bina jn"]),
    "LAL": ("Lalitpur Jn", "ललितपुर जंक्शन", ["lalitpur", "lal"]),
    "MABA": ("Mahoba Jn", "महोबा जंक्शन", ["mahoba", "maba"]),
    "HPP": ("Harpalpur", "हरपालपुर", ["harpalpur", "hpp"]),
    "KURJ": ("Khajuraho", "खजुराहो", ["khajuraho", "kurj"]),
    "KLAR": ("Kulpahar", "कुलपहाड़", ["kulpahar", "klar"]),

    # Delhi NCR Hubs
    "NDLS": ("New Delhi", "नई दिल्ली", ["delhi", "new delhi", "ndls", "dilli"]),
    "DLI": ("Old Delhi Jn", "पुरानी दिल्ली जंक्शन", ["old delhi", "dli"]),
    "NZM": ("Hazrat Nizamuddin", "हज़रत निज़ामुद्दीन", ["nizamuddin", "nzm", "hazrat nizamuddin"]),
    "ANVT": ("Anand Vihar Terminal", "आनंद विहार टर्मिनल", ["anand vihar", "anvt"]),
    "DEE": ("Delhi Sarai Rohilla", "दिल्ली सराय रोहिल्ला", ["sarai rohilla", "dee"]),
    "GZB": ("Ghaziabad Jn", "गाज़ियाबाद जंक्शन", ["ghaziabad", "gzb"]),

    # Major Metros & National Hubs
    "BCT": ("Mumbai Central", "मुंबई सेंट्रल", ["mumbai", "mumbai central", "bct", "bombay"]),
    "MMCT": ("Mumbai Central", "मुंबई सेंट्रल", ["mmct"]),
    "CSMT": ("Chhatrapati Shivaji Maharaj Terminus", "छत्रपति शिवाजी महाराज टर्मिनस", ["cst", "csmt", "vt"]),
    "BDTS": ("Bandra Terminus", "बांद्रा टर्मिनस", ["bandra", "bdts"]),
    "HWH": ("Howrah Jn", "हावड़ा जंक्शन", ["howrah", "hwh", "kolkata"]),
    "SDAH": ("Sealdah", "सियालदह", ["sealdah", "sdah"]),
    "KOAA": ("Kolkata", "कोलकाता", ["koaa"]),
    "MAS": ("Chennai Central", "चेन्नई सेंट्रल", ["chennai", "mas", "madras"]),
    "SBC": ("KSR Bengaluru", "केएसआर बेंगलुरु", ["bangalore", "bengaluru", "sbc"]),
    "SC": ("Secunderabad Jn", "सिकंदराबाद जंक्शन", ["hyderabad", "secunderabad", "sc"]),
    "HYB": ("Hyderabad Deccan", "हैदराबाद दक्कन", ["hyb"]),
    "PUNE": ("Pune Jn", "पुणे जंक्शन", ["pune", "poona", "pune jn"]),
    "ADI": ("Ahmedabad Jn", "अहमदाबाद जंक्शन", ["ahmedabad", "adi"]),
    "JP": ("Jaipur Jn", "जयपुर जंक्शन", ["jaipur", "jp"]),
    "JU": ("Jodhpur Jn", "जोधपुर जंक्शन", ["jodhpur", "ju"]),
    "PNBE": ("Patna Jn", "पटना जंक्शन", ["patna", "pnbe"]),
    "RNC": ("Ranchi Jn", "राँची जंक्शन", ["ranchi", "rnc"]),
    "GHY": ("Guwahati", "गुवाहाटी", ["guwahati", "ghy"]),
    "SVDK": ("Shri Mata Vaishno Devi Katra", "श्री माता वैष्णो देवी कटरा", ["katra", "svdk", "vaishno devi"]),
    "JAT": ("Jammu Tawi", "जम्मू तवी", ["jammu", "jat"]),
    "ASR": ("Amritsar Jn", "अमृतसर जंक्शन", ["amritsar", "asr"]),
    "CDG": ("Chandigarh", "चंडीगढ़", ["chandigarh", "cdg"]),
    "DDN": ("Dehradun", "देहरादून", ["dehradun", "ddn"]),
    "HW": ("Haridwar", "हरिद्वार", ["haridwar", "hw"]),
    "CNI": ("Chandrapur", "चंद्रपुर", ["chandrapur"]),
    "NGP": ("Nagpur Jn", "नागपुर जंक्शन", ["nagpur", "ngp"]),
    "JBP": ("Jabalpur Jn", "जबलपुर जंक्शन", ["jabalpur", "jbp"]),
    "ITR": ("Itarsi Jn", "इटारसी जंक्शन", ["itarsi", "et"]),
    "ET": ("Itarsi Jn", "इटारसी जंक्शन", ["itarsi"]),
}

# Standard Railway Travel Classes
TRAVEL_CLASSES: Dict[str, str] = {
    "1A": "First AC (1A)",
    "2A": "AC 2 Tier (2A)",
    "3A": "AC 3 Tier (3A)",
    "3E": "AC 3 Economy (3E)",
    "CC": "AC Chair Car (CC)",
    "EC": "Exec Chair Car (EC)",
    "SL": "Sleeper (SL)",
    "2S": "Second Sitting (2S)",
}

# Standard Quotas
QUOTAS: Dict[str, str] = {
    "GN": "General Quota (GN)",
    "TQ": "Tatkal Quota (TQ)",
    "PT": "Premium Tatkal (PT)",
    "LD": "Ladies Quota (LD)",
    "SS": "Senior Citizen (SS)",
}


def normalize_text(text: str) -> str:
    """Strip punctuation and lowercase text for fuzzy matching."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def resolve_station_code(query: str) -> Optional[Tuple[str, str]]:
    """
    Resolve a station name, colloquial alias, or code into (station_code, display_name).
    
    Examples:
        'orai' -> ('ORAI', 'Orai')
        'kanpur' -> ('CNB', 'Kanpur Central')
        'delhi' -> ('NDLS', 'New Delhi')
        'jhansi' -> ('VGLJ', 'Virangana Lakshmibai Jhansi')
        'NDLS' -> ('NDLS', 'New Delhi')
    """
    if not query:
        return None
    
    q_norm = normalize_text(query)
    q_upper = query.strip().upper()

    # 1. Exact station code match
    if q_upper in STATION_CATALOG:
        return q_upper, STATION_CATALOG[q_upper][0]

    # 2. Exact alias match
    for code, (eng_name, hin_name, aliases) in STATION_CATALOG.items():
        if q_norm in aliases or q_norm == normalize_text(eng_name):
            return code, eng_name

    # 3. Substring / Prefix match
    for code, (eng_name, hin_name, aliases) in STATION_CATALOG.items():
        for alias in aliases:
            if alias.startswith(q_norm) or q_norm.startswith(alias):
                return code, eng_name
        if q_norm in normalize_text(eng_name) or normalize_text(eng_name) in q_norm:
            return code, eng_name

    # 4. Fallback if the query looks like a valid 3-5 uppercase station code
    if re.match(r"^[A-Z]{3,5}$", q_upper):
        return q_upper, q_upper

    return None


def parse_travel_date(date_str: Optional[str] = None) -> str:
    """
    Parse colloquial date strings in English or Hindi into ISO 'YYYY-MM-DD'.
    
    Supports:
      'today', 'aaj', 'tomorrow', 'kal', 'parso', 'day after tomorrow',
      'YYYY-MM-DD', 'DD-MM-YYYY', 'DD/MM/YYYY', 'DD Month' (e.g. '15 Oct').
      Defaults to today if unspecified.
    """
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    today = now_ist.date()

    if not date_str or not date_str.strip():
        return today.strftime("%Y-%m-%d")

    raw = date_str.strip().lower()

    if raw in ("today", "aaj", "current"):
        return today.strftime("%Y-%m-%d")

    if raw in ("tomorrow", "kal", "next day"):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    if raw in ("parso", "day after tomorrow"):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    # Day of week parsing: e.g. "next monday", "monday", "somwar"
    weekdays = {
        "monday": 0, "somwar": 0,
        "tuesday": 1, "mangalwar": 1,
        "wednesday": 2, "budhwar": 2,
        "thursday": 3, "guruwar": 3, "veervar": 3,
        "friday": 4, "shukrawar": 4,
        "saturday": 5, "shaniwar": 5,
        "sunday": 6, "raviwar": 6
    }
    for day_name, target_weekday in weekdays.items():
        if day_name in raw:
            days_ahead = (target_weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Match YYYY-MM-DD
    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m_iso:
        y, m, d = int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Match DD-MM-YYYY or DD/MM/YYYY
    m_dmy = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", raw)
    if m_dmy:
        d, m, y = int(m_dmy.group(1)), int(m_dmy.group(2)), int(m_dmy.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Match DD Month (e.g., '15 oct', '15 october', '5 nov 2026')
    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "september": 9, "oct": 10, "october": 10,
        "nov": 11, "november": 11, "dec": 12, "december": 12
    }
    m_month = re.match(r"^(\d{1,2})\s+([a-zA-Z]+)(?:\s+(\d{4}))?$", raw)
    if m_month:
        d = int(m_month.group(1))
        m_name = m_month.group(2)[:3].lower()
        if m_name in months:
            m = months[m_name]
            y = int(m_month.group(3)) if m_month.group(3) else today.year
            # If the month is in the past this year, bump to next year
            if not m_month.group(3) and (m < today.month or (m == today.month and d < today.day)):
                y += 1
            try:
                return datetime(y, m, d).strftime("%Y-%m-%d")
            except ValueError:
                pass

    return today.strftime("%Y-%m-%d")
