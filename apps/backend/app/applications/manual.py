"""Manual add: create a tracker card from a pasted job description.

The orchestration used to live inline in the REST handler
(``app/routers/applications.py``); the MCP tool layer (``app/mcp/tools/
applications.py``) needs the exact same sequence, so it is a shared service
function here and both layers call it - extraction fallback, orphan cleanup,
and the company/role cache can no longer drift between them.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.improver import extract_job_keywords

logger = logging.getLogger(__name__)


class ManualApplicationCreateError(Exception):
    """Application creation failed after the job was created.

    The just-created job has been cleaned up (no orphan jobs / retry drift);
    the caller surfaces this as its own transport error (REST: 500, MCP:
    tool error).
    """


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


async def create_manual_application(
    user_id: str,
    job_description: str,
    resume_id: str,
    *,
    company: str | None = None,
    role: str | None = None,
    notes: str | None = None,
    status: str = "applied",
) -> dict[str, Any]:
    """Manually add a card from a pasted job description.

    Creates the job, runs a best-effort company/role extraction when not
    provided, then creates the application. If application creation fails the
    just-created job is cleaned up (no orphan jobs / retry drift); caching
    company/role on the job is best-effort and never fails the request.

    ``db`` is resolved at call time (not import time) so tests and any future
    re-binding of the process-wide ``app.database.db`` are picked up.
    """
    from app.database import db

    job = await db.create_job(user_id, content=job_description, resume_id=resume_id)

    final_company = company
    final_role = role
    if not final_company or not final_role:
        extracted = await _extract_company_role(job_description)
        final_company = final_company or extracted.get("company")
        final_role = final_role or extracted.get("role")

    try:
        application = await db.create_application(
            user_id,
            job_id=job["job_id"],
            resume_id=resume_id,
            status=status,
            company=final_company,
            role=final_role,
            notes=notes,
        )
    except Exception as e:
        logger.error("Failed to create application: %s", e)
        try:
            await db.delete_job(user_id, job["job_id"])
        except Exception as cleanup_error:
            logger.warning("Failed to clean up orphan job %s: %s", job["job_id"], cleanup_error)
        raise ManualApplicationCreateError(
            "Failed to create application. Please try again."
        ) from e

    # Best-effort: cache company/role on the job for later reuse - never 500.
    if final_company or final_role:
        try:
            await db.update_job(
                user_id, job["job_id"], {"company": final_company, "role": final_role}
            )
        except Exception as e:
            logger.warning("Failed to cache company/role on job %s: %s", job["job_id"], e)

    return application
