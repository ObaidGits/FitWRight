"""Per-user LLM rate-limit tests (cost/abuse guard on generation endpoints).

Drives a real generation endpoint through the app with the limit dialed to a
tiny value, asserting the (limit+1)th call returns 429 + Retry-After. Uses the
isolated DB + local KVStore (per-test reset), no real LLM/provider.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import settings as app_settings
from app.main import app

pytestmark = pytest.mark.integration


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_llm_endpoint_rate_limited(auth_env, owner_id, monkeypatch):
    # Dial the per-user LLM limit down to 2/min for a fast, deterministic test.
    monkeypatch.setattr(app_settings, "llm_rate_per_min_user", 2)

    async with _client() as client:
        # The cover-letter endpoint 404s on a missing resume, but the rate-limit
        # route dependency runs BEFORE the body — so the first 2 calls get 404
        # (limit not hit) and the 3rd is rejected with 429 before the handler.
        statuses = []
        for _ in range(3):
            r = await client.post("/api/v1/resumes/nonexistent/generate-cover-letter")
            statuses.append(r.status_code)

        assert statuses[0] != 429 and statuses[1] != 429, statuses
        assert statuses[2] == 429, statuses
        # 429 carries a Retry-After and the ADR-7 envelope code.
        last = await client.post("/api/v1/resumes/nonexistent/generate-cover-letter")
        assert last.status_code == 429
        assert "retry-after" in {k.lower() for k in last.headers}
        assert last.json()["error"]["code"] == "rate_limited"


async def test_llm_limit_disabled_when_zero(auth_env, owner_id, monkeypatch):
    monkeypatch.setattr(app_settings, "llm_rate_per_min_user", 0)
    async with _client() as client:
        # Many calls, never rate-limited (limit disabled) — all resolve to 404.
        for _ in range(6):
            r = await client.post("/api/v1/resumes/nonexistent/generate-cover-letter")
            assert r.status_code != 429
