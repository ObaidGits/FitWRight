"""Unit tests for the ``OverviewService`` KPI read model (Task 14.4).

Covers the Requirement-13 guarantees of the Overview KPI cards
(see :mod:`app.admin.overview`), all assembled from shared primitives via
injected fakes/spies (no DB, no singletons):

- **KPI math + UTC day/window boundaries (Req 13.3).** ``totalUsers`` from the
  injected ``MetricsService.stats()`` snapshot; ``newUsersToday`` from a single
  day-bounded ``AdminRepo.metric_for_day("signups", ...)`` whose start/end are
  asserted to be *exactly* today's UTC day bounds (``00:00:00`` -> next day
  ``00:00:00``); ``aiCallsToday`` from ``MetricStore.sum([AI_CALLS], today,
  today)``; ``errorRate24h`` = ``5xx / (2xx+4xx+5xx) * 100`` over the last two
  UTC days (today + yesterday) - asserted; ``purgeBacklog`` from the in-process
  gauge.
- **errorRate24h zero + clamp (Req 13.3).** ``total == 0`` => ``0.00`` (a
  computable value, not "unavailable"); result is clamped to ``0.00``-``100.00``.
- **unavailable-KPI partial response (Req 13.7).** One failing source isolates to
  its own KPI (``value=None``, ``unavailable=True``) while every other KPI still
  computes; the whole ``kpis()`` call succeeds. The unset ``purge_backlog`` gauge
  reports unavailable rather than a false 0.
- **Secret-free (Req 15.8).** The serialized ``OverviewKpis`` passes the
  response-boundary forbidden-field guard.
- **Stale flag (Req 13.9).** ``stats().stale`` propagates to ``OverviewKpis.stale``.

Requirements: 13.3, 13.7, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.metric_registry import (
    AI_CALLS,
    REQUEST_2XX,
    REQUEST_4XX,
    REQUEST_5XX,
)
from app.admin.overview import OverviewService, _day_bounds, _day_str
from app.admin.schemas import OverviewKpis, assert_no_forbidden_fields

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Injectable fakes / spies (shared primitives only - no DB, no singletons)
# ---------------------------------------------------------------------------


class FakeMetricsService:
    """Stand-in for ``MetricsService`` - ``stats()`` returns a canned dict."""

    def __init__(self, stats: dict | None = None, *, raises: bool = False) -> None:
        self._stats = stats or {}
        self._raises = raises

    async def stats(self) -> dict:
        if self._raises:
            raise RuntimeError("snapshot unavailable")
        return dict(self._stats)


class SpyAdminRepo:
    """Stand-in for ``AdminRepo`` capturing the ``metric_for_day`` call args."""

    def __init__(self, value: int = 0, *, raises: bool = False) -> None:
        self._value = value
        self._raises = raises
        self.calls: list[tuple[str, str, str]] = []

    async def metric_for_day(self, metric: str, day_start: str, day_end: str) -> int:
        self.calls.append((metric, day_start, day_end))
        if self._raises:
            raise RuntimeError("count failed")
        return self._value


class FakeMetricStore:
    """Stand-in for ``MetricStore``. ``sum`` returns per-key seeded values and
    records the ``(keys, day_from, day_to)`` of every call."""

    def __init__(
        self,
        *,
        ai_calls: int = 0,
        err_5xx: int = 0,
        req_total: int = 0,
        raises: bool = False,
    ) -> None:
        self._ai_calls = ai_calls
        self._err_5xx = err_5xx
        self._req_total = req_total
        self._raises = raises
        self.calls: list[tuple[frozenset[str], str, str]] = []

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        key_set = frozenset(keys)
        self.calls.append((key_set, day_from, day_to))
        if self._raises:
            raise RuntimeError("store unavailable")
        if key_set == frozenset({AI_CALLS}):
            return self._ai_calls
        if key_set == frozenset({REQUEST_5XX}):
            return self._err_5xx
        if key_set == frozenset({REQUEST_2XX, REQUEST_4XX, REQUEST_5XX}):
            return self._req_total
        return 0


class FakeAdminMetrics:
    """Stand-in for ``AdminMetrics`` - ``snapshot()`` returns a gauges dict."""

    def __init__(self, gauges: dict | None = None, *, raises: bool = False) -> None:
        self._gauges = gauges
        self._raises = raises

    def snapshot(self) -> dict:
        if self._raises:
            raise RuntimeError("gauge read failed")
        return {"gauges": dict(self._gauges) if self._gauges is not None else {}}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _service(**over) -> OverviewService:
    """Build an ``OverviewService`` with sensible healthy defaults, overridable."""
    defaults = dict(
        metrics_service=FakeMetricsService({"totalUsers": 1500, "stale": False}),
        admin_repo=SpyAdminRepo(12),
        metric_store=FakeMetricStore(ai_calls=42, err_5xx=5, req_total=200),
        admin_metrics=FakeAdminMetrics({"purge_backlog": 7}),
    )
    defaults.update(over)
    return OverviewService(**defaults)


# ===========================================================================
# 1. KPI math + UTC boundaries (Req 13.3)
# ===========================================================================


class TestKpiMathAndUtcBoundaries:
    """Validates: Requirements 13.3"""

    async def test_all_kpis_computed_from_injected_sources(self):
        repo = SpyAdminRepo(12)
        store = FakeMetricStore(ai_calls=42, err_5xx=5, req_total=200)
        svc = _service(
            metrics_service=FakeMetricsService({"totalUsers": 1500, "stale": False}),
            admin_repo=repo,
            metric_store=store,
            admin_metrics=FakeAdminMetrics({"purge_backlog": 7}),
        )

        kpis = await svc.kpis()

        # totalUsers from the snapshot; stale propagated.
        assert kpis.totalUsers.value == 1500
        assert kpis.totalUsers.unavailable is False
        assert kpis.stale is False
        # newUsersToday from AdminRepo.metric_for_day.
        assert kpis.newUsersToday.value == 12
        # aiCallsToday from MetricStore.sum([AI_CALLS], today, today).
        assert kpis.aiCallsToday.value == 42
        # errorRate24h = 5 / 200 * 100 = 2.5 (2dp).
        assert kpis.errorRate24h.value == 2.5
        assert kpis.errorRate24h.unavailable is False
        # purgeBacklog from the in-process gauge.
        assert kpis.purgeBacklog.value == 7

    async def test_new_users_today_uses_exact_utc_day_bounds(self):
        """The signups count must be day-bounded on today's UTC day (Req 13.3)."""
        repo = SpyAdminRepo(12)
        svc = _service(admin_repo=repo)

        await svc.kpis()

        # Exactly one metric_for_day call, for "signups".
        assert len(repo.calls) == 1
        metric, start, end = repo.calls[0]
        assert metric == "signups"
        # The captured bounds equal today's UTC day bounds: 00:00:00 (inclusive)
        # -> next day 00:00:00 (exclusive). This proves UTC boundaries (Req 13.3).
        expected_start, expected_end = _day_bounds(_today())
        assert (start, end) == (expected_start, expected_end)
        # Structural cross-check: start is midnight today, end is midnight next day.
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        assert start_dt.tzinfo is not None and start_dt.utcoffset() == timedelta(0)
        assert (start_dt.hour, start_dt.minute, start_dt.second) == (0, 0, 0)
        assert end_dt - start_dt == timedelta(days=1)

    async def test_error_rate_sums_span_today_and_yesterday_utc(self):
        """errorRate24h sums the durable REQUEST_* keys over the last 2 UTC days."""
        store = FakeMetricStore(ai_calls=42, err_5xx=5, req_total=200)
        svc = _service(metric_store=store)

        await svc.kpis()

        today, yesterday = _today(), _yesterday()

        # The error-rate errors sum: [REQUEST_5XX] over [yesterday, today].
        err_calls = [c for c in store.calls if c[0] == frozenset({REQUEST_5XX})]
        assert len(err_calls) == 1
        _, day_from, day_to = err_calls[0]
        assert (day_from, day_to) == (yesterday, today)

        # The error-rate total sum: [2XX, 4XX, 5XX] over the same 2-day window.
        total_calls = [
            c for c in store.calls
            if c[0] == frozenset({REQUEST_2XX, REQUEST_4XX, REQUEST_5XX})
        ]
        assert len(total_calls) == 1
        _, t_from, t_to = total_calls[0]
        assert (t_from, t_to) == (yesterday, today)

        # aiCallsToday sums [AI_CALLS] over today..today (a single UTC day).
        ai_calls = [c for c in store.calls if c[0] == frozenset({AI_CALLS})]
        assert len(ai_calls) == 1
        _, a_from, a_to = ai_calls[0]
        assert (a_from, a_to) == (today, today)

    async def test_returns_overview_kpis_model(self):
        kpis = await _service().kpis()
        assert isinstance(kpis, OverviewKpis)
        assert kpis.computedAt  # ISO timestamp present


# ===========================================================================
# 2. errorRate24h zero + clamp (Req 13.3)
# ===========================================================================


class TestErrorRateZeroAndClamp:
    """Validates: Requirements 13.3"""

    async def test_zero_total_requests_is_zero_not_unavailable(self):
        # total == 0 -> 0.00 (0 requests is 0% error, a computable value).
        store = FakeMetricStore(ai_calls=0, err_5xx=0, req_total=0)
        kpis = await _service(metric_store=store).kpis()
        assert kpis.errorRate24h.value == 0.0
        assert kpis.errorRate24h.unavailable is False

    async def test_zero_errors_nonzero_total(self):
        store = FakeMetricStore(err_5xx=0, req_total=100)
        kpis = await _service(metric_store=store).kpis()
        assert kpis.errorRate24h.value == 0.0
        assert kpis.errorRate24h.unavailable is False

    async def test_rate_clamped_to_100_when_errors_exceed_total(self):
        # Shouldn't happen, but if 5xx > total the rate clamps to 100.00.
        store = FakeMetricStore(err_5xx=150, req_total=100)
        kpis = await _service(metric_store=store).kpis()
        assert kpis.errorRate24h.value == 100.0

    async def test_rate_rounded_to_two_decimals(self):
        # 1 / 3 * 100 = 33.333... -> 33.33 (2dp).
        store = FakeMetricStore(err_5xx=1, req_total=3)
        kpis = await _service(metric_store=store).kpis()
        assert kpis.errorRate24h.value == 33.33


# ===========================================================================
# 3. unavailable-KPI partial response (Req 13.7)
# ===========================================================================


class TestPartialResponseIsolation:
    """Validates: Requirements 13.7"""

    async def test_totals_source_failure_isolates_to_total_users(self):
        # metrics_service.stats() raises -> totalUsers unavailable, rest computed.
        svc = _service(metrics_service=FakeMetricsService(raises=True))
        kpis = await svc.kpis()

        assert kpis.totalUsers.unavailable is True
        assert kpis.totalUsers.value is None
        # stale cannot be confirmed -> stays False.
        assert kpis.stale is False
        # Every other KPI still computed with a value.
        assert kpis.newUsersToday.value == 12
        assert kpis.aiCallsToday.value == 42
        assert kpis.errorRate24h.value == 2.5
        assert kpis.purgeBacklog.value == 7
        assert all(
            not k.unavailable
            for k in (
                kpis.newUsersToday,
                kpis.aiCallsToday,
                kpis.errorRate24h,
                kpis.purgeBacklog,
            )
        )

    async def test_new_users_source_failure_isolates(self):
        svc = _service(admin_repo=SpyAdminRepo(raises=True))
        kpis = await svc.kpis()

        assert kpis.newUsersToday.unavailable is True
        assert kpis.newUsersToday.value is None
        # Others unaffected.
        assert kpis.totalUsers.value == 1500
        assert kpis.aiCallsToday.value == 42
        assert kpis.errorRate24h.value == 2.5
        assert kpis.purgeBacklog.value == 7

    async def test_store_failure_isolates_ai_calls_and_error_rate(self):
        # A store outage takes out the two store-backed KPIs, nothing else.
        svc = _service(metric_store=FakeMetricStore(raises=True))
        kpis = await svc.kpis()

        assert kpis.aiCallsToday.unavailable is True and kpis.aiCallsToday.value is None
        assert kpis.errorRate24h.unavailable is True and kpis.errorRate24h.value is None
        # Snapshot-, repo-, gauge-backed KPIs still return.
        assert kpis.totalUsers.value == 1500
        assert kpis.newUsersToday.value == 12
        assert kpis.purgeBacklog.value == 7

    async def test_purge_backlog_gauge_unset_is_unavailable(self):
        # Gauge never set -> cannot report a count (unavailable, not a false 0).
        svc = _service(admin_metrics=FakeAdminMetrics({}))
        kpis = await svc.kpis()

        assert kpis.purgeBacklog.unavailable is True
        assert kpis.purgeBacklog.value is None
        # Everything else still computes.
        assert kpis.totalUsers.value == 1500
        assert kpis.newUsersToday.value == 12

    async def test_gauge_read_failure_isolates_to_purge_backlog(self):
        svc = _service(admin_metrics=FakeAdminMetrics(raises=True))
        kpis = await svc.kpis()

        assert kpis.purgeBacklog.unavailable is True
        assert kpis.purgeBacklog.value is None
        assert kpis.totalUsers.value == 1500

    async def test_kpis_call_succeeds_even_when_every_source_fails(self):
        # Total degradation still yields a well-formed (all-unavailable) response.
        svc = OverviewService(
            metrics_service=FakeMetricsService(raises=True),
            admin_repo=SpyAdminRepo(raises=True),
            metric_store=FakeMetricStore(raises=True),
            admin_metrics=FakeAdminMetrics(raises=True),
        )
        kpis = await svc.kpis()
        assert isinstance(kpis, OverviewKpis)
        for k in (
            kpis.totalUsers,
            kpis.newUsersToday,
            kpis.aiCallsToday,
            kpis.errorRate24h,
            kpis.purgeBacklog,
        ):
            assert k.unavailable is True and k.value is None


# ===========================================================================
# 4. Secret-free serialization (Req 15.8 / Property 3)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_kpis_serialization_has_no_forbidden_fields(self):
        kpis = await _service().kpis()
        assert isinstance(kpis, OverviewKpis)
        assert_no_forbidden_fields(kpis.model_dump(by_alias=True))


# ===========================================================================
# 5. Stale flag propagation (Req 13.9)
# ===========================================================================


class TestStaleFlag:
    """Validates: Requirements 13.3"""

    async def test_stale_true_propagates_from_stats(self):
        svc = _service(
            metrics_service=FakeMetricsService({"totalUsers": 10, "stale": True})
        )
        kpis = await svc.kpis()
        assert kpis.stale is True
        assert kpis.totalUsers.value == 10

    async def test_stale_defaults_false_when_absent(self):
        svc = _service(metrics_service=FakeMetricsService({"totalUsers": 10}))
        kpis = await svc.kpis()
        assert kpis.stale is False
