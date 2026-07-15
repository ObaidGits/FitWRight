"""Productivity background jobs (design §Platform, ADR-15).

A single entrypoint the worker triggers invoke — the free tier's external cron
(``POST /api/v1/internal/run-jobs``) and the premium in-process scheduler both
call :func:`run_productivity_jobs`. Every step is single-flighted + idempotent so
overlapping triggers never double-process:

1. drain a bounded outbox batch → notifications/search (at-least-once, idempotent);
2. send pending immediate notification emails (honoring prefs);
3. run scheduler scans for due reminders / upcoming interviews (claim → emit).

Digest emails + retention run on their own (slower) cadence via
:func:`run_productivity_digests` and the retention job.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["run_productivity_jobs", "run_productivity_digests"]


async def run_productivity_jobs(*, kvstore=None) -> dict:
    """Drain the outbox, send immediate emails, and run scheduler scans."""
    from app.events.outbox import process_outbox_batch
    from app.notifications.consumer import ensure_registered
    from app.notifications.service import get_notification_service
    from app.profile.analytics_consumer import ensure_analytics_registered
    from app.search.indexer import ensure_search_consumers_registered

    ensure_registered()
    ensure_search_consumers_registered()
    ensure_analytics_registered()

    result: dict[str, object] = {}
    # 1) Scheduler scans emit events into the outbox first, so the same pass can
    #    then drain them into notifications (lower end-to-end latency).
    try:
        from app.scheduling.scheduler import run_due_scans

        result["scheduler"] = await run_due_scans(kvstore=kvstore)
    except ModuleNotFoundError:
        result["scheduler"] = {"status": "not_configured"}
    except Exception:  # pragma: no cover - one job must not fail the batch
        logger.exception("Scheduler scan failed")
        result["scheduler"] = {"status": "error"}

    try:
        result["outbox"] = await process_outbox_batch(kvstore=kvstore)
    except Exception:  # pragma: no cover
        logger.exception("Outbox drain failed")
        result["outbox"] = {"status": "error"}

    try:
        result["emails"] = await get_notification_service().process_pending_emails()
    except Exception:  # pragma: no cover
        logger.exception("Notification email send failed")
        result["emails"] = {"status": "error"}

    return result


async def run_productivity_digests() -> dict:
    """Send batched digest emails (slower cadence)."""
    from app.notifications.service import get_notification_service

    return await get_notification_service().process_digests()
