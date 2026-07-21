"""Profile Analytics - privacy-respecting, event-driven usage counters.

Analytics is a **consumer**, not a concern woven through business logic: domain
services emit typed events to the outbox; the analytics consumer
(``analytics_consumer.py``) translates them into per-user counters stored in the
shared :class:`KVStore` (works across workers; no schema change). This module
owns only the counter store + snapshot read; it never imports business services.

Counters are per-user and non-PII (event tallies + a completeness gauge). No raw
content, IPs, or third-party identifiers are stored.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AnalyticsService", "get_analytics_service", "METRICS"]

# The stable metric keys surfaced by the analytics endpoint + dashboard.
METRICS: tuple[str, ...] = (
    "profile_updates",
    "resumes_generated",
    "imports",
    "merges",
    "syncs",
    "ai_suggestions",
    "exports",
    "searches",
    "public_views",
    "portfolio_views",
    "shares",
)

_PREFIX = "analytics:profile"


class AnalyticsService:
    """KVStore-backed per-user counters (atomic incr, cheap snapshot read)."""

    def __init__(self, kvstore=None) -> None:
        self._kv = kvstore

    def _store(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    @staticmethod
    def _key(user_id: str, metric: str) -> str:
        return f"{_PREFIX}:{user_id}:{metric}"

    async def record(self, user_id: str | None, metric: str, *, amount: int = 1) -> None:
        """Increment ``metric`` for ``user_id`` (best-effort; never raises)."""
        if not user_id or metric not in METRICS:
            return
        try:
            await self._store().incr(self._key(user_id, metric), amount=amount)
        except Exception:  # pragma: no cover - analytics must never break a flow
            logger.debug("analytics record failed: %s/%s", user_id, metric, exc_info=True)

    async def set_gauge(self, user_id: str | None, name: str, value: int) -> None:
        """Store a gauge value (e.g. current completeness) for ``user_id``."""
        if not user_id:
            return
        try:
            await self._store().set(self._key(user_id, f"gauge:{name}"), str(int(value)))
        except Exception:  # pragma: no cover
            logger.debug("analytics gauge failed: %s/%s", user_id, name, exc_info=True)

    async def snapshot(self, user_id: str) -> dict[str, Any]:
        """Return all counters (+ the completeness gauge) for ``user_id``."""
        store = self._store()
        counters: dict[str, int] = {}
        for metric in METRICS:
            try:
                raw = await store.get(self._key(user_id, metric))
            except Exception:  # pragma: no cover
                raw = None
            counters[metric] = int(raw) if raw and raw.isdigit() else 0
        try:
            g = await store.get(self._key(user_id, "gauge:completeness"))
        except Exception:  # pragma: no cover
            g = None
        return {
            "counters": counters,
            "completeness": int(g) if g and g.isdigit() else 0,
            "total_events": sum(counters.values()),
        }


_service: AnalyticsService | None = None


def get_analytics_service() -> AnalyticsService:
    """Process-wide analytics service (built on first use)."""
    global _service
    if _service is None:
        _service = AnalyticsService()
    return _service


def reset_analytics_service() -> None:
    """Drop the cached service (tests)."""
    global _service
    _service = None
