"""
BABU Telematics Subsystem — Category 1 (Telemetry, Latency, Token Accounting, System Health)
Decoupled non-semantic performance and resource metrics engine aligned with SII & Runtime Index.
"""

import os
import time
import threading
from typing import Optional, Tuple, Dict, Any

# Initial process start time for uptime tracking
BOT_START_TIME = time.time()

# Service health checkpoints
LAST_TELEGRAM_SUCCESS_TIME: Optional[float] = None
LAST_FB_SUCCESS_TIME: Optional[float] = None
LAST_GOOGLE_SUCCESS_TIME: Optional[float] = None
LAST_WEB_SUCCESS_TIME: Optional[float] = None

# Model Pricing Table ($ per 1 Million Tokens: [Prompt, Completion])
PRICING_TABLE = {
    "llama-3.1-8b-instant": [0.05, 0.08],
    "llama-3.3-70b-versatile": [0.59, 0.79],
    "llama-3.3-70b-specdec": [0.59, 0.99],
    "llama3-70b-8192": [0.59, 0.79],
    "llama3-8b-8192": [0.05, 0.08],
    "mixtral-8x7b-32768": [0.24, 0.24],
    "gemma2-9b-it": [0.20, 0.20],
    "gemini-2.0-flash": [0.10, 0.40],
    "gemini-1.5-flash": [0.075, 0.30],
    "gemini-1.5-pro": [1.25, 5.00],
    "compound": [0.59, 0.79],
    "compound-mini": [0.05, 0.08]
}

def get_token_costs(model_name: str) -> Tuple[float, float]:
    """Return (prompt_cost_per_token, completion_cost_per_token) for a given model."""
    if not model_name:
        return 0.15 / 1_000_000, 0.60 / 1_000_000
    m_lower = model_name.lower().strip()
    for key, rates in PRICING_TABLE.items():
        if key in m_lower:
            return rates[0] / 1_000_000, rates[1] / 1_000_000
    return 0.15 / 1_000_000, 0.60 / 1_000_000

def calculate_inference_cost(model_name: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate the exact USD cost for an LLM inference call."""
    p_rate, c_rate = get_token_costs(model_name)
    return round((prompt_tokens * p_rate) + (completion_tokens * c_rate), 6)

def extract_tokens(response: Any) -> Dict[str, int]:
    """Surgically extract token usage from LangChain / provider response metadata without full payload dumping."""
    tokens = {"prompt": 0, "completion": 0, "total": 0}
    if not response:
        return tokens
        
    usage = None
    if hasattr(response, "response_metadata") and isinstance(response.response_metadata, dict):
        usage = response.response_metadata.get("token_usage") or response.response_metadata.get("usage")
    elif hasattr(response, "usage_metadata") and isinstance(response.usage_metadata, dict):
        usage = response.usage_metadata
    elif isinstance(response, dict):
        usage = response.get("usage") or response.get("token_usage")
        
    if usage and isinstance(usage, dict):
        p = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        c = usage.get("completion_tokens") or usage.get("output_tokens") or 0
        t = usage.get("total_tokens") or (p + c)
        tokens["prompt"] = int(p)
        tokens["completion"] = int(c)
        tokens["total"] = int(t)
    return tokens

def add_tokens(t1: Dict[str, int], t2: Dict[str, int]) -> Dict[str, int]:
    """Accurately combine two token dictionaries."""
    return {
        "prompt": t1.get("prompt", 0) + t2.get("prompt", 0),
        "completion": t1.get("completion", 0) + t2.get("completion", 0),
        "total": t1.get("total", 0) + t2.get("total", 0)
    }

def get_uptime_summary() -> Dict[str, Any]:
    """Compute lightweight system uptime metrics."""
    uptime_sec = time.time() - BOT_START_TIME
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    secs = int(uptime_sec % 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if mins > 0:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    
    return {
        "uptime_seconds": round(uptime_sec, 2),
        "uptime_formatted": " ".join(parts),
        "start_time_epoch": BOT_START_TIME
    }

def get_telemetry_snapshot() -> Dict[str, Any]:
    """
    Produce a lightweight, decoupled telemetry snapshot.
    Fetches aggregate metrics from execution_ledger without loading full rows into memory.
    """
    try:
        from services import get_db_connection
        conn, is_pg = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM execution_ledger")
        total_ledger_events = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(DISTINCT goal_id) FROM execution_ledger WHERE goal_id IS NOT NULL")
        total_goals = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
    except Exception:
        total_ledger_events = 0
        total_goals = 0

    uptime_info = get_uptime_summary()
    
    return {
        "status": "HEALTHY",
        "uptime": uptime_info["uptime_formatted"],
        "uptime_seconds": uptime_info["uptime_seconds"],
        "total_goals_tracked": total_goals,
        "total_ledger_events": total_ledger_events,
        "pricing_table_entries": len(PRICING_TABLE)
    }
