"""FeatureUsageService - daily per-feature aggregate totals (Req 16).

A read-only Product Analytics service that queries the shared Metric_Store for
the 8 tracked feature-usage keys and returns a zero-filled daily series per
feature over a validated window (7/30/90 days). Exposes ONLY daily aggregate
totals - no user identity, funnel, cohort, retention, or session-level data
(Req 16.6). All reads are O(1) bounded - one ``MetricStore.series()`` call per
fixed feature key (Req 16.5).

Follows the singleton pattern (``get_feature_usage_service`` /
``reset_feature_usage_service``) consistent with other Domain_Metrics_Services.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.admin.metric_registry import (
    FEAT_BUILDER,
    FEAT_COVER_LETTER,
    FEAT_IMPORT,
    FEAT_JD_PARSE,
    FEAT_PARSER,
    FEAT_PORTFOLIO,
    FEAT_PROFILE_GEN,
    FEAT_TAILOR,
)
from app.admin.schemas import FeatureSeries, FeatureUsage, SeriesPoint

__all__ = [
    "FeatureUsageService",
    "get_feature_usage_service",
    "reset_feature_usage_service",
]

# The closed, fixed set of feature-usage keys (Req 16.2). Order matches the
# registry definition; adding a feature is a one-line edit here + in the
# Metric_Registry.
_FEATURE_KEYS: tuple[str, ...] = (
    FEAT_BUILDER,
    FEAT_TAILOR,
    FEAT_PARSER,
    FEAT_IMPORT,
    FEAT_COVER_LETTER,
    FEAT_PROFILE_GEN,
    FEAT_PORTFOLIO,
    FEAT_JD_PARSE,
)

_ALLOWED_WINDOWS: frozenset[int] = frozenset((7, 30, 90))


class FeatureUsageService:
    """Read-only service: daily aggregate series per tracked feature."""

    def __init__(self, *, metric_store=None) -> None:
        self._metric_store = metric_store

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def series(self, window: int) -> FeatureUsage:
        """Return daily-total series for all features over ``window`` days.

        Validates ``window`` ∈ {7, 30, 90}; raises ``ValueError`` otherwise.
        Each feature gets a ``FeatureSeries`` with zero-filled ``SeriesPoint``
        entries and a computed total. The response carries no user-level data.
        """
        if window not in _ALLOWED_WINDOWS:
            raise ValueError(
                f"window must be one of {sorted(_ALLOWED_WINDOWS)}, got {window}"
            )

        store = self._get_metric_store()
        feature_series: list[FeatureSeries] = []

        for key in _FEATURE_KEYS:
            raw = await store.series(key, window)
            points = [SeriesPoint(date=day, value=value) for day, value in raw]
            total = sum(value for _, value in raw)
            feature_series.append(
                FeatureSeries(feature=key, points=points, total=total)
            )

        return FeatureUsage(
            window=window,
            series=feature_series,
            computedAt=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )


# ---------------------------------------------------------------------------
# Singleton management
# ---------------------------------------------------------------------------

_service: FeatureUsageService | None = None


def get_feature_usage_service() -> FeatureUsageService:
    """Return the process-wide :class:`FeatureUsageService` (built on first use)."""
    global _service
    if _service is None:
        _service = FeatureUsageService()
    return _service


def reset_feature_usage_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
