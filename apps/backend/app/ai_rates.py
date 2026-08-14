"""What a call COST THE OPERATOR, as opposed to what the user was charged.

Credits are the user's unit and are deliberately stable. This module is the other
half: real provider money, so margin is a measured number rather than a hope.

THE UNIT is micros - millionths of one currency unit - stored as integers. Provider
prices are quoted per million tokens at four or five decimal places, and floats
accumulate error over millions of rows; a cent is too coarse to represent a single
small call at all. Rates are expressed per 1K tokens because that is how providers
publish them, which keeps a human able to check an entry against a pricing page.

AN UNKNOWN MODEL COSTS ZERO AND SAYS SO. It does not guess. A guessed rate silently
corrupts every margin figure derived from it, and the error is invisible because the
number still looks plausible. Instead ``resolve_rate`` reports ``known=False``, the
ledger records zero, and the spend dashboard shows the count of unpriced calls - so an
incomplete picture is visibly incomplete.

THE DEFAULTS BELOW ARE STARTING ESTIMATES, NOT FACTS. Provider prices change, vary by
region and tier, and are negotiated. The operator is expected to correct them for
their own account; `AI_RATE_OVERRIDES` exists for exactly that and takes precedence.
Nothing in the billing path depends on these being right - they affect the operator's
own margin reporting, never what a user is charged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["Rate", "cost_micros", "resolve_rate"]


@dataclass(frozen=True)
class Rate:
    """Cost per 1000 tokens, in micros."""

    prompt_micros_per_1k: int
    completion_micros_per_1k: int
    known: bool = True


#: Matched by SUBSTRING against the model name, longest pattern first, because model
#: ids carry dated suffixes ("gpt-5-nano-2025-08-07") that would defeat exact keys and
#: silently make every dated model unpriced.
_DEFAULT_RATES: dict[str, Rate] = {
    # OpenAI
    "gpt-5-nano": Rate(50, 400),
    "gpt-4o-mini": Rate(150, 600),
    "gpt-4o": Rate(2500, 10000),
    # Anthropic
    "claude-haiku": Rate(800, 4000),
    "claude-sonnet": Rate(3000, 15000),
    # Google
    "gemini-3-flash": Rate(75, 300),
    "gemini-2.0-flash": Rate(75, 300),
    "gemini-1.5-flash": Rate(75, 300),
    # DeepSeek
    "deepseek": Rate(270, 1100),
    # Local / self-hosted: the operator's own hardware, no per-token charge.
    "ollama": Rate(0, 0),
}


def _overrides() -> dict[str, Rate]:
    """Operator corrections from ``AI_RATE_OVERRIDES``.

    Shape: ``{"model-substring": [prompt_per_1k, completion_per_1k]}``. A malformed
    value is ignored with a warning rather than raising - a typo in an operator's
    reporting config must not take generation down.
    """
    raw = getattr(settings, "ai_rate_overrides", "") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        out: dict[str, Rate] = {}
        for pattern, pair in parsed.items():
            out[str(pattern).lower()] = Rate(int(pair[0]), int(pair[1]))
        return out
    except Exception:
        logger.warning("AI_RATE_OVERRIDES is not valid JSON; using default rates")
        return {}


def resolve_rate(provider: str | None, model: str | None) -> Rate:
    """The rate for a model, or an explicitly UNKNOWN rate.

    Overrides win over defaults; within each, the longest matching pattern wins so a
    specific entry beats a general one.
    """
    haystack = f"{provider or ''}/{model or ''}".lower()

    for table in (_overrides(), _DEFAULT_RATES):
        matches = [p for p in table if p in haystack]
        if matches:
            return table[max(matches, key=len)]

    return Rate(0, 0, known=False)


def cost_micros(
    provider: str | None,
    model: str | None,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> tuple[int, bool]:
    """Return ``(cost_in_micros, rate_was_known)``.

    When a provider reports only a total (many do), it is priced at the COMPLETION
    rate. That is the deliberate choice: completion tokens cost several times more, so
    pricing an unknown split at the cheaper rate would understate cost and overstate
    margin - and a margin report that flatters itself is worse than none.
    """
    rate = resolve_rate(provider, model)
    if not rate.known:
        return 0, False

    prompt = max(0, int(prompt_tokens or 0))
    completion = max(0, int(completion_tokens or 0))

    if not prompt and not completion:
        completion = max(0, int(total_tokens or 0))

    micros = (prompt * rate.prompt_micros_per_1k + completion * rate.completion_micros_per_1k) // 1000
    return micros, True
