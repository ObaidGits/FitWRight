"""Notification Center endpoints (P3 §B, Requirements 4-6).

User-scoped, auth-guarded, cursor-paginated, and gated by the ``NOTIFICATIONS``
feature flag (off -> 404). The unread badge reads the denormalized O(1) counter
(never a COUNT scan); the transport (polling vs SSE) is advertised to the client
so the same endpoints back both delivery modes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_effective_user_id
from app.config import settings
from app.notifications.repo import get_notification_repo
from app.notifications.service import CATEGORIES
from app.schemas.notifications import (
    ActionResponse,
    DismissGroupRequest,
    NotificationListResponse,
    NotificationPrefsResponse,
    NotificationResponse,
    UnreadCountResponse,
    UpdatePrefsRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _require_enabled() -> None:
    if not settings.notifications_enabled:
        raise HTTPException(status_code=404, detail="notifications_disabled")


def _to_response(row: dict) -> NotificationResponse:
    return NotificationResponse(
        id=row["id"],
        type=row["type"],
        category=row["category"],
        priority=row["priority"],
        title=row["title"],
        body=row.get("body"),
        node_type=row.get("node_type"),
        node_id=row.get("node_id"),
        group_key=row.get("group_key"),
        read=row["read"],
        created_at=row["created_at"],
    )


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    unread: bool = Query(default=False),
    category: str | None = Query(default=None),
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> NotificationListResponse:
    """List non-dismissed notifications (newest first), keyset-paginated (R4.3)."""
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown category")
    repo = get_notification_repo()
    rows = await repo.list(
        user_id, limit=limit + 1, cursor=cursor, unread_only=unread, category=category
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = f"{last['created_at']}|{last['id']}"
    return NotificationListResponse(
        items=[_to_response(r) for r in page], next_cursor=next_cursor
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> UnreadCountResponse:
    """Return the O(1) unread badge + the client poll/transport hints (R4.2/6.3)."""
    count = await get_notification_repo().unread_count(user_id)
    return UnreadCountResponse(
        unread=count,
        transport="sse" if settings.sse_notifications else "polling",
        poll_interval_seconds=settings.notification_poll_interval_seconds,
    )


@router.post("/{notification_id}/read", response_model=ActionResponse)
async def mark_read(
    notification_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ActionResponse:
    """Mark one notification read (404 if absent/foreign)."""
    ok = await get_notification_repo().mark_read(user_id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return ActionResponse(affected=1)


@router.post("/read-all", response_model=ActionResponse)
async def mark_all_read(
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ActionResponse:
    """Mark all unread notifications read; unread counter -> 0."""
    affected = await get_notification_repo().mark_all_read(user_id)
    return ActionResponse(affected=affected)


@router.delete("/{notification_id}", response_model=ActionResponse)
async def dismiss(
    notification_id: str,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ActionResponse:
    """Dismiss one notification (404 if absent/foreign)."""
    ok = await get_notification_repo().dismiss(user_id, notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return ActionResponse(affected=1)


@router.post("/dismiss-group", response_model=ActionResponse)
async def dismiss_group(
    request: DismissGroupRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> ActionResponse:
    """Dismiss every notification under a ``group_key`` (R4.3)."""
    affected = await get_notification_repo().dismiss_group(user_id, request.group_key)
    return ActionResponse(affected=affected)


@router.get("/prefs", response_model=NotificationPrefsResponse)
async def get_prefs(
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> NotificationPrefsResponse:
    """Return per-category delivery prefs + the digest setting (defaults applied)."""
    prefs = await get_notification_repo().get_prefs(user_id)
    return NotificationPrefsResponse(**prefs)


@router.put("/prefs", response_model=NotificationPrefsResponse)
async def update_prefs(
    request: UpdatePrefsRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> NotificationPrefsResponse:
    """Update one or more category prefs and/or the digest setting (R6.1)."""
    repo = get_notification_repo()
    if request.categories:
        for pref in request.categories:
            await repo.set_pref(user_id, pref.category, in_app=pref.in_app, email=pref.email)
    if request.digest is not None:
        await repo.set_digest(user_id, request.digest)
    prefs = await repo.get_prefs(user_id)
    return NotificationPrefsResponse(**prefs)
