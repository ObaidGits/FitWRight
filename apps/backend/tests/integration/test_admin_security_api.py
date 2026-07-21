"""Integration tests for ``GET /api/v1/admin/security`` (Task 13.4).

Exercises the real Security view endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Reuses the ``_client`` / ``_admin_client`` / ``_seed`` /
``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 - Req 9.4 / 15.1) with a secret-free ``SecurityView`` body (Property 3 /
Req 15.8). With an empty store the view degrades to all-zero counts rather than
erroring (no ``audit_log`` fallback - Req 9.5).

Requirements: 9.4, 15.1, 15.8.
"""

from __future__ import annotations

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
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # Fixed 24h window + every count present and non-negative.
        assert body["windowHours"] == 24
        for field in ("loginFailed", "adminLogin", "authzDenied", "rateLimited", "suspicious"):
            assert field in body
            assert body[field] >= 0
        # Empty store -> all-zero counts, no audit_log fallback (Req 9.5).
        assert body["loginFailed"] == 0
        assert body["adminLogin"] == 0
        assert body["authzDenied"] == 0
        assert body["rateLimited"] == 0
        assert body["suspicious"] == 0
        # Honesty: signals with no durable source are flagged not-instrumented
        # (rendered as such by the UI) rather than surfaced as a real "0".
        assert set(body["notInstrumented"]) == {"rateLimited", "suspicious"}
        assert "computedAt" in body
