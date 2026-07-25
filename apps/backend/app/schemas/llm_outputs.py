"""Validated internal contracts for structured provider responses.

These models sit at the LLM trust boundary. They intentionally tolerate extra
provider fields (Pydantic's default) while requiring every shape consumed by a
workflow, so harmless additions do not break compatibility and malformed core
fields trigger the shared content retry path.
"""

from pydantic import BaseModel, Field, model_validator

from app.schemas.models import ResumeData
from app.schemas.resume_wizard import ResumeWizardParsedEntry


class SkillTargetItem(BaseModel):
    skill: str
    reason: str = ""


class SkillTargetPlanOutput(BaseModel):
    # Older prompts/providers emitted plain strings; retain that safe alias.
    target_skills: list[SkillTargetItem | str]
    strategy_notes: str = ""


class EnrichmentEnhancementOutput(BaseModel):
    additional_bullets: list[str] | None = None
    # Backward-compatible key used by earlier prompt versions.
    enhanced_description: list[str] | None = None

    @model_validator(mode="after")
    def _require_a_bullet_key(self) -> "EnrichmentEnhancementOutput":
        if self.additional_bullets is None and self.enhanced_description is None:
            raise ValueError("additional_bullets is required")
        return self


class RegeneratedBulletsOutput(BaseModel):
    new_bullets: list[str]
    change_summary: str = ""


class RegeneratedSkillsOutput(BaseModel):
    new_skills: list[str]
    change_summary: str = ""


class WizardNextQuestionOutput(BaseModel):
    text: str
    # Keep this open to preserve the service's deterministic invalid-section
    # fallback rather than rejecting an otherwise useful provider response.
    section: str


class WizardTurnOutput(BaseModel):
    resume_data: ResumeData
    next_question: WizardNextQuestionOutput
    inferred_skills: list[str] = Field(default_factory=list)
    is_complete: bool = False


class WizardBulletsOutput(BaseModel):
    bullets: list[str]


class WizardParsedEntriesOutput(BaseModel):
    entries: list[ResumeWizardParsedEntry]


class ProfileSummaryOutput(BaseModel):
    summary: str


class ProfileBulletsOutput(BaseModel):
    bullets: list[str]


class UserAdditionProject(BaseModel):
    """A project the user explicitly asked to add via tailoring instructions."""

    name: str = ""
    years: str = ""
    description: list[str] = Field(default_factory=list)


class UserAdditionExperience(BaseModel):
    """A role/experience the user explicitly asked to add."""

    title: str = ""
    company: str = ""
    years: str = ""
    description: list[str] = Field(default_factory=list)


class UserAdditionsOutput(BaseModel):
    """Structured extraction of ONLY the additions the user requested in their
    free-text tailoring instructions. Empty lists mean "nothing to add" - the
    common case where instructions are pure steering."""

    projects: list[UserAdditionProject] = Field(default_factory=list)
    experiences: list[UserAdditionExperience] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
