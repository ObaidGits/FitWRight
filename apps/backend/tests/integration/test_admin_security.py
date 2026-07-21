"""Security + observability sign-off for the P2 Admin surface (Task 9.1/9.2, R14).

- CSRF required on every admin mutation (incl. delete) in hosted mode;
- log/CRLF-injection via search ``q`` is sanitized (no error, no injection);
- the field allowlist holds even when a user has an API key (aiConfigured only);
- the destructive kill-switch (ADMIN_DESTRUCTIVE_ACTIONS) refuses delete/restore;
- bulk-disable is bounded;
- admin metrics: ``admin_action_total{action,result}`` + the internal snapshot.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _login

pytestmark = pytest.mark.integration


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


async def _seed(db, email, *, role="user"):
    return await create_user(
        email=email,
        name="U",
        password_hash=get_password_service().hash_password(STRONG_PW),
        role=role,
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00",
        db=db,
    )


class TestCsrf:
    async def test_mutation_without_csrf_header_rejected(self, auth_env, hosted):
        await _seed(auth_env, "admin@example.com", role="admin")
        target = await _seed(auth_env, "t@example.com")
        async with _client() as client:
            await _login(client, "admin@example.com")
            # No X-CSRF-Token header on a state-changing request -> 403 csrf_failed.
            resp = await client.post(f"/api/v1/admin/users/{target.id}/disable")
        assert resp.status_code == 403

    async def test_delete_requires_csrf(self, auth_env, hosted):
        await _seed(auth_env, "admin2@example.com", role="admin")
        target = await _seed(auth_env, "t2@example.com")
        async with _client() as client:
            await _login(client, "admin2@example.com")
            resp = await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "t2@example.com"}
            )
        assert resp.status_code == 403


class TestLogInjection:
    async def test_crlf_search_is_sanitized(self, auth_env, hosted):
        await _seed(auth_env, "admin3@example.com", role="admin")
        async with _client() as client:
            await _login(client, "admin3@example.com")
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
            # A CRLF-laden search must not error or inject - just returns a page.
            resp = await client.get("/api/v1/admin/users?q=evil%0d%0ainjected")
        assert resp.status_code == 200


class TestAllowlistWithApiKey:
    async def test_ai_configured_but_no_key_leaks(self, auth_env, hosted):
        from app.admin.schemas import assert_no_forbidden_fields

        admin = await _seed(auth_env, "admin4@example.com", role="admin")
        target = await _seed(auth_env, "keyed@example.com")
        # Give the target a stored (encrypted) API key.
        auth_env.set_api_key_ciphertext(target.id, "openai", "ct-secret")
        async with _client() as client:
            await _login(client, "admin4@example.com")
            resp = await client.get(f"/api/v1/admin/users/{target.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert_no_forbidden_fields(body)
        assert body["aiConfigured"] is True
        assert "ct-secret" not in resp.text


class TestKillSwitch:
    async def test_delete_refused_when_destructive_off(self, auth_env, hosted, monkeypatch):
        monkeypatch.setattr(app_settings, "admin_destructive_actions", False)
        await _seed(auth_env, "admin5@example.com", role="admin")
        target = await _seed(auth_env, "safe@example.com")
        async with _client() as client:
            await _login(client, "admin5@example.com")
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
            resp = await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "safe@example.com"}
            )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "destructive_disabled"


class TestBulkBounds:
    async def test_bulk_disable_batch_too_large(self, auth_env, hosted, monkeypatch):
        monkeypatch.setattr(app_settings, "admin_bulk_disable_max", 2)
        await _seed(auth_env, "admin6@example.com", role="admin")
        async with _client() as client:
            await _login(client, "admin6@example.com")
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/admin/users/bulk-disable", json={"ids": ["a", "b", "c"]}
            )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "batch_too_large"


class TestErasure:
    async def test_no_pii_in_audit_after_soft_delete(self, auth_env, hosted):
        """H3: the retained audit trail must carry no PII (email) - ids only (R8.4)."""
        from app.admin.repo import get_admin_repo

        await _seed(auth_env, "erase-admin@example.com", role="admin")
        target = await _seed(auth_env, "erase-me@example.com")
        async with _client() as client:
            await _login(client, "erase-admin@example.com")
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
            r = await client.post(
                f"/api/v1/admin/users/{target.id}/delete", json={"email": "erase-me@example.com"}
            )
            assert r.status_code == 200
        rows, _ = await get_admin_repo().list_audit(target=target.id)
        assert rows, "soft-delete should be audited"
        for row in rows:
            # The event ties to the opaque target id; no email persists in meta.
            blob = str(row.meta or {})
            assert "erase-me@example.com" not in blob
            assert "@" not in blob  # no address of any kind survives in the trail


class TestDashboardScaling:
    async def test_stats_bootstrap_before_any_rollup(self, auth_env, hosted):
        # H1: /stats returns correct totals even before the first rollup
        # (one-time bootstrap) and marks the snapshot fresh.
        from tests.integration.test_admin_api import _admin_client

        await _seed(auth_env, "s1@example.com")
        async with _admin_client(auth_env, "statsadmin@example.com") as client:
            resp = await client.get("/api/v1/admin/stats")
            assert resp.status_code == 200
            body = resp.json()
            assert body["totalUsers"] >= 2  # admin + s1
            assert body["stale"] is False

    async def test_stats_reads_snapshot_after_rollup(self, auth_env, hosted):
        from app.admin.jobs import run_rollup_job
        from tests.integration.test_admin_api import _admin_client

        await _seed(auth_env, "s2@example.com")
        await run_rollup_job()  # populates the O(1) totals snapshot
        async with _admin_client(auth_env, "statsadmin2@example.com") as client:
            resp = await client.get("/api/v1/admin/stats")
            assert resp.status_code == 200
            assert resp.json()["stale"] is False


class TestObservability:
    async def test_admin_action_metric_recorded(self, auth_env, hosted):
        from app.admin.metrics import get_admin_metrics

        await _seed(auth_env, "admin7@example.com", role="admin")
        target = await _seed(auth_env, "obs@example.com")
        async with _client() as client:
            await _login(client, "admin7@example.com")
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
            await client.post(f"/api/v1/admin/users/{target.id}/disable")
        snap = get_admin_metrics().snapshot()
        assert snap["admin_action_total"].get("disable", {}).get("ok", 0) >= 1
        # request latency + status buckets were recorded by the middleware.
        assert snap["counters"].get("request_2xx", 0) >= 1

    async def test_authz_denied_counter(self, auth_env, hosted):
        from app.admin.metrics import get_admin_metrics

        await _seed(auth_env, "plainobs@example.com", role="user")
        before = get_admin_metrics().snapshot()["counters"].get("authz_denied", 0)
        async with _client() as client:
            await _login(client, "plainobs@example.com")
            await client.get("/api/v1/admin/stats")
        after = get_admin_metrics().snapshot()["counters"].get("authz_denied", 0)
        assert after == before + 1
