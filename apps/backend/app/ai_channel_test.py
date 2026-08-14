"""Probe one channel: does the credential work, and can the model return JSON?

Kept out of the router because it is real logic with real failure modes, and out of
``ai_routing`` because that module answers "where should traffic go?" while this one
answers "would this channel work at all?" - a question asked about a channel that is
deliberately NOT yet receiving traffic.

The probe is deliberately tiny (a two-word answer, a few tokens) because it spends the
operator's money every time it runs. It is also deliberately NOT routed through the
normal channel router: that would apply failover, and a probe that silently succeeds
via a different channel is worse than no probe - it would report a broken channel as
healthy.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["probe_channel"]

#: Small enough to cost almost nothing, structured enough to prove JSON mode.
_PROBE_PROMPT = 'Reply with only this JSON and nothing else: {"ok": true}'


def _classify(exc: BaseException) -> tuple[str, str]:
    """Map a probe failure to (error_class, operator-facing explanation).

    Generic transport errors are useless to an operator staring at a form. The three
    cases below are the ones they can actually act on, and telling them apart is the
    difference between "fix your key" and "your key is fine, the model name is wrong".
    """
    text = str(exc).lower()
    if any(w in text for w in ("api key", "unauthorized", "401", "authentication")):
        return "auth", "The provider rejected the credential. Check the API key."
    if any(w in text for w in ("model", "not found", "404", "does not exist")):
        return "model", "The provider does not recognise this model name."
    if any(w in text for w in ("timeout", "timed out")):
        return "timeout", "The provider did not respond in time. It may be degraded."
    if any(w in text for w in ("rate", "429", "quota", "insufficient_quota")):
        return "rate_limit", "The provider rate-limited or refused for quota/billing."
    return "error", "The call failed. See the message for the provider's own words."


async def probe_channel(channel: dict[str, Any]) -> dict[str, Any]:
    """Run one completion against this channel alone.

    Returns a verdict dict. Never raises: a failing probe is a RESULT, not an error -
    the operator asked a question and "no, and here is why" is the answer.
    """
    from app.ai_routing import _load_channel_keys
    from app.llm import LLMConfig, complete

    provider = str(channel.get("provider") or "")
    model = str(channel.get("model") or "")
    keys = await _load_channel_keys()
    key = keys.get(str(channel.get("id")))

    if not key and provider not in ("ollama", "openai_compatible"):
        return {
            "ok": False,
            "error_class": "auth",
            "message": "No credential is stored for this channel.",
            "structured_verdict": "unknown",
            "latency_ms": 0,
        }

    config = LLMConfig(
        provider=provider,
        model=model,
        api_key=key or "",
        api_base=channel.get("api_base") or None,
    )

    started = time.perf_counter()
    try:
        # An EXPLICIT config, which _resolve_router honours by never redirecting it.
        # That is what keeps this a test of THIS channel rather than of the fleet.
        text = await complete(
            _PROBE_PROMPT, config=config, max_tokens=64, temperature=0
        )
    except Exception as exc:
        error_class, explanation = _classify(exc)
        return {
            "ok": False,
            "error_class": error_class,
            "message": f"{explanation} ({str(exc)[:200]})",
            "structured_verdict": "unknown",
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    verdict = _judge_structured(text)
    return {
        "ok": True,
        "error_class": None,
        "message": "The channel responded.",
        "structured_verdict": verdict,
        "latency_ms": latency_ms,
        "sample": (text or "")[:120],
    }


def _judge_structured(text: str | None) -> str:
    """Did the model actually return the JSON it was asked for?

    ``reliable`` only for clean, parseable JSON. Anything wrapped in prose or fences is
    ``flaky`` rather than ``unsupported``, because it usually DOES work with a stricter
    prompt or a repair pass - and this codebase already has a JSON repair path. Calling
    it unsupported would bar the channel from features it could serve.
    """
    if not text:
        return "unsupported"
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        return "reliable" if isinstance(parsed, dict) else "flaky"
    except (ValueError, TypeError):
        pass
    # JSON present but not alone: fences, preamble, or trailing chatter.
    if "{" in stripped and "}" in stripped:
        return "flaky"
    return "unsupported"
