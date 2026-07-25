"""Integration tests for health and status endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def client():
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestHealthEndpoint:
    """GET /api/v1/health - lightweight liveness probe (does NOT call the LLM)."""

    async def test_health_returns_healthy(self, client):
        """Liveness probe always reports healthy and needs no LLM call."""
        async with client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    async def test_health_is_independent_of_llm(self, mock_health, client):
        """/health is a liveness probe: it stays healthy even when the LLM is
        unhealthy, and must NOT call the provider. Dependency readiness lives
        at /health/ready; provider testing is explicit and authenticated.

        Regression guard for the liveness-vs-readiness split - the previous
        version of this test asserted the deleted '/health returns degraded'
        behavior and failed silently because nothing ran the suite.
        """
        mock_health.return_value = {"healthy": False, "error_code": "api_key_missing"}
        async with client:
            resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
        mock_health.assert_not_awaited()


class TestStatusEndpoint:
    """GET /api/v1/status - persisted setup facts, never a live LLM probe."""

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_ready_from_persisted_setup(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "sk-test", "provider": "openai"}
        )()
        mock_db.get_stats.return_value = {
            "total_resumes": 1,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": True,
        }
        async with client:
            resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["llm_configured"] is True
        assert data["llm_healthy"] is None
        assert data["has_master_resume"] is True
        mock_health.assert_not_awaited()

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_setup_required_only_for_missing_persisted_setup(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "", "provider": "openai"}
        )()
        mock_db.get_stats.return_value = {
            "total_resumes": 0,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": False,
        }
        async with client:
            resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "setup_required"
        assert data["llm_healthy"] is None
        mock_health.assert_not_awaited()

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_provider_outage_cannot_change_complete_setup_to_required(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "sk-test", "provider": "openai"}
        )()
        mock_health.side_effect = RuntimeError("must not be called")
        mock_db.get_stats.return_value = {
            "total_resumes": 2,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": True,
        }
        async with client:
            resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["llm_healthy"] is None
        mock_health.assert_not_awaited()

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_degrades_when_db_stats_fails(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "sk-test", "provider": "openai"}
        )()
        mock_db.get_stats.side_effect = RuntimeError("db boom")
        async with client:
            resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "setup_required"
        assert data["llm_healthy"] is None
        assert data["has_master_resume"] is False
        assert data["database_stats"]["total_resumes"] == 0
        mock_health.assert_not_awaited()

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_status_openai_compatible_is_configured_without_key(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "", "provider": "openai_compatible"}
        )()
        mock_db.get_stats.return_value = {
            "total_resumes": 1,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": True,
        }
        async with client:
            resp = await client.get("/api/v1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["llm_configured"] is True
        assert data["status"] == "ready"
        assert data["llm_healthy"] is None
        mock_health.assert_not_awaited()

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_repeated_status_requests_never_probe_provider(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type(
            "C", (), {"api_key": "sk-test", "provider": "openai"}
        )()
        mock_db.get_stats.return_value = {
            "total_resumes": 1,
            "total_jobs": 0,
            "total_improvements": 0,
            "has_master_resume": True,
        }
        async with client:
            for _ in range(5):
                resp = await client.get("/api/v1/status")
                assert resp.status_code == 200
                assert resp.json()["llm_healthy"] is None
        mock_health.assert_not_awaited()

    async def test_anonymous_hosted_status_never_resolves_owner_config(self, client):
        """No hosted principal means no config/key or owned DB lookup at all."""
        with (
            patch(
                "app.routers.health._resolve_status_user_id",
                new=AsyncMock(return_value=None),
            ),
            patch("app.routers.health.get_llm_config") as mock_config,
            patch("app.routers.health.db", new_callable=AsyncMock) as mock_db,
            patch("app.llm.check_llm_health", new_callable=AsyncMock) as mock_health,
        ):
            async with client:
                resp = await client.get("/api/v1/status")

        assert resp.status_code == 200
        body = resp.json()
        # The deployment-mode flag is reported for the frontend mismatch guard;
        # it reflects the backend's own configured mode.
        from app.config import settings

        assert body.pop("single_user") == settings.single_user_mode
        assert body == {
            "status": "setup_required",
            "llm_configured": False,
            "llm_healthy": None,
            "has_master_resume": False,
            "database_stats": {
                "total_resumes": 0,
                "total_jobs": 0,
                "total_improvements": 0,
                "has_master_resume": False,
            },
        }
        mock_config.assert_not_called()
        mock_db.get_stats.assert_not_awaited()
        mock_health.assert_not_awaited()


class TestSetupStatusEndpoint:
    """GET /api/v1/setup/status - deterministic persisted onboarding facts."""

    @patch("app.llm.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_complete_for_configured_user_with_master(
        self, mock_config, mock_db, mock_health, client
    ):
        mock_config.return_value = type("C", (), {"api_key": "sk-test", "provider": "openai"})()
        mock_db.get_stats.return_value = {"has_master_resume": True}
        async with client:
            resp = await client.get("/api/v1/setup/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "complete": True,
            "llm_configured": True,
            "has_master_resume": True,
        }
        # Setup detection must never wait on or be changed by provider health.
        mock_health.assert_not_awaited()

    @patch("app.routers.health.db", new_callable=AsyncMock)
    @patch("app.routers.health.get_llm_config")
    async def test_incomplete_reports_exact_missing_facts(self, mock_config, mock_db, client):
        mock_config.return_value = type("C", (), {"api_key": "", "provider": "openai"})()
        mock_db.get_stats.return_value = {"has_master_resume": True}
        async with client:
            resp = await client.get("/api/v1/setup/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "complete": False,
            "llm_configured": False,
            "has_master_resume": True,
        }
