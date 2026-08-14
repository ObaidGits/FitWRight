"""`/status` must report a refused provider key as unhealthy.

This is the signal the frontend gate reads. Before it existed, `llm_healthy` was
always null, so a stale deployment-level LLM_API_KEY looked usable: the upload was
allowed, the file was read, and the provider refused it at the very end.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app import llm_health
from app.main import app

pytestmark = pytest.mark.integration


@pytest.fixture
def client():
    """Async HTTP client for the app, matching test_health_api.py."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture(autouse=True)
def _clean():
    llm_health.reset_for_tests()
    yield
    llm_health.reset_for_tests()


def _config(api_key: str = "sk-test", provider: str = "openai"):
    return type("C", (), {"api_key": api_key, "provider": provider})()


def _stats(has_master: bool = True) -> dict:
    return {
        "total_resumes": 1,
        "total_jobs": 0,
        "total_improvements": 0,
        "has_master_resume": has_master,
    }


class TestRefusedKeyIsReported:
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_healthy_is_null_before_any_refusal(self, mock_config, mock_db, client):
        mock_config.return_value = _config()
        mock_db.get_stats.return_value = _stats()
        async with client:
            data = (await client.get("/api/v1/status")).json()
        # Null, not true: this endpoint makes no live provider call.
        assert data["llm_healthy"] is None
        assert data["llm_configured"] is True

    @patch("app.routers.health.credentials_rejected")
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_healthy_is_false_once_the_provider_has_refused(
        self, mock_config, mock_db, mock_rejected, client
    ):
        mock_config.return_value = _config()
        mock_db.get_stats.return_value = _stats()
        mock_rejected.return_value = llm_health.CredentialRejection(
            provider="openai", at=0.0, detail="resume_parse"
        )
        async with client:
            data = (await client.get("/api/v1/status")).json()

        assert data["llm_healthy"] is False
        # Still configured - a key string exists. Keeping these two facts separate
        # is the point: conflating them is what let the bad key through.
        assert data["llm_configured"] is True

    @patch("app.routers.health.credentials_rejected")
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_clearing_the_refusal_restores_null(
        self, mock_config, mock_db, mock_rejected, client
    ):
        mock_config.return_value = _config()
        mock_db.get_stats.return_value = _stats()
        mock_rejected.return_value = None
        async with client:
            data = (await client.get("/api/v1/status")).json()
        assert data["llm_healthy"] is None
