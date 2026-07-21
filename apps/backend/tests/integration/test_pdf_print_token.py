"""Integration tests for the PDF print-token flow (hosted-mode 401 fix).

The headless-Chromium PDF render loads the frontend /print route, whose SSR
fetch has no user session cookie. In hosted mode that fetch used to 401 (-> the
generic "PDF rendering failed" error). The export endpoint now mints a
short-lived signed print token, and `GET /resumes/print-data` authenticates the
render with it. These tests exercise that endpoint directly.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.pdf_token import make_print_token


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_kv(isolated_db):
    from app.platform import reset_container
    reset_container()
    yield
    reset_container()


class TestPrintDataEndpoint:
    async def test_valid_token_returns_resume(self, isolated_db, owner_id):
        resume = await isolated_db.create_resume(
            owner_id,
            content=json.dumps({"summary": "print me"}),
            content_type="json",
            processed_data={"summary": "print me"},
            processing_status="ready",
        )
        rid = resume["resume_id"]
        token = make_print_token(owner_id, rid)

        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/print-data?resume_id={rid}&token={token}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["resume_id"] == rid

    async def test_invalid_token_401(self, isolated_db, owner_id):
        resume = await isolated_db.create_resume(owner_id, content="{}", content_type="json")
        rid = resume["resume_id"]
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/print-data?resume_id={rid}&token=forged.token.value")
        assert resp.status_code == 401

    async def test_token_bound_to_resume(self, isolated_db, owner_id):
        """A token minted for resume A must not unlock resume B."""
        a = await isolated_db.create_resume(owner_id, content="{}", content_type="json")
        b = await isolated_db.create_resume(owner_id, content="{}", content_type="json")
        token_a = make_print_token(owner_id, a["resume_id"])
        async with _client() as c:
            resp = await c.get(f"/api/v1/resumes/print-data?resume_id={b['resume_id']}&token={token_a}")
        assert resp.status_code == 401

    async def test_missing_token_422(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.get("/api/v1/resumes/print-data?resume_id=whatever")
        assert resp.status_code == 422  # token is a required query param

    async def test_anonymous_reachable_without_session(self, isolated_db, owner_id):
        """The endpoint is NOT session-guarded (token-only) - anon can reach it,
        but a bad token still 401s (never leaks data)."""
        async with _client() as c:
            resp = await c.get("/api/v1/resumes/print-data?resume_id=x&token=bad")
        assert resp.status_code == 401  # reached the handler, rejected by token
