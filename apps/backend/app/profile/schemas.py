"""``ProfileData`` — the canonical professional-profile document + DTOs.

``ProfileData`` is a backward-compatible **superset** of ``ResumeData`` so a
resume can be *derived* by projection (``app/profile/projection.py``) and a
parsed resume can be *merged in* (``app/profile/merge.py``). Design:
docs/architecture/PROFILE_SYSTEM_PLAN.md §13–§16.

Key properties:
- **Professional Identity** first-class block (``identity``).
- **Canonical skills** (``Skill`` with canonical/aliases/proficiency/confidence).
- **AI Memory** as a separate namespace (``aiMemory``) — never projected into a
  resume (ADR-11).
- **Stable uids** on every list item (ADR-8) minted on creation.
- **Compact provenance** in ``meta.provenance`` (ADR-9), not inline per field.
- Reuses the resume-schema text-coercion helpers so parsed content normalizes
  the same way it does for resumes.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.schemas.models import (
    CustomSection,
    SectionMeta,
    _coerce_optional_text,
    _coerce_string_list,
    _coerce_text,
)

__all__ = [
    "ProfileData",
    "ProfileIdentity",
    "ProfileExperience",
    "ProfileEducation",
    "ProfileProject",
    "Skill",
    "ProfileSkills",
    "Certification",
    "Achievement",
    "ProfileLink",
    "AiMemory",
    "ProfileMeta",
    "ProfileResponse",
    "ProfileUpdateRequest",
    "ProfileCompletenessResponse",
    "CompletenessSuggestion",
    "GenerateResumeRequest",
    "GenerateResumeResponse",
    "ProfileVersionMeta",
    "ProfileVersionListResponse",
    "ProfileVersionDataResponse",
    "FieldChange",
    "MergeOperation",
    "MergePlan",
    "ImportStatistics",
    "ImportPreviewResponse",
    "ApplyMergeRequest",
    "ApplyMergeResponse",
    "AiMemoryUpdateRequest",
    "SkillSuggestion",
    "SkillSuggestResponse",
    "AiSuggestRequest",
    "AiSuggestResponse",
    "SyncPreviewRequest",
    "SyncChange",
    "SyncPreviewResponse",
    "SyncApplyRequest",
    "PublicProfileResponse",
    "PublishRequest",
    "PublicationStateResponse",
    "PublicProfilePageResponse",
    "ProfileSearchResult",
    "ProfileSearchResponse",
    "ProfileAnalyticsResponse",
    "MERGE_RESOLUTIONS",
    "PROVENANCE_SOURCES",
    "PROFICIENCY_LEVELS",
    "new_uid",
]

# Resolution verbs a user can pick for a merge operation (design §P3).
MERGE_RESOLUTIONS = ("accept", "reject", "keep_existing", "replace", "merge")

PROVENANCE_SOURCES = ("manual", "import", "merge", "ai", "migration")
PROFICIENCY_LEVELS = ("beginner", "intermediate", "advanced", "expert")


def new_uid() -> str:
    """Mint a fresh stable uid for a profile list item (ADR-8)."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Professional identity layer
# ---------------------------------------------------------------------------


class ProfileIdentity(BaseModel):
    """The "who am I professionally" header read first by every projection."""

    name: str = ""
    headline: str = ""
    currentRole: str = ""
    currentCompany: str = ""
    yearsExperience: float | None = None
    industry: str = ""
    careerStage: str = ""  # e.g. student | early | mid | senior | lead | exec
    targetRoles: list[str] = Field(default_factory=list)
    careerObjective: str = ""
    # Availability / logistics.
    employmentStatus: str = ""  # employed | open | not_looking | freelance
    availability: str = ""  # immediate | 2_weeks | 1_month | passive
    remotePreference: str = ""  # onsite | hybrid | remote | any
    relocation: bool | None = None
    noticePeriod: str = ""
    workAuthorization: str = ""
    visaStatus: str = ""
    preferredLocations: list[str] = Field(default_factory=list)
    # Private + future-ready; never projected unless explicitly requested.
    salaryExpectation: str = ""
    careerVisibility: Literal["private", "unlisted", "public"] = "private"

    # Contact (mirrors resume PersonalInfo so projection is lossless).
    email: str = ""
    phone: str = ""
    location: str = ""
    timezone: str = ""
    website: str | None = None
    linkedin: str | None = None
    github: str | None = None
    avatarUrl: str | None = None  # reference to users.avatar_url (bytes stored once)

    @field_validator("targetRoles", "preferredLocations", mode="before")
    @classmethod
    def _norm_list(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


# ---------------------------------------------------------------------------
# Core sections (each item carries a stable uid — ADR-8)
# ---------------------------------------------------------------------------


class ProfileExperience(BaseModel):
    """A work-experience entry (superset of resume Experience)."""

    uid: str = Field(default_factory=new_uid)
    title: str = ""
    company: str = ""
    location: str | None = None
    years: str = ""
    current: bool = False
    description: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)

    @field_validator("description", "tech", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


class ProfileEducation(BaseModel):
    """An education entry."""

    uid: str = Field(default_factory=new_uid)
    institution: str = ""
    degree: str = ""
    years: str = ""
    description: str | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> str | None:
        return _coerce_optional_text(value)


class ProfileProject(BaseModel):
    """A personal/portfolio project entry."""

    uid: str = Field(default_factory=new_uid)
    name: str = ""
    role: str = ""
    years: str = ""
    github: str | None = None
    website: str | None = None
    description: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)
    # KG relation (ADR-7): the experience this project was delivered under.
    experienceUid: str | None = None

    @field_validator("description", "tech", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


class Skill(BaseModel):
    """A canonical skill (Canonical Skill Engine — ADR-12).

    ``canonical`` is the normalized identity (e.g. ``javascript``);
    ``displayName`` is what the UI shows (e.g. ``JavaScript``); ``aliases``
    captures variants seen in source material. ``evidenceUids`` links to the
    experiences/projects that demonstrate the skill (KG relation — ADR-7).
    """

    uid: str = Field(default_factory=new_uid)
    canonical: str = ""
    displayName: str = ""
    aliases: list[str] = Field(default_factory=list)
    category: str = ""  # e.g. technical | soft | language | tool
    subcategory: str = ""
    yearsExperience: float | None = None
    proficiency: str = ""  # one of PROFICIENCY_LEVELS (soft-validated)
    lastUsed: str = ""
    confidence: float | None = None
    verificationSource: str = ""
    aiNormalizedName: str = ""
    evidenceUids: list[str] = Field(default_factory=list)

    @field_validator("aliases", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


class ProfileSkills(BaseModel):
    """Skills grouped for projection convenience (richer than resume additional)."""

    technical: list[Skill] = Field(default_factory=list)
    soft: list[Skill] = Field(default_factory=list)
    languages: list[Skill] = Field(default_factory=list)
    tools: list[Skill] = Field(default_factory=list)


class Certification(BaseModel):
    """A certification / license / training."""

    uid: str = Field(default_factory=new_uid)
    name: str = ""
    issuer: str = ""
    date: str = ""
    url: str | None = None


class Achievement(BaseModel):
    """A standalone achievement / award / honor / publication / patent.

    ``kind`` distinguishes the future-modeled sections (award, publication,
    patent, volunteer, organization) without a separate table each.
    """

    uid: str = Field(default_factory=new_uid)
    kind: str = "achievement"
    title: str = ""
    description: str | None = None
    date: str = ""
    url: str | None = None
    # KG relation (ADR-7): the experience/project this achievement belongs to.
    relatedUid: str | None = None

    @field_validator("description", mode="before")
    @classmethod
    def _norm(cls, value: Any) -> str | None:
        return _coerce_optional_text(value)


class ProfileLink(BaseModel):
    """An external link (migrates users.links here)."""

    uid: str = Field(default_factory=new_uid)
    label: str = ""
    url: str = ""
    kind: str = ""  # portfolio | github | linkedin | twitter | other


class AiMemory(BaseModel):
    """AI Memory — a separate namespace that steers generation (ADR-11).

    **Never projected into a resume.** Captures learned preferences so AI
    assists produce consistent, on-voice output.
    """

    writingStyle: str = ""
    tone: str = ""
    atsPreference: str = ""
    templatePreference: str = ""
    targetCompanies: list[str] = Field(default_factory=list)
    targetIndustries: list[str] = Field(default_factory=list)
    dos: list[str] = Field(default_factory=list)
    donts: list[str] = Field(default_factory=list)

    @field_validator(
        "targetCompanies", "targetIndustries", "dos", "donts", mode="before"
    )
    @classmethod
    def _norm(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


class ProvenanceEntry(BaseModel):
    """Compact provenance for one entity-uid or field path (ADR-9)."""

    source: Literal["manual", "import", "merge", "ai", "migration"] = "manual"
    at: str = ""
    confidence: float | None = None
    verificationSource: str = ""


class ProfileMeta(BaseModel):
    """Document metadata: schema version, source, and the provenance map."""

    schemaVersion: int = 1
    source: str = "manual"
    lastImportedResumeId: str | None = None
    # entity-uid / field-path -> provenance. Absent ⇒ manual (safe default).
    provenance: dict[str, ProvenanceEntry] = Field(default_factory=dict)


class ProfileData(BaseModel):
    """The complete canonical profile document (superset of ResumeData)."""

    identity: ProfileIdentity = Field(default_factory=ProfileIdentity)
    summary: str = ""
    workExperience: list[ProfileExperience] = Field(default_factory=list)
    education: list[ProfileEducation] = Field(default_factory=list)
    personalProjects: list[ProfileProject] = Field(default_factory=list)
    skills: ProfileSkills = Field(default_factory=ProfileSkills)
    certifications: list[Certification] = Field(default_factory=list)
    achievements: list[Achievement] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    links: list[ProfileLink] = Field(default_factory=list)
    customSections: dict[str, CustomSection] = Field(default_factory=dict)
    # Ordering/visibility for a *generated* resume (same shape as resume).
    sectionMeta: list[SectionMeta] = Field(default_factory=list)
    aiMemory: AiMemory = Field(default_factory=AiMemory)
    meta: ProfileMeta = Field(default_factory=ProfileMeta)

    @field_validator("summary", mode="before")
    @classmethod
    def _norm_summary(cls, value: Any) -> str:
        return _coerce_text(value)

    @field_validator("interests", mode="before")
    @classmethod
    def _norm_interests(cls, value: Any) -> list[str]:
        return _coerce_string_list(value)


# ---------------------------------------------------------------------------
# API DTOs
# ---------------------------------------------------------------------------


class ProfileResponse(BaseModel):
    """Full profile read: the document + cached completeness + CAS version."""

    data: ProfileData
    completeness: int = 0
    version: int = 1
    updated_at: str | None = None


class ProfileUpdateRequest(BaseModel):
    """Partial profile update (If-Match CAS via ``base_version``).

    The client sends the full ``data`` document it edited (mass-assignment is
    prevented by validating into ``ProfileData`` — unknown top-level keys are
    dropped by Pydantic). ``base_version`` is the version the client last read;
    the server applies the write only if it still matches (409 otherwise).
    """

    data: ProfileData
    base_version: int = Field(ge=1)


class CompletenessSuggestion(BaseModel):
    """A prioritized "add X to reach Y%" nudge."""

    key: str
    label: str
    weight: int
    done: bool = False


class ProfileCompletenessResponse(BaseModel):
    """Weighted completeness score + prioritized suggestions + readiness bands."""

    score: int = 0
    suggestions: list[CompletenessSuggestion] = Field(default_factory=list)
    ats_readiness: int = 0
    ai_readiness: int = 0


class GenerateResumeRequest(BaseModel):
    """Generate a resume from the profile (Projection Engine — ADR-6).

    ``persist`` = save the projected data as a new resume (optionally master);
    otherwise the projection is returned for preview only. ``template`` and
    ``sections`` (per-section visibility, keyed by section ``key``) let the
    caller tailor the generated resume without mutating the profile.
    """

    title: str | None = None
    persist: bool = False
    as_master: bool = False
    include_photo: bool = False
    # Full per-resume photo configuration (Photo System). Takes precedence over
    # ``include_photo``; a plain dict validated by the Projection Engine against
    # ``app.profile.photo.PhotoConfig`` so this schema stays decoupled.
    photo: dict[str, Any] | None = None
    template: str | None = None
    sections: dict[str, bool] | None = None
    # Persisted appearance (frontend ``TemplateSettings`` shape). Stored verbatim
    # on the generated resume so it opens — and exports — in the chosen template.
    template_settings: dict[str, Any] | None = None


class GenerateResumeResponse(BaseModel):
    """Projected resume data + (when persisted) the new resume id."""

    resume_data: dict[str, Any]
    resume_id: str | None = None


class ProfileVersionMeta(BaseModel):
    """Metadata-only projection of a profile snapshot (no ``data_gz``)."""

    id: str
    profile_id: str
    source: str
    label: str | None = None
    content_hash: str
    size_bytes: int = 0
    created_at: str


class ProfileVersionListResponse(BaseModel):
    """Keyset-paginated snapshot metadata list."""

    items: list[ProfileVersionMeta] = Field(default_factory=list)
    next_cursor: str | None = None


class ProfileVersionDataResponse(ProfileVersionMeta):
    """A single snapshot's metadata + decompressed document."""

    data: ProfileData


# ---------------------------------------------------------------------------
# Merge / Import Engine DTOs (P3)
# ---------------------------------------------------------------------------


class FieldChange(BaseModel):
    """A single field-level difference within a matched entity."""

    field: str
    existing: Any = None
    incoming: Any = None


class MergeOperation(BaseModel):
    """One planned change from merging an imported document into the profile.

    ``id`` is deterministic (``section:incoming_ref``) so the client can echo
    per-operation resolutions back and the server re-derives the identical plan
    (stateless apply). ``op`` classifies the change; ``default_resolution`` is a
    safe default (never destructive to manual data) the user can override.
    """

    id: str
    section: str
    op: Literal["add", "update", "duplicate", "conflict"]
    label: str = ""
    confidence: float = 1.0
    similarity: float | None = None
    existing_uid: str | None = None
    existing: Any = None
    incoming: Any = None
    changes: list[FieldChange] = Field(default_factory=list)
    default_resolution: str = "accept"
    allowed_resolutions: list[str] = Field(default_factory=list)


class MergePlan(BaseModel):
    """A reviewable set of operations + rollup counts by ``op``."""

    operations: list[MergeOperation] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


class ImportStatistics(BaseModel):
    """Quality + shape signals for an import, surfaced before the user commits.

    ``quality_score`` is the weighted completeness of the *incoming* candidate
    (0..100) — a quick read on how much usable content the source carried.
    ``sections`` counts parsed items per section; the op tallies mirror the plan.
    """

    quality_score: int = 0
    sections: dict[str, int] = Field(default_factory=dict)
    total_operations: int = 0
    new_items: int = 0
    updates: int = 0
    conflicts: int = 0
    duplicates: int = 0


class ImportPreviewResponse(BaseModel):
    """Preview of importing a source: the derived candidate + the merge plan.

    ``incoming`` is echoed so the client sends it back verbatim on apply, letting
    the server re-derive the same plan without server-side session state.
    """

    source: str
    incoming: ProfileData
    plan: MergePlan
    statistics: ImportStatistics = Field(default_factory=ImportStatistics)
    warnings: list[str] = Field(default_factory=list)


class ApplyMergeRequest(BaseModel):
    """Apply a reviewed merge (version-CAS)."""

    incoming: ProfileData
    resolutions: dict[str, str] = Field(default_factory=dict)
    base_version: int = Field(ge=1)
    source: Literal["import", "merge"] = "import"


class ApplyMergeResponse(BaseModel):
    """The updated profile after applying a merge + what changed."""

    data: ProfileData
    completeness: int
    version: int
    applied: int = 0
    skipped: int = 0


# ---------------------------------------------------------------------------
# Synchronization DTOs (P4)
# ---------------------------------------------------------------------------


class SyncPreviewRequest(BaseModel):
    """Preview syncing the profile into an existing resume (regenerate/refresh)."""

    include_photo: bool = False


class SyncChange(BaseModel):
    """One field-level change the sync would apply to the resume."""

    path: str
    action: Literal["added", "removed", "changed"]
    before: Any = None
    after: Any = None


class SyncPreviewResponse(BaseModel):
    """Diff between a resume's current data and a fresh projection of the profile."""

    resume_id: str
    resume_version: int = 1
    changes: list[SyncChange] = Field(default_factory=list)
    projected: dict[str, Any] = Field(default_factory=dict)
    immutable: bool = False
    reason: str | None = None


class SyncApplyRequest(BaseModel):
    """Apply the projection to a resume (CAS on the resume's ``version``)."""

    base_version: int = Field(ge=1)
    include_photo: bool = False


# ---------------------------------------------------------------------------
# AI layer DTOs (P5)
# ---------------------------------------------------------------------------


class AiMemoryUpdateRequest(BaseModel):
    """Update the AI-memory namespace (CAS on the profile version)."""

    aiMemory: AiMemory
    base_version: int = Field(ge=1)


class SkillSuggestion(BaseModel):
    """A canonical-skill autocomplete suggestion."""

    canonical: str
    displayName: str
    category: str = ""


class SkillSuggestResponse(BaseModel):
    """Autocomplete results for the skill editor."""

    suggestions: list[SkillSuggestion] = Field(default_factory=list)


class AiSuggestRequest(BaseModel):
    """Request an AI improvement for a target field (never fabricates facts)."""

    kind: Literal[
        "summary",
        "experience_bullets",
        "skills_normalize",
        "skills_gap",
        "keywords",
    ]
    experience_uid: str | None = None


class AiSuggestResponse(BaseModel):
    """AI suggestion output — a proposed value the user can accept/reject."""

    kind: str
    suggestion: Any = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Public projection DTOs (P6)
# ---------------------------------------------------------------------------


class PublicProfileResponse(BaseModel):
    """A safe, public-facing projection of the profile (no private fields)."""

    slug: str
    visibility: Literal["private", "unlisted", "public"]
    identity: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    experience: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    education: list[dict[str, Any]] = Field(default_factory=list)


class PublishRequest(BaseModel):
    """Publish the profile publicly (P7)."""

    visibility: Literal["public", "unlisted"] = "public"
    slug: str | None = None
    theme: Literal["minimal", "modern", "developer"] | None = None


class PublicationStateResponse(BaseModel):
    """The owner-facing publish state of the profile."""

    public_slug: str | None = None
    visibility: Literal["private", "unlisted", "public"] = "private"
    public_theme: Literal["minimal", "modern", "developer"] = "minimal"


class PublicProfilePageResponse(BaseModel):
    """Anonymous public page payload: projection + SEO structured data."""

    profile: PublicProfileResponse
    json_ld: dict[str, Any] = Field(default_factory=dict)
    indexable: bool = False
    theme: Literal["minimal", "modern", "developer"] = "minimal"


class ProfileSearchResult(BaseModel):
    """One ranked, highlighted profile search hit."""

    type: str
    uid: str
    section: str
    title: str = ""
    subtitle: str = ""
    snippet: str = ""
    score: float = 0.0


class ProfileSearchResponse(BaseModel):
    """Ranked results for an in-profile search."""

    query: str
    results: list[ProfileSearchResult] = Field(default_factory=list)


class ProfileAnalyticsResponse(BaseModel):
    """Per-user usage analytics snapshot (non-PII counters + completeness gauge)."""

    counters: dict[str, int] = Field(default_factory=dict)
    completeness: int = 0
    total_events: int = 0
