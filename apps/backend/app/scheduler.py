"""Background scheduling for the session reaper (ADR-15, Package C).

The session reaper (``SessionService.reap``) batch-deletes expired/long-revoked
sessions and expired verification/reset/email-change tokens. *When* it runs is
governed by ``SCHEDULER_MODE`` (design `§Session mechanics`):

- ``external_cron`` (free-tier default): nothing runs in-process; an external
  scheduler (GitHub Actions / cron-job.org) calls the authenticated internal
  endpoint ``POST /api/v1/internal/run-jobs`` (see ``app.routers.internal``).
- ``internal`` (premium): the lightweight :func:`reaper_loop` below runs inside
  the app process on an interval, started in the FastAPI ``lifespan`` and
  cancelled cleanly on shutdown.

Either way the reaper itself is single-flighted via the KVStore lock, so even if
several workers each run the ``internal`` loop (or an ``external_cron`` call
overlaps a worker) only one batch executes at a time.

The loop takes its collaborators by injection (the sleep primitive and the
service accessor) so it is unit-testable with a fake clock/event and never
sleeps for real in tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Awaitable, Callable

from app.auth.sessions import SessionService, get_session_service

logger = logging.getLogger(__name__)

__all__ = [
    "reaper_loop",
    "start_reaper",
    "stop_reaper",
    "admin_jobs_loop",
    "start_admin_jobs",
    "stop_admin_jobs",
]


async def reaper_loop(
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    get_service: Callable[[], SessionService] = get_session_service,
) -> None:
    """Run :meth:`SessionService.reap` forever, once per ``interval_seconds``.

    A single failed batch is logged and swallowed so a transient DB/KVStore blip
    never tears the loop down. The loop exits only on cancellation (clean
    shutdown), which propagates out of the injected ``sleep``.

    ``sleep`` and ``get_service`` are injectable purely for testing; production
    uses ``asyncio.sleep`` and the process-wide session service.
    """
    logger.info("Internal reaper loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            counts = await get_service().reap()
            if any(counts.values()):
                logger.info("Reaper batch removed %s", counts)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive; must not kill the loop
            logger.exception("Reaper batch failed; will retry next interval")
        await sleep(interval_seconds)


def start_reaper(interval_seconds: float) -> asyncio.Task[None]:
    """Create and return the background reaper task (``internal`` mode)."""
    return asyncio.create_task(reaper_loop(interval_seconds), name="session-reaper")


async def admin_jobs_loop(
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    run_jobs: Callable[[], Awaitable[dict]] | None = None,
) -> None:
    """Run the P2 admin jobs (rollup + purge) once per ``interval_seconds``.

    ``internal`` (premium) mode only — the free tier drives the same jobs from
    the external-cron ``run-jobs`` endpoint. Each job is single-flighted +
    idempotent, so running on this interval never double-counts (rollup writes
    only closed days) and only purges grace-elapsed users. A failed batch is
    logged and swallowed so a transient blip never tears the loop down; the loop
    exits only on cancellation.
    """
    if run_jobs is None:
        from app.admin.jobs import run_admin_jobs

        run_jobs = run_admin_jobs
    logger.info("Internal admin-jobs loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            result = await run_jobs()
            logger.info("Admin jobs batch: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive; must not kill the loop
            logger.exception("Admin jobs batch failed; will retry next interval")
        await sleep(interval_seconds)


def start_admin_jobs(interval_seconds: float) -> asyncio.Task[None]:
    """Create and return the background admin-jobs task (``internal`` mode)."""
    return asyncio.create_task(admin_jobs_loop(interval_seconds), name="admin-jobs")


async def stop_admin_jobs(task: asyncio.Task[None] | None) -> None:
    """Cancel the admin-jobs task and await it (idempotent, None-safe)."""
    await stop_reaper(task)


async def stop_reaper(task: asyncio.Task[None] | None) -> None:
    """Cancel the reaper task and await it, swallowing the cancellation.

    Idempotent and safe to call with ``None`` (nothing was started) or an
    already-finished task, so the ``lifespan`` shutdown never leaks the task or
    raises on the way out.
    """
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
