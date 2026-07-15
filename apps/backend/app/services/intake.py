"""Shared hardening for public "intake" endpoints (contact + reviews).

Both the contact form and the review submission are unauthenticated, so they
share the same production defenses — per-IP rate limiting, honeypot + submit-
timing spam heuristics, de-duplication, and durable KVStore persistence. This
module centralizes that logic so the routers stay thin and consistent (no
duplication), and a future intake surface (e.g. feature-request board) can reuse
it verbatim.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass

from app.auth import get_kvstore
from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

__all__ = [
    "IntakeLimits",
    "enforce_intake_rate_limit",
    "looks_like_bot",
    "hash_ip",
    "check_and_reserve_dedup",
    "persist_record",
]

# Two-tier per-IP limits shared by all intake endpoints: a tight burst window
# blunts double-submits/scripts; a sustained hourly cap blunts slow-drip abuse.
_BURST_RULE = RateLimitRule(limit=3, window_seconds=60)
_HOURLY_RULE = RateLimitRule(limit=10, window_seconds=3600)

# A genuine human takes a couple of seconds to fill a form; faster ⇒ bot.
_MIN_ELAPSED_MS = 1200

_DEDUP_TTL_SECONDS = 300
_RECORD_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


@dataclass(frozen=True, slots=True)
class IntakeLimits:
    """Tunable per-endpoint knobs (defaults match the shared policy)."""

    burst: RateLimitRule = _BURST_RULE
    hourly: RateLimitRule = _HOURLY_RULE
    min_elapsed_ms: int = _MIN_ELAPSED_MS


async def enforce_intake_rate_limit(
    route_class: str, ip: str, limits: IntakeLimits | None = None
) -> None:
    """Raise 429 (+ Retry-After) if ``ip`` exceeds the burst or hourly window.

    Fail-open on a KVStore blip (never block a genuine user because the store
    hiccuped); genuine over-limit traffic still gets a 429.
    """
    limits = limits or IntakeLimits()
    limiter = get_rate_limiter()
    for rule in (limits.burst, limits.hourly):
        rl = await limiter.check(route_class, ip, rule, fail_closed=False)
        if not rl.allowed:
            raise ApiError(
                429,
                "rate_limited",
                "You've sent a few messages already. Please wait a little while "
                "before sending another.",
                headers={"Retry-After": str(max(1, rl.retry_after))},
            )


def looks_like_bot(
    honeypot: str, elapsed_ms: int | None, limits: IntakeLimits | None = None
) -> bool:
    """Honeypot + submit-timing heuristics (no user-facing friction)."""
    limits = limits or IntakeLimits()
    if honeypot.strip():
        return True
    if elapsed_ms is not None and elapsed_ms < limits.min_elapsed_ms:
        return True
    return False


def hash_ip(ip: str) -> str:
    """Salted, non-reversible IP hash for stored records (never store raw IP)."""
    return hashlib.sha256(f"{settings.ip_hash_secret}:{ip}".encode()).hexdigest()


async def check_and_reserve_dedup(kind: str, fingerprint: str, reference: str) -> str | None:
    """Return an existing reference for a duplicate submission, else reserve this one.

    Best-effort over the KVStore: a store error returns ``None`` (proceed) rather
    than blocking a real submission.
    """
    kv = get_kvstore()
    key = f"{kind}:dedup:{fingerprint}"
    try:
        existing = await kv.get(key)
        if existing:
            return existing
        await kv.set(key, reference, ttl_seconds=_DEDUP_TTL_SECONDS)
    except Exception:
        logger.warning("%s de-dup store unavailable; continuing", kind, exc_info=True)
    return None


async def persist_record(kind: str, reference: str, record: dict) -> None:
    """Durably store an intake record (KVStore is DB-backed in hosted mode).

    Persisted BEFORE delivery so a mail-provider outage never loses the message.
    Best-effort: a store failure is logged, not surfaced.
    """
    kv = get_kvstore()
    record.setdefault("received_at", time.time())
    try:
        await kv.set(f"{kind}:msg:{reference}", json.dumps(record), ttl_seconds=_RECORD_TTL_SECONDS)
    except Exception:
        logger.warning("Could not persist %s record (ref=%s); continuing", kind, reference, exc_info=True)
