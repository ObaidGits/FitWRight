"""Retention / archival worker (design §Retention, R17.4).

Prunes unbounded growth on a slow cadence, single-flighted via the KVStore lock:
- read/dismissed **notifications** older than ``NOTIFICATION_RETENTION_DAYS``;
- processed **outbox** rows older than ``OUTBOX_RETENTION_DAYS`` (dead-lettered
  rows are retained for the operator to inspect/replay);
- (snapshot caps are enforced inline on write; a future sweep can reconcile).

Idempotent + resumable - each run re-scans from scratch, so a crash mid-batch is
recovered next run. Windows are configurable; the job never touches live/unread
data or the audit log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete

logger = logging.getLogger(__name__)

__all__ = ["run_retention_jobs", "RETENTION_LOCK_KEY"]

RETENTION_LOCK_KEY = "productivity:retention"
_LOCK_TTL = 300


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


async def run_retention_jobs(*, kvstore=None) -> dict:
    """Prune expired notifications + processed outbox rows (single-flighted)."""
    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    lock = kvstore.lock(RETENTION_LOCK_KEY, ttl_seconds=_LOCK_TTL, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"status": "locked"}

        from app.config import settings
        from app.notifications.service import get_notification_service

        # Prune through the owning modules' services - retention orchestrates,
        # each module remains the sole writer of its tables (Amendment E).
        notifications_pruned = await get_notification_service().prune_read_before(
            _cutoff_iso(settings.notification_retention_days)
        )
        outbox_pruned = await _prune_processed_outbox(_cutoff_iso(settings.outbox_retention_days))

        from app.scheduling.service import get_scheduling_service

        reminders_pruned = await get_scheduling_service().prune_fired_reminders_before(
            _cutoff_iso(settings.notification_retention_days)
        )
        avatars_reclaimed = await _reclaim_orphan_avatars()
        return {
            "status": "ok",
            "notifications_pruned": notifications_pruned,
            "outbox_pruned": outbox_pruned,
            "reminders_pruned": reminders_pruned,
            "avatars_reclaimed": avatars_reclaimed,
        }


async def _reclaim_orphan_avatars() -> int:
    """Delete stored avatar objects no longer referenced by any user (R13.2).

    Local provider only (the on-disk sweep); hosted CDN objects are reclaimed by
    the provider's lifecycle rules. Best-effort - never raises into the batch.
    """
    from app.config import settings

    if settings.storage_provider != "local":
        return 0
    try:
        from app.auth.accounts import all_avatar_keys

        referenced = await all_avatar_keys()
        root = (settings.data_dir / "avatars").resolve()
        if not root.exists():
            return 0
        reclaimed = 0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            key = str(path.relative_to(root))
            if key not in referenced:
                path.unlink(missing_ok=True)
                reclaimed += 1
        return reclaimed
    except Exception:  # pragma: no cover - GC is best-effort
        logger.debug("Orphan avatar reclaim failed", exc_info=True)
        return 0


async def _prune_processed_outbox(cutoff_iso: str) -> int:
    """Delete processed (non-dead) outbox rows older than ``cutoff_iso``."""
    from app import database
    from app.models import Outbox

    async with database.db.session_factory() as session:
        result = await session.execute(
            delete(Outbox).where(
                Outbox.processed_at.is_not(None),
                Outbox.dead_at.is_(None),
                Outbox.created_at < cutoff_iso,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
