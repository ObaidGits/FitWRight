"""Integration tests for verify-before-switch email change (Task 6.2).

Exercises ``POST /users/me/email`` (step-up required, uniqueness 409, and — the
core guarantee — the primary email is NOT switched until confirmation) and
``POST /users/me/email/confirm`` (single-use token swaps the email, uniqueness
race → 409, token invalidated after use). Runs in hosted mode so the step-up +
CSRF gates are live.

Requirements: 7.4, 9.1
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.accounts import create_user, get_by_email, get_by_id
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _login, _seed_active_user
from tests.integration.test_auth_step_up import _step_up
from tests.integration.test_auth_verification_reset import (
    _install_sender,
    _token_from,
)

pytestmark = pytest.mark.integration

NEW_EMAIL = "renamed@example.com"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


async def _begin_change(client: AsyncClient, new_email: str):
    csrf = client.cookies.get("csrf")
    return await client.post(
        "/api/v1/users/me/email",
        json={"email": new_email},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )


async def _confirm_change(client: AsyncClient, token: str):
    csrf = client.cookies.get("csrf")
    return await client.post(
        "/api/v1/users/me/email/confirm",
        json={"token": token},
        headers={"X-CSRF-Token": csrf} if csrf else {},
    )


class TestBeginEmailChange:
    async def test_requires_step_up(self, auth_env, hosted):
        await _seed_active_user(auth_env, "ec-gate@example.com")
        async with _client() as client:
            await _login(client, "ec-gate@example.com")
            # No recent step-up → gated.
            resp = await _begin_change(client, NEW_EMAIL)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "step_up_required"

    async def test_new_email_taken_is_409(self, auth_env, hosted):
        await _seed_active_user(auth_env, "ec-owner@example.com")
        # Someone already owns the target address.
        await _seed_active_user(auth_env, NEW_EMAIL)
        async with _client() as client:
            await _login(client, "ec-owner@example.com")
            assert (await _step_up(client)).status_code == 200
            resp = await _begin_change(client, NEW_EMAIL)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "email_unavailable"

    async def test_email_not_switched_until_confirm(self, auth_env, hosted, monkeypatch):
        sender = _install_sender(monkeypatch)
        record = await _seed_active_user(auth_env, "ec-verify@example.com")
        async with _client() as client:
            await _login(client, "ec-verify@example.com")
            assert (await _step_up(client)).status_code == 200
            begin = await _begin_change(client, NEW_EMAIL)
            assert begin.status_code == 200

            # The confirmation link went to the NEW address.
            assert sender.last.to == NEW_EMAIL

            # Primary email is UNCHANGED until confirmation (verify-before-switch).
            me = await client.get("/api/v1/users/me")
            assert me.json()["email"] == "ec-verify@example.com"
        # And in the DB.
        current = await get_by_id(record.id, db=auth_env)
        assert current.email == "ec-verify@example.com"


class TestConfirmEmailChange:
    async def test_confirm_switches_email_and_burns_token(self, auth_env, hosted, monkeypatch):
        sender = _install_sender(monkeypatch)
        record = await _seed_active_user(auth_env, "ec-swap@example.com")
        async with _client() as client:
            await _login(client, "ec-swap@example.com")
            assert (await _step_up(client)).status_code == 200
            assert (await _begin_change(client, NEW_EMAIL)).status_code == 200
            token = _token_from(sender.last)

            confirmed = await _confirm_change(client, token)
            assert confirmed.status_code == 200
            assert confirmed.json()["email"] == NEW_EMAIL
            assert confirmed.json()["emailVerified"] is True

            # Single-use: replaying the token fails.
            replay = await _confirm_change(client, token)
            assert replay.status_code == 400
            assert replay.json()["error"]["code"] == "invalid_token"

        # The switch is persisted and the new address logs in.
        current = await get_by_id(record.id, db=auth_env)
        assert current.email == NEW_EMAIL

    async def test_confirm_invalid_token(self, auth_env, hosted):
        async with _client() as client:
            resp = await _confirm_change(client, "not-a-real-token")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_token"

    async def test_uniqueness_race_at_confirm_is_409(self, auth_env, hosted, monkeypatch):
        sender = _install_sender(monkeypatch)
        await _seed_active_user(auth_env, "ec-race@example.com")
        async with _client() as client:
            await _login(client, "ec-race@example.com")
            assert (await _step_up(client)).status_code == 200
            assert (await _begin_change(client, NEW_EMAIL)).status_code == 200
            token = _token_from(sender.last)

            # Between begin and confirm, another account claims the address.
            await create_user(
                email=NEW_EMAIL,
                name="Racer",
                password_hash=get_password_service().hash_password(STRONG_PW),
                status="active",
                db=auth_env,
            )
            resp = await _confirm_change(client, token)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "email_unavailable"
