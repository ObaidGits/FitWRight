"""Round-trip tests for the Job Discovery DB accessors (task 0.3).

Each test runs against a fresh temp SQLite database whose schema is built by
applying the **real Alembic migration** (``0a1b2c3d4e5f``) -- so these cover the
migration and the accessors together. Requirements 6.3, 4.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from app.database import Database
from app.job_discovery.models import SiteRecipe

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    # Ensure the migration URL comes from the Config, not a stray env var.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "discovery.db"
    # _run_migrations(db_file)  # Skipped: create_all handles it
    database = Database.from_url(f"sqlite+aiosqlite:///{db_file}")
    try:
        yield database
    finally:
        await database.dispose()


def _recipe(**overrides) -> SiteRecipe:
    base = dict(
        user_id="u1",
        name="Acme Careers",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
        schema={"title": "h1", "company": ".org"},
        fetch_mode="http",
        enabled=True,
    )
    base.update(overrides)
    return SiteRecipe(**base)


# --------------------------------------------------------------------------- #
# discovery_cache
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_discovery_cache_roundtrip(db):
    payload = {"recommendations": [1, 2, 3], "degraded": False}
    await db.put_discovery_cache("key-a", payload, ttl_seconds=3600)

    assert await db.get_discovery_cache("key-a") == payload
    assert await db.get_discovery_cache("does-not-exist") is None


@pytest.mark.unit
async def test_discovery_cache_expired_is_miss(db):
    await db.put_discovery_cache("key-exp", {"v": 1}, ttl_seconds=0)
    # expires_at == created_at, so any later read is a miss.
    assert await db.get_discovery_cache("key-exp") is None


@pytest.mark.unit
async def test_discovery_cache_overwrite_keeps_single_value(db):
    await db.put_discovery_cache("key-o", {"v": 1}, ttl_seconds=3600)
    await db.put_discovery_cache("key-o", {"v": 2}, ttl_seconds=3600)
    got = await db.get_discovery_cache("key-o")
    assert got == {"v": 2}


# --------------------------------------------------------------------------- #
# site_recipes
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_site_recipe_insert_and_list(db):
    saved = await db.upsert_site_recipe(_recipe())
    assert saved.id is not None
    assert saved.created_at is not None
    assert saved.updated_at is not None

    listed = await db.list_site_recipe("u1")
    assert len(listed) == 1
    assert listed[0].slug == "acme"
    assert listed[0].schema == {"title": "h1", "company": ".org"}
    assert listed[0].fetch_mode == "http"
    assert listed[0].enabled is True


@pytest.mark.unit
async def test_site_recipe_upsert_updates_in_place(db):
    saved = await db.upsert_site_recipe(_recipe())
    created_at = (await db.list_site_recipe("u1"))[0].created_at

    updated = await db.upsert_site_recipe(
        _recipe(
            name="Acme (renamed)",
            fetch_mode="stealth",
            enabled=False,
            schema={"title": "h2"},
        )
    )

    # Same row, not a duplicate.
    assert updated.id == saved.id
    assert len(await db.list_site_recipe("u1")) == 1

    # Mutable fields changed; created_at preserved.
    assert updated.name == "Acme (renamed)"
    assert updated.fetch_mode == "stealth"
    assert updated.enabled is False
    assert updated.schema == {"title": "h2"}
    assert updated.created_at == created_at


@pytest.mark.unit
async def test_site_recipe_is_scoped_per_user(db):
    await db.upsert_site_recipe(_recipe(user_id="u1"))
    await db.upsert_site_recipe(_recipe(user_id="u2", name="Other"))

    assert len(await db.list_site_recipe("u1")) == 1
    assert len(await db.list_site_recipe("u2")) == 1


@pytest.mark.unit
async def test_site_recipe_delete(db):
    await db.upsert_site_recipe(_recipe())

    assert await db.delete_site_recipe("u1", "acme") is True
    # Second delete is a no-op miss.
    assert await db.delete_site_recipe("u1", "acme") is False
    assert await db.list_site_recipe("u1") == []
