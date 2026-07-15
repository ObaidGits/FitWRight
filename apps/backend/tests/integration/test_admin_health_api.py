"""Integration tests for ``GET /api/v1/admin/health`` (Task 6.6).

Exercises the real System Health endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Mirrors the ``_client`` / ``_admin_client`` / ``_login``
setup used by :mod:`tests.integration.test_admin_api` (reusing its helpers).

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 — Req 15.1) and asserts the admin body carries the six subsystem tiles +
secret-free release fields (Req 3.2 / 17.3, Property 3).
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_HEALTH_URL = "/api/v1/admin/health"
_EXPECTED_TILES = {
    "Backend",
    "Database",
    "KVStore/Queue",
    "AI provider",
    "Storage provider",
    "Migrations",
}


class TestAdminHealthAuthz:
    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_HEALTH_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-health@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-health@example.com")
            assert (await client.get(_HEALTH_URL)).status_code == 403

    async def test_admin_200_with_tiles_and_release(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_HEALTH_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 17.3).
        assert_no_forbidden_fields(body)
        # Exactly the six subsystem tiles are present.
        assert {t["name"] for t in body["tiles"]} == _EXPECTED_TILES
        assert len(body["tiles"]) == 6
        # Release metadata is present with a version + env.
        assert body["release"]["version"]
        assert body["release"]["env"]
