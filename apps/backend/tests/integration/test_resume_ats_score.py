"""The match score survives past the review screen.

Tailoring computed a score, showed it once, and threw it away. A user with a
dozen tailored variants therefore had no way to see which one actually matched
its job best. These tests pin the three properties that make the stored score
trustworthy rather than merely present.
"""

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Resume


async def _seed(db, user_id: str):
    resume = await db.create_resume(
        user_id,
        content='{"personalInfo":{"name":"Jane"}}',
        content_type="json",
        processed_data={"personalInfo": {"name": "Jane"}},
        processing_status="ready",
        filename="resume.json",
    )
    job = await db.create_job(user_id, content="Senior Python engineer")
    return resume, job


async def _confirm(db, user_id: str, resume, job, *, ats_score):
    preview = await db.create_tailor_preview(
        user_id,
        resume_id=resume["resume_id"],
        job_id=job["job_id"],
        prompt_id="keywords",
        payload_hash="hash",
        request_id=str(uuid4()),
        result_payload={"data": {}},
    )
    assert preview is not None
    return await db.confirm_tailor_preview(
        user_id,
        preview_id=preview["preview_id"],
        resume_id=resume["resume_id"],
        job_id=job["job_id"],
        payload_hash="hash",
        improved_data={"personalInfo": {"name": "Jane"}, "summary": "Python"},
        improved_text="{}",
        improvements=[{"suggestion": "Target Python"}],
        cover_letter=None,
        outreach_message=None,
        interview_prep=None,
        title="Python Engineer",
        ats_score=ats_score,
    )


async def _tailored_row(db, user_id: str, source_id: str) -> Resume:
    async with db.session_factory() as session:
        return (
            await session.execute(
                select(Resume).where(
                    Resume.user_id == user_id, Resume.parent_id == source_id
                )
            )
        ).scalars().one()


@pytest.mark.asyncio
class TestResumeAtsScorePersistence:
    async def test_score_is_stored_on_the_tailored_resume(self, isolated_db, owner_id):
        resume, job = await _seed(isolated_db, owner_id)
        status, committed = await _confirm(
            isolated_db, owner_id, resume, job, ats_score=73.5
        )
        assert status == "created" and committed is not None

        row = await _tailored_row(isolated_db, owner_id, resume["resume_id"])
        assert row.ats_score == pytest.approx(73.5)

    async def test_absent_score_stays_null_rather_than_zero(self, isolated_db, owner_id):
        # Scoring can fail without costing the user the resume they accepted, so
        # None must round-trip as "no score". Zero would be a lie: it reads as a
        # terrible match rather than an unmeasured one.
        resume, job = await _seed(isolated_db, owner_id)
        status, _ = await _confirm(isolated_db, owner_id, resume, job, ats_score=None)
        assert status == "created"

        row = await _tailored_row(isolated_db, owner_id, resume["resume_id"])
        assert row.ats_score is None

    async def test_an_untailored_resume_has_no_score(self, isolated_db, owner_id):
        # A resume that was never tailored has no job to be measured against, so
        # the column stays NULL for its whole life. This is the state every
        # master resume is permanently in.
        resume, _ = await _seed(isolated_db, owner_id)
        async with isolated_db.session_factory() as session:
            row = (
                await session.execute(
                    select(Resume).where(Resume.resume_id == resume["resume_id"])
                )
            ).scalars().one()
        assert row.ats_score is None

    async def test_list_summaries_expose_the_score(self, isolated_db, owner_id):
        # The library ranks and badges on this, so the list endpoint - not just
        # the single-resume read - has to carry it.
        resume, job = await _seed(isolated_db, owner_id)
        await _confirm(isolated_db, owner_id, resume, job, ats_score=91.0)

        summaries = await isolated_db.list_resume_summaries(owner_id, include_master=True)
        by_id = {row["resume_id"]: row for row in summaries}
        assert "ats_score" in summaries[0]

        # The tailored copy is the one whose parent is the source resume.
        tailored = [row for row in summaries if row["parent_id"] == resume["resume_id"]]
        assert len(tailored) == 1
        assert tailored[0]["ats_score"] == pytest.approx(91.0)
        # The resume it was tailored FROM was never scored, and must not inherit
        # its child's score.
        assert by_id[resume["resume_id"]]["ats_score"] is None
