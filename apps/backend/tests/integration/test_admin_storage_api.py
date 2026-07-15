"""Integration tests for ``GET /api/v1/admin/storage`` (Task 12.4).

Exercises the real Storage panel endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Reuses the ``_client`` / ``_admin_client`` / ``_seed`` /
``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 — Req 15.1) with a secret-free ``StoragePanel`` body (Property 3 / Req 15.8).
The panel is served from cached/pre-aggregated values only; with an empty store
it degrades to stale/unavailable markers rather than erroring (Req 7.6/7.7).

Requirements: 15.1, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_STORAGE_URL = "/api/v1/admin/storage"


class TestStorageAuthz:
    """Validates: Requirements 15.1, 15.8"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_STORAGE_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-storage@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-storage@example.com")
            assert (await client.get(_STORAGE_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_STORAGE_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # Shape: counts + growth + staleness markers + timestamp.
        assert body["avatarCount"] == 0
        assert body["resumeCount"] == 0
        assert body["resumeVersionCount"] == 0
        # Empty store → nothing sampled yet → degraded markers (Req 7.6/7.7/7.8).
        assert body["dbSizeStale"] is True
        assert body["objectStorageStale"] is True
        assert body["growthUnavailable"] is True
        assert "computedAt" in body
