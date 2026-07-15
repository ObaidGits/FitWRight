"""Backend job-cycle E2E for ``POST /api/v1/internal/run-jobs`` (Task 19.3).

Exercises the full admin job cycle end-to-end over an ASGI transport against an
isolated temp database + a fresh in-process KVStore:

- one authorized call runs the whole admin pipeline (rollup → purge →
  audit_retention → alerting), returns 200, and populates the durable per-job KV
  run markers for ``rollup``/``purge``/``audit_retention`` (Req 1.2, 2.1, 3.4);
- the nested ``admin`` result carries an ``alerting`` key whose ``status`` is a
  real evaluation outcome (``ok``/``locked``) — the minimal threshold
  Alerting_Job actually ran on the tick (Req 12.2);
- the cycle is single-flighted + idempotent: back-to-back and concurrent calls
  both return 200 with no error and leave the markers consistent (Req 15.8);
- with seeded activity (a user + a recorded metric) the cycle still completes
  and the markers/keys are populated (Req 7.1, 9.1).

Requirements: 1.2, 2.1, 7.1, 9.1, 12.2, 15.8

DB / metric-store / kvstore wiring
----------------------------------
``auth_env`` (integration conftest) rebinds the process auth singletons +
KVStore to the isolated temp DB and a fresh in-process store. The admin jobs
also read/write through the process ``MetricStore`` singleton (run markers,
snapshots) which lazily binds to ``app.database.db`` on first build — so the
``job_cycle_env`` fixture calls :func:`reset_metric_store` after the DB swap to
force a rebuild against the temp DB. ``run_admin_jobs()`` is called by the
endpoint with no explicit kvstore, so it resolves the same container-bound
``get_kvstore()`` for its single-flight locks; markers are read back through the
same rebound ``MetricStore``.
"""

from __future__ import annotations

import asyncio

import pytest

from app.admin.job_markers import job_marker_name
from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

TOKEN = "super-secret-internal-token-0123456789"
STRONG_PW = "correct-horse-battery-staple-9"

# The stable per-job marker names the cycle records (run_admin_jobs runs these
# three through _run_job_with_markers; alerting runs unwrapped, no marker).
MARKED_JOBS = ("rollup", "purge", "audit_retention")


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setattr(app_settings, "internal_job_token", TOKEN)
    return TOKEN


@pytest.fixture
async def job_cycle_env(auth_env):
    """Isolated DB with the process MetricStore singleton rebound to it.

    ``auth_env`` swaps ``app.database.db`` for the temp DB + a fresh KVStore;
    resetting the MetricStore singleton here forces ``get_metric_store()`` (used
    by the run-marker writer inside ``run_admin_jobs``) to rebuild against that
    temp DB rather than any leftover instance.
    """
    from app.admin.metric_store import reset_metric_store

    reset_metric_store()
    yield auth_env
    reset_metric_store()


async def _seed_user(db, email: str = "cycle@example.com"):
    return await create_user(
        email=email,
        name="Cycle",
        password_hash=get_password_service().hash_password(STRONG_PW),
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


async def _run_jobs(client: AsyncClient):
    return await client.post(
        "/api/v1/internal/run-jobs",
        headers={"X-Internal-Job-Token": TOKEN},
    )


async def _read_markers() -> dict[str, dict]:
    """Read the per-job run markers back through the rebound MetricStore."""
    from app.admin.metric_store import get_metric_store

    store = get_metric_store()
    markers: dict[str, dict] = {}
    for job in MARKED_JOBS:
        marker = await store.snapshot_get(job_marker_name(job))
        if marker is not None:
            markers[job] = marker
    return markers


# ---------------------------------------------------------------------------
# 1. Full cycle populates the per-job run markers
# ---------------------------------------------------------------------------


class TestFullCyclePopulatesMarkers:
    async def test_single_run_writes_all_marked_job_markers(
        self, job_cycle_env, internal_token
    ):
        async with _client() as client:
            resp = await _run_jobs(client)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        markers = await _read_markers()
        # All three marker-wrapped jobs recorded a completed run.
        assert set(markers) == set(MARKED_JOBS)
        for job, marker in markers.items():
            assert marker["job"] == job
            # A completed run: last_run stamped, running_since cleared, and an
            # outcome recorded (success/skipped/failure — never left unknown).
            assert marker["last_run"]
            assert marker["running_since"] is None
            assert marker["last_outcome"] in {"success", "skipped", "failure"}


# ---------------------------------------------------------------------------
# 2. Alerting ran (nested under the ``admin`` key of the response)
# ---------------------------------------------------------------------------


class TestAlertingRan:
    async def test_admin_result_includes_alerting_status(
        self, job_cycle_env, internal_token
    ):
        async with _client() as client:
            resp = await _run_jobs(client)
        assert resp.status_code == 200
        body = resp.json()

        # The admin jobs result is nested under "admin" (see internal.run_jobs).
        admin = body["admin"]
        assert isinstance(admin, dict)
        # The whole pipeline is present in the nested result.
        for key in ("rollup", "purge", "audit_retention", "alerting"):
            assert key in admin, f"missing {key} in admin result: {admin}"

        alerting = admin["alerting"]
        assert isinstance(alerting, dict)
        # Alerting ran a real evaluation this tick (not the isolated-error path).
        assert alerting.get("status") in {"ok", "locked"}


# ---------------------------------------------------------------------------
# 3. Idempotent / single-flighted
# ---------------------------------------------------------------------------


class TestIdempotentSingleFlight:
    async def test_back_to_back_calls_both_succeed(self, job_cycle_env, internal_token):
        async with _client() as client:
            r1 = await _run_jobs(client)
            r2 = await _run_jobs(client)

        assert r1.status_code == r2.status_code == 200
        assert r1.json()["status"] == r2.json()["status"] == "ok"

        # Markers remain consistent after a second cycle: still present, still
        # completed (running_since cleared), no crash / stuck-running state.
        markers = await _read_markers()
        assert set(markers) == set(MARKED_JOBS)
        for marker in markers.values():
            assert marker["running_since"] is None
            assert marker["last_outcome"] in {"success", "skipped", "failure"}

    async def test_concurrent_calls_are_single_flighted(
        self, job_cycle_env, internal_token
    ):
        async with _client() as c1, _client() as c2:
            r1, r2 = await asyncio.gather(_run_jobs(c1), _run_jobs(c2))

        # Both requests succeed; the per-job KVStore locks make overlapping runs
        # safe (a job whose lock is held simply reports "locked" / no-ops) — no
        # error, no double-run crash.
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["status"] == r2.json()["status"] == "ok"

        markers = await _read_markers()
        assert set(markers) == set(MARKED_JOBS)
        for marker in markers.values():
            assert marker["running_since"] is None


# ---------------------------------------------------------------------------
# 4. Expected keys/markers after seeding activity
# ---------------------------------------------------------------------------


class TestCycleWithSeededActivity:
    async def test_cycle_completes_and_populates_markers_with_activity(
        self, job_cycle_env, internal_token
    ):
        # Seed a real user + record an admin metric signal so the rollup pipeline
        # has something to flush/aggregate this tick.
        await _seed_user(job_cycle_env)
        from app.admin.metrics import get_admin_metrics

        get_admin_metrics().record_action("user_view", "ok")

        async with _client() as client:
            resp = await _run_jobs(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

        # The full admin pipeline ran and the durable run markers are populated.
        admin = body["admin"]
        for key in ("rollup", "purge", "audit_retention", "alerting"):
            assert key in admin

        markers = await _read_markers()
        assert set(markers) == set(MARKED_JOBS)
        for marker in markers.values():
            assert marker["last_run"]
            assert marker["running_since"] is None
