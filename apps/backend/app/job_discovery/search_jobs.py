"""In-flight tracking for background job searches.

Why this exists: a manual search across job boards takes ~15-35 seconds, and
Heroku's router kills any HTTP request at 30. The request was therefore being
destroyed mid-flight while the work carried on and quietly succeeded - the user
saw a timeout error for a search that had actually populated their feed.

So the search now runs detached from the request that started it, and this module
is how the UI finds out what happened. ``POST /discovery/search/start`` registers
a job and returns immediately; the page polls ``GET /discovery/search/progress``
until the job reports ``done``, then refreshes the feed.

State is in-process on purpose. The backend runs a single uvicorn worker
(``BACKEND_WORKERS=1``), so every poll reaches the process holding the job, and
keeping it out of Postgres avoids a schema migration - which on this deployment is
a manual production step, not something that happens on deploy. The cost is that a
restart forgets in-flight jobs; ``get`` reports that honestly as ``expired`` so the
UI can refresh the feed and stop polling rather than spinning forever.

Ownership is enforced on read: a job is only ever returned to the user who started
it, so a guessed id leaks nothing.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

logger = logging.getLogger(__name__)

Status = Literal["running", "done", "failed", "expired"]

# A finished job is kept only long enough for the page to collect its result; the
# feed is the durable record, so there is nothing to preserve here.
_RETAIN_SECONDS = 600
# Hard ceiling so a wedged connector cannot pin a job as "running" forever.
_MAX_RUNTIME_SECONDS = 300


@dataclass
class SearchJob:
    """One background search, and everything the UI needs to narrate it."""

    job_id: str
    user_id: str
    query: str
    sites: list[str]
    status: Status = "running"
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # Per-board progress, so the UI can say which board it is on rather than
    # showing an unqualified spinner for half a minute.
    done_sites: list[str] = field(default_factory=list)
    found: int = 0
    saved: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    @property
    def elapsed_ms(self) -> int:
        end = self.finished_at if self.finished_at is not None else time.time()
        return int((end - self.started_at) * 1000)

    def to_dict(self) -> dict[str, Any]:
        return {
            "search_id": self.job_id,
            "status": self.status,
            "query": self.query,
            "sites": self.sites,
            "done_sites": list(self.done_sites),
            # Progress is reported as a fraction of boards finished rather than a
            # fake percentage: it is the only honest signal available mid-scrape.
            "sites_total": len(self.sites),
            "sites_done": len(self.done_sites),
            "found": self.found,
            "saved": self.saved,
            "failures": list(self.failures),
            "error": self.error,
            "elapsed_ms": self.elapsed_ms,
        }

    # -- progress hooks used by the search executor -------------------------- #

    def site_finished(self, site: str, found: int = 0) -> None:
        if site not in self.done_sites:
            self.done_sites.append(site)
        self.found += max(0, found)

    def note_failure(self, source: str, reason: str) -> None:
        self.failures.append({"source": source, "reason": reason})


_jobs: dict[str, SearchJob] = {}


def _prune() -> None:
    """Drop finished jobs past their retention, and time out stuck ones."""
    now = time.time()
    for job_id, job in list(_jobs.items()):
        if job.status == "running" and now - job.started_at > _MAX_RUNTIME_SECONDS:
            job.status = "failed"
            job.error = "The search took too long and was abandoned."
            job.finished_at = now
            logger.warning("Search job %s exceeded %ds", job_id, _MAX_RUNTIME_SECONDS)
        elif job.finished_at and now - job.finished_at > _RETAIN_SECONDS:
            _jobs.pop(job_id, None)


def running_for(user_id: str) -> SearchJob | None:
    """The user's in-flight search, if any.

    One search at a time per user: a second one would compete with the first for
    the same rate-limited scrapers and make both slower and more likely to trip a
    board's blocking.
    """
    _prune()
    for job in _jobs.values():
        if job.user_id == user_id and job.status == "running":
            return job
    return None


def start(
    user_id: str,
    query: str,
    sites: list[str],
    work: Callable[[SearchJob], Awaitable[Any]],
) -> SearchJob:
    """Register a job and run ``work`` detached from the calling request."""
    _prune()
    job = SearchJob(
        job_id=uuid.uuid4().hex, user_id=user_id, query=query, sites=list(sites)
    )
    _jobs[job.job_id] = job

    async def _runner() -> None:
        try:
            await work(job)
            if job.status == "running":
                job.status = "done"
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "The search was interrupted."
            raise
        except Exception as exc:  # noqa: BLE001
            job.status = "failed"
            # The class name alone: connector exceptions can carry scraped page
            # fragments, and this string is rendered in the browser.
            job.error = f"The search failed ({type(exc).__name__})."
            logger.exception("Background search %s failed", job.job_id)
        finally:
            if job.finished_at is None:
                job.finished_at = time.time()

    # Held so the loop cannot garbage-collect a running task mid-scrape.
    task = asyncio.create_task(_runner(), name=f"discovery-search-{job.job_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


_tasks: set[asyncio.Task] = set()


def get(user_id: str, job_id: str) -> dict[str, Any]:
    """Look up a job for its owner.

    An unknown id is reported as ``expired`` rather than 404: after a dyno restart
    the job is genuinely gone, and the useful instruction for the UI is "stop
    polling and reload the feed", which is exactly what ``expired`` means.
    """
    _prune()
    job = _jobs.get(job_id)
    if job is None or job.user_id != user_id:
        return {
            "search_id": job_id,
            "status": "expired",
            "found": 0,
            "saved": 0,
            "sites": [],
            "done_sites": [],
            "sites_total": 0,
            "sites_done": 0,
            "failures": [],
            "error": None,
            "elapsed_ms": 0,
        }
    return job.to_dict()


def reset_for_tests() -> None:
    _jobs.clear()
    _tasks.clear()
