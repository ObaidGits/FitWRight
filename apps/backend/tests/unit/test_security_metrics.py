"""Unit tests for the Security panel domain (Task 13.4, Req 9).

Two units under test, both in :mod:`app.admin.security_metrics`:

- :class:`SecurityMetricsService` — the request-path read model whose ``view()``
  assembles a :class:`~app.admin.schemas.SecurityView` from the durable ``SEC_*``
  ``metrics_daily`` keys via an injected :class:`~app.admin.metric_store.MetricStore`.
- :class:`SecurityAggregateStep` — the rollup-time step that reads a day's
  ``SEC_*`` counts from :meth:`AdminRepo.security_daily` and UPSERTs each into the
  store, with per-day + per-key failure isolation over a bounded lookback window.

The headline guarantee (Req 9.6/9.7/15.4) is that the **request path never scans
``audit_log``**. We prove it two ways: *structurally* — the service holds only a
``MetricStore`` (no repo / session), so it has nothing through which it *could*
reach ``audit_log`` — and *behaviourally* — a spy store shows ``view()`` issues
exactly five ``MetricStore.sum`` reads and touches no other store method, a fixed
O(1) cost independent of how many days/rows are seeded.

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 15.4, 15.8.
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

# The five SEC_* keys view()/the step operate over (ordered as view() reads).
_SEC_KEYS = (
    SEC_LOGIN_FAILED,
    SEC_ADMIN_LOGIN,
    SEC_AUTHZ_DENIED,
    SEC_RATE_LIMITED,
    SEC_SUSPICIOUS,
)


def _store(isolated_db) -> MetricStore:
    """A DB-backed MetricStore on the isolated engine (security reads only)."""
    return MetricStore(isolated_db.session_factory)


def _service(store) -> SecurityMetricsService:
    return SecurityMetricsService(metric_store=store)


def _day(offset: int = 0) -> str:
    """The UTC ``YYYY-MM-DD`` string ``offset`` days before today."""
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


class _CountingStore:
    """A ``MetricStore``-shaped spy that records every method it is asked for.

    Delegates ``sum`` / ``upsert`` to the wrapped real store (so returned data is
    real) while tallying each call. ``series`` / ``snapshot_get`` / ``snapshot_put``
    are present so we can assert ``view()`` *never* touches them — a request-path
    read that only sums indexed aggregates and never scans anything.
    """

    def __init__(self, inner: MetricStore) -> None:
        self._inner = inner
        self.sum_calls: list[tuple[list[str], str, str]] = []
        self.series_calls = 0
        self.upsert_calls = 0
        self.snapshot_get_calls = 0
        self.snapshot_put_calls = 0

    async def sum(self, keys, day_from: str, day_to: str) -> int:
        self.sum_calls.append((list(keys), day_from, day_to))
        return await self._inner.sum(keys, day_from, day_to)

    async def upsert(self, day: str, key: str, value: int) -> None:
        self.upsert_calls += 1
        await self._inner.upsert(day, key, value)

    async def series(self, key: str, days: int):
        self.series_calls += 1
        return await self._inner.series(key, days)

    async def snapshot_get(self, name: str):
        self.snapshot_get_calls += 1
        return await self._inner.snapshot_get(name)

    async def snapshot_put(self, name: str, payload, *, ttl_seconds=None):
        self.snapshot_put_calls += 1
        return await self._inner.snapshot_put(name, payload, ttl_seconds=ttl_seconds)


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
# 1. view() aggregates vs baseline (Req 9.3) — two-day sum, out-of-window excluded
# ===========================================================================


class TestViewAggregates:
    """Validates: Requirements 9.3"""

    async def test_view_sums_last_two_utc_days_per_field(self, isolated_db):
        store = _store(isolated_db)
        # Inside the window: today + yesterday.
        await _seed_day(store, _day(0), {
            SEC_LOGIN_FAILED: 5, SEC_ADMIN_LOGIN: 1, SEC_AUTHZ_DENIED: 2,
            SEC_RATE_LIMITED: 3, SEC_SUSPICIOUS: 4,
        })
        await _seed_day(store, _day(1), {
            SEC_LOGIN_FAILED: 10, SEC_ADMIN_LOGIN: 2, SEC_AUTHZ_DENIED: 1,
            SEC_RATE_LIMITED: 0, SEC_SUSPICIOUS: 6,
        })
        # OUTSIDE the two-day window (two days ago) — must be excluded entirely.
        await _seed_day(store, _day(2), {
            SEC_LOGIN_FAILED: 100, SEC_ADMIN_LOGIN: 100, SEC_AUTHZ_DENIED: 100,
            SEC_RATE_LIMITED: 100, SEC_SUSPICIOUS: 100,
        })

        view = await _service(store).view()

        assert view.loginFailed == 15  # 5 + 10  (day -2 excluded)
        assert view.adminLogin == 3  # 1 + 2
        assert view.authzDenied == 3  # 2 + 1
        assert view.rateLimited == 3  # 3 + 0
        assert view.suspicious == 10  # 4 + 6
        assert view.windowHours == 24
        assert isinstance(view, SecurityView)
        # Honesty guard (audit fix): rate-limited + suspicious have no durable
        # source, so they are surfaced as explicitly not-instrumented rather than
        # a misleading 0 — never silently return a fabricated zero.
        assert set(view.notInstrumented) == {"rateLimited", "suspicious"}

    async def test_view_counts_are_non_negative_and_window_fixed(self, isolated_db):
        store = _store(isolated_db)
        await _seed_day(store, _day(0), {SEC_LOGIN_FAILED: 7})
        view = await _service(store).view()
        assert view.windowHours == 24
        assert all(v >= 0 for v in (
            view.loginFailed, view.adminLogin, view.authzDenied,
            view.rateLimited, view.suspicious,
        ))
        assert view.computedAt  # ISO timestamp present


# ===========================================================================
# 2. Zero-data (Req 9.5) — empty store → all-zero counts, no error, no fallback
# ===========================================================================


class TestZeroData:
    """Validates: Requirements 9.5"""

    async def test_empty_store_yields_all_zero_counts(self, isolated_db):
        view = await _service(_store(isolated_db)).view()
        assert view.loginFailed == 0
        assert view.adminLogin == 0
        assert view.authzDenied == 0
        assert view.rateLimited == 0
        assert view.suspicious == 0
        assert view.windowHours == 24
        assert view.computedAt


# ===========================================================================
# 3. No audit_log scan on the request path (Req 9.6 / 9.7 / 15.4) — CRITICAL
# ===========================================================================


class TestNoAuditLogScan:
    """Validates: Requirements 9.6, 9.7, 15.4"""

    def test_service_structurally_holds_only_a_store(self, isolated_db):
        # Structural proof: the service owns ONLY a MetricStore — no repo, no
        # session/session_factory — so there is nothing through which it could
        # reach audit_log on the request path.
        service = _service(_store(isolated_db))
        attrs = vars(service)
        assert set(attrs) == {"_metric_store"}
        assert "_repo" not in attrs
        assert "_admin_repo" not in attrs
        assert "_session_factory" not in attrs
        # The one collaborator it holds is exactly the injected MetricStore.
        assert isinstance(attrs["_metric_store"], MetricStore)

    async def test_view_issues_exactly_five_sum_reads_and_nothing_else(self, isolated_db):
        spy = _CountingStore(_store(isolated_db))
        await spy.upsert(_day(0), SEC_LOGIN_FAILED, 3)

        await SecurityMetricsService(metric_store=spy).view()

        # Behavioural proof: exactly one sum() per SEC_* key, nothing else.
        assert len(spy.sum_calls) == 5
        summed_keys = [tuple(keys) for keys, _, _ in spy.sum_calls]
        assert summed_keys == [(k,) for k in _SEC_KEYS]
        # No series / snapshot / upsert on the request path (no scan of any kind).
        assert spy.series_calls == 0
        assert spy.snapshot_get_calls == 0
        assert spy.snapshot_put_calls == 0
        # (the single upsert above was our seed, not view()'s doing)
        assert spy.upsert_calls == 1

    async def test_every_sum_spans_the_same_two_day_range(self, isolated_db):
        spy = _CountingStore(_store(isolated_db))
        await SecurityMetricsService(metric_store=spy).view()
        # All five reads use the identical [yesterday, today] inclusive range.
        ranges = {(day_from, day_to) for _, day_from, day_to in spy.sum_calls}
        assert ranges == {(_day(1), _day(0))}


# ===========================================================================
# 4. O(1) independent of row count (Req 9.7) — fixed 5 reads, any data volume
# ===========================================================================


class TestConstantReadCount:
    """Validates: Requirements 9.7"""

    async def _seed(self, store: MetricStore, n_days: int) -> None:
        for offset in range(n_days):
            for key in _SEC_KEYS:
                await store.upsert(_day(offset), key, offset + 1)

    async def test_sum_call_count_is_five_regardless_of_seed_size(self, isolated_db):
        # Tiny seed (2 days).
        small = _CountingStore(_store(isolated_db))
        await self._seed(small, 2)
        small.sum_calls.clear()  # ignore seed writes; count only view()'s reads
        await SecurityMetricsService(metric_store=small).view()

        # Large seed (60 days, 30x more rows).
        big = _CountingStore(_store(isolated_db))
        await self._seed(big, 60)
        big.sum_calls.clear()
        await SecurityMetricsService(metric_store=big).view()

        assert len(small.sum_calls) == 5
        assert len(big.sum_calls) == len(small.sum_calls)  # O(1) w.r.t. volume


# ===========================================================================
# 5. Secret-free serialization (Req 15.8 / Property 3)
# ===========================================================================


class TestSecretFree:
    """Validates: Requirements 15.8"""

    async def test_view_serialization_has_no_forbidden_fields(self, isolated_db):
        store = _store(isolated_db)
        await _seed_day(store, _day(0), {SEC_LOGIN_FAILED: 2, SEC_ADMIN_LOGIN: 1})
        view = await _service(store).view()
        assert isinstance(view, SecurityView)
        assert_no_forbidden_fields(view.model_dump(by_alias=True))


# ===========================================================================
# 6. SecurityAggregateStep — aggregates the lookback window + idempotent (Req 9.1)
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

        # UPSERT of an absolute value → re-running recomputes the same count, not
        # a doubled one (idempotent per closed day).
        assert first == self._COUNTS[SEC_LOGIN_FAILED]
        assert second == first


# ===========================================================================
# 7. SecurityAggregateStep — failure isolation / preserve-last (Req 9.2)
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
        # Pre-seed a known good value on the failing day — it must be preserved.
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
        # Pre-seed the key whose upsert will fail — its value must survive.
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
