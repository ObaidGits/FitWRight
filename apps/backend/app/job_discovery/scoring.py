"""Scoring feed rows that arrived without a match score.

A job only carries a score if it was matched against a resume. Anything harvested
by keyword - which is most of a feed, and everything the extension brings back -
stores 0.0. The visible consequence was a match filter that could only ever return
nothing, and a ranking the user had no reason to trust.

Why this is a separate, user-initiated pass rather than part of ingest: scoring
reads each job description through the keyword extractor, which is an LLM call per
job. It is content-cached, so re-scoring the same posting is free, but the first
pass over 200 jobs is 200 calls. Spending that because someone pressed Search once
would be a bill they did not agree to. So the count is shown, the call is bounded,
and the user chooses.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["count_unscored", "score_unscored_results"]


async def count_unscored(db, user_id: str) -> int:
    """How many feed rows have no score yet."""
    from sqlalchemy import func, select

    from app.models import DiscoveryResult

    async with db._session() as session:  # noqa: SLF001
        return (
            await session.execute(
                select(func.count(DiscoveryResult.id)).where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.match_score <= 0)
                )
            )
        ).scalar() or 0


async def score_unscored_results(
    db, user_id: str, resume: dict[str, Any], *, limit: int = 40
) -> tuple[int, int]:
    """Score up to ``limit`` unscored rows. Returns (scored, remaining).

    Newest first: a user scoring a slice of their feed wants the jobs they are
    about to look at, not the oldest ones nearest expiry.

    A row that fails to score is left at zero rather than marked, so a transient
    LLM outage costs a retry rather than permanently branding the job unscorable.
    """
    from sqlalchemy import select

    from app.job_discovery.models import JobListing
    from app.job_discovery.ranker import rank_listings
    from app.models import DiscoveryResult

    processed = resume.get("processed_data")
    if isinstance(processed, str):
        import json

        try:
            processed = json.loads(processed)
        except json.JSONDecodeError:
            processed = None
    if not isinstance(processed, dict):
        # Without structured resume data there is nothing to match against, and a
        # score of zero for every job would be worse than no score at all.
        return 0, await count_unscored(db, user_id)

    async with db._session() as session:  # noqa: SLF001
        rows = (
            (
                await session.execute(
                    select(DiscoveryResult)
                    .where(
                        (DiscoveryResult.user_id == user_id)
                        & (DiscoveryResult.match_score <= 0)
                    )
                    .order_by(DiscoveryResult.created_at.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0, 0

        listings = [
            JobListing(
                fingerprint=r.fingerprint,
                source=r.source,
                title=r.title,
                company=r.company,
                location=r.location,
                url=r.url,
                is_remote=r.is_remote,
                description=r.description,
                salary=r.salary,
            )
            for r in rows
        ]

        try:
            recommendations = await rank_listings(user_id, listings, processed)
        except Exception:  # noqa: BLE001
            logger.exception("Scoring feed rows failed for user %s", user_id)
            return 0, await count_unscored(db, user_id)

        by_fingerprint = {rec.listing.fingerprint: rec for rec in recommendations}
        scored = 0
        async with session.begin():
            for row in rows:
                rec = by_fingerprint.get(row.fingerprint)
                if rec is None or not rec.match_score:
                    continue
                row.match_score = rec.match_score
                row.matched_keywords = list(rec.matched)
                row.missing_keywords = list(rec.missing)
                scored += 1

    return scored, await count_unscored(db, user_id)
