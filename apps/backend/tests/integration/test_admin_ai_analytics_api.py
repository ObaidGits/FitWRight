"""Integration tests for ``GET /api/v1/admin/ai-analytics`` (Task 9.5).

Exercises the real AI Analytics endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Reuses the ``_client`` / ``_admin_client`` / ``_seed`` /
``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 - Req 15.1) with a secret-free ``AiAnalytics`` body (Property 3), and the
``window`` request-validation bounds (1-365, default 30 - Req 4.3): 0 and 366
are rejected with 422, valid/omitted windows return 200.

Requirements: 4.3, 15.1, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_AI_URL = "/api/v1/admin/ai-analytics"


class TestAiAnalyticsAuthz:
    """Validates: Requirements 15.1"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_AI_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-ai@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-ai@example.com")
            assert (await client.get(_AI_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_AI_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # Shape: allowlisted aggregates + all five providers.
        assert body["window"] == 30
        assert body["totalCalls"] >= 0
        assert 0.0 <= body["successRate"] <= 1.0
        assert 0.0 <= body["failureRate"] <= 1.0
        assert len(body["providers"]) == 5
        assert "estimatedCostDollars" in body


class TestAiAnalyticsWindowValidation:
    """Validates: Requirements 4.3"""

    async def test_window_zero_rejected_422(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"{_AI_URL}?window=0")
        assert resp.status_code == 422

    async def test_window_over_max_rejected_422(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"{_AI_URL}?window=366")
        assert resp.status_code == 422

    async def test_window_default_and_valid_ok(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            omitted = await client.get(_AI_URL)
            default = await client.get(f"{_AI_URL}?window=30")
            valid = await client.get(f"{_AI_URL}?window=7")
            boundary_lo = await client.get(f"{_AI_URL}?window=1")
            boundary_hi = await client.get(f"{_AI_URL}?window=365")
        assert omitted.status_code == 200 and omitted.json()["window"] == 30
        assert default.status_code == 200 and default.json()["window"] == 30
        assert valid.status_code == 200 and valid.json()["window"] == 7
        assert boundary_lo.status_code == 200 and boundary_lo.json()["window"] == 1
        assert boundary_hi.status_code == 200 and boundary_hi.json()["window"] == 365
