"""Schemas for the adaptive one-question-at-a-time AI resume wizard."""

from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.schemas.models import Education, Experience, Project, ResumeData

ResumeWizardSection = Literal[
    "intro",
    "contact",
    "summary",
    "workExperience",
    "internships",  # mapped onto workExperience by the service merge layer
    "education",
    "personalProjects",
    "skills",
    "review",
]

ResumeWizardStep = Literal["intro", "question", "review", "complete"]

ResumeWizardAction = Literal[
    "start", "answer", "skip", "back", "review", "structured"
]

# Fields on ``personalInfo`` a structured turn is allowed to set (W-P1.1). Kept
# explicit so a structured payload can never write arbitrary/unknown attributes.
STRUCTURED_PERSONAL_INFO_FIELDS = frozenset(
    {"name", "title", "email", "phone", "location", "website", "linkedin", "github"}
)


class ResumeWizardQuestion(BaseModel):
    """A single question the wizard asks."""

    text: str = ""
    section: ResumeWizardSection = "intro"


class ResumeSectionConfidence(BaseModel):
    """A per-section quality label surfaced to the user (W-P2.3)."""

    section: str
    level: Literal["missing", "weak", "fair", "strong"]


class ResumeScores(BaseModel):
    """Deterministic quality signals shown live instead of a step count (W-P2.3)."""

    completeness: int = 0
    ats: int = 0
    sections: list[ResumeSectionConfidence] = Field(default_factory=list)


class ResumeWizardProgress(BaseModel):
    """Server-computed progress for the question card's bar.

    ``total`` is a FIXED milestone count (Identity, Contact, Experience,
    Education, Skills, Summary) so the denominator never grows while the user
    answers (see W-P0.3). ``current`` is the number of those milestones already
    satisfied by ``resume_data``.
    """

    current: int = 0
    total: int = 6


class ResumeWizardAnswer(BaseModel):
    """User answer for one wizard turn."""

    text: str = Field(min_length=1, max_length=6000)

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer text must not be blank")
        return value


class ResumeWizardStructuredUpdate(BaseModel):
    """A deterministic, no-LLM update for a structured section (W-P1.1/W-P1.2).

    Carries discrete field values (identity/contact) and/or a confirmed skills
    list (chips). The client may name the ``next_section`` to drive the ordered
    structured portion of the flow; when omitted the server falls back to the
    next empty content section.
    """

    personal_info: dict[str, str] = Field(default_factory=dict)
    technical_skills: list[str] | None = None
    # A single structured Education entry to append/replace (W-P2.1/W-P2.2).
    education: Education | None = None
    # Structured Experience/Project entries to append/replace (W-P2.2 - hybrid
    # cards: structured facts + AI-drafted bullets). Lists so a parsed multi-role
    # paste can be confirmed in one submit. ``internships`` reuse ``experiences``
    # (they map onto workExperience).
    experiences: list[Experience] | None = None
    projects: list[Project] | None = None
    next_section: ResumeWizardSection | None = None

    @field_validator("experiences", "projects")
    @classmethod
    def _cap_entries(cls, value: list | None) -> list | None:
        if value is not None and len(value) > 25:
            raise ValueError("too many entries")
        return value

    @field_validator("personal_info")
    @classmethod
    def _validate_personal_info(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for field, raw in value.items():
            if field not in STRUCTURED_PERSONAL_INFO_FIELDS:
                continue  # ignore unknown keys rather than 422 the whole turn
            if not isinstance(raw, str):
                raise ValueError("personal_info values must be strings")
            if len(raw) > 500:
                raise ValueError("personal_info value too long")
            cleaned[field] = raw
        return cleaned

    @field_validator("technical_skills")
    @classmethod
    def _validate_skills(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if len(value) > 100:
            raise ValueError("too many skills")
        for item in value:
            if not isinstance(item, str):
                raise ValueError("skills must be strings")
            if len(item) > 100:
                raise ValueError("skill too long")
        return value


class ResumeWizardHistoryEntry(BaseModel):
    """One answered question, with a pre-answer draft snapshot for Back."""

    question: str
    answer: str
    section: ResumeWizardSection
    resume_data_before: ResumeData


class ResumeWizardState(BaseModel):
    """Complete state that round-trips between client and server."""

    step: ResumeWizardStep = "intro"
    resume_data: ResumeData = Field(default_factory=ResumeData)
    current_question: ResumeWizardQuestion = Field(default_factory=ResumeWizardQuestion)
    history: list[ResumeWizardHistoryEntry] = Field(default_factory=list)
    asked_count: int = 0
    inferred_skills: list[str] = Field(default_factory=list)
    is_complete: bool = False
    progress: ResumeWizardProgress = Field(default_factory=ResumeWizardProgress)
    warnings: list[str] = Field(default_factory=list)
    # The answer the user gave to the question restored by a ``back`` action, so
    # the client can repopulate the input for editing (W-P0.1). Empty for every
    # other transition.
    restored_answer: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def scores(self) -> ResumeScores:
        """Live, deterministic quality scores derived from ``resume_data`` (W-P2.3).

        Output-only: recomputed on every serialization so the client always sees
        scores consistent with the current draft, and any client-sent value is
        ignored. Imported locally to avoid a schema->service import at module load.
        """
        from app.services.resume_score import compute_resume_scores

        return compute_resume_scores(self.resume_data)


class ResumeWizardTurnRequest(BaseModel):
    """Request for one wizard turn."""

    state: ResumeWizardState
    action: ResumeWizardAction
    answer: ResumeWizardAnswer | None = None
    structured: ResumeWizardStructuredUpdate | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "ResumeWizardTurnRequest":
        if self.action == "answer" and self.answer is None:
            raise ValueError("answer is required for answer actions")
        if self.action == "structured" and self.structured is None:
            raise ValueError("structured payload is required for structured actions")
        return self


class ResumeWizardTurnResponse(BaseModel):
    """Response for one wizard turn."""

    state: ResumeWizardState


# ---------------------------------------------------------------------------
# Hybrid Experience/Project assist (W-P2.2): focused AI helpers that never
# mutate wizard state - they return content the client shows for confirmation.
# ---------------------------------------------------------------------------

ResumeWizardAssistKind = Literal["draft_bullets", "parse_entries"]

# Only these sections may drive the assist helpers (facts-heavy list sections).
_ASSIST_SECTIONS = frozenset({"workExperience", "internships", "personalProjects"})


class ResumeWizardAssistRequest(BaseModel):
    """Request for a focused AI assist (bullet drafting or paste parsing)."""

    kind: ResumeWizardAssistKind
    section: ResumeWizardSection
    # For ``draft_bullets``: a plain "what I did" description (+ optional facts for
    # context). For ``parse_entries``: the pasted resume blob.
    text: str = Field(min_length=1, max_length=8000)
    title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _validate_section(self) -> "ResumeWizardAssistRequest":
        if self.section not in _ASSIST_SECTIONS:
            raise ValueError("assist is only available for experience/project sections")
        return self


class ResumeWizardParsedEntry(BaseModel):
    """A structured entry parsed from pasted text (for user confirmation)."""

    title: str = ""
    company: str = ""
    location: str = ""
    years: str = ""
    name: str = ""  # projects
    role: str = ""  # projects
    description: list[str] = Field(default_factory=list)


class ResumeWizardAssistResponse(BaseModel):
    """Response for an assist call: drafted bullets and/or parsed entries."""

    bullets: list[str] = Field(default_factory=list)
    entries: list[ResumeWizardParsedEntry] = Field(default_factory=list)


class ResumeWizardFinalizeRequest(BaseModel):
    """Request to save the wizard draft as a resume.

    ``is_master`` is the user's intent:
      - ``True``  -> set this as the master resume (only honoured when none exists);
      - ``False`` -> save as a regular (non-master) resume;
      - ``None``  -> default: become the master only if the user has none yet.
    The server never silently replaces an existing master.
    """

    state: ResumeWizardState
    is_master: bool | None = None

    @model_validator(mode="after")
    def _validate_ready_to_finalize(self) -> "ResumeWizardFinalizeRequest":
        if not self.state.resume_data.personalInfo.name.strip():
            raise ValueError("personalInfo.name is required")
        return self


class ResumeWizardFinalizeResponse(BaseModel):
    """Response after creating the master resume."""

    message: str
    request_id: str
    resume_id: str
    processing_status: Literal["ready"] = "ready"
    is_master: bool
