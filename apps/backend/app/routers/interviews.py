"""Interview scheduling endpoints (P3 §F, Requirement 11).

Nested under an application (parent-ownership -> 404), user-scoped, feature-flag
gated, idempotency-key aware. Reschedule re-arms lead-time notifications; overlap
is a soft warning (never blocks). ICS export is timezone-correct + escaped.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response

from app.auth import get_effective_user_id
from app.config import settings
from app.schemas.scheduling import InterviewCreate, InterviewResponse, InterviewUpdate
from app.scheduling.service import SchedulingError, get_scheduling_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Interviews"])
ics_router = APIRouter(prefix="/interviews", tags=["Interviews"])

_STATUS = {"not_found": 404, "invalid": 422, "conflict": 409, "limit": 429}


def _require_enabled() -> None:
    if not settings.interviews_enabled:
        raise HTTPException(status_code=404, detail="interviews_disabled")


def _raise(exc: SchedulingError):
    raise HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.message)


@router.get("/{application_id}/interviews", response_model=list[InterviewResponse])
async def list_interviews(
    application_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> list[InterviewResponse]:
    try:
        rows = await get_scheduling_service().list_interviews(user_id, application_id)
    except SchedulingError as exc:
        _raise(exc)
    return [InterviewResponse(**r) for r in rows]


@router.post("/{application_id}/interviews", response_model=InterviewResponse)
async def create_interview(
    application_id: str,
    request: InterviewCreate,
    user_id: str = Depends(get_effective_user_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: None = Depends(_require_enabled),
) -> InterviewResponse:
    try:
        created = await get_scheduling_service().create_interview(
            user_id, application_id, starts_at=request.starts_at, tz=request.tz,
            duration_min=request.duration_min, kind=request.kind, location=request.location,
            notes=request.notes, lead_times=request.lead_times, idempotency_key=idempotency_key,
        )
    except SchedulingError as exc:
        _raise(exc)
    return InterviewResponse(**created)


@router.patch("/{application_id}/interviews/{interview_id}", response_model=InterviewResponse)
async def update_interview(
    application_id: str,
    interview_id: str,
    request: InterviewUpdate,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> InterviewResponse:
    try:
        updated = await get_scheduling_service().update_interview(
            user_id, interview_id, starts_at=request.starts_at, tz=request.tz,
            duration_min=request.duration_min, kind=request.kind, location=request.location,
            notes=request.notes, lead_times=request.lead_times,
        )
    except SchedulingError as exc:
        _raise(exc)
    return InterviewResponse(**updated)


@router.delete("/{application_id}/interviews/{interview_id}")
async def cancel_interview(
    application_id: str,
    interview_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> dict[str, bool]:
    try:
        await get_scheduling_service().cancel_interview(user_id, interview_id)
    except SchedulingError as exc:
        _raise(exc)
    return {"cancelled": True}


@ics_router.get("/{interview_id}.ics")
async def interview_ics(
    interview_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> Response:
    """Download a timezone-correct, escaped VEVENT for the interview (R11.3)."""
    try:
        ics = await get_scheduling_service().get_interview_ics(user_id, interview_id)
    except SchedulingError as exc:
        _raise(exc)
    return Response(
        content=ics,
        media_type="text/calendar",
        headers={"Content-Disposition": f'attachment; filename="interview-{interview_id}.ics"'},
    )
