"""Persistent AI analysis cache — facade, service, and endpoint-reuse tests.

Exercises the "compute once, reuse everywhere" substrate end to end against a
REAL isolated database (``isolated_db``): the ``analysis_artifacts`` facade
methods, the ``analysis_cache`` service (hit/miss/force/failure), dependency-
aware invalidation, user scoping, and the wired workflows (job-analysis LLM
reuse + aux-generation reuse-unless-regenerate).
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app import analysis_cache


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Facade: analysis_artifacts CRUD + scoping + invalidation
# ---------------------------------------------------------------------------


class TestArtifactFacade:
    async def test_put_then_get_exact_key_hits(self, isolated_db, owner_id):
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="job_analysis",
            source_id="hash-abc",
            checksum="hash-abc",
            version="1|default",
            analysis_data={"keywords": ["python"]},
        )
        hit = await isolated_db.get_analysis_artifact(
            owner_id,
            artifact_type="job_analysis",
            source_id="hash-abc",
            checksum="hash-abc",
            version="1|default",
        )
        assert hit is not None
        assert hit["analysis_data"] == {"keywords": ["python"]}
        assert hit["status"] == "ready"

    async def test_get_misses_on_version_or_checksum_change(self, isolated_db, owner_id):
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="resume_parse",
            source_id="c1",
            checksum="c1",
            version="1|gpt-4",
            analysis_data={"x": 1},
        )
        # Different version (model/prompt changed) ⇒ miss (lazy regeneration).
        assert (
            await isolated_db.get_analysis_artifact(
                owner_id,
                artifact_type="resume_parse",
                source_id="c1",
                checksum="c1",
                version="2|gpt-4",
            )
        ) is None
        # Different checksum (content changed) ⇒ miss.
        assert (
            await isolated_db.get_analysis_artifact(
                owner_id,
                artifact_type="resume_parse",
                source_id="c1",
                checksum="c2",
                version="1|gpt-4",
            )
        ) is None

    async def test_put_is_idempotent_upsert_on_reuse_key(self, isolated_db, owner_id):
        for payload in ({"v": 1}, {"v": 2}):
            await isolated_db.put_analysis_artifact(
                owner_id,
                artifact_type="job_analysis",
                source_id="k",
                checksum="k",
                version="1|default",
                analysis_data=payload,
            )
        hit = await isolated_db.get_analysis_artifact(
            owner_id,
            artifact_type="job_analysis",
            source_id="k",
            checksum="k",
            version="1|default",
        )
        # Second put updated the same row (no duplicate) to the latest payload.
        assert hit["analysis_data"] == {"v": 2}

    async def test_invalidate_by_source_and_related(self, isolated_db, owner_id):
        # Directly keyed on the resource.
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="tailor_preview",
            source_id="resume-1",
            checksum="c",
            version="1",
            analysis_data={"a": 1},
        )
        # Only references the resource via related_id.
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="job_analysis",
            source_id="job-hash",
            related_id="resume-1",
            checksum="c",
            version="1",
            analysis_data={"b": 2},
        )
        deleted = await isolated_db.invalidate_analysis_artifacts(owner_id, "resume-1")
        assert deleted == 2

    async def test_invalidate_respects_type_filter(self, isolated_db, owner_id):
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="tailor_preview",
            source_id="resume-2",
            checksum="c",
            version="1",
            analysis_data={},
        )
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="keep_me",
            source_id="resume-2",
            checksum="c",
            version="1",
            analysis_data={},
        )
        deleted = await isolated_db.invalidate_analysis_artifacts(
            owner_id, "resume-2", artifact_types=["tailor_preview"]
        )
        assert deleted == 1
        # The other type survived (dependency-aware, not scorched-earth).
        assert (
            await isolated_db.get_analysis_artifact(
                owner_id,
                artifact_type="keep_me",
                source_id="resume-2",
                checksum="c",
                version="1",
            )
        ) is not None

    async def test_artifacts_are_user_scoped(self, isolated_db, owner_id):
        await isolated_db.put_analysis_artifact(
            owner_id,
            artifact_type="job_analysis",
            source_id="s",
            checksum="s",
            version="1",
            analysis_data={"secret": True},
        )
        # A different user must not see the owner's artifact.
        assert (
            await isolated_db.get_analysis_artifact(
                "someone-else",
                artifact_type="job_analysis",
                source_id="s",
                checksum="s",
                version="1",
            )
        ) is None


# ---------------------------------------------------------------------------
# Service: get_or_compute hit/miss/force/failure
# ---------------------------------------------------------------------------


class TestGetOrCompute:
    async def test_computes_on_miss_then_reuses_on_hit(self, isolated_db, owner_id):
        calls = {"n": 0}

        async def compute():
            calls["n"] += 1
            return {"result": calls["n"]}

        kw = dict(
            user_id=owner_id,
            artifact_type="resume_parse",
            source_id="hash1",
            checksum="hash1",
            version="1|default",
        )
        first, cached1 = await analysis_cache.get_or_compute(compute=compute, **kw)
        second, cached2 = await analysis_cache.get_or_compute(compute=compute, **kw)

        assert first == {"result": 1}
        assert cached1 is False
        # Second call is a pure cache hit — compute() never ran again.
        assert second == {"result": 1}
        assert cached2 is True
        assert calls["n"] == 1

    async def test_force_bypasses_cache_but_stores_fresh(self, isolated_db, owner_id):
        calls = {"n": 0}

        async def compute():
            calls["n"] += 1
            return {"n": calls["n"]}

        kw = dict(
            user_id=owner_id,
            artifact_type="resume_parse",
            source_id="h",
            checksum="h",
            version="1|default",
        )
        await analysis_cache.get_or_compute(compute=compute, **kw)
        forced, cached = await analysis_cache.get_or_compute(compute=compute, force=True, **kw)
        assert cached is False
        assert forced == {"n": 2}
        assert calls["n"] == 2

    async def test_failed_compute_is_not_cached(self, isolated_db, owner_id):
        async def boom():
            raise RuntimeError("llm down")

        kw = dict(
            user_id=owner_id,
            artifact_type="resume_parse",
            source_id="hx",
            checksum="hx",
            version="1|default",
        )
        with pytest.raises(RuntimeError):
            await analysis_cache.get_or_compute(compute=boom, **kw)
        # Nothing was stored as a reusable hit.
        assert (
            await isolated_db.get_analysis_artifact(
                owner_id,
                artifact_type="resume_parse",
                source_id="hx",
                checksum="hx",
                version="1|default",
            )
        ) is None

    def test_checksum_is_deterministic_and_content_addressed(self):
        assert analysis_cache.checksum_text("abc") == analysis_cache.checksum_text("abc")
        assert analysis_cache.checksum_text("abc") != analysis_cache.checksum_text("abd")
        assert analysis_cache.checksum_obj({"a": 1, "b": 2}) == analysis_cache.checksum_obj(
            {"b": 2, "a": 1}
        )

    def test_version_key_changes_with_model(self):
        v1 = analysis_cache.version_key(analysis_cache.ARTIFACT_RESUME_PARSE, "gpt-4")
        v2 = analysis_cache.version_key(analysis_cache.ARTIFACT_RESUME_PARSE, "gemini")
        assert v1 != v2


# ---------------------------------------------------------------------------
# Endpoint: /jobs/analyze reuses the LLM keyword extraction
# ---------------------------------------------------------------------------


class TestJobAnalyzeReuse:
    _KEYWORDS = {
        "required_skills": ["Python"],
        "preferred_skills": [],
        "keywords": ["api"],
        "experience_requirements": [],
        "seniority_level": "senior",
        "experience_years": 5,
    }

    async def test_identical_jd_reuses_keywords_without_second_llm_call(
        self, isolated_db, owner_id, client
    ):
        jd = "Senior Python engineer building APIs at scale."
        with patch(
            "app.routers.jobs.extract_job_keywords",
            new=AsyncMock(return_value=self._KEYWORDS),
        ) as mock_extract:
            async with client:
                r1 = await client.post("/api/v1/jobs/analyze", json={"job_description": jd})
                r2 = await client.post("/api/v1/jobs/analyze", json={"job_description": jd})
            assert r1.status_code == 200
            assert r2.status_code == 200
            assert r1.json()["keywords"]["required_skills"] == ["Python"]
            # The expensive LLM extraction ran exactly once — the second identical
            # analysis was served from the persistent cache.
            assert mock_extract.await_count == 1


# ---------------------------------------------------------------------------
# Endpoint: aux generation reuses stored content unless regenerate=true
# ---------------------------------------------------------------------------


class TestAuxGenerationReuse:
    async def test_cover_letter_reused_without_llm(self, isolated_db, owner_id, client):
        resume = await isolated_db.create_resume(
            owner_id,
            content="{}",
            content_type="json",
            parent_id="master-1",
            processed_data={"summary": "x"},
            processing_status="ready",
            cover_letter="Dear Hiring Manager, ...",
        )
        rid = resume["resume_id"]
        with patch(
            "app.routers.resumes.generate_cover_letter", new=AsyncMock()
        ) as mock_gen:
            async with client:
                resp = await client.post(
                    f"/api/v1/resumes/{rid}/generate-cover-letter"
                )
            assert resp.status_code == 200
            assert resp.json()["content"] == "Dear Hiring Manager, ..."
            # Stored copy returned — the LLM generator was never invoked.
            mock_gen.assert_not_awaited()

    async def test_interview_prep_reused_without_llm(self, isolated_db, owner_id, client):
        import json as _json

        prep = {
            "role_fit_analysis": ["Strong Python match"],
            "resume_questions": [
                {
                    "question": "Tell me about your API work",
                    "focus_area": "backend",
                    "suggested_answer_points": ["scale", "latency"],
                }
            ],
            "project_follow_ups": [],
            "skill_gaps": [
                {
                    "skill": "Kubernetes",
                    "why_it_matters": "Mentioned in JD",
                    "preparation_suggestion": "Review core concepts",
                }
            ],
            "talking_points": ["p1"],
        }
        resume = await isolated_db.create_resume(
            owner_id,
            content="{}",
            content_type="json",
            parent_id="master-1",
            processed_data={"summary": "x"},
            processing_status="ready",
            interview_prep=_json.dumps(prep),
        )
        rid = resume["resume_id"]
        with patch(
            "app.routers.resumes.generate_interview_prep", new=AsyncMock()
        ) as mock_gen:
            async with client:
                resp = await client.post(
                    f"/api/v1/resumes/{rid}/generate-interview-prep"
                )
            assert resp.status_code == 200
            mock_gen.assert_not_awaited()
