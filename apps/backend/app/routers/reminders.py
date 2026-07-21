"""Follow-up reminder endpoints (P3 §E, Requirement 10).

Nested under an application (parent-ownership -> 404), user-scoped, feature-flag
gated, idempotency-key aware on create. The claim-based scheduler fires due
reminders -> notifications; recurring reminders materialize their next occurrence.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException

from app.auth import get_effective_user_id
from app.config import settings
from app.schemas.scheduling import (
    ReminderCreate,
    ReminderResponse,
    ReminderSnooze,
    ReminderUpdate,
)
from app.scheduling.service import SchedulingError, get_scheduling_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Reminders"])

_STATUS = {"not_found": 404, "invalid": 422, "conflict": 409, "limit": 429}


def _require_enabled() -> None:
    if not settings.reminders_enabled:
        raise HTTPException(status_code=404, detail="reminders_disabled")


def _raise(exc: SchedulingError):
    raise HTTPException(status_code=_STATUS.get(exc.code, 422), detail=exc.message)


@router.get("/{application_id}/reminders", response_model=list[ReminderResponse])
async def list_reminders(
    application_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> list[ReminderResponse]:
    try:
        rows = await get_scheduling_service().list_reminders(user_id, application_id)
    except SchedulingError as exc:
        _raise(exc)
    return [ReminderResponse(**r) for r in rows]


@router.post("/{application_id}/reminders", response_model=ReminderResponse)
async def create_reminder(
    application_id: str,
    request: ReminderCreate,
    user_id: str = Depends(get_effective_user_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    _: None = Depends(_require_enabled),
) -> ReminderResponse:
    svc = get_scheduling_service()
    try:
        due_at = request.due_at or (svc.preset_due(request.preset) if request.preset else None)
        if not due_at:
            raise HTTPException(status_code=422, detail="Provide due_at or a preset.")
        created = await svc.create_reminder(
            user_id, application_id, due_at=due_at, tz=request.tz, note=request.note,
            recurrence=request.recurrence, idempotency_key=idempotency_key,
        )
    except SchedulingError as exc:
        _raise(exc)
    return ReminderResponse(**created)


@router.patch("/{application_id}/reminders/{reminder_id}", response_model=ReminderResponse)
async def update_reminder(
    application_id: str,
    reminder_id: str,
    request: ReminderUpdate,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ReminderResponse:
    try:
        updated = await get_scheduling_service().update_reminder(
            user_id, reminder_id, due_at=request.due_at, tz=request.tz,
            note=request.note, recurrence=request.recurrence,
        )
    except SchedulingError as exc:
        _raise(exc)
    return ReminderResponse(**updated)


@router.post("/{application_id}/reminders/{reminder_id}/snooze", response_model=ReminderResponse)
async def snooze_reminder(
    application_id: str,
    reminder_id: str,
    request: ReminderSnooze,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ReminderResponse:
    try:
        updated = await get_scheduling_service().snooze_reminder(
            user_id, reminder_id, until=request.until, preset=request.preset
        )
    except SchedulingError as exc:
        _raise(exc)
    return ReminderResponse(**updated)


@router.delete("/{application_id}/reminders/{reminder_id}")
async def cancel_reminder(
    application_id: str,
    reminder_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> dict[str, bool]:
    try:
        await get_scheduling_service().cancel_reminder(user_id, reminder_id)
    except SchedulingError as exc:
        _raise(exc)
    return {"cancelled": True}
