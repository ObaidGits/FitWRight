"""The free monthly allowance: granting it, renewing it, and cleaning up after it.

TWO DESIGN DECISIONS WORTH THE WORDS

**Lazy, not scheduled.** The allowance is granted the first time a user's account is
touched, and renewed on the first touch after their period rolls over. A cron that
walks every account was the obvious alternative and is worse in three ways: users
created between ticks start with nothing, a missed tick silently denies everyone
their month, and existing users need a separate one-off backfill that will itself miss
anyone created while it runs. Doing it on touch means a user cannot be looked at
without their allowance being correct, and the backfill for existing users is simply
the first time each of them uses AI.

The scheduled job remains as a SAFETY NET, so an operator viewing a dormant user's
balance sees a current figure rather than last month's.

**Idempotent per (user, period), UTC-anchored.** The key is
``allowance:<user>:<YYYY-MM>``, so a double-run, a retried request, two concurrent
requests, or the job racing a live request all collapse to one grant. UTC rather than
local time because "the month" must not depend on which server answered, and a
timezone-dependent boundary is a bug that only appears for some users at some hours.

Note the deliberate asymmetry: the allowance REPLACES rather than accumulates
(use-it-or-lose-it), while purchased credits are additive and never expire. Rolling
the free grant over would let a dormant account build a balance the operator has to
honour indefinitely.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.ai_credits import resolve_allowance
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "current_period",
    "ensure_allowance",
    "run_credit_maintenance_job",
]


def current_period(now: datetime | None = None) -> str:
    """The allowance period this instant falls in, as ``YYYY-MM`` in UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    return f"{moment.year:04d}-{moment.month:02d}"


def _period_of(iso: str | None) -> str | None:
    """The period an existing ``allowance_period_start`` belongs to."""
    if not iso:
        return None
    try:
        return current_period(datetime.fromisoformat(iso))
    except (TypeError, ValueError):
        # An unparseable stamp is treated as "no period", so the next touch
        # re-grants and repairs the row rather than leaving the user stuck.
        return None


async def ensure_allowance(user_id: str, *, account: dict | None = None) -> dict | None:
    """Grant or renew this user's free allowance if their period has rolled.

    Returns the account as it now stands, or ``None`` when credits are disabled.
    Safe to call on every touch: after the first call in a period it is one cheap
    comparison and no write.
    """
    if not settings.ai_credits_enabled:
        return None

    from app.database import db

    acct = account or await db.get_or_create_credit_account(user_id)
    period = current_period()
    if _period_of(acct.get("allowance_period_start")) == period:
        return acct

    # The monthly grant now comes from the user's PLAN, not one global number. A
    # per-user override still wins over both, because an operator who deliberately
    # restricted (or comped) one account must not have that silently widened by a plan
    # edit. Falls back to the global setting only when no plan can be resolved at all.
    from app.ai_plans import resolve_account_plan

    plan = await resolve_account_plan(db, acct)
    # A real plan row wins. When none exists, the operator's configured setting wins
    # over the built-in stand-in - an install that never seeded plans has often still
    # set AI_MONTHLY_ALLOWANCE_CREDITS deliberately, and quietly replacing it with a
    # default would change what its existing users are granted.
    plan_default = (
        settings.ai_monthly_allowance_credits
        if plan.is_fallback
        else plan.monthly_credits
    )
    amount = resolve_allowance(acct, global_default=plan_default)
    status = await db.grant_credits(
        user_id,
        credits=amount,
        kind="allowance",
        # The whole idempotency story in one string.
        idempotency_key=f"allowance:{user_id}:{period}",
        reason=f"Monthly {'free' if plan.is_fallback else plan.label} allowance for {period}",
        to_wallet=False,
        period_start=datetime.now(timezone.utc).isoformat(),
    )
    if status == "no_account":  # pragma: no cover - created immediately above
        return acct
    return await db.get_or_create_credit_account(user_id)


async def run_credit_maintenance_job(*, kvstore=None) -> dict:
    """Release expired holds, and top up accounts whose period has rolled.

    The sweep is the part that must not be skipped. A reservation whose request died
    without releasing (a killed worker, a lost connection) otherwise holds credits
    forever: the user sees a balance they cannot spend and no error explaining why,
    which is indistinguishable from theft from their point of view.

    Refill here is a safety net only - see the module docstring. It walks accounts
    that are already stale rather than every account, so the cost is proportional to
    the work actually needed.
    """
    from app.database import db

    result: dict[str, object] = {"status": "ok", "swept": 0, "refilled": 0}

    try:
        result["swept"] = await db.sweep_expired_reservations()
    except Exception:
        logger.exception("Reservation sweep failed")
        result["status"] = "partial"

    # Retention and reconciliation run regardless of the flag: rows and holds can
    # exist from before it was turned off, and leaving them unattended is how a
    # disabled feature still causes a problem.
    try:
        from app.ai_retention import reconcile_credits, trim_usage_ledger

        result["trimmed"] = await trim_usage_ledger()
        report = await reconcile_credits()
        result["reconciliation"] = report
        if report.get("status") == "attention":
            # Loud, because every counter in there should be zero.
            logger.error("Credit reconciliation found problems: %s", report.get("findings"))
    except Exception:
        logger.exception("Retention or reconciliation step failed")
        result["status"] = "partial"

    if not settings.ai_credits_enabled:
        result["refilled"] = 0
        return result

    try:
        period = current_period()
        stale = await db.list_accounts_needing_refill(period=period, limit=500)
        refilled = 0
        for user_id in stale:
            try:
                await ensure_allowance(user_id)
                refilled += 1
            except Exception:
                # One bad account must not stop the rest from being topped up.
                logger.warning("Could not refill allowance for one account")
        result["refilled"] = refilled
    except Exception:
        logger.exception("Allowance refill sweep failed")
        result["status"] = "partial"

    return result
