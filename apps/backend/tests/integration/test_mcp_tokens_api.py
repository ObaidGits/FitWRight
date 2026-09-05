"""Integration tests for the browser-authenticated MCP token API (Task 3).

Exercises the real ``/api/v1/mcp/tokens`` surface end-to-end over an ASGI
transport against an isolated temp database with real sessions (no dependency
overrides), in **hosted** mode so authN/CSRF all apply, mirroring the
``test_admin_authz_matrix`` harness.

Coverage (task brief, in order):

1. ``MCP_ENABLED=false`` kill-switch -> all three endpoints 404.
2. Unauthenticated POST -> 401.
3. Authenticated create -> 201, raw ``fw_`` token appears exactly once;
   listing shows ``label`` + masked fields but never ``token``/``token_hash``.
4. Owner revoke -> service ``verify()`` returns None; listing shows
   ``revoked_at``.
5. Cross-user scoping: B's list is empty, B cannot revoke A's token.
6. Label bounds: >100 chars -> 422, empty -> 422.
7. Audit entries recorded (``mcp_token.created`` / ``mcp_token.revoked``).

Plus the inherited CSRF gate: a session-authenticated mutation without the
``X-CSRF-Token`` header is rejected with 403 before reaching the route.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.auth.accounts import create_user
from app.auth.mcp_tokens import get_mcp_token_service, reset_mcp_token_service
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app
from app.models import AuditLog

from tests.integration.test_auth_api import STRONG_PW, _login

pytestmark = pytest.mark.integration

TOKENS = "/api/v1/mcp/tokens"


def _client() -> AsyncClient:
    # https base_url so the httpx cookie jar stores/returns the Secure __Host- cookie.
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


# ---------------------------------------------------------------------------
# Harness (mirrors tests/integration/test_admin_authz_matrix.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


@pytest.fixture
async def mcp_env(auth_env, monkeypatch):
    """Isolated auth stack with MCP enabled and the token service rebound.

    ``auth_env`` swaps ``app.database.db`` for the temp DB; resetting the MCP
    token singleton here forces it to rebuild against that temp DB (it binds
    ``db.session_factory`` lazily) instead of any leftover instance.
    """
    monkeypatch.setattr(app_settings, "mcp_enabled", True)
    reset_mcp_token_service()
    yield auth_env
    reset_mcp_token_service()


async def _seed(db, email, *, verified=True):
    return await create_user(
        email=email,
        name="U",
        password_hash=get_password_service().hash_password(STRONG_PW),
        role="user",
        status="active",
        email_verified_at="2024-01-01T00:00:00+00:00" if verified else None,
        db=db,
    )


@asynccontextmanager
async def _user_client(db, email):
    """Yield ``(client, user)`` - logged in, CSRF header set."""
    user = await _seed(db, email)
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client, user


async def _audit_rows(db, event, actor_id):
    async with db.session_factory() as s:
        return (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.event == event, AuditLog.actor_user_id == actor_id
                    )
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# 1) Kill-switch: MCP_ENABLED=false 404s the whole surface
# ---------------------------------------------------------------------------


class TestKillSwitch:
    async def test_disabled_404s_all_three(self, auth_env, hosted, monkeypatch):
        monkeypatch.setattr(app_settings, "mcp_enabled", False)
        async with _client() as client:
            assert (
                await client.post(TOKENS, json={"label": "Claude Desktop"})
            ).status_code == 404
            assert (await client.get(TOKENS)).status_code == 404
            assert (await client.delete(f"{TOKENS}/some-id")).status_code == 404


# ---------------------------------------------------------------------------
# 2) Unauthenticated -> 401
# ---------------------------------------------------------------------------


class TestAuthentication:
    async def test_unauthenticated_post_401(self, mcp_env, hosted):
        async with _client() as client:
            resp = await client.post(TOKENS, json={"label": "Claude Desktop"})
        assert resp.status_code == 401

    async def test_unauthenticated_get_401(self, mcp_env, hosted):
        async with _client() as client:
            assert (await client.get(TOKENS)).status_code == 401

    async def test_unauthenticated_delete_401(self, mcp_env, hosted):
        async with _client() as client:
            assert (await client.delete(f"{TOKENS}/some-id")).status_code == 401


# ---------------------------------------------------------------------------
# 3) Create -> 201, raw shown exactly once; list is masked
# ---------------------------------------------------------------------------


class TestCreateAndList:
    async def test_create_returns_raw_once_and_list_is_masked(
        self, mcp_env, hosted
    ):
        async with _user_client(mcp_env, "a@example.com") as (client, _user):
            resp = await client.post(TOKENS, json={"label": "Claude Desktop"})
            assert resp.status_code == 201, resp.text
            body = resp.json()
            raw = body["token"]
            assert raw.startswith("fw_")
            # The raw token appears exactly once in the creation response...
            assert json.dumps(body).count(raw) == 1
            assert body["label"] == "Claude Desktop"
            assert body["id"]
            assert body["created_at"]
            assert "expires_at" in body

            # ...and never in the listing, which is masked.
            listed = await client.get(TOKENS)
            assert listed.status_code == 200, listed.text
            items = listed.json()["items"]
            assert len(items) == 1
            item = items[0]
            assert item["label"] == "Claude Desktop"
            assert item["id"] == body["id"]
            assert "token" not in item
            assert "token_hash" not in item
            assert raw not in listed.text

    async def test_ttl_days_override_sets_expiry(self, mcp_env, hosted):
        async with _user_client(mcp_env, "a@example.com") as (client, _user):
            resp = await client.post(
                TOKENS, json={"label": "Short-lived", "ttl_days": 30}
            )
            assert resp.status_code == 201
            assert resp.json()["expires_at"] is not None

    async def test_create_rejects_out_of_range_ttl(self, mcp_env, hosted):
        async with _user_client(mcp_env, "a@example.com") as (client, _user):
            assert (
                await client.post(TOKENS, json={"label": "X", "ttl_days": 0})
            ).status_code == 422
            assert (
                await client.post(TOKENS, json={"label": "X", "ttl_days": 9999})
            ).status_code == 422


# ---------------------------------------------------------------------------
# 4) Owner revoke
# ---------------------------------------------------------------------------


class TestRevoke:
    async def test_owner_revoke_invalidates_token(self, mcp_env, hosted):
        svc = get_mcp_token_service()
        async with _user_client(mcp_env, "a@example.com") as (client, user):
            created = (
                await client.post(TOKENS, json={"label": "Revoke me"})
            ).json()
            raw, token_id = created["token"], created["id"]

            assert (await svc.verify(raw)) is not None

            resp = await client.delete(f"{TOKENS}/{token_id}")
            assert resp.status_code == 200, resp.text
            assert resp.json() == {"revoked": True}

            assert await svc.verify(raw) is None

            listed = (await client.get(TOKENS)).json()["items"]
            assert listed[0]["revoked_at"] is not None

            # Revoking twice: the second DELETE 404s (no such *active* token).
            assert (
                await client.delete(f"{TOKENS}/{token_id}")
            ).status_code == 404


# ---------------------------------------------------------------------------
# 5) Cross-user scoping
# ---------------------------------------------------------------------------


class TestUserScoping:
    async def test_user_b_cannot_see_or_revoke_user_a_tokens(
        self, mcp_env, hosted
    ):
        svc = get_mcp_token_service()
        async with _user_client(mcp_env, "a@example.com") as (client_a, _a):
            created = (
                await client_a.post(TOKENS, json={"label": "A's token"})
            ).json()
            raw, token_id = created["token"], created["id"]

        async with _user_client(mcp_env, "b@example.com") as (client_b, _b):
            # B's listing is empty: A's token is invisible.
            listed = await client_b.get(TOKENS)
            assert listed.status_code == 200
            assert listed.json()["items"] == []

            # B's revoke of A's token fails and revokes nothing.
            resp = await client_b.delete(f"{TOKENS}/{token_id}")
            assert resp.status_code == 404, resp.text

        assert await svc.verify(raw) is not None


# ---------------------------------------------------------------------------
# 6) Label bounds
# ---------------------------------------------------------------------------


class TestLabelValidation:
    @pytest.mark.parametrize("label", ["", "x" * 101])
    async def test_invalid_labels_rejected(self, mcp_env, hosted, label):
        async with _user_client(mcp_env, "a@example.com") as (client, _user):
            resp = await client.post(TOKENS, json={"label": label})
        assert resp.status_code == 422, (label, resp.text)

    async def test_max_length_label_accepted(self, mcp_env, hosted):
        async with _user_client(mcp_env, "a@example.com") as (client, _user):
            resp = await client.post(TOKENS, json={"label": "x" * 100})
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# 7) Audit trail
# ---------------------------------------------------------------------------


class TestAudit:
    async def test_create_and_revoke_are_audited(self, mcp_env, hosted):
        async with _user_client(mcp_env, "a@example.com") as (client, user):
            created = (
                await client.post(TOKENS, json={"label": "Audited"})
            ).json()
            token_id = created["id"]
            assert (
                await client.delete(f"{TOKENS}/{token_id}")
            ).status_code == 200

            created_rows = await _audit_rows(
                mcp_env, "mcp_token.created", user.id
            )
            revoked_rows = await _audit_rows(
                mcp_env, "mcp_token.revoked", user.id
            )

        assert len(created_rows) == 1, "create must record mcp_token.created"
        assert created_rows[0].actor_user_id == user.id
        # The raw token must never reach the audit trail.
        assert created["token"] not in json.dumps(created_rows[0].meta or {})

        assert len(revoked_rows) == 1, "revoke must record mcp_token.revoked"
        assert revoked_rows[0].actor_user_id == user.id


# ---------------------------------------------------------------------------
# Mount precedence: the FastMCP mount must not shadow the REST routes
# ---------------------------------------------------------------------------


class TestMountPrecedence:
    async def test_token_routes_registered_before_mcp_mount(self):
        """When MCP_ENABLED is true at import the /api/v1/mcp mount exists.

        Starlette matches routes in registration order, so the token REST
        routes must be registered BEFORE the prefix-mount or they 404 inside
        the FastMCP app. (Only runs in an MCP-enabled process; with the
        kill-switch off there is no mount to shadow anything.)
        """
        from fastapi.routing import APIRoute
        from starlette.routing import Mount

        routes = app.routes
        mount_indices = [
            i for i, r in enumerate(routes)
            if isinstance(r, Mount) and r.path == "/api/v1/mcp"
        ]
        if not mount_indices:  # MCP_ENABLED=false: no mount, nothing to shadow
            pytest.skip("MCP mount not mounted (MCP_ENABLED=false at import)")
        mount_index = mount_indices[0]
        token_routes = [
            i for i, r in enumerate(routes)
            if isinstance(r, APIRoute) and r.path.startswith("/api/v1/mcp/tokens")
        ]
        assert token_routes, "token REST routes must exist when the mount does"
        assert max(token_routes) < mount_index, (
            "the /api/v1/mcp Mount is registered before the /api/v1/mcp/tokens "
            "API routes and shadows them (they 404 inside the FastMCP app)"
        )


# ---------------------------------------------------------------------------
# CSRF: inherited from the session middleware (mutations 403 without header)
# ---------------------------------------------------------------------------


class TestCsrfInherited:
    async def test_mutation_without_csrf_header_403(self, mcp_env, hosted):
        await _seed(mcp_env, "a@example.com")
        async with _client() as client:
            await _login(client, "a@example.com")
            # Session cookie present, but the X-CSRF-Token header is not.
            resp = await client.post(TOKENS, json={"label": "No CSRF"})
            assert resp.status_code == 403
            assert resp.json()["detail"] == "csrf_failed"

    async def test_delete_without_csrf_header_403(self, mcp_env, hosted):
        await _seed(mcp_env, "a@example.com")
        async with _client() as client:
            await _login(client, "a@example.com")
            resp = await client.delete(f"{TOKENS}/some-id")
            assert resp.status_code == 403
            assert resp.json()["detail"] == "csrf_failed"
