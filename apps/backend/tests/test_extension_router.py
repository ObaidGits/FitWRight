"""Integration tests for the browser-extension router.

Three properties are worth pinning down, because each is a decision the
extension depends on rather than an implementation detail:

1. **The kill-switch hides the surface.** With ``JOB_DISCOVERY`` off every
   ``/extension/*`` route returns 404, not 403. A disabled deployment must not
   reveal that these endpoints exist. ``/ping`` is the load-bearing case: it
   touches no service with its own gate, so if the router-level dependency were
   dropped it would happily answer 200 and this test would fail.

2. **The handshake reports a version.** The extension compares
   ``api_version`` against its own constant and warns when they differ, so an
   old build fails loudly at connect time rather than confusingly halfway
   through an autofill.

3. **Absence is not an error.** ``/profile`` on an account with no resume
   returns an empty profile, and ``/applied`` for a job never in the feed
   returns ``updated: false``. Both are legitimate states, so both are 200.

Collaborators are swapped via ``app.dependency_overrides`` - nothing here
reaches an LLM, a browser, or a live scraper.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Database
from app.main import app
from app.routers.extension import EXTENSION_API_VERSION, get_db, get_settings_dep

pytestmark = pytest.mark.integration

# Every route on the surface, with a body valid enough to reach the gate.
ALL_ROUTES: list[tuple[str, str, dict | None]] = [
    ("GET", "/api/v1/extension/ping", None),
    ("GET", "/api/v1/extension/profile", None),
    (
        "POST",
        "/api/v1/extension/capture",
        {
            "title": "Backend Engineer",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/jobs/1",
            "source": "extension",
        },
    ),
    ("POST", "/api/v1/extension/scrape", {"source": "indeed", "jobs": []}),
    ("POST", "/api/v1/extension/match", {"description": "Python work", "title": "Dev"}),
    (
        "POST",
        "/api/v1/extension/draft",
        {"question": "Why us?", "description": "d", "company": "Acme", "title": "Dev"},
    ),
    ("POST", "/api/v1/extension/applied", {"url": "https://example.com/jobs/1"}),
]


def _config(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        JOB_DISCOVERY=enabled,
        JOB_DISCOVERY_MAX_RESULTS=50,
        JOB_DISCOVERY_CACHE_TTL_SECONDS=3600,
        JOB_DISCOVERY_MAX_RECIPES=20,
        job_discovery_jobspy_sites=["indeed"],
    )


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    database = Database.from_url(f"sqlite+aiosqlite:///{tmp_path / 'extension.db'}")
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture
async def client(db):
    app.dependency_overrides[get_db] = lambda: db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_settings_dep, None)


def _enable(enabled: bool) -> None:
    app.dependency_overrides[get_settings_dep] = lambda: _config(enabled=enabled)


# --------------------------------------------------------------------------- #
# Kill-switch OFF -> the whole surface is invisible
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("method", "path", "body"),
    ALL_ROUTES,
    ids=[f"{m}-{p.rsplit('/', 1)[-1]}" for m, p, _ in ALL_ROUTES],
)
async def test_every_route_404s_when_disabled(client, method, path, body):
    _enable(False)
    resp = await client.request(method, path, json=body)
    assert resp.status_code == 404, (
        f"{method} {path} returned {resp.status_code} with JOB_DISCOVERY off; "
        "the router-level kill-switch dependency is missing or bypassed."
    )


# --------------------------------------------------------------------------- #
# Kill-switch ON -> the gate passes through
# --------------------------------------------------------------------------- #
async def test_ping_reports_the_api_version(client):
    _enable(True)
    resp = await client.get("/api/v1/extension/ping")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ok"] is True
    # The extension refuses to operate against a mismatched version, so this
    # constant is part of the contract, not a detail.
    assert body["api_version"] == EXTENSION_API_VERSION
    assert body["has_resume"] is False
    assert body["resume_count"] == 0


async def test_profile_is_empty_not_404_without_a_resume(client):
    """A fresh account is not an error - the extension shows an upload prompt."""
    _enable(True)
    resp = await client.get("/api/v1/extension/profile")
    assert resp.status_code == 200

    body = resp.json()
    assert body["full_name"] == ""
    assert body["email"] == ""
    assert body["resume_id"] is None
    # Nothing to attach to a file input, so autofill must skip the upload.
    assert body["resume_pdf_path"] is None
    assert body["years_experience"] is None


async def test_applied_reports_no_update_for_an_unknown_job(client):
    """The user may apply to something that was never in their feed."""
    _enable(True)
    resp = await client.post(
        "/api/v1/extension/applied",
        json={"url": "https://example.com/never-seen"},
    )
    assert resp.status_code == 200
    assert resp.json()["updated"] is False


async def test_capture_then_recapture_is_flagged_duplicate(client):
    """Fingerprint dedup is inherited from the discovery feed, not reimplemented."""
    _enable(True)
    job = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Bengaluru, IN",
        "url": "https://example.com/jobs/42",
        "source": "extension",
        "description": "Build and operate services.",
    }

    first = await client.post("/api/v1/extension/capture", json=job)
    assert first.status_code == 200
    assert first.json()["duplicate"] is False
    fingerprint = first.json()["fingerprint"]
    assert fingerprint

    second = await client.post("/api/v1/extension/capture", json=job)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    # Same job must hash the same, or the feed would accumulate copies.
    assert second.json()["fingerprint"] == fingerprint


async def test_scrape_accepts_an_empty_batch(client):
    """A search page that yielded nothing is a normal outcome, not a failure."""
    _enable(True)
    resp = await client.post(
        "/api/v1/extension/scrape",
        json={"source": "indeed", "jobs": []},
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body["received"] == 0
    assert body["saved"] == 0
    assert body["source"] == "indeed"


async def test_scrape_rejects_an_oversized_batch(client):
    """The 200-job cap keeps one runaway page from flooding the feed."""
    _enable(True)
    jobs = [
        {
            "title": f"Engineer {i}",
            "company": "Acme",
            "location": "Remote",
            "url": f"https://example.com/jobs/{i}",
            "source": "indeed",
        }
        for i in range(201)
    ]
    resp = await client.post(
        "/api/v1/extension/scrape",
        json={"source": "indeed", "jobs": jobs},
    )
    assert resp.status_code == 422


async def test_capture_requires_a_title(client):
    """A title-less capture means extraction failed; storing it is worse than 422."""
    _enable(True)
    resp = await client.post(
        "/api/v1/extension/capture",
        json={
            "title": "",
            "company": "Acme",
            "location": "Remote",
            "url": "https://example.com/jobs/9",
            "source": "extension",
        },
    )
    assert resp.status_code == 422
