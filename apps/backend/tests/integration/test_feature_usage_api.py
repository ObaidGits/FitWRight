"""Integration tests for the feature-usage analytics endpoint (Task 16.4).

Exercises ``GET /api/v1/admin/analytics/feature-usage`` end-to-end over an ASGI
transport against an isolated temp database with real sessions (no dependency
overrides), in **hosted** mode so authN/CSRF/rate-limit/capability all apply:

- authz matrix: anon -> 401, non-admin -> 403, admin -> 200 (Req 16 read authz);
- window validation: admin + ``window=45`` -> 400 ``invalid_window``; admin +
  ``window=30`` -> 200 with a secret-free, aggregate-only body (Req 16.3/16.6);
- the response passes the response-boundary forbidden-field guard (Req 15.8).

Requirements: 16.3, 16.6, 15.8.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin.schemas import assert_no_forbidden_fields
from app.auth.accounts import create_user
from app.auth.passwords import get_password_service
from app.config import settings as app_settings
from app.main import app

from tests.integration.test_auth_api import STRONG_PW, _login

pytestmark = pytest.mark.integration

_FEATURE_USAGE = "/api/v1/admin/analytics/feature-usage"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="https://test")


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setattr(app_settings, "single_user_mode", False)


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
    """Yield a logged-in admin client with the per-session csrf header attached."""
    await _seed(db, email, role="admin")
    async with _client() as client:
        await _login(client, email)
        client.headers["X-CSRF-Token"] = client.cookies.get("csrf")
        yield client


class TestFeatureUsageAuthz:
    """Validates: Requirements 16.3, 16.6, 15.8"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_FEATURE_USAGE)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain@example.com")
            assert (await client.get(_FEATURE_USAGE)).status_code == 403

    async def test_admin_invalid_window_400(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"{_FEATURE_USAGE}?window=45")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_window"

    async def test_admin_valid_window_200_and_secret_free(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"{_FEATURE_USAGE}?window=30")
        assert resp.status_code == 200
        body = resp.json()
        # Aggregate-only shape (Req 16.6) + secret-free (Req 15.8).
        assert body["window"] == 30
        assert len(body["series"]) == 8
        assert all(len(fs["points"]) == 30 for fs in body["series"])
        assert_no_forbidden_fields(body)
