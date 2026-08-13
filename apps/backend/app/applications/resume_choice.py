"""Which resume should go out for a given job.

FitWright exists to produce a resume tailored to one job. Until this module, the
apply step ignored that entirely and attached the master resume - so every
tailored resume the user generated went unused at the exact moment it mattered.

The resolution order is deliberately narrow, because attaching the *wrong*
tailored resume is worse than attaching the master:

1. A tracked application for the same company and role. Its ``resume_id`` is the
   resume the user chose for this job, which is as close to intent as we get.
2. Otherwise the master resume - the honest fallback, and what a person would
   send if they had prepared nothing specific.

Matching is on normalized company AND role together. Company alone is not enough:
two roles at the same employer deserve different resumes, and quietly sending the
one tailored for a different opening would be a silent downgrade.
"""

from __future__ import annotations

from typing import Any

from app.applications.submissions import _norm
from app.database import Database

__all__ = ["resolve_resume_id_for_role"]


async def resolve_resume_id_for_role(
    db: Database,
    user_id: str,
    *,
    company: str | None,
    role: str | None,
) -> str | None:
    """Return the resume *tailored* for this company+role, or None.

    None means "nothing specific was prepared" and the caller should fall back to
    the master resume. It never guesses from a partial match.

    A queued job that has not been tailored yet holds the master resume on its
    application row. Returning that would be technically a match and a lie in
    practice - the extension would announce "tailored resume attached" while
    sending the generic one. So the master is filtered out here rather than at the
    call site, where the next caller would forget.
    """
    target_company = _norm(company)
    target_role = _norm(role)
    if not target_company or not target_role:
        return None

    # Newest first, so a re-tailored role returns the later attempt.
    rows = await db.list_application_rows(user_id)
    candidates = [
        row["resume_id"]
        for row in rows
        if _norm(row["company"]) == target_company and _norm(row["role"]) == target_role
    ]
    if not candidates:
        return None

    masters = await db.get_master_resume_ids(user_id, candidates)
    for resume_id in candidates:
        if resume_id not in masters:
            return resume_id
    return None


async def describe_resume_choice(
    db: Database,
    user_id: str,
    *,
    company: str | None,
    role: str | None,
) -> dict[str, Any]:
    """The chosen resume id plus whether it was tailored, for honest UI.

    The extension shows "tailored resume attached" versus "master resume
    attached", and the difference matters enough to the user that it must come
    from the same lookup that picked the file rather than being inferred.
    """
    tailored = await resolve_resume_id_for_role(db, user_id, company=company, role=role)
    return {"resume_id": tailored, "tailored": tailored is not None}
