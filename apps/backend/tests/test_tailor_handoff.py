"""Unit tests for the discovery → tailor handoff (task 0.17, Req 8.1–8.3).

Both branches are exercised with fakes so no test touches a live DB, the
network, or the ``app/jd`` fetch stack:

* **full-listing branch** — the listing already carries its JD body, so the
  handoff persists it directly and never fetches (Req 8.1, 8.3).
* **partial branch** — the listing has no description, so the full JD is fetched
  via the injected ``fetch_jd`` (standing in for the existing ``app/jd`` URL
  path) *before* the job is created (Req 8.2).

Plus the contract edges: the SSRF guard runs before a partial fetch, a partial
listing with no URL is rejected, and the default creator binds ``db.create_job``.
"""

from __future__ import annotations

import pytest

from app.job_discovery.models import JobListing
from app.job_discovery.tailor import (
    JobDraft,
    PartialJDFetchError,
    TailorError,
    TailorHandoff,
    TailorService,
    is_partial_listing,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Fakes / helpers
# --------------------------------------------------------------------------- #
def _listing(*, description: str | None, url: str = "https://jobs.example/1") -> JobListing:
    return JobListing(
        source="indeed",
        title="Senior Backend Engineer",
        company="Acme",
        location="Remote",
        url=url,
        is_remote=True,
        description=description,
        salary="$180k",
        fingerprint="fp1",
    )


class _RecordingCreator:
    """A fake ``create_job``: records the draft, returns a scripted job id."""

    def __init__(self, job_id: str = "job-123") -> None:
        self.job_id = job_id
        self.calls: list[tuple[str, JobDraft]] = []

    async def __call__(self, user_id: str, draft: JobDraft) -> str:
        self.calls.append((user_id, draft))
        return self.job_id


class _RecordingFetch:
    """A fake JD fetcher standing in for the existing app/jd URL path."""

    def __init__(self, text: str = "Full JD body with the real requirements.") -> None:
        self.text = text
        self.urls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.urls.append(url)
        return self.text


# --------------------------------------------------------------------------- #
# is_partial_listing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("Real JD", False),
    ],
)
def test_is_partial_listing(description, expected):
    assert is_partial_listing(_listing(description=description)) is expected


# --------------------------------------------------------------------------- #
# Full-listing branch (Req 8.1, 8.3)
# --------------------------------------------------------------------------- #
async def test_full_listing_persists_directly_without_fetch():
    creator = _RecordingCreator(job_id="job-full")
    fetch = _RecordingFetch()
    svc = TailorService(create_job=creator, fetch_jd=fetch)

    listing = _listing(description="A complete job description with duties.")
    result = await svc.tailor(user_id="u1", resume_id="r1", listing=listing)

    assert result == TailorHandoff(job_id="job-full", resume_id="r1")
    # Full listing: the jd/ URL path is never touched.
    assert fetch.urls == []
    # The persisted draft reuses the listing's own description verbatim.
    assert len(creator.calls) == 1
    user_id, draft = creator.calls[0]
    assert user_id == "u1"
    assert draft.description == "A complete job description with duties."
    assert draft.title == "Senior Backend Engineer"
    assert draft.company == "Acme"
    assert draft.url == "https://jobs.example/1"
    assert draft.source == "indeed"


# --------------------------------------------------------------------------- #
# Partial branch (Req 8.2)
# --------------------------------------------------------------------------- #
async def test_partial_listing_fetches_full_jd_before_creating_job():
    creator = _RecordingCreator(job_id="job-partial")
    fetch = _RecordingFetch(text="Fetched full JD: Python, FastAPI, 5+ years.")
    validated: list[str] = []
    svc = TailorService(
        create_job=creator,
        fetch_jd=fetch,
        validate_url=lambda url: validated.append(url),
    )

    listing = _listing(description=None, url="https://jobs.example/42")
    result = await svc.tailor(user_id="u1", resume_id="r1", listing=listing)

    assert result == TailorHandoff(job_id="job-partial", resume_id="r1")
    # The full JD was fetched from the listing URL...
    assert fetch.urls == ["https://jobs.example/42"]
    # ...through the SSRF guard...
    assert validated == ["https://jobs.example/42"]
    # ...and the fetched body is what gets persisted (not the empty original).
    assert len(creator.calls) == 1
    _, draft = creator.calls[0]
    assert draft.description == "Fetched full JD: Python, FastAPI, 5+ years."


async def test_partial_fetch_runs_before_job_creation_order():
    """The JD must be fetched BEFORE create_job so the job carries the body."""
    events: list[str] = []

    async def _fetch(url: str) -> str:
        events.append("fetch")
        return "fetched jd"

    async def _create(user_id: str, draft: JobDraft) -> str:
        events.append("create")
        assert draft.description == "fetched jd"  # body present at create time
        return "job-x"

    svc = TailorService(create_job=_create, fetch_jd=_fetch, validate_url=lambda u: None)
    await svc.tailor(user_id="u1", resume_id="r1", listing=_listing(description=""))

    assert events == ["fetch", "create"]


# --------------------------------------------------------------------------- #
# Contract edges
# --------------------------------------------------------------------------- #
async def test_missing_resume_id_is_rejected():
    svc = TailorService(create_job=_RecordingCreator(), fetch_jd=_RecordingFetch())
    with pytest.raises(TailorError):
        await svc.tailor(user_id="u1", resume_id="", listing=_listing(description="jd"))


async def test_partial_listing_without_url_is_rejected():
    creator = _RecordingCreator()
    svc = TailorService(create_job=creator, fetch_jd=_RecordingFetch(), validate_url=lambda u: None)
    with pytest.raises(TailorError):
        await svc.tailor(
            user_id="u1", resume_id="r1", listing=_listing(description=None, url="")
        )
    assert creator.calls == []  # never persisted


async def test_ssrf_rejection_blocks_fetch_and_creation():
    creator = _RecordingCreator()
    fetch = _RecordingFetch()

    def _reject(url: str) -> None:
        raise ValueError("blocked: loopback address")

    svc = TailorService(create_job=creator, fetch_jd=fetch, validate_url=_reject)
    with pytest.raises(PartialJDFetchError):
        await svc.tailor(user_id="u1", resume_id="r1", listing=_listing(description=None))

    assert fetch.urls == []  # fetch never attempted
    assert creator.calls == []  # job never created


async def test_fetch_failure_surfaces_as_partial_error():
    async def _boom(url: str) -> str:
        raise RuntimeError("upstream 503")

    svc = TailorService(
        create_job=_RecordingCreator(), fetch_jd=_boom, validate_url=lambda u: None
    )
    with pytest.raises(PartialJDFetchError):
        await svc.tailor(user_id="u1", resume_id="r1", listing=_listing(description=None))


async def test_empty_fetched_jd_is_rejected():
    svc = TailorService(
        create_job=_RecordingCreator(),
        fetch_jd=_RecordingFetch(text="   "),
        validate_url=lambda u: None,
    )
    with pytest.raises(PartialJDFetchError):
        await svc.tailor(user_id="u1", resume_id="r1", listing=_listing(description=None))


# --------------------------------------------------------------------------- #
# Default creator binds db.create_job (Req 8.3)
# --------------------------------------------------------------------------- #
async def test_default_creator_binds_db_create_job():
    class _FakeDB:
        def __init__(self) -> None:
            self.kwargs: dict = {}

        async def create_job(self, **kwargs) -> str:
            self.kwargs = kwargs
            return "job-from-db"

    db = _FakeDB()
    svc = TailorService(db, fetch_jd=_RecordingFetch())  # no create_job -> binds db.create_job

    result = await svc.tailor(
        user_id="u9", resume_id="r9", listing=_listing(description="jd body")
    )

    assert result.job_id == "job-from-db"
    assert result.resume_id == "r9"
    assert db.kwargs["user_id"] == "u9"
    assert db.kwargs["description"] == "jd body"
    assert db.kwargs["title"] == "Senior Backend Engineer"


async def test_default_creator_without_create_job_raises():
    class _NoJobsDB:
        pass

    svc = TailorService(_NoJobsDB(), fetch_jd=_RecordingFetch())
    with pytest.raises(TailorError):
        await svc.tailor(user_id="u1", resume_id="r1", listing=_listing(description="jd"))
