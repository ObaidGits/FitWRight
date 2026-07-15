"""Unit tests for ``MetricsFlushStep`` — durable flush of in-process metrics (Task 4.3).

Covers the four Requirement-2 guarantees the step provides when it bridges the
ephemeral, cumulative-since-start in-process ``AdminMetrics`` request buckets
(``request_2xx`` / ``request_4xx`` / ``request_5xx``) into the durable per-(day,
key) ``metrics_daily`` rows via :class:`~app.admin.metric_store.MetricStore`:

- **Cross-worker summation (Req 2.4).** Two ``MetricsFlushStep`` instances
  ("workers") sharing ONE injected ``MetricStore`` each add only their own delta;
  the store holds a single summed value per ``(day, key)`` with no per-worker rows.
- **Idempotent re-run (Req 2.6).** Re-running with unchanged counters adds a
  zero delta, so the stored value and the single row are stable (no double-count).
- **Restart preserves totals (Req 2.3).** A fresh step instance (in-process
  baseline reset, counters back to 0 as after a restart) re-seeds its baseline and
  never reduces the durable value; new activity then accumulates on top.
- **Per-key failure isolation (Req 2.5, supporting 15.8).** A store whose ``add``
  raises for one key still flushes the other keys, leaves the failed key's value
  unchanged, returns a failed ``StepResult`` naming the key, and does not advance
  that key's baseline (so a later healthy run retries the delta).

The step reads its source counters via ``app.admin.metrics.get_admin_metrics``
(imported inside ``run``), and its store via the injected ``metric_store``. Tests
control the counters by patching ``get_admin_metrics`` to return a small fake
exposing ``.snapshot() -> {"counters": {...}}``, and inject an isolated
``MetricStore`` (DB-backed) or a raising fake store (failure isolation).

Requirements: 2.3, 2.4, 2.6, 15.8.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

import pytest

import app.admin.metrics as metrics_mod
from app.admin.metric_registry import REQUEST_2XX, REQUEST_4XX, REQUEST_5XX
from app.admin.metric_store import MetricStore
from app.admin.metrics_service import MetricsFlushStep
from app.admin.rollup_pipeline import StepResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _today() -> str:
    """The current UTC ``YYYY-MM-DD`` day the step always targets."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _FakeAdminMetrics:
    """Minimal ``AdminMetrics`` stand-in exposing a controllable snapshot.

    The step reads ``get_admin_metrics().snapshot()["counters"]`` — this fake lets
    each simulated worker present its own cumulative-since-start counters.
    """

    def __init__(self, **counters: int) -> None:
        self._counters: dict[str, int] = {k: int(v) for k, v in counters.items()}

    def set(self, **counters: int) -> None:
        self._counters.update({k: int(v) for k, v in counters.items()})

    def snapshot(self) -> dict:
        return {"counters": dict(self._counters)}


class _FakeKV:
    """In-memory KVStore stand-in so the best-effort marker write has a sink."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, ttl_seconds=None) -> None:
        self.data[key] = value


class _FailingStore:
    """A ``MetricStore``-shaped fake whose ``add`` raises for configured keys.

    Backs the per-key failure-isolation test: ``add`` succeeds for healthy keys
    (recording into an in-memory ``(day, key) -> value`` map) and raises for any
    key in ``fail_keys``. ``fail_keys`` can be cleared to simulate the store
    recovering for a subsequent retry run.
    """

    def __init__(self, *fail_keys: str) -> None:
        self.values: dict[tuple[str, str], int] = defaultdict(int)
        self.fail_keys: set[str] = set(fail_keys)
        self.snapshots: dict[str, dict] = {}

    async def add(self, day: str, key: str, delta: int) -> None:
        if key in self.fail_keys:
            raise RuntimeError(f"add failed for {key}")
        self.values[(day, key)] += int(delta)

    async def snapshot_put(self, name: str, payload: dict, *, ttl_seconds=None) -> None:
        self.snapshots[name] = payload


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore on the isolated engine (with an in-memory KV)."""
    return MetricStore(isolated_db.session_factory, kvstore=_FakeKV())


def _use_metrics(monkeypatch, fake: _FakeAdminMetrics) -> None:
    """Point the step's ``get_admin_metrics`` at ``fake`` (per-run swappable)."""
    monkeypatch.setattr(metrics_mod, "get_admin_metrics", lambda: fake)


async def _row_count(session_factory, day: str, key: str) -> int:
    """Number of ``metrics_daily`` rows for a single ``(day, key)``."""
    from sqlalchemy import func, select

    from app.models import MetricsDaily

    async with session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MetricsDaily)
                    .where(MetricsDaily.day_utc == day, MetricsDaily.metric == key)
                )
            ).scalar()
        )


# A closed-day arg the pipeline would pass; the step ignores it and targets today.
_CLOSED_DAY_ARG = "2020-01-01"


# ===========================================================================
# Cross-worker summation, no per-worker rows (Req 2.4)
# ===========================================================================


class TestCrossWorkerSummation:
    """Validates: Requirements 2.4"""

    async def test_two_workers_sum_into_single_row_per_key(self, isolated_db, monkeypatch):
        """Two workers sharing one store add only their own delta; the store holds
        one summed value per ``(day, key)`` and no per-worker rows."""
        store = _store(isolated_db)
        today = _today()

        # Two independent workers, each with its own cumulative counters + its own
        # per-process flush baseline, but the SAME durable store.
        worker_a = MetricsFlushStep(metric_store=store)
        worker_b = MetricsFlushStep(metric_store=store)

        metrics_a = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)
        metrics_b = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)

        # First run per worker seeds each baseline to the current cumulative
        # (0 here) and adds nothing for the crossing.
        _use_metrics(monkeypatch, metrics_a)
        assert (await worker_a.run(_CLOSED_DAY_ARG)).ok is True
        _use_metrics(monkeypatch, metrics_b)
        assert (await worker_b.run(_CLOSED_DAY_ARG)).ok is True

        # Each worker accumulates its own new activity across the day.
        metrics_a.set(request_2xx=10, request_4xx=5, request_5xx=2)
        metrics_b.set(request_2xx=7, request_4xx=3, request_5xx=1)

        _use_metrics(monkeypatch, metrics_a)
        assert (await worker_a.run(_CLOSED_DAY_ARG)).ok is True
        _use_metrics(monkeypatch, metrics_b)
        assert (await worker_b.run(_CLOSED_DAY_ARG)).ok is True

        # Durable store holds ONE summed value per (day, key) = A + B.
        assert await store.sum([REQUEST_2XX], today, today) == 17  # 10 + 7
        assert await store.sum([REQUEST_4XX], today, today) == 8  # 5 + 3
        assert await store.sum([REQUEST_5XX], today, today) == 3  # 2 + 1

        # No per-worker / per-event rows: exactly one row per (day, key).
        for key in (REQUEST_2XX, REQUEST_4XX, REQUEST_5XX):
            assert await _row_count(isolated_db.session_factory, today, key) == 1


# ===========================================================================
# Idempotent re-run — no double-count (Req 2.6)
# ===========================================================================


class TestIdempotentRerun:
    """Validates: Requirements 2.6"""

    async def test_rerun_with_unchanged_counters_adds_nothing(self, isolated_db, monkeypatch):
        """A second run without new activity is a zero-delta no-op: the stored
        value and its single row are unchanged."""
        store = _store(isolated_db)
        today = _today()
        step = MetricsFlushStep(metric_store=store)
        metrics = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)
        _use_metrics(monkeypatch, metrics)

        # Seed baseline, then accumulate + flush a delta for today.
        assert (await step.run(_CLOSED_DAY_ARG)).ok is True
        metrics.set(request_2xx=12)
        assert (await step.run(_CLOSED_DAY_ARG)).ok is True
        assert await store.sum([REQUEST_2XX], today, today) == 12

        # Re-run with the counters unchanged → delta 0 → nothing added.
        assert (await step.run(_CLOSED_DAY_ARG)).ok is True
        assert await store.sum([REQUEST_2XX], today, today) == 12
        assert await _row_count(isolated_db.session_factory, today, REQUEST_2XX) == 1

        # A third re-run is still stable (idempotent).
        assert (await step.run(_CLOSED_DAY_ARG)).ok is True
        assert await store.sum([REQUEST_2XX], today, today) == 12
        assert await _row_count(isolated_db.session_factory, today, REQUEST_2XX) == 1


# ===========================================================================
# Restart preserves totals (Req 2.3)
# ===========================================================================


class TestRestartPreservesTotals:
    """Validates: Requirements 2.3"""

    async def test_new_instance_after_restart_does_not_reduce_persisted_value(
        self, isolated_db, monkeypatch
    ):
        """A fresh step (in-process baseline reset, counters back to 0 as after a
        restart) re-seeds its baseline and never reduces the durable value; new
        activity then accumulates on top of the preserved total."""
        store = _store(isolated_db)
        today = _today()

        # --- Pre-restart worker flushes a delta V. ---
        pre = MetricsFlushStep(metric_store=store)
        pre_metrics = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)
        _use_metrics(monkeypatch, pre_metrics)
        assert (await pre.run(_CLOSED_DAY_ARG)).ok is True
        pre_metrics.set(request_2xx=20, request_5xx=4)
        assert (await pre.run(_CLOSED_DAY_ARG)).ok is True

        v_2xx = await store.sum([REQUEST_2XX], today, today)
        v_5xx = await store.sum([REQUEST_5XX], today, today)
        assert (v_2xx, v_5xx) == (20, 4)

        # --- Restart: NEW instance (fresh baseline), counters reset to 0. ---
        restarted = MetricsFlushStep(metric_store=store)
        post_metrics = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)
        _use_metrics(monkeypatch, post_metrics)

        # First run re-seeds the baseline (adds nothing) and must NOT reduce V.
        assert (await restarted.run(_CLOSED_DAY_ARG)).ok is True
        assert await store.sum([REQUEST_2XX], today, today) == v_2xx
        assert await store.sum([REQUEST_5XX], today, today) == v_5xx
        assert await _row_count(isolated_db.session_factory, today, REQUEST_2XX) == 1

        # New post-restart activity accumulates on top of the preserved total.
        post_metrics.set(request_2xx=6)
        assert (await restarted.run(_CLOSED_DAY_ARG)).ok is True
        assert await store.sum([REQUEST_2XX], today, today) == v_2xx + 6
        assert await store.sum([REQUEST_5XX], today, today) == v_5xx


# ===========================================================================
# Per-key failure isolation (Req 2.5 — supports 15.8)
# ===========================================================================


class TestPerKeyFailureIsolation:
    """Validates: Requirements 2.5"""

    async def test_one_failing_key_isolated_others_flushed_and_retried(self, monkeypatch):
        """A store that fails ``add`` for one key still flushes the others, leaves
        the failed key's value unchanged, returns a failed StepResult naming it,
        and does not advance that key's baseline (so a later healthy run retries)."""
        store = _FailingStore(REQUEST_4XX)
        today = _today()
        step = MetricsFlushStep(metric_store=store)
        metrics = _FakeAdminMetrics(request_2xx=0, request_4xx=0, request_5xx=0)
        _use_metrics(monkeypatch, metrics)

        # Seed baseline, then accumulate nonzero deltas on all three keys.
        assert (await step.run(_CLOSED_DAY_ARG)).ok is True
        metrics.set(request_2xx=10, request_4xx=5, request_5xx=2)

        result = await step.run(_CLOSED_DAY_ARG)

        # The good keys are persisted; the failing key was never written.
        assert store.values[(today, REQUEST_2XX)] == 10
        assert store.values[(today, REQUEST_5XX)] == 2
        assert (today, REQUEST_4XX) not in store.values

        # The step surfaces an observable failure naming the failed key.
        assert result.ok is False
        assert result.name == step.name
        assert REQUEST_4XX in (result.error or "")

        # Recover the store; the failed key's baseline was left unadvanced, so its
        # delta retries. The healthy keys' baselines advanced (delta 0 now).
        store.fail_keys.clear()
        retry = await step.run(_CLOSED_DAY_ARG)

        assert retry.ok is True
        assert store.values[(today, REQUEST_4XX)] == 5  # retried delta flushed
        assert store.values[(today, REQUEST_2XX)] == 10  # unchanged (no re-add)
        assert store.values[(today, REQUEST_5XX)] == 2  # unchanged (no re-add)
