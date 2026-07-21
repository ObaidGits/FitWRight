"""Reminder + interview business logic (design §E/§F, R10/R11/R17).

Enforces parent-ownership (the application must belong to the caller -> 404),
per-user abuse caps (429), timezone/recurrence validation (422),
idempotency-keys on creates (collapse double-submits), snooze presets, interview
reschedule with lead-time re-arming, and soft overlap detection. All persistence
goes through the user-scoped :class:`~app.scheduling.repo.SchedulingRepo`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.scheduling.recurrence import is_valid_timezone, validate_recurrence
from app.scheduling.repo import get_scheduling_repo

logger = logging.getLogger(__name__)

__all__ = ["SchedulingError", "SchedulingService", "get_scheduling_service"]

_MAX_LEAD_TIMES = 5
_MAX_LEAD_MINUTES = 60 * 24 * 14  # 14 days
_MAX_DURATION_MIN = 60 * 24  # 24h
_VALID_KINDS = {"screen", "technical", "onsite", "behavioral", "final", "other"}
_IDEMPOTENCY_TTL = 60 * 60 * 24


class SchedulingError(Exception):
    """code -> HTTP: not_found 404, invalid 422, conflict 409, limit 429."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str, field: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise SchedulingError("invalid", f"{field} must be an ISO-8601 datetime.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SchedulingService:
    def __init__(self, repo=None) -> None:
        self._repo = repo or get_scheduling_repo()

    def _db(self):
        from app import database

        return database.db

    async def _require_application(self, user_id: str, application_id: str) -> None:
        app = await self._db().get_application(user_id, application_id)
        if app is None:
            raise SchedulingError("not_found", "Application not found.")

    async def _idempotent(self, user_id: str, key: str | None):
        """Return a previously-created resource id for ``key`` (or None)."""
        if not key:
            return None
        from app.auth.runtime import get_kvstore

        cached = await get_kvstore().get(f"idem:{user_id}:{key}")
        return cached

    async def _remember_idempotent(self, user_id: str, key: str | None, resource_id: str) -> None:
        if not key:
            return
        from app.auth.runtime import get_kvstore

        await get_kvstore().set(f"idem:{user_id}:{key}", resource_id, ttl_seconds=_IDEMPOTENCY_TTL)

    # ======================================================================
    # Reminders
    # ======================================================================

    @staticmethod
    def preset_due(preset: str) -> str:
        """Map a quick preset to a UTC due instant."""
        now = _now()
        mapping = {
            "in_1_day": timedelta(days=1),
            "in_3_days": timedelta(days=3),
            "in_1_week": timedelta(weeks=1),
            "next_week": timedelta(weeks=1),
            "in_2_weeks": timedelta(weeks=2),
        }
        delta = mapping.get(preset)
        if delta is None:
            raise SchedulingError("invalid", f"Unknown preset: {preset}")
        return (now + delta).isoformat()

    async def create_reminder(
        self,
        user_id: str,
        application_id: str,
        *,
        due_at: str,
        tz: str = "UTC",
        note: str | None = None,
        recurrence: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        await self._require_application(user_id, application_id)
        existing = await self._idempotent(user_id, idempotency_key)
        if existing:
            found = await self._repo.get_reminder(user_id, existing)
            if found:
                return found
        due_dt = _parse_utc(due_at, "due_at")
        if not is_valid_timezone(tz):
            raise SchedulingError("invalid", f"Unknown timezone: {tz}")
        if not validate_recurrence(recurrence):
            raise SchedulingError("invalid", "Invalid recurrence rule.")
        from app.config import settings

        if await self._repo.count_active_reminders(user_id) >= settings.max_reminders_per_user:
            raise SchedulingError("limit", "Reminder limit reached.")
        created = await self._repo.create_reminder(
            user_id, application_id, due_at=due_dt.isoformat(), tz=tz,
            note=(note or None), recurrence=(recurrence or None),
        )
        await self._remember_idempotent(user_id, idempotency_key, created["id"])
        return created

    async def update_reminder(
        self, user_id: str, reminder_id: str, *, due_at=None, tz=None, note=None, recurrence=None
    ) -> dict[str, Any]:
        current = await self._repo.get_reminder(user_id, reminder_id)
        if current is None:
            raise SchedulingError("not_found", "Reminder not found.")
        fields: dict[str, Any] = {}
        if due_at is not None:
            fields["due_at"] = _parse_utc(due_at, "due_at").isoformat()
            fields["status"] = "pending"  # editing due re-arms it
        if tz is not None:
            if not is_valid_timezone(tz):
                raise SchedulingError("invalid", f"Unknown timezone: {tz}")
            fields["tz"] = tz
        if note is not None:
            fields["note"] = note or None
        if recurrence is not None:
            if not validate_recurrence(recurrence):
                raise SchedulingError("invalid", "Invalid recurrence rule.")
            fields["recurrence"] = recurrence or None
        updated = await self._repo.update_reminder(user_id, reminder_id, fields)
        if updated is None:
            raise SchedulingError("not_found", "Reminder not found.")
        return updated

    async def snooze_reminder(self, user_id: str, reminder_id: str, *, until: str | None, preset: str | None) -> dict[str, Any]:
        current = await self._repo.get_reminder(user_id, reminder_id)
        if current is None:
            raise SchedulingError("not_found", "Reminder not found.")
        if preset:
            new_due = self.preset_due(preset)
        elif until:
            new_due = _parse_utc(until, "until").isoformat()
        else:
            raise SchedulingError("invalid", "Provide a snooze 'until' or 'preset'.")
        updated = await self._repo.update_reminder(
            user_id, reminder_id, {"due_at": new_due, "status": "snoozed", "claimed_at": None}
        )
        return updated  # type: ignore[return-value]

    async def cancel_reminder(self, user_id: str, reminder_id: str) -> None:
        updated = await self._repo.update_reminder(user_id, reminder_id, {"status": "cancelled"})
        if updated is None:
            raise SchedulingError("not_found", "Reminder not found.")

    async def list_reminders(self, user_id: str, application_id: str) -> list[dict[str, Any]]:
        await self._require_application(user_id, application_id)
        return await self._repo.list_reminders(user_id, application_id)

    # ======================================================================
    # Interviews
    # ======================================================================

    def _validate_interview_inputs(self, starts_at: str, tz: str, duration_min: int, kind: str, lead_times: list[int]) -> tuple[str, list[int]]:
        starts_dt = _parse_utc(starts_at, "starts_at")
        if not is_valid_timezone(tz):
            raise SchedulingError("invalid", f"Unknown timezone: {tz}")
        if not (1 <= duration_min <= _MAX_DURATION_MIN):
            raise SchedulingError("invalid", "duration_min out of range.")
        if kind not in _VALID_KINDS:
            raise SchedulingError("invalid", f"Unknown interview kind: {kind}")
        leads: list[int] = []
        for lead in lead_times or []:
            try:
                m = int(lead)
            except (TypeError, ValueError):
                raise SchedulingError("invalid", "lead_times must be integers (minutes).")
            if 1 <= m <= _MAX_LEAD_MINUTES:
                leads.append(m)
        leads = sorted(set(leads), reverse=True)[:_MAX_LEAD_TIMES]
        return starts_dt.isoformat(), leads

    async def create_interview(
        self, user_id: str, application_id: str, *, starts_at: str, tz: str = "UTC",
        duration_min: int = 60, kind: str = "screen", location: str | None = None,
        notes: str | None = None, lead_times: list[int] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        await self._require_application(user_id, application_id)
        existing = await self._idempotent(user_id, idempotency_key)
        if existing:
            found = await self._repo.get_interview(user_id, existing)
            if found:
                return {**found, "overlaps": []}
        starts_iso, leads = self._validate_interview_inputs(
            starts_at, tz, duration_min, kind, lead_times or [1440, 60]
        )
        from app.config import settings

        if await self._repo.count_active_interviews(user_id) >= settings.max_interviews_per_user:
            raise SchedulingError("limit", "Interview limit reached.")
        ends_iso = (datetime.fromisoformat(starts_iso) + timedelta(minutes=duration_min)).isoformat()
        overlaps = await self._repo.find_overlapping(user_id, starts_iso, ends_iso)
        created = await self._repo.create_interview(
            user_id, application_id, starts_at=starts_iso, tz=tz, duration_min=duration_min,
            kind=kind, location=location, notes=notes, lead_times=leads,
        )
        await self._remember_idempotent(user_id, idempotency_key, created["id"])
        return {**created, "overlaps": [{"id": o["id"], "starts_at": o["starts_at"]} for o in overlaps]}

    async def update_interview(
        self, user_id: str, interview_id: str, *, starts_at=None, tz=None, duration_min=None,
        kind=None, location=None, notes=None, lead_times=None,
    ) -> dict[str, Any]:
        current = await self._repo.get_interview(user_id, interview_id)
        if current is None:
            raise SchedulingError("not_found", "Interview not found.")
        fields: dict[str, Any] = {}
        new_starts = starts_at if starts_at is not None else current["starts_at"]
        new_tz = tz if tz is not None else current["tz"]
        new_dur = duration_min if duration_min is not None else current["duration_min"]
        new_kind = kind if kind is not None else current["kind"]
        new_leads = lead_times if lead_times is not None else current["lead_times"]
        starts_iso, leads = self._validate_interview_inputs(new_starts, new_tz, new_dur, new_kind, new_leads)
        fields.update(starts_at=starts_iso, tz=new_tz, duration_min=new_dur, kind=new_kind, lead_times=leads)
        if location is not None:
            fields["location"] = location or None
        if notes is not None:
            fields["notes"] = notes or None
        updated = await self._repo.update_interview(user_id, interview_id, fields)
        if updated is None:
            raise SchedulingError("not_found", "Interview not found.")
        ends_iso = (datetime.fromisoformat(starts_iso) + timedelta(minutes=new_dur)).isoformat()
        overlaps = await self._repo.find_overlapping(user_id, starts_iso, ends_iso, exclude_id=interview_id)
        return {**updated, "overlaps": [{"id": o["id"], "starts_at": o["starts_at"]} for o in overlaps]}

    async def cancel_interview(self, user_id: str, interview_id: str) -> None:
        updated = await self._repo.update_interview(user_id, interview_id, {"status": "cancelled"})
        if updated is None:
            raise SchedulingError("not_found", "Interview not found.")

    async def list_interviews(self, user_id: str, application_id: str) -> list[dict[str, Any]]:
        await self._require_application(user_id, application_id)
        return await self._repo.list_interviews(user_id, application_id)

    async def get_interview_ics(self, user_id: str, interview_id: str) -> str:
        iv = await self._repo.get_interview(user_id, interview_id)
        if iv is None:
            raise SchedulingError("not_found", "Interview not found.")
        from app.scheduling.ics import build_ics

        app = await self._db().get_application(user_id, iv["application_id"])
        company = (app or {}).get("company") or ""
        role = (app or {}).get("role") or ""
        summary = f"Interview: {company} {role}".strip() or "Interview"
        return build_ics(
            uid=iv["id"],
            starts_at_iso=iv["starts_at"],
            duration_min=iv["duration_min"],
            summary=summary,
            location=iv.get("location"),
            description=iv.get("notes"),
        )


    # ======================================================================
    # Aggregation reads + maintenance (module-owned; foreign modules call these
    # instead of touching the repo directly - ARCHITECTURE Amendment E).
    # ======================================================================

    async def upcoming_reminders(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Upcoming reminders for the agenda aggregation (read use-case)."""
        return await self._repo.upcoming_reminders(user_id, limit)

    async def upcoming_interviews(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Upcoming interviews for the agenda aggregation (read use-case)."""
        return await self._repo.upcoming_interviews(user_id, limit)

    async def prune_fired_reminders_before(self, cutoff_iso: str) -> int:
        """Retention: prune fired reminders older than ``cutoff_iso`` (owner writes)."""
        return await self._repo.prune_fired_reminders_before(cutoff_iso)


_service: SchedulingService | None = None


def get_scheduling_service() -> SchedulingService:
    global _service
    if _service is None:
        _service = SchedulingService()
    return _service
