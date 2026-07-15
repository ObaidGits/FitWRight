"""Bounded rrule-lite recurrence + timezone/DST-correct math (design §E, R10.2).

A deliberately small, safe recurrence grammar (full RFC-5545 is out of scope —
design Round 5 residual (b)):

    daily | weekly | every:N:days | every:N:weeks   [ ";until=<UTC ISO>" ]

``next_occurrence`` advances a UTC instant by one interval **in the reminder's
IANA timezone** so the *wall-clock* time is preserved across DST transitions
(e.g. a 9am weekly reminder stays 9am local even when the UTC offset shifts),
then converts back to UTC. Returns ``None`` when there is no recurrence or the
next occurrence would fall after ``until`` (the series ends).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

__all__ = ["validate_recurrence", "next_occurrence", "parse_recurrence", "is_valid_timezone"]

_MAX_INTERVAL = 365


def is_valid_timezone(tz_name: str) -> bool:
    """Whether ``tz_name`` is a resolvable IANA timezone."""
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def parse_recurrence(recurrence: str | None) -> dict | None:
    """Parse an rrule-lite string into ``{interval, unit, until}`` or ``None``.

    Returns ``None`` for a one-shot (no/invalid recurrence) — callers treat a
    ``None`` result as "does not repeat", never as an error, so a malformed
    stored value degrades safely to a single fire.
    """
    if not recurrence or not isinstance(recurrence, str):
        return None
    parts = [p.strip() for p in recurrence.split(";") if p.strip()]
    if not parts:
        return None
    rule = parts[0].lower()
    until: str | None = None
    for extra in parts[1:]:
        if extra.lower().startswith("until="):
            until = extra[len("until=") :]

    interval: int
    unit: str
    if rule == "daily":
        interval, unit = 1, "days"
    elif rule == "weekly":
        interval, unit = 1, "weeks"
    elif rule.startswith("every:"):
        segs = rule.split(":")
        if len(segs) != 3:
            return None
        try:
            interval = int(segs[1])
        except ValueError:
            return None
        unit = segs[2]
        if unit not in ("days", "weeks") or not (1 <= interval <= _MAX_INTERVAL):
            return None
    else:
        return None

    until_dt: datetime | None = None
    if until:
        try:
            until_dt = datetime.fromisoformat(until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            until_dt = None
    return {"interval": interval, "unit": unit, "until": until_dt}


def validate_recurrence(recurrence: str | None) -> bool:
    """Whether ``recurrence`` is a well-formed rrule-lite string (or None/empty)."""
    if recurrence is None or recurrence == "":
        return True
    return parse_recurrence(recurrence) is not None


def next_occurrence(due_at_iso: str, recurrence: str | None, tz_name: str) -> str | None:
    """Return the next occurrence (UTC ISO) after ``due_at_iso``, or ``None``.

    DST-correct: advances the *wall-clock* time in ``tz_name`` then re-derives the
    UTC instant, so a fixed local time is preserved across offset changes.
    """
    spec = parse_recurrence(recurrence)
    if spec is None:
        return None
    try:
        base = datetime.fromisoformat(due_at_iso)
    except ValueError:
        return None
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        tz = timezone.utc

    local = base.astimezone(tz)
    delta = (
        timedelta(days=spec["interval"])
        if spec["unit"] == "days"
        else timedelta(weeks=spec["interval"])
    )
    # Advance the naive wall-clock, then re-attach the tz so the UTC offset is
    # recomputed for the new date (DST-correct); convert back to UTC.
    naive_next = local.replace(tzinfo=None) + delta
    local_next = naive_next.replace(tzinfo=tz)
    utc_next = local_next.astimezone(timezone.utc)

    if spec["until"] is not None and utc_next > spec["until"]:
        return None
    return utc_next.isoformat()
