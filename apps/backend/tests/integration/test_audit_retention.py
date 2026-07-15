"""Integration tests for the Audit_Retention_Job (Task 3.5).

Exercises :func:`app.admin.jobs.run_audit_retention_job` and its two pruning
paths against an isolated temp database + a fresh in-process KVStore:

- tiering: Security_Critical rows past the hot window are deleted, in-window
  kept; Never_Dropped events survive regardless of age; Downsamplable rows past
  the downsample window are aggregated-then-deleted, in-window kept (Req 1.3/1.5);
- aggregate-then-delete ordering: the AUDIT_DOWNSAMPLED_* key is written at the
  correct day and the exact aggregated rows are deleted, per day (Req 1.4);
- aggregate-failure retention: a per-group aggregate failure retains that
  group's rows + leaves its key unchanged while other groups still process
  (Req 1.9);
- resume-after-interruption: repeated bounded runs drain the backlog with no
  double-count — the final key total equals the aged-row count exactly once
  (Req 1.6);
- shared per-invocation budget: downsampled + deleted <= batch (Req 1.7);
- single-flight lock: a held lock makes the job a no-op returning
  ``{"status": "locked"}`` (Req 1.1).

Requirements: 1.3, 1.4, 1.5, 1.6, 1.9, 15.8

DB / metric-store / kvstore wiring
----------------------------------
- ``auth_env`` (integration conftest) rebinds the process singletons to the
  isolated temp DB. The Audit_Retention_Job uses the process ``MetricStore``
  singleton (``get_metric_store()``), which lazily binds to ``app.database.db``
  the first time it is built — so the ``retention_env`` fixture calls
  :func:`reset_metric_store` after the DB is swapped, forcing the singleton to
  rebuild against the temp DB. Metric assertions read ``metrics_daily`` through
  the same isolated ``session_factory``.
- The job's single-flight lock uses the process ``KVStore`` from
  ``app.auth.runtime.get_kvstore`` (a fresh in-process store, since ``auth_env``
  resets the container). Tests pass that same store to the job and, for the lock
  test, hold the lock on it directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.admin.jobs import AUDIT_RETENTION_LOCK_KEY, run_audit_retention_job
from app.admin.metric_registry import (
    AUDIT_DOWNSAMPLED_USER_VIEWED,
    DownsamplableEvent,
    audit_downsample_key,
)
from app.auth.audit import AuditEvent
from app.config import settings as app_settings
from app.models import AuditLog, MetricsDaily

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def retention_env(auth_env):
    """Isolated DB with the process MetricStore singleton rebound to it.

    ``auth_env`` swaps ``app.database.db`` for the temp DB; resetting the
    MetricStore singleton here forces ``get_metric_store()`` (used inside the
    job) to rebuild against that temp DB rather than any leftover instance.
    """
    from app.admin.metric_store import reset_metric_store

    reset_metric_store()
    yield auth_env
    reset_metric_store()


@pytest.fixture
def kvstore():
    """The fresh in-process KVStore (container was reset by ``auth_env``)."""
    from app.auth.runtime import get_kvstore

    return get_kvstore()


def _ts_days_ago(days: float, *, second: int = 0) -> str:
    """An ISO UTC timestamp ``days`` in the past (anchored at noon for a stable
    calendar day; ``second`` lets callers spread rows within the same day)."""
    base = datetime.now(timezone.utc).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    return (base - timedelta(days=days) + timedelta(seconds=second)).isoformat()


async def _seed_audit(db, event: str, *, ts: str) -> str:
    """Insert one audit_log row with an explicit event + ts; return its id."""
    row = AuditLog(event=event, ts=ts, meta=None)
    async with db.session_factory() as session:
        session.add(row)
        await session.commit()
        return row.id


async def _count_event(db, event: str) -> int:
    async with db.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.event == event)
                )
            ).scalar()
            or 0
        )


async def _count_total(db) -> int:
    async with db.session_factory() as session:
        return int(
            (await session.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
        )


async def _count_event_on_day(db, event: str, day: str) -> int:
    async with db.session_factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.event == event, AuditLog.ts.like(f"{day}%"))
                )
            ).scalar()
            or 0
        )


async def _metric_value(db, day: str, key: str):
    async with db.session_factory() as session:
        return (
            await session.execute(
                select(MetricsDaily.value).where(
                    MetricsDaily.day_utc == day, MetricsDaily.metric == key
                )
            )
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# 1. Tiering (Req 1.3, 1.5)
# ---------------------------------------------------------------------------


class TestTiering:
    async def test_security_critical_aged_deleted_recent_kept(self, retention_env, kvstore):
        db = retention_env
        # LOGIN is Security_Critical (not Never_Dropped): past the 365d hot window
        # → deleted; inside the window → kept.
        await _seed_audit(db, AuditEvent.LOGIN, ts=_ts_days_ago(400))
        await _seed_audit(db, AuditEvent.LOGIN, ts=_ts_days_ago(10))

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["status"] == "ok"
        assert result["deleted"] == 1
        # Exactly the recent LOGIN remains.
        assert await _count_event(db, AuditEvent.LOGIN) == 1

    async def test_never_dropped_events_survive_regardless_of_age(self, retention_env, kvstore):
        db = retention_env
        # The three Never_Dropped events are also Security_Critical, but excluded
        # via set subtraction — they must survive even far past the hot window.
        never_dropped = (
            AuditEvent.ROLE_CHANGED,
            AuditEvent.ADMIN_USER_SOFT_DELETED,
            AuditEvent.ADMIN_USER_PURGED,
        )
        for event in never_dropped:
            await _seed_audit(db, event, ts=_ts_days_ago(1000))

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["status"] == "ok"
        assert result["deleted"] == 0
        for event in never_dropped:
            assert await _count_event(db, event) == 1

    async def test_downsamplable_aged_aggregated_recent_kept(self, retention_env, kvstore):
        db = retention_env
        # ADMIN_USER_VIEWED is Downsamplable: past the 90d window → aggregated +
        # deleted; inside the window → kept.
        aged_ts = _ts_days_ago(100)
        await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=aged_ts)
        await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(10))

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["status"] == "ok"
        assert result["downsampled"] == 1
        # Only the recent row remains; the aged one was aggregated then deleted.
        assert await _count_event(db, AuditEvent.ADMIN_USER_VIEWED) == 1
        key = audit_downsample_key(DownsamplableEvent.USER_VIEWED)
        assert await _metric_value(db, aged_ts[:10], key) == 1


# ---------------------------------------------------------------------------
# 2. Aggregate-then-delete ordering (Req 1.4)
# ---------------------------------------------------------------------------


class TestAggregateThenDelete:
    async def test_counts_land_on_correct_day_and_rows_deleted(self, retention_env, kvstore):
        db = retention_env
        # Two distinct aged days: 3 rows on dayA, 2 rows on dayB.
        day_a_ts = _ts_days_ago(100)
        day_b_ts = _ts_days_ago(120)
        day_a, day_b = day_a_ts[:10], day_b_ts[:10]
        assert day_a != day_b
        for i in range(3):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(100, second=i))
        for i in range(2):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(120, second=i))

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["downsampled"] == 5
        key = audit_downsample_key(DownsamplableEvent.USER_VIEWED)
        # Each day's count folds into that day's key; all rows are then deleted.
        assert await _metric_value(db, day_a, key) == 3
        assert await _metric_value(db, day_b, key) == 2
        assert await _count_event(db, AuditEvent.ADMIN_USER_VIEWED) == 0


# ---------------------------------------------------------------------------
# 3. Aggregate-failure retention + per-group isolation (Req 1.9)
# ---------------------------------------------------------------------------


class TestAggregateFailureRetention:
    async def test_failed_group_retained_other_group_processed(
        self, retention_env, kvstore, monkeypatch
    ):
        db = retention_env
        day_fail_ts = _ts_days_ago(100)
        day_ok_ts = _ts_days_ago(120)
        day_fail, day_ok = day_fail_ts[:10], day_ok_ts[:10]
        assert day_fail != day_ok
        # 2 rows in the group whose aggregate will fail, 3 in the group that succeeds.
        for i in range(2):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(100, second=i))
        for i in range(3):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(120, second=i))

        from app.admin.metric_store import MetricStore

        class _FailingAddStore:
            """Delegates to a real store but raises ``add`` for one day only."""

            def __init__(self, inner: MetricStore, fail_day: str) -> None:
                self._inner = inner
                self._fail_day = fail_day

            async def add(self, day: str, key: str, delta: int) -> None:
                if day == self._fail_day:
                    raise RuntimeError("simulated aggregate write failure")
                await self._inner.add(day, key, delta)

        store = _FailingAddStore(MetricStore(db.session_factory), day_fail)
        monkeypatch.setattr("app.admin.jobs.get_metric_store", lambda: store)

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["status"] == "ok"
        # Only the succeeding group was counted-and-deleted.
        assert result["downsampled"] == 3
        key = audit_downsample_key(DownsamplableEvent.USER_VIEWED)
        # Failed group: rows RETAINED and its metric key left unchanged (None).
        assert await _count_event_on_day(db, AuditEvent.ADMIN_USER_VIEWED, day_fail) == 2
        assert await _metric_value(db, day_fail, key) is None
        # Succeeding group: aggregated then deleted.
        assert await _count_event_on_day(db, AuditEvent.ADMIN_USER_VIEWED, day_ok) == 0
        assert await _metric_value(db, day_ok, key) == 3


# ---------------------------------------------------------------------------
# 4. Resume-after-interruption / no double-count (Req 1.6)
# ---------------------------------------------------------------------------


class TestResumeNoDoubleCount:
    async def test_bounded_runs_drain_backlog_exactly_once(
        self, retention_env, kvstore, monkeypatch
    ):
        db = retention_env
        # Small shared budget so a single run can't process the whole backlog.
        monkeypatch.setattr(app_settings, "admin_audit_retention_batch", 2)
        # 5 aged Downsamplable rows on the SAME day.
        aged_ts = _ts_days_ago(100)
        day = aged_ts[:10]
        for i in range(5):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(100, second=i))

        r1 = await run_audit_retention_job(kvstore=kvstore)
        r2 = await run_audit_retention_job(kvstore=kvstore)
        r3 = await run_audit_retention_job(kvstore=kvstore)

        assert r1["downsampled"] == 2
        assert r2["downsampled"] == 2
        assert r3["downsampled"] == 1
        # Backlog fully drained and each aged row counted exactly once.
        key = audit_downsample_key(DownsamplableEvent.USER_VIEWED)
        assert await _metric_value(db, day, key) == 5
        assert await _count_event(db, AuditEvent.ADMIN_USER_VIEWED) == 0
        # A further run is a clean no-op (nothing left, no over-count).
        r4 = await run_audit_retention_job(kvstore=kvstore)
        assert r4["downsampled"] == 0
        assert await _metric_value(db, day, key) == 5


# ---------------------------------------------------------------------------
# 5. Shared per-invocation budget (Req 1.7)
# ---------------------------------------------------------------------------


class TestSharedBudget:
    async def test_downsampled_plus_deleted_within_batch(
        self, retention_env, kvstore, monkeypatch
    ):
        db = retention_env
        monkeypatch.setattr(app_settings, "admin_audit_retention_batch", 4)
        # 3 aged Downsamplable + 3 aged Security_Critical rows; budget is 4.
        for i in range(3):
            await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=_ts_days_ago(100, second=i))
        for i in range(3):
            await _seed_audit(db, AuditEvent.LOGIN, ts=_ts_days_ago(400, second=i))

        result = await run_audit_retention_job(kvstore=kvstore)

        assert result["batch"] == 4
        # Downsample consumes its slice first (3), delete gets the leftover (1).
        assert result["downsampled"] == 3
        assert result["deleted"] == 1
        # The core invariant: total processed never exceeds the shared budget.
        assert result["downsampled"] + result["deleted"] <= 4


# ---------------------------------------------------------------------------
# 6. Single-flight lock (Req 1.1)
# ---------------------------------------------------------------------------


class TestSingleFlightLock:
    async def test_held_lock_makes_job_a_noop(self, retention_env, kvstore):
        db = retention_env
        # Seed rows that WOULD be pruned if the job ran.
        await _seed_audit(db, AuditEvent.LOGIN, ts=_ts_days_ago(400))
        aged_ts = _ts_days_ago(100)
        await _seed_audit(db, AuditEvent.ADMIN_USER_VIEWED, ts=aged_ts)
        before = await _count_total(db)

        # Hold the job's lock externally, then invoke the job with the same store.
        lock = kvstore.lock(AUDIT_RETENTION_LOCK_KEY, ttl_seconds=60, blocking=False)
        assert await lock.acquire() is True
        try:
            result = await run_audit_retention_job(kvstore=kvstore)
        finally:
            await lock.release()

        # Terminates immediately without deleting/aggregating anything.
        assert result == {"status": "locked"}
        assert await _count_total(db) == before
        key = audit_downsample_key(DownsamplableEvent.USER_VIEWED)
        assert await _metric_value(db, aged_ts[:10], key) is None
