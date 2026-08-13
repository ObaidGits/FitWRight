"""Kanban application-tracker endpoints."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.applications import submissions
from app.auth import get_effective_user_id
from app.database import db
from app.services.improver import extract_job_keywords
from app.schemas import (
    APPLICATION_STATUS_ORDER,
    ApplicationActionResponse,
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationResponse,
    ApplicationUpdate,
    BulkDelete,
    BulkStatusUpdate,
    ManualApplicationCreate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/applications", tags=["Application Tracker"])


def _group_by_status(applications: list[dict[str, Any]]) -> dict[str, list[ApplicationResponse]]:
    """Group a flat list into the seven columns (all keys always present).

    A row with an unknown status can't be represented by the enum-backed
    ``ApplicationResponse``; rather than 500 the whole board we skip it (the
    board only renders the seven known columns) and log it.
    """
    columns: dict[str, list[ApplicationResponse]] = {s: [] for s in APPLICATION_STATUS_ORDER}
    for app in applications:
        status = app.get("status")
        if status not in columns:
            logger.warning("Skipping application with unknown status %r", status)
            continue
        columns[status].append(ApplicationResponse(**app))
    return columns


@router.get("", response_model=ApplicationListResponse)
async def list_applications(
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationListResponse:
    """List all applications grouped by status column."""
    try:
        applications = await db.list_applications(user_id)
    except Exception as e:
        logger.error("Failed to list applications: %s", e)
        raise HTTPException(status_code=500, detail="Failed to load applications. Please try again.")
    return ApplicationListResponse(columns=_group_by_status(applications))


@router.post("", response_model=ApplicationResponse)
async def create_application(
    request: ManualApplicationCreate,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationResponse:
    """Manually add a card from a pasted job description.

    Creates the job, runs a best-effort company/role extraction when not
    provided, then creates the application. If application creation fails the
    just-created job is cleaned up (no orphan jobs / retry drift); caching
    company/role on the job is best-effort and never fails the request.
    """
    job = await db.create_job(
        user_id, content=request.job_description, resume_id=request.resume_id
    )

    company = request.company
    role = request.role
    if not company or not role:
        extracted = await _extract_company_role(request.job_description)
        company = company or extracted.get("company")
        role = role or extracted.get("role")

    try:
        application = await db.create_application(
            user_id,
            job_id=job["job_id"],
            resume_id=request.resume_id,
            status=request.status.value,
            company=company,
            role=role,
            notes=request.notes,
        )
    except Exception as e:
        logger.error("Failed to create application: %s", e)
        try:
            await db.delete_job(user_id, job["job_id"])
        except Exception as cleanup_error:
            logger.warning("Failed to clean up orphan job %s: %s", job["job_id"], cleanup_error)
        raise HTTPException(status_code=500, detail="Failed to create application. Please try again.")

    # Best-effort: cache company/role on the job for later reuse - never 500.
    if company or role:
        try:
            await db.update_job(user_id, job["job_id"], {"company": company, "role": role})
        except Exception as e:
            logger.warning("Failed to cache company/role on job %s: %s", job["job_id"], e)

    return ApplicationResponse(**application)


# --------------------------------------------------------------------------- #
# Apply queue and submission records (Phase 5)
#
# These are declared BEFORE `/{application_id}` on purpose. FastAPI matches routes
# in definition order, so a `/queue` route added after it would never be reached -
# "queue" would be captured as an application id and 404.
# --------------------------------------------------------------------------- #
class SubmissionRequest(BaseModel):
    """What was actually submitted to the employer."""

    answers: dict[str, Any] = Field(default_factory=dict)
    resume_version_id: str | None = None
    # extension | manual | api
    submitted_via: str = "manual"


class ReorderRequest(BaseModel):
    """The queue in the order it should be worked through."""

    application_ids: list[str] = Field(default_factory=list, max_length=500)


class DuplicateCheckRequest(BaseModel):
    company: str | None = None
    role: str | None = None


@router.get("/queue", summary="Jobs to work through, in order")
async def get_apply_queue(user_id: str = Depends(get_effective_user_id)):
    """The apply queue: saved applications in the order to open them.

    Holds jobs, not part-filled forms - an employer's form cannot be persisted
    across tabs, so the queue decides what you open next and the extension fills
    each one when you get there.
    """
    items = await submissions.list_queue(user_id)
    return {"items": items, "total": len(items)}


@router.post("/queue/reorder", summary="Reorder the apply queue")
async def reorder_apply_queue(
    body: ReorderRequest, user_id: str = Depends(get_effective_user_id)
):
    moved = await submissions.reorder_queue(user_id, body.application_ids)
    return {"reordered": moved}


@router.post("/queue/check-duplicate", summary="Have I already applied to this?")
async def check_duplicate(
    body: DuplicateCheckRequest, user_id: str = Depends(get_effective_user_id)
):
    """Warn before queueing a role already applied to.

    Advisory, not a block: the user may have a legitimate reason (a referral, a
    genuinely re-opened req), and refusing outright would be the tool overruling
    someone with more context than it has.
    """
    duplicate = await submissions.find_duplicate(
        user_id, company=body.company, role=body.role
    )
    return {"duplicate": duplicate, "is_duplicate": duplicate is not None}


@router.get("/export.csv", summary="Download your applications as CSV")
async def export_applications_csv(user_id: str = Depends(get_effective_user_id)):
    """Every application as a spreadsheet.

    Months of application history with no way out is a lock-in the user did not
    agree to. CSV rather than JSON because the people who want this open it in a
    spreadsheet, not a text editor.

    Answers are deliberately *not* included. They can contain anything the user
    typed into an employer's form, and a file that lands in a downloads folder is
    the wrong place for that by default; the count is given instead so nothing
    appears to be missing silently.
    """
    import csv
    import io

    from fastapi.responses import StreamingResponse

    from app.applications import submissions

    rows = await submissions.export_rows(user_id)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "company",
            "role",
            "status",
            "applied_at",
            "submitted_via",
            "answers_recorded",
            "resume_id",
            "created_at",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("company") or "",
                row.get("role") or "",
                row.get("status") or "",
                row.get("applied_at") or "",
                row.get("submitted_via") or "",
                row.get("answers_recorded") or 0,
                row.get("resume_id") or "",
                row.get("created_at") or "",
            ]
        )

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="fitwright-applications.csv"'},
    )


@router.get("/outcomes", summary="Which resume actually gets replies")
async def get_outcomes(user_id: str = Depends(get_effective_user_id)):
    """Reply rate per resume, so the next application is an informed one.

    A reply is any status past "sent and waiting" - response, interview or
    accepted. ``no_response`` and ``rejected`` are outcomes, not silence, and
    ``applied`` is still in flight.

    Rates are withheld below ``MIN_SAMPLE`` applications and the raw counts are
    shown instead. "100% reply rate" from one application is not a finding, and
    dressing it up as one would push the user to bet on noise.
    """
    return await submissions.outcomes_by_resume(user_id)


@router.post("/{application_id}/submission", summary="Record what was submitted")
async def create_submission(
    application_id: str,
    body: SubmissionRequest,
    user_id: str = Depends(get_effective_user_id),
):
    """Store the answers, resume version and channel, and mark it applied."""
    record = await submissions.record_submission(
        user_id,
        application_id,
        answers=body.answers,
        resume_version_id=body.resume_version_id,
        submitted_via=body.submitted_via,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Application not found")
    logger.info(
        "Recorded submission for %s (%d answers, via %s)",
        application_id,
        len(body.answers),  # count only - the answers themselves are never logged
        body.submitted_via,
    )
    return record


@router.get("/{application_id}/submission", summary="What was submitted")
async def read_submission(
    application_id: str, user_id: str = Depends(get_effective_user_id)
):
    record = await submissions.get_submission(user_id, application_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return record


@router.get("/{application_id}", response_model=ApplicationDetailResponse)
async def get_application_detail(
    application_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationDetailResponse:
    """Get a card with its embedded JD and applied resume (one round-trip).

    Tolerates a deleted resume by returning ``resume: null`` rather than 500.
    """
    application = await db.get_application_detail(user_id, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return ApplicationDetailResponse(**application)


@router.patch("/bulk", response_model=ApplicationActionResponse)
async def bulk_update_applications(
    request: BulkStatusUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationActionResponse:
    """Move many cards to one column."""
    try:
        moved = await db.bulk_update_applications(
            user_id, request.application_ids, request.status.value
        )
    except Exception as e:
        logger.error("Failed to bulk-update applications: %s", e)
        raise HTTPException(status_code=500, detail="Failed to move applications. Please try again.")
    return ApplicationActionResponse(message=f"Moved {moved} application(s)", affected=moved)


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    application_id: str,
    request: ApplicationUpdate,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationResponse:
    """Update a card (status/position/notes/company/role/applied_at)."""
    updates = request.model_dump(exclude_unset=True)
    # Normalize the enum to its stable string value for the data layer.
    if "status" in updates and updates["status"] is not None:
        updates["status"] = request.status.value
    try:
        updated = await db.update_application(user_id, application_id, updates)
    except Exception as e:
        logger.error("Failed to update application %s: %s", application_id, e)
        raise HTTPException(status_code=500, detail="Failed to update application. Please try again.")
    if updated is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return ApplicationResponse(**updated)


@router.delete("/{application_id}", response_model=ApplicationActionResponse)
async def delete_application(
    application_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationActionResponse:
    """Delete a card."""
    try:
        deleted = await db.delete_application(user_id, application_id)
    except Exception as e:
        logger.error("Failed to delete application %s: %s", application_id, e)
        raise HTTPException(status_code=500, detail="Failed to delete application. Please try again.")
    if not deleted:
        raise HTTPException(status_code=404, detail="Application not found")
    return ApplicationActionResponse(message="Application deleted", affected=1)


@router.post("/bulk-delete", response_model=ApplicationActionResponse)
async def bulk_delete_applications(
    request: BulkDelete,
    user_id: str = Depends(get_effective_user_id),
) -> ApplicationActionResponse:
    """Delete many cards."""
    try:
        deleted = await db.bulk_delete_applications(user_id, request.application_ids)
    except Exception as e:
        logger.error("Failed to bulk-delete applications: %s", e)
        raise HTTPException(status_code=500, detail="Failed to delete applications. Please try again.")
    return ApplicationActionResponse(message=f"Deleted {deleted} application(s)", affected=deleted)


async def _extract_company_role(job_description: str) -> dict[str, str | None]:
    """Best-effort company/role extraction for the manual-add path.

    Reuses the cached keyword-extraction pass; falls back to blank (editable)
    on any failure so a flaky LLM never blocks card creation. LLM output isn't
    guaranteed to be a string, so values are type-guarded before ``.strip()``.
    """
    try:
        keywords = await extract_job_keywords(job_description)
        raw_company = keywords.get("company")
        raw_role = keywords.get("role")
        return {
            "company": (raw_company.strip() if isinstance(raw_company, str) else "") or None,
            "role": (raw_role.strip() if isinstance(raw_role, str) else "") or None,
        }
    except Exception as e:
        logger.warning("Company/role extraction failed (manual add): %s", e)
        return {}
