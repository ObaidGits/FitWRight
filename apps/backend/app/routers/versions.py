"""Resume version-history endpoints (P3 §A, Requirements 1-3).

All routes are user-scoped (``get_effective_user_id``), parent-ownership checked
(a foreign/absent resume -> 404, no existence disclosure), and gated by the
``VERSION_HISTORY`` feature flag (off -> 404 ``version_history_disabled`` so the
feature can be dark-launched / killed without a redeploy). Snapshot payloads are
metadata-only on the list; the (decompressed) data is fetched on demand.

Endpoint map:
- ``GET    /resumes/{id}/versions``               metadata list (keyset cursor)
- ``POST   /resumes/{id}/versions``               capture a manual snapshot now
- ``GET    /resumes/{id}/versions/compare?a=&b=``  field-level diff of two snaps
- ``GET    /resumes/{id}/versions/{vid}``          one snapshot + decompressed data
- ``POST   /resumes/{id}/versions/{vid}/restore``  non-destructive restore (CAS)
- ``POST   /resumes/{id}/undo-last-ai``            restore the pre-last-AI state
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_effective_user_id
from app.config import settings
from app.database import db
from app.schemas import (
    CreateManualSnapshotRequest,
    RestoreResponse,
    RestoreVersionRequest,
    VersionCompareResponse,
    VersionDataResponse,
    VersionListResponse,
)
from app.versions import service as version_service
from app.versions.service import VersionServiceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/resumes", tags=["Version History"])

# Map service error codes -> HTTP status.
_STATUS = {"not_found": 404, "conflict": 409, "invalid": 422}


def _require_enabled() -> None:
    """404 the whole surface when the feature flag is off (kill-switch)."""
    if not settings.version_history_enabled:
        raise HTTPException(status_code=404, detail="version_history_disabled")


async def _require_owned_resume(user_id: str, resume_id: str) -> dict:
    """Load the resume or 404 (parent-ownership check - R17.1)."""
    resume = await db.get_resume(user_id, resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


def _raise(exc: VersionServiceError) -> None:
    raise HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.message)


@router.get("/{resume_id}/versions", response_model=VersionListResponse)
async def list_versions(
    resume_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> VersionListResponse:
    """List snapshot metadata (newest first), keyset-paginated (R3.1)."""
    await _require_owned_resume(user_id, resume_id)
    page = await version_service.list_version_metadata(
        user_id, resume_id, limit=limit, cursor=cursor
    )
    return VersionListResponse(**page)


@router.post("/{resume_id}/versions", response_model=VersionDataResponse | None)
async def create_manual_snapshot(
    resume_id: str,
    request: CreateManualSnapshotRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> VersionDataResponse | None:
    """Capture the current resume state as a labeled ``manual`` snapshot.

    Returns the created snapshot (with data) or 204-like ``null`` when the write
    was deduped (identical content) or debounced.
    """
    resume = await _require_owned_resume(user_id, resume_id)
    processed = resume.get("processed_data")
    if not processed:
        raise HTTPException(status_code=422, detail="Resume has no processed data to snapshot.")
    try:
        created = await version_service.capture_snapshot(
            user_id, resume_id, processed, "manual", label=request.label
        )
    except VersionServiceError as exc:
        _raise(exc)
    if created is None:
        return None
    return VersionDataResponse(**created, processed_data=processed)


@router.get("/{resume_id}/versions/compare", response_model=VersionCompareResponse)
async def compare_versions(
    resume_id: str,
    a: str = Query(...),
    b: str = Query(...),
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> VersionCompareResponse:
    """Field-level diff between two owned snapshots of this resume (R3.2)."""
    await _require_owned_resume(user_id, resume_id)
    try:
        result = await version_service.compare_versions(user_id, resume_id, a, b)
    except VersionServiceError as exc:
        _raise(exc)
    return VersionCompareResponse(**result)


@router.get("/{resume_id}/versions/{version_id}", response_model=VersionDataResponse)
async def get_version(
    resume_id: str,
    version_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> VersionDataResponse:
    """Fetch one snapshot's metadata + decompressed data on demand (R3.1)."""
    await _require_owned_resume(user_id, resume_id)
    try:
        data = await version_service.get_version_data(user_id, version_id)
    except VersionServiceError as exc:
        _raise(exc)
    if data.get("resume_id") != resume_id:
        raise HTTPException(status_code=404, detail="Version not found.")
    return VersionDataResponse(**data)


@router.post("/{resume_id}/versions/{version_id}/restore", response_model=RestoreResponse)
async def restore_version(
    resume_id: str,
    version_id: str,
    request: RestoreVersionRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> RestoreResponse:
    """Non-destructively restore a snapshot (snapshot-current-first + CAS - R2.1/2.3)."""
    await _require_owned_resume(user_id, resume_id)
    try:
        updated = await version_service.restore_version(
            user_id, resume_id, version_id,
            expected_updated_at=request.expected_updated_at,
        )
    except VersionServiceError as exc:
        _raise(exc)
    return RestoreResponse(
        resume_id=updated["resume_id"],
        updated_at=updated["updated_at"],
        processed_data=updated.get("processed_data"),
    )


@router.post("/{resume_id}/undo-last-ai", response_model=RestoreResponse)
async def undo_last_ai(
    resume_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> RestoreResponse:
    """Restore the snapshot immediately preceding the last AI change (R2.2)."""
    await _require_owned_resume(user_id, resume_id)
    try:
        updated = await version_service.undo_last_ai(user_id, resume_id)
    except VersionServiceError as exc:
        _raise(exc)
    return RestoreResponse(
        resume_id=updated["resume_id"],
        updated_at=updated["updated_at"],
        processed_data=updated.get("processed_data"),
    )
