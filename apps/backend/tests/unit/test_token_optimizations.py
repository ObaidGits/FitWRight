"""Regression tests for LLM token-optimization changes.

Covers:
1. Deterministic resume-title composition from already-extracted JD keywords
   (no extra LLM round-trip on the common path; LLM fallback preserved).
2. complete_json raising the output budget on a truncation retry instead of
   re-issuing an identically-capped request.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import llm
from app.routers.resumes import (
    _compose_role_and_company_from_keywords,
    _generate_auxiliary_messages,
)

pytestmark = pytest.mark.unit


class TestDeterministicTitle:
    def test_compose_role_and_company(self):
        assert (
            _compose_role_and_company_from_keywords(
                {"role": "Senior Backend Engineer", "company": "Acme Corp"}
            )
            == "Senior Backend Engineer @ Acme Corp"
        )

    def test_compose_role_only_when_company_missing(self):
        assert (
            _compose_role_and_company_from_keywords({"role": "Data Scientist", "company": ""})
            == "Data Scientist"
        )

    def test_compose_empty_when_no_role(self):
        assert _compose_role_and_company_from_keywords({"company": "Acme"}) == ""
        assert _compose_role_and_company_from_keywords({}) == ""
        assert _compose_role_and_company_from_keywords(None) == ""

    async def test_title_uses_no_llm_when_keywords_have_role(self):
        improved = {"personalInfo": {"name": "Jane Doe"}}
        with patch(
            "app.routers.resumes.generate_resume_title", new_callable=AsyncMock
        ) as mock_title:
            _, _, title, _, _ = await _generate_auxiliary_messages(
                improved,
                "Some JD",
                "en",
                enable_cover_letter=False,
                enable_outreach=False,
                enable_interview_prep=False,
                job_keywords={"role": "Senior Backend Engineer", "company": "Acme Corp"},
            )
        # Deterministic path: LLM title generator must NOT be called.
        mock_title.assert_not_awaited()
        assert title == "Jane Doe - Senior Backend Engineer @ Acme Corp"

    async def test_title_falls_back_to_llm_without_role(self):
        improved = {"personalInfo": {"name": "Jane Doe"}}
        with patch(
            "app.routers.resumes.generate_resume_title",
            new_callable=AsyncMock,
            return_value="Jane Doe - Engineer",
        ) as mock_title:
            _, _, title, _, _ = await _generate_auxiliary_messages(
                improved,
                "Some JD",
                "en",
                enable_cover_letter=False,
                enable_outreach=False,
                enable_interview_prep=False,
                job_keywords={"keywords": ["Python"], "required_skills": []},
            )
        mock_title.assert_awaited_once()
        assert title == "Jane Doe - Engineer"


class TestDeterministicSkillPlan:
    def test_accepts_only_existing_and_jd_skills(self):
        from app.services.improver import build_skill_target_plan

        original = {"additional": {"technicalSkills": ["Python", "FastAPI"]}}
        jd = "We need Python and Kubernetes experience for our platform team."
        job_keywords = {"required_skills": ["Kubernetes"], "preferred_skills": [], "keywords": []}

        plan = build_skill_target_plan(original, job_keywords, jd)
        accepted = {t["skill"].lower() for t in plan["accepted"]}

        # Existing skill relevant to the JD is targeted; JD-stated skill is added.
        assert "python" in accepted
        assert "kubernetes" in accepted
        # Anti-fabrication: nothing outside (existing skills ∪ JD skills) appears.
        assert accepted <= {"python", "fastapi", "kubernetes"}
        # The deterministic planner never rejects (it only proposes supportable).
        assert plan["rejected"] == []

    def test_no_fabrication_when_jd_has_no_skills(self):
        from app.services.improver import build_skill_target_plan

        original = {"additional": {"technicalSkills": ["Go"]}}
        # JD text does not mention Go, and no JD skills extracted.
        plan = build_skill_target_plan(original, {"required_skills": [], "preferred_skills": []}, "A vague role.")
        assert plan["accepted"] == []


def _fake_response(content: str, total_tokens: int = 100):
    """Minimal LiteLLM-shaped response object for complete_json."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(total_tokens=total_tokens),
        model="test-model",
    )


class TestRetryBumpsMaxTokens:
    async def test_truncation_retry_increases_max_tokens(self):
        # First response is a "truncated" resume (empty required arrays), which
        # trips _appears_truncated(schema_type="resume") and forces a retry.
        truncated = '{"workExperience": [], "education": [], "skills": []}'
        full = (
            '{"workExperience": [{"title": "SWE"}], "education": [{"degree": "BS"}], '
            '"skills": ["Python"]}'
        )
        seen_max_tokens: list[int] = []

        async def fake_acompletion(**kwargs):
            seen_max_tokens.append(kwargs["max_tokens"])
            return _fake_response(truncated if len(seen_max_tokens) == 1 else full)

        fake_router = SimpleNamespace(acompletion=fake_acompletion)
        cfg = llm.LLMConfig(provider="openai", model="gpt-5-nano", api_key="sk-x")

        with (
            patch("app.llm.get_router", return_value=(fake_router, cfg)),
            patch("app.llm._supports_json_mode", return_value=False),
            patch("app.llm._get_retry_temperature", return_value=None),
        ):
            result = await llm.complete_json(
                prompt="parse this",
                max_tokens=4096,
                retries=2,
                schema_type="resume",
            )

        assert result == {
            "workExperience": [{"title": "SWE"}],
            "education": [{"degree": "BS"}],
            "skills": ["Python"],
        }
        assert len(seen_max_tokens) == 2
        # The retry must request MORE output room than the truncated first call.
        assert seen_max_tokens[1] > seen_max_tokens[0]
