"""Integration tests for profile + device management (Task 4.2).

Covers ``GET/PATCH /users/me`` (name-only update, role/status ignored,
optimistic-concurrency 409) and the session/device endpoints
(``GET /users/me/sessions`` + ``DELETE /users/me/sessions/{id}`` with foreign-id
404 and CSRF enforcement). Reuses the auth helpers from ``test_auth_api``.

Requirements: 7.2, 7.5, 3.5, 8.4, 10.3
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import get_by_id
from app.auth.sessions import get_session_service
from app.config import settings as app_settings
from app.main import app
from app.schemas.auth import SAFE_USER_FIELDS

from tests.integration.test_auth_api import (
    STRONG_PW,
    _login,
    _seed_active_user,
    _signup,
)

pytestmark = pytest.mark.integration


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _login_new_user(client: AsyncClient, db, email: str):
    """Seed an active user and log ``client`` in as them; return the record."""
    record = await _seed_active_user(db, email)
    resp = await _login(client, email)
    assert resp.status_code == 200
    return record


class TestGetMe:
    async def test_returns_safe_user(self, auth_env):
        async with _client() as client:
            await _login_new_user(client, auth_env, "me@example.com")
            resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) <= SAFE_USER_FIELDS
        assert body["email"] == "me@example.com"

    async def test_anonymous_is_unauthorized(self, auth_env):
        async with _client() as client:
            resp = await client.get("/api/v1/users/me")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


class TestPatchMe:
    async def test_updates_name_only_ignoring_role_status(self, auth_env):
        async with _client() as client:
            await _login_new_user(client, auth_env, "patch@example.com")
            resp = await client.patch(
                "/api/v1/users/me",
                json={"name": "Renamed", "role": "admin", "status": "disabled"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "Renamed"
        # role/status are never mutated via this endpoint (R7.2, R8.4).
        assert body["role"] == "user"
        assert body["status"] == "active"

    async def test_stale_updated_at_conflicts(self, auth_env):
        async with _client() as client:
            record = await _login_new_user(client, auth_env, "conflict@example.com")
            stale = await client.patch(
                "/api/v1/users/me",
                json={"name": "X", "updated_at": "1999-01-01T00:00:00+00:00"},
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "conflict"

            # The current token succeeds.
            current = await get_by_id(record.id, db=auth_env)
            ok = await client.patch(
                "/api/v1/users/me",
                json={"name": "Fresh", "updated_at": current.updated_at},
            )
        assert ok.status_code == 200
        assert ok.json()["name"] == "Fresh"


class TestDeviceSessions:
    async def test_list_active_sessions_marks_current_and_hides_token(self, auth_env):
        async with _client() as client:
            record = await _login_new_user(client, auth_env, "dev@example.com")
            resp = await client.get("/api/v1/users/me/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        entry = sessions[0]
        assert entry["current"] is True
        # Never exposes a raw/hashed session token.
        assert "token" not in entry and "token_hash" not in entry
        assert set(entry) == {"id", "deviceLabel", "ipHash", "createdAt", "lastSeenAt", "current"}

    async def test_revoke_one_session(self, auth_env):
        # Two independent logins -> two active sessions for the same user.
        async with _client() as client_a, _client() as client_b:
            record = await _login_new_user(client_a, auth_env, "multi@example.com")
            assert (await _login(client_b, "multi@example.com")).status_code == 200

            listed = (await client_a.get("/api/v1/users/me/sessions")).json()["sessions"]
            assert len(listed) == 2
            other = next(s for s in listed if not s["current"])

            deleted = await client_a.delete(f"/api/v1/users/me/sessions/{other['id']}")
            assert deleted.status_code == 204

            remaining = (await client_a.get("/api/v1/users/me/sessions")).json()["sessions"]
        assert len(remaining) == 1
        assert remaining[0]["current"] is True

    async def test_revoke_foreign_session_is_404(self, auth_env):
        # user A's client tries to revoke user B's session id -> 404 (no disclosure).
        other = await _seed_active_user(auth_env, "victim@example.com")
        async with _client() as attacker_client, _client() as victim_client:
            await _login_new_user(attacker_client, auth_env, "attacker@example.com")
            assert (await _login(victim_client, "victim@example.com")).status_code == 200
            victim_sessions = await get_session_service().list_active_sessions(other.id)
            victim_sid = victim_sessions[0].id

            resp = await attacker_client.delete(f"/api/v1/users/me/sessions/{victim_sid}")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_found"
        # The victim's session is untouched.
        assert len(await get_session_service().list_active_sessions(other.id)) == 1

    async def test_revoke_requires_csrf(self, auth_env, monkeypatch):
        monkeypatch.setattr(app_settings, "single_user_mode", False)
        async with _client() as client:
            record = await _login_new_user(client, auth_env, "csrfdev@example.com")
            sessions = await get_session_service().list_active_sessions(record.id)
            sid = sessions[0].id
            # Mutation without the per-session CSRF header is rejected by middleware.
            resp = await client.delete(f"/api/v1/users/me/sessions/{sid}")
        assert resp.status_code == 403
