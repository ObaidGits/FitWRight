"""Integration tests for ``GET /api/v1/admin/performance`` (Task 11.3).

Exercises the real Performance signals endpoint end-to-end over an ASGI
transport against an isolated temp database in **hosted** mode, so authN/CSRF/
rate-limit/capability all apply. Reuses the ``_client`` / ``_admin_client`` /
``_seed`` / ``hosted`` harness from :mod:`tests.integration.test_admin_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 — Req 15.1) with a secret-free ``PerformanceSignals`` body (Property 3), and
the ``response_model_exclude_none=True`` behaviour (Req 6.5): the None-valued
host metrics (``memoryBytes`` / ``cpuPercent`` / ``diskBytes``) and
``dbQueryTimeMs`` are dropped from the payload, while the present aggregates and
the ``unavailable`` list (naming ``dbQueryTimeMs``) are retained.

Requirements: 6.5, 6.7, 15.1, 15.8.
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_PERF_URL = "/api/v1/admin/performance"


class TestPerformanceAuthz:
    """Validates: Requirements 15.1, 15.8"""

    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_PERF_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-perf@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-perf@example.com")
            assert (await client.get(_PERF_URL)).status_code == 403

    async def test_admin_200_secret_free_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_PERF_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # Shape: aggregate lists + cache ratio + unavailable + timestamp.
        assert isinstance(body["routeClasses"], list)
        assert isinstance(body["topSlowRoutes"], list)
        assert isinstance(body["topSlowJobs"], list)
        assert 0.0 <= body["cacheHitRatio"] <= 1.0
        assert "computedAt" in body


class TestPerformanceExcludeNone:
    """Validates: Requirements 6.5, 6.7"""

    async def test_none_fields_excluded_from_body(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_PERF_URL)
        assert resp.status_code == 200
        body = resp.json()
        # Host metrics are a Non-Goal → None → dropped by exclude_none (Req 6.5).
        for field in ("memoryBytes", "cpuPercent", "diskBytes"):
            assert field not in body
        # dbQueryTimeMs is None → excluded from the body, but its name is surfaced
        # in the unavailable list so the client knows it is a wired-but-empty
        # signal (Req 6.7).
        assert "dbQueryTimeMs" not in body
        assert "dbQueryTimeMs" in body["unavailable"]
        # Present aggregates retained.
        for field in (
            "routeClasses",
            "topSlowRoutes",
            "topSlowJobs",
            "cacheHitRatio",
            "unavailable",
            "computedAt",
        ):
            assert field in body
