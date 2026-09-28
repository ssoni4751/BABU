"""
tests/test_railway_intelligence.py — Automated Test Suite for Railway Intelligence & Travel Enquiry
"""

import os
import pytest
from datetime import datetime, timedelta, timezone

from babu.travel_catalog import (
    resolve_station_code,
    parse_travel_date,
    normalize_text,
    STATION_CATALOG,
    TRAVEL_CLASSES
)
from babu.railway_service import (
    search_trains,
    check_seat_availability,
    get_pnr_status,
    register_tracked_pnr,
    get_tracked_pnrs,
    poll_tracked_pnrs,
    try_handle_railway_natural_query
)


class TestTravelCatalog:
    """Tests for station resolution and travel date parsing."""

    def test_station_resolution_exact_codes(self):
        assert resolve_station_code("ORAI") == ("ORAI", "Orai")
        assert resolve_station_code("CNB") == ("CNB", "Kanpur Central")
        assert resolve_station_code("VGLJ") == ("VGLJ", "Virangana Lakshmibai Jhansi")
        assert resolve_station_code("NDLS") == ("NDLS", "New Delhi")
        assert resolve_station_code("LKO") == ("LKO", "Lucknow Charbagh NR")

    def test_station_resolution_colloquial_names(self):
        assert resolve_station_code("orai") == ("ORAI", "Orai")
        assert resolve_station_code("kanpur") == ("CNB", "Kanpur Central")
        assert resolve_station_code("cawnpore") == ("CNB", "Kanpur Central")
        assert resolve_station_code("jhansi") == ("VGLJ", "Virangana Lakshmibai Jhansi")
        assert resolve_station_code("delhi") == ("NDLS", "New Delhi")
        assert resolve_station_code("new delhi") == ("NDLS", "New Delhi")
        assert resolve_station_code("lucknow") == ("LKO", "Lucknow Charbagh NR")
        assert resolve_station_code("banda") == ("BNDA", "Banda Jn")

    def test_station_resolution_invalid(self):
        assert resolve_station_code("xyz_non_existent_place_123") is None
        assert resolve_station_code("") is None

    def test_date_parser_relative_terms(self):
        now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        today = now_ist.date()
        tomorrow = today + timedelta(days=1)
        parso = today + timedelta(days=2)

        assert parse_travel_date("today") == today.strftime("%Y-%m-%d")
        assert parse_travel_date("aaj") == today.strftime("%Y-%m-%d")
        assert parse_travel_date("tomorrow") == tomorrow.strftime("%Y-%m-%d")
        assert parse_travel_date("kal") == tomorrow.strftime("%Y-%m-%d")
        assert parse_travel_date("parso") == parso.strftime("%Y-%m-%d")

    def test_date_parser_explicit_formats(self):
        assert parse_travel_date("2026-10-15") == "2026-10-15"
        assert parse_travel_date("15-10-2026") == "2026-10-15"
        assert parse_travel_date("15/10/2026") == "2026-10-15"


class TestRailwayService:
    """Tests for train search, live seat availability, and PNR status."""

    def test_search_trains_popular_route(self):
        res = search_trains("orai", "delhi", "tomorrow")
        assert res["success"] is True
        assert res["src_code"] == "ORAI"
        assert res["dest_code"] == "NDLS"
        assert len(res["trains"]) > 0
        assert "12555" in [t["train_no"] for t in res["trains"]]
        assert "GORAKHDHAM" in res["formatted_text"]

    def test_search_trains_invalid_station(self):
        res = search_trains("unknown_xyz", "delhi")
        assert res["success"] is False
        assert "स्टेशन कोड नहीं मिला" in res["error"]

    def test_check_seat_availability(self):
        res = check_seat_availability("12555", "orai", "delhi", travel_class="3A")
        assert res["success"] is True
        assert res["train_no"] == "12555"
        assert res["class"] == "3A"
        assert res["badge"] in ("🟢", "🟡", "🔴")
        assert res["fare"] > 0
        assert "GORAKHDHAM" in res["formatted_text"]

    def test_get_pnr_status_valid(self):
        res = get_pnr_status("2458963214")
        assert res["success"] is True
        assert res["pnr"] == "2458963214"
        assert len(res["passengers"]) > 0
        assert "CHART" in res["chart_status"]
        assert "GORAKHDHAM" in res["formatted_text"]

    def test_get_pnr_status_invalid_length(self):
        res = get_pnr_status("12345")
        assert res["success"] is False
        assert "अमान्य PNR" in res["error"]


class TestPNRTracker:
    """Tests for SQLite-backed autonomous PNR tracking."""

    def test_register_and_get_tracked_pnr(self):
        chat_id = 8832681666
        pnr = "9876543210"
        ok, msg = register_tracked_pnr(pnr, chat_id, train_no="12555", train_name="GORAKHDHAM EXP")
        assert ok is True
        assert "ट्रैकिंग शुरू" in msg

        tracked = get_tracked_pnrs(chat_id)
        assert any(t["pnr"] == pnr for t in tracked)

    def test_poll_tracked_pnrs(self):
        # Polling should execute without exceptions
        notifications = poll_tracked_pnrs()
        assert isinstance(notifications, list)


class TestNaturalLanguageIntercept:
    """Tests for conversational railway query detection in Hinglish/English."""

    def test_intercept_train_search_hinglish(self):
        query = "kal orai se delhi train batao"
        res = try_handle_railway_natural_query(query)
        assert res is not None
        assert res["type"] == "train_search"
        assert res["success"] is True
        assert "12555" in res["text"]

    def test_intercept_train_search_english(self):
        query = "train from kanpur to delhi"
        res = try_handle_railway_natural_query(query)
        assert res is not None
        assert res["type"] == "train_search"
        assert res["success"] is True

    def test_intercept_seat_availability(self):
        query = "12555 me 3AC ki seat check karo"
        res = try_handle_railway_natural_query(query)
        assert res is not None
        assert res["type"] == "seats"
        assert res["train_no"] == "12555"
        assert res["class"] == "3A"
        assert res["success"] is True

    def test_intercept_pnr_status(self):
        query = "mera pnr 2458963214 status check karo"
        res = try_handle_railway_natural_query(query)
        assert res is not None
        assert res["type"] == "pnr"
        assert res["pnr"] == "2458963214"
        assert res["success"] is True

    def test_non_railway_query_passes_through(self):
        assert try_handle_railway_natural_query("hi babu how are you") is None
        assert try_handle_railway_natural_query("what is my current bank balance") is None
        assert try_handle_railway_natural_query("post on facebook page") is None
