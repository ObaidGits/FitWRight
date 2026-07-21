"""Reminder + interview data access (design §E/§F) - centralized + user-scoped.

Allow-listed in the scoping guard (same trust model as ``app/admin/repo.py``);
every method is scoped by ``user_id`` (or is a system scheduler scan). The
scheduler uses an **atomic claim** (conditional UPDATE ``pending|snoozed ->
firing`` guarded on the current status) so multiple workers never double-fire the
same reminder (Property 3). A crashed claim is reclaimed after a lease timeout.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update

from app.models import Interview, Reminder

logger = logging.getLogger(__name__)

__all__ = ["SchedulingRepo", "get_scheduling_repo"]

# A claimed-but-not-fired reminder older than this (seconds) is presumed crashed
# and reclaimed on the next scan (self-healing - design §Reliability).
_CLAIM_LEASE_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SchedulingRepo:
    def _sf(self):
        from app import database

        return database.db.session_factory

    # -- serializers --------------------------------------------------------

    @staticmethod
    def _reminder_dict(r: Reminder) -> dict[str, Any]:
        return {
            "id": r.id,
            "user_id": r.user_id,
            "application_id": r.application_id,
            "due_at": r.due_at,
            "tz": r.tz,
            "note": r.note,
            "recurrence": r.recurrence,
            "status": r.status,
            "fired_at": r.fired_at,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }

    @staticmethod
    def _interview_dict(i: Interview) -> dict[str, Any]:
        return {
            "id": i.id,
            "user_id": i.user_id,
            "application_id": i.application_id,
            "starts_at": i.starts_at,
            "tz": i.tz,
            "duration_min": i.duration_min,
            "kind": i.kind,
            "location": i.location,
            "notes": i.notes,
            "lead_times": list(i.lead_times or []),
            "status": i.status,
            "created_at": i.created_at,
            "updated_at": i.updated_at,
        }

    # ======================================================================
    # Reminders
    # ======================================================================

    async def create_reminder(
        self, user_id: str, application_id: str, *, due_at: str, tz: str,
        note: str | None, recurrence: str | None,
    ) -> dict[str, Any]:
        async with self._sf()() as session:
            row = Reminder(
                user_id=user_id, application_id=application_id, due_at=due_at, tz=tz,
                note=note, recurrence=recurrence, status="pending",
                created_at=_now(), updated_at=_now(),
            )
            session.add(row)
            await session.commit()
            return self._reminder_dict(row)

    async def get_reminder(self, user_id: str, reminder_id: str) -> dict[str, Any] | None:
        async with self._sf()() as session:
            row = await session.get(Reminder, reminder_id)
            if row is None or row.user_id != user_id:
                return None
            return self._reminder_dict(row)

    async def list_reminders(self, user_id: str, application_id: str) -> list[dict[str, Any]]:
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Reminder)
                    .where(Reminder.user_id == user_id, Reminder.application_id == application_id)
                    .order_by(Reminder.due_at)
                )
            ).scalars().all()
            return [self._reminder_dict(r) for r in rows]

    async def update_reminder(
        self, user_id: str, reminder_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._sf()() as session:
            row = await session.get(Reminder, reminder_id)
            if row is None or row.user_id != user_id:
                return None
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            row.updated_at = _now()
            await session.commit()
            return self._reminder_dict(row)

    async def count_active_reminders(self, user_id: str) -> int:
        from sqlalchemy import func

        async with self._sf()() as session:
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(Reminder).where(
                            Reminder.user_id == user_id,
                            Reminder.status.in_(("pending", "snoozed", "firing")),
                        )
                    )
                ).scalar() or 0
            )

    async def claim_due_reminders(self, now_iso: str, limit: int) -> list[dict[str, Any]]:
        """Atomically claim due pending/snoozed reminders (pending->firing).

        Also reclaims stale ``firing`` rows whose claim lease elapsed (crashed
        worker). Each claim is a guarded conditional UPDATE, so across workers a
        given reminder is claimed by exactly one (Property 3).
        """
        lease_cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=_CLAIM_LEASE_SECONDS)
        ).isoformat()
        async with self._sf()() as session:
            candidates = (
                await session.execute(
                    select(Reminder.id)
                    .where(
                        Reminder.due_at <= now_iso,
                        or_(
                            Reminder.status.in_(("pending", "snoozed")),
                            and_(Reminder.status == "firing", Reminder.claimed_at < lease_cutoff),
                        ),
                    )
                    .order_by(Reminder.due_at)
                    .limit(limit)
                )
            ).scalars().all()

        claimed: list[dict[str, Any]] = []
        for rid in candidates:
            async with self._sf()() as session:
                result = await session.execute(
                    update(Reminder)
                    .where(
                        Reminder.id == rid,
                        or_(
                            Reminder.status.in_(("pending", "snoozed")),
                            and_(Reminder.status == "firing", Reminder.claimed_at < lease_cutoff),
                        ),
                    )
                    .values(status="firing", claimed_at=_now())
                )
                if (result.rowcount or 0) == 1:
                    await session.commit()
                    row = await session.get(Reminder, rid)
                    if row is not None:
                        claimed.append(self._reminder_dict(row))
                else:
                    await session.rollback()
        return claimed

    async def complete_reminder(self, reminder_id: str, *, next_due: str | None) -> None:
        """Finalize a fired reminder: materialize the next occurrence or mark fired."""
        async with self._sf()() as session:
            row = await session.get(Reminder, reminder_id)
            if row is None:
                return
            row.fired_at = _now()
            if next_due is not None:
                # Recurring: re-arm the same row for the next occurrence (no
                # infinite rows - R10.2). Reset claim bookkeeping.
                row.due_at = next_due
                row.status = "pending"
                row.claimed_at = None
            else:
                row.status = "fired"
            row.updated_at = _now()
            await session.commit()

    # ======================================================================
    # Interviews
    # ======================================================================

    async def create_interview(
        self, user_id: str, application_id: str, *, starts_at: str, tz: str,
        duration_min: int, kind: str, location: str | None, notes: str | None,
        lead_times: list[int],
    ) -> dict[str, Any]:
        async with self._sf()() as session:
            row = Interview(
                user_id=user_id, application_id=application_id, starts_at=starts_at, tz=tz,
                duration_min=duration_min, kind=kind, location=location, notes=notes,
                lead_times=lead_times, fired_leads=[], status="scheduled",
                created_at=_now(), updated_at=_now(),
            )
            session.add(row)
            await session.commit()
            return self._interview_dict(row)

    async def get_interview(self, user_id: str, interview_id: str) -> dict[str, Any] | None:
        async with self._sf()() as session:
            row = await session.get(Interview, interview_id)
            if row is None or row.user_id != user_id:
                return None
            return self._interview_dict(row)

    async def list_interviews(self, user_id: str, application_id: str) -> list[dict[str, Any]]:
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Interview)
                    .where(Interview.user_id == user_id, Interview.application_id == application_id)
                    .order_by(Interview.starts_at)
                )
            ).scalars().all()
            return [self._interview_dict(r) for r in rows]

    async def update_interview(
        self, user_id: str, interview_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        async with self._sf()() as session:
            row = await session.get(Interview, interview_id)
            if row is None or row.user_id != user_id:
                return None
            reschedule = "starts_at" in fields and fields["starts_at"] != row.starts_at
            for k, v in fields.items():
                if hasattr(row, k):
                    setattr(row, k, v)
            if reschedule:
                row.fired_leads = []  # re-arm lead-time notifications
            row.updated_at = _now()
            await session.commit()
            return self._interview_dict(row)

    async def count_active_interviews(self, user_id: str) -> int:
        from sqlalchemy import func

        async with self._sf()() as session:
            return int(
                (
                    await session.execute(
                        select(func.count()).select_from(Interview).where(
                            Interview.user_id == user_id, Interview.status == "scheduled"
                        )
                    )
                ).scalar() or 0
            )

    async def find_overlapping(
        self, user_id: str, starts_at: str, ends_at: str, *, exclude_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Scheduled interviews overlapping [starts_at, ends_at) (soft warning)."""
        # Lower bound = new start − 1 day (durations are ≤24h) so a long
        # cross-midnight interview starting the previous day is still considered.
        lower = (
            datetime.fromisoformat(starts_at) - timedelta(days=1)
        ).isoformat()
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Interview).where(
                        Interview.user_id == user_id,
                        Interview.status == "scheduled",
                        # Prefilter: existing.start < new.end AND existing.start >= new.start − 1d
                        Interview.starts_at < ends_at,
                        Interview.starts_at >= lower,
                    )
                )
            ).scalars().all()
            out = []
            for r in rows:
                if exclude_id and r.id == exclude_id:
                    continue
                # Precise overlap check using duration.
                r_end = _add_minutes(r.starts_at, r.duration_min)
                if r.starts_at < ends_at and r_end > starts_at:
                    out.append(self._interview_dict(r))
            return out

    async def scan_due_interview_leads(
        self, now_iso: str, horizon_iso: str, limit: int
    ) -> list[dict[str, Any]]:
        """Return scheduled interviews within the lead horizon (for lead-time fire)."""
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Interview)
                    .where(
                        Interview.status == "scheduled",
                        Interview.starts_at >= now_iso,
                        Interview.starts_at <= horizon_iso,
                    )
                    .order_by(Interview.starts_at)
                    .limit(limit)
                )
            ).scalars().all()
            return [
                {**self._interview_dict(r), "user_id": r.user_id, "fired_leads": list(r.fired_leads or [])}
                for r in rows
            ]

    async def mark_lead_fired(self, interview_id: str, lead: int) -> None:
        async with self._sf()() as session:
            row = await session.get(Interview, interview_id)
            if row is None:
                return
            fired = list(row.fired_leads or [])
            if lead not in fired:
                fired.append(lead)
                row.fired_leads = fired
                row.updated_at = _now()
                await session.commit()

    # ======================================================================
    # Agenda (merged upcoming) + retention
    # ======================================================================

    async def upcoming_reminders(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Reminder)
                    .where(
                        Reminder.user_id == user_id,
                        Reminder.status.in_(("pending", "snoozed", "firing")),
                    )
                    .order_by(Reminder.due_at)
                    .limit(limit)
                )
            ).scalars().all()
            return [self._reminder_dict(r) for r in rows]

    async def upcoming_interviews(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Interview)
                    .where(Interview.user_id == user_id, Interview.status == "scheduled")
                    .order_by(Interview.starts_at)
                    .limit(limit)
                )
            ).scalars().all()
            return [self._interview_dict(r) for r in rows]

    async def prune_fired_reminders_before(self, cutoff_iso: str) -> int:
        """Delete fired non-recurring reminders older than cutoff (R17.4)."""
        from sqlalchemy import delete

        async with self._sf()() as session:
            result = await session.execute(
                delete(Reminder).where(
                    Reminder.status == "fired",
                    Reminder.recurrence.is_(None),
                    Reminder.fired_at < cutoff_iso,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)


def _add_minutes(iso: str, minutes: int) -> str:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(minutes=minutes or 0)).isoformat()


_repo: SchedulingRepo | None = None


def get_scheduling_repo() -> SchedulingRepo:
    global _repo
    if _repo is None:
        _repo = SchedulingRepo()
    return _repo
