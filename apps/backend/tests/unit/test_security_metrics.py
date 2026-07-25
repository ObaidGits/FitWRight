"""Unit tests for exact security views and durable closed-day aggregation.

``SecurityMetricsService.view`` delegates one exact trailing ``[start, end)``
24-hour interval to ``AdminRepo.security_window`` and labels current-role admin
classification explicitly. ``SecurityAggregateStep`` coverage below remains
focused on idempotent closed-day UPSERTs and failure isolation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.admin.metric_registry import (
    SEC_ADMIN_LOGIN,
    SEC_AUTHZ_DENIED,
    SEC_LOGIN_FAILED,
    SEC_RATE_LIMITED,
    SEC_SUSPICIOUS,
)
from app.admin.metric_store import MetricStore
from app.admin.schemas import SecurityView, assert_no_forbidden_fields
from app.admin.security_metrics import (
    SecurityAggregateStep,
    SecurityMetricsService,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers / test doubles
# ---------------------------------------------------------------------------


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore used only by ``SecurityAggregateStep`` tests."""
    return MetricStore(isolated_db.session_factory)


def _service(repo) -> SecurityMetricsService:
    return SecurityMetricsService(repo=repo)


def _day(offset: int = 0) -> str:
    """The UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


class _WindowRepo:
    """Repository fake recording exact trailing-window reads."""

    def __init__(self, counts: dict[str, int] | None = None) -> None:
        self._counts = counts or {}
        self.calls: list[tuple[str, str]] = []

    async def security_window(self, start: str, end: str) -> dict[str, int]:
        self.calls.append((start, end))
        return dict(self._counts)


class _SpyRepo:
    """An ``AdminRepo`` stand-in returning fixed ``SEC_*`` counts per day.

    Records each ``security_daily`` call so the step's lookback fan-out can be
    asserted. Every call returns the same dict, mirroring the real repo's
    whole-day recompute (which is why the step UPSERTs an absolute value).
    """

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.calls: list[tuple[str, str]] = []

    async def security_daily(self, day_start: str, day_end: str) -> dict[str, int]:
        self.calls.append((day_start, day_end))
        return dict(self._counts)


class _FailingDayRepo(_SpyRepo):
    """A spy repo whose ``security_daily`` raises for one target UTC day."""

    def __init__(self, counts: dict[str, int], fail_day: str) -> None:
        super().__init__(counts)
        self._fail_day = fail_day

    async def security_daily(self, day_start: str, day_end: str) -> dict[str, int]:
        self.calls.append((day_start, day_end))
        if day_start.startswith(self._fail_day):
            raise RuntimeError("simulated security_daily failure")
        return dict(self._counts)


class _FailingKeyStore:
    """A store wrapper whose ``upsert`` raises for one target key.

    Delegates every other write; used to exercise the step's per-key isolation
    (a failing key preserves its last value while the rest are still written).
    """

    def __init__(self, inner: MetricStore, fail_key: str) -> None:
        self._inner = inner
        self._fail_key = fail_key
        self.attempted: list[tuple[str, str, int]] = []

    async def upsert(self, day: str, key: str, value: int) -> None:
        self.attempted.append((day, key, value))
        if key == self._fail_key:
            raise RuntimeError("simulated upsert failure")
        await self._inner.upsert(day, key, value)

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        return await self._inner.sum(keys, day_from, day_to)


async def _seed_day(store: MetricStore, day: str, values: dict[str, int]) -> None:
    for key, value in values.items():
        await store.upsert(day, key, value)


# ===========================================================================
# 1. SecurityMetricsService exact trailing-window view (Req 9.3-9.7)
# ===========================================================================


class TestSecurityView:
    """The request path delegates one exact half-open 24-hour window to the repo."""

    async def test_exact_24_hour_half_open_window_and_response_metadata(self, monkeypatch):
        end = datetime(2025, 2, 14, 12, 34, 56, 789000, tzinfo=timezone.utc)
        start = end - timedelta(hours=24)
        monkeypatch.setattr("app.admin.security_metrics._now", lambda: end)
        counts = {
            SEC_LOGIN_FAILED: 5,
            SEC_ADMIN_LOGIN: 2,
            SEC_AUTHZ_DENIED: 3,
            SEC_RATE_LIMITED: 7,
            SEC_SUSPICIOUS: 11,
        }
        repo = _WindowRepo(counts)
        service = _service(repo)

        view = await service.view()

        # Exactly one repository read receives the precise [start, end) bounds.
        assert repo.calls == [(start.isoformat(), end.isoformat())]
        called_start, called_end = map(datetime.fromisoformat, repo.calls[0])
        assert called_end - called_start == timedelta(hours=24)
        assert called_start < called_end
        assert vars(service) == {"_repo": repo}

        assert isinstance(view, SecurityView)
        assert view.windowHours == 24
        assert view.windowStart == start.isoformat()
        assert view.windowEnd == end.isoformat()
        assert view.windowKind == "exact_trailing"
        assert view.adminLoginRoleBasis == "current_role_at_query_time"
        assert view.computedAt == end.isoformat()
        assert view.notInstrumented == []
        assert {
            "loginFailed": view.loginFailed,
            "adminLogin": view.adminLogin,
            "authzDenied": view.authzDenied,
            "rateLimited": view.rateLimited,
            "suspicious": view.suspicious,
        } == {
            "loginFailed": 5,
            "adminLogin": 2,
            "authzDenied": 3,
            "rateLimited": 7,
            "suspicious": 11,
        }

    async def test_missing_repo_counts_default_to_measured_zero(self):
        repo = _WindowRepo()
        view = await _service(repo).view()

        assert len(repo.calls) == 1
        assert view.loginFailed == 0
        assert view.adminLogin == 0
        assert view.authzDenied == 0
        assert view.rateLimited == 0
        assert view.suspicious == 0
        assert view.notInstrumented == []

    async def test_view_serialization_has_no_forbidden_fields(self):
        view = await _service(_WindowRepo({SEC_LOGIN_FAILED: 2})).view()
        assert_no_forbidden_fields(view.model_dump(by_alias=True))


# ===========================================================================
# 2. SecurityAggregateStep - aggregates the lookback window + idempotent (Req 9.1)
# ===========================================================================


class TestAggregateStep:
    """Validates: Requirements 9.1"""

    _COUNTS = {
        SEC_LOGIN_FAILED: 4, SEC_ADMIN_LOGIN: 1, SEC_AUTHZ_DENIED: 2,
        SEC_RATE_LIMITED: 0, SEC_SUSPICIOUS: 0,
    }

    async def test_step_upserts_all_keys_for_lookback_window(self, isolated_db):
        store = _store(isolated_db)
        repo = _SpyRepo(self._COUNTS)
        step = SecurityAggregateStep(metric_store=store, repo=repo, lookback_days=2)

        result = await step.run(_day(1))  # pipeline passes the just-closed day
        assert result.ok is True
        assert result.name == "security_aggregate"

        # Aggregated exactly the 2-day lookback window (passed day + one before).
        assert len(repo.calls) == 2
        # Both closed days now carry every SEC_* value.
        for day in (_day(1), _day(2)):
            for key, expected in self._COUNTS.items():
                assert await store.sum([key], day, day) == expected

    async def test_rerun_is_idempotent_not_doubled(self, isolated_db):
        store = _store(isolated_db)
        repo = _SpyRepo(self._COUNTS)
        step = SecurityAggregateStep(metric_store=store, repo=repo, lookback_days=2)

        await step.run(_day(1))
        first = await store.sum([SEC_LOGIN_FAILED], _day(1), _day(1))
        await step.run(_day(1))  # re-run the same closed day
        second = await store.sum([SEC_LOGIN_FAILED], _day(1), _day(1))

        # UPSERT of an absolute value -> re-running recomputes the same count, not
        # a doubled one (idempotent per closed day).
        assert first == self._COUNTS[SEC_LOGIN_FAILED]
        assert second == first


# ===========================================================================
# 7. SecurityAggregateStep - failure isolation / preserve-last (Req 9.2)
# ===========================================================================


class TestAggregateStepFailureIsolation:
    """Validates: Requirements 9.2"""

    _COUNTS = {
        SEC_LOGIN_FAILED: 8, SEC_ADMIN_LOGIN: 3, SEC_AUTHZ_DENIED: 1,
        SEC_RATE_LIMITED: 0, SEC_SUSPICIOUS: 0,
    }

    async def test_repo_failure_preserves_that_day_and_processes_others(self, isolated_db):
        store = _store(isolated_db)
        fail_day = _day(1)  # the just-closed day's read will raise
        # Pre-seed a known good value on the failing day - it must be preserved.
        await store.upsert(fail_day, SEC_LOGIN_FAILED, 42)

        repo = _FailingDayRepo(self._COUNTS, fail_day=fail_day)
        step = SecurityAggregateStep(metric_store=store, repo=repo, lookback_days=2)

        result = await step.run(_day(1))

        # StepResult.failure names the failed read for that day.
        assert result.ok is False
        assert f"security_daily@{fail_day}" in result.error
        # The failing day's pre-seeded value is untouched (no overwrite, no zero).
        assert await store.sum([SEC_LOGIN_FAILED], fail_day, fail_day) == 42
        # The other day in the window was still processed successfully.
        other = _day(2)
        for key, expected in self._COUNTS.items():
            assert await store.sum([key], other, other) == expected

    async def test_per_key_upsert_failure_preserves_that_key_only(self, isolated_db):
        real = _store(isolated_db)
        day = _day(1)
        # Pre-seed the key whose upsert will fail - its value must survive.
        await real.upsert(day, SEC_AUTHZ_DENIED, 99)

        failing = _FailingKeyStore(real, fail_key=SEC_AUTHZ_DENIED)
        repo = _SpyRepo(self._COUNTS)
        step = SecurityAggregateStep(metric_store=failing, repo=repo, lookback_days=1)

        result = await step.run(day)

        # StepResult.failure names the failing key@day.
        assert result.ok is False
        assert f"{SEC_AUTHZ_DENIED}@{day}" in result.error
        # The failing key kept its previous value (preserve-last).
        assert await real.sum([SEC_AUTHZ_DENIED], day, day) == 99
        # Every other key was still written for the day.
        for key, expected in self._COUNTS.items():
            if key == SEC_AUTHZ_DENIED:
                continue
            assert await real.sum([key], day, day) == expected
