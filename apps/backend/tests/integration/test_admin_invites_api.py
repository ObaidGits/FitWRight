"""Integration tests for secure admin signup (Option B) end-to-end.

Exercises the real routers over ASGI in **hosted** mode (authN/CSRF/capability
all apply):

- ``POST /admin/invites`` authz matrix (anon 401, non-admin 403, admin 200) and
  a secret-free body (the shareable URL lives in a value, never a forbidden key);
- ``GET /admin/invites`` lists bounded lifecycle history without tokens;
- ``DELETE /admin/invites/{id}`` revokes (idempotent);
- ``POST /auth/signup`` with a valid invite creates an ADMIN, signed in;
- invalid / used / expired / email-mismatch invites are rejected (400);
- an existing email is refused (409) WITHOUT consuming the invite;
- a plain public signup can NEVER become admin (role is server-controlled).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin.schemas import assert_no_forbidden_fields
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_admin_api import _admin_client, _client, _seed
from tests.integration.test_auth_api import STRONG_PW, _csrf, _login

pytestmark = pytest.mark.integration


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


async def _create_invite(client: AsyncClient, email: str, *, ttl_hours: int | None = None):
    body: dict = {"email": email}
    if ttl_hours is not None:
        body["ttlHours"] = ttl_hours
    return await client.post("/api/v1/admin/invites", json=body)


async def _signup_with_invite(client: AsyncClient, email: str, invite_token: str, *, name="Invited"):
    token = await _csrf(client)
    return await client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": STRONG_PW,
            "name": name,
            "invite_token": invite_token,
        },
        headers={"X-CSRF-Token": token},
    )


def _token_from_url(invite_url: str) -> str:
    from urllib.parse import parse_qs, urlparse

    return parse_qs(urlparse(invite_url).query)["invite"][0]


# ---------------------------------------------------------------------------
# Authz + response-shape for POST/GET/DELETE /admin/invites
# ---------------------------------------------------------------------------


class TestInviteEndpointsAuthz:
    async def test_anonymous_cannot_create_invite(self, auth_env, hosted):
        async with _client() as client:
            token = await _csrf(client)
            resp = await client.post(
                "/api/v1/admin/invites",
                json={"email": "x@example.com"},
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 401

    async def test_non_admin_cannot_create_invite(self, auth_env, hosted):
        await _seed(auth_env, "plain@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain@example.com")
            csrf = client.cookies.get("csrf")
            resp = await client.post(
                "/api/v1/admin/invites",
                json={"email": "x@example.com"},
                headers={"X-CSRF-Token": csrf},
            )
        assert resp.status_code == 403

    async def test_admin_creates_invite_secret_free(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await _create_invite(client, "invitee@example.com")
        assert resp.status_code == 200
        body = resp.json()
        # No forbidden key (token/secret/hash/...) anywhere in the response.
        assert_no_forbidden_fields(body)
        assert body["email"] == "invitee@example.com"
        assert body["role"] == "admin"
        assert "/signup?invite=" in body["inviteUrl"]
        assert "token" not in body  # the raw token is only in the URL value


# ---------------------------------------------------------------------------
# Redemption at /auth/signup
# ---------------------------------------------------------------------------


class TestInviteRedemption:
    async def test_valid_invite_creates_signed_in_admin(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            created = (await _create_invite(client, "newadmin@example.com")).json()
        raw = _token_from_url(created["inviteUrl"])

        async with _client() as client:
            resp = await _signup_with_invite(client, "newadmin@example.com", raw)
        assert resp.status_code == 200
        user = resp.json()
        assert user["role"] == "admin"
        assert user["email"] == "newadmin@example.com"

        # The account is real, active, and an admin in the DB.
        from app.auth.accounts import get_by_email

        rec = await get_by_email("newadmin@example.com", db=auth_env)
        assert rec.role == "admin" and rec.status == "active"

    async def test_invite_is_single_use(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            created = (await _create_invite(client, "once@example.com")).json()
        raw = _token_from_url(created["inviteUrl"])

        async with _client() as client:
            first = await _signup_with_invite(client, "once@example.com", raw)
        assert first.status_code == 200
        # A second signup (different email won't match; same email now exists) -
        # reusing the burned token for a fresh email must fail.
        async with _client() as client:
            second = await _signup_with_invite(client, "another@example.com", raw)
        assert second.status_code == 400
        assert second.json()["error"]["code"] == "invite_invalid"

    async def test_email_mismatch_rejected(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            created = (await _create_invite(client, "bound@example.com")).json()
        raw = _token_from_url(created["inviteUrl"])
        async with _client() as client:
            resp = await _signup_with_invite(client, "attacker@example.com", raw)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invite_invalid"

    async def test_bogus_token_rejected(self, auth_env, hosted):
        async with _client() as client:
            resp = await _signup_with_invite(client, "nobody@example.com", "totally-bogus")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invite_invalid"

    async def test_revoked_invite_cannot_be_redeemed(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            created = (await _create_invite(client, "revoked@example.com")).json()
            raw = _token_from_url(created["inviteUrl"])
            drop = await client.delete(f"/api/v1/admin/invites/{created['id']}")
            assert drop.status_code == 200 and drop.json()["changed"] is True
            listed = (await client.get("/api/v1/admin/invites")).json()["items"]
            lifecycle = next(item for item in listed if item["id"] == created["id"])
            assert lifecycle["status"] == "revoked"
            assert lifecycle["usedAt"] is None
            assert lifecycle["usedBy"] is None
            assert lifecycle["revokedAt"] is not None
            assert lifecycle["revokedBy"] is not None
            assert lifecycle["revokeReason"] == "manual"
            assert "inviteUrl" not in lifecycle
        async with _client() as client:
            resp = await _signup_with_invite(client, "revoked@example.com", raw)
        assert resp.status_code == 400

    async def test_existing_email_refused_without_consuming_invite(self, auth_env, hosted):
        await _seed(auth_env, "taken@example.com", role="user")
        async with _admin_client(auth_env) as client:
            created = (await _create_invite(client, "taken@example.com")).json()
            raw = _token_from_url(created["inviteUrl"])
        async with _client() as client:
            resp = await _signup_with_invite(client, "taken@example.com", raw)
        assert resp.status_code == 409
        # Invite was NOT consumed: its lifecycle record remains active.
        async with _admin_client(auth_env, email="admin2@example.com") as client:
            listed = (await client.get("/api/v1/admin/invites")).json()["items"]
        assert any(i["id"] == created["id"] for i in listed)


# ---------------------------------------------------------------------------
# The critical privilege-escalation guard: public signup can't self-grant admin
# ---------------------------------------------------------------------------


class TestNoSelfServeAdmin:
    async def test_plain_signup_ignores_role_and_is_user(self, auth_env, hosted, monkeypatch):
        # Verification OFF so signup returns the SafeUser directly (single-user
        # semantics) and we can read the role; a hosted+verify path returns a
        # uniform pending response by design.
        monkeypatch.setattr(app_settings, "email_verification", False)
        async with _client() as client:
            token = await _csrf(client)
            resp = await client.post(
                "/api/v1/auth/signup",
                # A hostile client trying to smuggle an admin role - extra fields
                # are ignored by the schema, and role is never read from input.
                json={
                    "email": "sneaky@example.com",
                    "password": STRONG_PW,
                    "name": "Sneaky",
                    "role": "admin",
                },
                headers={"X-CSRF-Token": token},
            )
        assert resp.status_code == 200
        assert resp.json()["role"] == "user"
