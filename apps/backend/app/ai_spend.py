"""The one entry point an AI endpoint calls to spend credits safely.

Endpoints should not orchestrate reserve/settle/release themselves - doing it in
each of the eight AI features would guarantee that one of them eventually forgets
to release on failure and silently bills users for the operator's outages. This
gives them a context manager instead:

    async with ai_spend(user_id, feature="resume_tailor") as spend:
        result = await do_the_ai_call()
        spend.record(total_tokens=result.usage.total_tokens, channel_id=...)

Guarantees the caller gets for free:

* Refused before any work starts if the balance is short (never mid-save).
* The hold is released on ANY exception, so a provider 5xx or a bug in our own
  code cannot charge the user.
* A zero-charge ledger row is written for failures, so "we did not bill for this"
  is provable rather than merely absent.
* A user on their own API key is metered and charged nothing.
* With the feature flag off, the whole thing is a no-op passthrough.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.ai_credits import (
    SpendDecision,
    credits_for_tokens,
    estimate_credits,
    resolve_velocity_cap,
    velocity_exceeded,
)
from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

__all__ = ["InsufficientCredits", "SpendHandle", "ai_spend", "check_can_spend"]


class InsufficientCredits(ApiError):
    """Raised before any work begins when the user cannot afford the feature.

    A DISTINCT error from "AI not configured" and from "all channels down". This
    codebase has already shipped a bug where an AI credential problem rendered as
    "You are offline" and sent users to check their wifi; three different causes
    must not collapse into one message again.
    """

    def __init__(self, *, needed: int, available: int, feature: str):
        super().__init__(
            402,
            "insufficient_credits",
            "You've used your AI credits for now. You can add your own provider key "
            "in Settings to keep going for free, or top up your credits.",
            details={"needed": needed, "available": available, "feature": feature},
        )
        self.needed = needed
        self.available = available


@dataclass
class SpendHandle:
    """Passed to the caller so it can report what the call actually used."""

    feature: str
    user_id: str
    reservation_id: str | None = None
    billing_bypassed: bool = False
    _recorded: bool = field(default=False, repr=False)
    _tokens: int = field(default=0, repr=False)
    _prompt_tokens: int = field(default=0, repr=False)
    _completion_tokens: int = field(default=0, repr=False)
    _estimated: bool = field(default=False, repr=False)
    _channel_id: str | None = field(default=None, repr=False)
    _model: str | None = field(default=None, repr=False)
    #: Needed to price the call - the rate table is keyed on provider and model.
    _provider: str | None = field(default=None, repr=False)

    def record(
        self,
        *,
        total_tokens: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        estimated: bool = False,
        channel_id: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        """Report real usage. Safe to call once; later calls overwrite.

        ``estimated=True`` when the provider returned no usage block. That flag is
        carried into the ledger so an estimate is never silently
        indistinguishable from a measurement - without it, reconciling against the
        provider's own invoice is impossible.
        """
        self._recorded = True
        self._tokens = max(0, int(total_tokens or 0))
        self._prompt_tokens = max(0, int(prompt_tokens or 0))
        self._completion_tokens = max(0, int(completion_tokens or 0))
        self._estimated = bool(estimated)
        self._channel_id = channel_id
        self._model = model
        self._provider = provider


async def check_can_spend(user_id: str, feature: str, *, has_own_key: bool = False) -> SpendDecision:
    """Answer "may this user run this feature?" without reserving anything.

    For the pre-flight hint in the UI ("this will use about 3 credits"), which must
    not consume a hold just to render a number.
    """
    if not settings.ai_credits_enabled:
        return SpendDecision(allowed=True, reason="ok")
    if has_own_key:
        return SpendDecision(allowed=True, reason="own_key", billing_bypassed=True)

    from app.database import db

    account = await db.get_or_create_credit_account(user_id)
    # Grant or renew the free allowance before judging the balance. Without this a
    # brand-new user's first action is refused for lack of credits they are entitled
    # to, and a returning user is refused on the 1st of the month.
    from app.ai_allowance import ensure_allowance

    account = await ensure_allowance(user_id, account=account) or account
    if account.get("ai_disabled"):
        return SpendDecision(allowed=False, reason="disabled_globally")
    if account.get("state") != "ok":
        return SpendDecision(allowed=False, reason="blocked")

    needed = await estimate_credits(db, feature)
    available = int(account.get("available_credits") or 0)

    cap = resolve_velocity_cap(account, global_default=settings.ai_velocity_cap_per_hour)
    if velocity_exceeded(account, cap=cap, additional=needed):
        return SpendDecision(
            allowed=False, reason="velocity", estimated_credits=needed, available_credits=available
        )
    if available < needed:
        return SpendDecision(
            allowed=False,
            reason="insufficient",
            estimated_credits=needed,
            available_credits=available,
        )
    return SpendDecision(
        allowed=True, reason="ok", estimated_credits=needed, available_credits=available
    )


@asynccontextmanager
async def ai_spend(
    user_id: str,
    *,
    feature: str,
    has_own_key: bool = False,
    idempotency_key: str | None = None,
):
    """Reserve, then settle or release. See the module docstring.

    The ordering is the point: the balance is checked and held BEFORE the caller
    does any work, and the release happens in a ``finally`` so no exception path can
    skip it.
    """
    handle = SpendHandle(feature=feature, user_id=user_id)

    # Flag off, or the user is on their own key: meter, never charge.
    if not settings.ai_credits_enabled or has_own_key:
        handle.billing_bypassed = True
        try:
            yield handle
        finally:
            await _record_unbilled(handle, outcome="ok" if handle._recorded else "failed")
        return

    from app.database import db

    decision = await check_can_spend(user_id, feature, has_own_key=False)
    if not decision.allowed:
        if decision.reason == "velocity":
            raise ApiError(
                429,
                "rate_limited",
                "You're using AI very quickly. Please wait a few minutes and try again.",
            )
        if decision.reason in ("blocked", "disabled_globally"):
            raise ApiError(
                403,
                "ai_disabled",
                "AI features are turned off for this account. Please contact support.",
            )
        raise InsufficientCredits(
            needed=decision.estimated_credits,
            available=decision.available_credits,
            feature=feature,
        )

    status, reservation = await db.reserve_credits(
        user_id,
        feature=feature,
        credits=decision.estimated_credits,
        idempotency_key=idempotency_key or f"{feature}:{user_id}:{uuid.uuid4()}",
        ttl_seconds=settings.ai_reservation_ttl_seconds,
    )
    if status in ("insufficient", "no_account"):
        # Lost a race against a concurrent request between the check and the hold.
        # The atomic reserve is exactly what makes this a clean refusal rather than
        # an overdraft.
        raise InsufficientCredits(
            needed=decision.estimated_credits,
            available=decision.available_credits,
            feature=feature,
        )
    if status == "blocked":
        raise ApiError(403, "ai_disabled", "AI features are turned off for this account.")

    handle.reservation_id = reservation["id"] if reservation else None
    settled = False
    try:
        yield handle
        if handle.reservation_id and handle._recorded:
            await db.settle_reservation(
                handle.reservation_id,
                actual_credits=credits_for_tokens(handle._tokens),
                ledger=_ledger_fields(handle, outcome="ok"),
            )
            settled = True
    finally:
        # Any path that did not settle gives the hold back. Billing for our own
        # failure is the fastest way to lose a user's trust.
        if handle.reservation_id and not settled:
            await db.release_reservation(handle.reservation_id)
            await _record_unbilled(handle, outcome="failed")


def _ledger_fields(handle: SpendHandle, *, outcome: str) -> dict:
    # Operator cost is computed here, at the one place every ledger row is built, so
    # no path can write usage without also recording what it cost us. An unknown model
    # records zero and is counted as unpriced rather than guessed - see app/ai_rates.
    from app.ai_rates import cost_micros

    cost, _known = cost_micros(
        handle._provider,
        handle._model,
        prompt_tokens=handle._prompt_tokens,
        completion_tokens=handle._completion_tokens,
        total_tokens=handle._tokens,
    )
    return {
        "channel_id": handle._channel_id,
        "model": handle._model,
        "prompt_tokens": handle._prompt_tokens,
        "completion_tokens": handle._completion_tokens,
        "total_tokens": handle._tokens,
        "tokens_estimated": handle._estimated,
        "provider_cost_micros": cost,
        "outcome": outcome,
    }


async def _record_unbilled(handle: SpendHandle, *, outcome: str) -> None:
    """Write a zero-charge ledger row. Never raises.

    Two callers: a user on their own key (metered for observability) and a failed
    call (so a zero charge is provable rather than merely absent). Ledger writing
    must never be able to fail a request, hence the blanket catch.
    """
    from app.database import db

    try:
        await db.record_usage_only(
            handle.user_id,
            feature=handle.feature,
            credits_charged=0,
            **_ledger_fields(handle, outcome=outcome),
        )
    except Exception:
        logger.warning("Could not write usage ledger row for %s", handle.feature)
