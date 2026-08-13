"""HTTP router for Job Discovery & Recommendations (design §9).

Exposes the discovery API under the ``/discovery`` prefix (mounted at
``/api/v1`` in :mod:`app.main`). Every route is guarded by the ``JOB_DISCOVERY``
kill-switch (Req 10.1/10.2): the router-level :func:`require_job_discovery_enabled`
dependency returns **404** for *all* routes when the feature is off, so a
disabled deployment is indistinguishable from one where the surface does not
exist (Req 11.3 — no capability leak).

Endpoints (design §9):

| Method & path                         | Purpose                              | Auth           |
|---------------------------------------|--------------------------------------|----------------|
| ``POST /discovery/recommend``         | Run discovery -> ranked recs         | verified user  |
| ``GET  /discovery/recommend/{id}``    | Last cached recommendations if fresh | effective user |
| ``POST /discovery/tailor``            | Handoff a listing -> create a job    | verified user  |
| ``GET  /discovery/recipes``           | List the user's site recipes         | effective user |
| ``POST /discovery/recipes``           | Create a recipe (validated)          | verified user  |
| ``PUT  /discovery/recipes/{slug}``    | Update a recipe                      | verified user  |
| ``DELETE /discovery/recipes/{slug}``  | Delete a recipe                      | verified user  |

Endpoints that trigger an LLM call (recommend, tailor) also carry
``llm_rate_limit_dep`` (Req 8, design §9).

Collaborators (settings, database, discovery service, tailor handler, auth) are
resolved through FastAPI dependencies so tests can override them via
``app.dependency_overrides`` without a live LLM/browser/scraper.

The tailor handoff *body* (fetch full JD for ``partial`` listings, then
``db.create_job``) is implemented in a later task; this router defines the
endpoint and delegates to an injectable handler (:func:`get_tailor_handler`)
whose default returns 501 until that handler is wired.

Design reference: ``.kiro/specs/job-discovery/design.md`` §9 (API surface).
Requirements: 1, 8, 9.4, 10.1, 10.2, 11.3.

NOTE: The path key for recipe update/delete is the recipe ``slug`` (the natural
per-user identifier the recipe service in :mod:`app.job_discovery.recipes`
operates on), rather than the numeric ``id`` sketched in the design table.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.config import Settings, settings
from app.database import Database
from app.job_discovery.models import (
    JobListing,
    Recommendation,
    SearchFilters,
    SearchQuery,
    SiteRecipe,
)
from app.job_discovery.recipes import (
    RecipeConflictError,
    RecipeLimitError,
    RecipeNotFoundError,
    RecipeValidationError,
    create_recipe,
    delete_recipe,
    list_recipes,
    update_recipe,
)
from app.job_discovery.service import (
    DiscoveryDisabledError,
    DiscoveryResult,
    DiscoveryService,
    ResumeData,
    ResumeNotFoundError,
)
from app.schemas.discovery import (
    JobListingOut,
    RecommendationOut,
    RecommendRequest,
    RecommendResponse,
    SearchQueryOut,
    SiteRecipeCreate,
    SiteRecipeOut,
    SiteRecipeUpdate,
    SourceFailureOut,
    TailorRequest,
    TailorResponse,
)

# --------------------------------------------------------------------------- #
# Dependencies — settings / kill-switch
# --------------------------------------------------------------------------- #


def get_settings_dep() -> Settings:
    """Return the active settings snapshot (overridable in tests)."""
    return settings


def require_job_discovery_enabled(
    config: Settings = Depends(get_settings_dep),
) -> None:
    """Kill-switch gate for the whole router (Req 10.1/10.2/11.3).

    When ``JOB_DISCOVERY`` is off, every route 404s — the discovery surface is
    invisible, not merely disabled, so a probe cannot tell the feature exists.
    Removing this dependency makes the kill-switch test fail.
    """
    if not config.JOB_DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        )


# --------------------------------------------------------------------------- #
# Dependencies — auth / rate limit
# --------------------------------------------------------------------------- #
# Wired to the real FitWright auth middleware (app.auth.principal) and the
# per-user LLM rate limiter (app.llm_ratelimit). Auth is enforced by the
# session middleware; these dependency functions extract the caller identity.

from app.auth import get_effective_user_id, require_verified_user_id
from app.llm_ratelimit import llm_rate_limit_dep


# --------------------------------------------------------------------------- #
# Dependencies — database / service
# --------------------------------------------------------------------------- #


@lru_cache
def _database_singleton() -> Database:
    from app.database import db
    return db


def get_db() -> Database:
    """Return the process-wide discovery :class:`Database` (overridable in tests)."""
    return _database_singleton()


async def _default_resume_loader(user_id: str, resume_id: str) -> ResumeData | None:
    """Ownership-scoped resume loader.

    Resolves the resume from the real database, scoped by user_id.
    Returns None when the resume is absent or not owned (ownership boundary).
    """
    from app.database import db

    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        return None

    # Extract text content: prefer raw text content, fall back to processed_data
    text = resume.get("content") or ""
    processed = resume.get("processed_data")

    # If content is the raw file bytes marker, try to get text from processed_data
    if not text and processed:
        # Build a text representation from structured data
        parts = []
        if isinstance(processed, dict):
            for section in ("personal_info", "experience", "education", "skills", "projects"):
                val = processed.get(section)
                if val:
                    parts.append(str(val))
        text = "\n".join(parts) if parts else ""

    # Version key: use updated_at or resume_id as cache key
    version = resume.get("updated_at") or resume.get("resume_id") or resume_id

    return ResumeData(
        resume_id=resume_id,
        text=text,
        processed=processed,
        version=str(version),
    )


def get_discovery_service(
    db: Database = Depends(get_db),
    config: Settings = Depends(get_settings_dep),
) -> DiscoveryService:
    """Assemble a :class:`DiscoveryService` for one request (overridable in tests)."""
    return DiscoveryService(
        db,
        resume_loader=_default_resume_loader,
        config=config,
    )


# ``(db, user_id, request) -> TailorResponse``. The default is a 501 stub; the
# tailor-handoff task supplies the real handler (fetch full JD for partial
# listings, then db.create_job).
async def _tailor_handler(
    db: Database, *, user_id: str, request: TailorRequest
) -> TailorResponse:
    """Real tailor handoff: creates a job record from the discovery listing.

    The job is created through the same path the manual JD-upload uses, so the
    tailor/builder flow can pick it up normally with {job_id, resume_id}.
    """
    listing = request.listing
    # Build job content from the listing (title + description)
    job_content = listing.description or f"{listing.title} at {listing.company}\n{listing.url}"

    # Create via the existing database method
    job = await db.create_job(
        user_id=user_id,
        content=job_content,
        resume_id=request.resume_id,
    )

    return TailorResponse(job_id=job["job_id"], resume_id=request.resume_id)


def get_tailor_handler():
    """Return the tailor-handoff handler."""
    return _tailor_handler


# --------------------------------------------------------------------------- #
# Mapping helpers: internal dataclasses -> wire models
# --------------------------------------------------------------------------- #
def _listing_out(listing: JobListing) -> JobListingOut:
    return JobListingOut(
        source=listing.source,
        title=listing.title,
        company=listing.company,
        location=listing.location,
        url=listing.url,
        is_remote=listing.is_remote,
        description=listing.description,
        posted_at=listing.posted_at,
        salary=listing.salary,
        fingerprint=listing.fingerprint,
    )


def _rec_out(rec: Recommendation) -> RecommendationOut:
    return RecommendationOut(
        listing=_listing_out(rec.listing),
        match_score=rec.match_score,
        partial=rec.partial,
        matched=list(rec.matched),
        missing=list(rec.missing),
    )


def _query_out(query: SearchQuery) -> SearchQueryOut:
    return SearchQueryOut(
        titles=list(query.titles),
        search_string=query.search_string,
        seniority=query.seniority,
        location=query.location,
        country_indeed=query.country_indeed,
        degraded=query.degraded,
    )


def _to_recommend_response(result: DiscoveryResult) -> RecommendResponse:
    return RecommendResponse(
        recommendations=[_rec_out(r) for r in result.recommendations],
        query=_query_out(result.query) if result.query else None,
        degraded=result.degraded,
        cached=result.cached,
        failures=[
            SourceFailureOut(source=f.source, reason=f.reason, kind=f.kind)
            for f in result.failures
        ],
    )


def _recipe_out(recipe: SiteRecipe) -> SiteRecipeOut:
    return SiteRecipeOut(
        id=recipe.id,
        user_id=recipe.user_id,
        name=recipe.name,
        slug=recipe.slug,
        base_url=recipe.base_url,
        search_url_template=recipe.search_url_template,
        schema_=recipe.schema or {},
        fetch_mode=recipe.fetch_mode,
        enabled=recipe.enabled,
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
    )


# --------------------------------------------------------------------------- #
# Router (kill-switch gated at the router level so EVERY route 404s when off)
# --------------------------------------------------------------------------- #
router = APIRouter(
    prefix="/discovery",
    tags=["discovery"],
    dependencies=[Depends(require_job_discovery_enabled)],
)


# --------------------------------------------------------------------------- #
# Recommend
# --------------------------------------------------------------------------- #
@router.post(
    "/recommend",
    response_model=RecommendResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def recommend(
    payload: RecommendRequest,
    user_id: str = Depends(require_verified_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
    db: Database = Depends(get_db),
) -> RecommendResponse:
    """Run discovery for ``{resume_id, filters}`` and return ranked recs (Req 1)."""
    filters = _filters_from_in(payload.filters)
    try:
        result = await service.recommend(
            user_id=user_id,
            resume_id=payload.resume_id,
            filters=filters,
            force_refresh=payload.force_refresh,
        )
    except DiscoveryDisabledError:
        # Defense in depth: the router gate already 404s when off.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        )
    except ResumeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="resume_not_found"
        )

    # Persist results to the feed so they're immediately visible
    if result.recommendations:
        feed_results = []
        for rec in result.recommendations:
            listing = rec.listing
            feed_results.append({
                "fingerprint": listing.fingerprint,
                "source": listing.source,
                "title": listing.title,
                "company": listing.company,
                "location": listing.location,
                "url": listing.url,
                "is_remote": listing.is_remote,
                "description": listing.description,
                "salary": listing.salary,
                "posted_at": listing.posted_at.isoformat() if listing.posted_at else None,
                "match_score": rec.match_score,
                "matched": list(rec.matched),
                "missing": list(rec.missing),
                "partial": rec.partial,
            })
        await db.upsert_discovery_results(user_id, "on-demand", feed_results)

    return _to_recommend_response(result)


@router.get("/recommend/{resume_id}", response_model=RecommendResponse)
async def cached_recommendation(
    resume_id: str,
    location: str | None = None,
    is_remote: bool | None = None,
    hours_old: int | None = None,
    results_wanted: int | None = None,
    country_indeed: str | None = None,
    user_id: str = Depends(get_effective_user_id),
    service: DiscoveryService = Depends(get_discovery_service),
) -> RecommendResponse:
    """Return the last *fresh* cached recommendations for a resume (design §9).

    The same filters passed to the originating ``POST /recommend`` must be
    supplied as query params so the content-addressed cache key matches. A cold
    or expired cache is a 404 (no fan-out is triggered).
    """
    filters = SearchFilters(
        location=location,
        is_remote=is_remote,
        hours_old=hours_old,
        results_wanted=results_wanted,
        country_indeed=country_indeed,
    )
    try:
        result = await service.cached_recommendation(
            user_id=user_id, resume_id=resume_id, filters=filters
        )
    except DiscoveryDisabledError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        )
    except ResumeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="resume_not_found"
        )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no_cached_recommendation",
        )
    return _to_recommend_response(result)


# --------------------------------------------------------------------------- #
# Tailor handoff (Req 8) — endpoint here; handler body wired in a later task.
# --------------------------------------------------------------------------- #
@router.post(
    "/tailor",
    response_model=TailorResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def tailor(
    payload: TailorRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
    handler=Depends(get_tailor_handler),
) -> TailorResponse:
    """Hand a chosen listing into the tailor flow -> ``{job_id, resume_id}`` (Req 8)."""
    return await handler(db, user_id=user_id, request=payload)


# --------------------------------------------------------------------------- #
# Site recipe CRUD (Req 4, 9.4)
# --------------------------------------------------------------------------- #
@router.get("/recipes", response_model=list[SiteRecipeOut])
async def list_site_recipes(
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> list[SiteRecipeOut]:
    """List the calling user's site recipes."""
    recipes = await list_recipes(db, user_id)
    return [_recipe_out(r) for r in recipes]


@router.post(
    "/recipes",
    response_model=SiteRecipeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_site_recipe(
    payload: SiteRecipeCreate,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> SiteRecipeOut:
    """Create a validated site recipe (Req 4)."""
    try:
        recipe = await create_recipe(
            db,
            user_id,
            name=payload.name,
            slug=payload.slug,
            base_url=payload.base_url,
            search_url_template=payload.search_url_template,
            schema=payload.schema_,
            fetch_mode=payload.fetch_mode,
            enabled=payload.enabled,
        )
    except RecipeValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    except RecipeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except RecipeLimitError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _recipe_out(recipe)


@router.put("/recipes/{slug}", response_model=SiteRecipeOut)
async def update_site_recipe(
    slug: str,
    payload: SiteRecipeUpdate,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> SiteRecipeOut:
    """Partially update an existing recipe (Req 4)."""
    try:
        recipe = await update_recipe(
            db,
            user_id,
            slug,
            name=payload.name,
            base_url=payload.base_url,
            search_url_template=payload.search_url_template,
            schema=payload.schema_,
            fetch_mode=payload.fetch_mode,
            enabled=payload.enabled,
        )
    except RecipeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    except RecipeValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )
    return _recipe_out(recipe)


@router.delete("/recipes/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site_recipe(
    slug: str,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> None:
    """Delete a recipe by slug (Req 4)."""
    try:
        await delete_recipe(db, user_id, slug)
    except RecipeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _filters_from_in(data) -> SearchFilters | None:
    if data is None:
        return None
    return SearchFilters(
        location=data.location,
        is_remote=data.is_remote,
        hours_old=data.hours_old,
        results_wanted=data.results_wanted,
        country_indeed=data.country_indeed,
    )


__all__ = ["router"]


# --------------------------------------------------------------------------- #
# Feed endpoints (Phase 1 — background discovery)
# --------------------------------------------------------------------------- #


@router.get("/feed", summary="Get the user's job discovery feed")
async def get_discovery_feed(
    status: str | None = None,
    sources: str | None = None,
    q: str | None = None,
    location: str | None = None,
    is_remote: bool | None = None,
    min_score: int | None = None,
    posted_within_hours: int | None = None,
    limit: int = 50,
    offset: int = 0,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
):
    """Paginated feed of discovered jobs (newest first).

    Filters, all optional and combined with AND:

    * ``status`` - new, interested, dismissed, tailored, applied
    * ``sources`` - comma-separated board ids; the feed then shows only those
      boards, so the list matches the platforms the user has selected
    * ``q`` - every token must appear in the title or company
    * ``location`` / ``is_remote`` - substring and flag match
    * ``min_score`` - match percentage floor, 0-100 as the UI shows it
    * ``posted_within_hours`` - recency window; jobs whose board published no
      date fall back to when we discovered them

    Filtering is done in the query rather than in the client so that ``total``
    and pagination describe the filtered set. A client-side filter over one page
    reports "3 of 228" and pages through rows the user cannot see.

    Not offered, deliberately: salary is stored as the free text the board
    printed ("competitive", "40-60 LPA"), so a numeric floor would silently
    drop rows it cannot parse, and job type is not persisted per result at all.
    An honest absence beats a filter that lies.
    """
    source_list = [s.strip() for s in (sources or "").split(",") if s.strip()] or None
    # The UI speaks percent; scores are stored 0..1.
    score_floor = max(0.0, min(100.0, float(min_score))) / 100 if min_score else None

    results = await db.get_discovery_feed(
        user_id,
        status=status,
        sources=source_list,
        query=q,
        location=location,
        is_remote=is_remote,
        min_score=score_floor,
        posted_within_hours=posted_within_hours,
        limit=min(limit, 100),
        offset=offset,
    )
    total = await db.count_discovery_feed(
        user_id,
        status=status,
        sources=source_list,
        query=q,
        location=location,
        is_remote=is_remote,
        min_score=score_floor,
        posted_within_hours=posted_within_hours,
    )
    unseen = await db.count_unseen_discovery_results(user_id)
    # Lets the UI hide the match-score filter rather than offer a control that
    # can only return nothing. Counted across the whole feed, not the filtered
    # set, so switching filters does not make the control flicker in and out.
    scored = await db.count_scored_discovery_results(user_id)

    # Mark results as seen on read
    if not offset:
        await db.mark_discovery_results_seen(user_id)

    return {
        "results": results,
        "total": total,
        "unseen": unseen,
        "scored": scored,
        "limit": limit,
        "offset": offset,
    }


@router.get("/feed/unseen", summary="Count unseen results (for badge)")
async def get_unseen_count(
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
):
    """Returns the count of new unseen job results (for nav badge)."""
    count = await db.count_unseen_discovery_results(user_id)
    return {"unseen": count}


@router.post("/feed/schedule", summary="Enable/configure background discovery")
async def schedule_discovery(
    resume_id: str,
    interval_hours: int = 24,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Enable background discovery for a resume. Creates or updates the schedule."""
    run = await db.get_or_create_discovery_run(user_id, resume_id, interval_hours)
    return {"schedule": run, "message": "Background discovery enabled"}


@router.post("/feed/schedule/toggle", summary="Enable/disable background discovery")
async def toggle_discovery_schedule(
    resume_id: str,
    enabled: bool = True,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Toggle background discovery on/off for a resume."""
    found = await db.toggle_discovery_run(user_id, resume_id, enabled)
    if not found:
        raise HTTPException(status_code=404, detail="No schedule found for this resume")
    return {"enabled": enabled, "message": f"Discovery {'enabled' if enabled else 'paused'}"}


# --------------------------------------------------------------------------- #
# Manual search endpoint (no resume required)
# --------------------------------------------------------------------------- #


class ManualSearchRequest(BaseModel):
    """Direct job search without resume — accepts raw query terms."""

    query: str  # e.g. "Backend Engineer Python"
    location: str | None = None
    is_remote: bool | None = None
    hours_old: int | None = None  # 24, 168 (week), 720 (month)
    results_wanted: int | None = 50
    country_indeed: str | None = None
    sites: list[str] | None = None  # ["indeed", "linkedin", "glassdoor", "naukri"]
    job_type: str | None = None  # fulltime, parttime, internship, contract
    distance: int | None = None  # miles from location (default 50)
    # Optional: match against a resume for scoring
    resume_id: str | None = None


@router.post("/search", summary="Manual job search (no resume required)")
async def manual_search(
    payload: ManualSearchRequest,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
    config: Settings = Depends(get_settings_dep),
):
    """Search jobs by raw query terms. No resume needed.

    If ``resume_id`` is provided, results are scored against that resume.
    Otherwise results are returned sorted by recency (no match scoring).
    If ``sites`` is provided, only those platforms are scraped.
    """
    _check_search_rate(user_id)  # Rate limit: 1 search per 10s per user
    from app.job_discovery.connectors.base import FailureReport, run_connector
    from app.job_discovery.connectors.jobspy import JobSpyConnector
    from app.job_discovery.models import SearchFilters, SearchQuery
    from app.job_discovery.normalize import normalize as normalize_listings

    # Build query from manual input
    query = SearchQuery(
        titles=[payload.query],
        search_string=payload.query,
        seniority=None,
        location=payload.location,
        country_indeed=payload.country_indeed,
        degraded=False,
    )

    filters = SearchFilters(
        location=payload.location,
        is_remote=payload.is_remote,
        hours_old=payload.hours_old,
        results_wanted=min(payload.results_wanted or 50, 100),
        country_indeed=payload.country_indeed,
        job_type=payload.job_type,
        distance=payload.distance,
    )

    # Determine which sites to scrape
    sites = payload.sites or config.job_discovery_jobspy_sites

    # Split sites into JobSpy-supported and extra platforms
    from app.job_discovery.connectors.extra_platforms import EXTRA_PLATFORMS, ExtraPlatformConnector
    jobspy_sites = [s for s in sites if s not in EXTRA_PLATFORMS]
    extra_sites = [s for s in sites if s in EXTRA_PLATFORMS]

    # Run all connectors
    report = FailureReport()
    raw: list = []

    # JobSpy connector (Indeed, LinkedIn, Glassdoor, Google, Naukri, ZipRecruiter)
    if jobspy_sites:
        connector = JobSpyConnector(sites=jobspy_sites)
        raw.extend(await run_connector(connector, query, filters, report))

    # Extra platforms connector (Remotive, WWR, SimplyHired, Hirist, Foundit, etc.)
    if extra_sites:
        extra_connector = ExtraPlatformConnector(sites=extra_sites)
        raw.extend(await extra_connector.search(query, filters, report.failures))

    # Normalize + dedup
    listings = normalize_listings(raw)

    # Optionally score against resume
    recommendations = []
    if payload.resume_id:
        # Load resume and rank
        resume = await db.get_resume(user_id, payload.resume_id)
        if resume and resume.get("processed_data"):
            from app.job_discovery.ranker import rank_listings
            recommendations = await rank_listings(
                user_id, listings, resume.get("processed_data"),
            )
        else:
            # No processed data — return without scoring
            for listing in listings:
                recommendations.append({
                    "listing": {
                        "source": listing.source,
                        "title": listing.title,
                        "company": listing.company,
                        "location": listing.location,
                        "url": listing.url,
                        "is_remote": listing.is_remote,
                        "description": listing.description,
                        "posted_at": listing.posted_at.isoformat() if listing.posted_at else None,
                        "salary": listing.salary,
                        "fingerprint": listing.fingerprint,
                    },
                    "match_score": 0,
                    "partial": not listing.description,
                    "matched": [],
                    "missing": [],
                })
    else:
        # No resume — return raw results without scoring
        for listing in listings:
            recommendations.append({
                "listing": {
                    "source": listing.source,
                    "title": listing.title,
                    "company": listing.company,
                    "location": listing.location,
                    "url": listing.url,
                    "is_remote": listing.is_remote,
                    "description": listing.description,
                    "posted_at": listing.posted_at.isoformat() if listing.posted_at else None,
                    "salary": listing.salary,
                    "fingerprint": listing.fingerprint,
                },
                "match_score": 0,
                "partial": not listing.description,
                "matched": [],
                "missing": [],
            })

    # If we got Recommendation dataclass objects from rank_listings, convert
    if recommendations and hasattr(recommendations[0], 'listing'):
        converted = []
        for rec in recommendations:
            converted.append({
                "listing": {
                    "source": rec.listing.source,
                    "title": rec.listing.title,
                    "company": rec.listing.company,
                    "location": rec.listing.location,
                    "url": rec.listing.url,
                    "is_remote": rec.listing.is_remote,
                    "description": rec.listing.description,
                    "posted_at": rec.listing.posted_at.isoformat() if rec.listing.posted_at else None,
                    "salary": rec.listing.salary,
                    "fingerprint": rec.listing.fingerprint,
                },
                "match_score": rec.match_score,
                "partial": rec.partial,
                "matched": list(rec.matched),
                "missing": list(rec.missing),
            })
        recommendations = converted

    # Persist to feed
    if recommendations:
        feed_results = []
        for r in recommendations:
            listing = r["listing"]
            feed_results.append({
                "fingerprint": listing["fingerprint"],
                "source": listing["source"],
                "title": listing["title"],
                "company": listing["company"],
                "location": listing["location"],
                "url": listing["url"],
                "is_remote": listing.get("is_remote"),
                "description": listing.get("description"),
                "salary": listing.get("salary"),
                "posted_at": listing.get("posted_at"),
                "match_score": r.get("match_score", 0),
                "matched": r.get("matched", []),
                "missing": r.get("missing", []),
                "partial": r.get("partial", False),
            })
        await db.upsert_discovery_results(user_id, "manual-search", feed_results)

    return {
        "results": recommendations[:min(payload.results_wanted or 50, 100)],
        "total": len(recommendations),
        "query": payload.query,
        "sites": sites,
        "degraded": report.degraded,
        "failures": [{"source": f.source, "reason": f.reason} for f in report.failures],
    }


# --------------------------------------------------------------------------- #
# Backend Improvements (Items 1-10)
# --------------------------------------------------------------------------- #


# 1. Status update endpoint
class StatusUpdateRequest(BaseModel):
    status: str  # new, interested, dismissed, tailored, applied


@router.patch("/feed/{result_id}/status", summary="Update job status")
async def update_result_status(
    result_id: str,
    payload: StatusUpdateRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Move a feed result between statuses: new → interested → applied → dismissed."""
    valid_statuses = {"new", "interested", "dismissed", "tailored", "applied"}
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {valid_statuses}")

    from sqlalchemy import update as sa_update
    from app.models import DiscoveryResult
    from datetime import datetime, timezone

    async with db._session() as session:
        async with session.begin():
            result = await session.execute(
                sa_update(DiscoveryResult)
                .where(
                    (DiscoveryResult.id == result_id)
                    & (DiscoveryResult.user_id == user_id)
                )
                .values(status=payload.status)
            )
            if (result.rowcount or 0) == 0:
                raise HTTPException(status_code=404, detail="Result not found")

    return {"id": result_id, "status": payload.status}


# 6. Rate limiting on /search (simple in-memory per-user throttle)
_search_timestamps: dict[str, float] = {}
_SEARCH_COOLDOWN_SECONDS = 10  # min 10s between searches per user


def _check_search_rate(user_id: str) -> None:
    """Raise 429 if user searched too recently."""
    import time
    now = time.time()
    last = _search_timestamps.get(user_id, 0)
    if now - last < _SEARCH_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {int(_SEARCH_COOLDOWN_SECONDS - (now - last))}s before searching again",
        )
    _search_timestamps[user_id] = now


# 8. Scheduled run editing
class ScheduleUpdateRequest(BaseModel):
    interval_hours: int | None = None
    resume_id: str | None = None  # switch to a different resume


@router.patch("/feed/schedule", summary="Edit discovery schedule")
async def edit_discovery_schedule(
    payload: ScheduleUpdateRequest,
    resume_id: str | None = None,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Edit the interval or resume of an existing discovery schedule."""
    from sqlalchemy import select, update as sa_update
    from app.models import DiscoveryRun
    from datetime import datetime, timezone

    async with db._session() as session:
        async with session.begin():
            # Find existing schedule
            stmt = select(DiscoveryRun).where(DiscoveryRun.user_id == user_id)
            if resume_id:
                stmt = stmt.where(DiscoveryRun.resume_id == resume_id)
            result = await session.execute(stmt)
            run = result.scalar_one_or_none()
            if not run:
                raise HTTPException(status_code=404, detail="No schedule found")

            values = {"updated_at": datetime.now(timezone.utc).isoformat()}
            if payload.interval_hours is not None:
                values["interval_hours"] = max(1, min(168, payload.interval_hours))
            if payload.resume_id:
                values["resume_id"] = payload.resume_id

            await session.execute(
                sa_update(DiscoveryRun).where(DiscoveryRun.id == run.id).values(**values)
            )

    return {"message": "Schedule updated", "changes": {k: v for k, v in values.items() if k != "updated_at"}}


# 10. Feed cleanup/TTL endpoint
@router.post("/feed/cleanup", summary="Archive old feed results")
async def cleanup_feed(
    days: int = 30,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Remove feed results older than N days (default 30). Keeps 'interested' and 'applied'."""
    from sqlalchemy import delete as sa_delete
    from app.models import DiscoveryResult
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(7, days))).isoformat()

    async with db._session() as session:
        async with session.begin():
            result = await session.execute(
                sa_delete(DiscoveryResult).where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.created_at < cutoff)
                    & (DiscoveryResult.status.in_(["new", "dismissed"]))
                )
            )
            deleted = result.rowcount or 0

    return {"deleted": deleted, "cutoff_days": days}
