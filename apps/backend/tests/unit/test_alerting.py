"""Unit tests for the alerting job (Task 15.3).

Exercises ``run_alerting_job`` and its supporting helpers (``_apply_alert``,
``_high_error_rate``, ``_job_marker_stuck``, ``_cooldown_seconds``,
``_deliver_alert``) in isolation - no real DB/KVStore. Dependencies are driven
via fake/mock objects injected through the ``kvstore`` param and monkeypatching
``get_metric_store`` / ``get_health_service`` / ``settings``.

Covers: independent evaluation (Req 12.2), cooldown suppression (Req 12.3),
resolve -> re-raise (Req 12.4), misconfig skip (Req 12.6), no new collection
(Req 12.1/21.8), and fixed alert-set (Req 12.2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.admin.jobs import (
    ALERT_CONDITIONS,
    ALERTING_LOCK_KEY,
    _apply_alert,
    _ConditionUnavailable,
    _cooldown_seconds,
    _deliver_alert,
    _HEALTH_BAD_STATUSES,
    _high_error_rate,
    _job_marker_stuck,
    run_alerting_job,
)
from app.admin.schemas import AdminHealth, HealthTile, ReleaseInfo

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake / mock helpers
# ---------------------------------------------------------------------------


class FakeKVStore:
    """In-memory KVStore with a controllable lock."""

    def __init__(self, *, lock_acquired=True):
        self._lock_acquired = lock_acquired

    def lock(self, name, *, ttl_seconds=None, blocking=False):
        acquired = self._lock_acquired
        return _FakeLockCtx(acquired)


class _FakeLockCtx:
    def __init__(self, acquired: bool):
        self._acquired = acquired

    async def __aenter__(self):
        return self._acquired

    async def __aexit__(self, *exc):
        return False


class FakeMetricStore:
    """In-memory MetricStore supporting snapshot_get/put and sum."""

    def __init__(self):
        self._snapshots: dict[str, dict | None] = {}
        self._sums: dict[tuple, int] = {}  # keyed by (tuple(keys), day_from, day_to)

    async def snapshot_get(self, name: str) -> dict | None:
        return self._snapshots.get(name)

    async def snapshot_put(self, name: str, payload: dict, **kwargs) -> None:
        self._snapshots[name] = payload

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        key = (tuple(sorted(keys)), day_from, day_to)
        if key in self._sums:
            return self._sums[key]
        # Default: return 0 for any unset sum
        return 0

    def set_sum(self, keys, day_from: str, day_to: str, value: int):
        """Test helper to preset a sum result."""
        key = (tuple(sorted(keys)), day_from, day_to)
        self._sums[key] = value


def _make_health(tile_overrides: dict[str, str] | None = None) -> AdminHealth:
    """Build an AdminHealth payload with all tiles ok, optionally overriding."""
    statuses = {
        "Backend": "ok",
        "Database": "ok",
        "KVStore/Queue": "ok",
        "AI provider": "ok",
        "Storage provider": "ok",
        "Migrations": "ok",
    }
    if tile_overrides:
        statuses.update(tile_overrides)
    tiles = [HealthTile(name=n, status=s) for n, s in statuses.items()]
    return AdminHealth(
        tiles=tiles,
        release=ReleaseInfo(version="1.0.0", env="test"),
        backendUptimeSeconds=100,
        computedAt=datetime.now(timezone.utc).isoformat(),
        stale=False,
    )


def _patch_alerting_deps(
    monkeypatch,
    *,
    health: AdminHealth | None = None,
    store: FakeMetricStore | None = None,
    cooldown: int = 3600,
    error_rate_pct: int = 5,
    stuck_multiplier: int = 3,
    stuck_ceiling: int = 3600,
):
    """Patch get_metric_store, get_health_service, and settings for alerting tests."""
    if store is None:
        store = FakeMetricStore()
    if health is None:
        health = _make_health()

    mock_health_svc = MagicMock()
    mock_health_svc.compose_health = AsyncMock(return_value=health)

    # These are imported inside run_alerting_job from their source modules,
    # so we must patch them at the source.
    monkeypatch.setattr(
        "app.admin.metric_store.get_metric_store",
        lambda: store,
    )

    # Patch settings via the jobs module's reference
    from app.config import settings

    monkeypatch.setattr(settings, "alert_cooldown_seconds", cooldown)
    monkeypatch.setattr(settings, "alert_error_rate_pct", error_rate_pct)
    monkeypatch.setattr(settings, "admin_job_stuck_multiplier", stuck_multiplier)
    monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", stuck_ceiling)

    # Patch get_health_service at the module level where it's imported inside the function
    monkeypatch.setattr(
        "app.admin.health_service.get_health_service",
        lambda: mock_health_svc,
    )

    return store, mock_health_svc


# ===========================================================================
# 1. Lock behavior
# ===========================================================================


class TestLockBehavior:
    async def test_locked_returns_status_locked(self):
        """When lock not acquired, returns {"status": "locked"}."""
        kv = FakeKVStore(lock_acquired=False)
        result = await run_alerting_job(kvstore=kv)
        assert result == {"status": "locked"}


# ===========================================================================
# 2. All conditions healthy - no alerts
# ===========================================================================


class TestAllHealthy:
    async def test_all_conditions_healthy_no_alerts(self, monkeypatch):
        """All tiles healthy, no markers stuck -> no raises/resolves."""
        store, _ = _patch_alerting_deps(monkeypatch)
        kv = FakeKVStore(lock_acquired=True)

        result = await run_alerting_job(kvstore=kv)
        assert result["status"] == "ok"
        assert result["raised"] == []
        assert result["resolved"] == []
        # storage_near_full is always skipped
        assert "storage_near_full" in result["skipped"]


# ===========================================================================
# 3. Independent evaluation
# ===========================================================================


class TestIndependentConditions:
    async def test_independent_conditions_one_bad_others_ok(self, monkeypatch):
        """One tile 'down' raises only that condition's alert."""
        health = _make_health({"Database": "down"})
        store, _ = _patch_alerting_deps(monkeypatch, health=health)
        kv = FakeKVStore(lock_acquired=True)

        result = await run_alerting_job(kvstore=kv)
        assert result["status"] == "ok"
        assert "db_unhealthy" in result["raised"]
        # Other health conditions should NOT be raised
        assert "kv_unavailable" not in result["raised"]
        assert "migration_mismatch" not in result["raised"]
        assert "ai_provider_unavailable" not in result["raised"]

    async def test_condition_skip_does_not_affect_others(self, monkeypatch):
        """storage_near_full always skips, but other conditions still evaluate."""
        health = _make_health({"KVStore/Queue": "degraded"})
        store, _ = _patch_alerting_deps(monkeypatch, health=health)
        kv = FakeKVStore(lock_acquired=True)

        result = await run_alerting_job(kvstore=kv)
        assert "storage_near_full" in result["skipped"]
        # kv_unavailable should still be raised despite the skip
        assert "kv_unavailable" in result["raised"]


# ===========================================================================
# 4. Cooldown suppression (Req 12.3)
# ===========================================================================


class TestCooldownSuppression:
    async def test_cooldown_suppresses_reraise(self, monkeypatch):
        """Raise once, call again within cooldown -> not re-raised."""
        health = _make_health({"Database": "down"})
        store, _ = _patch_alerting_deps(monkeypatch, health=health, cooldown=3600)
        kv = FakeKVStore(lock_acquired=True)

        # First run - raises
        result1 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result1["raised"]

        # Second run - within cooldown, should NOT re-raise
        result2 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" not in result2["raised"]
        assert "db_unhealthy" not in result2["resolved"]

    async def test_cooldown_elapsed_allows_reraise(self, monkeypatch):
        """Raise once, call again after cooldown -> re-raised."""
        health = _make_health({"Database": "down"})
        store, _ = _patch_alerting_deps(monkeypatch, health=health, cooldown=60)
        kv = FakeKVStore(lock_acquired=True)

        # First run
        result1 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result1["raised"]

        # Simulate cooldown elapsed by backdating the stored state
        from app.admin.jobs import _alert_state_name

        state_key = _alert_state_name("db_unhealthy")
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        await store.snapshot_put(state_key, {
            "name": "db_unhealthy",
            "state": "raised",
            "last_raised_at": old_time,
            "updated_at": old_time,
        })

        # Second run - cooldown has elapsed, should re-raise
        result2 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result2["raised"]


# ===========================================================================
# 5. Resolve -> re-raise (Req 12.4)
# ===========================================================================


class TestResolveThenReraise:
    async def test_resolve_then_reraise_fresh(self, monkeypatch):
        """Raise -> condition clears -> resolved; fires again -> raises fresh."""
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)

        # Phase 1: DB is down -> raise
        health_down = _make_health({"Database": "down"})
        _patch_alerting_deps(monkeypatch, health=health_down, store=store, cooldown=9999)

        result1 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result1["raised"]

        # Phase 2: DB recovers -> resolve
        health_ok = _make_health({"Database": "ok"})
        mock_health_svc = MagicMock()
        mock_health_svc.compose_health = AsyncMock(return_value=health_ok)
        monkeypatch.setattr(
            "app.admin.health_service.get_health_service",
            lambda: mock_health_svc,
        )

        result2 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result2["resolved"]

        # Phase 3: DB goes down again -> raises fresh (no cooldown gate)
        mock_health_svc.compose_health = AsyncMock(return_value=health_down)

        result3 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result3["raised"]


# ===========================================================================
# 6. Misconfig skip (Req 12.6)
# ===========================================================================


class TestMisconfigSkip:
    async def test_misconfig_cooldown_skips_raises_but_resolves_still_work(self, monkeypatch):
        """Invalid cooldown -> true conditions skipped, but resolves still process."""
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)

        # First: raise db_unhealthy with valid cooldown
        health_down = _make_health({"Database": "down"})
        _patch_alerting_deps(monkeypatch, health=health_down, store=store, cooldown=3600)
        result1 = await run_alerting_job(kvstore=kv)
        assert "db_unhealthy" in result1["raised"]

        # Now DB recovers but cooldown is misconfigured (non-positive)
        health_ok = _make_health({"Database": "ok"})
        mock_health_svc = MagicMock()
        mock_health_svc.compose_health = AsyncMock(return_value=health_ok)
        monkeypatch.setattr(
            "app.admin.health_service.get_health_service",
            lambda: mock_health_svc,
        )
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", -1)

        result2 = await run_alerting_job(kvstore=kv)
        # Resolve should still work even with misconfigured cooldown
        assert "db_unhealthy" in result2["resolved"]

    async def test_misconfig_cooldown_skips_triggered_conditions(self, monkeypatch):
        """Invalid cooldown -> triggered conditions are skipped (not raised)."""
        health_down = _make_health({"Database": "down"})
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)

        from app.config import settings

        _patch_alerting_deps(monkeypatch, health=health_down, store=store, cooldown=-1)
        # Ensure cooldown is invalid
        monkeypatch.setattr(settings, "alert_cooldown_seconds", -1)

        result = await run_alerting_job(kvstore=kv)
        # db_unhealthy should be skipped, not raised (cooldown invalid)
        assert "db_unhealthy" in result["skipped"]
        assert "db_unhealthy" not in result["raised"]

    async def test_misconfig_error_rate_pct_skips_high_error_rate(self, monkeypatch):
        """Invalid alert_error_rate_pct -> high_error_rate condition is skipped."""
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)

        _patch_alerting_deps(monkeypatch, store=store)
        from app.config import settings

        # Set error_rate_pct to invalid (non-int or out of range)
        monkeypatch.setattr(settings, "alert_error_rate_pct", -1)

        result = await run_alerting_job(kvstore=kv)
        assert "high_error_rate" in result["skipped"]


# ===========================================================================
# 7. No new collection (Req 12.1/21.8)
# ===========================================================================


class TestNoNewCollection:
    async def test_no_new_collection(self, monkeypatch):
        """Health service compose_health is called; no new DB query or metric increment."""
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)
        store, mock_health_svc = _patch_alerting_deps(monkeypatch, store=store)

        # Wrap store to track calls
        store.upsert = AsyncMock()
        store.add = AsyncMock()

        result = await run_alerting_job(kvstore=kv)
        assert result["status"] == "ok"

        # compose_health WAS called (reads existing signal)
        mock_health_svc.compose_health.assert_called_once()

        # No new data collection - upsert and add should NOT be called
        store.upsert.assert_not_called()
        store.add.assert_not_called()


# ===========================================================================
# 8. Fixed alert-set only (Req 12.2)
# ===========================================================================


class TestFixedAlertSet:
    def test_fixed_alert_set_only(self):
        """ALERT_CONDITIONS is exactly the expected 8 strings."""
        expected = (
            "db_unhealthy",
            "kv_unavailable",
            "ai_provider_unavailable",
            "storage_near_full",
            "rollup_failed",
            "migration_mismatch",
            "high_error_rate",
            "background_job_stuck",
        )
        assert ALERT_CONDITIONS == expected
        assert len(ALERT_CONDITIONS) == 8
        assert isinstance(ALERT_CONDITIONS, tuple)


# ===========================================================================
# 9. High error rate detection
# ===========================================================================


class TestHighErrorRate:
    async def test_high_error_rate_above_threshold(self, monkeypatch):
        """5xx rate >= threshold triggers."""
        store = FakeMetricStore()

        from app.admin.metric_registry import REQUEST_2XX, REQUEST_4XX, REQUEST_5XX
        from app.config import settings

        monkeypatch.setattr(settings, "alert_error_rate_pct", 5)

        now = datetime.now(timezone.utc)
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # Set sums: 10 errors out of 100 total = 10% >= 5% threshold
        error_key = (tuple(sorted([REQUEST_5XX])), day_from, day_to)
        total_key = (tuple(sorted([REQUEST_2XX, REQUEST_4XX, REQUEST_5XX])), day_from, day_to)
        store._sums[error_key] = 10
        store._sums[total_key] = 100

        result = await _high_error_rate(store, now)
        assert result is True

    async def test_high_error_rate_below_threshold(self, monkeypatch):
        """5xx rate < threshold does not trigger."""
        store = FakeMetricStore()

        from app.admin.metric_registry import REQUEST_2XX, REQUEST_4XX, REQUEST_5XX
        from app.config import settings

        monkeypatch.setattr(settings, "alert_error_rate_pct", 5)

        now = datetime.now(timezone.utc)
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # Set sums: 2 errors out of 100 total = 2% < 5% threshold
        error_key = (tuple(sorted([REQUEST_5XX])), day_from, day_to)
        total_key = (tuple(sorted([REQUEST_2XX, REQUEST_4XX, REQUEST_5XX])), day_from, day_to)
        store._sums[error_key] = 2
        store._sums[total_key] = 100

        result = await _high_error_rate(store, now)
        assert result is False

    async def test_high_error_rate_zero_total_not_triggered(self, monkeypatch):
        """Zero total requests -> 0% -> not triggered."""
        store = FakeMetricStore()

        from app.config import settings

        monkeypatch.setattr(settings, "alert_error_rate_pct", 5)

        now = datetime.now(timezone.utc)
        result = await _high_error_rate(store, now)
        assert result is False


# ===========================================================================
# 10. Job marker stuck detection
# ===========================================================================


class TestJobMarkerStuck:
    def test_job_marker_stuck_detected(self, monkeypatch):
        """Marker with running_since far in the past -> stuck -> alert raised."""
        from app.config import settings

        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)

        now = datetime.now(timezone.utc)
        # running for 5000 seconds with no expected duration -> exceeds 3600 ceiling
        marker = {
            "running_since": (now - timedelta(seconds=5000)).isoformat(),
        }
        assert _job_marker_stuck(marker, now) is True

    def test_job_marker_not_stuck_within_ceiling(self, monkeypatch):
        """Marker running within ceiling -> not stuck."""
        from app.config import settings

        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)

        now = datetime.now(timezone.utc)
        # running for 100 seconds < 3600 ceiling
        marker = {
            "running_since": (now - timedelta(seconds=100)).isoformat(),
        }
        assert _job_marker_stuck(marker, now) is False

    def test_job_marker_stuck_with_expected_duration(self, monkeypatch):
        """Marker exceeding expected * multiplier -> stuck."""
        from app.config import settings

        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)

        now = datetime.now(timezone.utc)
        # expected 60s, multiplier 3 -> stuck if >180s; running for 200s
        marker = {
            "running_since": (now - timedelta(seconds=200)).isoformat(),
            "expected_duration_seconds": 60,
        }
        assert _job_marker_stuck(marker, now) is True

    def test_job_marker_none_not_stuck(self, monkeypatch):
        """No marker -> not stuck."""
        from app.config import settings

        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)

        now = datetime.now(timezone.utc)
        assert _job_marker_stuck(None, now) is False

    def test_job_marker_not_running_not_stuck(self, monkeypatch):
        """Marker without running_since -> not stuck."""
        from app.config import settings

        monkeypatch.setattr(settings, "admin_job_stuck_multiplier", 3)
        monkeypatch.setattr(settings, "admin_job_stuck_ceiling_seconds", 3600)

        now = datetime.now(timezone.utc)
        assert _job_marker_stuck({"running_since": None}, now) is False


# ===========================================================================
# 11. Deliver alert logs warning
# ===========================================================================


class TestDeliverAlert:
    def test_deliver_alert_logs_warning(self, caplog):
        """_deliver_alert emits a structured WARNING log."""
        with caplog.at_level(logging.WARNING, logger="app.admin.jobs"):
            _deliver_alert("test_alert", "something is broken")

        assert len(caplog.records) >= 1
        record = caplog.records[-1]
        assert record.levelno == logging.WARNING
        assert "test_alert" in record.message
        assert "something is broken" in record.message


# ===========================================================================
# 12. _apply_alert state machine
# ===========================================================================


class TestApplyAlert:
    async def test_fresh_triggered_raises_immediately(self):
        """triggered + no prior state -> raise immediately."""
        store = FakeMetricStore()
        now = datetime.now(timezone.utc)
        raised = []
        resolved = []

        await _apply_alert(
            store, "test_cond", True,
            cooldown=3600, now=now, message="test msg",
            raised=raised, resolved=resolved,
        )
        assert "test_cond" in raised
        assert resolved == []

    async def test_triggered_within_cooldown_suppressed(self):
        """triggered + already raised within cooldown -> suppressed."""
        store = FakeMetricStore()
        now = datetime.now(timezone.utc)

        # Pre-set a "raised" state from 10 seconds ago
        from app.admin.jobs import _alert_state_name

        await store.snapshot_put(_alert_state_name("test_cond"), {
            "name": "test_cond",
            "state": "raised",
            "last_raised_at": (now - timedelta(seconds=10)).isoformat(),
            "updated_at": (now - timedelta(seconds=10)).isoformat(),
        })

        raised = []
        resolved = []
        await _apply_alert(
            store, "test_cond", True,
            cooldown=3600, now=now, message="test msg",
            raised=raised, resolved=resolved,
        )
        assert raised == []
        assert resolved == []

    async def test_not_triggered_was_raised_resolves(self):
        """not triggered + was raised -> resolve."""
        store = FakeMetricStore()
        now = datetime.now(timezone.utc)

        from app.admin.jobs import _alert_state_name

        await store.snapshot_put(_alert_state_name("test_cond"), {
            "name": "test_cond",
            "state": "raised",
            "last_raised_at": (now - timedelta(seconds=100)).isoformat(),
            "updated_at": (now - timedelta(seconds=100)).isoformat(),
        })

        raised = []
        resolved = []
        await _apply_alert(
            store, "test_cond", False,
            cooldown=3600, now=now, message="test msg",
            raised=raised, resolved=resolved,
        )
        assert raised == []
        assert "test_cond" in resolved

    async def test_not_triggered_not_raised_noop(self):
        """not triggered + not previously raised -> nothing."""
        store = FakeMetricStore()
        now = datetime.now(timezone.utc)
        raised = []
        resolved = []

        await _apply_alert(
            store, "test_cond", False,
            cooldown=3600, now=now, message="test msg",
            raised=raised, resolved=resolved,
        )
        assert raised == []
        assert resolved == []


# ===========================================================================
# 13. _cooldown_seconds validation
# ===========================================================================


class TestCooldownSeconds:
    def test_valid_cooldown(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", 300)
        assert _cooldown_seconds() == 300

    def test_invalid_cooldown_raises_condition_unavailable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", -1)
        with pytest.raises(_ConditionUnavailable):
            _cooldown_seconds()

    def test_zero_cooldown_raises_condition_unavailable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", 0)
        with pytest.raises(_ConditionUnavailable):
            _cooldown_seconds()

    def test_none_cooldown_raises_condition_unavailable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", None)
        with pytest.raises(_ConditionUnavailable):
            _cooldown_seconds()

    def test_bool_cooldown_raises_condition_unavailable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "alert_cooldown_seconds", True)
        with pytest.raises(_ConditionUnavailable):
            _cooldown_seconds()


# ===========================================================================
# 14. Integration: background_job_stuck in full run
# ===========================================================================


class TestBackgroundJobStuckIntegration:
    async def test_job_marker_stuck_raises_alert(self, monkeypatch):
        """A stuck job marker in the full run -> background_job_stuck alert raised."""
        store = FakeMetricStore()
        kv = FakeKVStore(lock_acquired=True)

        now = datetime.now(timezone.utc)

        # Pre-set a stuck rollup marker
        from app.admin.job_markers import job_marker_name

        stuck_marker = {
            "job": "rollup",
            "running_since": (now - timedelta(seconds=5000)).isoformat(),
            "last_outcome": "success",
        }
        await store.snapshot_put(job_marker_name("rollup"), stuck_marker)

        _patch_alerting_deps(monkeypatch, store=store, stuck_ceiling=3600)

        result = await run_alerting_job(kvstore=kv)
        assert "background_job_stuck" in result["raised"]
