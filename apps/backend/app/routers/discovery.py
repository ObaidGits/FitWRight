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
from pydantic import BaseModel, Field

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
    # Scores are stored 0..100, the same scale the UI prints, so the filter value
    # passes through unchanged. It was briefly divided by 100 here, which made a
    # "70%+ match" filter accept anything scoring 1 or more - invisible at the
    # time because no job in the feed had been scored at all.
    score_floor = max(0.0, min(100.0, float(min_score))) if min_score else None

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
    # What scoring the rest would involve. Shown before the action, because each
    # unscored job costs one AI call and the user should see the size of that.
    from app.job_discovery.scoring import count_unscored

    unscored = await count_unscored(db, user_id)

    # One row per job already, from the query. This only adds the labels naming
    # the other boards that carry it, so collapsing is visible rather than silent.
    annotated = await db.annotate_duplicate_sources(user_id, results)

    # Mark results as seen on read
    if not offset:
        await db.mark_discovery_results_seen(user_id)

    return {
        "results": annotated,
        # Distinct jobs, matching what the list returns, so pagination cannot walk
        # past the end of the real set.
        "total": total,
        "shown": len(annotated),
        "unseen": unseen,
        "scored": scored,
        "unscored": unscored,
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
    """Search jobs by raw query terms, waiting for the result.

    Kept synchronous for the browser extension and any caller that wants the rows
    in the response. The web app uses ``/search/start`` instead, because a scrape
    routinely outlives Heroku's 30-second request ceiling.
    """
    _check_search_rate(user_id)  # Rate limit: 1 search per 10s per user
    return await _execute_manual_search(payload, user_id, db, config)


@router.post("/search/start", status_code=202, summary="Start a background job search")
async def start_manual_search(
    payload: ManualSearchRequest,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
    config: Settings = Depends(get_settings_dep),
):
    """Kick off a search and return immediately with an id to poll.

    A scrape across several boards takes 15-35 seconds and Heroku destroys any
    request still open at 30, so the synchronous endpoint reported failure for
    searches that had in fact worked. Detaching the work from the request removes
    that ceiling entirely: the response lands in milliseconds and the page follows
    progress through ``/search/progress/{search_id}``.
    """
    _check_search_rate(user_id)

    from app.job_discovery import search_jobs

    # One at a time: a second concurrent search competes with the first for the
    # same rate-limited boards and makes both slower.
    existing = search_jobs.running_for(user_id)
    if existing is not None:
        return {**existing.to_dict(), "already_running": True}

    sites = payload.sites or config.job_discovery_jobspy_sites

    async def _work(job: "search_jobs.SearchJob"):
        return await _execute_manual_search(payload, user_id, db, config, job=job)

    job = search_jobs.start(user_id, payload.query, list(sites), _work)
    return {**job.to_dict(), "already_running": False}


@router.get("/search/progress/{search_id}", summary="Progress of a background search")
async def manual_search_progress(
    search_id: str,
    user_id: str = Depends(get_effective_user_id),
):
    """Report how a background search is going.

    An id the server no longer knows about comes back as ``expired`` rather than
    404, because after a restart that is the truth and the useful instruction is
    "reload the feed and stop polling".
    """
    from app.job_discovery import search_jobs

    return search_jobs.get(user_id, search_id)


async def _execute_manual_search(
    payload: ManualSearchRequest,
    user_id: str,
    db: Database,
    config: Settings,
    job=None,
):
    """Run one manual search: scrape, normalize, optionally score, persist.

    Shared by the synchronous endpoint and the background one. ``job``, when given,
    is updated as each board finishes so the UI can report real progress instead of
    an unqualified spinner. Rate limiting belongs to the callers, not here - a
    background job must not re-check a limit its own start already consumed.
    """
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
        found = await run_connector(connector, query, filters, report)
        raw.extend(found)
        if job is not None:
            # Attributed to the group: JobSpy scrapes its boards as one call, so
            # per-board counts are not separable here without lying about them.
            for site in jobspy_sites:
                job.site_finished(site)
            job.found = len(raw)

    # Extra platforms connector (Remotive, WWR, SimplyHired, Hirist, Foundit, etc.)
    if extra_sites:
        extra_connector = ExtraPlatformConnector(sites=extra_sites)
        raw.extend(await extra_connector.search(query, filters, report.failures))
        if job is not None:
            for site in extra_sites:
                job.site_finished(site)
            job.found = len(raw)

    if job is not None:
        for failure in report.failures:
            job.note_failure(failure.source, failure.reason)

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
        if job is not None:
            # What actually reached the feed, which is what the user cares about -
            # `found` counts scraped rows before dedup against what they already had.
            job.saved = len(feed_results)

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
    """Move a feed result between statuses: new → interested → applied → dismissed.

    Saving a job (``interested``) also puts it in the apply queue. Before this, the
    feed and the tracker were separate tables that never met: a user could save
    twenty jobs and find an empty queue. Marking it interested *is* the intent to
    apply, so it creates the queue entry rather than making them say it twice.

    Dismissing removes it from the queue again, but only if it is still waiting.
    An application already sent is history, and changing your mind about the
    listing is no reason to erase it.
    """
    valid_statuses = {"new", "interested", "dismissed", "tailored", "applied"}
    if payload.status not in valid_statuses:
        raise HTTPException(status_code=422, detail=f"Invalid status. Must be one of: {valid_statuses}")

    from sqlalchemy import update as sa_update
    from app.models import DiscoveryResult
    from datetime import datetime, timezone

    from app.job_discovery.queueing import ensure_queued_application, unqueue_application

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

    row = await db.get_discovery_result(user_id, result_id)
    queued = False

    if payload.status == "interested" and row:
        created = await ensure_queued_application(db, user_id, row)
        if created:
            queued = True
            # Remember the link so dismissing later knows exactly what to remove
            # instead of guessing from company and role.
            if not row.get("job_id"):
                await db.set_discovery_result_job(user_id, result_id, created["job_id"])
    elif payload.status == "dismissed" and row:
        await unqueue_application(db, user_id, row.get("job_id"))

    return {"id": result_id, "status": payload.status, "queued": queued}


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


class BulkStatusRequest(BaseModel):
    result_ids: list[str] = Field(default_factory=list, max_length=200)
    status: str


@router.patch("/feed/bulk-status", summary="Update several jobs at once")
async def bulk_update_status(
    payload: BulkStatusRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Move several feed results at once.

    Triaging a few hundred jobs one click at a time is the difference between a
    feed that gets used and one that gets abandoned, and a bulk request also keeps
    the queue side consistent: saving fifteen jobs individually was fifteen round
    trips, each re-reading the same profile and resume.

    Bounded at 200 ids per call. Dismissing in bulk is the common case, so it
    matters that this shares the single-row path's rules rather than reimplementing
    them - the same queue entries are created and removed.
    """
    valid_statuses = {"new", "interested", "dismissed", "tailored", "applied"}
    if payload.status not in valid_statuses:
        raise HTTPException(
            status_code=422, detail=f"Invalid status. Must be one of: {valid_statuses}"
        )
    if not payload.result_ids:
        return {"updated": 0, "queued": 0}

    from sqlalchemy import update as sa_update

    from app.job_discovery.queueing import ensure_queued_application, unqueue_application
    from app.models import DiscoveryResult

    async with db._session() as session:
        async with session.begin():
            result = await session.execute(
                sa_update(DiscoveryResult)
                .where(
                    (DiscoveryResult.user_id == user_id)
                    & DiscoveryResult.id.in_(payload.result_ids)
                )
                .values(status=payload.status)
            )
            updated = result.rowcount or 0

    # Queue side, one row at a time on purpose: `create_application` dedupes per
    # job, and a partial failure should cost one job rather than the whole batch.
    queued = 0
    for result_id in payload.result_ids:
        row = await db.get_discovery_result(user_id, result_id)
        if row is None:
            continue
        if payload.status == "interested":
            created = await ensure_queued_application(db, user_id, row)
            if created:
                queued += 1
                if not row.get("job_id"):
                    await db.set_discovery_result_job(user_id, result_id, created["job_id"])
        elif payload.status == "dismissed":
            await unqueue_application(db, user_id, row.get("job_id"))

    return {"updated": updated, "queued": queued}


class ScoreFeedRequest(BaseModel):
    resume_id: str | None = None
    # Bounded per call so a 200-job feed cannot become one enormous request.
    limit: int = 40


@router.post("/feed/score", summary="Score unscored feed jobs against a resume")
async def score_feed(
    payload: ScoreFeedRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Score jobs that have no match score yet.

    Scores exist only for jobs matched against a resume; a keyword harvest stores
    none. So most of a feed is unscored, and the score filter has nothing to work
    with.

    Deliberately explicit rather than automatic. Scoring reads each job
    description through the keyword extractor, which is an LLM call per job
    (cached by content, but the first pass is real). Quietly scoring 200 jobs
    because a user pressed Search once would spend their budget without asking.
    The UI shows the count before this runs, and ``limit`` caps a single call.
    """
    from app.job_discovery.scoring import score_unscored_results

    resume = (
        await db.get_resume(user_id, payload.resume_id)
        if payload.resume_id
        else await db.get_master_resume(user_id)
    )
    if resume is None:
        raise HTTPException(status_code=404, detail="resume_not_found")

    scored, remaining = await score_unscored_results(
        db, user_id, resume, limit=max(1, min(payload.limit, 100))
    )
    return {"scored": scored, "remaining": remaining, "resume_id": resume["resume_id"]}


class ForgetResult(BaseModel):
    """What was removed, so the user sees the size of what they just deleted."""

    captured_jobs: int = 0
    learned_answers: int = 0
    board_health: int = 0


@router.delete(
    "/data",
    response_model=ForgetResult,
    summary="Delete everything the extension contributed",
)
async def forget_extension_data(
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> ForgetResult:
    """Remove the data the browser extension put here.

    Uninstalling an extension removes the extension. Everything it sent - captured
    jobs, the questions it learned from forms, per-board health - stays on the
    server, and until now there was no way to ask for it back. "I changed my mind
    about this feature" deserves an answer better than editing a database.

    Deliberately narrow. It removes what the *extension* contributed and nothing
    else: applications, resumes and the Profile are the user's own work, created
    through the app, and are not this endpoint's business.

    The line between "extension exhaust" and "the user's work" is whether they acted
    on it, not what created it. A feed row they marked interested is a decision; a
    question they answered is an answer. Both survive, even though the extension put
    them there. Only untouched captures and still-unanswered questions go.
    """
    from sqlalchemy import delete as sa_delete

    from app.models import ApplicationField, BoardHealth, DiscoveryResult

    async with db._session() as session:  # noqa: SLF001
        async with session.begin():
            jobs = await session.execute(
                sa_delete(DiscoveryResult).where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.source == "extension")
                    # Untouched rows only: anything the user decided about stays.
                    & (DiscoveryResult.status.in_(["new", "dismissed"]))
                )
            )
            answers = await session.execute(
                sa_delete(ApplicationField).where(
                    (ApplicationField.user_id == user_id)
                    # Unanswered questions only. `source` is set when the row is
                    # created and never changes, so filtering on it would delete
                    # answers the user typed themselves - the row was still born
                    # from a form report. An answered question is the user's work
                    # whatever created the row, exactly as an "interested" feed row
                    # is a decision whatever harvested it.
                    & (ApplicationField.status == "needs_answer")
                )
            )
            boards = await session.execute(
                sa_delete(BoardHealth).where(BoardHealth.user_id == user_id)
            )

    return ForgetResult(
        captured_jobs=jobs.rowcount or 0,
        learned_answers=answers.rowcount or 0,
        board_health=boards.rowcount or 0,
    )


@router.get("/board-health", summary="Which boards are actually working")
async def get_board_health(
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
):
    """Per-board status, worst first, plus which ones need attention.

    Answers the question a user cannot answer for themselves: "is this board
    broken, or is my search too narrow?"
    """
    from app.job_discovery.board_health import FAILURE_THRESHOLD, list_health

    boards = await list_health(db, user_id)
    return {
        "boards": boards,
        "needs_attention": [b for b in boards if b["needs_attention"]],
        "failure_threshold": FAILURE_THRESHOLD,
    }


# 10. Feed cleanup/TTL endpoint
@router.post("/feed/cleanup", summary="Archive old feed results")
async def cleanup_feed(
    days: int = 30,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Remove untouched feed results older than N days (default 30).

    Anything the user decided about - interested, tailored, applied - is kept: a
    decision is not disk to reclaim. The background worker runs the same sweep
    daily, so this endpoint is the "do it now" version rather than the only way it
    ever happens; sharing one implementation is what keeps the two from drifting
    into different definitions of "old".
    """
    from app.job_discovery.retention import (
        DEFAULT_RETENTION_DAYS,
        MIN_RETENTION_DAYS,
        sweep_feed,
    )

    deleted = await sweep_feed(db, user_id, days or DEFAULT_RETENTION_DAYS)
    return {
        "deleted": deleted,
        "cutoff_days": max(MIN_RETENTION_DAYS, days),
        "minimum_days": MIN_RETENTION_DAYS,
    }
