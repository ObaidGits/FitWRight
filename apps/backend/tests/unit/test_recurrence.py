"""Unit tests for recurrence + timezone/DST math and ICS escaping (P3 §E/§F)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.scheduling.ics import build_ics
from app.scheduling.recurrence import (
    is_valid_timezone,
    next_occurrence,
    parse_recurrence,
    validate_recurrence,
)


class TestParse:
    def test_daily(self):
        assert parse_recurrence("daily") == {"interval": 1, "unit": "days", "until": None}

    def test_weekly(self):
        assert parse_recurrence("weekly")["unit"] == "weeks"

    def test_every_n(self):
        spec = parse_recurrence("every:3:days")
        assert spec == {"interval": 3, "unit": "days", "until": None}

    def test_until_parsed(self):
        spec = parse_recurrence("weekly;until=2026-12-31T00:00:00+00:00")
        assert spec["until"].year == 2026

    def test_invalid_returns_none(self):
        assert parse_recurrence("monthly") is None
        assert parse_recurrence("every:0:days") is None
        assert parse_recurrence("every:5:years") is None
        assert parse_recurrence("garbage") is None

    def test_validate(self):
        assert validate_recurrence(None) is True
        assert validate_recurrence("") is True
        assert validate_recurrence("daily") is True
        assert validate_recurrence("nope") is False


class TestNextOccurrence:
    def test_no_recurrence_none(self):
        assert next_occurrence("2026-01-01T09:00:00+00:00", None, "UTC") is None

    def test_daily_advances_one_day(self):
        nxt = next_occurrence("2026-01-01T09:00:00+00:00", "daily", "UTC")
        assert nxt == "2026-01-02T09:00:00+00:00"

    def test_weekly_advances_seven_days(self):
        nxt = next_occurrence("2026-01-01T09:00:00+00:00", "weekly", "UTC")
        assert datetime.fromisoformat(nxt) == datetime(2026, 1, 8, 9, tzinfo=timezone.utc)

    def test_until_stops_series(self):
        nxt = next_occurrence(
            "2026-01-01T09:00:00+00:00", "daily;until=2026-01-01T12:00:00+00:00", "UTC"
        )
        assert nxt is None

    def test_dst_preserves_wall_clock(self):
        # US DST 2026 starts Sun Mar 8. A daily 9am America/New_York reminder on
        # Mar 7 must still be 9am local on Mar 8 - i.e. the UTC hour shifts by 1.
        before = "2026-03-07T14:00:00+00:00"  # 09:00 EST (UTC-5)
        nxt = next_occurrence(before, "daily", "America/New_York")
        # Next day 09:00 EDT (UTC-4) -> 13:00Z, not 14:00Z.
        assert nxt == "2026-03-08T13:00:00+00:00"

    def test_invalid_tz_falls_back_utc(self):
        nxt = next_occurrence("2026-01-01T09:00:00+00:00", "daily", "Not/AZone")
        assert nxt == "2026-01-02T09:00:00+00:00"


class TestTimezone:
    def test_valid(self):
        assert is_valid_timezone("America/New_York")
        assert is_valid_timezone("UTC")

    def test_invalid(self):
        assert not is_valid_timezone("Mars/Phobos")


class TestIcs:
    def test_basic_vevent(self):
        ics = build_ics(uid="abc", starts_at_iso="2026-05-01T15:00:00+00:00", duration_min=60, summary="Interview")
        assert "BEGIN:VCALENDAR" in ics
        assert "DTSTART:20260501T150000Z" in ics
        assert "DTEND:20260501T160000Z" in ics
        assert ics.endswith("\r\n")

    def test_escaping_prevents_injection(self):
        # Newlines/commas/semicolons must be escaped, never break out of the field.
        ics = build_ics(
            uid="x", starts_at_iso="2026-05-01T15:00:00+00:00", duration_min=30,
            summary="Evil\r\nBEGIN:VEVENT", location="a,b;c", description="line1\nline2",
        )
        assert "a\\,b\\;c" in ics
        assert "line1\\nline2" in ics
        # The injected "BEGIN:VEVENT" is escaped INTO the SUMMARY value, never a
        # real content line: exactly one true VEVENT boundary (CRLF-delimited).
        assert ics.count("\r\nBEGIN:VEVENT\r\n") == 1
        assert ics.count("\r\nEND:VEVENT\r\n") == 1
        # The raw CRLF from the malicious summary was stripped/escaped.
        assert "SUMMARY:Evil\\nBEGIN:VEVENT" in ics
