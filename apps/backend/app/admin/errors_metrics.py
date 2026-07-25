"""Errors summary combining durable UTC-day totals with live route classes.

Selected-window 4xx/5xx totals, AI failures, and the daily trend come only from
``metrics_daily`` date buckets. Cumulative process counters are deliberately not
merged into those durable totals. Top failing route classes are a separate,
clearly scoped current-process view read from bounded :class:`AdminMetrics`
route buckets. Job and storage failures remain explicitly uninstrumented.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.admin.metrics import get_admin_metrics
from app.admin.metric_registry import (
    AI_FAILURE,
    REQUEST_4XX,
    REQUEST_5XX,
)
from app.admin.schemas import (
    ErrorsBySource,
    ErrorsSummary,
    RouteClassFailures,
    SeriesPoint,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ErrorsMetricsService",
    "get_errors_metrics_service",
    "reset_errors_metrics_service",
]


class ErrorsMetricsService:
    """Durable selected-window errors plus current-process route failures.

    ``metric_store`` and ``admin_metrics`` are optional injected collaborators;
    omitting either preserves singleton-based backward compatibility.
    """

    def __init__(self, *, metric_store=None, admin_metrics=None) -> None:
        self._metric_store = metric_store
        self._admin_metrics = admin_metrics

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _get_admin_metrics(self):
        if self._admin_metrics is not None:
            return self._admin_metrics
        return get_admin_metrics()

    async def summary(self, window: int) -> ErrorsSummary:
        """Return durable totals/trend plus current-process route failures.

        The five store reads are bounded by the selected UTC-day window. Route
        failures come from one bounded ``AdminMetrics`` snapshot and remain
        separate from all durable totals and trend values.
        """
        store = self._get_metric_store()
        now = datetime.now(timezone.utc)
        win = max(1, int(window))
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=win - 1)).strftime("%Y-%m-%d")

        # -- grouped request-failure counts (Req 5.1), durable window only ----
        # Cumulative in-process AdminMetrics counters are intentionally NOT folded
        # in (they are not windowed - see module docstring "Live-today handling").
        counts_4xx = await store.sum([REQUEST_4XX], day_from, day_to)
        counts_5xx = await store.sum([REQUEST_5XX], day_from, day_to)

        # -- by-source failure counts (Req 5.3) -------------------------------
        # api = all request failures; ai = durable AI failures (timeouts already
        # counted in AI_FAILURE); job/storage have no durable signal -> 0.
        ai_failures = await store.sum([AI_FAILURE], day_from, day_to)
        by_source = ErrorsBySource(
            api=counts_4xx + counts_5xx,
            job=0,       # documented gap: no durable windowed job-failure key
            storage=0,   # documented gap: no durable storage-failure signal
            ai=ai_failures,
        )

        # Route-class failures are intentionally separate current-process data;
        # they never alter the durable selected-window totals above.
        route_snapshot = self._get_admin_metrics().snapshot()
        route_latency = route_snapshot.get("latency", {}) or {}
        top_route_classes = [
            RouteClassFailures(
                routeClass=str(route_class),
                failures=int((stats or {}).get("failure_count", 0) or 0),
            )
            for route_class, stats in route_latency.items()
            if int((stats or {}).get("failure_count", 0) or 0) > 0
        ]
        top_route_classes.sort(key=lambda route: (-route.failures, route.routeClass))
        top_route_classes = top_route_classes[:10]

        # -- daily error-count trend (Req 5.4) --------------------------------
        # total errors per day = REQUEST_4XX + REQUEST_5XX, oldest->newest. Both
        # series cover the same trailing window, so they align index-by-index.
        series_4xx = await store.series(REQUEST_4XX, win)
        series_5xx = await store.series(REQUEST_5XX, win)
        by_day_5xx = {day: value for day, value in series_5xx}
        trend: list[SeriesPoint] = [
            SeriesPoint(date=day, value=value + by_day_5xx.get(day, 0))
            for day, value in series_4xx
        ]

        return ErrorsSummary(
            window=int(window),
            windowStartDate=day_from,
            windowEndDate=day_to,
            granularity="utc_day",
            dataScope="durable_utc_day_buckets_plus_current_process_route_classes",
            counts4xx=counts_4xx,
            counts5xx=counts_5xx,
            topRouteClasses=top_route_classes,
            bySource=by_source,
            trend=trend,
            notInstrumented=["bySource.job", "bySource.storage"],
            computedAt=now.isoformat(),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.ai_metrics.get_ai_metrics_service)
# ---------------------------------------------------------------------------

_service: ErrorsMetricsService | None = None


def get_errors_metrics_service() -> ErrorsMetricsService:
    """Return the process-wide :class:`ErrorsMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = ErrorsMetricsService()
    return _service


def reset_errors_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
