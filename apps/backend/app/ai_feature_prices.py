"""What each AI action costs, resolved from the database.

This replaced a *variable* charge. Cost used to be the 95th percentile of what the
feature had recently consumed in tokens - the honest figure for what the operator paid,
but an unusable one to quote to a user. A range cannot be displayed as a price, and a
charge that differs from the number the user was shown reads as being cheated. So a
feature now has one published integer price, editable from the admin panel.

Token metering is untouched and still records real consumption. That is what the admin
spend and margin views are built from; it simply no longer decides what the user pays.

ON THE FALLBACK TABLE BELOW: the database is authoritative. ``DEFAULT_FEATURE_PRICES``
exists for two narrow reasons - it is the source the seed script writes into the
database, and it is what a lookup falls back to if a row is missing. A missing price
must never crash a request or, worse, silently charge zero: an unpriced feature that
runs free is a revenue leak nobody notices. It is not a second source of truth; nothing
reads it once the row exists.

CACHING: prices are read on every spend, so they are cached in-process for a short TTL
and the cache is dropped explicitly when an admin edits a price. With more than one
worker the TTL is what bounds staleness, because an invalidation only clears the
process that served the edit - a minute of a stale price is acceptable, an unbounded
stale price is not.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_FEATURE_PRICES",
    "FeatureCost",
    "invalidate_price_cache",
    "resolve_feature_cost",
    "resolve_all_feature_costs",
]


@dataclass(frozen=True)
class FeatureCost:
    """The resolved price of one action."""

    feature: str
    label: str
    credits: int
    is_charged: bool
    description: str | None = None

    @property
    def effective_credits(self) -> int:
        """What the user is actually debited. Zero when the action is free."""
        return int(self.credits) if self.is_charged else 0


#: Seed values AND the missing-row fallback. Keys are the feature strings the spend
#: path already passes; the numbers are the operator's published price list.
DEFAULT_FEATURE_PRICES: dict[str, tuple[str, int, str | None]] = {
    "resume_tailor": ("Tailored resume", 20, "A resume rewritten for one job"),
    "interview_prep": ("Interview prep", 12, "Questions and answers for one role"),
    "resume_parse": ("Resume upload", 8, "Reading a PDF or DOCX you upload"),
    "resume_wizard": ("Resume from scratch", 6, "Building a resume with the wizard"),
    "jd_extract": ("Job description read", 6, "Pulling a job description from a link"),
    "cover_letter": ("Cover letter", 4, "One tailored cover letter"),
    "match_score": ("Match score", 4, "How well a resume fits a job"),
    "enrichment": ("Profile enrichment", 3, "Improving your saved profile"),
    "discovery_recommend": (
        "AI job ranking",
        10,
        "Ranking search results against your resume",
    ),
    "extension_draft": (
        "Application answer",
        2,
        "Per open-ended question drafted for you",
    ),
    "outreach": ("Outreach message", 2, "A message to a recruiter or referral"),
}

#: Features that make up the headline "one application" figure shown to users. Kept
#: here rather than in the UI so the number on the pricing screen and the number in the
#: balance summary cannot drift apart.
APPLICATION_BUNDLE = ("resume_tailor", "cover_letter", "extension_draft")

_CACHE_TTL_SECONDS = 60.0
_cache: dict[str, FeatureCost] = {}
_cache_loaded_at: float = 0.0


def invalidate_price_cache() -> None:
    """Drop the in-process cache. Called after an admin edits a price."""
    global _cache_loaded_at
    _cache.clear()
    _cache_loaded_at = 0.0


def _fallback_cost(feature: str) -> FeatureCost:
    label, credits, description = DEFAULT_FEATURE_PRICES.get(
        feature, (feature.replace("_", " ").capitalize(), 8, None)
    )
    return FeatureCost(
        feature=feature,
        label=label,
        credits=credits,
        is_charged=True,
        description=description,
    )


async def _load_cache(db) -> None:
    global _cache_loaded_at
    rows = await db.list_feature_prices()
    _cache.clear()
    for row in rows:
        _cache[row["feature"]] = FeatureCost(
            feature=row["feature"],
            label=row["label"],
            credits=int(row["credits"]),
            is_charged=bool(row["is_charged"]),
            description=row.get("description"),
        )
    _cache_loaded_at = time.monotonic()


async def _ensure_cache(db) -> None:
    if _cache_loaded_at and (time.monotonic() - _cache_loaded_at) < _CACHE_TTL_SECONDS:
        return
    try:
        await _load_cache(db)
    except Exception:
        # A pricing lookup failure must not take down every AI feature. Falling back
        # to the published list keeps the app usable and keeps charging - failing OPEN
        # to zero here would hand out free usage for as long as the outage lasted.
        logger.warning("Feature price lookup failed; using the built-in price list")


async def resolve_feature_cost(db, feature: str) -> FeatureCost:
    """The price of one action, from the database, with a safe fallback."""
    await _ensure_cache(db)
    cost = _cache.get(feature)
    if cost is None:
        logger.info(
            "No price row for feature %r; using the built-in default. Add it in "
            "Admin > Feature prices to make it editable.",
            feature,
        )
        return _fallback_cost(feature)
    return cost


async def resolve_all_feature_costs(db, *, only_active: bool = True) -> list[FeatureCost]:
    """Every priced action, for the pricing screen."""
    try:
        rows = await db.list_feature_prices(only_active=only_active)
    except Exception:
        logger.warning("Feature price list failed; using the built-in price list")
        return [_fallback_cost(f) for f in DEFAULT_FEATURE_PRICES]
    if not rows:
        return [_fallback_cost(f) for f in DEFAULT_FEATURE_PRICES]
    return [
        FeatureCost(
            feature=r["feature"],
            label=r["label"],
            credits=int(r["credits"]),
            is_charged=bool(r["is_charged"]),
            description=r.get("description"),
        )
        for r in rows
    ]


async def application_bundle_credits(db) -> int:
    """Credits for one complete application - the headline number users see.

    Computed from the same rows the pricing screen renders, so "about 65 applications"
    and the per-action prices can never disagree.
    """
    total = 0
    for feature in APPLICATION_BUNDLE:
        cost = await resolve_feature_cost(db, feature)
        total += cost.effective_credits
    return max(1, total)
