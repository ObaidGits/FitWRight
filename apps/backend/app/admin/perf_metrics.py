"""Performance signals read model — the ``PerformanceMetricsService`` (Req 6).

``PerformanceMetricsService.signals()`` assembles the
:class:`~app.admin.schemas.PerformanceSignals` served by
``GET /api/v1/admin/performance`` (endpoint is Task 11.2): per-route-class
latency, the top-10 slowest route-classes and background jobs, DB query time,
and the dashboard cache hit ratio. It is a cohesive, single-responsibility
Domain_Metrics_Service (design §Bounded Contexts) that depends **only** on shared
primitives — the in-process :class:`~app.admin.metrics.AdminMetrics`, the shared
:class:`~app.admin.metric_store.MetricStore` (for KV job-run markers), the job
marker key helper, config, and the response schemas — never on another
Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5; Property 9).

**Existing signals only — NO new instrumentation (Req 21.4 / Req 6).** Every
number here is read from an aggregate the backend *already* produces. This module
adds no histogram, no timer, no counter, and no probe. Where an existing
aggregate cannot answer a field, the field is omitted (``None``) or surfaced as
*unavailable* rather than newly instrumented.

**O(1) read (Req 6.6, delivered in Task 11.2; built O(1) here).** The route-class
and cache figures are single in-process reads of ``AdminMetrics`` (a dict copy,
independent of user/row count). The job figures are a fixed handful of KV point
reads — one ``MetricStore.snapshot_get`` per known job (3), never a scan.

---

## Exact source mapping (and every documented gap)

``routeClasses`` — avg latency per route-class (Req 6.1)
    From ``AdminMetrics.snapshot()["latency"]``, which exposes each route-class's
    ``[sum_ms, count]`` bucket as ``{route_class: {count, avg_ms}}``. ``avgMs`` =
    the already-computed ``avg_ms = sum_ms / count`` — the existing aggregate. One
    :class:`~app.admin.schemas.RouteClassLatency` per bucketed route-class.

``p95Ms`` — omitted everywhere (``None``) — **documented (Req 6.2)**
    Req 6.2 asks for p95 *where the per-route-class aggregates support computing
    it from stored aggregates*. The ``AdminMetrics`` latency bucket stores only
    ``[sum_ms, count]`` — a running sum and a count, **not** a distribution or
    histogram — so a percentile **cannot** be derived from the stored aggregate.
    Per Req 6.2 the p95 field is therefore **omitted** (``p95Ms = None``) for every
    route-class. Adding a histogram to enable p95 would be *new instrumentation*,
    which Req 21.4 / Req 6 forbid, so it is intentionally not added. If a durable
    latency histogram is introduced later, ``p95Ms`` can be populated without any
    schema change.

``topSlowRoutes`` — top-10 slow route-classes (Req 6.3)
    The same route-class latency aggregates, ordered by ``avgMs`` descending and
    truncated to 10. Fewer than 10 are returned when fewer route-classes have been
    bucketed (Req 6.3 permits this). Each entry carries the same ``p95Ms = None``
    for the reason above.

``topSlowJobs`` — top-10 slow background jobs (Req 6.3)
    From the per-job KV **run markers** written by
    :mod:`app.admin.job_markers` (read via ``MetricStore.snapshot_get`` — the same
    pattern :class:`~app.admin.jobs_panel.JobsPanelService` uses). Each marker
    carries ``expected_duration_seconds`` (an EWMA of completed-run durations —
    the *typical* duration) and ``last_duration_seconds``. ``avgMs`` uses the
    typical ``expected_duration_seconds`` when present, else the observed
    ``last_duration_seconds``, converted seconds→milliseconds. Only the three jobs
    run through :func:`app.admin.jobs.run_admin_jobs` (``rollup``, ``purge``,
    ``audit_retention``) publish markers, so at most three jobs appear (well under
    the top-10 cap). A job whose marker is absent, or whose marker has no usable
    duration yet, is **skipped** (no fabricated 0). The list is ordered by
    ``avgMs`` descending.

``dbQueryTimeMs`` — ``None`` + listed *unavailable* — **documented gap (Req 6.4/6.7)**
    Req 6.4 wants DB query time from an existing computed aggregate. There is **no**
    DB-query-time aggregate anywhere in the admin surface today — ``AdminMetrics``
    tracks request latency per route-class (which includes, but does not isolate,
    DB time), cache hits, and a few gauges, but no DB-query-time metric — and
    adding one would be new instrumentation (Req 21.4). ``dbQueryTimeMs`` is
    therefore ``None`` **and** the field name is added to ``unavailable`` (Req 6.7):
    it is a signal we *expose* but for which no data source exists yet.

``cacheHitRatio`` — dashboard cache hit ratio (Req 6.4)
    ``AdminMetrics.dashboard_cache_hit_ratio`` — ``hits / (hits + misses)`` in
    ``[0.0, 1.0]``, from the existing ``dashboard_cache_hit`` / ``_miss`` counters.
    Returned directly. With no cache activity yet the property returns ``0.0``,
    which is a valid ratio (a real "0% of 0 observations" reading), so it is
    reported as ``0.0`` — **not** listed as unavailable.

``memoryBytes`` / ``cpuPercent`` / ``diskBytes`` — omitted (``None``) — Req 6.5 / Non-Goal 21.4
    Optional host metrics. The backend produces **no** host CPU/memory/disk
    aggregate (explicit Non-Goal, Req 21.4), so all three are left ``None`` and are
    dropped from the response by the endpoint's ``exclude_none`` serialization
    (Req 6.5 — "omit each such field WHEN its value is not already present").

    **``unavailable`` vs omitted host metrics (deliberate distinction).**
    ``unavailable`` lists fields we *do* expose as signals but have **no data for
    right now** (e.g. ``dbQueryTimeMs``) — an operator should read that as "this
    should have a value; the source isn't wired up". The host metrics are a
    different case: we *never* expose them (they are a Non-Goal), so they are
    simply ``None``/omitted and are **not** placed in ``unavailable``. Conflating
    the two would mislead operators into thinking host metrics are a wired-but-
    empty signal rather than an intentional exclusion.

``unavailable`` (Req 6.7)
    The list of exposed-but-empty signal field names. Currently exactly
    ``["dbQueryTimeMs"]`` (no DB-query-time source). Route-class latency and the
    cache ratio always have data (even if empty/zero), so they are never listed.

``computedAt``
    Current UTC time as an ISO-8601 string.
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

# Jobs that publish KV run markers via ``app.admin.job_markers`` — the only ones
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
        """Return the performance signals (existing aggregates only — Req 6.1–6.7).

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

        # -- per-route-class avg latency (Req 6.1); p95 omitted (Req 6.2) ------
        # ``avg_ms`` is the existing aggregate (sum_ms / count). p95 cannot be
        # derived from the stored [sum_ms, count] bucket (no distribution), so
        # ``p95Ms`` is None for every class — no histogram is added (Req 21.4).
        route_classes: list[RouteClassLatency] = [
            RouteClassLatency(
                routeClass=str(route_class),
                avgMs=float((stats or {}).get("avg_ms", 0.0) or 0.0),
                p95Ms=None,
            )
            for route_class, stats in latency.items()
        ]

        # -- top-10 slow route-classes by avg latency desc (Req 6.3) ----------
        top_slow_routes = sorted(route_classes, key=lambda r: r.avgMs, reverse=True)[
            :_TOP_N
        ]

        # -- top-10 slow background jobs by typical/last duration desc (Req 6.3)
        top_slow_jobs = await self._slow_jobs()

        # -- cache hit ratio (Req 6.4); 0.0 is a valid reading, not unavailable
        cache_hit_ratio = float(metrics.dashboard_cache_hit_ratio)

        # -- DB query time (Req 6.4/6.7): no existing aggregate → None + listed
        # unavailable (a signal we expose but have no source for yet).
        unavailable = ["dbQueryTimeMs"]

        return PerformanceSignals(
            routeClasses=route_classes,
            topSlowRoutes=top_slow_routes,
            topSlowJobs=top_slow_jobs,
            dbQueryTimeMs=None,
            cacheHitRatio=cache_hit_ratio,
            # Host metrics are a Non-Goal (Req 21.4): never produced → None (the
            # endpoint drops them via exclude_none) and NOT listed in unavailable.
            memoryBytes=None,
            cpuPercent=None,
            diskBytes=None,
            unavailable=unavailable,
            computedAt=now.isoformat(),
        )

    async def _slow_jobs(self) -> list[SlowJob]:
        """Build the slow-job list from KV run markers (Req 6.3).

        One ``snapshot_get`` per known job. ``avgMs`` prefers the typical
        ``expected_duration_seconds`` (EWMA of completed runs), falling back to the
        last observed ``last_duration_seconds``, converted seconds→ms. Jobs with no
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
