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
    "FEATURE_FALLBACK_TOKENS",
    "SpendDecision",
    "credits_for_tokens",
    "describe_balance",
    "estimate_credits",
    "resolve_allowance",
    "resolve_velocity_cap",
    "velocity_exceeded",
]

#: Credits charged per 1,000 tokens. The single conversion between the internal
#: metering unit (tokens) and the user-facing unit (credits). Deliberately a round
#: number so a user can reason about it, and deliberately NOT derived from a
#: provider's price list - if it were, switching provider would silently change what
#: a credit buys, which users experience as being cheated.
CREDITS_PER_1K_TOKENS = 1

#: Conservative per-feature token estimates, used ONLY until the ledger has enough
#: observations to compute a real p95. Sized generously on purpose: a hold that is
#: too small gets truncated at settle time and the operator absorbs the overrun,
#: whereas a hold that is too large only briefly over-reserves.
FEATURE_FALLBACK_TOKENS = {
    "resume_parse": 8000,
    "resume_tailor": 20000,
    "resume_wizard": 6000,
    "cover_letter": 4000,
    "outreach": 2000,
    "interview_prep": 12000,
    "enrichment": 3000,
    "jd_extract": 6000,
    "discovery_recommend": 10000,
    "extension_draft": 2000,
    "match_score": 4000,
}

#: Used when a feature is not in the table at all - better than raising, so a new
#: feature cannot crash a request before it has an estimate.
_DEFAULT_FALLBACK_TOKENS = 8000

#: Multiplier applied to the observed p95 when sizing a hold. The p95 is a typical
#: worst case, not an absolute one; this leaves headroom so most calls settle inside
#: their hold rather than being capped.
_HEADROOM = 1.3


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
    """Size the hold for ``feature`` from what it has actually been costing.

    Falls back to a conservative constant while the ledger is still thin. Never
    raises: an estimate failure must not block a user from working.
    """
    observed: int | None = None
    try:
        observed = await db.feature_usage_percentile(feature, percentile=0.95)
    except Exception:  # pragma: no cover - estimation must never break a request
        logger.warning("Usage percentile lookup failed for %s; using fallback", feature)

    tokens = observed if observed else FEATURE_FALLBACK_TOKENS.get(
        feature, _DEFAULT_FALLBACK_TOKENS
    )
    return credits_for_tokens(int(tokens * _HEADROOM))


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


def describe_balance(available_credits: int, *, feature: str = "resume_tailor") -> str:
    """Turn a credit count into something a human can act on.

    "About 12 more tailorings" answers the question a user actually has. A raw
    credit count does not, which is why credits are never the only thing shown.
    """
    per_action = credits_for_tokens(
        int(FEATURE_FALLBACK_TOKENS.get(feature, _DEFAULT_FALLBACK_TOKENS) * _HEADROOM)
    )
    if per_action <= 0:
        return f"{available_credits} credits"
    actions = available_credits // per_action
    if actions <= 0:
        return "not enough for another tailored resume"
    if actions == 1:
        return "about 1 more tailored resume"
    return f"about {actions} more tailored resumes"
