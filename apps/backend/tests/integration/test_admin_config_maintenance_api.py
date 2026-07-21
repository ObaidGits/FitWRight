"""Integration tests for Config diagnostics + Maintenance actions (Task 8.4).

Exercises the real ``/api/v1/admin/config`` (read) and ``/api/v1/admin/maintenance/*``
(manage) endpoints end-to-end over an ASGI transport against an isolated temp
database in **hosted** mode, so authN/CSRF/rate-limit/capability all apply.
Reuses the ``_client`` / ``_admin_client`` / ``_login`` / ``_seed`` / ``hosted``
harness from :mod:`tests.integration.test_admin_api`.

Covers:
- Config authz matrix (anon 401, non-admin 403, admin 200) + secret-free body
  (Req 15.1 / 10.2 / 15.8);
- Config access is audited ``admin.config_viewed`` (Req 15.3);
- Config audit-failure -> 500 ``audit_failed`` and NO config returned (Req 15.9);
- Maintenance authz matrix for each of the four actions (Req 18.2);
- Maintenance idempotent/already-running when the underlying lock is held (Req 18.3);
- Maintenance access is audited ``admin.maintenance_action`` with meta.action (Req 18.2);
- No destructive/SQL/config-edit maintenance route exists + config is GET-only (Req 18.5).

**Read-only-admin representation finding.** The capability model
(``app.auth.principal._ROLE_CAPABILITIES``) has exactly two roles: ``user`` (no
capabilities) and ``admin`` (both ``admin.read`` AND ``admin.manage``). There is
no role that grants ``admin.read`` WITHOUT ``admin.manage``, so a "read-only
admin" cannot be represented today. Consequently the authz matrix uses ``user``
as the 403 case and ``admin`` as the 200 case for BOTH config and maintenance;
the "read-only admin is 403 on maintenance but 200 on config" split is documented
here as un-representable rather than tested.

Requirements: 10.2, 10.3, 18.2, 18.3, 18.5, 15.8, 15.1, 15.3, 15.9.
"""

from __future__ import annotations

import pytest

from app.admin.jobs import ROLLUP_LOCK_KEY
from app.admin.repo import get_admin_repo
from app.admin.schemas import assert_no_forbidden_fields
from app.auth.principal import Capabilities, capabilities_for

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import (  # noqa: F401
    _admin_client,
    _client,
    _seed,
    hosted,
)
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_CONFIG_URL = "/api/v1/admin/config"
_MAINTENANCE_ACTIONS = ("refresh-metrics", "run-rollup", "run-cleanup", "run-retention")


def _maint_url(action: str) -> str:
    return f"/api/v1/admin/maintenance/{action}"


# ---------------------------------------------------------------------------
# Read-only-admin representation finding (documented as a test).
# ---------------------------------------------------------------------------


class TestRoleCapabilityModel:
    def test_no_read_only_admin_role_can_be_represented(self):
        """Only ``user`` (none) and ``admin`` (read+manage) exist; no read-only admin.

        This documents WHY the authz matrices below use ``user`` for 403 and
        ``admin`` for 200 on both config and maintenance, rather than a
        read-only-admin that is 200 on config but 403 on maintenance.
        """
        assert capabilities_for("user") == frozenset()
        admin_caps = capabilities_for("admin")
        assert Capabilities.ADMIN_READ in admin_caps
        assert Capabilities.ADMIN_MANAGE in admin_caps
        # There is no role granting read WITHOUT manage.
        read_only = [
            role
            for role in ("user", "admin")
            if Capabilities.ADMIN_READ in capabilities_for(role)
            and Capabilities.ADMIN_MANAGE not in capabilities_for(role)
        ]
        assert read_only == [], "a read-only admin role is not representable today"


# ---------------------------------------------------------------------------
# Config diagnostics - authz + secret-free (Req 15.1 / 10.2 / 15.8)
# ---------------------------------------------------------------------------


class TestConfigAuthz:
    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_CONFIG_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-config@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-config@example.com")
            assert (await client.get(_CONFIG_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_CONFIG_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Req 10.2 / 15.8 / Property 3).
        assert_no_forbidden_fields(body)
        # Secrets appear only as presence booleans.
        assert body["configured"]
        assert all(isinstance(v, bool) for v in body["configured"].values())
        assert all(isinstance(v, bool) for v in body["featureFlags"].values())


# ---------------------------------------------------------------------------
# Config diagnostics - audited (Req 15.3) + audit-failure -> 500 (Req 15.9)
# ---------------------------------------------------------------------------


class TestConfigAudit:
    async def test_config_access_is_audited(self, auth_env, hosted):
        async with _admin_client(auth_env, "cfgaudit@example.com") as client:
            me = (await client.get("/api/v1/users/me")).json()
            resp = await client.get(_CONFIG_URL)
            assert resp.status_code == 200
        rows, _ = await get_admin_repo().list_audit(event="admin.config_viewed")
        assert any(r.actor_user_id == me["id"] for r in rows), (
            "expected an admin.config_viewed audit row for the acting admin"
        )

    async def test_audit_failure_returns_500_and_no_config(self, auth_env, hosted, monkeypatch):
        """When recording the sensitive read fails, endpoint 500s and hides config."""
        from app.auth.audit import AuditEvent, get_audit_service

        service = get_audit_service()
        real_record = service.record

        async def _failing_record(event, **kwargs):
            if event == AuditEvent.ADMIN_CONFIG_VIEWED:
                raise RuntimeError("audit backend down")
            return await real_record(event, **kwargs)

        monkeypatch.setattr(service, "record", _failing_record)

        async with _admin_client(auth_env, "cfgfail@example.com") as client:
            resp = await client.get(_CONFIG_URL)
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "audit_failed"
        # No configuration payload leaks on the error path.
        assert "configured" not in resp.text
        assert "killSwitches" not in resp.text


# ---------------------------------------------------------------------------
# Maintenance - authz matrix per action (Req 18.2)
# ---------------------------------------------------------------------------


class TestMaintenanceAuthz:
    @pytest.mark.parametrize("action", _MAINTENANCE_ACTIONS)
    async def test_anonymous_401(self, auth_env, hosted, action):
        async with _client() as client:
            # No session -> CSRF middleware allows through to the guard -> 401.
            assert (await client.post(_maint_url(action))).status_code == 401

    @pytest.mark.parametrize("action", _MAINTENANCE_ACTIONS)
    async def test_non_admin_403(self, auth_env, hosted, action):
        await _seed(auth_env, "plain-maint@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-maint@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(_maint_url(action), headers={"X-CSRF-Token": csrf})
        assert resp.status_code == 403

    @pytest.mark.parametrize("action", _MAINTENANCE_ACTIONS)
    async def test_admin_manage_200_with_result(self, auth_env, hosted, action):
        async with _admin_client(auth_env) as client:
            resp = await client.post(_maint_url(action))
        assert resp.status_code == 200
        body = resp.json()
        assert body["action"] == action
        assert body["status"] in {"started", "already_running", "disabled"}
        assert set(body.keys()) == {"action", "status"}


# ---------------------------------------------------------------------------
# Maintenance - idempotent / already-running (Req 18.3)
# ---------------------------------------------------------------------------


class TestMaintenanceIdempotency:
    async def test_lock_held_returns_already_running(self, auth_env, hosted):
        """Holding the rollup single-flight lock -> run-rollup is already_running."""
        from app.auth.runtime import get_kvstore

        kv = get_kvstore()
        held = kv.lock(ROLLUP_LOCK_KEY, ttl_seconds=60, blocking=False)
        async with held as acquired:
            assert acquired is True
            async with _admin_client(auth_env) as client:
                resp = await client.post(_maint_url("run-rollup"))
        assert resp.status_code == 200
        assert resp.json()["status"] == "already_running"

    async def test_repeated_invocation_is_safe(self, auth_env, hosted):
        """Two sequential invocations both succeed with a valid maintenance status."""
        async with _admin_client(auth_env) as client:
            first = await client.post(_maint_url("run-retention"))
            second = await client.post(_maint_url("run-retention"))
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["status"] in {"started", "already_running", "disabled"}
        assert second.json()["status"] in {"started", "already_running", "disabled"}


# ---------------------------------------------------------------------------
# Maintenance - audited (Req 18.2)
# ---------------------------------------------------------------------------


class TestMaintenanceAudit:
    async def test_successful_action_is_audited_with_meta_action(self, auth_env, hosted):
        async with _admin_client(auth_env, "maintaudit@example.com") as client:
            me = (await client.get("/api/v1/users/me")).json()
            resp = await client.post(_maint_url("run-rollup"))
            assert resp.status_code == 200
        rows, _ = await get_admin_repo().list_audit(event="admin.maintenance_action")
        mine = [r for r in rows if r.actor_user_id == me["id"]]
        assert mine, "expected an admin.maintenance_action audit row for the actor"
        assert any((r.meta or {}).get("action") == "run-rollup" for r in mine)


# ---------------------------------------------------------------------------
# Maintenance - no destructive/SQL/config-edit route (Req 18.5)
# ---------------------------------------------------------------------------


class TestMaintenanceNoDestructiveSurface:
    def _admin_routes(self):
        from app.main import app

        return [r for r in app.routes if getattr(r, "path", "").startswith("/api/v1/admin")]

    def test_only_four_maintenance_routes_exist(self):
        routes = self._admin_routes()
        maint = {
            r.path
            for r in routes
            if r.path.startswith("/api/v1/admin/maintenance")
        }
        expected = {f"/api/v1/admin/maintenance/{a}" for a in _MAINTENANCE_ACTIONS}
        assert maint == expected, f"unexpected maintenance routes: {maint - expected}"

    def test_no_maintenance_delete_or_sql_route(self):
        routes = self._admin_routes()
        for r in routes:
            path = r.path.lower()
            if "/maintenance" in path:
                for token in ("delete", "sql", "query", "exec", "flush", "drop"):
                    assert token not in path, f"destructive maintenance route exposed: {r.path}"

    def test_config_is_get_only_no_mutation_verb(self):
        """``/admin/config`` accepts only GET - no PUT/POST/PATCH/DELETE (Req 10.3)."""
        routes = self._admin_routes()
        config_methods: set[str] = set()
        for r in routes:
            if getattr(r, "path", "") == _CONFIG_URL:
                config_methods |= set(getattr(r, "methods", set()) or set())
        # HEAD/OPTIONS may be auto-added; the only mutating-capable verb allowed is none.
        mutating = {"POST", "PUT", "PATCH", "DELETE"} & config_methods
        assert mutating == set(), f"config endpoint exposes mutating verbs: {mutating}"
        assert "GET" in config_methods
