"""Unit tests for the Performance signals read service (Task 11.3, Req 6).

Exercises :class:`app.admin.perf_metrics.PerformanceMetricsService.signals()` in
isolation — the per-route-class average latency mapping, the always-``None`` p95
(no stored distribution → cannot compute; Req 6.2), the top-10 slow route-class
ordering, the slow-job durations read from KV run markers (typical/last, seconds
→ ms; Req 6.3), the ``dbQueryTimeMs`` unavailable handling (Req 6.7), the cache
hit ratio pass-through (Req 6.4), the intentionally-omitted host metrics
(``memory``/``cpu``/``disk`` are a Non-Goal — Req 6.5 / 21.4, NOT unavailable),
the "no new instrumentation" guarantee (``signals()`` never mutates
``AdminMetrics``; Req 21.4 / 15.8), and the secret-free serialization
(Property 3 / Req 15.8).

Dependencies are injected (a fake ``AdminMetrics`` + a fake ``MetricStore``) so
nothing here touches the process-wide singletons or the app DB engine.

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 21.4, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.job_markers import job_marker_name
from app.admin.perf_metrics import (
    PerformanceMetricsService,
    get_perf_metrics_service,
    reset_perf_metrics_service,
)
from app.admin.schemas import PerformanceSignals, assert_no_forbidden_fields

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeAdminMetrics:
    """An ``AdminMetrics`` stand-in exposing only what ``signals()`` reads.

    ``signals()`` must READ from ``snapshot()`` + ``dashboard_cache_hit_ratio``
    and never mutate. Every mutator therefore records the call and raises, so a
    stray write is caught loudly (Req 21.4 / 15.8). ``mutations`` records any
    mutator that fired (should stay empty).
    """

    def __init__(self, *, latency=None, cache_hit_ratio=0.0) -> None:
        # latency shaped like AdminMetrics.snapshot()["latency"]:
        # {route_class: {"count": int, "avg_ms": float}}
        self._latency = latency or {}
        self._cache_hit_ratio = float(cache_hit_ratio)
        self.mutations: list[str] = []

    # -- reads (the only surface signals() is allowed to touch) --------------

    def snapshot(self) -> dict[str, object]:
        return {
            "counters": {},
            "admin_action_total": {},
            "latency": {rc: dict(stats) for rc, stats in self._latency.items()},
            "gauges": {},
        }

    @property
    def dashboard_cache_hit_ratio(self) -> float:
        return self._cache_hit_ratio

    # -- mutators (must never be called by signals()) ------------------------

    def _trip(self, name: str):
        self.mutations.append(name)
        raise AssertionError(
            f"signals() invoked mutator {name!r} — no new instrumentation allowed"
        )

    def incr(self, *a, **k):
        self._trip("incr")

    def record_action(self, *a, **k):
        self._trip("record_action")

    def record_request(self, *a, **k):
        self._trip("record_request")

    def record_authz_denied(self, *a, **k):
        self._trip("record_authz_denied")

    def record_cache(self, *a, **k):
        self._trip("record_cache")

    def set_gauge(self, *a, **k):
        self._trip("set_gauge")


class _FakeStore:
    """A ``MetricStore`` stand-in returning a chosen marker per job name.

    ``snapshot_get(name)`` maps the ``job_marker:{job}`` snapshot name back to the
    bare job name; unknown names return ``None`` (no marker). ``calls`` records
    each requested name so the O(1)/per-job read bound can be checked.
    """

    def __init__(self, markers: dict[str, object]) -> None:
        self._markers = markers  # keyed by bare job name
        self.calls: list[str] = []

    async def snapshot_get(self, name: str):
        self.calls.append(name)
        job = name.split(":", 1)[1] if ":" in name else name
        return self._markers.get(job)


def _service(*, latency=None, cache_hit_ratio=0.0, markers=None):
    metrics = _FakeAdminMetrics(latency=latency, cache_hit_ratio=cache_hit_ratio)
    store = _FakeStore(markers or {})
    svc = PerformanceMetricsService(admin_metrics=metrics, metric_store=store)
    return svc, metrics, store


# ===========================================================================
# 1. Average latency per route-class (Req 6.1)
# ===========================================================================


class TestRouteClassAverages:
    """Validates: Requirements 6.1"""

    async def test_avg_ms_maps_per_route_class(self):
        svc, _, _ = _service(
            latency={
                "a": {"count": 10, "avg_ms": 50.0},
                "b": {"count": 5, "avg_ms": 200.0},
            }
        )
        signals = await svc.signals()
        by_class = {r.routeClass: r.avgMs for r in signals.routeClasses}
        assert by_class == {"a": 50.0, "b": 200.0}
        assert len(signals.routeClasses) == 2


# ===========================================================================
# 2. p95 omitted everywhere (Req 6.2)
# ===========================================================================


class TestP95Omitted:
    """Validates: Requirements 6.2"""

    async def test_p95_is_none_for_all_route_classes(self):
        svc, _, _ = _service(
            latency={
                "a": {"count": 10, "avg_ms": 50.0},
                "b": {"count": 5, "avg_ms": 200.0},
                "c": {"count": 1, "avg_ms": 5.0},
            }
        )
        signals = await svc.signals()
        assert all(r.p95Ms is None for r in signals.routeClasses)
        assert all(r.p95Ms is None for r in signals.topSlowRoutes)


# ===========================================================================
# 3. Top-slow route ordering + top-10 cap (Req 6.3)
# ===========================================================================


class TestTopSlowRoutes:
    """Validates: Requirements 6.3"""

    async def test_top_10_when_more_than_ten_classes_desc(self):
        # 12 route classes with distinct averages 10, 20, ..., 120.
        latency = {
            f"rc{i}": {"count": 1, "avg_ms": float(i * 10)} for i in range(1, 13)
        }
        svc, _, _ = _service(latency=latency)
        signals = await svc.signals()
        assert len(signals.topSlowRoutes) == 10
        avgs = [r.avgMs for r in signals.topSlowRoutes]
        # Sorted descending, and only the ten slowest (120..30) are present.
        assert avgs == sorted(avgs, reverse=True)
        assert avgs == [120.0, 110.0, 100.0, 90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 30.0]

    async def test_fewer_than_ten_returns_all_sorted(self):
        svc, _, _ = _service(
            latency={
                "a": {"count": 1, "avg_ms": 50.0},
                "b": {"count": 1, "avg_ms": 200.0},
                "c": {"count": 1, "avg_ms": 10.0},
            }
        )
        signals = await svc.signals()
        assert [r.routeClass for r in signals.topSlowRoutes] == ["b", "a", "c"]


# ===========================================================================
# 4. Slow jobs from KV markers (Req 6.3)
# ===========================================================================


class TestSlowJobs:
    """Validates: Requirements 6.3"""

    async def test_expected_and_last_duration_ordered_desc(self):
        markers = {
            # typical duration wins → 2.0s → 2000ms
            "rollup": {"expected_duration_seconds": 2.0, "last_duration_seconds": 9.0},
            # no expected → falls back to last → 5.0s → 5000ms
            "purge": {"expected_duration_seconds": None, "last_duration_seconds": 5.0},
            # audit_retention has no marker at all → absent
        }
        svc, _, store = _service(markers=markers)
        signals = await svc.signals()
        assert [(j.name, j.avgMs) for j in signals.topSlowJobs] == [
            ("purge", 5000.0),
            ("rollup", 2000.0),
        ]
        # audit_retention (no marker) never appears.
        assert "audit_retention" not in {j.name for j in signals.topSlowJobs}
        # One point read per known job, no scan.
        assert store.calls == [
            job_marker_name("rollup"),
            job_marker_name("purge"),
            job_marker_name("audit_retention"),
        ]

    async def test_marker_without_usable_duration_is_skipped(self):
        markers = {
            "rollup": {"expected_duration_seconds": None, "last_duration_seconds": None},
            "purge": {"expected_duration_seconds": 3.0},
        }
        svc, _, _ = _service(markers=markers)
        signals = await svc.signals()
        # rollup has no usable duration → skipped; only purge remains.
        assert [j.name for j in signals.topSlowJobs] == ["purge"]
        assert signals.topSlowJobs[0].avgMs == 3000.0


# ===========================================================================
# 5. dbQueryTimeMs unavailable (Req 6.7)
# ===========================================================================


class TestDbQueryTimeUnavailable:
    """Validates: Requirements 6.7"""

    async def test_db_query_time_none_and_listed_unavailable(self):
        svc, _, _ = _service()
        signals = await svc.signals()
        assert signals.dbQueryTimeMs is None
        assert "dbQueryTimeMs" in signals.unavailable


# ===========================================================================
# 6. Cache hit ratio pass-through (Req 6.4)
# ===========================================================================


class TestCacheHitRatio:
    """Validates: Requirements 6.4"""

    async def test_ratio_passed_through(self):
        svc, _, _ = _service(cache_hit_ratio=0.9)
        signals = await svc.signals()
        assert signals.cacheHitRatio == 0.9

    async def test_zero_ratio_is_valid_not_unavailable(self):
        svc, _, _ = _service(cache_hit_ratio=0.0)
        signals = await svc.signals()
        assert signals.cacheHitRatio == 0.0
        assert "cacheHitRatio" not in signals.unavailable


# ===========================================================================
# 7. Host metrics omitted, NOT unavailable (Req 6.5 / 21.4)
# ===========================================================================


class TestHostMetricsOmitted:
    """Validates: Requirements 6.5, 21.4"""

    async def test_host_metrics_none_and_not_in_unavailable(self):
        svc, _, _ = _service()
        signals = await svc.signals()
        assert signals.memoryBytes is None
        assert signals.cpuPercent is None
        assert signals.diskBytes is None
        # Host metrics are an intentional Non-Goal — never listed as unavailable.
        for field in ("memoryBytes", "cpuPercent", "diskBytes"):
            assert field not in signals.unavailable


# ===========================================================================
# 8. No new instrumentation (Req 21.4 / 15.8)
# ===========================================================================


class TestNoNewInstrumentation:
    """Validates: Requirements 21.4, 15.8"""

    async def test_signals_never_mutates_admin_metrics(self):
        svc, metrics, _ = _service(
            latency={"a": {"count": 3, "avg_ms": 12.0}},
            cache_hit_ratio=0.5,
        )
        # signals() must complete without tripping any mutator guard.
        await svc.signals()
        assert metrics.mutations == []

    async def test_exclude_none_drops_host_metric_keys(self):
        svc, _, _ = _service()
        signals = await svc.signals()
        dumped = signals.model_dump(exclude_none=True)
        # None-valued signals (host metrics + dbQueryTimeMs) drop out.
        for field in ("memoryBytes", "cpuPercent", "diskBytes", "dbQueryTimeMs"):
            assert field not in dumped
        # Present aggregates are retained.
        assert "routeClasses" in dumped
        assert "cacheHitRatio" in dumped


# ===========================================================================
# 9. Secret-free serialization (Req 15.8 / Property 3)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_no_forbidden_fields(self):
        svc, _, _ = _service(
            latency={"a": {"count": 1, "avg_ms": 5.0}},
            cache_hit_ratio=0.75,
            markers={"rollup": {"expected_duration_seconds": 1.0}},
        )
        signals = await svc.signals()
        assert_no_forbidden_fields(signals.model_dump(by_alias=True))


# ===========================================================================
# 10. Empty metrics still valid (Req 6.1 / 6.4)
# ===========================================================================


class TestEmptyMetrics:
    """Validates: Requirements 6.1, 6.4"""

    async def test_no_latency_no_cache_activity(self):
        svc, _, _ = _service()  # empty latency, ratio 0.0, no markers
        signals = await svc.signals()
        assert isinstance(signals, PerformanceSignals)
        assert signals.routeClasses == []
        assert signals.topSlowRoutes == []
        assert signals.topSlowJobs == []
        assert signals.cacheHitRatio == 0.0


# ===========================================================================
# Singleton accessor
# ===========================================================================


class TestAccessor:
    def test_get_perf_metrics_service_is_singleton(self):
        reset_perf_metrics_service()
        try:
            assert get_perf_metrics_service() is get_perf_metrics_service()
        finally:
            reset_perf_metrics_service()
