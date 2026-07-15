"""Unit tests for the ``ResumeMetricsService`` (Task 17.4).

Covers the Requirement-14 guarantees of the resume-analytics read model
(see :mod:`app.analytics.resume_metrics`), served from a pre-computed
``"resume_snapshot"`` KV blob (source split + popular templates) plus the
zero-filled daily growth series from the ``RESUMES_*`` durable keys, all via an
injected :class:`~app.admin.metric_store.MetricStore`-shaped store:

- **Split math (Req 14.1).** ``sourceSplit`` echoes the snapshot counts and adds
  percentages = ``count / total * 100`` rounded to 1 decimal (total =
  generated + imported + tailored + deleted).
- **Empty state (Req 14.1).** No snapshot → all counts 0, all percentages 0.0,
  empty ``topTemplates``, and a zero-filled growth series.
- **top-N ordering (Req 14.2).** ``topTemplates`` is the snapshot's popular
  templates capped at 10, sorted descending by count with ties broken by name
  ascending.
- **Window validation (Req 14.4).** ``analytics(45)`` raises ``ValueError``;
  7 / 30 / 90 are accepted.
- **Scope-limited fields (Req 14.7).** The serialized ``ResumeAnalytics`` exposes
  ONLY window / sourceSplit / topTemplates / growth / computedAt — no
  funnel / cohort / retention keys — and passes the forbidden-field guard.
- **O(1) read (Req 14.6).** ``analytics`` issues a fixed, bounded number of store
  reads (1 ``snapshot_get`` + 3 ``series``) regardless of data volume.
- **Growth (Req 14.3).** The growth series sums generated + imported + tailored
  per day, excludes deleted, and is zero-filled across the window.

Requirements: 14.1, 14.4, 14.7, 15.8 (also exercises 14.2, 14.3, 14.6).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.metric_registry import (
    RESUMES_DELETED,
    RESUMES_GENERATED,
    RESUMES_IMPORTED,
    RESUMES_TAILORED,
)
from app.admin.schemas import ResumeAnalytics, assert_no_forbidden_fields
from app.analytics.resume_metrics import ResumeMetricsService

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _day(offset: int = 0) -> str:
    """The UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _trailing_days(days: int) -> list[str]:
    """Trailing ``days`` UTC ``YYYY-MM-DD`` strings, oldest→newest (mirrors store)."""
    now = datetime.now(timezone.utc)
    n = max(0, int(days))
    return [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]


class _FakeStore:
    """A ``MetricStore``-shaped fake that serves an in-memory snapshot + series.

    - ``snapshot_get`` returns the seeded snapshot dict (or ``None``).
    - ``series`` zero-fills the trailing window exactly like the real store, so
      the service's growth zero-fill is exercised faithfully.

    It also tallies read calls so a single ``analytics`` call can be proven to
    issue a bounded, data-volume-independent number of reads (Req 14.6).
    """

    def __init__(self, *, snapshot=None, series_data=None) -> None:
        self._snapshot = snapshot
        # key -> {day: value}
        self._series = series_data or {}
        self.snapshot_get_calls = 0
        self.series_calls = 0

    async def snapshot_get(self, name: str):
        self.snapshot_get_calls += 1
        return self._snapshot

    async def series(self, key: str, days: int):
        self.series_calls += 1
        stored = self._series.get(key, {})
        return [(day, int(stored.get(day, 0))) for day in _trailing_days(days)]


def _service(store) -> ResumeMetricsService:
    """A ``ResumeMetricsService`` reading from the injected store."""
    return ResumeMetricsService(metric_store=store)


def _snapshot(*, source_counts=None, templates=None) -> dict:
    """Build a ``resume_snapshot`` payload like ``ResumeSnapshotStep`` writes."""
    return {
        "sourceCounts": source_counts or {},
        "popularTemplates": templates or [],
        "sampledAt": "2024-01-01T00:00:00+00:00",
    }


# ===========================================================================
# 1. Split math (Req 14.1) — counts echoed, percentages 1-decimal of total
# ===========================================================================


class TestSplitMath:
    """Validates: Requirements 14.1"""

    async def test_counts_and_percentages(self):
        # total = 40 + 30 + 20 + 10 = 100 → percentages are exact tenths.
        snap = _snapshot(
            source_counts={
                "generated": 40,
                "imported": 30,
                "tailored": 20,
                "deleted": 10,
            }
        )
        result = await _service(_FakeStore(snapshot=snap)).analytics(30)
        split = result.sourceSplit

        # Counts echoed verbatim.
        assert split.generated == 40
        assert split.imported == 30
        assert split.tailored == 20
        assert split.deleted == 10

        # Percentages = count / total * 100, rounded to 1 decimal.
        assert split.generatedPct == 40.0
        assert split.importedPct == 30.0
        assert split.tailoredPct == 20.0
        assert split.deletedPct == 10.0

        # Percentages are within [0.0, 100.0] and sum sensibly to ~100.
        for p in (split.generatedPct, split.importedPct, split.tailoredPct, split.deletedPct):
            assert 0.0 <= p <= 100.0
        assert round(
            split.generatedPct + split.importedPct + split.tailoredPct + split.deletedPct, 1
        ) == 100.0

    async def test_percentages_rounded_to_one_decimal(self):
        # total = 3 → 1/3 = 33.333.. rounds to 33.3 (one decimal place).
        snap = _snapshot(
            source_counts={"generated": 1, "imported": 1, "tailored": 1, "deleted": 0}
        )
        split = (await _service(_FakeStore(snapshot=snap)).analytics(30)).sourceSplit

        assert split.generatedPct == 33.3
        assert split.importedPct == 33.3
        assert split.tailoredPct == 33.3
        assert split.deletedPct == 0.0
        # Each percentage carries at most one decimal place.
        for p in (split.generatedPct, split.importedPct, split.tailoredPct):
            assert round(p, 1) == p


# ===========================================================================
# 2. Empty state (Req 14.1) — no snapshot → zeros, empty templates, zero growth
# ===========================================================================


class TestEmptyState:
    """Validates: Requirements 14.1"""

    async def test_no_snapshot_yields_all_zero(self):
        result = await _service(_FakeStore(snapshot=None)).analytics(30)
        split = result.sourceSplit

        assert split.generated == 0
        assert split.imported == 0
        assert split.tailored == 0
        assert split.deleted == 0
        # Percentages are 0.0 (not NaN / division-by-zero) when the total is 0.
        assert split.generatedPct == 0.0
        assert split.importedPct == 0.0
        assert split.tailoredPct == 0.0
        assert split.deletedPct == 0.0

        assert result.topTemplates == []

        # Growth is still a full zero-filled window (one point per day).
        assert len(result.growth) == 30
        assert all(p.value == 0 for p in result.growth)


# ===========================================================================
# 3. top-N ordering (Req 14.2) — cap 10, desc by count, ties → name asc
# ===========================================================================


class TestTopTemplateOrdering:
    """Validates: Requirements 14.2"""

    async def test_top_ten_ordering_and_tie_break(self):
        # 13 templates with a deliberate tie at count=5 ("alpha" vs "beta")
        # placed at ranks 9 & 10 so both make the top-10 cut; three templates
        # with counts below 5 fall outside it.
        raw = [
            {"template": "t-100", "count": 100},
            {"template": "t-90", "count": 90},
            {"template": "t-80", "count": 80},
            {"template": "t-70", "count": 70},
            {"template": "t-60", "count": 60},
            {"template": "beta", "count": 5},    # tie with "alpha" (rank 9/10)
            {"template": "alpha", "count": 5},   # tie with "beta" (rank 9/10)
            {"template": "t-50", "count": 50},
            {"template": "t-40", "count": 40},
            {"template": "t-30", "count": 30},
            {"template": "t-4", "count": 4},     # 11th by count → excluded
            {"template": "t-3", "count": 3},     # excluded
            {"template": "t-2", "count": 2},     # excluded
        ]
        # Pre-sorted exactly as ResumeSnapshotStep persists it (desc count, name asc).
        ordered = sorted(raw, key=lambda t: (-t["count"], t["template"]))
        snap = _snapshot(templates=ordered)

        top = (await _service(_FakeStore(snapshot=snap)).analytics(30)).topTemplates

        # Capped at 10.
        assert len(top) == 10

        # Descending by count.
        counts = [t.count for t in top]
        assert counts == sorted(counts, reverse=True)

        # Tie at count=5 broken by name ascending: "alpha" precedes "beta".
        names_at_5 = [t.name for t in top if t.count == 5]
        assert names_at_5 == ["alpha", "beta"]

        # The three lowest-count templates fell outside the top 10.
        returned_names = {t.name for t in top}
        assert "t-4" not in returned_names
        assert "t-3" not in returned_names
        assert "t-2" not in returned_names

    async def test_empty_template_list(self):
        snap = _snapshot(templates=[])
        top = (await _service(_FakeStore(snapshot=snap)).analytics(7)).topTemplates
        assert top == []


# ===========================================================================
# 4. Window validation (Req 14.4) — invalid → ValueError; 7/30/90 accepted
# ===========================================================================


class TestWindowValidation:
    """Validates: Requirements 14.4"""

    async def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            await _service(_FakeStore()).analytics(45)

    @pytest.mark.parametrize("window", [7, 30, 90])
    async def test_valid_windows_accepted(self, window):
        result = await _service(_FakeStore()).analytics(window)
        assert result.window == window
        # Growth window length matches the requested window.
        assert len(result.growth) == window


# ===========================================================================
# 5. Scope-limited fields (Req 14.7 / 15.8) — enumerated metrics only
# ===========================================================================


class TestScopeLimitedFields:
    """Validates: Requirements 14.7, 15.8"""

    async def test_only_enumerated_fields_and_secret_free(self):
        snap = _snapshot(
            source_counts={"generated": 2, "imported": 1, "tailored": 1, "deleted": 0},
            templates=[{"template": "modern", "count": 3}],
        )
        result = await _service(_FakeStore(snapshot=snap)).analytics(30)
        assert isinstance(result, ResumeAnalytics)

        body = result.model_dump(by_alias=True)

        # Exactly the enumerated top-level metrics — nothing else.
        assert set(body.keys()) == {
            "window",
            "sourceSplit",
            "topTemplates",
            "growth",
            "computedAt",
        }

        # No funnel / retention / cohort analytics leak in (Req 14.7).
        serialized = str(body).casefold()
        for forbidden in ("funnel", "retention", "cohort"):
            assert forbidden not in serialized

        # Response-boundary forbidden-field guard (Req 15.8).
        assert_no_forbidden_fields(body)


# ===========================================================================
# 6. O(1) read (Req 14.6) — fixed store-read count regardless of data volume
# ===========================================================================


class TestO1Read:
    """Validates: Requirements 14.6"""

    def _series_for(self, n_days: int) -> dict:
        """Seed all three growth keys with a value on each of ``n_days`` days."""
        data: dict = {}
        for key in (RESUMES_GENERATED, RESUMES_IMPORTED, RESUMES_TAILORED):
            data[key] = {_day(offset): offset + 1 for offset in range(n_days)}
        return data

    async def test_read_count_bounded_and_independent_of_volume(self):
        small = _FakeStore(
            snapshot=_snapshot(source_counts={"generated": 1}),
            series_data=self._series_for(5),
        )
        await _service(small).analytics(90)

        big = _FakeStore(
            snapshot=_snapshot(source_counts={"generated": 1}),
            series_data=self._series_for(90),
        )
        await _service(big).analytics(90)

        # Fixed shape: 1 snapshot read + 3 series reads (generated/imported/tailored).
        assert small.snapshot_get_calls == 1
        assert small.series_calls == 3
        # Identical regardless of how much data was seeded — O(1) w.r.t. volume.
        assert big.snapshot_get_calls == small.snapshot_get_calls
        assert big.series_calls == small.series_calls


# ===========================================================================
# 7. Growth (Req 14.3) — sum generated+imported+tailored, exclude deleted, zero-fill
# ===========================================================================


class TestGrowth:
    """Validates: Requirements 14.3"""

    async def test_growth_sums_creation_keys_excludes_deleted_and_zero_fills(self):
        series_data = {
            RESUMES_GENERATED: {_day(0): 5, _day(2): 1},
            RESUMES_IMPORTED: {_day(0): 2, _day(2): 3},
            RESUMES_TAILORED: {_day(0): 1},
            # Deletions must NOT count toward growth — seed a big value to prove it.
            RESUMES_DELETED: {_day(0): 999, _day(2): 999},
        }
        result = await _service(
            _FakeStore(snapshot=_snapshot(), series_data=series_data)
        ).analytics(7)

        # One point per day, oldest→newest.
        assert len(result.growth) == 7
        dates = [p.date for p in result.growth]
        assert dates == sorted(dates)

        by_day = {p.date: p.value for p in result.growth}
        # today: 5 + 2 + 1 = 8 (deleted 999 excluded).
        assert by_day[_day(0)] == 8
        # two days ago: 1 + 3 + 0 = 4.
        assert by_day[_day(2)] == 4
        # empty days zero-filled.
        assert by_day[_day(1)] == 0
        assert by_day[_day(3)] == 0

        # Total growth = sum of creation keys only (never deletions).
        assert sum(p.value for p in result.growth) == 8 + 4
