"""Submission records, the apply queue, and the duplicate guard.

The rules pinned here are the ones that protect the user rather than the code:
recording a submission is the act of applying (so a tracker cannot show a sent
application as still saved), a repeat application to the same role is caught, the
same company for a different role is not, and an application from before this
feature reports an honest empty record instead of erroring.
"""
from datetime import datetime, timedelta, timezone

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


async def make_application(db, *, company, role, status="saved", applied_at=None, position=0):
    from app.models import Application

    async with db._session() as session:
        async with session.begin():
            row = Application(
                application_id=f"app-{company}-{role}-{status}".replace(" ", "-").lower(),
                user_id=USER,
                job_id=f"job-{company}-{role}".replace(" ", "-").lower(),
                resume_id="resume-1",
                status=status,
                company=company,
                role=role,
                applied_at=applied_at,
                position=position,
            )
            session.add(row)
            return row.application_id


class TestSubmissionRecord:
    async def test_recording_a_submission_marks_it_applied(self, db):
        """Recording IS applying - leaving the two uncoordinated is how a tracker
        ends up showing sent applications as still saved."""
        app_id = await make_application(db, company="Acme", role="Engineer")
        record = await submissions.record_submission(
            USER,
            app_id,
            answers={"Notice period": "30 days"},
            resume_version_id="v3",
            submitted_via="extension",
        )
        assert record is not None
        assert record["status"] == "applied"
        assert record["applied_at"]
        assert record["answers"] == {"Notice period": "30 days"}
        assert record["resume_version_id"] == "v3"
        assert record["submitted_via"] == "extension"

    async def test_a_later_stage_is_not_dragged_backwards(self, db):
        """A submission recorded late must not undo an interview."""
        app_id = await make_application(
            db, company="Globex", role="Lead", status="interview", applied_at="2026-01-01T00:00:00Z"
        )
        record = await submissions.record_submission(
            USER, app_id, answers={}, resume_version_id=None, submitted_via="manual"
        )
        assert record["status"] == "interview"
        assert record["applied_at"] == "2026-01-01T00:00:00Z"

    async def test_round_trips(self, db):
        app_id = await make_application(db, company="Initech", role="Dev")
        await submissions.record_submission(
            USER, app_id, answers={"Q": "A"}, resume_version_id="v1", submitted_via="extension"
        )
        read = await submissions.get_submission(USER, app_id)
        assert read["answers"] == {"Q": "A"}
        assert read["has_record"] is True

    async def test_pre_feature_application_reports_empty_not_error(self, db):
        """The gap is real; pretending otherwise would be worse than admitting it."""
        app_id = await make_application(db, company="Old", role="Role", status="applied")
        read = await submissions.get_submission(USER, app_id)
        assert read is not None
        assert read["answers"] == {}
        assert read["has_record"] is False

    async def test_unknown_application_is_none(self, db):
        assert await submissions.record_submission(
            USER, "nope", answers={}, resume_version_id=None, submitted_via="manual"
        ) is None
        assert await submissions.get_submission(USER, "nope") is None

    async def test_another_users_application_is_not_reachable(self, db):
        app_id = await make_application(db, company="Acme", role="Engineer")
        assert await submissions.get_submission("someone-else", app_id) is None


class TestQueue:
    async def test_queue_holds_saved_applications_in_position_order(self, db):
        second = await make_application(db, company="B", role="Two", position=1)
        first = await make_application(db, company="A", role="One", position=0)
        items = await submissions.list_queue(USER)
        assert [i["application_id"] for i in items] == [first, second]

    async def test_applied_applications_leave_the_queue(self, db):
        app_id = await make_application(db, company="A", role="One")
        assert len(await submissions.list_queue(USER)) == 1
        await submissions.record_submission(
            USER, app_id, answers={}, resume_version_id=None, submitted_via="extension"
        )
        assert await submissions.list_queue(USER) == []

    async def test_reorder_is_stable(self, db):
        a = await make_application(db, company="A", role="One", position=0)
        b = await make_application(db, company="B", role="Two", position=1)
        c = await make_application(db, company="C", role="Three", position=2)

        moved = await submissions.reorder_queue(USER, [c, a, b])
        assert moved == 3
        assert [i["application_id"] for i in await submissions.list_queue(USER)] == [c, a, b]

    async def test_reorder_ignores_ids_the_user_does_not_own(self, db):
        """A stale tab reordering a changed list must not fail the whole request."""
        a = await make_application(db, company="A", role="One")
        moved = await submissions.reorder_queue(USER, ["ghost", a])
        assert moved == 1
        assert len(await submissions.list_queue(USER)) == 1


class TestDuplicateGuard:
    async def test_catches_a_repeat_of_the_same_role(self, db):
        await make_application(
            db, company="Acme", role="Engineer", status="applied", applied_at=_recent()
        )
        found = await submissions.find_duplicate(USER, company="Acme", role="Engineer")
        assert found is not None
        assert found["company"] == "Acme"

    async def test_same_company_different_role_is_allowed(self, db):
        """The common case for anyone targeting one employer."""
        await make_application(
            db, company="Acme", role="Engineer", status="applied", applied_at=_recent()
        )
        assert await submissions.find_duplicate(USER, company="Acme", role="Designer") is None

    async def test_matching_ignores_case_and_spacing(self, db):
        await make_application(
            db, company="Acme Corp", role="Senior Engineer", status="applied", applied_at=_recent()
        )
        found = await submissions.find_duplicate(
            USER, company="  acme   corp ", role="senior engineer"
        )
        assert found is not None

    async def test_outside_the_cool_off_window_is_a_new_opportunity(self, db):
        old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        await make_application(
            db, company="Acme", role="Engineer", status="applied", applied_at=old
        )
        assert await submissions.find_duplicate(USER, company="Acme", role="Engineer") is None

    async def test_a_saved_application_is_not_a_duplicate(self, db):
        """Only a live application counts; a queued one is the thing being added."""
        await make_application(db, company="Acme", role="Engineer", status="saved")
        assert await submissions.find_duplicate(USER, company="Acme", role="Engineer") is None

    async def test_rejected_does_not_block_reapplying(self, db):
        await make_application(
            db, company="Acme", role="Engineer", status="rejected", applied_at=_recent()
        )
        assert await submissions.find_duplicate(USER, company="Acme", role="Engineer") is None

    async def test_missing_company_or_role_does_not_guess(self, db):
        await make_application(
            db, company="Acme", role="Engineer", status="applied", applied_at=_recent()
        )
        assert await submissions.find_duplicate(USER, company="Acme", role=None) is None
        assert await submissions.find_duplicate(USER, company=None, role="Engineer") is None

    async def test_applied_without_a_date_counts_as_recent(self, db):
        """We know it was applied; the safer read is that it was recent."""
        await make_application(
            db, company="Acme", role="Engineer", status="applied", applied_at=None
        )
        assert await submissions.find_duplicate(USER, company="Acme", role="Engineer") is not None


def _recent() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
