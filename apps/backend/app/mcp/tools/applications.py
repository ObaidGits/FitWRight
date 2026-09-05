"""Application-tracker read tools - thin wrappers over service calls.

Covers the job-application board (list/detail), the apply queue, and the
duplicate-application check. Same registration pattern as ``resumes.py``.
"""

from __future__ import annotations

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken

from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id

mcp = get_mcp_instance()


@mcp.tool
async def list_applications(token: AccessToken = CurrentAccessToken()) -> dict:
    """List all the user's job applications, grouped into the seven status
    columns: saved, applied, no_response, response, interview, accepted,
    rejected.

    Every column is ALWAYS present (an empty list when that status has no
    cards), so the shape is stable regardless of the data. Each card carries
    company, role, status, and position. Use get_application with an
    application_id for a single application's detail.
    """
    user_id = current_user_id(token)
    from app.database import db
    from app.schemas import APPLICATION_STATUS_ORDER

    apps = await db.list_applications(user_id)
    # Same grouping as the REST board (app/routers/applications.py
    # _group_by_status): all seven columns always present; a row with an
    # unknown status cannot be placed in a column and is skipped.
    columns: dict[str, list] = {status: [] for status in APPLICATION_STATUS_ORDER}
    for app in apps:
        status = app.get("status")
        if status in columns:
            columns[status].append(app)
    return {"columns": columns, "total": len(apps)}


@mcp.tool
async def get_application(
    application_id: str, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Get one application's detail by id, including the embedded job
    description text and the resume deliverables (cover letter, outreach
    message, interview prep) that were generated for it.

    application_id must come from list_applications or get_apply_queue.
    """
    user_id = current_user_id(token)
    from app.database import db

    detail = await db.get_application_detail(user_id, application_id)
    if detail is None:
        raise ValueError(
            f"application_not_found: {application_id}. Call list_applications "
            "to get valid application ids."
        )
    return detail


@mcp.tool
async def get_apply_queue(token: AccessToken = CurrentAccessToken()) -> dict:
    """The user's apply queue: saved-but-not-yet-applied jobs in the order to
    work through them (queue position order)."""
    user_id = current_user_id(token)
    from app.applications import submissions

    queue = await submissions.list_queue(user_id)
    return {"queue": queue, "total": len(queue)}


@mcp.tool
async def check_duplicate(
    company: str, role: str, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Check whether the user already has a live application to this exact
    company AND role (advisory, before queueing a new one).

    Matching is case/whitespace-insensitive on both fields; a recent
    application to the same company for a different role is NOT a duplicate.
    Returns is_duplicate true with the existing application, or
    {"is_duplicate": false, "application": null}.
    """
    user_id = current_user_id(token)
    from app.applications import submissions

    duplicate = await submissions.find_duplicate(user_id, company=company, role=role)
    return {"is_duplicate": duplicate is not None, "application": duplicate}
