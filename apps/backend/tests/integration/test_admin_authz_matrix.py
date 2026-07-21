"""Authz-matrix integration tests over every new admin read + maintenance action.

Task 18.1. Exercises the real ``/api/v1/admin/*`` surface end-to-end over an
ASGI transport against an isolated temp database with real sessions (no
dependency overrides), in **hosted** mode so authN/CSRF/rate-limit/capability
all apply. Parametrized over every new read endpoint and every maintenance
action:

- **Read authz (Req 15.1).** For each new read endpoint: anon -> 401,
  authenticated non-admin -> 403, admin -> 200.
- **Maintenance authz (Req 15.1).** For each maintenance action (POST + CSRF):
  anon -> 401, non-admin -> 403, admin (``admin.manage``) -> 200 with a
  ``started``/``already_running``/``disabled`` outcome.
- **Sensitive_Endpoint audit (Req 15.3).** ``GET /config`` records an
  ``admin.config_viewed`` audit entry naming the acting admin; every maintenance
  action records an ``admin.maintenance_action`` entry naming the acting admin +
  the specific action.
- **Per-admin rate limit (Req 15.2).** The read bucket is dialled down for one
  endpoint to show the (limit+1)th request returns 429 + ``Retry-After``.

The read endpoints below read through the process-wide ``MetricStore`` singleton
(lazily bound to ``app.database.db``); the ``admin_env`` fixture resets it after
the DB swap so it rebinds to the isolated temp DB (mirrors the ``resume_env`` /
``retention_env`` pattern).

Requirements: 15.1, 15.2, 15.3.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.auth.ratelimit import RateLimitRule
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _login

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# The surface under test (Task 18.1)
# ---------------------------------------------------------------------------

# Every new read endpoint (all ``require_admin_read``). ``/config`` is a
# Sensitive_Endpoint audited ``admin.config_viewed`` (asserted separately).
READ_ENDPOINTS = [
    "/api/v1/admin/health",
    "/api/v1/admin/jobs",
    "/api/v1/admin/config",
    "/api/v1/admin/ai-analytics",
    "/api/v1/admin/errors",
    "/api/v1/admin/performance",
    "/api/v1/admin/storage",
    "/api/v1/admin/security",
    "/api/v1/admin/kpis",
    "/api/v1/admin/analytics/feature-usage",
    "/api/v1/admin/analytics/resumes",
]

# Every maintenance action (all ``require_admin_manage``, POST, audited
# ``admin.maintenance_action`` with the specific action in ``meta``).
MAINTENANCE_ACTIONS = [
    ("/api/v1/admin/maintenance/refresh-metrics", "refresh-metrics"),
    ("/api/v1/admin/maintenance/run-rollup", "run-rollup"),
    ("/api/v1/admin/maintenance/run-cleanup", "run-cleanup"),
    ("/api/v1/admin/maintenance/run-retention", "run-retention"),
]

_MAINTENANCE_STATUSES = {"started", "already_running", "disabled"}


# ---------------------------------------------------------------------------
# Harness (mirrors tests/integration/test_admin_api.py + resume_env pattern)
# ---------------------------------------------------------------------------


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


@pytest.fixture
def admin_env(auth_env):
    """Isolated DB with the process ``MetricStore`` singleton rebound to it.

    ``auth_env`` swaps ``app.database.db`` for the temp DB; resetting the
    ``MetricStore`` here forces ``get_metric_store()`` (used by the health/
    analytics/errors/performance/kpis reads and the maintenance jobs) to rebuild
    against that temp DB rather than any leftover instance.
    """
    from app.admin.metric_store import reset_metric_store

    reset_metric_store()
    yield auth_env
    reset_metric_store()


async def _seed(db, email, *, role="user", status="active", verified=True, name="U"):
    return await create_user(
        email=email,
        name=name,
        password_hash=get_password_service().hash_password(STRONG_PW),
        role=role,
        status=status,
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


@asynccontextmanager
async def _admin_client(db, email="admin@example.com"):
    """Yield ``(client, admin)`` - a logged-in admin with the csrf header set."""
    admin = await _seed(db, email, role="admin")
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client, admin


@asynccontextmanager
async def _user_client(db, email="plain@example.com"):
    """Yield a logged-in non-admin with the csrf header set (so a POST reaches
    the capability check rather than being short-circuited by CSRF)."""
    await _seed(db, email, role="user")
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client


# ---------------------------------------------------------------------------
# 1) Parametrized read authz (Req 15.1)
# ---------------------------------------------------------------------------


class TestReadAuthzMatrix:
    """Validates: Requirement 15.1 (Capability_Gate on every new read)."""

    @pytest.mark.parametrize("path", READ_ENDPOINTS)
    async def test_anonymous_401(self, admin_env, hosted, path):
        async with _client() as client:
            assert (await client.get(path)).status_code == 401

    @pytest.mark.parametrize("path", READ_ENDPOINTS)
    async def test_non_admin_403(self, admin_env, hosted, path):
        await _seed(admin_env, "plain@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain@example.com")
            assert (await client.get(path)).status_code == 403

    @pytest.mark.parametrize("path", READ_ENDPOINTS)
    async def test_admin_200(self, admin_env, hosted, path):
        async with _admin_client(admin_env) as (client, _admin):
            resp = await client.get(path)
        assert resp.status_code == 200, (path, resp.text)


# ---------------------------------------------------------------------------
# 2) Parametrized maintenance-action authz (Req 15.1)
# ---------------------------------------------------------------------------


class TestMaintenanceAuthzMatrix:
    """Validates: Requirement 15.1 (Capability_Gate on every maintenance action)."""

    @pytest.mark.parametrize("path,action", MAINTENANCE_ACTIONS)
    async def test_anonymous_401(self, admin_env, hosted, path, action):
        async with _client() as client:
            # No session -> CSRF is not enforced; the guard is the first stop -> 401.
            assert (await client.post(path)).status_code == 401

    @pytest.mark.parametrize("path,action", MAINTENANCE_ACTIONS)
    async def test_non_admin_403(self, admin_env, hosted, path, action):
        async with _user_client(admin_env) as client:
            # Authenticated with a valid CSRF token but lacking ``admin.manage``.
            assert (await client.post(path)).status_code == 403

    @pytest.mark.parametrize("path,action", MAINTENANCE_ACTIONS)
    async def test_admin_200(self, admin_env, hosted, path, action):
        async with _admin_client(admin_env) as (client, _admin):
            resp = await client.post(path)
        assert resp.status_code == 200, (path, resp.text)
        body = resp.json()
        assert body["action"] == action
        assert body["status"] in _MAINTENANCE_STATUSES, body


# ---------------------------------------------------------------------------
# 3) Sensitive_Endpoint + maintenance audit (Req 15.3)
# ---------------------------------------------------------------------------


async def _audit_items(client, *, event, actor):
    resp = await client.get(f"/api/v1/admin/audit?event={event}&actor={actor}")
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


class TestSensitiveEndpointAudit:
    """Validates: Requirement 15.3 (Sensitive_Endpoint access -> audit_log entry
    with acting admin + action)."""

    async def test_config_view_is_audited(self, admin_env, hosted):
        async with _admin_client(admin_env) as (client, admin):
            assert (await client.get("/api/v1/admin/config")).status_code == 200
            items = await _audit_items(
                client, event="admin.config_viewed", actor=admin.id
            )
        assert items, "GET /config must record an admin.config_viewed audit entry"
        entry = items[0]
        assert entry["event"] == "admin.config_viewed"
        assert entry["actorUserId"] == admin.id  # acting admin identified

    @pytest.mark.parametrize("path,action", MAINTENANCE_ACTIONS)
    async def test_maintenance_action_is_audited(self, admin_env, hosted, path, action):
        async with _admin_client(admin_env) as (client, admin):
            assert (await client.post(path)).status_code == 200
            items = await _audit_items(
                client, event="admin.maintenance_action", actor=admin.id
            )
        # Exactly the one action we just invoked is recorded for this admin.
        matching = [i for i in items if (i.get("meta") or {}).get("action") == action]
        assert matching, f"{path} must record an admin.maintenance_action for {action}"
        entry = matching[0]
        assert entry["actorUserId"] == admin.id  # acting admin identified
        assert entry["meta"]["action"] == action  # specific action identified


# ---------------------------------------------------------------------------
# 4) Per-admin rate limit (Req 15.2)
# ---------------------------------------------------------------------------


class TestPerAdminRateLimit:
    """Validates: Requirement 15.2 (per-admin rate limit -> 429 + retry hint).

    The production read bucket is 240/60s - impractical to exhaust deterministically
    in a test. We dial the read rule down to 2/60s (the limiter reads the module
    global at call time) so the 3rd read on one endpoint trips the limit, proving
    the per-admin limiter is wired on the new read surface and returns a 429 with a
    ``Retry-After`` hint. The write bucket shares the same limiter path.
    """

    async def test_read_limit_returns_429_with_retry_after(
        self, admin_env, hosted, monkeypatch
    ):
        monkeypatch.setattr(
            "app.admin.deps._READ_RULE", RateLimitRule(limit=2, window_seconds=60)
        )
        async with _admin_client(admin_env) as (client, _admin):
            first = await client.get("/api/v1/admin/health")
            second = await client.get("/api/v1/admin/health")
            third = await client.get("/api/v1/admin/health")

        assert first.status_code == 200
        assert second.status_code == 200
        assert third.status_code == 429, third.text
        assert third.json()["error"]["code"] == "rate_limited"
        retry_after = third.headers.get("Retry-After")
        assert retry_after is not None and int(retry_after) >= 1
