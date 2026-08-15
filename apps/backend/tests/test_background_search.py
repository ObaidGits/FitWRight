"""Background job search: the request must survive Heroku's 30-second ceiling.

The bug these cover: a manual search takes 15-35 seconds, Heroku destroys any
request open at 30, so the user saw a timeout for a search that had actually
populated their feed. The fix detaches the work from the request.
"""

from __future__ import annotations

import asyncio

import pytest

from app.job_discovery import search_jobs

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_registry():
    search_jobs.reset_for_tests()
    yield
    search_jobs.reset_for_tests()


class TestJobLifecycle:
    async def test_start_returns_before_the_work_finishes(self):
        """The whole point: the caller is not made to wait for the scrape."""
        gate = asyncio.Event()

        async def slow(job):
            await gate.wait()
            job.saved = 7

        job = search_jobs.start("u1", "engineer", ["indeed"], slow)
        # Still running, and we already have the handle back.
        assert job.status == "running"
        assert search_jobs.get("u1", job.job_id)["status"] == "running"

        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        done = search_jobs.get("u1", job.job_id)
        assert done["status"] == "done"
        assert done["saved"] == 7

    async def test_failure_is_reported_without_leaking_exception_text(self):
        """Connector errors can carry scraped page fragments; the UI renders this."""

        async def boom(job):
            raise RuntimeError("secret-looking scraped fragment")

        job = search_jobs.start("u1", "q", ["indeed"], boom)
        for _ in range(5):
            await asyncio.sleep(0)

        state = search_jobs.get("u1", job.job_id)
        assert state["status"] == "failed"
        assert "RuntimeError" in state["error"]
        assert "secret-looking" not in state["error"]

    async def test_progress_counts_boards_as_they_finish(self):
        async def work(job):
            job.site_finished("indeed", found=12)
            job.site_finished("linkedin", found=8)

        job = search_jobs.start("u1", "q", ["indeed", "linkedin", "glassdoor"], work)
        for _ in range(3):
            await asyncio.sleep(0)

        state = search_jobs.get("u1", job.job_id)
        assert state["sites_total"] == 3
        assert state["sites_done"] == 2
        assert state["found"] == 20


class TestOwnershipAndExpiry:
    async def test_another_user_cannot_read_someone_elses_search(self):
        async def work(job):
            job.saved = 99

        job = search_jobs.start("owner", "q", ["indeed"], work)
        for _ in range(3):
            await asyncio.sleep(0)

        assert search_jobs.get("owner", job.job_id)["saved"] == 99
        # A guessed id must reveal nothing at all.
        leaked = search_jobs.get("someone-else", job.job_id)
        assert leaked["status"] == "expired"
        assert leaked["saved"] == 0

    async def test_unknown_id_is_expired_not_an_error(self):
        """After a restart the job is genuinely gone; the UI should reload the feed."""
        state = search_jobs.get("u1", "never-existed")
        assert state["status"] == "expired"


class TestSingleFlight:
    async def test_only_one_search_runs_per_user(self):
        gate = asyncio.Event()

        async def slow(job):
            await gate.wait()

        first = search_jobs.start("u1", "q", ["indeed"], slow)
        assert search_jobs.running_for("u1") is not None
        assert search_jobs.running_for("u1").job_id == first.job_id

        # A different user is unaffected - the limit is per person, not global.
        assert search_jobs.running_for("u2") is None

        gate.set()
        for _ in range(3):
            await asyncio.sleep(0)
        assert search_jobs.running_for("u1") is None

    async def test_stuck_job_is_eventually_abandoned(self, monkeypatch):
        """A wedged connector must not pin a search as running forever."""
        gate = asyncio.Event()

        async def never(job):
            await gate.wait()

        job = search_jobs.start("u1", "q", ["indeed"], never)
        monkeypatch.setattr(search_jobs, "_MAX_RUNTIME_SECONDS", -1)

        state = search_jobs.get("u1", job.job_id)
        assert state["status"] == "failed"
        assert "too long" in state["error"]

        gate.set()
        for _ in range(3):
            await asyncio.sleep(0)
