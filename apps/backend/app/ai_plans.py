"""Subscription plans: which tier an account is on, and what that tier allows.

Two things live here.

**The plan a user is on.** ``credit_accounts.plan_id`` is nullable, and a NULL resolves
to the default plan at read time rather than being backfilled. A backfill would miss
every account created after it ran, and this is the same reasoning that made the
monthly allowance a lazy grant rather than a cron: a user cannot be looked at without
their plan being correct.

**Caps on actions that are free but not unlimited.** Job search is deliberately not
charged in credits - metering exploration teaches people to stop exploring, and
exploring is what produces the applications that ARE charged. But an uncapped search is
an invitation to hammer job boards from a residential IP, so each plan carries a
per-day ceiling instead of a price. A rate limit, not a charge, and the difference
matters in the UI too: running out of searches is "back tomorrow", not "pay me".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_PLANS",
    "PlanView",
    "SEARCH_KIND",
    "SearchAllowance",
    "check_search_allowance",
    "consume_search",
    "resolve_account_plan",
]

#: The counter kind for job searches in ``daily_usage_counters``.
SEARCH_KIND = "job_search"


@dataclass(frozen=True)
class PlanView:
    """A plan as the rest of the app needs to see it."""

    id: str
    label: str
    price_minor: int
    currency: str
    monthly_credits: int
    #: ``None`` means uncapped.
    search_daily_limit: int | None
    is_default: bool
    description: str | None = None
    #: True when NO plan row could be resolved and this is the built-in stand-in.
    #: Callers that have their own configured value (the env-var monthly allowance)
    #: must prefer it over this object's numbers: an install that never seeded plans has
    #: still often configured that setting deliberately, and silently overriding it with
    #: a built-in default would change what existing users are granted.
    is_fallback: bool = False

    @property
    def is_free(self) -> bool:
        return int(self.price_minor) <= 0


#: Seed values for a fresh deployment, and the fallback when no plan row exists at all.
#: The database is authoritative once seeded; this exists so an unseeded install still
#: grants a sane free tier instead of granting nobody anything.
#: (id, label, price_minor, monthly_credits, search_daily_limit, is_default, description)
DEFAULT_PLANS: tuple[tuple[str, str, int, int, int | None, bool, str], ...] = (
    (
        "free",
        "Free",
        0,
        300,
        20,
        True,
        "Enough to see it work on a few real jobs",
    ),
    (
        "job_hunt",
        "Job Hunt",
        29900,
        2000,
        100,
        False,
        "For an active search - about 65 applications a month",
    ),
    (
        "serious",
        "Serious Search",
        69900,
        6000,
        300,
        False,
        "For an aggressive search - about 200 applications a month",
    ),
)

_FALLBACK_PLAN = PlanView(
    id="free",
    label="Free",
    price_minor=0,
    currency="INR",
    monthly_credits=300,
    search_daily_limit=20,
    is_default=True,
    description="Enough to see it work on a few real jobs",
    is_fallback=True,
)


def _row_to_plan(row: dict) -> PlanView:
    return PlanView(
        id=row["id"],
        label=row["label"],
        price_minor=int(row["price_minor"]),
        currency=row.get("currency") or "INR",
        monthly_credits=int(row["monthly_credits"]),
        search_daily_limit=row.get("search_daily_limit"),
        is_default=bool(row.get("is_default")),
        description=row.get("description"),
    )


async def resolve_account_plan(db, account: dict | None) -> PlanView:
    """The plan this account is on, resolving NULL and retired plans to the default.

    Never raises and never returns ``None``: every downstream caller needs a monthly
    allowance and a search ceiling, and having no plan at all would mean choosing
    between blocking the user and granting them everything.
    """
    plan_id = (account or {}).get("plan_id")
    try:
        if plan_id:
            row = await db.get_subscription_plan(plan_id)
            if row is not None:
                return _row_to_plan(row)
            # The plan was retired while this account still pointed at it. Fall through
            # to the default rather than failing - the account is not at fault.
            logger.info("Account is on unknown plan %r; falling back to default", plan_id)
        row = await db.get_default_subscription_plan()
        if row is not None:
            return _row_to_plan(row)
    except Exception:
        logger.warning("Plan lookup failed; using the built-in free plan")
    return _FALLBACK_PLAN


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass(frozen=True)
class SearchAllowance:
    """Whether a search may run, and what to tell the user if not."""

    allowed: bool
    used: int
    limit: int | None
    plan_label: str

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, int(self.limit) - int(self.used))


async def check_search_allowance(db, user_id: str, plan: PlanView) -> SearchAllowance:
    """Read-only: how many searches are left today. Safe to call on page load."""
    limit = plan.search_daily_limit
    try:
        used = await db.get_daily_usage(user_id, kind=SEARCH_KIND, day=_today_utc())
    except Exception:
        logger.warning("Search counter read failed; allowing the search")
        return SearchAllowance(allowed=True, used=0, limit=limit, plan_label=plan.label)
    allowed = limit is None or int(used) < int(limit)
    return SearchAllowance(
        allowed=allowed, used=int(used), limit=limit, plan_label=plan.label
    )


async def consume_search(db, user_id: str, plan: PlanView) -> SearchAllowance:
    """Count one search, atomically, and say whether it was permitted.

    The check and the increment are one statement in the repository, because a cap that
    reads then writes lets two concurrent requests both see "19 of 20" and both proceed.
    """
    limit = plan.search_daily_limit
    try:
        allowed, count = await db.increment_daily_usage(
            user_id, kind=SEARCH_KIND, day=_today_utc(), limit=limit
        )
    except Exception:
        # Fail OPEN here, unlike the credit path. This cap protects job boards from
        # volume, not revenue: refusing a paying user's search because a counter write
        # failed is the worse outcome.
        logger.warning("Search counter write failed; allowing the search")
        return SearchAllowance(allowed=True, used=0, limit=limit, plan_label=plan.label)
    return SearchAllowance(
        allowed=allowed, used=int(count), limit=limit, plan_label=plan.label
    )
