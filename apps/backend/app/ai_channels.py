"""Channel selection: which provider routes a request may use, in what order.

FitWright's LiteLLM Router previously had exactly one deployment and carried this
comment in ``_build_router``:

    Cooldowns disabled: with a single deployment and no fallback, cooldowns would
    blackout the backend on transient failures. Re-enable when a fallback
    deployment is added.

This module supplies those fallback deployments. It answers one question -
"which channels, in which order, for this feature right now?" - and deliberately
does not perform the call: LiteLLM already handles ordered fallback, retry
classification and cooldown natively, so this configures an existing capability
rather than reimplementing a load balancer.

Three rules, each guarding a specific production failure:

1. **Health.** A channel benched by cooldown is skipped, except that exactly one
   probe request is let through once its cooldown lapses. Sending full traffic at a
   provider that is still struggling just re-breaks it.

2. **Structured-output gating.** Features needing valid JSON (resume parse, resume
   tailoring) exclude channels whose structured verdict is ``unsupported``. A
   fallback that keeps the app "up" while returning unusable output is worse than an
   honest failure, because the user only discovers it after reading the result.

3. **Retryable errors only.** Failing over on an auth error or a malformed request is
   pointless - those fail identically on every provider and only multiply latency.
   Only timeout / rate-limit / 5xx / connection failures are worth another channel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ChannelCandidate",
    "FEATURES_REQUIRING_STRUCTURED_OUTPUT",
    "classify_error",
    "is_retryable",
    "select_channels",
]

#: Features whose output is parsed as JSON. A channel that cannot reliably produce
#: structured output must never serve these, however healthy it looks.
FEATURES_REQUIRING_STRUCTURED_OUTPUT = frozenset(
    {
        "resume_parse",
        "resume_tailor",
        "resume_wizard",
        "enrichment",
        "jd_extract",
    }
)

#: Verdicts acceptable for a structured feature. ``flaky`` is allowed because the
#: caller already retries invalid JSON; ``unsupported`` means it never works.
_STRUCTURED_OK = frozenset({"reliable", "flaky", "unknown"})

#: Error classes worth trying another channel for.
_RETRYABLE = frozenset({"timeout", "rate_limit", "server", "connection"})


@dataclass(frozen=True)
class ChannelCandidate:
    """One usable route, already ordered."""

    id: str
    name: str
    provider: str
    model: str
    api_base: str | None
    priority: int
    #: True when this channel is only being tried as a post-cooldown probe. The
    #: caller may choose to send a single request rather than a burst.
    probe: bool = False


def classify_error(exc: BaseException) -> str:
    """Map a provider exception to a coarse, loggable class.

    Deliberately coarse: the class is stored on the channel's health row, and a
    provider's raw message can contain fragments of the prompt.
    """
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    # Order matters: check the specific, non-retryable causes first so a message
    # mentioning "timeout" inside an auth error cannot be misfiled as retryable.
    if "auth" in name or "unauthorized" in text or "invalid api key" in text or "401" in text:
        return "auth"
    if "permission" in name or "403" in text:
        return "auth"
    if "contentpolicy" in name or "content_policy" in text:
        return "content_policy"
    if "badrequest" in name or "400" in text or "unprocessable" in text:
        return "bad_request"
    if "notfound" in name or "404" in text:
        return "bad_request"
    if "ratelimit" in name or "rate limit" in text or "429" in text:
        return "rate_limit"
    if "timeout" in name or "timed out" in text:
        return "timeout"
    if "connection" in name or "connect" in text:
        return "connection"
    if "internalserver" in name or "serviceunavailable" in name:
        return "server"
    if any(code in text for code in (" 500", " 502", " 503", " 504")):
        return "server"
    return "unknown"


def is_retryable(error_class: str) -> bool:
    """Whether ``error_class`` justifies trying a different channel.

    ``unknown`` is deliberately NOT retryable: failing over on an error we cannot
    classify risks burning every channel (and, once billing exists, multiplying the
    operator's cost) on a request that was never going to succeed.
    """
    return error_class in _RETRYABLE


def _cooling(health: dict[str, Any] | None, now: datetime) -> bool:
    """Is this channel currently benched?"""
    if not health:
        return False
    until = health.get("cooling_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > now
    except (TypeError, ValueError):
        # An unparseable timestamp must not permanently bench a channel.
        logger.warning("Unparseable cooling_until on channel health; treating as healthy")
        return False


def _lapsed_cooldown(health: dict[str, Any] | None, now: datetime) -> bool:
    """Was this channel benched, with its cooldown now expired?

    Such a channel is a *probe* candidate: worth one request to see if the provider
    recovered, but ranked below channels that are simply healthy.
    """
    if not health:
        return False
    until = health.get("cooling_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) <= now
    except (TypeError, ValueError):
        return False


def over_monthly_cap(channel: dict[str, Any], spend_micros: int) -> bool:
    """Whether this channel has spent its monthly ceiling.

    The cap is stored in CENTS (what an operator types) and spend is measured in
    MICROS (what survives millions of small calls without rounding). 1 cent =
    10_000 micros, and getting that conversion wrong by a factor of 10,000 in
    either direction is either a cap that never fires or one that fires instantly,
    so it lives in exactly one function with a test either side of the boundary.

    ``None`` or ``0`` means no cap. Zero deliberately means "unlimited" rather
    than "spend nothing": a channel capped at zero would be indistinguishable from
    a disabled one, and there is already a state for disabled.
    """
    cap_cents = channel.get("monthly_cost_cap_cents")
    if not cap_cents:
        return False
    return int(spend_micros) >= int(cap_cents) * 10_000


def select_channels(
    channels: list[dict[str, Any]],
    health: dict[str, dict[str, Any]],
    *,
    feature: str,
    now: datetime | None = None,
    pinned_channel_id: str | None = None,
    spend_by_channel: dict[str, int] | None = None,
) -> list[ChannelCandidate]:
    """Return usable channels in the order they should be attempted.

    Healthy channels come first in priority order, then any whose cooldown has
    lapsed (as probes). A channel that is disabled, draining, or still cooling is
    excluded entirely.

    ``pinned_channel_id`` forces one channel for support/debugging. A pin still
    respects structured-output gating - pinning a channel that cannot produce JSON
    into a JSON feature would produce a confusing bug report, not a useful one.

    ``spend_by_channel`` is month-to-date provider cost in micros per channel. A
    channel that has reached its configured cap is excluded here, which is the ONLY
    place the cap is enforced - the field existed and was editable in the admin UI
    for a while without being enforced anywhere, which is worse than not offering it,
    because an operator believed they were protected.
    """
    moment = now or datetime.now(timezone.utc)
    needs_structured = feature in FEATURES_REQUIRING_STRUCTURED_OUTPUT
    spend = spend_by_channel or {}

    healthy: list[ChannelCandidate] = []
    probes: list[ChannelCandidate] = []

    for ch in channels:
        if ch.get("state") != "active":
            continue
        if needs_structured and ch.get("structured_verdict") not in _STRUCTURED_OK:
            continue
        if pinned_channel_id and ch.get("id") != pinned_channel_id:
            continue
        if over_monthly_cap(ch, spend.get(ch["id"], 0)):
            continue

        h = health.get(ch["id"])
        if _cooling(h, moment):
            continue

        candidate = ChannelCandidate(
            id=ch["id"],
            name=ch["name"],
            provider=ch["provider"],
            model=ch["model"],
            api_base=ch.get("api_base"),
            priority=int(ch.get("priority") or 100),
            probe=_lapsed_cooldown(h, moment),
        )
        (probes if candidate.probe else healthy).append(candidate)

    # Ties already broke on created_at in the repository query, so a stable sort
    # here preserves that determinism.
    healthy.sort(key=lambda c: c.priority)
    probes.sort(key=lambda c: c.priority)
    return healthy + probes
