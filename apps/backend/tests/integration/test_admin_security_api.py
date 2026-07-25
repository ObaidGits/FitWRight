"""Integration tests for ``GET /api/v1/admin/security`` (Task 13.4).

Exercises the real exact-window Security view endpoint end-to-end over an ASGI
transport against an isolated temp database in hosted mode. The admin login used
to authorize the successful request is itself a durable audit event, so the
response must report it rather than assuming an empty aggregate source.

Requirements: 9.4, 15.1, 15.8.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_SECURITY_URL = "/api/v1/admin/security"


class TestSecurityAuthz:
    """Validates: Requirements 9.4, 15.1, 15.8"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_SECURITY_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-security@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-security@example.com")
            assert (await client.get(_SECURITY_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_SECURITY_URL)
        assert resp.status_code == 200
        body = resp.json()
        assert_no_forbidden_fields(body)

        assert body["windowHours"] == 24
        start = datetime.fromisoformat(body["windowStart"])
        end = datetime.fromisoformat(body["windowEnd"])
        assert end - start == timedelta(hours=24)
        assert body["windowKind"] == "exact_trailing"
        assert body["adminLoginRoleBasis"] == "current_role_at_query_time"

        for field in ("loginFailed", "adminLogin", "authzDenied", "rateLimited", "suspicious"):
            assert field in body
            assert body[field] >= 0
        # The login establishing this admin session is in the exact audit window.
        assert body["adminLogin"] >= 1
        assert body["notInstrumented"] == []
        assert body["computedAt"] == body["windowEnd"]
