"""Performance signals assembled from bounded current-process observations.

Route-class averages and nearest-rank p95 values come from
:class:`app.admin.metrics.AdminMetrics`; each p95 is based on that class's latest
bounded latency sample. ``cacheObservationCount`` is exactly cache hits plus
misses, allowing clients to distinguish a numeric zero ratio with no
observations. The whole route/cache payload is explicitly scoped to
``current_process``. Slow-job values still come from the fixed set of durable KV
run markers. DB query timing and host resource metrics remain uninstrumented.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.admin.job_markers import job_marker_name
from app.admin.metrics import get_admin_metrics
from app.admin.schemas import PerformanceSignals, RouteClassLatency, SlowJob

logger = logging.getLogger(__name__)

__all__ = [
    "PerformanceMetricsService",
    "get_perf_metrics_service",
    "reset_perf_metrics_service",
]

# Jobs that publish KV run markers via ``app.admin.job_markers`` - the only ones
# with a measurable typical/last duration to surface as a slow job (Req 6.3).
# Matches the marker names written by ``run_admin_jobs`` (see ``jobs_panel``).
_JOB_NAMES: tuple[str, ...] = ("rollup", "purge", "audit_retention")

# Max entries in the top-slow lists (Req 6.3).
_TOP_N = 10


class PerformanceMetricsService:
    """Performance signals from existing aggregates only (Req 6).

    Depends on the in-process :class:`AdminMetrics` (route-class latency + cache
    ratio) and, for slow-job durations, the shared
    :class:`~app.admin.metric_store.MetricStore` KV run markers. Both are
    optionally injected for tests; otherwise the process-wide singletons are
    resolved lazily so importing this module forces no DB/engine init.

    Holds **no** dependency on another Domain_Metrics_Service (import-graph guard,
    Req 19.2/19.3/19.5).
    """

    def __init__(self, *, admin_metrics=None, metric_store=None) -> None:
        self._admin_metrics = admin_metrics
        self._metric_store = metric_store

    def _get_admin_metrics(self):
        if self._admin_metrics is not None:
            return self._admin_metrics
        return get_admin_metrics()

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def signals(self) -> PerformanceSignals:
        """Return the performance signals (existing aggregates only - Req 6.1-6.7).

        Assembles per-route-class latency, the top-10 slow route-classes and jobs,
        DB query time, and the cache hit ratio. Async because slow-job durations
        are read from KV run markers via the shared ``MetricStore``; the
        route-class/cache figures are synchronous in-process reads. See the module
        docstring for the exact source mapping and every documented gap.

        **O(1) read.** One ``AdminMetrics`` snapshot (a dict copy) + one KV point
        read per known job (3). No row scan, no new instrumentation.
        """
        now = datetime.now(timezone.utc)

        metrics = self._get_admin_metrics()
        snapshot = metrics.snapshot()
        latency = snapshot.get("latency", {}) or {}

        # Per-class p95 is the exact nearest-rank percentile of the bounded
        # sample captured in the same snapshot as the average.
        route_classes: list[RouteClassLatency] = [
            RouteClassLatency(
                routeClass=str(route_class),
                avgMs=float((stats or {}).get("avg_ms", 0.0) or 0.0),
                p95Ms=(
                    float((stats or {})["p95_ms"])
                    if (stats or {}).get("p95_ms") is not None
                    else None
                ),
            )
            for route_class, stats in latency.items()
        ]

        top_slow_routes = sorted(
            route_classes, key=lambda route: (-route.avgMs, route.routeClass)
        )[:_TOP_N]
        top_slow_jobs = await self._slow_jobs()

        # Keep ratio and observation count from one consistent snapshot. Older
        # injected fakes may omit counters, so retain the ratio-property fallback.
        counters = snapshot.get("counters", {}) or {}
        hits = int(counters.get("dashboard_cache_hit", 0) or 0)
        misses = int(counters.get("dashboard_cache_miss", 0) or 0)
        cache_observation_count = hits + misses
        if cache_observation_count:
            cache_hit_ratio = hits / cache_observation_count
        else:
            cache_hit_ratio = float(
                counters.get(
                    "dashboard_cache_hit_ratio",
                    getattr(metrics, "dashboard_cache_hit_ratio", 0.0),
                )
                or 0.0
            )

        return PerformanceSignals(
            routeClasses=route_classes,
            topSlowRoutes=top_slow_routes,
            topSlowJobs=top_slow_jobs,
            dbQueryTimeMs=None,
            cacheHitRatio=cache_hit_ratio,
            cacheObservationCount=cache_observation_count,
            dataScope="current_process",
            memoryBytes=None,
            cpuPercent=None,
            diskBytes=None,
            unavailable=["dbQueryTimeMs"],
            computedAt=now.isoformat(),
        )

    async def _slow_jobs(self) -> list[SlowJob]:
        """Build the slow-job list from KV run markers (Req 6.3).

        One ``snapshot_get`` per known job. ``avgMs`` prefers the typical
        ``expected_duration_seconds`` (EWMA of completed runs), falling back to the
        last observed ``last_duration_seconds``, converted seconds->ms. Jobs with no
        marker or no usable duration yet are skipped; the result is ordered by
        ``avgMs`` descending and capped at the top 10.
        """
        store = self._get_metric_store()
        jobs: list[SlowJob] = []
        for job_name in _JOB_NAMES:
            try:
                marker = await store.snapshot_get(job_marker_name(job_name))
            except Exception:  # a marker read failure degrades gracefully (Req 6.7)
                logger.debug("Job marker read failed for %s", job_name, exc_info=True)
                marker = None
            if not marker:
                continue
            seconds = self._job_duration_seconds(marker)
            if seconds is None:
                continue
            jobs.append(SlowJob(name=job_name, avgMs=round(seconds * 1000.0, 2)))
        jobs.sort(key=lambda j: j.avgMs, reverse=True)
        return jobs[:_TOP_N]

    @staticmethod
    def _job_duration_seconds(marker: dict) -> float | None:
        """Typical (expected) duration in seconds, else last observed, else None."""
        for field in ("expected_duration_seconds", "last_duration_seconds"):
            value = marker.get(field)
            if value is None:
                continue
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                continue
            if seconds >= 0:
                return seconds
        return None


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.errors_metrics.get_errors_metrics_service)
# ---------------------------------------------------------------------------

_service: PerformanceMetricsService | None = None


def get_perf_metrics_service() -> PerformanceMetricsService:
    """Return the process-wide :class:`PerformanceMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = PerformanceMetricsService()
    return _service


def reset_perf_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
