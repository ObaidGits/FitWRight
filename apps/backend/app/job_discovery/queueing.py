"""Turning a discovered job into something the apply queue can hold.

The gap this closes: marking a job "interested" in Discover changed a row in
``discovery_results`` and nothing else, while the apply queue is a view over
``applications``. So a user could save twenty jobs, open the queue, and find it
empty - the two halves of the product did not touch.

Design decisions worth keeping:

* **Interested means queued.** Rather than adding a separate "add to queue"
  action, saving a job is what puts it in the queue. Two verbs for one intention
  is how people end up with twenty saved jobs and an empty queue again.
* **The master resume is the placeholder, not a claim.** A queued job has not been
  tailored yet, so its application points at the master resume. ``resume_choice``
  filters masters out, so nothing will announce a tailored resume that does not
  exist.
* **Idempotent.** ``create_application`` dedupes on (user, job, resume), and the
  feed row remembers its application id, so toggling interested twice does not
  create a second queue entry.
* **Un-saving does not delete the application.** Dismissing a job it turns out you
  already applied to must not erase that history; it only leaves the queue.
"""

from __future__ import annotations

from typing import Any

from app.database import Database

__all__ = ["ensure_queued_application", "unqueue_application"]


def _job_content(row: dict[str, Any]) -> str:
    """A job-description body from what the board gave us.

    The description is often absent on a list-page harvest, so title/company/
    location are included: a tailoring run against an empty body would produce
    nothing useful, and this at least carries the role.
    """
    parts = [
        str(row.get("title") or "").strip(),
        str(row.get("company") or "").strip(),
        str(row.get("location") or "").strip(),
        str(row.get("url") or "").strip(),
    ]
    header = " · ".join(p for p in parts if p)
    body = str(row.get("description") or "").strip()
    return f"{header}\n\n{body}".strip()


async def ensure_queued_application(
    db: Database, user_id: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """Make sure this discovered job has a `saved` application, and return it.

    Returns None when the user has no resume at all - there is nothing to attach
    an application to yet, and inventing one would put a broken card on the board.
    """
    master = await db.get_master_resume(user_id)
    if not master:
        return None

    resume_id = master["resume_id"]

    existing_job_id = result.get("job_id")
    if existing_job_id:
        job_id = existing_job_id
    else:
        job = await db.create_job(user_id, _job_content(result), resume_id=resume_id)
        job_id = job["job_id"]

    application = await db.create_application(
        user_id,
        job_id=job_id,
        resume_id=resume_id,
        status="saved",
        company=str(result.get("company") or "") or None,
        role=str(result.get("title") or "") or None,
    )
    return {"application": application, "job_id": job_id}


async def unqueue_application(db: Database, user_id: str, job_id: str | None) -> None:
    """Remove a job from the queue when it is dismissed.

    Only touches applications still sitting at ``saved``. Anything further along
    is history the user earned, and dismissing a listing is not a reason to
    destroy the record that they applied to it.
    """
    if not job_id:
        return

    from sqlalchemy import delete

    from app.models import Application

    async with db._session() as session:  # noqa: SLF001
        async with session.begin():
            await session.execute(
                delete(Application).where(
                    (Application.user_id == user_id)
                    & (Application.job_id == job_id)
                    & (Application.status == "saved")
                )
            )
