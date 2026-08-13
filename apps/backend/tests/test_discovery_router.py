"""Integration tests for the discovery router (task 0.16).

Proves the ``JOB_DISCOVERY`` kill-switch gate (design §9, Req 10.1/10.2/11.3):
with the feature **off** every discovery route returns **404** — the surface is
invisible, not merely disabled. The recipe-list assertion is the load-bearing
one: recipe endpoints have no service-level kill-switch, so if the router-level
gate (:func:`require_job_discovery_enabled`) were removed, ``GET /recipes`` would
fall through to the DB and return ``200 []`` and this test would fail.

With the feature **on** the same routes are reachable (gate passes), exercised
via a recipe create -> list round-trip against a migrated temp database.

Collaborators are swapped through ``app.dependency_overrides`` so nothing here
touches a live LLM/browser/scraper.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.database import Database
from app.main import app
from app.routers.discovery import get_db, get_settings_dep

BACKEND_DIR = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


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
    db_file = tmp_path / "discovery.db"
    # _run_migrations(db_file)  # Skipped: create_all handles it
    database = Database.from_url(f"sqlite+aiosqlite:///{db_file}")
    try:
        yield database
    finally:
        await database.dispose()


@pytest_asyncio.fixture
async def client(db):
    """AsyncClient bound to the app with db + settings overridable per test."""
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
# Kill-switch OFF -> every route 404s
# --------------------------------------------------------------------------- #
async def test_recommend_404_when_disabled(client):
    _enable(False)
    resp = await client.post("/api/v1/discovery/recommend", json={"resume_id": "r1"})
    assert resp.status_code == 404


async def test_cached_get_404_when_disabled(client):
    _enable(False)
    resp = await client.get("/api/v1/discovery/recommend/r1")
    assert resp.status_code == 404


async def test_tailor_404_when_disabled(client):
    _enable(False)
    resp = await client.post(
        "/api/v1/discovery/tailor",
        json={
            "resume_id": "r1",
            "listing": {
                "source": "indeed",
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://example.com/1",
            },
        },
    )
    assert resp.status_code == 404


async def test_recipes_crud_all_404_when_disabled(client):
    """The load-bearing gate proof: recipe routes have no service-level gate, so
    a removed router gate would make these 200/other instead of 404."""
    _enable(False)
    assert (await client.get("/api/v1/discovery/recipes")).status_code == 404
    assert (
        await client.post(
            "/api/v1/discovery/recipes",
            json={
                "name": "Acme",
                "slug": "acme-careers",
                "base_url": "https://acme.example",
                "search_url_template": "https://acme.example/jobs?q={query}",
            },
        )
    ).status_code == 404
    assert (
        await client.put("/api/v1/discovery/recipes/acme-careers", json={})
    ).status_code == 404
    assert (
        await client.delete("/api/v1/discovery/recipes/acme-careers")
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Kill-switch ON -> gate passes, routes are reachable
# --------------------------------------------------------------------------- #
async def test_recipes_reachable_when_enabled(client):
    _enable(True)
    # Empty list initially (gate passed through to the DB).
    listing = await client.get("/api/v1/discovery/recipes")
    assert listing.status_code == 200
    assert listing.json() == []

    # Create -> 201, then it shows up in the list (round-trip through the router).
    created = await client.post(
        "/api/v1/discovery/recipes",
        json={
            "name": "Acme Careers",
            "slug": "acme-careers",
            "base_url": "https://acme.example",
            "search_url_template": "https://acme.example/jobs?q={query}",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["slug"] == "acme-careers"
    assert body["schema"] == {}

    after = await client.get("/api/v1/discovery/recipes")
    assert after.status_code == 200
    assert [r["slug"] for r in after.json()] == ["acme-careers"]


async def test_cached_get_miss_is_404_when_enabled(client):
    """With the feature on, an unknown resume resolves to a 404 (not the
    kill-switch 404) — proving the gate lets the request through to the service."""
    _enable(True)
    resp = await client.get("/api/v1/discovery/recommend/unknown-resume")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "resume_not_found"
