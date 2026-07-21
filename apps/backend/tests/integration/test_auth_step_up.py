"""Integration tests for step-up (sudo) + password change (Task 6).

Exercises ``POST /auth/step-up`` (wrong password rejected + rate limit, correct
password bumps the window) and how the ``require_step_up`` gate protects
sensitive actions: ``POST /auth/password/change`` is 401 ``step_up_required``
without a recent step-up, passes within the window, and 401s again once the
window lapses. Password change revokes every OTHER session while keeping the
current one signed in (R7.3). Runs in hosted mode (``single_user_mode=False``)
so the per-session CSRF gate and step-up gate are both live.

Requirements: 9.1, 9.3, 7.3
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.sessions import get_session_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import (
    STRONG_PW,
    _login,
    _seed_active_user,
)

pytestmark = pytest.mark.integration

NEW_PW = "totally-different-passphrase-42"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


async def _step_up(client: AsyncClient, *, password: str = STRONG_PW):
    """Call ``/auth/step-up`` with the per-session CSRF header (hosted mode)."""
    csrf = client.cookies.get("csrf")
    return await client.post(
        "/api/v1/auth/step-up",
        json={"password": password},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )


async def _change_password(
    client: AsyncClient, *, current: str = STRONG_PW, new: str = NEW_PW
):
    csrf = client.cookies.get("csrf")
    return await client.post(
        "/api/v1/auth/password/change",
        json={"current_password": current, "new_password": new},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )


@pytest.fixture
def hosted(monkeypatch):
    """Run in hosted mode so CSRF + step-up gates are enforced."""
    monkeypatch.setattr(app_settings, "single_user_mode", False)


class TestStepUp:
    async def test_wrong_password_rejected(self, auth_env, hosted):
        await _seed_active_user(auth_env, "su-wrong@example.com")
        async with _client() as client:
            await _login(client, "su-wrong@example.com")
            resp = await _step_up(client, password="not-the-password-1")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"

    async def test_correct_password_bumps_window(self, auth_env, hosted):
        record = await _seed_active_user(auth_env, "su-ok@example.com")
        async with _client() as client:
            await _login(client, "su-ok@example.com")

            # No step-up recorded yet.
            sessions = await get_session_service().list_active_sessions(record.id)
            assert sessions[0].step_up_at is None

            resp = await _step_up(client)
            assert resp.status_code == 200
            assert resp.json()["email"] == "su-ok@example.com"

        # The session now carries a step_up_at (write-through eviction means the
        # next resolution sees it).
        sessions = await get_session_service().list_active_sessions(record.id)
        assert sessions[0].step_up_at is not None

    async def test_step_up_rate_limited(self, auth_env, hosted):
        await _seed_active_user(auth_env, "su-rl@example.com")
        statuses: list[int] = []
        async with _client() as client:
            await _login(client, "su-rl@example.com")
            for _ in range(12):
                resp = await _step_up(client, password="wrong-password-here-1")
                statuses.append(resp.status_code)
                if resp.status_code == 429:
                    break
        assert 401 in statuses
        assert 429 in statuses


class TestPasswordChangeGate:
    async def test_change_requires_step_up(self, auth_env, hosted):
        await _seed_active_user(auth_env, "pc-gate@example.com")
        async with _client() as client:
            await _login(client, "pc-gate@example.com")
            # No recent step-up -> gated.
            resp = await _change_password(client)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_change_passes_within_window(self, auth_env, hosted):
        await _seed_active_user(auth_env, "pc-ok@example.com")
        async with _client() as client:
            await _login(client, "pc-ok@example.com")
            assert (await _step_up(client)).status_code == 200
            resp = await _change_password(client)
            assert resp.status_code == 200

            # The new password now authenticates; the old one does not.
        async with _client() as client:
            good = await _login(client, "pc-ok@example.com", password=NEW_PW)
            assert good.status_code == 200
        async with _client() as client:
            bad = await _login(client, "pc-ok@example.com", password=STRONG_PW)
            assert bad.status_code == 401

    async def test_change_401_after_window_lapses(self, auth_env, hosted, monkeypatch):
        await _seed_active_user(auth_env, "pc-lapse@example.com")
        async with _client() as client:
            await _login(client, "pc-lapse@example.com")
            assert (await _step_up(client)).status_code == 200
            # Force the sudo window to have lapsed.
            monkeypatch.setattr(app_settings, "step_up_window", -1)
            resp = await _change_password(client)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_wrong_current_password_rejected(self, auth_env, hosted):
        await _seed_active_user(auth_env, "pc-cur@example.com")
        async with _client() as client:
            await _login(client, "pc-cur@example.com")
            assert (await _step_up(client)).status_code == 200
            resp = await _change_password(client, current="wrong-current-pw-1")
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "invalid_credentials"

    async def test_new_password_policy_enforced(self, auth_env, hosted):
        await _seed_active_user(auth_env, "pc-weak@example.com")
        async with _client() as client:
            await _login(client, "pc-weak@example.com")
            assert (await _step_up(client)).status_code == 200
            resp = await _change_password(client, new="short")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "weak_password"

    async def test_new_password_breach_rejected(self, auth_env, hosted, monkeypatch):
        from app.auth.breach import BreachResult
        from app.auth.passwords import get_password_service

        class _FakeBreach:
            async def check(self, password: str) -> BreachResult:
                return BreachResult(breached=True, count=99)

        await _seed_active_user(auth_env, "pc-breach@example.com")
        async with _client() as client:
            await _login(client, "pc-breach@example.com")
            assert (await _step_up(client)).status_code == 200
            monkeypatch.setattr(get_password_service(), "_breach_check", _FakeBreach())
            resp = await _change_password(client)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "breached_password"

    async def test_change_revokes_other_sessions_keeps_current(self, auth_env, hosted):
        record = await _seed_active_user(auth_env, "pc-multi@example.com")
        async with _client() as current, _client() as other:
            await _login(current, "pc-multi@example.com")
            assert (await _login(other, "pc-multi@example.com")).status_code == 200
            assert len(await get_session_service().list_active_sessions(record.id)) == 2

            assert (await _step_up(current)).status_code == 200
            assert (await _change_password(current)).status_code == 200

            # The initiating session survives; the other is revoked.
            current_ok = await current.get("/api/v1/auth/session")
            assert current_ok.status_code == 200
            other_dead = await other.get("/api/v1/auth/session")
            assert other_dead.status_code == 401

        remaining = await get_session_service().list_active_sessions(record.id)
        assert len(remaining) == 1
