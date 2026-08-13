"""Canonical internal shapes for Job Discovery & Recommendations.

These are the in-process dataclasses the pipeline passes between stages
(connectors → normalize → dedup → rank → cache). They are intentionally
transport-agnostic; the wire-level request/response models live in
:mod:`app.schemas.discovery`.

Design reference: ``.kiro/specs/job-discovery/design.md`` §3.2 (canonical shapes)
and §5 (query generation).
Requirements: 1.4, 4.3, 6.1, 7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# Fetch strategy for a connector / site recipe.
#   "http"    → bounded httpx, no browser (Req 5.1)
#   "stealth" → headless stealth browser via app/jd/browser (Req 5.2)
FetchMode = Literal["http", "stealth"]


@dataclass
class SearchFilters:
    """User-supplied constraints passed through to the connectors (Req 3).

    Every field is optional; connectors apply what they understand and ignore
    the rest so a single connector never fails on an unknown filter.
    """

    location: str | None = None
    is_remote: bool | None = None
    hours_old: int | None = None
    results_wanted: int | None = None
    # Resolved Indeed country code, e.g. "india" (Req 3, derivable from location).
    country_indeed: str | None = None
    # Job type: fulltime, parttime, internship, contract
    job_type: str | None = None
    # Distance in miles from location (default 50)
    distance: int | None = None


@dataclass
class SearchQuery:
    """Normalized search intent produced by ``query.py`` (design §5, Req 2).

    Built from the resume via the LLM, or synthesized deterministically on LLM
    failure with :attr:`degraded` set (Req 2.2). Cached by :attr:`resume_version`
    (Req 2.3).
    """

    # 1–3 target job titles inferred from the resume.
    titles: list[str] = field(default_factory=list)
    # Boolean-style search string suitable for Indeed and friends.
    search_string: str = ""
    seniority: str | None = None
    location: str | None = None
    country_indeed: str | None = None
    # True when the query was synthesized without the LLM (fallback path).
    degraded: bool = False
    # Resume content version used as the query/cache key.
    resume_version: str | None = None


@dataclass
class JobListing:
    """A normalized job posting from any source (design §3.2)."""

    source: str  # "indeed" | "naukri" | recipe slug | ...
    title: str
    company: str
    location: str
    url: str  # direct apply/view URL
    is_remote: bool | None = None
    description: str | None = None
    posted_at: datetime | None = None
    salary: str | None = None
    # Fingerprint from app/jd/fingerprint over (title, company, location, url).
    fingerprint: str = ""


@dataclass
class Recommendation:
    """A ranked :class:`JobListing` with match metadata (design §3.2, Req 7)."""

    listing: JobListing
    match_score: float  # 0..100
    partial: bool = False  # scored without a full description (Req 7.2)
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass
class SiteRecipe:
    """A persisted custom-site scraping recipe (design §3.2, §4, Req 4).

    Uniqueness is ``(user_id, slug)``. ``schema`` is the JSON extraction schema
    handed to the LLM extraction strategy.
    """

    user_id: str
    name: str
    slug: str
    base_url: str
    search_url_template: str
    schema: dict = field(default_factory=dict)
    fetch_mode: FetchMode = "http"
    enabled: bool = True
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class SourceFailure:
    """A single non-fatal source failure collected during fan-out (Req 3.2, 4).

    Connectors **never raise** on a single-source failure; they append one of
    these to a shared report so the service can still return partial results and
    surface a ``degraded`` signal to the caller.
    """

    source: str
    reason: str
    # Coarse classification, e.g. "blocked" | "timeout" | "unavailable" | "error".
    kind: str | None = None


__all__ = [
    "FetchMode",
    "SearchFilters",
    "SearchQuery",
    "JobListing",
    "Recommendation",
    "SiteRecipe",
    "SourceFailure",
]
