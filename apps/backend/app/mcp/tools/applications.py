"""Application-tracker tools - thin wrappers over service calls.

Read tools cover the board (list/detail), the apply queue, and the
duplicate-application check. Write tools (``add_application``,
``update_application_status``) mirror the REST manual-add and PATCH handlers
in ``app/routers/applications.py`` by calling the exact same service sequence,
so behavior (extraction, orphan cleanup, dedupe) can never drift.
"""

from __future__ import annotations

from fastmcp.dependencies import CurrentAccessToken
from fastmcp.server.auth import AccessToken
from pydantic import ValidationError

from app.mcp.server import get_mcp_instance
from app.mcp.tools._context import current_user_id

mcp = get_mcp_instance()


def _first_validation_error(exc: ValidationError) -> ValueError:
    """A pydantic ValidationError as a one-line actionable ValueError.

    The REST layer rejects bad bodies via pydantic; the tool layer reuses the
    same schemas, so the first failing field is surfaced with its message.
    """
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first.get("loc", ()))
    return ValueError(f"invalid_argument: {field} - {first.get('msg')}")


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


@mcp.tool
async def add_application(
    job_description: str,
    company: str | None = None,
    role: str | None = None,
    resume_id: str | None = None,
    token: AccessToken = CurrentAccessToken(),
) -> dict:
    """Manually add a job-application tracker card from a pasted job
    description. The new card starts in the "applied" column.

    company/role are optional overrides; when omitted, a best-effort
    extraction from the job description fills them in (same as the manual-add
    REST endpoint). resume_id is the resume the card is attached to - get one
    from list_resumes.
    """
    user_id = current_user_id(token)
    from app.database import db
    from app.routers.applications import _extract_company_role
    from app.schemas import ManualApplicationCreate

    # Same bounds as the REST body (ManualApplicationCreate): a non-empty
    # job_description, and the resume the card attaches to.
    if not resume_id:
        raise ValueError(
            "resume_id_required: add_application needs the resume to attach "
            "the card to. Call list_resumes to get valid resume ids."
        )
    try:
        request = ManualApplicationCreate(
            job_description=job_description,
            company=company,
            role=role,
            resume_id=resume_id,
        )
    except ValidationError as exc:
        raise _first_validation_error(exc) from None

    # Mirror of the REST create_application handler (app/routers/
    # applications.py): create the job, extract company/role when missing,
    # then create the application with orphan-job cleanup on failure and a
    # best-effort company/role cache on the job.
    job = await db.create_job(
        user_id, content=request.job_description, resume_id=request.resume_id
    )

    final_company = request.company
    final_role = request.role
    if not final_company or not final_role:
        extracted = await _extract_company_role(request.job_description)
        final_company = final_company or extracted.get("company")
        final_role = final_role or extracted.get("role")

    try:
        application = await db.create_application(
            user_id,
            job_id=job["job_id"],
            resume_id=request.resume_id,
            status=request.status.value,
            company=final_company,
            role=final_role,
            notes=request.notes,
        )
    except Exception:
        # No orphan jobs / retry drift - cleanup is best-effort, never raises.
        try:
            await db.delete_job(user_id, job["job_id"])
        except Exception:
            pass
        raise ValueError(
            "application_create_failed: Failed to create application. "
            "Please try again."
        ) from None

    if final_company or final_role:
        # Best-effort cache on the job for later reuse - never fails.
        try:
            await db.update_job(
                user_id, job["job_id"], {"company": final_company, "role": final_role}
            )
        except Exception:
            pass

    return application


@mcp.tool
async def update_application_status(
    application_id: str, status: str, token: AccessToken = CurrentAccessToken()
) -> dict:
    """Move one tracker card to a different status column.

    Valid statuses (any transition is allowed, same as the REST board):
    saved, applied, no_response, response, interview, accepted, rejected.
    application_id must come from list_applications or get_apply_queue.
    """
    user_id = current_user_id(token)
    from app.database import db
    from app.schemas import APPLICATION_STATUS_ORDER, ApplicationStatus

    # Same enum the REST PATCH body validates against - checked BEFORE the
    # mutation so a typo never reaches the data layer.
    try:
        status_value = ApplicationStatus(status).value
    except ValueError:
        raise ValueError(
            f"invalid_status: {status!r}. Valid statuses: "
            f"{', '.join(APPLICATION_STATUS_ORDER)}."
        ) from None

    try:
        updated = await db.update_application(
            user_id, application_id, {"status": status_value}
        )
    except Exception:
        raise ValueError(
            "application_update_failed: Failed to update application. "
            "Please try again."
        ) from None

    if updated is None:
        raise ValueError(
            f"application_not_found: {application_id}. Call list_applications "
            "to get valid application ids."
        )
    return updated
