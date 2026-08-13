"""Ingest validation and extension data deletion.

Two classes of problem pinned here.

**Bounds that only bite in production.** Only ``title`` had a length limit, while the
database columns have several. SQLite ignores declared column widths, so an
over-long company name stored happily on a local install and raised a 500 on the
Postgres deployment - a bug that appears only when hosted, which is the worst kind.

**A promise the code did not keep.** The form-report endpoint's whole guarantee is
"labels and types only, never values", and it stored the raw application URL -
routinely carrying a session token, an applicant id, or an email address in a query
parameter.
"""
import pytest

from app.routers.application_fields import FormReport, SaveAnswers, sanitize_url
from app.routers.extension import CapturedJob


class TestCapturedJobBounds:
    def test_over_long_text_is_clipped_to_its_own_column_width(self):
        job = CapturedJob(
            title="Engineer",
            company="A" * 400,
            location="B" * 400,
            url="https://example.test/1",
            salary="S" * 300,
        )
        # Each field to its own limit, not one shared number.
        assert len(job.company) == 255
        assert len(job.location) == 255
        assert len(job.salary) == 100

    def test_a_runaway_description_is_bounded(self):
        job = CapturedJob(
            title="Engineer", url="https://example.test/1", description="D" * 200_000
        )
        assert len(job.description) == 60_000

    def test_a_normal_job_is_untouched(self):
        job = CapturedJob(
            title="Senior Data Engineer",
            company="Globex",
            location="Pune, India",
            url="https://example.test/jobs/1",
            description="Build pipelines.",
        )
        assert job.company == "Globex"
        assert job.description == "Build pipelines."

    def test_an_over_long_url_is_refused_not_truncated(self):
        """A clipped URL is a broken link, which is worse than a rejected capture."""
        with pytest.raises(Exception):
            CapturedJob(title="x", url="https://e.test/" + "u" * 3000)

    def test_a_missing_title_is_still_refused(self):
        """Truncation must not have loosened the one field that must be present."""
        with pytest.raises(Exception):
            CapturedJob(title="", url="https://example.test/1")

    def test_non_string_values_survive_truncation(self):
        job = CapturedJob(title="x", url="https://example.test/1", is_remote=True)
        assert job.is_remote is True


class TestUrlSanitising:
    def test_a_token_in_the_query_is_dropped(self):
        assert (
            sanitize_url("https://acme.icims.com/jobs/1/apply?token=SECRET&email=me@example.com")
            == "https://acme.icims.com/jobs/1/apply"
        )

    def test_a_fragment_is_dropped(self):
        assert (
            sanitize_url("https://boards.greenhouse.io/acme/jobs/9#application")
            == "https://boards.greenhouse.io/acme/jobs/9"
        )

    def test_a_clean_url_is_unchanged(self):
        assert sanitize_url("https://x.test/apply") == "https://x.test/apply"

    def test_none_stays_none(self):
        assert sanitize_url(None) is None

    def test_something_unparseable_still_loses_its_query(self):
        assert sanitize_url("not a url?token=abc") == "not a url"

    def test_the_form_report_model_applies_it(self):
        report = FormReport(fields=[], url="https://acme.icims.com/a?token=SECRET")
        assert report.url == "https://acme.icims.com/a"
        assert "SECRET" not in (report.url or "")

    def test_the_answers_model_applies_it_too(self):
        """Both endpoints take a URL; a rule on one of them is not a rule."""
        answers = SaveAnswers(answers=[], url="https://acme.icims.com/a?token=SECRET")
        assert answers.url == "https://acme.icims.com/a"


class TestForgetExtensionData:
    async def test_removes_extension_captures_but_keeps_decisions(self, isolated_db, owner_id):
        from sqlalchemy import update

        from app.models import DiscoveryResult
        from app.routers.discovery import forget_extension_data

        db = isolated_db
        rows = [
            {
                "fingerprint": f"f{i}",
                "source": source,
                "title": f"Job {i}",
                "company": "Acme",
                "location": "Pune",
                "url": f"https://example.test/{i}",
                "match_score": 0,
                "matched": [],
                "missing": [],
                "partial": False,
            }
            for i, source in enumerate(["extension", "extension", "linkedin"])
        ]
        await db.upsert_discovery_results(owner_id, "run", rows)

        # One extension row the user decided about: a decision is not exhaust.
        async with db._session() as session:
            async with session.begin():
                await session.execute(
                    update(DiscoveryResult)
                    .where(DiscoveryResult.fingerprint == "f0")
                    .values(status="interested")
                )

        result = await forget_extension_data(user_id=owner_id, db=db)

        assert result.captured_jobs == 1  # only the untouched extension row
        remaining = await db.get_discovery_feed(owner_id, limit=50)
        sources = sorted(r["source"] for r in remaining)
        # The saved extension job and the server-harvested one both survive.
        assert sources == ["extension", "linkedin"]

    async def test_removes_unanswered_questions_but_keeps_answered_ones(
        self, isolated_db, owner_id
    ):
        """An answered question is the user's work, whatever created the row.

        The bug this pins was in the first version of this endpoint: it filtered on
        `source`, which is set at creation and never changes - so answering a
        question the extension found left it marked `learned`, and "delete what the
        extension contributed" deleted the user's own answers. Measured on a real
        database: 19 rows removed where only 2 were untouched.
        """
        from app.routers.discovery import forget_extension_data

        db = isolated_db
        await db.upsert_application_field(
            owner_id, label="Why us?", label_normalized="why us", source="learned"
        )
        answered = await db.upsert_application_field(
            owner_id, label="Notice period", label_normalized="notice period", source="learned"
        )
        assert answered is not None
        await db.set_application_field_value(
            owner_id, label_normalized="notice period", company=None, value="30 days"
        )

        result = await forget_extension_data(user_id=owner_id, db=db)

        assert result.learned_answers == 1  # only the unanswered one
        remaining = await db.list_application_fields(owner_id)
        assert [row["label"] for row in remaining] == ["Notice period"]

    async def test_keeps_answers_the_user_wrote_directly(self, isolated_db, owner_id):
        from app.routers.discovery import forget_extension_data

        db = isolated_db
        await db.upsert_application_field(
            owner_id,
            label="Salary expectation",
            label_normalized="salary expectation",
            source="user",
            status="answered",
        )

        result = await forget_extension_data(user_id=owner_id, db=db)
        assert result.learned_answers == 0
        assert len(await db.list_application_fields(owner_id)) == 1

    async def test_removes_board_health(self, isolated_db, owner_id):
        from app.job_discovery import board_health
        from app.routers.discovery import forget_extension_data

        db = isolated_db
        await board_health.record_outcome(db, owner_id, board="hirist", status="ok", found=3)

        result = await forget_extension_data(user_id=owner_id, db=db)
        assert result.board_health == 1
        assert await board_health.list_health(db, owner_id) == []

    async def test_is_safe_to_run_with_nothing_to_delete(self, isolated_db, owner_id):
        from app.routers.discovery import forget_extension_data

        result = await forget_extension_data(user_id=owner_id, db=isolated_db)
        assert (result.captured_jobs, result.learned_answers, result.board_health) == (0, 0, 0)
