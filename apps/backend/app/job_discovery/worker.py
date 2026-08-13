"""Background discovery worker — runs scheduled discovery for users.

Follows the same asyncio-loop pattern as app/scheduler.py. Started in the
FastAPI lifespan when scheduler_mode == "internal" and JOB_DISCOVERY is on.

The worker wakes every 5 minutes, finds runs whose next_run_at <= now, and
executes discovery for each (sequentially to stay within memory/rate limits).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 300  # 5 minutes between checks
_task: asyncio.Task | None = None
_running_lock = asyncio.Lock()  # Single-flight: only one execution at a time


async def _execute_one_run(run: dict[str, Any]) -> None:
    """Execute a single discovery run and persist results."""
    from app.config import settings
    from app.database import db
    from app.job_discovery.models import SearchFilters
    from app.job_discovery.service import (
        DiscoveryService,
        ResumeData,
        ResumeNotFoundError,
    )

    run_id = run["id"]
    user_id = run["user_id"]
    resume_id = run["resume_id"]
    interval_hours = run.get("interval_hours", 24)

    # Calculate next run time
    next_run = (datetime.now(timezone.utc) + timedelta(hours=interval_hours)).isoformat()

    try:
        # Load resume
        resume_dict = await db.get_resume(user_id, resume_id)
        if resume_dict is None:
            await db.update_discovery_run_status(
                run_id, status="error", error="Resume not found or deleted",
                next_run_at=next_run,
            )
            return

        text = resume_dict.get("content") or ""
        processed = resume_dict.get("processed_data")
        if not text and processed and isinstance(processed, dict):
            parts = []
            for section in ("personal_info", "experience", "education", "skills", "projects"):
                val = processed.get(section)
                if val:
                    parts.append(str(val))
            text = "\n".join(parts)

        version = resume_dict.get("updated_at") or resume_id

        async def loader(uid: str, rid: str) -> ResumeData | None:
            if uid == user_id and rid == resume_id:
                return ResumeData(
                    resume_id=resume_id, text=text,
                    processed=processed, version=str(version),
                )
            return None

        # Run discovery
        svc = DiscoveryService(db, resume_loader=loader, config=settings)
        result = await svc.recommend(
            user_id=user_id,
            resume_id=resume_id,
            filters=SearchFilters(),
            force_refresh=True,
        )

        # Convert recommendations to dicts for storage
        results_to_store = []
        for rec in result.recommendations:
            listing = rec.listing
            results_to_store.append({
                "fingerprint": listing.fingerprint,
                "source": listing.source,
                "title": listing.title,
                "company": listing.company,
                "location": listing.location,
                "url": listing.url,
                "is_remote": listing.is_remote,
                "description": listing.description,
                "salary": listing.salary,
                "posted_at": listing.posted_at.isoformat() if listing.posted_at else None,
                "match_score": rec.match_score,
                "matched": list(rec.matched),
                "missing": list(rec.missing),
                "partial": rec.partial,
            })

        # Persist (deduped by fingerprint)
        new_count = await db.upsert_discovery_results(user_id, run_id, results_to_store)

        await db.update_discovery_run_status(
            run_id, status="success", results_count=new_count,
            next_run_at=next_run,
        )
        logger.info(
            "Discovery run %s for user %s: %d new results (of %d total)",
            run_id, user_id, new_count, len(results_to_store),
        )

    except ResumeNotFoundError:
        await db.update_discovery_run_status(
            run_id, status="error", error="Resume not found",
            next_run_at=next_run,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Discovery run %s failed: %s", run_id, exc)
        await db.update_discovery_run_status(
            run_id, status="error", error=str(exc)[:500],
            next_run_at=next_run,
        )


async def discovery_worker_loop(
    poll_interval: int = _POLL_INTERVAL,
    sleep_fn=asyncio.sleep,
) -> None:
    """Main worker loop — poll for due runs and execute them."""
    from app.config import settings
    from app.database import db

    logger.info("Discovery background worker started (poll every %ds)", poll_interval)

    while True:
        try:
            await sleep_fn(poll_interval)

            if not settings.JOB_DISCOVERY:
                continue

            # Single-flight guard: skip if a previous cycle is still running
            if _running_lock.locked():
                logger.debug("Discovery worker: previous cycle still running, skipping")
                continue

            async with _running_lock:
                due_runs = await db.list_due_discovery_runs(limit=5)
                if not due_runs:
                    continue

                logger.info("Discovery worker: %d runs due", len(due_runs))
                for run in due_runs:
                    await _execute_one_run(run)
                    # Brief pause between runs to be gentle on scrapers
                    await sleep_fn(5)

        except asyncio.CancelledError:
            logger.info("Discovery worker cancelled")
            break
        except Exception:  # noqa: BLE001
            logger.exception("Discovery worker loop error (will retry next cycle)")


def start_discovery_worker() -> asyncio.Task:
    """Start the background discovery worker. Returns the task handle."""
    global _task
    _task = asyncio.create_task(discovery_worker_loop(), name="discovery-worker")
    return _task


def stop_discovery_worker() -> None:
    """Cancel the discovery worker."""
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
