"""Unit tests for durable error totals plus live route-class failures.

Selected-window 4xx/5xx, AI failures, and trend values come only from durable
UTC-day buckets. An injected ``AdminMetrics`` snapshot supplies bounded
current-process route ``failure_count`` values without changing those totals.
Job and storage source counts remain explicitly not instrumented.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.errors_metrics import ErrorsMetricsService
from app.admin.metric_registry import (
    AI_FAILURE,
    REQUEST_4XX,
    REQUEST_5XX,
)
from app.admin.metric_store import MetricStore
from app.admin.schemas import ErrorsSummary, assert_no_forbidden_fields

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore on the isolated engine (errors reads only)."""
    return MetricStore(isolated_db.session_factory)


class _FakeAdminMetrics:
    """Current-process route buckets returned by ``AdminMetrics.snapshot``."""

    def __init__(self, failures: dict[str, int] | None = None) -> None:
        self._failures = failures or {}
        self.snapshot_calls = 0

    def snapshot(self) -> dict[str, object]:
        self.snapshot_calls += 1
        return {
            "latency": {
                route_class: {"failure_count": failure_count}
                for route_class, failure_count in self._failures.items()
            }
        }


def _service(store, admin_metrics=None) -> ErrorsMetricsService:
    """Inject durable storage and an isolated current-process route source."""
    return ErrorsMetricsService(
        metric_store=store,
        admin_metrics=admin_metrics or _FakeAdminMetrics(),
    )


def _day(offset: int = 0) -> str:
    """The UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


class _CountingStore:
    """A ``MetricStore``-shaped wrapper that counts ``sum`` / ``series`` calls.

    Delegates every read to the wrapped real store so the returned data is real,
    while tallying how many bounded reads a single ``summary`` issues - the proof
    of O(1)-w.r.t.-data-volume (Req 5.7). ``upsert`` is delegated for seeding.
    """

    def __init__(self, inner: MetricStore) -> None:
        self._inner = inner
        self.sum_calls = 0
        self.series_calls = 0

    async def upsert(self, day: str, key: str, value: int) -> None:
        await self._inner.upsert(day, key, value)

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        self.sum_calls += 1
        return await self._inner.sum(keys, day_from, day_to)

    async def series(self, key: str, days: int):
        self.series_calls += 1
        return await self._inner.series(key, days)


# ===========================================================================
# 1. Bucket math (Req 5.1 / 5.3) - windowed sums, out-of-window excluded
# ===========================================================================


class TestBucketMath:
    """Validates: Requirements 5.1, 5.3"""

    async def test_windowed_counts_and_by_source_exclude_out_of_window(self, isolated_db):
        store = _store(isolated_db)
        # Days INSIDE the 30-day window (day_from = today-29): today, -3, -29.
        await store.upsert(_day(0), REQUEST_4XX, 5)
        await store.upsert(_day(0), REQUEST_5XX, 2)
        await store.upsert(_day(0), AI_FAILURE, 1)
        await store.upsert(_day(3), REQUEST_4XX, 10)
        await store.upsert(_day(3), REQUEST_5XX, 4)
        await store.upsert(_day(3), AI_FAILURE, 2)
        await store.upsert(_day(29), REQUEST_4XX, 1)
        await store.upsert(_day(29), REQUEST_5XX, 1)
        await store.upsert(_day(29), AI_FAILURE, 1)
        # A day OUTSIDE the window (40 days ago) - must be excluded entirely.
        await store.upsert(_day(40), REQUEST_4XX, 100)
        await store.upsert(_day(40), REQUEST_5XX, 100)
        await store.upsert(_day(40), AI_FAILURE, 100)

        summary = await _service(store).summary(30)

        assert summary.counts4xx == 16  # 5 + 10 + 1 (40-day row excluded)
        assert summary.counts5xx == 7  # 2 + 4 + 1
        assert summary.bySource.api == 23  # counts4xx + counts5xx
        assert summary.bySource.ai == 4  # 1 + 2 + 1 (AI_FAILURE, windowed)
        assert summary.bySource.job == 0  # documented gap
        assert summary.bySource.storage == 0  # documented gap
        assert summary.notInstrumented == ["bySource.job", "bySource.storage"]
        assert summary.window == 30
        assert summary.windowStartDate == _day(29)
        assert summary.windowEndDate == _day(0)
        assert summary.granularity == "utc_day"
        assert (
            summary.dataScope
            == "durable_utc_day_buckets_plus_current_process_route_classes"
        )

    async def test_counts_are_non_negative_and_window_echoed(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(1), REQUEST_4XX, 3)
        summary = await _service(store).summary(7)
        assert summary.window == 7
        assert summary.counts4xx >= 0 and summary.counts5xx >= 0
        assert summary.bySource.api == summary.counts4xx + summary.counts5xx


# ===========================================================================
# 2. Trend (Req 5.4) - window points, oldest->newest, per-day 4xx+5xx
# ===========================================================================


class TestTrend:
    """Validates: Requirements 5.4"""

    async def test_trend_shape_ordering_values_and_last_is_today(self, isolated_db):
        store = _store(isolated_db)
        # today: 4xx=5 5xx=2 -> 7 ; two days ago: 4xx=1 5xx=0 -> 1 ; rest empty.
        await store.upsert(_day(0), REQUEST_4XX, 5)
        await store.upsert(_day(0), REQUEST_5XX, 2)
        await store.upsert(_day(2), REQUEST_4XX, 1)

        summary = await _service(store).summary(7)

        # Exactly `window` points.
        assert len(summary.trend) == 7
        # Oldest->newest: dates strictly ascending.
        dates = [p.date for p in summary.trend]
        assert dates == sorted(dates)
        # Last point is today, value = 4xx + 5xx for today.
        assert summary.trend[-1].date == _day(0)
        assert summary.trend[-1].value == 7
        # Two days ago = 1 (4xx only); empty days are 0.
        by_day = {p.date: p.value for p in summary.trend}
        assert by_day[_day(2)] == 1
        assert by_day[_day(1)] == 0
        assert by_day[_day(3)] == 0
        # Trend total matches the windowed 4xx+5xx sum.
        assert sum(p.value for p in summary.trend) == summary.counts4xx + summary.counts5xx


# ===========================================================================
# 3. Current-process route failures (Req 5.2)
# ===========================================================================


class TestTopRouteClasses:
    """Route failures are bounded current-process data, not durable totals."""

    async def test_route_failures_do_not_alter_durable_totals(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(0), REQUEST_4XX, 9)
        await store.upsert(_day(0), REQUEST_5XX, 4)
        admin_metrics = _FakeAdminMetrics(
            {"users_item": 3, "root": 7, "healthy": 0}
        )

        summary = await _service(store, admin_metrics).summary(30)

        assert admin_metrics.snapshot_calls == 1
        assert [route.model_dump() for route in summary.topRouteClasses] == [
            {"routeClass": "root", "failures": 7},
            {"routeClass": "users_item", "failures": 3},
        ]
        # Current-process route failures remain separate from durable day totals.
        assert summary.counts4xx == 9
        assert summary.counts5xx == 4
        assert summary.bySource.api == 13
        assert sum(point.value for point in summary.trend) == 13
        assert summary.notInstrumented == ["bySource.job", "bySource.storage"]


# ===========================================================================
# 4. Zero / empty store (Req 5.1 / 5.3 / 5.4)
# ===========================================================================


class TestZeroEmpty:
    """Validates: Requirements 5.1, 5.3, 5.4"""

    async def test_empty_store_yields_all_zero_summary(self, isolated_db):
        summary = await _service(_store(isolated_db)).summary(30)

        assert summary.counts4xx == 0
        assert summary.counts5xx == 0
        assert summary.bySource.api == 0
        assert summary.bySource.job == 0
        assert summary.bySource.storage == 0
        assert summary.bySource.ai == 0
        assert summary.topRouteClasses == []
        # One all-zero point per day for the whole window.
        assert len(summary.trend) == 30
        assert all(p.value == 0 for p in summary.trend)


# ===========================================================================
# 5. Secret-free serialization (Req 15.8 / Property 3)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_summary_serialization_has_no_forbidden_fields(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert(_day(0), REQUEST_4XX, 3)
        await store.upsert(_day(0), REQUEST_5XX, 1)
        await store.upsert(_day(0), AI_FAILURE, 2)

        summary = await _service(store).summary(30)
        assert isinstance(summary, ErrorsSummary)
        # Raises if any key matches a forbidden substring.
        assert_no_forbidden_fields(summary.model_dump(by_alias=True))


# ===========================================================================
# 6. O(1) read (Req 5.7) - fixed store-read count regardless of data volume
# ===========================================================================


class TestO1Read:
    """Validates: Requirements 5.7"""

    async def _seed_days(self, store, n_days: int) -> None:
        for offset in range(n_days):
            await store.upsert(_day(offset), REQUEST_4XX, offset + 1)
            await store.upsert(_day(offset), REQUEST_5XX, offset + 1)
            await store.upsert(_day(offset), AI_FAILURE, offset + 1)

    async def test_read_count_is_bounded_and_independent_of_data_volume(self, isolated_db):
        # Few rows.
        small = _CountingStore(_store(isolated_db))
        await self._seed_days(small, 5)
        await _service(small).summary(90)

        # Many rows (18x more).
        big = _CountingStore(_store(isolated_db))
        await self._seed_days(big, 90)
        await _service(big).summary(90)

        # Fixed shape: 3 sum reads (4xx, 5xx, AI_FAILURE) + 2 series reads (4xx, 5xx).
        assert small.sum_calls == 3
        assert small.series_calls == 2
        # Identical regardless of how much data was seeded - O(1) w.r.t. volume.
        assert big.sum_calls == small.sum_calls
        assert big.series_calls == small.series_calls
