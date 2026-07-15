"""Unit tests for the Rollup_Pipeline orchestrator + MetricsPruneStep (Task 2.4).

Covers the pipeline coordination and metrics-retention step landed in tasks
2.1–2.3:

- ``app.admin.rollup_pipeline.run_rollup_pipeline`` — the coordinator that runs
  every :class:`RollupStep` in :data:`PIPELINE` in order, isolates a failing step
  (whether it raises or returns a failed :class:`StepResult`) so later steps still
  run, and returns one :class:`StepResult` per step (R2.5 failure isolation).
- ``app.admin.rollup_pipeline.MetricsPruneStep`` + ``MetricStore.prune_before`` —
  the retention primitive that deletes ``metrics_daily`` rows older than the
  configured window, keeps the boundary day, never touches the reserved
  ``_TOTALS_DAY`` sentinel, and is idempotent on re-run (R15.6).
- Idempotency per closed day at the Metric_Store level, exercised through the
  pipeline (R2.6): re-running never changes an already-written closed-day value.

Requirements: 2.6, 15.6, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app.admin.metric_store as metric_store_mod
import app.admin.rollup_pipeline as pipeline_mod
from app.admin.metric_store import MetricStore
from app.admin.metrics_service import _TOTALS_DAY
from app.admin.rollup_pipeline import (
    MetricsPruneStep,
    StepResult,
    run_rollup_pipeline,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _day(offset: int) -> str:
    """UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


class _RecordingStep:
    """A fake RollupStep that records it ran and returns success."""

    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self._order = order
        self.calls: list[str] = []

    async def run(self, day: str) -> StepResult:
        self._order.append(self.name)
        self.calls.append(day)
        return StepResult.success(self.name)


class _RaisingStep:
    """A fake RollupStep that raises — the orchestrator must isolate it."""

    def __init__(self, name: str, order: list[str], exc: Exception) -> None:
        self.name = name
        self._order = order
        self._exc = exc
        self.calls: list[str] = []

    async def run(self, day: str) -> StepResult:
        self._order.append(self.name)
        self.calls.append(day)
        raise self._exc


class _FailingResultStep:
    """A fake RollupStep that *returns* a failed StepResult (handled internally)."""

    def __init__(self, name: str, order: list[str], error: str) -> None:
        self.name = name
        self._order = order
        self._error = error
        self.calls: list[str] = []

    async def run(self, day: str) -> StepResult:
        self._order.append(self.name)
        self.calls.append(day)
        return StepResult.failure(self.name, self._error)


def _store(isolated_db) -> MetricStore:
    return MetricStore(isolated_db.session_factory)


async def _all_days(session_factory) -> set[str]:
    """Return the set of distinct ``day_utc`` values currently in metrics_daily."""
    from sqlalchemy import select

    from app.models import MetricsDaily

    async with session_factory() as session:
        rows = (await session.execute(select(MetricsDaily.day_utc))).scalars().all()
    return set(rows)


# ===========================================================================
# Pipeline orchestration — run_rollup_pipeline
# ===========================================================================


class TestPipelineRunsAllSteps:
    async def test_runs_all_steps_in_declared_order(self, monkeypatch):
        order: list[str] = []
        steps = [
            _RecordingStep("a", order),
            _RecordingStep("b", order),
            _RecordingStep("c", order),
        ]
        monkeypatch.setattr(pipeline_mod, "PIPELINE", steps)

        results = await run_rollup_pipeline(_day(1))

        # Every step's run() was called exactly once, in declared order.
        assert order == ["a", "b", "c"]
        for step in steps:
            assert step.calls == [_day(1)]
        # One StepResult per step, all successful, names preserved in order.
        assert [(r.name, r.ok) for r in results] == [("a", True), ("b", True), ("c", True)]

    async def test_returns_one_result_per_step(self, monkeypatch):
        order: list[str] = []
        steps = [_RecordingStep(f"s{i}", order) for i in range(5)]
        monkeypatch.setattr(pipeline_mod, "PIPELINE", steps)

        results = await run_rollup_pipeline(_day(1))

        assert len(results) == len(steps)


class TestPipelineIsolatesFailingStep:
    async def test_raising_step_is_caught_and_later_steps_still_run(self, monkeypatch):
        order: list[str] = []
        boom = RuntimeError("kaboom")
        steps = [
            _RecordingStep("before", order),
            _RaisingStep("raiser", order, boom),
            _RecordingStep("after", order),
        ]
        monkeypatch.setattr(pipeline_mod, "PIPELINE", steps)

        results = await run_rollup_pipeline(_day(1))

        # The raise did NOT abort the run — the later step still executed.
        assert order == ["before", "raiser", "after"]
        by_name = {r.name: r for r in results}
        assert by_name["before"].ok is True
        assert by_name["after"].ok is True
        # The raised exception is caught and recorded as an observable failure.
        assert by_name["raiser"].ok is False
        assert by_name["raiser"].error == "kaboom"

    async def test_returned_failure_result_does_not_abort_rest(self, monkeypatch):
        order: list[str] = []
        steps = [
            _RecordingStep("before", order),
            _FailingResultStep("softfail", order, "per-key partial failure"),
            _RecordingStep("after", order),
        ]
        monkeypatch.setattr(pipeline_mod, "PIPELINE", steps)

        results = await run_rollup_pipeline(_day(1))

        assert order == ["before", "softfail", "after"]
        by_name = {r.name: r for r in results}
        assert by_name["before"].ok is True
        assert by_name["after"].ok is True
        assert by_name["softfail"].ok is False
        assert by_name["softfail"].error == "per-key partial failure"

    async def test_first_step_failure_does_not_block_subsequent(self, monkeypatch):
        order: list[str] = []
        steps = [
            _RaisingStep("first", order, ValueError("bad")),
            _RecordingStep("second", order),
        ]
        monkeypatch.setattr(pipeline_mod, "PIPELINE", steps)

        results = await run_rollup_pipeline(_day(1))

        assert order == ["first", "second"]
        assert results[0].ok is False and results[0].name == "first"
        assert results[1].ok is True and results[1].name == "second"


# ===========================================================================
# MetricStore.prune_before — retention bounds (Req 15.6)
# ===========================================================================


class TestPruneBefore:
    async def test_deletes_rows_older_than_cutoff_keeps_newer(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert("2020-01-01", "k", 1)  # older → deleted
        await store.upsert("2020-05-31", "k", 2)  # older → deleted
        await store.upsert("2020-06-02", "k", 3)  # newer → kept
        await store.upsert("2020-12-31", "k", 4)  # newer → kept

        removed = await store.prune_before("2020-06-01")

        assert removed == 2
        assert await _all_days(isolated_db.session_factory) == {"2020-06-02", "2020-12-31"}

    async def test_cutoff_boundary_day_is_kept(self, isolated_db):
        """Delete is ``day_utc < cutoff`` — a row on the cutoff day survives."""
        store = _store(isolated_db)
        await store.upsert("2020-06-01", "k", 1)  # == cutoff → kept
        await store.upsert("2020-05-31", "k", 2)  # < cutoff → deleted

        removed = await store.prune_before("2020-06-01")

        assert removed == 1
        assert await _all_days(isolated_db.session_factory) == {"2020-06-01"}

    async def test_totals_sentinel_is_never_pruned(self, isolated_db):
        """The reserved ``_TOTALS_DAY`` row survives even a cutoff above every row."""
        store = _store(isolated_db)
        await store.upsert("2020-01-01", "k", 1)
        await store.upsert("2020-06-01", "k", 2)
        await store.upsert(_TOTALS_DAY, "totalUsers", 42)

        # A cutoff that lexicographically exceeds every row (including "_totals_",
        # which sorts after numeric dates) would otherwise delete everything.
        removed = await store.prune_before("~~~~", exclude_days=(_TOTALS_DAY,))

        remaining = await _all_days(isolated_db.session_factory)
        assert removed == 2
        assert remaining == {_TOTALS_DAY}

    async def test_exclude_days_spares_listed_days(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert("2020-01-01", "k", 1)  # excluded → kept
        await store.upsert("2020-02-01", "k", 2)  # < cutoff, not excluded → deleted

        removed = await store.prune_before("2020-06-01", exclude_days=("2020-01-01",))

        assert removed == 1
        assert await _all_days(isolated_db.session_factory) == {"2020-01-01"}

    async def test_idempotent_second_run_deletes_nothing(self, isolated_db):
        store = _store(isolated_db)
        await store.upsert("2020-01-01", "k", 1)
        await store.upsert("2020-12-31", "k", 2)

        first = await store.prune_before("2020-06-01")
        remaining_after_first = await _all_days(isolated_db.session_factory)
        second = await store.prune_before("2020-06-01")
        remaining_after_second = await _all_days(isolated_db.session_factory)

        assert first == 1
        assert second == 0  # nothing left to remove
        assert remaining_after_first == remaining_after_second == {"2020-12-31"}


# ===========================================================================
# MetricsPruneStep.run — cutoff computation + exclusion wiring (Req 15.6)
# ===========================================================================


class TestMetricsPruneStep:
    async def test_run_prunes_by_retention_window_and_keeps_totals(
        self, isolated_db, monkeypatch
    ):
        store = _store(isolated_db)
        # Older than the retention window → deleted.
        await store.upsert(_day(10), "k", 1)
        # On the cutoff boundary (today - retention) → kept (delete is strict <).
        await store.upsert(_day(5), "k", 2)
        # Inside the window → kept.
        await store.upsert(_day(3), "k", 3)
        # Reserved sentinel → never pruned.
        await store.upsert(_TOTALS_DAY, "totalUsers", 99)

        # Point the step at the isolated store + a controlled retention window.
        # The step lazily imports get_metric_store from app.admin.metric_store,
        # so the patch must land on that module.
        monkeypatch.setattr(metric_store_mod, "get_metric_store", lambda: store)
        from app.config import settings

        monkeypatch.setattr(settings, "admin_metrics_retention_days", 5)

        result = await MetricsPruneStep().run(_day(1))

        assert result == StepResult.success("metrics_prune")
        remaining = await _all_days(isolated_db.session_factory)
        assert remaining == {_day(5), _day(3), _TOTALS_DAY}

    async def test_run_is_idempotent(self, isolated_db, monkeypatch):
        store = _store(isolated_db)
        await store.upsert(_day(10), "k", 1)
        await store.upsert(_day(2), "k", 2)
        await store.upsert(_TOTALS_DAY, "totalUsers", 7)

        monkeypatch.setattr(metric_store_mod, "get_metric_store", lambda: store)
        from app.config import settings

        monkeypatch.setattr(settings, "admin_metrics_retention_days", 5)

        first = await MetricsPruneStep().run(_day(1))
        remaining_first = await _all_days(isolated_db.session_factory)
        second = await MetricsPruneStep().run(_day(1))
        remaining_second = await _all_days(isolated_db.session_factory)

        assert first.ok is True and second.ok is True
        assert remaining_first == remaining_second == {_day(2), _TOTALS_DAY}

    async def test_run_isolates_store_failure_as_failed_result(self, monkeypatch):
        """A store error is caught and surfaced as an observable failed StepResult."""

        class _BoomStore:
            async def prune_before(self, cutoff, *, exclude_days=()):
                raise RuntimeError("db down")

        monkeypatch.setattr(
            metric_store_mod, "get_metric_store", lambda: _BoomStore()
        )
        from app.config import settings

        monkeypatch.setattr(settings, "admin_metrics_retention_days", 5)

        result = await MetricsPruneStep().run(_day(1))

        assert result.ok is False
        assert result.name == "metrics_prune"
        assert "db down" in (result.error or "")


# ===========================================================================
# Idempotency per closed day, exercised through the pipeline (Req 2.6)
# ===========================================================================


class _UpsertClosedDayStep:
    """A pipeline step that UPSERTs a fixed closed-day value via Metric_Store.

    Stands in for the real closed-day flush/rollup steps to demonstrate the
    pipeline-level idempotency guarantee without coupling to MetricsService
    internals: re-running the pipeline for a closed day re-writes the same
    absolute value, so the stored value never changes (R2.6).
    """

    name = "upsert_closed_day"

    def __init__(self, store: MetricStore, day: str, key: str, value: int) -> None:
        self._store = store
        self._day = day
        self._key = key
        self._value = value

    async def run(self, day: str) -> StepResult:
        await self._store.upsert(self._day, self._key, self._value)
        return StepResult.success(self.name)


class TestPipelineIdempotentPerClosedDay:
    async def test_rerun_leaves_closed_day_value_unchanged(self, isolated_db, monkeypatch):
        store = _store(isolated_db)
        closed_day = _day(2)
        step = _UpsertClosedDayStep(store, closed_day, "signups", 17)
        monkeypatch.setattr(pipeline_mod, "PIPELINE", [step])

        # First run writes the closed-day value.
        await run_rollup_pipeline(closed_day)
        assert await store.sum(["signups"], closed_day, closed_day) == 17

        # Re-running the pipeline for the same closed day is a no-op change.
        results = await run_rollup_pipeline(closed_day)

        assert all(r.ok for r in results)
        assert await store.sum(["signups"], closed_day, closed_day) == 17
        # And it did not spawn a second row for the same (day, key).
        from sqlalchemy import func, select

        from app.models import MetricsDaily

        async with isolated_db.session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(MetricsDaily)
                    .where(
                        MetricsDaily.day_utc == closed_day,
                        MetricsDaily.metric == "signups",
                    )
                )
            ).scalar()
        assert int(count) == 1

    async def test_direct_metric_store_reupsert_is_noop(self, isolated_db):
        """Metric_Store idempotency primitive backing the closed-day guarantee."""
        store = _store(isolated_db)
        day = _day(2)
        await store.upsert(day, "resumes_tailored", 5)
        await store.upsert(day, "resumes_tailored", 5)  # re-run, same value
        assert await store.sum(["resumes_tailored"], day, day) == 5
