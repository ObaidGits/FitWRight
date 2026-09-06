"""REST-side bound on the manual-search query (hardening F9 / T10 M1).

``ManualSearchRequest.query`` (``POST /api/v1/discovery/search/start``) was an
unbounded ``str`` shared verbatim with the MCP ``start_job_search`` tool, so a
hostile 1MB query traveled into the handler and echoed back through error
paths at ~2x. The schema now caps it at 256 characters - real search terms are
job titles plus a location, never a megabyte.

Body validation fails before the handler runs, so the test needs no scraped
boards and no search state - only the kill-switch to be ON so the route is
reachable at all.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings as app_settings
from app.main import app

pytestmark = pytest.mark.integration

SEARCH_START = "/api/v1/discovery/search/start"


@pytest.fixture
async def client(auth_env, isolated_db, monkeypatch):
    """A client against the real app: single-user owner on the isolated DB,
    JOB_DISCOVERY on so the route is reachable."""
    monkeypatch.setattr(app_settings, "JOB_DISCOVERY", True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


class TestManualSearchQueryBound:
    async def test_1mb_query_is_422(self, client):
        resp = await client.post(SEARCH_START, json={"query": "x" * 1_048_576})
        assert resp.status_code == 422, resp.text
        assert "query" in resp.text  # the failing field is named

    async def test_query_over_256_chars_is_422(self, client):
        resp = await client.post(SEARCH_START, json={"query": "y" * 257})
        assert resp.status_code == 422, resp.text

    async def test_schema_rejects_oversized_query_directly(self):
        """The bound lives on the shared schema, so the MCP path (which
        constructs the same model) inherits it even before its own check."""
        from pydantic import ValidationError

        from app.routers.discovery import ManualSearchRequest

        ManualSearchRequest(query="Backend Engineer Python")  # in bounds
        with pytest.raises(ValidationError):
            ManualSearchRequest(query="z" * 257)
