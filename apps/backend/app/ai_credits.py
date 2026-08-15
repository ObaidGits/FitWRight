"""Credit policy: what a feature costs, who may spend, and how much is left.

This sits between the endpoints and the repository. The repository owns atomicity;
this module owns *policy* - the three-tier resolution, the pre-flight estimate, the
velocity cap, and translating credits into something a human understands.

Design notes worth keeping:

* **Estimates come from observed usage, not constants.** A hardcoded guess is either
  so generous it blocks users who could afford the call, or so tight that heavy
  requests overrun their hold. The p95 of what this feature actually cost recently is
  the honest number, with a conservative fallback until enough data exists.

* **Users are shown actions, not credits.** "About 12 more tailorings" is actionable;
  "148 credits" is not. Credits stay the internal unit so switching provider does not
  change what a credit buys.

* **A user on their own API key is metered but never charged.** They cost the
  operator nothing, so billing them would be indefensible - and it makes
  out-of-credits a choice ("add your own key") rather than a wall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

__all__ = [
    "CREDITS_PER_1K_TOKENS",
    "SpendDecision",
    "credits_for_tokens",
    "describe_balance",
    "estimate_credits",
    "resolve_allowance",
    "resolve_velocity_cap",
    "velocity_exceeded",
]

#: Credits charged per 1,000 tokens. Still the conversion used when RECORDING what a
#: call consumed (the ledger, the admin spend view, margin). It no longer decides what
#: a user pays - that is the published per-feature price in ``app.ai_feature_prices``.
CREDITS_PER_1K_TOKENS = 1


@dataclass(frozen=True)
class SpendDecision:
    """Whether a user may run a feature, and why not if they may not."""

    allowed: bool
    #: ``ok`` | ``insufficient`` | ``blocked`` | ``velocity`` | ``disabled_globally``
    #: | ``own_key`` - each maps to a DISTINCT user-facing message. This codebase has
    #: already shipped a bug where an AI credential problem rendered as "You are
    #: offline"; collapsing these states would repeat that class of error.
    reason: str
    estimated_credits: int = 0
    available_credits: int = 0
    #: True when the user supplied their own provider key: metered, charged zero.
    billing_bypassed: bool = False


def credits_for_tokens(total_tokens: int) -> int:
    """Convert measured tokens to credits, rounding UP.

    Rounds up so a stream of tiny calls cannot each round to zero and add up to
    free usage. Integer arithmetic throughout - no floats in a money path.
    """
    tokens = max(0, int(total_tokens))
    if tokens == 0:
        return 0
    per_1k = max(1, int(CREDITS_PER_1K_TOKENS))
    return max(1, -(-tokens * per_1k // 1000))


async def estimate_credits(db, feature: str) -> int:
    """What ``feature`` will cost this user, from the admin-set price list.

    This used to be the 95th percentile of the feature's own recent token usage. That
    is the right number for what the OPERATOR paid, and the wrong one to quote to a
    user: a variable charge cannot be shown as a price before the action runs, and a
    final charge that differs from the number on screen reads as being cheated. The
    published price is now the charge, and it is the same integer the pricing screen
    renders.

    Token metering did not go away - ``ai_usage_ledger`` still records real consumption,
    which is what the admin spend and margin views are built from. It simply no longer
    decides what the user pays.

    Never raises: a pricing lookup failure falls back to the built-in list rather than
    blocking a user from working.
    """
    from app.ai_feature_prices import resolve_feature_cost

    cost = await resolve_feature_cost(db, feature)
    return cost.effective_credits


def resolve_allowance(account: dict, *, global_default: int) -> int:
    """Three-tier resolution for the monthly free grant.

    A per-user override is ABSOLUTE: raising the global default must never
    implicitly widen someone's specific ceiling, or an operator adjusting the
    default silently re-grants to users they had deliberately restricted.
    """
    override = account.get("monthly_allowance_override")
    if override is not None:
        return max(0, int(override))
    return max(0, int(global_default))


def resolve_velocity_cap(account: dict, *, global_default: int) -> int:
    """Credits-per-hour ceiling. ``0`` disables the cap."""
    override = account.get("velocity_cap_override")
    if override is not None:
        return max(0, int(override))
    return max(0, int(global_default))


def velocity_exceeded(
    account: dict, *, cap: int, additional: int, now: datetime | None = None
) -> bool:
    """Would spending ``additional`` credits breach the hourly cap?

    Exists independently of the balance because credits alone do not stop a stolen
    session from draining a funded wallet in one minute. A window older than an hour
    counts as reset.
    """
    if cap <= 0:
        return False
    moment = now or datetime.now(timezone.utc)
    start_raw = account.get("velocity_window_start")
    spent = int(account.get("velocity_spent") or 0)
    if start_raw:
        try:
            if datetime.fromisoformat(start_raw) < moment - timedelta(hours=1):
                spent = 0  # window has rolled over
        except (TypeError, ValueError):
            spent = 0
    return (spent + max(0, additional)) > cap


def describe_balance(
    available_credits: int,
    *,
    per_action_credits: int,
    action_singular: str = "application",
    action_plural: str = "applications",
) -> str:
    """Turn a credit count into something a human can act on.

    "About 12 more applications" answers the question a user actually has; "148 credits"
    does not, which is why credits are never the only thing shown.

    ``per_action_credits`` is passed in rather than looked up here so this stays a pure
    function, and - more importantly - so the number in this sentence is the same one the
    pricing screen shows. Deriving it independently in two places is how a balance
    summary ends up contradicting the price list beside it.
    """
    per_action = max(1, int(per_action_credits))
    actions = max(0, int(available_credits)) // per_action
    if actions <= 0:
        return f"not enough for another {action_singular}"
    if actions == 1:
        return f"about 1 more {action_singular}"
    return f"about {actions} more {action_plural}"
