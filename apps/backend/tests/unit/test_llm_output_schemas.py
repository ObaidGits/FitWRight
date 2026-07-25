"""Contracts for structured responses consumed by AI workflows."""

import pytest
from pydantic import ValidationError

from app.schemas.llm_outputs import (
    EnrichmentEnhancementOutput,
    ProfileBulletsOutput,
    ProfileSummaryOutput,
    RegeneratedBulletsOutput,
    RegeneratedSkillsOutput,
    SkillTargetPlanOutput,
    WizardBulletsOutput,
    WizardParsedEntriesOutput,
    WizardTurnOutput,
)

pytestmark = pytest.mark.unit


def test_skill_plan_accepts_documented_and_legacy_targets():
    parsed = SkillTargetPlanOutput.model_validate(
        {"target_skills": [{"skill": "Python", "reason": "present"}, "SQL"]}
    )
    assert len(parsed.target_skills) == 2
    with pytest.raises(ValidationError):
        SkillTargetPlanOutput.model_validate({"target_skills": "Python"})


def test_enrichment_outputs_require_typed_bullet_shapes():
    EnrichmentEnhancementOutput.model_validate({"additional_bullets": []})
    EnrichmentEnhancementOutput.model_validate({"enhanced_description": ["Legacy"]})
    with pytest.raises(ValidationError):
        EnrichmentEnhancementOutput.model_validate({})
    with pytest.raises(ValidationError):
        RegeneratedBulletsOutput.model_validate({"new_bullets": "not-a-list"})
    with pytest.raises(ValidationError):
        RegeneratedSkillsOutput.model_validate({"new_skills": [1]})


def test_wizard_outputs_validate_nested_resume_and_entries():
    valid_turn = {
        "resume_data": {},
        "next_question": {"text": "Next?", "section": "unknown-safe-fallback"},
    }
    WizardTurnOutput.model_validate(valid_turn)
    WizardBulletsOutput.model_validate({"bullets": ["Built APIs"]})
    WizardParsedEntriesOutput.model_validate(
        {"entries": [{"title": "Engineer", "description": ["Built APIs"]}]}
    )
    with pytest.raises(ValidationError):
        WizardParsedEntriesOutput.model_validate({"entries": "invalid"})


def test_profile_outputs_reject_wrong_core_types():
    ProfileSummaryOutput.model_validate({"summary": "Concise summary"})
    ProfileBulletsOutput.model_validate({"bullets": ["Built APIs"]})
    with pytest.raises(ValidationError):
        ProfileSummaryOutput.model_validate({"summary": ["wrong"]})
    with pytest.raises(ValidationError):
        ProfileBulletsOutput.model_validate({"bullets": "wrong"})


def test_job_keywords_coerces_numeric_experience_years():
    """Regression (live-test find): the extraction prompt asks for
    ``experience_years`` as an integer ("5+ years" -> 5), so the schema must
    accept a number and coerce it to a string rather than rejecting valid model
    output and failing the whole keyword-extraction step."""
    from app.schemas.models import JobAnalyzeKeywords

    parsed = JobAnalyzeKeywords.model_validate(
        {
            "required_skills": ["Python", "AWS"],
            "experience_years": 5,  # integer, exactly as the prompt example shows
            "seniority_level": "senior",
        }
    )
    assert parsed.experience_years == "5"
    assert parsed.required_skills == ["Python", "AWS"]

    # Strings still pass through unchanged.
    assert JobAnalyzeKeywords.model_validate({"experience_years": "5+"}).experience_years == "5+"
    # Absent -> None.
    assert JobAnalyzeKeywords.model_validate({}).experience_years is None
