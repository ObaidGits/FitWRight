"""Retention, board health, and the unscored count.

Three pieces of the same problem: a feature that runs in the background has to
tell the user what it did, forget what stopped mattering, and admit when a board
has broken. Otherwise it degrades silently and they blame themselves.

The judgements pinned here:

* retention never deletes a decision - only jobs never looked at or explicitly
  dismissed;
* a week is the floor, because "older than a day" is nobody's real intent;
* being rate-limited by our own pacing is not a board failure, so it must not be
  counted as one;
* a board that has worked before and is failing now is a different problem from
  one that never worked.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.job_discovery import board_health, retention

USER: str = ""


@pytest.fixture
async def db(isolated_db, owner_id):
    global USER
    USER = owner_id
    return isolated_db


def row(fp, *, status="new", title="Some Job", company="Acme"):
    return {
        "fingerprint": fp,
        "source": "linkedin",
        "title": title,
        "company": company,
        "location": "Pune",
        "url": f"https://example.test/{fp}",
        "match_score": 0,
        "matched": [],
        "missing": [],
        "partial": False,
    }


async def age_rows(db, days):
    """Backdate every feed row so retention sees them as old."""
    from sqlalchemy import update

    from app.models import DiscoveryResult

    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with db._session() as session:
        async with session.begin():
            await session.execute(update(DiscoveryResult).values(created_at=old))


class TestRetention:
    async def test_old_untouched_rows_are_removed(self, db):
        await db.upsert_discovery_results(USER, "run", [row("a"), row("b", title="Other")])
        await age_rows(db, 40)

        removed = await retention.sweep_feed(db, USER, days=30)
        assert removed == 2
        assert await db.count_discovery_feed(USER) == 0

    async def test_recent_rows_are_kept(self, db):
        await db.upsert_discovery_results(USER, "run", [row("a")])

        assert await retention.sweep_feed(db, USER, days=30) == 0
        assert await db.count_discovery_feed(USER) == 1

    async def test_decisions_are_never_swept(self, db):
        """Interested, tailored and applied are decisions, not disk to reclaim."""
        from sqlalchemy import update

        from app.models import DiscoveryResult

        await db.upsert_discovery_results(
            USER,
            "run",
            [row("a"), row("b", title="B"), row("c", title="C"), row("d", title="D")],
        )
        await age_rows(db, 90)
        async with db._session() as session:
            async with session.begin():
                for fp, status in (("a", "interested"), ("b", "tailored"), ("c", "applied")):
                    await session.execute(
                        update(DiscoveryResult)
                        .where(DiscoveryResult.fingerprint == fp)
                        .values(status=status)
                    )

        removed = await retention.sweep_feed(db, USER, days=30)
        # Only "d", which was left at `new`.
        assert removed == 1
        assert await db.count_discovery_feed(USER) == 3

    async def test_dismissed_rows_are_swept(self, db):
        from sqlalchemy import update

        from app.models import DiscoveryResult

        await db.upsert_discovery_results(USER, "run", [row("a")])
        await age_rows(db, 60)
        async with db._session() as session:
            async with session.begin():
                await session.execute(update(DiscoveryResult).values(status="dismissed"))

        assert await retention.sweep_feed(db, USER, days=30) == 1

    async def test_a_week_is_the_floor(self, db):
        """"Delete everything older than a day" is not a real intent."""
        await db.upsert_discovery_results(USER, "run", [row("a")])
        await age_rows(db, 3)

        # Asking for 1 day is clamped to the 7-day minimum, so a 3-day-old row survives.
        assert await retention.sweep_feed(db, USER, days=1) == 0
        assert await db.count_discovery_feed(USER) == 1

    async def test_sweeping_all_users_covers_this_one(self, db):
        await db.upsert_discovery_results(USER, "run", [row("a")])
        await age_rows(db, 40)

        assert await retention.sweep_all_users(db, days=30) == 1


class TestBoardHealth:
    async def test_a_successful_run_is_recorded(self, db):
        await board_health.record_outcome(db, USER, board="hirist", status="ok", found=20)

        boards = await board_health.list_health(db, USER)
        assert len(boards) == 1
        assert boards[0]["board"] == "hirist"
        assert boards[0]["last_found"] == 20
        assert boards[0]["consecutive_failures"] == 0
        assert boards[0]["needs_attention"] is False
        assert boards[0]["worked_before"] is True

    async def test_one_empty_run_is_not_an_alarm(self, db):
        """A single empty search is a normal search."""
        await board_health.record_outcome(db, USER, board="hirist", status="empty")

        boards = await board_health.list_health(db, USER)
        assert boards[0]["consecutive_failures"] == 1
        assert boards[0]["needs_attention"] is False

    async def test_three_failures_in_a_row_needs_attention(self, db):
        for _ in range(board_health.FAILURE_THRESHOLD):
            await board_health.record_outcome(db, USER, board="hirist", status="empty")

        flagged = await board_health.boards_needing_attention(db, USER)
        assert [b["board"] for b in flagged] == ["hirist"]

    async def test_a_success_resets_the_counter(self, db):
        for _ in range(5):
            await board_health.record_outcome(db, USER, board="hirist", status="error")
        await board_health.record_outcome(db, USER, board="hirist", status="ok", found=3)

        boards = await board_health.list_health(db, USER)
        assert boards[0]["consecutive_failures"] == 0
        assert boards[0]["needs_attention"] is False

    async def test_being_rate_limited_is_not_a_failure(self, db):
        """We chose not to run. Calling our own restraint a fault would tell the
        user a healthy board is broken."""
        for _ in range(5):
            await board_health.record_outcome(db, USER, board="naukri", status="capped")

        boards = await board_health.list_health(db, USER)
        assert boards[0]["consecutive_failures"] == 0
        assert boards[0]["needs_attention"] is False

    async def test_a_board_that_never_worked_is_distinguishable(self, db):
        await board_health.record_outcome(db, USER, board="glassdoor", status="error")

        boards = await board_health.list_health(db, USER)
        assert boards[0]["worked_before"] is False

    async def test_signed_out_is_recorded_as_such(self, db):
        await board_health.record_outcome(db, USER, board="naukri", status="signed_out")

        boards = await board_health.list_health(db, USER)
        assert boards[0]["last_status"] == "signed_out"

    async def test_a_whole_run_is_recorded_from_the_extension_report(self, db):
        recorded = await board_health.record_run(
            db,
            USER,
            [
                {"source": "hirist", "found": 20, "saved": 5},
                {"source": "naukri", "found": 0, "reason": "signed-out", "error": "Signed out"},
                {"source": "glassdoor", "found": 0, "reason": "capped"},
                {"source": "google", "found": 0, "error": "Could not open tab"},
            ],
        )
        assert recorded == 4

        by_board = {b["board"]: b for b in await board_health.list_health(db, USER)}
        assert by_board["hirist"]["last_status"] == "ok"
        assert by_board["naukri"]["last_status"] == "signed_out"
        assert by_board["glassdoor"]["last_status"] == "capped"
        assert by_board["google"]["last_status"] == "error"

    async def test_worst_boards_come_first(self, db):
        await board_health.record_outcome(db, USER, board="good", status="ok", found=5)
        for _ in range(4):
            await board_health.record_outcome(db, USER, board="bad", status="empty")

        boards = await board_health.list_health(db, USER)
        assert boards[0]["board"] == "bad"

    async def test_nameless_boards_are_skipped(self, db):
        assert await board_health.record_run(db, USER, [{"source": "", "found": 0}]) == 0


class TestUnscoredCount:
    async def test_counts_rows_without_a_score(self, db):
        from app.job_discovery.scoring import count_unscored

        await db.upsert_discovery_results(
            USER,
            "run",
            [
                row("a"),
                {**row("b", title="Scored"), "match_score": 80},
            ],
        )

        assert await count_unscored(db, USER) == 1

    async def test_an_empty_feed_has_nothing_unscored(self, db):
        from app.job_discovery.scoring import count_unscored

        assert await count_unscored(db, USER) == 0
