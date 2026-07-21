"""SchedulerService - claim-based due scans (design §Platform/§E/§F, R10.3/11.3/16.2).

Single-flighted via the KVStore lock (safe under external-cron + in-process
worker). Two scans per pass:

1. **Reminders** - atomically claim due rows (``pending|snoozed -> firing``),
   emit ``reminder.due`` to the outbox (-> idempotent notification), then either
   materialize the next occurrence (recurring) or mark ``fired``. The claim
   guarantees no double-fire across workers; the notification ``dedupe_key``
   (per occurrence bucket) makes delivery exactly-once (Property 3).
2. **Interviews** - for scheduled interviews inside the lead horizon, emit
   ``interview.upcoming`` for each lead bucket now due (dedupe per
   ``(interview, lead)``) and record it in ``fired_leads``.

Invoked by :func:`app.events.jobs.run_productivity_jobs` (reserved hook).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

__all__ = ["run_due_scans", "SCHEDULER_LOCK_KEY"]

SCHEDULER_LOCK_KEY = "scheduling:scan"
_BATCH = 200
# Widest lead time we support scanning for (bounds the interview scan window).
_MAX_LEAD_MINUTES = 60 * 24 * 14  # 14 days


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


async def run_due_scans(*, kvstore=None) -> dict:
    """Run one single-flighted scheduler pass (reminders + interview leads)."""
    from app.config import settings

    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    lock = kvstore.lock(SCHEDULER_LOCK_KEY, ttl_seconds=120, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"status": "locked"}
        result: dict[str, object] = {}
        if settings.reminders_enabled:
            result["reminders"] = await _scan_reminders()
        if settings.interviews_enabled:
            result["interviews"] = await _scan_interviews()
        return result or {"status": "idle"}


async def _scan_reminders() -> dict[str, int]:
    from app.events import EventType, emit
    from app.scheduling.recurrence import next_occurrence
    from app.scheduling.repo import get_scheduling_repo

    repo = get_scheduling_repo()
    now_iso = _now_dt().isoformat()
    claimed = await repo.claim_due_reminders(now_iso, _BATCH)
    fired = 0
    for r in claimed:
        # dedupe bucket = the occurrence instant that fired (unique per recurrence
        # occurrence, so recurring reminders notify once per occurrence).
        bucket = r["due_at"]
        await emit(
            EventType.REMINDER_DUE,
            {
                "reminder_id": r["id"],
                "application_id": r["application_id"],
                "title": (r.get("note") or "Follow-up reminder")[:120],
                "bucket": bucket,
            },
            user_id=r.get("user_id"),
        )
        nxt = next_occurrence(r["due_at"], r.get("recurrence"), r.get("tz") or "UTC")
        await repo.complete_reminder(r["id"], next_due=nxt)
        fired += 1
    if fired:
        from app.productivity.metrics import get_productivity_metrics

        get_productivity_metrics().reminders_fired(fired)
    return {"claimed": len(claimed), "fired": fired}


async def _scan_interviews() -> dict[str, int]:
    from app.events import EventType, emit
    from app.scheduling.repo import get_scheduling_repo

    repo = get_scheduling_repo()
    now = _now_dt()
    horizon = (now + timedelta(minutes=_MAX_LEAD_MINUTES)).isoformat()
    interviews = await repo.scan_due_interview_leads(now.isoformat(), horizon, _BATCH)
    emitted = 0
    for iv in interviews:
        try:
            starts = datetime.fromisoformat(iv["starts_at"])
        except ValueError:
            continue
        if starts.tzinfo is None:
            starts = starts.replace(tzinfo=timezone.utc)
        fired_leads = set(iv.get("fired_leads") or [])
        for lead in iv.get("lead_times") or []:
            try:
                lead_min = int(lead)
            except (TypeError, ValueError):
                continue
            if lead_min in fired_leads:
                continue
            fire_at = starts - timedelta(minutes=lead_min)
            if fire_at <= now <= starts:
                await emit(
                    EventType.INTERVIEW_UPCOMING,
                    {
                        "interview_id": iv["id"],
                        "application_id": iv["application_id"],
                        "title": f"Interview {_humanize_lead(lead_min)}",
                        "lead_minutes": lead_min,
                    },
                    user_id=iv["user_id"],
                )
                await repo.mark_lead_fired(iv["id"], lead_min)
                emitted += 1
    if emitted:
        from app.productivity.metrics import get_productivity_metrics

        get_productivity_metrics().interview_leads_fired(emitted)
    return {"scanned": len(interviews), "emitted": emitted}


def _humanize_lead(minutes: int) -> str:
    if minutes % (60 * 24) == 0:
        d = minutes // (60 * 24)
        return f"in {d} day{'s' if d != 1 else ''}"
    if minutes % 60 == 0:
        h = minutes // 60
        return f"in {h} hour{'s' if h != 1 else ''}"
    return f"in {minutes} minutes"
