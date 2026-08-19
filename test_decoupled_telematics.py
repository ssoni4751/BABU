import os
import sys
import pytest

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)
sys.path.insert(0, os.path.join(CURRENT_DIR, "babu"))

from telematics import (
    get_token_costs,
    calculate_inference_cost,
    extract_tokens,
    add_tokens,
    get_uptime_summary,
    get_telemetry_snapshot,
    PRICING_TABLE
)
from data_catalog import (
    RUNTIME_DATA_CATALOG,
    get_runtime_data_catalog,
    resolve_knowledge_class_for_query
)

def test_telematics_isolated_metrics():
    """Verify telematics module functions accurately and independently."""
    # 1. Pricing table lookup
    p_cost, c_cost = get_token_costs("llama-3.3-70b-versatile")
    assert p_cost > 0
    assert c_cost > 0
    
    # 2. Cost calculation
    total_cost = calculate_inference_cost("llama-3.3-70b-versatile", prompt_tokens=1000, completion_tokens=500)
    assert total_cost > 0
    
    # 3. Token extraction from mock metadata
    mock_resp = type("MockResp", (), {"response_metadata": {"token_usage": {"prompt_tokens": 150, "completion_tokens": 50}}})()
    extracted = extract_tokens(mock_resp)
    assert extracted["prompt"] == 150
    assert extracted["completion"] == 50
    assert extracted["total"] == 200
    
    # 4. Token addition
    combined = add_tokens({"prompt": 10, "completion": 20, "total": 30}, {"prompt": 5, "completion": 5, "total": 10})
    assert combined["total"] == 40
    
    # 5. Uptime & snapshot
    uptime = get_uptime_summary()
    assert "uptime_seconds" in uptime
    assert uptime["uptime_seconds"] >= 0
    
    snapshot = get_telemetry_snapshot()
    assert snapshot["status"] == "HEALTHY"
    assert "pricing_table_entries" in snapshot

def test_sii_aligned_data_catalog():
    """Verify runtime data catalog enforces K0-K7 alignment and zero bulk dumping."""
    catalog = get_runtime_data_catalog()
    assert len(catalog) >= 7
    
    # Verify every class strictly disallows bulk dumps
    for k_class, info in catalog.items():
        assert info["bulk_dump_allowed"] is False, f"Bulk dump must be False for {k_class}"
        assert "storage" in info
        assert "fetch_method" in info
        assert "privacy" in info

def test_knowledge_class_query_resolver():
    """Verify surgical routing to specific knowledge classes."""
    assert resolve_knowledge_class_for_query("what is the current latency and uptime") == "K2_RUNTIME_TELEMETRY"
    assert resolve_knowledge_class_for_query("what did you do yesterday") == "K2_RUNTIME_TELEMETRY"
    assert resolve_knowledge_class_for_query("who is my father") == "K1_IDENTITY"
    assert resolve_knowledge_class_for_query("what is the fee for GST registration at Anshu consultancy") == "K3_BUSINESS"
    assert resolve_knowledge_class_for_query("what is ADR-002 and its tradeoffs") == "K5_ARCHITECTURE_GOVERNANCE"
    assert resolve_knowledge_class_for_query("can you draft an email for Rajat") == "K0_WORKING_MEMORY"
