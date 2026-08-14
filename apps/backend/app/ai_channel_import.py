"""Adopt the operator's existing ``LLM_API_KEY`` as a real channel (task 1.10).

Before this, a hosted deployment had its credential in two conceptual places: the
env var that the single-provider path reads, and the channels table that the routing
path reads. That ambiguity is not academic - it decides which key pays for a request,
and the two can disagree after any config change. An operator debugging "which key
served this?" had no way to answer it.

Adoption is IDEMPOTENT and CONSERVATIVE:

* It runs only when channels are enabled and NO channel exists yet. Once the operator
  manages channels, this code must never touch their configuration again - silently
  re-adding a channel someone deleted would be indistinguishable from a bug.
* The adopted channel starts DISABLED. Creating it already active would move live
  traffic onto a new code path during a deploy, without anybody choosing that. The
  operator activates it when they are ready, which is also when they can test it.
* The env var is left in place, untouched, and remains the fallback. Removing it would
  make adoption a one-way door; leaving it means the worst case of a bad adoption is
  the behaviour they already had.
"""

from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["ADOPTED_CHANNEL_NAME", "adopt_env_key_as_channel"]

ADOPTED_CHANNEL_NAME = "Imported from LLM_API_KEY"


async def adopt_env_key_as_channel() -> dict | None:
    """Create a disabled channel mirroring the env credential, once.

    Returns the created channel, or ``None`` when there was nothing to do. Never
    raises: this runs at startup, and failing to import a convenience channel must not
    stop the application from serving.
    """
    if not settings.ai_credits_enabled:
        return None

    api_key = (settings.llm_api_key or "").strip()
    provider = (settings.llm_provider or "").strip()
    model = (settings.llm_model or "").strip()

    # Self-hosted providers need a base URL rather than a key, and there is no useful
    # credential to import for them.
    if not provider or not model:
        return None
    if not api_key and provider not in ("ollama", "openai_compatible"):
        return None

    try:
        from app.database import db

        existing = await db.list_ai_channels()
        if existing:
            # The operator owns this configuration now.
            return None

        created = await db.create_ai_channel(
            name=ADOPTED_CHANNEL_NAME,
            provider=provider,
            model=model,
            api_base=getattr(settings, "llm_api_base", None) or None,
            priority=100,
            monthly_cost_cap_cents=None,
        )

        if api_key:
            from app.crypto import encrypt

            await db.set_ai_channel_key(created["id"], encrypt(api_key))

        logger.info(
            "Imported LLM_API_KEY as a disabled AI channel (%s/%s). Activate it in "
            "Admin > AI channels when ready.",
            provider,
            model,
        )
        return created
    except Exception:
        # Log the CAUSE, not just the fact. A fail-soft path that hides its reason
        # turns a five-second fix into an investigation - this exact swallow already
        # cost one debugging round.
        logger.warning(
            "Could not import LLM_API_KEY as a channel; leaving it as-is", exc_info=True
        )
        return None
