"""``Depends(ai_metered("feature"))`` - the one line that makes an endpoint billed.

Why a route dependency instead of editing each handler:

Wrapping the body of all seventeen AI endpoints in ``async with ai_spend(...)``
would put the credit logic inside seventeen functions that are already the longest
in the codebase, and would leave the guarantee resting on every future author
remembering to do it. A missing wrapper produces an endpoint that WORKS PERFECTLY
and is silently free forever - the failure mode nobody notices.

As a dependency, the reserve happens before the handler is entered and the settle
in its teardown, so:

* An unaffordable request is refused before any work starts, not mid-save.
* The handler needs no knowledge of credits at all, and cannot forget to release.
* Token counts come from ``ai_usage_meter``, which every provider call reports
  into - so multi-call and streaming endpoints are correct for free.
* The architecture ratchet can see the wiring, because it is visible in the route
  declaration rather than buried in a function body.

The user's own key is checked here too: someone on their own credentials is metered
for observability and charged nothing.

The billing logic itself lives in :func:`metered_ai_call`, an async context manager
the dependency enters on the route's behalf. MCP tools (no FastAPI DI) enter the
same context manager directly, so REST and MCP bill through literally one function.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from fastapi import Depends

from app.ai_spend import ai_spend, check_can_spend
from app.ai_usage_meter import start_metering, stop_metering
from app.auth.principal import get_effective_user_id

logger = logging.getLogger(__name__)

__all__ = ["ai_metered", "mark_unbilled", "metered_ai_call", "user_has_own_key"]

#: Request-scoped signal from a metered handler to ``metered_ai_call``: "this
#: call performed no billable work" (e.g. it returned a previously generated
#: deliverable from storage). Set inside the handler; read in the billing
#: context's teardown, which runs in the same request task - on REST via the
#: route dependency's exit, on MCP directly around the handler call.
_unbilled_reason: ContextVar[str | None] = ContextVar(
    "ai_unbilled_reason", default=None
)


def mark_unbilled(outcome: str = "ok") -> None:
    """Declare that the running metered AI call did no billable work.

    Call this right before returning a saved deliverable as-is (the
    "Loaded your saved cover letter" path): the credit hold is released
    instead of settled, and a zero-charge ledger row makes the free pass
    provable. Without it, reuse of stored content re-charges the full
    published price for zero provider work.
    """
    _unbilled_reason.set(outcome)

#: Providers that legitimately run without a key. A user pointing FitWright at
#: their own Ollama or self-hosted endpoint is supplying their own compute, which
#: costs the operator nothing - so it must bypass billing exactly like a key does.
_SELF_HOSTED_PROVIDERS = frozenset({"ollama", "openai_compatible"})


def user_has_own_key(user_id: str) -> bool:
    """True when this user's AI calls are funded by THEM, not the operator.

    Reads the user-scoped stored credentials only. It deliberately does NOT consult
    the environment default (``LLM_API_KEY``), even though normal key resolution
    falls back to it: in a hosted deployment that variable is the OPERATOR's key,
    so honouring it here would make every user look self-funded and nothing would
    ever be charged. That mistake would be invisible in testing - where the env key
    is usually the only key - and would only surface as a provider bill.
    """
    try:
        from app.config import load_config_file

        stored = load_config_file(user_id)
        provider = str(stored.get("provider") or "")

        if stored.get("api_key"):
            return True

        api_keys = stored.get("api_keys")
        if isinstance(api_keys, dict) and provider:
            from app.llm import _PROVIDER_KEY_MAP

            if api_keys.get(_PROVIDER_KEY_MAP.get(provider, provider)):
                return True

        # Their own machine, their own cost.
        if provider in _SELF_HOSTED_PROVIDERS and stored.get("api_base"):
            return True
    except Exception:
        # Fail CLOSED (treat as operator-funded, so the request is metered and
        # charged). Failing open would give a free pass on any config read error.
        logger.warning("Could not determine own-key status; treating as operator-funded")
    return False


@asynccontextmanager
async def metered_ai_call(
    user_id: str, feature: str, *, blocking: bool = True
) -> AsyncIterator[None]:
    """The billing context behind ``Depends(ai_metered(...))`` - shared with MCP tools.

    REST routes get this via the route dependency; MCP tools (which have no
    FastAPI dependency injection) enter it directly around the handler call.
    ONE function means the two paths literally cannot drift: same feature
    name, same refusal ordering, same metering.

    Entering can raise 402 / 403 / 429 BEFORE the caller's body runs - that is
    the point: the refusal arrives before any partial work exists.
    """
    bypass_billing = user_has_own_key(user_id)

    if not bypass_billing and not blocking:
        decision = await check_can_spend(user_id, feature)
        # Only a SHORT BALANCE degrades. `blocked` and `disabled_globally` fall
        # through to ai_spend, which refuses them - an operator who turned a
        # user off must not be overridden by an endpoint's leniency.
        if not decision.allowed and decision.reason == "insufficient":
            bypass_billing = True

    async with ai_spend(user_id, feature=feature, has_own_key=bypass_billing) as spend:
        # Publishing the feature here is what lets llm.py pick an operator
        # channel: the provider call is several frames below this point and has
        # no other way to know which feature it is serving.
        usage, token = start_metering(
            feature=feature, user_id=user_id, has_own_key=bypass_billing
        )
        try:
            yield
        finally:
            stop_metering(token)
            # A handler that served stored content marks itself unbilled; the
            # hold is then released instead of settled (zero-charge row).
            unbilled = _unbilled_reason.get()
            if unbilled is not None:
                spend.mark_free(unbilled)
            # Recorded even on the failure path, where it settles nothing and
            # the release writes a zero-charge row - so "we did not bill for
            # this" stays provable rather than merely absent.
            spend.record(
                total_tokens=usage.total_tokens,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                estimated=usage.estimated,
                channel_id=usage.channel_id,
                model=usage.model,
                provider=usage.provider,
                latency_ms=usage.latency_ms,
            )


def ai_metered(feature: str, *, blocking: bool = True):
    """Build the route dependency that charges ``feature`` to the calling user.

    Usage::

        @router.post("/improve", dependencies=[Depends(ai_metered("resume_improve"))])

    The ``feature`` string is the billing identity: it selects the credit estimate,
    groups the user's history, and appears in the ledger. Reuse an existing name
    rather than inventing a variant, or one feature's spend splits across two rows.

    ``blocking=False`` is for endpoints that FINISH something the user has already
    paid for. ``/improve/confirm`` is the case: the tailoring was charged at preview,
    and confirm is the step that saves it - refusing there for a short balance would
    delete work the user already bought, which is a far worse outcome than an
    uncharged call. Such a request is metered and recorded at zero charge instead of
    being refused, so the operator still sees the cost.

    ``blocking=False`` never bypasses an ADMINISTRATIVE block: a user the operator
    disabled, or one who is over the velocity cap for abuse, is still refused. Only
    a short balance degrades.
    """

    async def dependency(user_id: str = Depends(get_effective_user_id)) -> AsyncIterator[None]:
        async with metered_ai_call(user_id, feature, blocking=blocking):
            yield

    # Lets the architecture ratchet DETECT that a route is metered instead of
    # trusting a hand-maintained list. A list drifts silently the moment someone
    # renames a path; a marker read off the live route cannot.
    dependency.__fw_metered_feature__ = feature  # type: ignore[attr-defined]
    dependency.__fw_metered_blocking__ = blocking  # type: ignore[attr-defined]
    return dependency
