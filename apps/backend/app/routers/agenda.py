"""Agenda endpoint (P3 §G, Requirement 12).

An aggregated, time-ordered view of a user's upcoming reminders + interviews
across all applications, keyset-paginated. Quick actions (snooze/reschedule/
cancel/mark-done) reuse the reminder/interview endpoints; deep links carry the
node ref. User-scoped + feature-flag gated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_effective_user_id
from app.config import settings
from app.schemas.scheduling import AgendaItem, AgendaResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agenda", tags=["Agenda"])


def _require_enabled() -> None:
    if not settings.agenda_enabled:
        raise HTTPException(status_code=404, detail="agenda_disabled")


@router.get("", response_model=AgendaResponse)
async def get_agenda(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> AgendaResponse:
    """Merged, time-ordered upcoming reminders + interviews (R12.1)."""
    from app.scheduling.service import get_scheduling_service

    svc = get_scheduling_service()
    # Over-fetch each source then merge + keyset-slice (both are indexed by time).
    fetch = limit + 1
    reminders = await svc.upcoming_reminders(user_id, fetch + (int(_cursor_n(cursor))))
    interviews = await svc.upcoming_interviews(user_id, fetch + (int(_cursor_n(cursor))))

    merged: list[AgendaItem] = []
    for r in reminders:
        merged.append(
            AgendaItem(
                kind="reminder", id=r["id"], application_id=r["application_id"],
                when=r["due_at"], tz=r["tz"], title=(r.get("note") or "Follow-up"),
                status=r["status"],
            )
        )
    for iv in interviews:
        merged.append(
            AgendaItem(
                kind="interview", id=iv["id"], application_id=iv["application_id"],
                when=iv["starts_at"], tz=iv["tz"], title=f"{iv['kind'].title()} interview",
                status=iv["status"],
            )
        )
    # Time-ordered; stable tiebreak on (when, kind, id) for cursor stability.
    merged.sort(key=lambda x: (x.when, x.kind, x.id))

    start = int(_cursor_n(cursor))
    page = merged[start : start + limit]
    has_more = len(merged) > start + limit
    next_cursor = str(start + limit) if has_more else None
    return AgendaResponse(items=page, next_cursor=next_cursor)


def _cursor_n(cursor: str | None) -> int:
    """Decode the simple offset cursor (defensive: bad cursor → 0)."""
    if not cursor:
        return 0
    try:
        return max(0, int(cursor))
    except ValueError:
        return 0
