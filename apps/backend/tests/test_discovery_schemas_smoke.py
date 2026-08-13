"""Smoke test: import and instantiate every discovery model/schema.

Guards the Wave 0 canonical shapes (``app.job_discovery.models``) and the
wire contracts (``app.schemas.discovery``) against import/definition
regressions. No behavior is asserted beyond "it constructs".

Requirements: 1.4, 4.3, 6.1, 7.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.job_discovery.models import (
    JobListing,
    Recommendation,
    SearchFilters,
    SearchQuery,
    SiteRecipe,
    SourceFailure,
)
from app.schemas import (
    JobListingOut,
    RecommendationOut,
    RecommendRequest,
    RecommendResponse,
    SearchFiltersIn,
    SearchQueryOut,
    SiteRecipeCreate,
    SiteRecipeOut,
    SiteRecipeUpdate,
    SourceFailureOut,
    TailorRequest,
    TailorResponse,
)

pytestmark = pytest.mark.unit


def _job_listing() -> JobListing:
    return JobListing(
        source="indeed",
        title="Senior Backend Engineer",
        company="Acme",
        location="Bengaluru, IN",
        url="https://example.com/jobs/1",
        is_remote=True,
        description="Build things.",
        posted_at=datetime.now(timezone.utc),
        salary="₹40L",
        fingerprint="abc123",
    )


def test_canonical_models_instantiate():
    filters = SearchFilters(location="Bengaluru", is_remote=True, results_wanted=25)
    query = SearchQuery(
        titles=["Backend Engineer"],
        search_string="backend engineer",
        location="Bengaluru",
        degraded=False,
        resume_version="v1",
    )
    listing = _job_listing()
    rec = Recommendation(
        listing=listing,
        match_score=87.5,
        partial=False,
        matched=["python"],
        missing=["kubernetes"],
    )
    recipe = SiteRecipe(
        user_id="u1",
        name="Careers Page",
        slug="careers-page",
        base_url="https://jobs.example.com",
        search_url_template="https://jobs.example.com/search?q={query}",
        schema={"title": "string"},
        fetch_mode="http",
    )
    failure = SourceFailure(source="naukri", reason="blocked", kind="blocked")

    assert filters.results_wanted == 25
    assert query.titles == ["Backend Engineer"]
    assert rec.listing.company == "Acme"
    assert recipe.fetch_mode == "http"
    assert failure.kind == "blocked"


def test_wire_schemas_instantiate():
    listing_out = JobListingOut(
        source="indeed",
        title="Senior Backend Engineer",
        company="Acme",
        location="Bengaluru, IN",
        url="https://example.com/jobs/1",
    )
    rec_out = RecommendationOut(listing=listing_out, match_score=87.5)
    query_out = SearchQueryOut(titles=["Backend Engineer"], search_string="be")
    failure_out = SourceFailureOut(source="naukri", reason="blocked", kind="blocked")

    recommend_req = RecommendRequest(
        resume_id="r1",
        filters=SearchFiltersIn(location="Bengaluru", results_wanted=10),
        force_refresh=True,
    )
    recommend_resp = RecommendResponse(
        recommendations=[rec_out],
        query=query_out,
        degraded=True,
        cached=False,
        failures=[failure_out],
    )
    tailor_req = TailorRequest(resume_id="r1", listing=listing_out)
    tailor_resp = TailorResponse(job_id="j1", resume_id="r1")

    recipe_create = SiteRecipeCreate(
        name="Careers Page",
        slug="careers-page",
        base_url="https://jobs.example.com",
        search_url_template="https://jobs.example.com/search?q={query}",
        **{"schema": {"title": "string"}},
    )
    recipe_update = SiteRecipeUpdate(enabled=False)
    recipe_out = SiteRecipeOut(
        id=1,
        user_id="u1",
        name="Careers Page",
        slug="careers-page",
        base_url="https://jobs.example.com",
        search_url_template="https://jobs.example.com/search?q={query}",
    )

    assert recommend_req.filters.results_wanted == 10
    assert recommend_req.force_refresh is True
    assert recommend_resp.recommendations[0].match_score == 87.5
    assert recommend_resp.degraded is True
    assert tailor_req.listing.title == "Senior Backend Engineer"
    assert tailor_resp.job_id == "j1"
    # `schema` alias populates the internal `schema_` field.
    assert recipe_create.schema_ == {"title": "string"}
    assert recipe_update.enabled is False
    assert recipe_out.slug == "careers-page"
    # Serialization uses the `schema` alias, not the internal name.
    assert "schema" in recipe_out.model_dump(by_alias=True)
