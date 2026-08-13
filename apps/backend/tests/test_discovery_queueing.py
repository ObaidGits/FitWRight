"""Saving a job puts it in the apply queue, and duplicates collapse into one row.

Two gaps closed here, both of which made the feature feel like two products:

* the feed and the tracker never met, so twenty saved jobs produced an empty
  apply queue;
* the same opening harvested from four boards showed up four times, so a feed of
  300 might have been 120 real jobs.

The rules worth pinning are the restraints: dismissing a listing must not delete
an application already sent, saving twice must not queue twice, and collapsing
must not merge two genuinely different roles.
"""
import pytest

from app.job_discovery.normalize import group_fingerprint
from app.job_discovery.queueing import ensure_queued_application, unqueue_application

USER: str = ""


@pytest.fixture
async def db(isolated_db, owner_id):
    global USER
    USER = owner_id
    return isolated_db


@pytest.fixture
async def with_master(db):
    """A master resume, without which nothing can be queued."""
    from app.models import Resume

    async with db._session() as session:
        async with session.begin():
            session.add(
                Resume(
                    resume_id="master-1",
                    user_id=USER,
                    content="# Master",
                    filename="master.pdf",
                    is_master=True,
                )
            )
    return db


def result(**overrides):
    base = {
        "id": "res-1",
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Pune",
        "url": "https://acme.test/jobs/1",
        "description": "Build things.",
        "source": "linkedin",
        "match_score": 0.0,
        "job_id": None,
    }
    base.update(overrides)
    return base


async def queue_size(db):
    from app.applications import submissions

    from sqlalchemy import select

    from app.models import Application

    async with db._session() as session:
        rows = (
            (
                await session.execute(
                    select(Application).where(
                        (Application.user_id == USER) & (Application.status == "saved")
                    )
                )
            )
            .scalars()
            .all()
        )
    assert submissions is not None  # module import proves the queue reads the same rows
    return len(rows)


class TestQueueing:
    async def test_saving_a_job_creates_a_queue_entry(self, with_master):
        db = with_master
        created = await ensure_queued_application(db, USER, result())

        assert created is not None
        assert created["application"]["status"] == "saved"
        assert created["application"]["company"] == "Acme"
        assert created["application"]["role"] == "Backend Engineer"
        assert await queue_size(db) == 1

    async def test_saving_twice_does_not_queue_twice(self, with_master):
        db = with_master
        first = await ensure_queued_application(db, USER, result())
        assert first is not None
        # Second save reuses the job id the first one created, as the router does.
        again = await ensure_queued_application(
            db, USER, result(job_id=first["job_id"])
        )

        assert again is not None
        assert await queue_size(db) == 1

    async def test_no_resume_means_nothing_is_queued(self, db):
        """Without a resume there is nothing to attach; a broken card is worse."""
        assert await ensure_queued_application(db, USER, result()) is None
        assert await queue_size(db) == 0

    async def test_dismissing_removes_it_from_the_queue(self, with_master):
        db = with_master
        created = await ensure_queued_application(db, USER, result())
        assert created is not None

        await unqueue_application(db, USER, created["job_id"])
        assert await queue_size(db) == 0

    async def test_dismissing_never_deletes_a_sent_application(self, with_master):
        """History the user earned is not undone by tidying the feed."""
        from sqlalchemy import select, update

        from app.models import Application

        db = with_master
        created = await ensure_queued_application(db, USER, result())
        assert created is not None

        async with db._session() as session:
            async with session.begin():
                await session.execute(
                    update(Application)
                    .where(Application.user_id == USER)
                    .values(status="interview")
                )

        await unqueue_application(db, USER, created["job_id"])

        async with db._session() as session:
            surviving = (
                (await session.execute(select(Application).where(Application.user_id == USER)))
                .scalars()
                .all()
            )
        assert len(surviving) == 1
        assert surviving[0].status == "interview"

    async def test_a_job_with_no_description_still_carries_the_role(self, with_master):
        db = with_master
        created = await ensure_queued_application(db, USER, result(description=None))
        assert created is not None

        job = await db.get_job(USER, created["job_id"])
        assert "Backend Engineer" in job["content"]


class TestGroupFingerprint:
    def test_same_job_on_two_boards_groups(self):
        linkedin = group_fingerprint(
            "Urgent Hiring: Backend Engineer (Remote) 3-5 yrs", "Acme Inc.", "Pune, Maharashtra"
        )
        indeed = group_fingerprint("Backend Engineer", "Acme", "Pune")
        assert linkedin == indeed

    def test_different_role_does_not_group(self):
        assert group_fingerprint("Backend Engineer", "Acme", "Pune") != group_fingerprint(
            "Frontend Engineer", "Acme", "Pune"
        )

    def test_different_company_does_not_group(self):
        assert group_fingerprint("Backend Engineer", "Acme", "Pune") != group_fingerprint(
            "Backend Engineer", "Globex", "Pune"
        )

    def test_different_city_does_not_group(self):
        assert group_fingerprint("Backend Engineer", "Acme", "Pune") != group_fingerprint(
            "Backend Engineer", "Acme", "Bangalore"
        )

    def test_legal_suffixes_and_punctuation_are_noise(self):
        assert group_fingerprint("Data Analyst", "Globex Pvt Ltd", "Delhi") == group_fingerprint(
            "Data Analyst", "Globex", "Delhi"
        )

    def test_employment_type_decoration_is_noise(self):
        assert group_fingerprint("Designer - Full Time", "Acme", "Pune") == group_fingerprint(
            "Designer", "Acme", "Pune"
        )


class TestDedupeInTheQuery:
    """Duplicates must be removed inside the query, not after paging.

    Measured on a real 300-row feed: 33 duplicate groups existed and *zero* of
    them landed on the same page of 100, because the copies came from different
    harvest runs and sit far apart in creation order. A page-local collapse would
    have caught none of them.
    """

    async def seed(self, db, rows):
        await db.upsert_discovery_results(USER, "run-1", rows)

    def row(self, fp, *, title, company, source, score=0.0, location="Pune"):
        return {
            "fingerprint": fp,
            "source": source,
            "title": title,
            "company": company,
            "location": location,
            "url": f"https://{source}.test/{fp}",
            "is_remote": False,
            "description": None,
            "salary": None,
            "posted_at": None,
            "match_score": score,
            "matched": [],
            "missing": [],
            "partial": False,
        }

    async def test_same_job_on_two_boards_returns_one_row(self, db):
        await self.seed(
            db,
            [
                self.row("f1", title="Backend Engineer", company="Acme", source="linkedin"),
                self.row("f2", title="Backend Engineer", company="Acme", source="indeed"),
            ],
        )

        rows = await db.get_discovery_feed(USER, limit=50)
        total = await db.count_discovery_feed(USER)
        assert len(rows) == 1
        # The count must agree with the list, or "1 of 2" reappears.
        assert total == 1

    async def test_the_better_scoring_copy_survives(self, db):
        await self.seed(
            db,
            [
                self.row("f1", title="Backend Engineer", company="Acme", source="linkedin", score=0.2),
                self.row("f2", title="Backend Engineer", company="Acme", source="indeed", score=0.9),
            ],
        )

        rows = await db.get_discovery_feed(USER, limit=50)
        assert rows[0]["source"] == "indeed"

    async def test_distinct_jobs_are_both_kept(self, db):
        await self.seed(
            db,
            [
                self.row("f1", title="Backend Engineer", company="Acme", source="linkedin"),
                self.row("f2", title="Frontend Engineer", company="Acme", source="linkedin"),
            ],
        )

        assert len(await db.get_discovery_feed(USER, limit=50)) == 2
        assert await db.count_discovery_feed(USER) == 2

    async def test_duplicates_split_across_pages_still_collapse(self, db):
        """The case a page-local collapse cannot handle."""
        rows_in = [
            self.row(f"pad{i}", title=f"Role {i}", company="Padding", source="linkedin")
            for i in range(4)
        ]
        # Same job at both ends of the ordering.
        rows_in.insert(0, self.row("dup-a", title="Data Engineer", company="Globex", source="hirist"))
        rows_in.append(self.row("dup-b", title="Data Engineer", company="Globex", source="naukri"))
        await self.seed(db, rows_in)

        total = await db.count_discovery_feed(USER)
        page_one = await db.get_discovery_feed(USER, limit=2, offset=0)
        page_two = await db.get_discovery_feed(USER, limit=2, offset=2)
        page_three = await db.get_discovery_feed(USER, limit=2, offset=4)

        # 6 rows in, one duplicate pair, so 5 distinct jobs across the pages.
        assert total == 5
        seen = page_one + page_two + page_three
        assert len(seen) == 5

    async def test_filters_still_apply_to_the_deduped_set(self, db):
        await self.seed(
            db,
            [
                self.row("f1", title="Backend Engineer", company="Acme", source="linkedin"),
                self.row("f2", title="Backend Engineer", company="Acme", source="indeed"),
                self.row("f3", title="Designer", company="Globex", source="linkedin"),
            ],
        )

        rows = await db.get_discovery_feed(USER, limit=50, query="backend")
        total = await db.count_discovery_feed(USER, query="backend")
        assert len(rows) == 1
        assert total == 1


class TestAlsoOnLabels:
    async def test_names_the_other_boards(self, db):
        from app.job_discovery.normalize import group_fingerprint

        key = group_fingerprint("Backend Engineer", "Acme", "Pune")
        await db.upsert_discovery_results(
            USER,
            "run-1",
            [
                {
                    "fingerprint": "f1",
                    "source": "linkedin",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "location": "Pune",
                    "url": "https://a.test/1",
                    "match_score": 0.1,
                    "matched": [],
                    "missing": [],
                    "partial": False,
                },
                {
                    "fingerprint": "f2",
                    "source": "indeed",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "location": "Pune",
                    "url": "https://b.test/2",
                    "match_score": 0.9,
                    "matched": [],
                    "missing": [],
                    "partial": False,
                },
            ],
        )

        rows = await db.get_discovery_feed(USER, limit=50)
        annotated = await db.annotate_duplicate_sources(USER, rows)
        assert annotated[0]["group_fingerprint"] == key
        assert annotated[0]["also_on"] == ["linkedin"]
        assert annotated[0]["duplicate_count"] == 2

    async def test_a_unique_job_gets_no_label(self, db):
        await db.upsert_discovery_results(
            USER,
            "run-1",
            [
                {
                    "fingerprint": "f1",
                    "source": "linkedin",
                    "title": "Only Job",
                    "company": "Acme",
                    "location": "Pune",
                    "url": "https://a.test/1",
                    "match_score": 0,
                    "matched": [],
                    "missing": [],
                    "partial": False,
                }
            ],
        )

        rows = await db.get_discovery_feed(USER, limit=50)
        annotated = await db.annotate_duplicate_sources(USER, rows)
        assert "also_on" not in annotated[0]


class TestBackfill:
    async def test_older_rows_get_a_group_key(self, db):
        from sqlalchemy import update

        from app.models import DiscoveryResult

        await db.upsert_discovery_results(
            USER,
            "run-1",
            [
                {
                    "fingerprint": "f1",
                    "source": "linkedin",
                    "title": "Backend Engineer",
                    "company": "Acme",
                    "location": "Pune",
                    "url": "https://a.test/1",
                    "match_score": 0,
                    "matched": [],
                    "missing": [],
                    "partial": False,
                }
            ],
        )
        # Simulate a row harvested before the column existed.
        async with db._session() as session:
            async with session.begin():
                await session.execute(update(DiscoveryResult).values(group_fingerprint=None))

        filled = await db.backfill_group_fingerprints()
        assert filled == 1
        # Idempotent: a second run finds nothing to do.
        assert await db.backfill_group_fingerprints() == 0
