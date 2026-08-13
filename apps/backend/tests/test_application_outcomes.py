"""Reply rate per resume.

This view exists to change a decision - which resume to send next - so the rules
that matter are the ones that keep it from being confidently wrong:

* a rate is withheld until enough applications have concluded, because "100%"
  off one reply is noise dressed as a finding;
* the denominator is concluded applications, not everything sent, so an
  application still in flight does not count as a failure;
* `saved` never counts at all - it was never sent, so it has no outcome;
* a resume with no rate sorts last rather than as a zero: "not enough data" and
  "never works" are different answers.

Rows are inserted directly, the same way `test_application_submissions.py` does.

Cross-user scoping is not re-tested here: `users` sits behind its own foreign
keys, and the API-level authz matrix already covers per-user isolation for
every application route.
"""
import pytest

from app.applications import submissions

USER: str = ""


@pytest.fixture
async def db(isolated_db, owner_id, monkeypatch):
    """The isolated database, wired in as the module-level `db` submissions uses."""
    global USER
    USER = owner_id
    monkeypatch.setattr(submissions, "db", isolated_db)
    return isolated_db


async def make_application(db, *, resume_id, status, suffix):
    from app.models import Application

    async with db._session() as session:
        async with session.begin():
            session.add(
                Application(
                    application_id=f"app-{resume_id}-{suffix}",
                    user_id=USER,
                    job_id=f"job-{suffix}",
                    resume_id=resume_id,
                    status=status,
                    company="Acme",
                    role=f"Engineer {suffix}",
                )
            )


async def make_resume(db, *, resume_id, filename):
    from app.models import Resume

    async with db._session() as session:
        async with session.begin():
            session.add(
                Resume(resume_id=resume_id, user_id=USER, content="# R", filename=filename)
            )


class TestOutcomes:
    async def test_no_applications_reports_nothing(self, db):
        result = await submissions.outcomes_by_resume(USER)
        assert result["resumes"] == []
        assert result["sent"] == 0
        assert result["replied"] == 0

    async def test_saved_applications_are_excluded(self, db):
        await make_application(db, resume_id="r1", status="saved", suffix="1")
        result = await submissions.outcomes_by_resume(USER)
        assert result["resumes"] == []
        assert result["sent"] == 0

    async def test_rate_withheld_below_the_sample_threshold(self, db):
        await make_application(db, resume_id="r1", status="interview", suffix="1")

        result = await submissions.outcomes_by_resume(USER)
        row = result["resumes"][0]
        assert row["sent"] == 1
        assert row["replied"] == 1
        # One reply out of one is not "100% reply rate".
        assert row["rate"] is None
        assert result["min_sample"] == submissions.MIN_SAMPLE

    async def test_rate_reported_once_enough_have_concluded(self, db):
        for i, status in enumerate(["interview", "rejected", "no_response", "response"]):
            await make_application(db, resume_id="r1", status=status, suffix=str(i))

        row = (await submissions.outcomes_by_resume(USER))["resumes"][0]
        assert row["sent"] == 4
        assert row["concluded"] == 4
        assert row["replied"] == 2  # interview + response
        assert row["rate"] == 0.5

    async def test_in_flight_applications_do_not_count_against_the_rate(self, db):
        for i, status in enumerate(["interview", "response", "accepted"]):
            await make_application(db, resume_id="r1", status=status, suffix=f"c{i}")
        for i in range(2):
            await make_application(db, resume_id="r1", status="applied", suffix=f"w{i}")

        row = (await submissions.outcomes_by_resume(USER))["resumes"][0]
        assert row["sent"] == 5
        assert row["concluded"] == 3
        # 3 of 3 concluded replied. Counting the two in flight would report 60%.
        assert row["rate"] == 1.0

    async def test_resumes_without_a_rate_sort_last(self, db):
        for i, status in enumerate(["rejected", "no_response", "interview"]):
            await make_application(db, resume_id="measured", status=status, suffix=f"m{i}")
        await make_application(db, resume_id="untested", status="applied", suffix="u0")

        rows = (await submissions.outcomes_by_resume(USER))["resumes"]
        assert rows[0]["resume_id"] == "measured"
        assert rows[0]["rate"] is not None
        assert rows[-1]["resume_id"] == "untested"
        assert rows[-1]["rate"] is None

    async def test_resume_filename_is_used_as_the_label(self, db):
        await make_resume(db, resume_id="r1", filename="backend-heavy.pdf")
        await make_application(db, resume_id="r1", status="applied", suffix="1")

        row = (await submissions.outcomes_by_resume(USER))["resumes"][0]
        assert row["name"] == "backend-heavy.pdf"

    async def test_missing_resume_row_still_reports(self, db):
        """A deleted resume must not make the whole view fail."""
        await make_application(db, resume_id="gone", status="applied", suffix="1")

        row = (await submissions.outcomes_by_resume(USER))["resumes"][0]
        assert row["name"] == "Untitled resume"
        assert row["sent"] == 1

    async def test_totals_span_every_resume(self, db):
        await make_application(db, resume_id="r1", status="interview", suffix="1")
        await make_application(db, resume_id="r2", status="rejected", suffix="2")

        result = await submissions.outcomes_by_resume(USER)
        assert result["sent"] == 2
        assert result["replied"] == 1
        assert len(result["resumes"]) == 2
