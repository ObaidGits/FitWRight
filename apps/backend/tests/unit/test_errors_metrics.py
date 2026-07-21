"""Unit tests for the ``ErrorsMetricsService`` (Task 10.3).

Covers the Requirement-5 guarantees of the grouped errors-summary read model
(see :mod:`app.admin.errors_metrics`), all served from durable ``metrics_daily``
Metric_Keys via an injected :class:`~app.admin.metric_store.MetricStore`:

- **Bucket math (Req 5.1/5.3).** ``counts4xx`` / ``counts5xx`` are the windowed
  sums of ``REQUEST_4XX`` / ``REQUEST_5XX``; ``bySource.api`` is their total;
  ``bySource.ai`` is the windowed ``AI_FAILURE`` sum; ``job`` / ``storage`` are
  0 (documented gaps); a day older than the window is excluded.
- **Trend (Req 5.4).** Exactly ``window`` daily points, oldest->newest, each equal
  to ``REQUEST_4XX + REQUEST_5XX`` for that day (0 for empty days); last is today.
- **top-N ordering (Req 5.2).** ``topRouteClasses`` is ``[]`` by design (no
  durable per-route-class failure key) - which satisfies "fewer than 10".
- **Zero/empty (Req 5.1/5.3/5.4).** An empty store yields all-zero counts, an
  all-zero by-source, an all-zero trend, and an empty route-class list.
- **Secret-free (Req 15.8).** The serialized summary passes the response-boundary
  forbidden-field guard.
- **O(1) read (Req 5.7).** ``summary`` issues a fixed, bounded number of store
  reads (3 ``sum`` + 2 ``series``) regardless of how many days/rows are seeded.

Requirements: 5.2, 5.5, 5.6, 5.7, 15.8.
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


def _service(store) -> ErrorsMetricsService:
    """An ``ErrorsMetricsService`` reading from the injected store."""
    return ErrorsMetricsService(metric_store=store)


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
        # Honesty (audit fix): the un-sourced fields are flagged not-instrumented
        # so the UI shows that instead of implying "zero failures".
        assert set(summary.notInstrumented) == {
            "topRouteClasses",
            "bySource.job",
            "bySource.storage",
        }
        assert summary.window == 30

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
# 3. top-N ordering (Req 5.2) - empty by design, satisfies "fewer than 10"
# ===========================================================================


class TestTopRouteClasses:
    """Validates: Requirements 5.2"""

    async def test_top_route_classes_is_empty_documented_contract(self, isolated_db):
        store = _store(isolated_db)
        # Even with request failures recorded, there is no durable per-route-class
        # FAILURE key, so the list is empty (Req 5.2 permits < 10 entries).
        await store.upsert(_day(0), REQUEST_4XX, 9)
        await store.upsert(_day(0), REQUEST_5XX, 9)

        summary = await _service(store).summary(30)

        assert summary.topRouteClasses == []
        assert len(summary.topRouteClasses) < 10  # satisfies the "fewer than 10" clause


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
