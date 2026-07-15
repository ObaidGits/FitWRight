"""Unit tests for the ``FeatureUsageService`` (Task 16.4).

Covers the Requirement-16 guarantees of the daily per-feature aggregate read
model (see :mod:`app.analytics.feature_usage`), all served from durable
``metrics_daily`` FEAT_* keys via an injected
:class:`~app.admin.metric_store.MetricStore`:

- **Increment-only aggregation (Req 16.1).** The service reads only daily
  aggregate ``(day, value)`` rows produced by ``MetricStore.add`` — never a
  per-user or per-invocation row. Seeding an increment shows up in the feature
  total; there is no user identity anywhere in the shape.
- **Window validation (Req 16.3).** ``series(window)`` accepts only {7, 30, 90};
  any other value raises ``ValueError`` (surfaced by the endpoint as a 400
  ``invalid_window``).
- **O(1) read (Req 16.5).** A single ``series()`` issues a fixed, bounded number
  of store reads (exactly 8 — one ``series`` call per feature key) regardless of
  how many days/rows are seeded.
- **Aggregate-only / no user data (Req 16.6).** The response model exposes only
  ``window`` / ``series`` / ``computedAt`` (and per-feature ``feature`` /
  ``points`` / ``total``) — no user id, funnel, cohort, retention, or session
  field. ``extra="forbid"`` makes any stray field a hard error.
- **Secret-free (Req 15.8).** The serialized ``FeatureUsage`` passes the
  response-boundary forbidden-field guard.

Requirements: 16.1, 16.3, 16.5, 16.6, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

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
from app.admin.metric_store import MetricStore
from app.admin.schemas import FeatureUsage, assert_no_forbidden_fields
from app.analytics.feature_usage import (
    FeatureUsageService,
    get_feature_usage_service,
    reset_feature_usage_service,
)

pytestmark = pytest.mark.unit


# The closed set of 8 feature keys, mirroring the service's private tuple.
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


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore on the isolated engine (feature reads only)."""
    return MetricStore(isolated_db.session_factory)


def _service(store) -> FeatureUsageService:
    """A ``FeatureUsageService`` reading from the injected store."""
    return FeatureUsageService(metric_store=store)


def _day(offset: int = 0) -> str:
    """The UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


class _CountingStore:
    """A ``MetricStore``-shaped wrapper that counts ``series`` / ``sum`` calls.

    Delegates every read to the wrapped real store so the returned data is real,
    while tallying how many bounded reads a single ``series()`` issues — the proof
    of O(1)-w.r.t.-data-volume (Req 16.5). ``add`` / ``upsert`` are delegated so
    tests can seed increments through the wrapper.
    """

    def __init__(self, inner: MetricStore) -> None:
        self._inner = inner
        self.series_calls = 0
        self.sum_calls = 0

    async def upsert(self, day: str, key: str, value: int) -> None:
        await self._inner.upsert(day, key, value)

    async def add(self, day: str, key: str, delta: int) -> None:
        await self._inner.add(day, key, delta)

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        self.sum_calls += 1
        return await self._inner.sum(keys, day_from, day_to)

    async def series(self, key: str, days: int):
        self.series_calls += 1
        return await self._inner.series(key, days)


# ===========================================================================
# 1. Shape: all 8 features, each a zero-filled series of `window` points
# ===========================================================================


class TestShape:
    """Validates: Requirements 16.6"""

    async def test_series_returns_all_eight_features_zero_filled(self, isolated_db):
        for window in (7, 30, 90):
            usage = await _service(_store(isolated_db)).series(window)

            assert isinstance(usage, FeatureUsage)
            assert usage.window == window
            # Exactly the 8 tracked features, in registry order.
            assert [fs.feature for fs in usage.series] == list(_FEATURE_KEYS)
            for fs in usage.series:
                # Zero-filled: exactly `window` points, all zero, oldest→newest.
                assert len(fs.points) == window
                assert all(p.value == 0 for p in fs.points)
                assert fs.total == 0
                dates = [p.date for p in fs.points]
                assert dates == sorted(dates)
                assert dates[-1] == _day(0)  # last point is today


# ===========================================================================
# 2. Increment-only aggregation (Req 16.1) — add shows up in the total
# ===========================================================================


class TestIncrementAggregation:
    """Validates: Requirements 16.1"""

    async def test_increment_via_add_shows_up_in_series_total(self, isolated_db):
        store = _store(isolated_db)
        # Two increments on the same (day, key) accumulate into one aggregate row.
        await store.add(_day(1), FEAT_BUILDER, 3)
        await store.add(_day(1), FEAT_BUILDER, 2)
        # A different feature on a different day.
        await store.add(_day(5), FEAT_TAILOR, 7)

        usage = await _service(store).series(30)
        by_feature = {fs.feature: fs for fs in usage.series}

        # Builder total = 3 + 2 (increment-only accumulation), placed on _day(1).
        assert by_feature[FEAT_BUILDER].total == 5
        builder_points = {p.date: p.value for p in by_feature[FEAT_BUILDER].points}
        assert builder_points[_day(1)] == 5
        assert builder_points[_day(0)] == 0

        # Tailor total = 7 on _day(5); untouched features stay at zero.
        assert by_feature[FEAT_TAILOR].total == 7
        assert by_feature[FEAT_PARSER].total == 0

    async def test_out_of_window_increment_excluded(self, isolated_db):
        store = _store(isolated_db)
        await store.add(_day(2), FEAT_PARSER, 4)  # inside a 7-day window
        await store.add(_day(40), FEAT_PARSER, 99)  # outside a 7-day window

        usage = await _service(store).series(7)
        parser = next(fs for fs in usage.series if fs.feature == FEAT_PARSER)

        # Only the in-window increment is counted; the 40-day row is excluded.
        assert parser.total == 4
        assert len(parser.points) == 7


# ===========================================================================
# 3. Window validation (Req 16.3) — {7,30,90} only, else ValueError
# ===========================================================================


class TestWindowValidation:
    """Validates: Requirements 16.3"""

    @pytest.mark.parametrize("window", [7, 30, 90])
    async def test_valid_windows_accepted(self, isolated_db, window):
        usage = await _service(_store(isolated_db)).series(window)
        assert usage.window == window
        assert all(len(fs.points) == window for fs in usage.series)

    @pytest.mark.parametrize("window", [0, 1, 6, 8, 45, 60, 91, 365, -7])
    async def test_invalid_window_raises_value_error(self, isolated_db, window):
        with pytest.raises(ValueError):
            await _service(_store(isolated_db)).series(window)


# ===========================================================================
# 4. O(1) read (Req 16.5) — fixed store-read count regardless of data volume
# ===========================================================================


class TestO1Read:
    """Validates: Requirements 16.5"""

    async def _seed_days(self, store, n_days: int) -> None:
        for offset in range(n_days):
            for key in _FEATURE_KEYS:
                await store.add(_day(offset), key, offset + 1)

    async def test_read_count_is_bounded_and_independent_of_data_volume(self, isolated_db):
        # Few rows.
        small = _CountingStore(_store(isolated_db))
        await self._seed_days(small, 5)
        await _service(small).series(90)

        # Many rows (18x more days, across all 8 keys).
        big = _CountingStore(_store(isolated_db))
        await self._seed_days(big, 90)
        await _service(big).series(90)

        # Fixed shape: exactly one series read per feature key (8), zero sum reads.
        assert small.series_calls == len(_FEATURE_KEYS) == 8
        assert small.sum_calls == 0
        # Identical regardless of how much data was seeded — O(1) w.r.t. volume.
        assert big.series_calls == small.series_calls
        assert big.sum_calls == small.sum_calls


# ===========================================================================
# 5. Secret-free serialization (Req 15.8 / Property 3)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_series_serialization_has_no_forbidden_fields(self, isolated_db):
        store = _store(isolated_db)
        await store.add(_day(0), FEAT_BUILDER, 3)
        await store.add(_day(1), FEAT_COVER_LETTER, 1)

        usage = await _service(store).series(30)
        assert isinstance(usage, FeatureUsage)
        # Raises if any key matches a forbidden substring.
        assert_no_forbidden_fields(usage.model_dump(by_alias=True))


# ===========================================================================
# 6. No user data (Req 16.6) — structural assertion on the serialized shape
# ===========================================================================


class TestNoUserData:
    """Validates: Requirements 16.6"""

    async def test_response_shape_has_only_aggregate_fields(self, isolated_db):
        store = _store(isolated_db)
        await store.add(_day(0), FEAT_TAILOR, 4)

        usage = await _service(store).series(7)
        dumped = usage.model_dump(by_alias=True)

        # Top-level: exactly the aggregate fields — no user/funnel/cohort/session.
        assert set(dumped.keys()) == {"window", "series", "computedAt"}
        # Per-feature: exactly feature/points/total.
        for fs in dumped["series"]:
            assert set(fs.keys()) == {"feature", "points", "total"}
            for point in fs["points"]:
                assert set(point.keys()) == {"date", "value"}

        # Defense-in-depth: no per-user / funnel / cohort / retention / session
        # substring anywhere in the serialized key set.
        forbidden_markers = (
            "user",
            "userid",
            "funnel",
            "cohort",
            "retention",
            "session",
            "email",
        )
        _assert_no_substrings(dumped, forbidden_markers)


def _assert_no_substrings(payload, markers) -> None:
    """Recursively assert no dict key contains any of ``markers`` (casefolded)."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).casefold()
            for marker in markers:
                assert marker not in lowered, f"user-level field leaked: {key}"
            _assert_no_substrings(value, markers)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _assert_no_substrings(item, markers)


# ===========================================================================
# 7. Singleton management (defensive — get/reset consistency)
# ===========================================================================


class TestSingleton:
    """Validates: service wiring (get/reset)"""

    def test_get_returns_singleton_and_reset_clears(self):
        reset_feature_usage_service()
        first = get_feature_usage_service()
        second = get_feature_usage_service()
        assert first is second
        reset_feature_usage_service()
        assert get_feature_usage_service() is not first
        reset_feature_usage_service()
