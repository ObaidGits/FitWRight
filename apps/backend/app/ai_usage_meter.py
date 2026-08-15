"""Request-scoped token accounting: what did THIS request actually cost?

The credit system needs a number that no endpoint author has to remember to
produce. Asking each of the seventeen AI endpoints to hand back its own token count
would fail in three predictable ways:

* An endpoint that makes SEVERAL provider calls (improve, tailor, and the wizard
  all do) would report the last one and silently under-bill the rest.
* A streamed endpoint would report nothing, because its provider call finishes
  inside an async generator long after the handler returned.
* A new endpoint would report nothing at all, and look exactly like a working one.

So metering happens where the calls actually happen - at the choke point inside
``llm.py`` that every completion already passes through - and accumulates here.

WHY A MUTABLE ACCUMULATOR IN THE CONTEXTVAR, and not an int:

A ``ContextVar`` holding an ``int`` is copied into every child task. A streaming
generator, an ``asyncio.gather`` fan-out, or a ``run_in_threadpool`` hop would each
increment their own private copy and the request would see zero. Storing one
mutable object and mutating it IN PLACE means every context that inherited the
reference reports into the same tally, whichever task it runs on.

This module is deliberately NOT the anonymous aggregate metrics in
``app/admin/ai_metrics.py``. That system promises raw provider input "is never
retained or exposed" and treats a prompt/completion split as a rejected field; this
one is a per-user billing record with the opposite privacy contract. They are
recorded side by side at the same choke points and must never be merged.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["UsageAccumulator", "current_usage", "note_call", "start_metering", "stop_metering"]


@dataclass
class UsageAccumulator:
    """Running total for one request. Mutated in place - see the module docstring.

    It also carries the request's AI IDENTITY (which feature, which user, and whether
    that user is self-funded). Those live here rather than in a second ContextVar
    because they have exactly the same lifetime and the same cross-task problem: the
    provider call that needs to know "which feature is this?" in order to pick a
    channel happens deep inside llm.py, several frames below the endpoint that knew.
    """

    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    #: Calls whose usage block was missing, so the tokens are our estimate. Carried
    #: to the ledger: an estimate must never be indistinguishable from a
    #: measurement, or reconciling against the provider invoice is impossible.
    estimated_calls: int = 0
    #: Summed provider wall-clock time for this request, in ms.
    latency_ms: int = 0
    provider: str | None = None
    model: str | None = None
    channel_id: str | None = None
    #: The billing identity, and what selects an operator channel.
    feature: str | None = None
    user_id: str | None = None
    #: True when the user supplied their own credential. Routing must then leave
    #: them on it: sending a self-funded user through an operator channel would
    #: spend the operator's money for someone who was not going to cost anything.
    has_own_key: bool = False

    @property
    def estimated(self) -> bool:
        """True when ANY call in this request was estimated.

        Deliberately pessimistic: a request that is half measured and half guessed
        is not a measurement, and labelling it as one would quietly corrupt the
        reconciliation it exists to support.
        """
        return self.estimated_calls > 0


_current: ContextVar[UsageAccumulator | None] = ContextVar("fw_ai_usage", default=None)


def start_metering(
    *,
    feature: str | None = None,
    user_id: str | None = None,
    has_own_key: bool = False,
) -> tuple[UsageAccumulator, Token]:
    """Begin accounting for this request. Returns the tally and its reset token."""
    acc = UsageAccumulator(feature=feature, user_id=user_id, has_own_key=has_own_key)
    return acc, _current.set(acc)


def stop_metering(token: Token) -> None:
    """End accounting. The accumulator the caller already holds stays readable."""
    try:
        _current.reset(token)
    except ValueError:
        # Reset from a different context than the set (possible if teardown hops
        # tasks). The accumulator itself is unaffected, so this is not worth
        # failing a request over.
        logger.debug("Usage meter reset out of context; ignoring")


def current_usage() -> UsageAccumulator | None:
    """The tally for the request in flight, or None when nothing is metering."""
    return _current.get()


def note_call(
    *,
    total_tokens: int = 0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    estimated: bool = False,
    provider: str | None = None,
    model: str | None = None,
    channel_id: str | None = None,
    latency_ms: float = 0,
) -> None:
    """Report one provider call into the request's tally.

    A no-op when nothing is metering, which is the common case: health probes,
    background jobs and unmetered paths all call the LLM too, and none of them
    should pay the cost of accounting.

    Never raises. It is called from ``finally`` blocks around live provider calls,
    where an accounting bug must not be able to turn a successful generation into a
    failed request.
    """
    acc = _current.get()
    if acc is None:
        return
    try:
        acc.calls += 1
        acc.total_tokens += max(0, int(total_tokens or 0))
        acc.prompt_tokens += max(0, int(prompt_tokens or 0))
        acc.completion_tokens += max(0, int(completion_tokens or 0))
        if estimated:
            acc.estimated_calls += 1
        acc.latency_ms += max(0, int(latency_ms or 0))
        # Last writer wins for provenance. A request that failed over mid-flight
        # should name the channel that actually served it.
        if provider:
            acc.provider = provider
        if model:
            acc.model = model
        if channel_id:
            acc.channel_id = channel_id
    except Exception:  # pragma: no cover - defensive
        logger.warning("Could not record AI usage for the current request")
