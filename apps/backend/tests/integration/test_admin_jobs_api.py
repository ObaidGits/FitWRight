"""Integration tests for ``GET /api/v1/admin/jobs`` (Task 7.3).

Exercises the real Background-Jobs panel endpoint end-to-end over an ASGI
transport against an isolated temp database in **hosted** mode, so authN/CSRF/
rate-limit/capability all apply. Mirrors the ``_client`` / ``_admin_client`` /
``_login`` setup used by :mod:`tests.integration.test_admin_api` (reusing its
helpers), matching the shape of :mod:`tests.integration.test_admin_health_api`.

Covers the ``require_admin_read`` authz matrix (anon 401, non-admin 403, admin
200 — Req 15.1) and asserts the admin body carries the jobs array + the queue/
purge-backlog gauge fields + ``computedAt``, and that it is secret-free
(Req 15.8 / Property 3).
"""

from __future__ import annotations

import pytest

from app.admin.schemas import assert_no_forbidden_fields

# Reuse the admin-API integration harness verbatim (client + login + fixtures).
from tests.integration.test_admin_api import _admin_client, _client, _seed, hosted  # noqa: F401
from tests.integration.test_auth_api import _login

pytestmark = pytest.mark.integration

_JOBS_URL = "/api/v1/admin/jobs"
_EXPECTED_JOBS = {"rollup", "purge", "audit_retention"}


class TestAdminJobsAuthz:
    async def test_anonymous_401(self, auth_env, hosted):
        async with _client() as client:
            assert (await client.get(_JOBS_URL)).status_code == 401

    async def test_non_admin_403(self, auth_env, hosted):
        await _seed(auth_env, "plain-jobs@example.com", role="user")
        async with _client() as client:
            await _login(client, "plain-jobs@example.com")
            assert (await client.get(_JOBS_URL)).status_code == 403

    async def test_admin_200_with_jobs_and_gauges(self, auth_env, hosted):
        async with _admin_client(auth_env) as client:
            resp = await client.get(_JOBS_URL)
        assert resp.status_code == 200
        body = resp.json()
        # No secret ever serializes (Property 3 / Req 15.8).
        assert_no_forbidden_fields(body)
        # The three run-marker jobs are surfaced as rows.
        assert {j["name"] for j in body["jobs"]} == _EXPECTED_JOBS
        # Queue + purge-backlog gauge fields are present.
        assert "queueLength" in body and "queueLengthUnavailable" in body
        assert "purgeBacklog" in body and "purgeBacklogUnavailable" in body
        assert body["computedAt"]
