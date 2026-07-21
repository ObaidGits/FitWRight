"""Integration tests for ``GET /api/v1/admin/errors`` (Task 10.3).

Exercises the real Errors Summary endpoint end-to-end over an ASGI transport
against an isolated temp database in **hosted** mode, so authN/CSRF/rate-limit/
capability all apply. Reuses the ``_client`` / ``_admin_client`` / ``_seed`` /
``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 - Req 5.6/15.1) with a secret-free ``ErrorsSummary`` body (Property 3), and
the discrete ``window`` validation (7/30/90, default 30 - Req 5.5): unlike the
range-validated AI window (422), errors rejects an out-of-set window with an
explicit **400 ``invalid_window``**.

Requirements: 5.5, 5.6, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_ERRORS_URL = "/api/v1/admin/errors"


class TestErrorsAuthz:
    """Validates: Requirements 5.6, 15.1"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_ERRORS_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-errors@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-errors@example.com")
            assert (await client.get(_ERRORS_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_ERRORS_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # Shape: grouped buckets + by-source + trend; default window echoed.
        assert body["window"] == 30
        assert body["counts4xx"] >= 0
        assert body["counts5xx"] >= 0
        assert set(body["bySource"]) == {"api", "job", "storage", "ai"}
        assert body["topRouteClasses"] == []
        # Un-sourced fields are flagged not-instrumented (rendered as such by the
        # UI) rather than silently implying zero failures / no route-classes.
        assert set(body["notInstrumented"]) == {
            "topRouteClasses",
            "bySource.job",
            "bySource.storage",
        }
        assert len(body["trend"]) == 30


class TestErrorsWindowValidation:
    """Validates: Requirements 5.5"""

    async def test_invalid_window_rejected_400_invalid_window(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(f"{_ERRORS_URL}?window=15")
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "invalid_window"
        # A rejected request returns no trend data (Req 5.5).
        assert "trend" not in resp.json()

    async def test_valid_windows_ok_and_echoed(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            omitted = await client.get(_ERRORS_URL)
            w7 = await client.get(f"{_ERRORS_URL}?window=7")
            w30 = await client.get(f"{_ERRORS_URL}?window=30")
            w90 = await client.get(f"{_ERRORS_URL}?window=90")
        # Omitted -> default 30, echoed.
        assert omitted.status_code == 200 and omitted.json()["window"] == 30
        assert w7.status_code == 200 and w7.json()["window"] == 7
        assert w30.status_code == 200 and w30.json()["window"] == 30
        assert w90.status_code == 200 and w90.json()["window"] == 90
