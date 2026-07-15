"""Integration tests for job description endpoints."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestJobUpload:
    """POST /api/v1/jobs/upload"""

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    async def test_upload_single_job(self, mock_db, client):
        mock_db.create_job.return_value = {
            "job_id": "job-123",
            "content": "Senior Engineer at TechCorp",
            "created_at": "2026-01-01T00:00:00Z",
        }
        async with client:
            resp = await client.post("/api/v1/jobs/upload", json={
                "job_descriptions": ["Senior Engineer at TechCorp"],
                "resume_id": None,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "data successfully processed"
        assert len(data["job_id"]) == 1

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    async def test_upload_multiple_jobs(self, mock_db, client):
        mock_db.create_job.side_effect = [
            {"job_id": f"job-{i}", "content": f"JD {i}", "created_at": "2026-01-01T00:00:00Z"}
            for i in range(3)
        ]
        async with client:
            resp = await client.post("/api/v1/jobs/upload", json={
                "job_descriptions": ["JD 1", "JD 2", "JD 3"],
            })
        assert resp.status_code == 200
        assert len(resp.json()["job_id"]) == 3

    async def test_upload_empty_list_returns_400(self, client):
        async with client:
            resp = await client.post("/api/v1/jobs/upload", json={
                "job_descriptions": [],
            })
        assert resp.status_code == 400

    async def test_upload_empty_string_returns_400(self, client):
        async with client:
            resp = await client.post("/api/v1/jobs/upload", json={
                "job_descriptions": ["  "],
            })
        assert resp.status_code == 400


class TestJobAnalyze:
    """POST /api/v1/jobs/analyze"""

    _KEYWORDS = {
        "required_skills": ["Python", "AWS"],
        "preferred_skills": ["Kubernetes"],
        "keywords": ["microservices"],
        "experience_requirements": ["5+ years"],
        "seniority_level": "senior",
        "experience_years": 5,
    }

    async def test_empty_jd_returns_400(self, client):
        async with client:
            resp = await client.post("/api/v1/jobs/analyze", json={"job_description": "  "})
        assert resp.status_code == 400

    @patch("app.routers.jobs.extract_job_keywords", new_callable=AsyncMock)
    async def test_keywords_only_without_resume(self, mock_extract, client):
        mock_extract.return_value = self._KEYWORDS
        async with client:
            resp = await client.post(
                "/api/v1/jobs/analyze",
                json={"job_description": "Senior Python/AWS engineer role."},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["keywords"]["required_skills"] == ["Python", "AWS"]
        # experience_years coerced to a string; matched/missing empty w/o resume.
        assert data["keywords"]["experience_years"] == "5"
        assert data["matched"] == []
        assert data["missing"] == []
        assert data["fit_score"] is None

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    @patch("app.routers.jobs.extract_job_keywords", new_callable=AsyncMock)
    async def test_computes_matched_missing_and_fit(self, mock_extract, mock_db, client):
        mock_extract.return_value = self._KEYWORDS
        mock_db.get_resume.return_value = {
            "processed_data": {
                "summary": "Backend engineer building microservices in Python.",
                "additional": {"technicalSkills": ["Python", "Docker"]},
            }
        }
        async with client:
            resp = await client.post(
                "/api/v1/jobs/analyze",
                json={
                    "job_description": "Senior Python/AWS engineer role.",
                    "resume_id": "r1",
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        # Python + microservices are present; AWS + Kubernetes are not.
        assert set(data["matched"]) == {"Python", "microservices"}
        assert set(data["missing"]) == {"AWS", "Kubernetes"}
        assert data["fit_score"] == pytest.approx(50.0)

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    @patch("app.routers.jobs.extract_job_keywords", new_callable=AsyncMock)
    async def test_missing_resume_returns_404(self, mock_extract, mock_db, client):
        mock_extract.return_value = self._KEYWORDS
        mock_db.get_resume.return_value = None
        async with client:
            resp = await client.post(
                "/api/v1/jobs/analyze",
                json={"job_description": "Senior role.", "resume_id": "nope"},
            )
        assert resp.status_code == 404

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    @patch("app.routers.jobs.extract_job_keywords", new_callable=AsyncMock)
    async def test_resume_without_processed_data_returns_keywords_only(
        self, mock_extract, mock_db, client
    ):
        mock_extract.return_value = self._KEYWORDS
        mock_db.get_resume.return_value = {"content_type": "md", "content": "# raw"}
        async with client:
            resp = await client.post(
                "/api/v1/jobs/analyze",
                json={"job_description": "Senior role.", "resume_id": "r1"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fit_score"] is None
        assert data["matched"] == []


class TestGetJob:
    """GET /api/v1/jobs/{job_id}"""

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    async def test_get_existing_job(self, mock_db, client):
        mock_db.get_job.return_value = {
            "job_id": "job-123",
            "content": "Engineer role",
            "created_at": "2026-01-01T00:00:00Z",
        }
        async with client:
            resp = await client.get("/api/v1/jobs/job-123")
        assert resp.status_code == 200
        assert resp.json()["job_id"] == "job-123"

    @patch("app.routers.jobs.db", new_callable=AsyncMock)
    async def test_get_nonexistent_job_returns_404(self, mock_db, client):
        mock_db.get_job.return_value = None
        async with client:
            resp = await client.get("/api/v1/jobs/nonexistent")
        assert resp.status_code == 404
