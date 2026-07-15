"""Outbox → profile-analytics consumers (event-driven, decoupled).

Maps profile domain events to per-user analytics counters via
:class:`AnalyticsService`. This is the *only* place event→metric logic lives, so
business services stay free of analytics concerns (they just emit events). Idempotency
is not required for counters (at-least-once slightly over-counts at most, which
is acceptable for usage analytics and never corrupts state).
"""

from __future__ import annotations

import logging

from app.events import EventType, OutboxEvent, register_handler
from app.profile.analytics import get_analytics_service

logger = logging.getLogger(__name__)

__all__ = ["ensure_analytics_registered"]

_registered = False

# event type -> metric key.
_EVENT_METRIC: dict[str, str] = {
    EventType.PROFILE_UPDATED.value: "profile_updates",
    EventType.PROFILE_RESUME_GENERATED.value: "resumes_generated",
    EventType.PROFILE_IMPORTED.value: "imports",
    EventType.MERGE_COMPLETED.value: "merges",
    EventType.RESUME_SYNCED.value: "syncs",
    EventType.PROFILE_AI_USED.value: "ai_suggestions",
    EventType.PROFILE_EXPORTED.value: "exports",
    EventType.PROFILE_SEARCHED.value: "searches",
    EventType.PUBLIC_VIEWED.value: "public_views",
    EventType.PORTFOLIO_VIEWED.value: "portfolio_views",
    EventType.PUBLIC_SHARED.value: "shares",
}


def _make_handler(metric: str):
    async def _handler(event: OutboxEvent) -> None:
        await get_analytics_service().record(event.user_id, metric)
        # Opportunistically refresh the completeness gauge when present.
        completeness = event.payload.get("completeness")
        if isinstance(completeness, int):
            await get_analytics_service().set_gauge(event.user_id, "completeness", completeness)

    return _handler


def ensure_analytics_registered() -> None:
    """Register the analytics handlers once (idempotent, import-safe)."""
    global _registered
    if _registered:
        return
    for event_type, metric in _EVENT_METRIC.items():
        register_handler(event_type, _make_handler(metric))
    _registered = True
