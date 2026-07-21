"""Integration tests for ``GET /api/v1/admin/kpis`` (Task 14.4).

Exercises the real Overview KPIs endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Reuses the ``_client`` / ``_admin_client`` / ``_seed`` /
``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 - Req 15.1) with a secret-free ``OverviewKpis`` body (Property 3 / Req 15.8):
all five KPI cards present as ``{value?, unavailable}``, plus ``computedAt`` and
``stale``. Each KPI is computed in isolation, so on a fresh store the response is
still well-formed (each card either a value or an explicit ``unavailable`` marker
- Req 13.7).

Requirements: 15.1, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_KPIS_URL = "/api/v1/admin/kpis"
_KPI_CARDS = ("totalUsers", "newUsersToday", "aiCallsToday", "errorRate24h", "purgeBacklog")


class TestKpisAuthz:
    """Validates: Requirements 15.1, 15.8"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_KPIS_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-kpis@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-kpis@example.com")
            assert (await client.get(_KPIS_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_KPIS_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # All five KPI cards present, each a {value?, unavailable} shape (Req 13.7).
        for card in _KPI_CARDS:
            assert card in body, f"missing KPI card: {card}"
            assert "unavailable" in body[card]
            assert isinstance(body[card]["unavailable"], bool)
            # value key present (may be null when unavailable).
            assert "value" in body[card]
        # Envelope fields.
        assert "computedAt" in body
        assert isinstance(body["stale"], bool)
