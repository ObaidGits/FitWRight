"""Reminder tools - thin wrappers over the scheduling service.

Same pattern as ``resumes.py`` / ``applications.py``: user id comes from the
bearer token only, and the tool calls the exact service functions the REST
handlers in ``app/routers/reminders.py`` call (including their feature-flag
gate), so ownership (parent application must belong to the caller) and
validation stay identical.
"""

from __future__ import annotations

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from pydantic import ValidationError

from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id

mcp = get_mcp_instance()


async def _reminders_service():
    """The scheduling service, gated exactly like the REST reminders routes.

    ``settings.reminders_enabled`` is checked per call (the router does the
    same via a dependency), so a disabled feature surfaces as a clean tool
    error instead of a silent mismatch with the REST API.
    """
    from app.config import settings
    from app.scheduling.service import get_scheduling_service

    if not settings.reminders_enabled:
        raise ValueError(
            "reminders_disabled: The reminders feature is turned off for this "
            "account."
        )
    return get_scheduling_service()


def _reminder_error(application_id: str, exc: Exception) -> ValueError:
    """A SchedulingError as an actionable tool ValueError (REST maps the same
    codes to 404/422/429)."""
    code = getattr(exc, "code", "invalid")
    if code == "not_found":
        return ValueError(
            f"application_not_found: {application_id}. Call list_applications "
            "to get valid application ids."
        )
    if code == "limit":
        return ValueError(f"reminder_limit_reached: {exc}")
    return ValueError(f"invalid_reminder: {exc}")


@mcp.tool
async def list_reminders(
    application_id: str, token: AccessToken = CurrentAccessToken()
) -> dict:
    """List the follow-up reminders attached to one job application, in due
    order. Each reminder carries due_at, note, status, and recurrence.

    application_id must come from list_applications or get_apply_queue.
    """
    user_id = current_user_id(token)
    svc = await _reminders_service()

    try:
        rows = await svc.list_reminders(user_id, application_id)
    except Exception as exc:
        if getattr(exc, "code", None):  # SchedulingError
            raise _reminder_error(application_id, exc) from None
        raise

    return {"reminders": rows, "total": len(rows)}


@mcp.tool
async def create_reminder(
    application_id: str,
    remind_at: str,
    note: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Schedule a follow-up reminder on one job application.

    remind_at is an ISO-8601 datetime string (e.g. "2026-09-08T09:00:00+00:00");
    a timestamp without a timezone is treated as UTC. note is optional
    (max 1000 characters). application_id must come from list_applications or
    get_apply_queue.
    """
    user_id = current_user_id(token)
    from app.schemas.scheduling import ReminderCreate

    if not remind_at or not remind_at.strip():
        raise ValueError(
            "remind_at_required: provide an ISO-8601 datetime, e.g. "
            "2026-09-08T09:00:00+00:00."
        )

    # Same body validation as the REST create (note length bound, types).
    try:
        ReminderCreate(due_at=remind_at, note=note)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        raise ValueError(f"invalid_argument: {field} - {first.get('msg')}") from None

    svc = await _reminders_service()

    try:
        created = await svc.create_reminder(
            user_id, application_id, due_at=remind_at, note=note
        )
    except Exception as exc:
        if getattr(exc, "code", None):  # SchedulingError
            raise _reminder_error(application_id, exc) from None
        raise

    return created
