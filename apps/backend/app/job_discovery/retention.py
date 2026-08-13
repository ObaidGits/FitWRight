"""Feed retention: forgetting jobs that stopped mattering.

Without this the feed only grows. A daily scheduled search adds rows forever, so
after a few months the list is mostly expired postings and "224 opportunities"
stops meaning anything - the number the user trusts becomes the number that
misleads them.

What is kept, and why:

* **Anything the user touched stays.** ``interested``, ``tailored`` and ``applied``
  are decisions, and deleting a decision to save disk is indefensible. Only
  ``new`` (never looked at) and ``dismissed`` (explicitly rejected) are swept.
* **A floor on the window.** The API accepts a retention period but never less
  than a week, because "delete everything older than a day" is a mistake nobody
  makes deliberately.
* **Age is measured from discovery, not posting.** Boards lie about posting dates
  and many omit them; when we found it is the one date we know is true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

__all__ = ["MIN_RETENTION_DAYS", "DEFAULT_RETENTION_DAYS", "sweep_feed", "sweep_all_users"]

# Below a week, a user who takes a holiday loses jobs they never saw.
MIN_RETENTION_DAYS = 7
DEFAULT_RETENTION_DAYS = 30

# Statuses that represent "no decision made" and are therefore safe to forget.
SWEEPABLE = ("new", "dismissed")


async def sweep_feed(db, user_id: str, days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Delete untouched feed rows older than ``days``. Returns how many went.

    Deliberately a delete rather than an archive flag: an archived row still has
    to be filtered out of every query, and a job posting from two months ago has
    no second life. The listing is gone from the board too.
    """
    from sqlalchemy import delete as sa_delete

    from app.models import DiscoveryResult

    window = max(MIN_RETENTION_DAYS, days)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).isoformat()

    async with db._session() as session:  # noqa: SLF001
        async with session.begin():
            result = await session.execute(
                sa_delete(DiscoveryResult).where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.created_at < cutoff)
                    & (DiscoveryResult.status.in_(SWEEPABLE))
                )
            )
            return result.rowcount or 0


async def sweep_all_users(db, days: int = DEFAULT_RETENTION_DAYS) -> int:
    """Sweep every user's feed. Used by the background worker.

    Scoped per user rather than one global delete so a failure cannot take out
    another account's rows, and so the row count is attributable.
    """
    from sqlalchemy import select

    from app.models import DiscoveryResult

    async with db._session() as session:  # noqa: SLF001
        user_ids = (
            (await session.execute(select(DiscoveryResult.user_id).distinct()))
            .scalars()
            .all()
        )

    removed = 0
    for user_id in user_ids:
        removed += await sweep_feed(db, user_id, days)
    return removed
