"""HTTP request/response contracts for Job Discovery & Recommendations.

These pydantic models are the wire-level shapes the discovery router
(``routers/discovery.py``) validates and serializes. They are deliberately
separate from the transport-agnostic in-process dataclasses in
:mod:`app.job_discovery.models`; a thin mapping layer in the service converts
between the two.

Design reference: ``.kiro/specs/job-discovery/design.md`` §9 (API surface).
Requirements: 1.4, 4.3, 6.1, 7.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.job_discovery.models import FetchMode

# --------------------------------------------------------------------------- #
# Shared / nested
# --------------------------------------------------------------------------- #


class SearchFiltersIn(BaseModel):
    """User-supplied search constraints (Req 3).

    Every field is optional; the pipeline applies what it understands and
    ignores the rest so a single unknown filter never fails a request.
    """

    model_config = ConfigDict(extra="ignore")

    location: str | None = None
    is_remote: bool | None = None
    hours_old: int | None = Field(default=None, ge=0)
    results_wanted: int | None = Field(default=None, ge=1)
    country_indeed: str | None = None


class JobListingOut(BaseModel):
    """A normalized job posting as returned to the client (design §3.2)."""

    source: str
    title: str
    company: str
    location: str
    url: str
    is_remote: bool | None = None
    description: str | None = None
    posted_at: datetime | None = None
    salary: str | None = None
    fingerprint: str = ""


class RecommendationOut(BaseModel):
    """A ranked listing with match metadata (design §3.2, Req 7)."""

    listing: JobListingOut
    match_score: float = Field(ge=0, le=100)
    partial: bool = False
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class SourceFailureOut(BaseModel):
    """A single non-fatal source failure surfaced to the caller (Req 3.2, 4)."""

    source: str
    reason: str
    kind: str | None = None


class SearchQueryOut(BaseModel):
    """The search intent the pipeline actually used (design §5, Req 2)."""

    titles: list[str] = Field(default_factory=list)
    search_string: str = ""
    seniority: str | None = None
    location: str | None = None
    country_indeed: str | None = None
    degraded: bool = False


# --------------------------------------------------------------------------- #
# Recommend
# --------------------------------------------------------------------------- #


class RecommendRequest(BaseModel):
    """Body for ``POST /discovery/recommend`` (Req 1)."""

    model_config = ConfigDict(extra="ignore")

    resume_id: str
    filters: SearchFiltersIn | None = None
    # Bypass the content-addressed cache and re-run the fan-out.
    force_refresh: bool = False


class RecommendResponse(BaseModel):
    """Response for a recommend / cached-get call (Req 1, 3.2, 7).

    ``degraded`` is True when any source failed or the query was synthesized
    without the LLM, so the client can show a partial-results banner.
    """

    recommendations: list[RecommendationOut] = Field(default_factory=list)
    query: SearchQueryOut | None = None
    degraded: bool = False
    cached: bool = False
    failures: list[SourceFailureOut] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Tailor handoff
# --------------------------------------------------------------------------- #


class TailorRequest(BaseModel):
    """Body for the tailor handoff (Req 8): create a job from a listing and
    hand back the ids the tailor flow needs."""

    model_config = ConfigDict(extra="ignore")

    resume_id: str
    listing: JobListingOut


class TailorResponse(BaseModel):
    """Result of the tailor handoff: the created job + the resume to tailor."""

    job_id: str
    resume_id: str


# --------------------------------------------------------------------------- #
# Site recipe CRUD
# --------------------------------------------------------------------------- #


class SiteRecipeCreate(BaseModel):
    """Body for creating a custom-site recipe (Req 4)."""

    model_config = ConfigDict(extra="ignore")

    name: str
    slug: str
    base_url: str
    search_url_template: str
    schema_: dict = Field(default_factory=dict, alias="schema")
    fetch_mode: FetchMode = "http"
    enabled: bool = True


class SiteRecipeUpdate(BaseModel):
    """Body for updating a recipe; all fields optional (partial update)."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    base_url: str | None = None
    search_url_template: str | None = None
    schema_: dict | None = Field(default=None, alias="schema")
    fetch_mode: FetchMode | None = None
    enabled: bool | None = None


class SiteRecipeOut(BaseModel):
    """A persisted recipe as returned to the client (design §3.2, Req 4)."""

    model_config = ConfigDict(populate_by_name=True)

    id: int | None = None
    user_id: str
    name: str
    slug: str
    base_url: str
    search_url_template: str
    schema_: dict = Field(default_factory=dict, serialization_alias="schema")
    fetch_mode: FetchMode = "http"
    enabled: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "SearchFiltersIn",
    "JobListingOut",
    "RecommendationOut",
    "SourceFailureOut",
    "SearchQueryOut",
    "RecommendRequest",
    "RecommendResponse",
    "TailorRequest",
    "TailorResponse",
    "SiteRecipeCreate",
    "SiteRecipeUpdate",
    "SiteRecipeOut",
]
