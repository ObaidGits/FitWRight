"""Unit tests for the Background-Jobs panel read service (Task 7.3, Req 8).

Exercises :class:`app.admin.jobs_panel.JobsPanelService` in isolation - the
per-job field mapping from KV run markers, the ``currentDurationSeconds`` /
running-since derivation, the potentially-stuck detection math, lock-state
probing across KVStore adapters, the queue/purge-backlog gauges, graceful
degradation on marker-read failure, the documented no-``retryCount`` gap, and
the O(1) (one KV read per job) bound.

Dependencies are injected (an isolated ``MetricStore`` + a real
``LocalKVStore``) so nothing here touches the process-wide singletons or the
app DB engine.

Requirements: 8.1, 8.3, 8.8, 8.10, 8.6, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.job_markers import job_marker_name
from app.admin.jobs import (
    AUDIT_RETENTION_LOCK_KEY,
    PURGE_LOCK_KEY,
    ROLLUP_LOCK_KEY,
)
from app.admin.jobs_panel import JobsPanelService, get_jobs_panel_service
from app.admin.metrics import get_admin_metrics, reset_admin_metrics
from app.admin.schemas import JobRow, JobsPanel, assert_no_forbidden_fields
from app.auth.kvstore.local import LocalKVStore
from app.config import settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Test doubles / helpers
# ---------------------------------------------------------------------------


class _FakeStore:
    """A MetricStore stand-in returning a chosen marker per job name.

    ``snapshot_get(name)`` looks the marker up by the ``job_marker:{job}`` name;
    unknown names return ``None`` (no marker). ``calls`` records every requested
    name so the O(1) bound can be asserted. Jobs whose marker value is the
    sentinel :data:`_RAISE` raise, exercising the graceful-degradation path.
    """

    def __init__(self, markers: dict[str, object]) -> None:
        # markers keyed by bare job name (e.g. "rollup")
        self._markers = markers
        self.calls: list[str] = []

    async def snapshot_get(self, name: str):
        self.calls.append(name)
        # Map the snapshot name back to the bare job name.
        job = name.split(":", 1)[1] if ":" in name else name
        value = self._markers.get(job)
        if value is _RAISE:
            raise RuntimeError("simulated marker read failure")
        return value


_RAISE = object()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _marker(**overrides) -> dict:
    """A fully-populated marker dict with sensible defaults, overridable."""
    base = {
        "job": "rollup",
        "last_run": "2024-01-01T00:00:00+00:00",
        "last_outcome": "success",
        "lag_seconds": None,
        "next_run": None,
        "last_success": "2024-01-01T00:00:00+00:00",
        "running_since": None,
        "last_duration_seconds": 12.0,
        "expected_duration_seconds": 10.0,
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _service(markers: dict[str, object], *, kvstore=None) -> JobsPanelService:
    store = _FakeStore(markers)
    return JobsPanelService(metric_store=store, kvstore=kvstore or LocalKVStore())


async def _row(panel: JobsPanel, name: str) -> JobRow:
    return next(r for r in panel.jobs if r.name == name)


# ===========================================================================
# 1. Field mapping from markers (Req 8.1 / 8.8)
# ===========================================================================


class TestFieldMapping:
    async def test_marker_fields_map_to_row(self):
        marker = _marker(
            last_run="2024-05-01T10:00:00+00:00",
            last_outcome="success",
            lag_seconds=7.0,
            next_run="2024-05-02T10:00:00+00:00",
            last_success="2024-05-01T10:00:00+00:00",
            running_since=None,
            expected_duration_seconds=42.0,
        )
        svc = _service({"rollup": marker, "purge": None, "audit_retention": None})
        panel = await svc.panel()

        row = await _row(panel, "rollup")
        assert row.lastRun == "2024-05-01T10:00:00+00:00"
        assert row.lastOutcome == "success"
        assert row.lagSeconds == 7
        assert row.nextRun == "2024-05-02T10:00:00+00:00"
        assert row.lastSuccess == "2024-05-01T10:00:00+00:00"
        assert row.expectedDurationSeconds == 42
        # not running -> no current duration, not stuck
        assert row.runningSince is None
        assert row.currentDurationSeconds is None
        assert row.potentiallyStuck is False

    async def test_next_run_null_when_marker_next_run_null(self):
        svc = _service({"rollup": _marker(next_run=None)})
        row = await _row(await svc.panel(), "rollup")
        assert row.nextRun is None

    async def test_all_three_jobs_present(self):
        svc = _service({"rollup": _marker(), "purge": _marker(job="purge"),
                        "audit_retention": _marker(job="audit_retention")})
        panel = await svc.panel()
        assert {r.name for r in panel.jobs} == {"rollup", "purge", "audit_retention"}


# ===========================================================================
# 2. currentDurationSeconds + running-since (Req 8.8)
# ===========================================================================


class TestCurrentDuration:
    async def test_running_since_yields_current_duration(self):
        running_since = _iso(_now() - timedelta(seconds=30))
        svc = _service({"rollup": _marker(running_since=running_since,
                                          expected_duration_seconds=1000.0)})
        row = await _row(await svc.panel(), "rollup")
        # ~30s elapsed (allow scheduling tolerance).
        assert row.runningSince == running_since
        assert row.currentDurationSeconds is not None
        assert 28 <= row.currentDurationSeconds <= 40
        # runningSince set -> the frontend can infer "running".

    async def test_running_since_null_yields_none(self):
        svc = _service({"rollup": _marker(running_since=None)})
        row = await _row(await svc.panel(), "rollup")
        assert row.currentDurationSeconds is None


# ===========================================================================
# 3. Stuck detection math (Req 8.10)
# ===========================================================================


class TestStuckDetection:
    async def test_running_over_expected_times_multiplier_is_stuck(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        # expected=10, multiplier=3 -> threshold 30s; run for 100s -> stuck.
        running_since = _iso(_now() - timedelta(seconds=100))
        svc = _service({"rollup": _marker(running_since=running_since,
                                          expected_duration_seconds=10.0)})
        row = await _row(await svc.panel(), "rollup")
        assert row.potentiallyStuck is True

    async def test_running_just_under_expected_threshold_not_stuck(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        # expected=100, multiplier=3 -> threshold 300s; run for 10s -> not stuck.
        running_since = _iso(_now() - timedelta(seconds=10))
        svc = _service({"rollup": _marker(running_since=running_since,
                                          expected_duration_seconds=100.0)})
        row = await _row(await svc.panel(), "rollup")
        assert row.potentiallyStuck is False

    async def test_no_expected_uses_ceiling_over_is_stuck(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 60)
        running_since = _iso(_now() - timedelta(seconds=120))
        svc = _service({"rollup": _marker(running_since=running_since,
                                          expected_duration_seconds=None)})
        row = await _row(await svc.panel(), "rollup")
        assert row.potentiallyStuck is True

    async def test_no_expected_under_ceiling_not_stuck(self, monkeypatch):
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)
        running_since = _iso(_now() - timedelta(seconds=30))
        svc = _service({"rollup": _marker(running_since=running_since,
                                          expected_duration_seconds=0.0)})
        row = await _row(await svc.panel(), "rollup")
        # expected 0 -> falls back to ceiling; 30s < 3600s -> not stuck.
        assert row.potentiallyStuck is False

    async def test_not_running_is_never_stuck(self, monkeypatch):
        # Even with an absurdly low ceiling, a job that isn't running is not stuck.
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 1)
        svc = _service({"rollup": _marker(running_since=None,
                                          expected_duration_seconds=None)})
        row = await _row(await svc.panel(), "rollup")
        assert row.potentiallyStuck is False


# ===========================================================================
# 4. Lock state held / free / unavailable (Req 8.3 / 8.7)
# ===========================================================================


class TestLockState:
    async def test_held_lock_reports_held(self):
        kv = LocalKVStore()
        lock = kv.lock(ROLLUP_LOCK_KEY, ttl_seconds=300, blocking=False)
        assert await lock.acquire() is True
        try:
            svc = _service({"rollup": _marker()}, kvstore=kv)
            row = await _row(await svc.panel(), "rollup")
            assert row.lockState == "held"
        finally:
            await lock.release()

    async def test_free_lock_reports_free(self):
        kv = LocalKVStore()
        svc = _service({"rollup": _marker()}, kvstore=kv)
        row = await _row(await svc.panel(), "rollup")
        assert row.lockState == "free"

    async def test_expired_local_lock_reads_free(self):
        # Simulate a lapsed lock by writing a past expiry directly into the map.
        import time

        kv = LocalKVStore()
        kv._lock_until[PURGE_LOCK_KEY] = (time.monotonic() - 5.0, "stale-token")
        svc = _service({"purge": _marker(job="purge")}, kvstore=kv)
        row = await _row(await svc.panel(), "purge")
        assert row.lockState == "free"

    async def test_only_targeted_job_lock_is_held(self):
        kv = LocalKVStore()
        lock = kv.lock(AUDIT_RETENTION_LOCK_KEY, ttl_seconds=300, blocking=False)
        assert await lock.acquire() is True
        try:
            svc = _service(
                {"rollup": _marker(), "purge": _marker(job="purge"),
                 "audit_retention": _marker(job="audit_retention")},
                kvstore=kv,
            )
            panel = await svc.panel()
            assert (await _row(panel, "audit_retention")).lockState == "held"
            assert (await _row(panel, "rollup")).lockState == "free"
            assert (await _row(panel, "purge")).lockState == "free"
        finally:
            await lock.release()

    async def test_unknown_adapter_reports_none_and_does_not_crash(self):
        class _MysteryKV:
            pass

        svc = _service({"rollup": _marker()}, kvstore=_MysteryKV())
        row = await _row(await svc.panel(), "rollup")
        assert row.lockState is None  # unavailable, not a crash


# ===========================================================================
# 5. Queue length + purge backlog gauges (Req 8.7)
# ===========================================================================


class TestGauges:
    def setup_method(self):
        reset_admin_metrics()

    def teardown_method(self):
        reset_admin_metrics()

    async def test_queue_length_always_unavailable(self):
        svc = _service({"rollup": _marker()})
        panel = await svc.panel()
        assert panel.queueLength is None
        assert panel.queueLengthUnavailable is True

    async def test_purge_backlog_from_admin_metrics_gauge(self):
        get_admin_metrics().set_purge_backlog(5)
        svc = _service({"rollup": _marker()})
        panel = await svc.panel()
        assert panel.purgeBacklog == 5
        assert panel.purgeBacklogUnavailable is False


# ===========================================================================
# 6. No marker / failed marker read (Req 8.7)
# ===========================================================================


class TestMissingAndFailedMarkers:
    async def test_no_marker_row_present_with_unknown_fields(self):
        kv = LocalKVStore()
        svc = _service({"rollup": None}, kvstore=kv)
        panel = await svc.panel()
        row = await _row(panel, "rollup")
        assert row.name == "rollup"
        assert row.lastRun is None
        assert row.lastOutcome is None
        assert row.currentDurationSeconds is None
        assert row.potentiallyStuck is False
        # lock state still probed from the injected adapter.
        assert row.lockState == "free"
        # A missing (None) marker is not itself an error -> not stale.
        assert panel.stale is False

    async def test_failed_marker_read_sets_stale_and_does_not_crash(self):
        svc = _service({"rollup": _RAISE, "purge": _marker(job="purge")})
        panel = await svc.panel()
        assert panel.stale is True
        # The failed job still yields a row; the healthy job maps normally.
        assert (await _row(panel, "rollup")).lastRun is None
        assert (await _row(panel, "purge")).lastOutcome == "success"


# ===========================================================================
# 7. Retry count documented N/A (Req 8.1)
# ===========================================================================


class TestNoRetryField:
    async def test_job_row_has_no_retry_field(self):
        # Retry is documented as N/A - JobRow carries no retryCount field, and
        # the panel builds successfully without one.
        svc = _service({"rollup": _marker()})
        panel = await svc.panel()
        assert "retryCount" not in JobRow.model_fields
        assert "retryCount" not in panel.jobs[0].model_dump()

    async def test_panel_is_secret_free(self):
        # 15.8 / Property 3: no forbidden field ever serializes.
        svc = _service({"rollup": _marker(), "purge": _marker(job="purge"),
                        "audit_retention": _marker(job="audit_retention")})
        panel = await svc.panel()
        assert_no_forbidden_fields(panel.model_dump())


# ===========================================================================
# 8. O(1) bound - one KV read per job, independent of data volume (Req 8.6)
# ===========================================================================


class TestBoundedReads:
    async def test_one_snapshot_read_per_job(self):
        store = _FakeStore({"rollup": _marker(), "purge": _marker(job="purge"),
                            "audit_retention": _marker(job="audit_retention")})
        svc = JobsPanelService(metric_store=store, kvstore=LocalKVStore())
        await svc.panel()
        # Exactly one snapshot_get per job (3), no per-row scanning.
        assert store.calls == [
            job_marker_name("rollup"),
            job_marker_name("purge"),
            job_marker_name("audit_retention"),
        ]

    async def test_read_count_independent_of_marker_content(self):
        # A marker with far more data does not change the read count.
        big = _marker(running_since=_iso(_now()))
        store = _FakeStore({"rollup": big, "purge": big, "audit_retention": big})
        svc = JobsPanelService(metric_store=store, kvstore=LocalKVStore())
        await svc.panel()
        assert len(store.calls) == 3


# ===========================================================================
# Singleton accessor
# ===========================================================================


class TestAccessor:
    def test_get_jobs_panel_service_is_singleton(self):
        assert get_jobs_panel_service() is get_jobs_panel_service()
