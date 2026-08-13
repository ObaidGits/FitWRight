"""Submission records and the apply queue.

Two related jobs, both about what happens around pressing submit:

**The submission record.** After an application is sent, this stores what was
actually said - the answers, which resume version the employer saw, and how it was
sent. Nothing else can answer "what notice period did I claim at Acme?" three
weeks later, and it is what makes callback rates comparable between resume
variants.

**The apply queue.** An ordered list of jobs to work through in one sitting. It
holds *jobs*, never partially filled forms: a half-completed form on an
employer's site cannot be persisted across tabs or sessions, so the queue's job
is to decide what you open next, and the extension fills each one as you reach it.

The queue is deliberately not a new status. The tracker already has ``saved`` for
"intend to apply, not applied yet" and a ``position`` column for ordering, so the
queue is a view over those rows. Inventing an eighth status would have split the
same concept across two columns of the board.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.database import db
from app.models import Application

# How long a previous application to the same role suppresses a new one. Long
# enough that re-applying looks careless to the employer, short enough that a
# genuinely re-opened role is not blocked forever.
DEFAULT_COOL_OFF_DAYS = 90

# Statuses that mean "this application is live with the employer". Re-applying
# while any of these hold is what the duplicate guard exists to prevent.
LIVE_STATUSES = ("applied", "no_response", "response", "interview", "accepted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str | None) -> str:
    """Compare company and role names loosely enough to catch a real repeat."""
    return " ".join((text or "").strip().lower().split())


async def record_submission(
    user_id: str,
    application_id: str,
    *,
    answers: dict[str, Any] | None,
    resume_version_id: str | None,
    submitted_via: str,
) -> dict[str, Any] | None:
    """Store what was submitted, and mark the application applied.

    Marking it applied here rather than expecting a separate call is deliberate:
    recording a submission IS the act of applying, and leaving the two to be
    coordinated by the caller is how a tracker ends up with applications that were
    sent but still show as saved.

    An application already further along the funnel (interview, offer) keeps its
    status - a late submission record must not drag it backwards.
    """
    async with db._session() as session:  # noqa: SLF001 - module-internal by design
        async with session.begin():
            row = (
                await session.execute(
                    select(Application).where(
                        (Application.user_id == user_id)
                        & (Application.application_id == application_id)
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None

            row.submitted_answers = answers or {}
            row.submitted_resume_version_id = resume_version_id
            row.submitted_via = submitted_via

            if row.status in ("saved", None):
                row.status = "applied"
            if not row.applied_at:
                row.applied_at = _now()
            row.updated_at = _now()

            return _submission_dict(row)


async def get_submission(user_id: str, application_id: str) -> dict[str, Any] | None:
    """What was submitted for this application, or None if it does not exist.

    An application from before this feature has no record, which is reported as
    empty rather than as an error: the gap is real and pretending otherwise would
    be worse than admitting it.
    """
    async with db._session() as session:  # noqa: SLF001
        row = (
            await session.execute(
                select(Application).where(
                    (Application.user_id == user_id)
                    & (Application.application_id == application_id)
                )
            )
        ).scalar_one_or_none()
        return _submission_dict(row) if row is not None else None


def _submission_dict(row: Application) -> dict[str, Any]:
    return {
        "application_id": row.application_id,
        "company": row.company,
        "role": row.role,
        "status": row.status,
        "applied_at": row.applied_at,
        "answers": row.submitted_answers or {},
        "resume_version_id": row.submitted_resume_version_id,
        "submitted_via": row.submitted_via,
        # False for anything applied before this feature existed.
        "has_record": bool(row.submitted_answers or row.submitted_via),
    }


async def list_queue(user_id: str) -> list[dict[str, Any]]:
    """Jobs to work through, in the order they should be opened."""
    async with db._session() as session:  # noqa: SLF001
        rows = (
            (
                await session.execute(
                    select(Application)
                    .where((Application.user_id == user_id) & (Application.status == "saved"))
                    .order_by(Application.position, Application.created_at)
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "application_id": r.application_id,
                "job_id": r.job_id,
                "company": r.company,
                "role": r.role,
                "position": r.position,
                "created_at": r.created_at,
            }
            for r in rows
        ]


async def reorder_queue(user_id: str, ordered_ids: list[str]) -> int:
    """Set queue order from a list of ids. Returns how many were repositioned.

    Ids the user does not own are ignored rather than rejected, so a stale tab
    reordering a list that has since changed cannot fail the whole request.
    """
    async with db._session() as session:  # noqa: SLF001
        async with session.begin():
            rows = (
                (
                    await session.execute(
                        select(Application).where(
                            (Application.user_id == user_id)
                            & (Application.application_id.in_(ordered_ids))
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_id = {r.application_id: r for r in rows}
            moved = 0
            for index, application_id in enumerate(ordered_ids):
                row = by_id.get(application_id)
                if row is None:
                    continue
                row.position = index
                row.updated_at = _now()
                moved += 1
            return moved


async def find_duplicate(
    user_id: str,
    *,
    company: str | None,
    role: str | None,
    cool_off_days: int = DEFAULT_COOL_OFF_DAYS,
) -> dict[str, Any] | None:
    """A live application to the same company and role, if one exists.

    Applying twice to the same role reads as careless to an employer and wastes
    the user's time, so this is checked before queueing. Matching is on normalized
    company AND role: the same company for a genuinely different role is fine, and
    is the common case for anyone targeting one employer.

    Only applications inside the cool-off window count. A role re-posted a year
    later is a new opportunity, not a repeat.
    """
    if not company or not role:
        # Without both, any match would be a guess. Better to allow the queue
        # entry than to block on a coincidence of company name alone.
        return None

    cutoff = (datetime.now(timezone.utc) - timedelta(days=cool_off_days)).isoformat()
    target_company, target_role = _norm(company), _norm(role)

    async with db._session() as session:  # noqa: SLF001
        rows = (
            (
                await session.execute(
                    select(Application).where(
                        (Application.user_id == user_id)
                        & (Application.status.in_(LIVE_STATUSES))
                    )
                )
            )
            .scalars()
            .all()
        )

    for row in rows:
        if _norm(row.company) != target_company or _norm(row.role) != target_role:
            continue
        # No applied_at (older row) still counts as a duplicate: we know it was
        # applied, we just do not know when, and the safer read is "recent".
        if row.applied_at and row.applied_at < cutoff:
            continue
        return {
            "application_id": row.application_id,
            "company": row.company,
            "role": row.role,
            "status": row.status,
            "applied_at": row.applied_at,
        }
    return None


# A reply rate needs enough applications behind it to mean anything. Below this
# the raw counts are reported and `rate` stays None.
MIN_SAMPLE = 3

# Statuses that mean the employer came back. `applied` is still in flight and
# `no_response` is a closed door, so neither counts as a reply.
REPLIED_STATUSES = frozenset({"response", "interview", "accepted"})

# Statuses that mean the application has run its course, reply or not. The
# denominator, so an application sent yesterday does not drag the rate down.
CONCLUDED_STATUSES = REPLIED_STATUSES | {"no_response", "rejected"}


async def outcomes_by_resume(user_id: str) -> dict[str, Any]:
    """Reply rate per resume used, plus the totals behind each one.

    Grouped on ``resume_id`` - the resume actually sent - rather than on the
    submission record, so applications tracked before submission recording
    existed still count. Their answers are unknown; which resume went out is not.
    """
    from app.models import Resume

    async with db._session() as session:  # noqa: SLF001
        rows = (
            (
                await session.execute(
                    select(Application).where(Application.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )

        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row.status == "saved":
                continue  # Never sent, so it has no outcome to report.
            bucket = groups.setdefault(
                row.resume_id,
                {"resume_id": row.resume_id, "sent": 0, "replied": 0, "concluded": 0},
            )
            bucket["sent"] += 1
            if row.status in REPLIED_STATUSES:
                bucket["replied"] += 1
            if row.status in CONCLUDED_STATUSES:
                bucket["concluded"] += 1

        if not groups:
            return {"resumes": [], "min_sample": MIN_SAMPLE, "sent": 0, "replied": 0}

        names = dict(
            (
                await session.execute(
                    select(Resume.resume_id, Resume.filename).where(
                        Resume.resume_id.in_(list(groups))
                    )
                )
            ).all()
        )

    items = []
    for group in groups.values():
        concluded = group["concluded"]
        items.append(
            {
                **group,
                "name": names.get(group["resume_id"]) or "Untitled resume",
                # Rate over concluded applications only, and only once the
                # sample is big enough to be worth acting on.
                "rate": (
                    round(group["replied"] / concluded, 3)
                    if concluded >= MIN_SAMPLE
                    else None
                ),
            }
        )

    # Best-performing first, but anything without a rate sorts last rather than
    # as a zero - "not enough data" is not the same as "never works".
    items.sort(key=lambda i: (i["rate"] is None, -(i["rate"] or 0), -i["sent"]))

    return {
        "resumes": items,
        "min_sample": MIN_SAMPLE,
        "sent": sum(i["sent"] for i in items),
        "replied": sum(i["replied"] for i in items),
    }
