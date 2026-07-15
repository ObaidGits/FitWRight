"""Professional Profile endpoints (docs/architecture/PROFILE_SYSTEM_PLAN.md).

The profile is the user's canonical career document; resumes are generated
snapshots produced from it. All routes are user-scoped
(``get_effective_user_id``) and gated by the ``PROFILE_ENABLED`` flag (off → 404
so the surface can be dark-launched / killed without a redeploy — ADR-14).

Endpoint map:
- ``GET   /profile``                      the canonical document + completeness + CAS version
- ``PATCH /profile``                      apply an edited document (version CAS → 409 on stale)
- ``GET   /profile/completeness``         weighted score + prioritized suggestions
- ``POST  /profile/generate-resume``      project the profile into a resume (preview or persist)
- ``GET   /profile/versions``             snapshot metadata (keyset cursor)
- ``GET   /profile/versions/{vid}``       one snapshot + decompressed document
- ``POST  /profile/versions/{vid}/restore``  non-destructive restore (version CAS)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth import get_effective_user_id
from app.config import settings
from app.profile.schemas import (
    AiMemoryUpdateRequest,
    AiSuggestRequest,
    AiSuggestResponse,
    ApplyMergeRequest,
    ApplyMergeResponse,
    GenerateResumeRequest,
    GenerateResumeResponse,
    ImportPreviewResponse,
    ProfileAnalyticsResponse,
    ProfileCompletenessResponse,
    ProfileData,
    ProfileResponse,
    ProfileSearchResponse,
    ProfileSearchResult,
    ProfileUpdateRequest,
    ProfileVersionDataResponse,
    ProfileVersionListResponse,
    ProfileVersionMeta,
    PublicationStateResponse,
    PublicProfileResponse,
    PublishRequest,
    SkillSuggestion,
    SkillSuggestResponse,
    SyncApplyRequest,
    SyncPreviewResponse,
)
from app.profile.service import ProfileServiceError, profile_service
from app.profile.versions import (
    get_profile_version_data,
    list_profile_version_metadata,
)
from app.versions.service import VersionServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["Profile"])

_STATUS = {"not_found": 404, "conflict": 409, "invalid": 422}


def _require_enabled() -> None:
    """404 the whole surface when the feature flag is off (kill-switch)."""
    if not settings.profile_enabled:
        raise HTTPException(status_code=404, detail="profile_disabled")


def _response(row: dict) -> ProfileResponse:
    """Build the read DTO from a stored profile row."""
    return ProfileResponse(
        data=ProfileData.model_validate(row.get("data") or {}),
        completeness=int(row.get("completeness") or 0),
        version=int(row.get("version") or 1),
        updated_at=row.get("updated_at"),
    )


@router.get("", response_model=ProfileResponse)
async def get_profile(
    user_id: str = Depends(get_effective_user_id),
) -> ProfileResponse:
    """Return the canonical profile, deriving it from the master resume if absent."""
    _require_enabled()
    row = await profile_service.get_or_create(user_id)
    return _response(row)


@router.patch("", response_model=ProfileResponse)
async def update_profile(
    request: ProfileUpdateRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ProfileResponse:
    """Apply an edited profile document with optimistic-concurrency CAS.

    ``base_version`` is the version the client last read; on a stale value the
    write is rejected with 409 and the *current* server state so the client can
    reconcile without a lost update.
    """
    _require_enabled()
    status, row = await profile_service.update(
        user_id, data=request.data, base_version=request.base_version
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Profile not found")
    if status == "conflict":
        current = _response(row) if row else None
        raise HTTPException(
            status_code=409,
            detail={
                "code": "version_conflict",
                "message": "Profile was modified by another write.",
                "your_base_version": request.base_version,
                "current_version": current.version if current else None,
                "current": current.model_dump(mode="json") if current else None,
            },
        )
    assert row is not None
    return _response(row)


@router.get("/completeness", response_model=ProfileCompletenessResponse)
async def get_completeness(
    user_id: str = Depends(get_effective_user_id),
) -> ProfileCompletenessResponse:
    """Return the weighted completion score + prioritized suggestions."""
    _require_enabled()
    result = await profile_service.completeness(user_id)
    return ProfileCompletenessResponse(**result)


@router.post("/generate-resume", response_model=GenerateResumeResponse)
async def generate_resume(
    request: GenerateResumeRequest,
    user_id: str = Depends(get_effective_user_id),
) -> GenerateResumeResponse:
    """Project the profile into resume data; optionally persist a new resume."""
    _require_enabled()
    result = await profile_service.generate_resume(
        user_id,
        title=request.title,
        persist=request.persist,
        as_master=request.as_master,
        include_photo=request.include_photo,
        photo=request.photo,
        template=request.template,
        sections=request.sections,
        template_settings=request.template_settings,
    )
    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_PROFILE_GEN
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_PROFILE_GEN, 1)
    except Exception:
        pass  # metrics never break user operations
    return GenerateResumeResponse(**result)


@router.get("/versions", response_model=ProfileVersionListResponse)
async def list_versions(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_effective_user_id),
) -> ProfileVersionListResponse:
    """Metadata-only, keyset-paginated list of profile snapshots (newest first)."""
    _require_enabled()
    row = await profile_service.get_or_create(user_id)
    page = await list_profile_version_metadata(
        user_id, row["id"], limit=limit, cursor=cursor
    )
    return ProfileVersionListResponse(
        items=[ProfileVersionMeta(**item) for item in page["items"]],
        next_cursor=page["next_cursor"],
    )


@router.get("/versions/{version_id}", response_model=ProfileVersionDataResponse)
async def get_version(
    version_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ProfileVersionDataResponse:
    """Return a single snapshot's metadata + decompressed document."""
    _require_enabled()
    try:
        data = await get_profile_version_data(user_id, version_id)
    except VersionServiceError as exc:
        raise HTTPException(
            status_code=_STATUS.get(exc.code, 422), detail=exc.message
        )
    return ProfileVersionDataResponse(
        **{k: v for k, v in data.items() if k != "data"},
        data=ProfileData.model_validate(data["data"]),
    )


@router.post("/versions/{version_id}/restore", response_model=ProfileResponse)
async def restore_version(
    version_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ProfileResponse:
    """Non-destructively restore a snapshot as the current profile (version CAS).

    The restored document is applied as a fresh ``manual`` write against the
    current version, so restoring is itself captured as a new snapshot and never
    clobbers a concurrent edit (a stale write loses the CAS and 409s).
    """
    _require_enabled()
    try:
        snapshot = await get_profile_version_data(user_id, version_id)
    except VersionServiceError as exc:
        raise HTTPException(
            status_code=_STATUS.get(exc.code, 422), detail=exc.message
        )

    current = await profile_service.get_or_create(user_id)
    if snapshot.get("profile_id") != current["id"]:
        raise HTTPException(status_code=404, detail="Version not found")

    restored = ProfileData.model_validate(snapshot["data"])
    status, row = await profile_service.update(
        user_id,
        data=restored,
        base_version=int(current.get("version") or 1),
        source="manual",
        label="Restored version",
    )
    if status == "conflict":
        raise HTTPException(
            status_code=409, detail="Profile changed during restore; retry."
        )
    if status == "not_found" or row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _response(row)


# ---------------------------------------------------------------------------
# Import / Merge (P3)
# ---------------------------------------------------------------------------


class ImportPreviewRequest(BaseModel):
    """Preview importing a source into the profile."""

    source: str = "resume"
    payload: dict = Field(default_factory=dict)


def _svc_error(exc: ProfileServiceError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.message)


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def import_preview(
    request: ImportPreviewRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ImportPreviewResponse:
    """Derive a candidate from a source and return the reviewable merge plan."""
    _require_enabled()
    try:
        result = await profile_service.preview_import(
            user_id, request.source, request.payload
        )
    except ProfileServiceError as exc:
        raise _svc_error(exc)
    return ImportPreviewResponse(**result)


@router.post("/import/apply", response_model=ApplyMergeResponse)
async def import_apply(
    request: ApplyMergeRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ApplyMergeResponse:
    """Apply a reviewed merge plan (version CAS)."""
    _require_enabled()
    status, row, applied, skipped = await profile_service.apply_import(
        user_id,
        incoming=request.incoming,
        resolutions=request.resolutions,
        base_version=request.base_version,
        source=request.source,
    )
    if status == "conflict":
        raise HTTPException(status_code=409, detail="Profile changed; reload and retry.")
    if status == "not_found" or row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return ApplyMergeResponse(
        data=ProfileData.model_validate(row.get("data") or {}),
        completeness=int(row.get("completeness") or 0),
        version=int(row.get("version") or 1),
        applied=applied,
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Synchronization (P4)
# ---------------------------------------------------------------------------


@router.get("/sync/{resume_id}", response_model=SyncPreviewResponse)
async def sync_preview(
    resume_id: str,
    include_photo: bool = Query(default=False),
    user_id: str = Depends(get_effective_user_id),
) -> SyncPreviewResponse:
    """Diff a resume against a fresh projection of the profile (read-only)."""
    _require_enabled()
    result = await profile_service.preview_sync(
        user_id, resume_id, include_photo=include_photo
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return SyncPreviewResponse(**result)


@router.post("/sync/{resume_id}")
async def sync_apply(
    resume_id: str,
    request: SyncApplyRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Apply the profile projection to a *draft* resume (submitted → 409 locked)."""
    _require_enabled()
    status, updated = await profile_service.apply_sync(
        user_id,
        resume_id,
        base_version=request.base_version,
        include_photo=request.include_photo,
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Resume not found")
    if status == "immutable":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "resume_locked",
                "message": "This resume was submitted and is immutable. Generate a new resume instead.",
            },
        )
    if status == "conflict":
        raise HTTPException(
            status_code=409,
            detail={"code": "version_conflict", "message": "Resume changed; reload and retry."},
        )
    return {"resume_id": resume_id, "resume": updated}


# ---------------------------------------------------------------------------
# AI layer (P5)
# ---------------------------------------------------------------------------


@router.put("/ai-memory", response_model=ProfileResponse)
async def update_ai_memory(
    request: AiMemoryUpdateRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ProfileResponse:
    """Update the AI-memory namespace (kept separate from resume content)."""
    _require_enabled()
    status, row = await profile_service.update_ai_memory(
        user_id, request.aiMemory, request.base_version
    )
    if status == "conflict":
        raise HTTPException(status_code=409, detail="Profile changed; reload and retry.")
    if status == "not_found" or row is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return _response(row)


@router.post("/ai/suggest", response_model=AiSuggestResponse)
async def ai_suggest(
    request: AiSuggestRequest,
    user_id: str = Depends(get_effective_user_id),
) -> AiSuggestResponse:
    """Return an AI suggestion for a field (reviewed by the user; never invents)."""
    _require_enabled()
    try:
        result = await profile_service.suggest(
            user_id, request.kind, experience_uid=request.experience_uid
        )
    except ProfileServiceError as exc:
        raise _svc_error(exc)
    return AiSuggestResponse(**result)


@router.get("/skills/suggest", response_model=SkillSuggestResponse)
async def skills_suggest(
    q: str = Query(default="", max_length=64),
    user_id: str = Depends(get_effective_user_id),
) -> SkillSuggestResponse:
    """Canonical-skill autocomplete for the editor (pure, deterministic)."""
    _require_enabled()
    items = profile_service.skill_suggestions(q)
    return SkillSuggestResponse(suggestions=[SkillSuggestion(**i) for i in items])


@router.get("/search", response_model=ProfileSearchResponse)
async def search_profile(
    q: str = Query(default="", max_length=128),
    limit: int = Query(default=20, ge=1, le=50),
    user_id: str = Depends(get_effective_user_id),
) -> ProfileSearchResponse:
    """Ranked, highlighted search across the user's own profile document."""
    _require_enabled()
    result = await profile_service.search(user_id, q, limit=limit)
    return ProfileSearchResponse(
        query=result["query"],
        results=[ProfileSearchResult(**r) for r in result["results"]],
    )


@router.get("/analytics", response_model=ProfileAnalyticsResponse)
async def profile_analytics(
    user_id: str = Depends(get_effective_user_id),
) -> ProfileAnalyticsResponse:
    """Per-user usage analytics (non-PII counters + completeness gauge)."""
    _require_enabled()
    return ProfileAnalyticsResponse(**await profile_service.analytics(user_id))


# ---------------------------------------------------------------------------
# Public projection platform (P6)
# ---------------------------------------------------------------------------


@router.get("/public", response_model=PublicProfileResponse)
async def public_projection(
    user_id: str = Depends(get_effective_user_id),
) -> PublicProfileResponse:
    """The safe, public-facing projection (no private/contact-sensitive fields)."""
    _require_enabled()
    return PublicProfileResponse(**await profile_service.public_profile(user_id))


@router.get("/portfolio")
async def portfolio_projection(
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """A portfolio-oriented projection (projects-first)."""
    _require_enabled()
    result = await profile_service.portfolio(user_id)
    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_PORTFOLIO
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_PORTFOLIO, 1)
    except Exception:
        pass  # metrics never break user operations
    return result


@router.get("/export/json-resume")
async def export_json_resume(
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Export the profile in the JSON Resume schema (round-trips with import)."""
    _require_enabled()
    return await profile_service.export_json_resume(user_id)


@router.get("/publication", response_model=PublicationStateResponse)
async def publication_state(
    user_id: str = Depends(get_effective_user_id),
) -> PublicationStateResponse:
    """Return the owner's current publish state (slug + visibility)."""
    _require_enabled()
    return PublicationStateResponse(**await profile_service.publication_state(user_id))


@router.post("/publish", response_model=PublicationStateResponse)
async def publish_profile(
    request: PublishRequest,
    user_id: str = Depends(get_effective_user_id),
) -> PublicationStateResponse:
    """Publish the profile at a unique slug with the chosen visibility."""
    _require_enabled()
    try:
        row = await profile_service.publish(
            user_id, visibility=request.visibility, slug=request.slug, theme=request.theme
        )
    except ProfileServiceError as exc:
        raise _svc_error(exc)
    return PublicationStateResponse(
        public_slug=row.get("public_slug"),
        visibility=row.get("visibility"),
        public_theme=row.get("public_theme") or "minimal",
    )


@router.post("/unpublish", response_model=PublicationStateResponse)
async def unpublish_profile(
    user_id: str = Depends(get_effective_user_id),
) -> PublicationStateResponse:
    """Make the profile private again (slug reserved for stable re-publish)."""
    _require_enabled()
    try:
        row = await profile_service.unpublish(user_id)
    except ProfileServiceError as exc:
        raise _svc_error(exc)
    return PublicationStateResponse(
        public_slug=row.get("public_slug"),
        visibility=row.get("visibility"),
        public_theme=row.get("public_theme") or "minimal",
    )
