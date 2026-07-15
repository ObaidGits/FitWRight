"""ICS (iCalendar) export for interviews (design §F, R11.3).

Emits a single VEVENT with **escaped** text fields and **UTC** DTSTART/DTEND so
the event is timezone-correct in any calendar client. All user-supplied text
(summary/location/description) is escaped per RFC 5545 (``\\`` ``,`` ``;`` and
newlines) and stripped of raw CR/LF, closing the ICS/CRLF-injection vector
(design threat model).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["build_ics"]


def _escape(text: str | None) -> str:
    """Escape a text value for an ICS content line (RFC 5545 §3.3.11)."""
    if not text:
        return ""
    # Strip raw control chars first (defense-in-depth against CRLF injection),
    # then escape the ICS metacharacters.
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "".join(ch for ch in cleaned if ch == "\n" or ch >= " ")
    return (
        cleaned.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fmt_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fold(line: str) -> str:
    """Fold a content line at 75 octets (RFC 5545 §3.1) using CRLF + space."""
    if len(line) <= 75:
        return line
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(chunks)


def build_ics(
    *,
    uid: str,
    starts_at_iso: str,
    duration_min: int,
    summary: str,
    location: str | None = None,
    description: str | None = None,
    organizer_email: str | None = None,
) -> str:
    """Build a VCALENDAR/VEVENT string for one interview (CRLF line endings)."""
    start = datetime.fromisoformat(starts_at_iso)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(minutes=max(0, duration_min))
    now = datetime.now(timezone.utc)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//FitWright//Interviews//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{_escape(uid)}@fitwright",
        f"DTSTAMP:{_fmt_utc(now)}",
        f"DTSTART:{_fmt_utc(start)}",
        f"DTEND:{_fmt_utc(end)}",
        f"SUMMARY:{_escape(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{_escape(location)}")
    if description:
        lines.append(f"DESCRIPTION:{_escape(description)}")
    if organizer_email and "@" in organizer_email and "\r" not in organizer_email and "\n" not in organizer_email:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
