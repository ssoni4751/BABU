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
        res = search_trains("kanpur", "delhi", "tomorrow")
        assert res["success"] is True
        assert res["src_code"] == "CNB"
        assert res["dest_code"] == "NDLS"
        assert len(res["trains"]) > 0
        assert "22435" in [t["train_no"] for t in res["trains"]]
        assert "VANDE BHARAT" in res["formatted_text"]

    def test_search_trains_orai_to_delhi_connecting(self):
        res = search_trains("orai", "delhi", "tomorrow")
        assert res["success"] is True
        assert res["is_connecting"] is True
        assert res["src_code"] == "ORAI"
        assert res["dest_code"] == "NDLS"
        assert "सीधी (Direct) ट्रेन उपलब्ध नहीं है" in res["formatted_text"]
        assert "झांसी" in res["formatted_text"]
        assert "कानपुर" in res["formatted_text"]

    def test_search_trains_invalid_station(self):
        res = search_trains("unknown_xyz", "delhi")
        assert res["success"] is False
        assert "स्टेशन कोड नहीं मिला" in res["error"]

    def test_check_seat_availability(self):
        res = check_seat_availability("12555", "orai", "delhi", travel_class="3A")
        assert res["success"] is True
        assert res["train_no"] == "12555"
        assert res["class"] == "3A"
        assert res["badge"] in ("🟢", "🟡", "🔴", "⚪")
        assert res["fare"] > 0
        assert "GORAKHDHAM" in res["formatted_text"]

        # Future date should have active booking status badge
        res_future = check_seat_availability("12555", "orai", "delhi", "2026-10-15", travel_class="3A")
        assert res_future["badge"] in ("🟢", "🟡", "🔴")

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
        assert res["is_connecting"] is True
        assert "झांसी" in res["text"]

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

    def test_intercept_slash_trains_and_explicit_date(self):
        res1 = try_handle_railway_natural_query("/trains jhansi delhi 05-10-2026")
        assert res1 is not None
        assert res1["type"] == "train_search"
        assert res1["src_code"] == "VGLJ"
        assert res1["dest_code"] == "NDLS"
        assert res1["date"] == "2026-10-05"

        res2 = try_handle_railway_natural_query("/train jhansi delhi 05-10-2026")
        assert res2 is not None
        assert res2["type"] == "train_search"
        assert res2["src_code"] == "VGLJ"
        assert res2["dest_code"] == "NDLS"

        res3 = try_handle_railway_natural_query("/seats 12279 CC")
        assert res3 is not None
        assert res3["type"] == "seats"
        assert res3["train_no"] == "12279"
        assert res3["class"] == "CC"


class TestDateChangeMenuMarkup:
    def test_build_train_search_reply_markup_direct_route(self):
        from babu.bot import build_train_search_reply_markup
        res = {
            "success": True,
            "src_code": "VGLJ",
            "dest_code": "NDLS",
            "date": "2026-10-05",
            "is_connecting": False,
            "trains": [
                {"train_no": "12279", "train_name": "TAJ EXPRESS", "classes": ["CC", "2S"]},
                {"train_no": "12001", "train_name": "SHATABDI", "classes": ["CC", "EC"]}
            ]
        }
        markup = build_train_search_reply_markup(res)
        keyboard = markup.inline_keyboard
        assert len(keyboard) >= 3

        # Row 1: Date Stepper
        stepper_row = keyboard[0]
        assert len(stepper_row) == 3
        assert "rail_search|VGLJ|NDLS|2026-10-04" in stepper_row[0].callback_data
        assert "rail_noop|2026-10-05" in stepper_row[1].callback_data
        assert "rail_search|VGLJ|NDLS|2026-10-06" in stepper_row[2].callback_data

        # Row 2: Quick Dates
        quick_dates_row = keyboard[1]
        assert len(quick_dates_row) == 3
        assert "rail_search|VGLJ|NDLS|" in quick_dates_row[0].callback_data
        assert "rail_search|VGLJ|NDLS|" in quick_dates_row[1].callback_data
        assert "rail_search|VGLJ|NDLS|" in quick_dates_row[2].callback_data

        # Row 3: Train seat buttons
        train_row = keyboard[2]
        assert any("rail_seats|12279|VGLJ|NDLS|2026-10-05" in btn.callback_data for btn in train_row)

    def test_build_train_search_reply_markup_connecting_route(self):
        from babu.bot import build_train_search_reply_markup
        res = {
            "success": True,
            "src_code": "ORAI",
            "dest_code": "NDLS",
            "date": "2026-10-05",
            "is_connecting": True,
            "trains": []
        }
        markup = build_train_search_reply_markup(res)
        keyboard = markup.inline_keyboard
        assert len(keyboard) == 4  # Stepper + Quick Dates + 2 Connecting Leg Rows

        # Check connecting leg buttons carrying the forward date
        leg_row1 = keyboard[2]
        leg_row2 = keyboard[3]
        assert "rail_search|ORAI|VGLJ|2026-10-05" in leg_row1[0].callback_data
        assert "rail_search|VGLJ|NDLS|2026-10-05" in leg_row1[1].callback_data
        assert "rail_search|ORAI|CNB|2026-10-05" in leg_row2[0].callback_data
        assert "rail_search|CNB|NDLS|2026-10-05" in leg_row2[1].callback_data


class TestDynamicSeatAvailability:
    def test_taj_express_seats_differ_across_dates(self):
        from babu.railway_service import check_seat_availability
        res_sep30 = check_seat_availability("12279", "VGLJ", "NDLS", "2026-09-30", travel_class="2S")
        res_oct05 = check_seat_availability("12279", "VGLJ", "NDLS", "2026-10-05", travel_class="2S")
        res_nov01 = check_seat_availability("12279", "VGLJ", "NDLS", "2026-11-01", travel_class="2S")

        assert res_sep30["success"] is True
        assert res_oct05["success"] is True
        assert res_nov01["success"] is True

        # Crucial check: verify they are NOT all stuck on RAC 11!
        statuses = [res_sep30["status"], res_oct05["status"], res_nov01["status"]]
        assert len(set(statuses)) > 1, f"Expected varied availability statuses, got identical: {statuses}"
        assert res_sep30["available_classes"] == ["CC", "2S"]

    def test_build_seat_check_reply_markup(self):
        from babu.bot import build_seat_check_reply_markup
        res = {
            "success": True,
            "train_no": "12279",
            "train_name": "TAJ EXPRESS",
            "src_code": "VGLJ",
            "dest_code": "NDLS",
            "date": "2026-10-05",
            "class": "2S",
            "available_classes": ["CC", "2S"],
            "status": "AVAILABLE 34",
            "probability": "Confirmed (100%)",
            "badge": "🟢"
        }
        markup = build_seat_check_reply_markup(res)
        keyboard = markup.inline_keyboard

        # Row 1: Class switcher
        assert len(keyboard[0]) == 2
        assert "rail_seats|12279|VGLJ|NDLS|2026-10-05|CC" in keyboard[0][0].callback_data
        assert "rail_noop|" in keyboard[0][1].callback_data

        # Row 2: Date stepper
        assert len(keyboard[1]) == 3
        assert "rail_seats|12279|VGLJ|NDLS|2026-10-04|2S" in keyboard[1][0].callback_data
        assert "rail_noop|2026-10-05" in keyboard[1][1].callback_data
        assert "rail_seats|12279|VGLJ|NDLS|2026-10-06|2S" in keyboard[1][2].callback_data

        # Row 3: Live ConfirmTkt link and Back to train list
        assert len(keyboard[2]) == 2
        assert keyboard[2][0].url is not None
        assert "confirmtkt.com" in keyboard[2][0].url
        assert "rail_search|VGLJ|NDLS|2026-10-05" in keyboard[2][1].callback_data


