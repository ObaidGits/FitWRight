"""Turn configured channels into a ready-to-use LiteLLM router for one feature.

This is the seam between the database (which channels exist, and how healthy they
are) and the transport (LiteLLM). It exists as its own module so the routing policy
in ``ai_channels`` stays pure and testable, and ``llm.py`` stays about talking to
providers.

The flag is checked here, once. With ``AI_CREDITS_ENABLED`` off this returns
``None`` and every caller falls straight back to the existing single-deployment,
per-user-key path - which is why the whole feature can ship dark.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai_channels import ChannelCandidate, select_channels
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "ChannelRoute",
    "channels_are_configured",
    "record_channel_outcome",
    "resolve_channel_route",
]

class ChannelRoute:
    """An ordered set of channel deployments, plus the ids they came from.

    The ids matter: after the call we must credit or blame the RIGHT channel, and
    LiteLLM does not tell us which deployment ultimately served the request. We
    record the outcome against the channel we asked for first, which is correct for
    health purposes - a channel that needed a fallback did fail.
    """

    def __init__(self, candidates: list[ChannelCandidate], deployments: list[dict[str, Any]]):
        self.candidates = candidates
        self.deployments = deployments

    @property
    def primary_channel_id(self) -> str | None:
        return self.candidates[0].id if self.candidates else None

    @property
    def primary_model(self) -> str | None:
        return self.candidates[0].model if self.candidates else None

    def __bool__(self) -> bool:
        return bool(self.deployments)


async def _load_channel_keys() -> dict[str, str]:
    """Decrypt every channel credential, keyed by CHANNEL ID.

    Entries that fail to decrypt are omitted rather than raising: one unreadable
    credential (after an encryption-secret change, say) must not take down every other
    channel. This mirrors how user keys already behave.
    """
    from app.crypto import decrypt
    from app.database import db

    out: dict[str, str] = {}
    try:
        ciphertexts = await db.get_ai_channel_keys()
    except Exception:
        logger.warning("Could not read channel credentials")
        return out
    for channel_id, ciphertext in (ciphertexts or {}).items():
        try:
            plaintext = decrypt(ciphertext)
        except Exception:
            # Report absence, never a broken value.
            logger.warning("Channel credential could not be decrypted; skipping")
            continue
        # `decrypt` reports failure by RETURNING EMPTY rather than raising, so the
        # exception handler above is not enough on its own. An empty string here would
        # be a credential that exists and cannot work - the confusing middle state this
        # function is meant to eliminate.
        if plaintext:
            out[channel_id] = plaintext
        else:
            logger.warning("Channel credential decrypted to empty; treating as absent")
    return out


async def resolve_channel_route(feature: str, *, pinned_channel_id: str | None = None):
    """Return a :class:`ChannelRoute` for ``feature``, or ``None``.

    ``None`` means "do not use channels" and has three distinct causes, all of which
    correctly fall back to the existing per-user-key path:

      * the feature flag is off,
      * no channels are configured,
      * every configured channel is disabled, draining, cooling, or barred from this
        feature by structured-output gating.

    The third case is NOT the same as "AI is unavailable" - the caller may still have
    their own key. Distinguishing them is what stops an out-of-channels state from
    rendering as a bogus "you are offline", which this codebase has shipped before.
    """
    if not settings.ai_credits_enabled:
        return None

    from app.database import db

    try:
        channels = await db.list_ai_channels()
        health = await db.get_ai_channel_health()
        # Month-to-date cost per channel, so a channel that has spent its configured
        # cap is skipped. Fetched here rather than inside the policy to keep
        # ai_channels pure and testable.
        spend = await db.channel_spend_micros_this_month()
    except Exception:
        logger.warning("Channel lookup failed; falling back to the per-user key path")
        return None

    if not channels:
        return None

    candidates = select_channels(
        channels,
        health,
        feature=feature,
        pinned_channel_id=pinned_channel_id,
        spend_by_channel=spend,
    )
    if not candidates:
        return None

    keys = await _load_channel_keys()
    deployments: list[dict[str, Any]] = []
    usable: list[ChannelCandidate] = []
    for cand in candidates:
        key = keys.get(cand.id)
        # A channel with no readable credential is skipped rather than added and
        # left to fail every request it receives. The admin API refuses to activate
        # one, but a key can also become unreadable after an encryption-secret
        # change - exactly the failure that already bit this app once.
        if not key and cand.provider not in ("ollama", "openai_compatible"):
            logger.warning("Channel %s has no usable credential; skipping", cand.name)
            continue
        deployments.append(
            {
                "model": f"{cand.provider}/{cand.model}"
                if "/" not in cand.model
                else cand.model,
                "api_key": key,
                "api_base": cand.api_base,
            }
        )
        usable.append(cand)

    if not deployments:
        return None
    return ChannelRoute(usable, deployments)


async def channels_are_configured() -> bool:
    """Whether the operator has set up ANY channel at all.

    The one thing ``resolve_channel_route`` returning ``None`` cannot tell you, and
    the difference between two messages a user must never see confused:

      * no channels configured -> this deployment does not offer hosted AI; use your
        own key. Nothing is broken.
      * channels configured but every one is down -> OUR outage. The user did nothing
        wrong and should be told to try again shortly, not sent to go and configure
        something.

    Collapsing those is how an outage turns into a support queue full of users
    re-entering API keys that were never the problem.
    """
    if not settings.ai_credits_enabled:
        return False
    from app.database import db

    try:
        return bool(await db.list_ai_channels())
    except Exception:
        logger.warning("Could not determine whether channels are configured")
        return False


async def record_channel_outcome(
    channel_id: str | None, *, ok: bool, error_class: str | None = None
) -> None:
    """Update a channel's health after a call. Never raises.

    Health recording must not be able to fail a request that already succeeded, so
    every error here is swallowed and logged.
    """
    if not channel_id:
        return
    from app.database import db

    try:
        await db.record_ai_channel_result(
            channel_id,
            ok=ok,
            error_class=error_class,
            cooldown_seconds=settings.ai_channel_cooldown_seconds,
            failure_threshold=settings.ai_channel_failure_threshold,
        )
    except Exception:
        logger.warning("Could not record channel health for %s", channel_id)
