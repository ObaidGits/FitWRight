"""Per-user rate limiting for expensive LLM-backed endpoints (cost/abuse guard).

The auth surface is rate-limited, and stream-start / JD-from-URL have their own
caps, but the other non-streaming generation endpoints (resume parse on upload,
resume tailoring/improve, cover letter, outreach, interview prep, enrichment,
resume wizard) previously had no per-user throttle — a single authenticated user
could drive unbounded provider cost, amplified by the LiteLLM router's retries.

This module centralizes a small fixed-window per-user limit applied via the
shared KVStore (so it holds across workers/instances). It is exposed both as a
plain helper (``enforce_llm_rate_limit``) and as a FastAPI route dependency
(``llm_rate_limit_dep``) so a route just adds
``dependencies=[Depends(llm_rate_limit_dep)]`` — non-invasive, and FastAPI
caches the resolved user id so it composes with the endpoint's own identity
dependency. ``fail_closed=False`` mirrors the stream limiter: a KVStore blip must
not hard-block generation, but genuine over-limit traffic gets a 429 +
``Retry-After``.
"""

from __future__ import annotations

from fastapi import Depends

from app.auth.principal import get_effective_user_id
from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.config import settings
from app.errors import ApiError

__all__ = ["enforce_llm_rate_limit", "llm_rate_limit_dep", "LLM_RATE_CLASS"]

LLM_RATE_CLASS = "llm"


async def enforce_llm_rate_limit(user_id: str) -> None:
    """Raise 429 if ``user_id`` exceeds the per-user LLM generation window.

    The window/limit are read from ``settings`` on each call (test-tunable, never
    binding a stale value at import). No-op when disabled (limit <= 0).
    """
    limit = int(settings.llm_rate_per_min_user)
    if limit <= 0:
        return
    rule = RateLimitRule(limit=limit, window_seconds=60)
    result = await get_rate_limiter().check(
        LLM_RATE_CLASS, f"llm:{user_id}", rule, fail_closed=False
    )
    if not result.allowed:
        retry_after = max(1, result.retry_after)
        raise ApiError(
            429,
            "rate_limited",
            "You're generating too fast. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )


async def llm_rate_limit_dep(user_id: str = Depends(get_effective_user_id)) -> None:
    """Route dependency form of :func:`enforce_llm_rate_limit` (keys on the user)."""
    await enforce_llm_rate_limit(user_id)
