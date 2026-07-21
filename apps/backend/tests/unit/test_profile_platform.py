"""Unit tests for projection platform (P4/P6), completion readiness, and AI (P5).

Covers projection options (template/visibility/overrides), the public/portfolio/
JSON-Resume projectors (with the round-trip invariant + no private-field leak),
ATS/AI readiness scoring, the pure skill normalizer + autocomplete, and the AI
suggestion guardrails when no model is configured.
"""

from __future__ import annotations

import pytest

from app.profile import ai
from app.profile.completion import compute_ai_readiness, compute_ats_readiness
from app.profile.import_adapters import derive_candidate
from app.profile.projection import ProjectionEngine
from app.profile.public import export_json_resume, project_portfolio, project_public_profile, slugify
from app.profile.schemas import ProfileData
from app.profile.skills import suggest_skills


def _rich_profile() -> ProfileData:
    return ProfileData.model_validate(
        {
            "identity": {
                "name": "Ada Lovelace",
                "headline": "Staff Engineer",
                "email": "ada@example.com",
                "phone": "+1 555 000",
                "salaryExpectation": "$250k",
                "visaStatus": "citizen",
                "linkedin": "https://linkedin.com/in/ada",
            },
            "summary": "Builds reliable systems.",
            "workExperience": [
                {"uid": "w1", "title": "Engineer", "company": "Acme", "years": "2020-2022", "description": ["Shipped X", "Led Y"]}
            ],
            "education": [{"institution": "MIT", "degree": "BS"}],
            "personalProjects": [{"name": "Proj", "description": ["Did"]}],
            "skills": {
                "technical": [{"canonical": "python", "displayName": "Python"}],
                "tools": [{"canonical": "docker", "displayName": "Docker"}],
                "languages": [{"canonical": "english", "displayName": "English"}],
            },
            "certifications": [{"name": "AWS SA", "issuer": "Amazon"}],
        }
    )


# ---------------------------------------------------------------------------
# Projection options (P4)
# ---------------------------------------------------------------------------


class TestProjectionOptions:
    def test_template_carried_into_meta(self):
        resume = ProjectionEngine.project_resume(_rich_profile(), options={"template": "modern"})
        assert resume["meta"]["template"] == "modern"

    def test_section_visibility_override(self):
        resume = ProjectionEngine.project_resume(
            _rich_profile(), options={"sections": {"education": False}}
        )
        edu_meta = next(m for m in resume["sectionMeta"] if m["key"] == "education")
        assert edu_meta["isVisible"] is False

    def test_overrides_applied_last(self):
        resume = ProjectionEngine.project_resume(
            _rich_profile(), options={"overrides": {"summary": "Tailored summary."}}
        )
        assert resume["summary"] == "Tailored summary."


# ---------------------------------------------------------------------------
# Public / portfolio / JSON Resume (P6)
# ---------------------------------------------------------------------------


class TestPublicProjection:
    def test_public_omits_private_fields(self):
        public = project_public_profile(_rich_profile())
        flat = str(public)
        assert "$250k" not in flat  # salary never leaked
        assert "+1 555 000" not in flat  # phone never leaked
        assert "citizen" not in flat  # visa never leaked
        assert public["identity"]["name"] == "Ada Lovelace"
        assert "Python" in public["skills"]

    def test_slugify(self):
        assert slugify("Ada Lovelace!") == "ada-lovelace"
        assert slugify("") == "profile"

    def test_portfolio_projects_first(self):
        portfolio = project_portfolio(_rich_profile())
        assert portfolio["projects"][0]["name"] == "Proj"
        assert portfolio["certifications"][0]["name"] == "AWS SA"

    def test_json_resume_roundtrip(self):
        exported = export_json_resume(_rich_profile())
        assert exported["basics"]["name"] == "Ada Lovelace"
        # Re-import the export and confirm the core content survives.
        reimported = derive_candidate("json_resume", {"data": exported})
        assert reimported.identity.name == "Ada Lovelace"
        assert reimported.workExperience[0].company == "Acme"
        assert reimported.identity.linkedin == "https://linkedin.com/in/ada"


# ---------------------------------------------------------------------------
# Readiness scoring (P5)
# ---------------------------------------------------------------------------


class TestReadiness:
    def test_ats_readiness_rewards_signals(self):
        empty = compute_ats_readiness(ProfileData())
        rich = compute_ats_readiness(_rich_profile())
        assert 0 <= empty < rich <= 100

    def test_ai_readiness_rewards_prose(self):
        empty = compute_ai_readiness(ProfileData())
        rich = compute_ai_readiness(_rich_profile())
        assert 0 <= empty < rich <= 100


# ---------------------------------------------------------------------------
# Skill engine (P5)
# ---------------------------------------------------------------------------


class TestSkillEngine:
    def test_normalize_dedupes_and_canonicalizes(self):
        profile = ProfileData.model_validate(
            {"skills": {"technical": [{"displayName": "js"}, {"displayName": "JavaScript"}, {"displayName": "python"}]}}
        )
        result = ai.normalize_skills(profile)
        names = [s["displayName"] for s in result["skills"]["technical"]]
        assert names.count("JavaScript") == 1  # js + JavaScript collapsed
        assert result["changed"] is True

    def test_suggest_skills_prefix(self):
        results = suggest_skills("java")
        assert any(r["displayName"] == "JavaScript" for r in results)

    def test_suggest_skills_empty(self):
        assert suggest_skills("") == []


# ---------------------------------------------------------------------------
# AI guardrails (P5)
# ---------------------------------------------------------------------------


class TestAiGuardrails:
    async def test_summary_needs_content(self, monkeypatch):
        monkeypatch.setattr(ai, "is_llm_available", lambda *a, **k: True)
        result = await ai.suggest_summary(ProfileData())
        # No existing content -> refuses to invent.
        assert result["suggestion"] is None
        assert "invent" in (result["note"] or "").lower()

    async def test_summary_needs_llm(self, monkeypatch):
        monkeypatch.setattr(ai, "is_llm_available", lambda *a, **k: False)
        result = await ai.suggest_summary(_rich_profile())
        assert result["suggestion"] is None
        assert "settings" in (result["note"] or "").lower()

    async def test_bullets_missing_experience(self, monkeypatch):
        monkeypatch.setattr(ai, "is_llm_available", lambda *a, **k: True)
        result = await ai.suggest_experience_bullets(_rich_profile(), "nonexistent")
        assert result["suggestion"] is None
