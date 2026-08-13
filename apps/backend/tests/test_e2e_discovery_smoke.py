"""End-to-end smoke for Job Discovery & Recommendations (task 0.23).

Drives the **whole stack through the HTTP router** — kill-switch gate ->
ownership -> query -> connector fan-out -> normalize/dedup -> rank -> cache ->
wire response — with the ``JOB_DISCOVERY`` kill-switch **on**, against a
*recorded HTML fixture* (``tests/fixtures/acme_careers_search.html``). Nothing
here touches a live LLM, browser, or network: the site-recipe connector is fed
the fixture through an injected ``fetch_fn`` + regex ``extractor`` (the same
seam the unit tests use), ranking runs on injected deterministic collaborators,
and query generation is a fixed non-degraded ``SearchQuery``.

What it proves (Req 1, 3.2, 2.2, 11):

* **Happy path (Req 1, 7):** a recommend call returns ranked recommendations
  built from the fixture — the full listing scores above the description-less
  one, which is flagged ``partial`` (Req 7.2).
* **Clean run is cacheable (Req 6):** a non-degraded result is stored, so the
  cheap ``GET /recommend/{id}`` path returns it with ``cached=True`` and no
  second fan-out.
* **Partial-source reporting (Req 1.2, 3.2):** with one source failing
  alongside the fixture source, the request still succeeds with the fixture's
  listings, the failing source is reported in ``failures``, and ``degraded`` is
  True. A single source failing never fails the whole request.
* **Degraded via query fallback (Req 2.2):** even with a healthy source, a
  synthesized (LLM-less) query sets ``degraded`` — the other trigger of the
  partial-results banner.
* **Kill-switch (Req 10, 11.3):** with the feature off every route 404s — the
  surface is invisible, not merely disabled.

This is the integration counterpart to the per-stage unit tests: it is the one
test that exercises the real router + service + normalize + rank + DB cache
wired together end to end.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from alembic import command
from app.database import Database
from app.job_discovery.connectors.site_recipe import SiteRecipeConnector
from app.job_discovery.fetch import FetchResult
from app.job_discovery.models import (
    FetchMode,
    SearchQuery,
    SiteRecipe,
)
from app.job_discovery.service import DiscoveryService, ResumeData
from app.main import app
from app.routers.discovery import (
    get_db,
    get_discovery_service,
    get_settings_dep,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURE = BACKEND_DIR / "tests" / "fixtures" / "acme_careers_search.html"

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Deterministic fakes — no LLM / browser / network anywhere in this file.
# --------------------------------------------------------------------------- #
_CARD_RE = re.compile(r'<li class="job">(.*?)</li>', re.DOTALL)
_FIELD_RE = re.compile(r'<span data-field="([^"]+)">(.*?)</span>', re.DOTALL)

# Skills the fake keyword extractor recognises in a JD blob. The resume below
# carries Python/Go/APIs, so the full description scores 100 and the
# description-less "Platform Engineer" row (scored on its title snippet) scores 0.
_SKILLS = ["Python", "Go", "APIs", "Kubernetes", "Platform", "Search"]

_RESUME = ResumeData(
    resume_id="resume-1",
    text="Senior backend engineer. Python, Go, REST APIs, distributed systems.",
    processed={
        "skills": ["Python", "Go", "APIs"],
        "summary": "Backend engineer building scalable Python and Go services.",
    },
    version="v1",
)


async def _fixture_extractor(page_text: str, schema: dict, base_url: str) -> list[dict]:
    """Stand-in for Crawl4AI: pull ``data-field`` spans out of each job card."""
    records: list[dict] = []
    for card in _CARD_RE.findall(page_text):
        records.append(
            {name: value.strip() for name, value in _FIELD_RE.findall(card)}
        )
    return records


def _fixture_fetch(html: str):
    async def _fetch(url, *, fetch_mode="http", timeout=None, max_bytes=None, **_):
        return FetchResult(url=url, status=200, text=html, mode=fetch_mode)

    return _fetch


def _fixture_connector() -> SiteRecipeConnector:
    """A site-recipe connector wired to the recorded fixture (no network)."""
    recipe = SiteRecipe(
        user_id="u1",
        name="Acme Careers",
        slug="acme-careers",
        base_url="https://jobs.acme.example",
        search_url_template="https://jobs.acme.example/search?q={query}",
        schema={"title": "text", "company": "text"},
        fetch_mode="http",
    )
    return SiteRecipeConnector(
        recipe,
        fetch_fn=_fixture_fetch(FIXTURE.read_text(encoding="utf-8")),
        url_validator=lambda u: u,  # bypass DNS; SSRF is covered by unit tests
        extractor=_fixture_extractor,
    )


class _FailingConnector:
    """A source that always fails — proves partial-success fan-out (Req 3.2)."""

    name = "naukri"
    fetch_mode: FetchMode = "http"

    async def search(self, query, filters, failures):
        raise TimeoutError("naukri: upstream timed out")


async def _fake_query_fn(resume_text, *, resume_version=None, filters=None, **_):
    """A fixed, non-degraded query (isolates degraded-by-source from degraded-by-query)."""
    return SearchQuery(
        titles=["Backend Engineer"],
        search_string="backend engineer python go",
        seniority="senior",
        location=(filters.location if filters else None),
        country_indeed="india",
        degraded=False,
        resume_version=resume_version,
    )


async def _fallback_query_fn(resume_text, *, resume_version=None, filters=None, **_):
    """A synthesized (LLM-less) query — degraded=True (Req 2.2)."""
    q = await _fake_query_fn(resume_text, resume_version=resume_version, filters=filters)
    q.degraded = True
    return q


async def _fake_keyword_extractor(user_id: str, jd_text: str):
    present = [s for s in _SKILLS if s.lower() in (jd_text or "").lower()]
    return {"required_skills": present or ["General"], "preferred_skills": [], "keywords": []}


def _fake_match_scorer(resume, keywords) -> float:
    req = keywords.get("required_skills", [])
    blob = json.dumps(resume).lower()
    hits = sum(1 for k in req if k.lower() in blob)
    return round(100.0 * hits / max(1, len(req)), 1)


async def _resume_loader(user_id: str, resume_id: str):
    return _RESUME if resume_id == _RESUME.resume_id else None


def _config(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        JOB_DISCOVERY=enabled,
        JOB_DISCOVERY_MAX_RESULTS=50,
        JOB_DISCOVERY_CACHE_TTL_SECONDS=3600,
        JOB_DISCOVERY_MAX_RECIPES=20,
        JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY=1,
        job_discovery_jobspy_sites=["indeed"],
    )


def _build_service(db: Database, *, connectors, query_fn) -> DiscoveryService:
    async def _builder(user_id: str):
        return list(connectors)

    return DiscoveryService(
        db,
        resume_loader=_resume_loader,
        config=_config(enabled=True),
        connector_builder=_builder,
        query_fn=query_fn,
        keyword_extractor=_fake_keyword_extractor,
        match_scorer=_fake_match_scorer,
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "discovery.db"
    # _run_migrations(db_file)  # Skipped: create_all handles it
    database = Database.from_url(f"sqlite+aiosqlite:///{db_file}")
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
        for dep in (get_db, get_settings_dep, get_discovery_service):
            app.dependency_overrides.pop(dep, None)


def _wire(db: Database, *, enabled: bool, connectors, query_fn) -> None:
    app.dependency_overrides[get_settings_dep] = lambda: _config(enabled=enabled)
    app.dependency_overrides[get_discovery_service] = lambda: _build_service(
        db, connectors=connectors, query_fn=query_fn
    )


# --------------------------------------------------------------------------- #
# 1. Happy path — ranked recs from the fixture, clean run is cached
# --------------------------------------------------------------------------- #
async def test_recommend_happy_path_and_cache_roundtrip(client, db):
    _wire(db, enabled=True, connectors=[_fixture_connector()], query_fn=_fake_query_fn)

    resp = await client.post(
        "/api/v1/discovery/recommend", json={"resume_id": "resume-1"}
    )
    assert resp.status_code == 200
    body = resp.json()

    # Two usable listings from the fixture (the title-less third card is dropped).
    titles = [r["listing"]["title"] for r in body["recommendations"]]
    assert "Senior Backend Engineer" in titles
    assert "Platform Engineer (Search Results Only)" in titles

    by_title = {r["listing"]["title"]: r for r in body["recommendations"]}
    full = by_title["Senior Backend Engineer"]
    partial = by_title["Platform Engineer (Search Results Only)"]

    # Full listing: scored on its description, higher, not partial.
    assert full["partial"] is False
    assert full["match_score"] > partial["match_score"]
    # Description-less row: flagged partial (Req 7.2) and ranked below the full one.
    assert partial["partial"] is True
    assert body["recommendations"][0]["listing"]["title"] == "Senior Backend Engineer"

    # Clean run: not degraded, no failures.
    assert body["degraded"] is False
    assert body["failures"] == []
    assert body["cached"] is False

    # ... and it was cached, so the cheap GET path returns it with cached=True.
    cached = await client.get("/api/v1/discovery/recommend/resume-1")
    assert cached.status_code == 200
    cached_body = cached.json()
    assert cached_body["cached"] is True
    assert [r["listing"]["title"] for r in cached_body["recommendations"]] == titles


# --------------------------------------------------------------------------- #
# 2. Partial-source reporting + degraded flag (Req 1.2, 3.2)
# --------------------------------------------------------------------------- #
async def test_partial_source_failure_reports_and_degrades(client, db):
    _wire(
        db,
        enabled=True,
        connectors=[_fixture_connector(), _FailingConnector()],
        query_fn=_fake_query_fn,
    )

    resp = await client.post(
        "/api/v1/discovery/recommend", json={"resume_id": "resume-1"}
    )
    assert resp.status_code == 200
    body = resp.json()

    # One source failed but the request still succeeded with the other's rows.
    assert len(body["recommendations"]) == 2, "fixture source's listings survive"

    # The failing source is attributed and classified.
    assert len(body["failures"]) == 1
    failure = body["failures"][0]
    assert failure["source"] == "naukri"
    assert failure["kind"] == "timeout"

    # A source failure sets the degraded banner signal (Req 1.2, 3.2).
    assert body["degraded"] is True

    # A degraded result must NOT be cached (a transient failure can't pin a
    # partial result until TTL) — so the cheap GET path is a cold 404.
    cached = await client.get("/api/v1/discovery/recommend/resume-1")
    assert cached.status_code == 404
    assert cached.json()["detail"] == "no_cached_recommendation"


# --------------------------------------------------------------------------- #
# 3. Degraded via query fallback (Req 2.2) — the second degraded trigger
# --------------------------------------------------------------------------- #
async def test_query_fallback_degrades_even_with_healthy_source(client, db):
    _wire(
        db,
        enabled=True,
        connectors=[_fixture_connector()],
        query_fn=_fallback_query_fn,
    )

    resp = await client.post(
        "/api/v1/discovery/recommend", json={"resume_id": "resume-1"}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["recommendations"], "healthy source still returns listings"
    assert body["failures"] == [], "no source failed"
    assert body["degraded"] is True, "synthesized query alone degrades the result"
    assert body["query"]["degraded"] is True


# --------------------------------------------------------------------------- #
# 4. Kill-switch off — the surface is invisible (Req 10, 11.3)
# --------------------------------------------------------------------------- #
async def test_kill_switch_off_hides_every_route(client, db):
    _wire(db, enabled=False, connectors=[_fixture_connector()], query_fn=_fake_query_fn)

    assert (
        await client.post("/api/v1/discovery/recommend", json={"resume_id": "resume-1"})
    ).status_code == 404
    assert (
        await client.get("/api/v1/discovery/recommend/resume-1")
    ).status_code == 404
    assert (await client.get("/api/v1/discovery/recipes")).status_code == 404


# --------------------------------------------------------------------------- #
# 5. Unknown resume -> 404 resume_not_found (ownership boundary, Req 1.5)
# --------------------------------------------------------------------------- #
async def test_unknown_resume_is_not_found(client, db):
    _wire(db, enabled=True, connectors=[_fixture_connector()], query_fn=_fake_query_fn)

    resp = await client.post(
        "/api/v1/discovery/recommend", json={"resume_id": "does-not-exist"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume_not_found"
