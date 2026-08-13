"""Unit tests for the content-addressed search cache (task 0.9).

Covers store/get round-trip, key derivation (determinism + sensitivity to each
of resume_version / query / filters), and TTL expiry. The cache is exercised
against a fresh temp SQLite database whose schema is built by applying the real
Alembic migration -- so these cover the cache, the accessors, and the migration
together. Requirements 6.2, 6.4.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from app.database import Database
from app.job_discovery.cache import SearchCache, make_cache_key
from app.job_discovery.models import SearchFilters, SearchQuery

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "cache.db"
    # _run_migrations(db_file)  # Skipped: create_all handles it
    database = Database.from_url(f"sqlite+aiosqlite:///{db_file}")
    try:
        yield database
    finally:
        await database.dispose()


def _query(**overrides) -> SearchQuery:
    base = dict(
        titles=["Backend Engineer", "Platform Engineer"],
        search_string="backend OR platform",
        seniority="senior",
        location="Bengaluru",
        country_indeed="india",
    )
    base.update(overrides)
    return SearchQuery(**base)


def _filters(**overrides) -> SearchFilters:
    base = dict(location="Bengaluru", is_remote=True, results_wanted=25)
    base.update(overrides)
    return SearchFilters(**base)


# --------------------------------------------------------------------------- #
# key derivation
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_key_is_deterministic_and_sha256_shaped():
    k1 = make_cache_key("rv1", _query(), _filters())
    k2 = make_cache_key("rv1", _query(), _filters())
    assert k1 == k2
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


@pytest.mark.unit
def test_key_is_insensitive_to_field_order():
    # Same content, different dict insertion order -> same key.
    a = make_cache_key(
        "rv1",
        {"search_string": "x", "titles": ["A"], "location": "LA"},
        {"is_remote": True, "location": "LA"},
    )
    b = make_cache_key(
        "rv1",
        {"location": "LA", "titles": ["A"], "search_string": "x"},
        {"location": "LA", "is_remote": True},
    )
    assert a == b


@pytest.mark.unit
def test_key_changes_with_resume_version():
    assert make_cache_key("rv1", _query(), _filters()) != make_cache_key(
        "rv2", _query(), _filters()
    )


@pytest.mark.unit
def test_key_changes_with_query():
    assert make_cache_key("rv1", _query(), _filters()) != make_cache_key(
        "rv1", _query(search_string="frontend"), _filters()
    )


@pytest.mark.unit
def test_key_changes_with_filters():
    assert make_cache_key("rv1", _query(), _filters()) != make_cache_key(
        "rv1", _query(), _filters(is_remote=False)
    )


@pytest.mark.unit
def test_key_ignores_degraded_flag():
    # ``degraded`` is a status flag, not part of the search intent.
    assert make_cache_key("rv1", _query(degraded=False), _filters()) == (
        make_cache_key("rv1", _query(degraded=True), _filters())
    )


@pytest.mark.unit
def test_dataclass_and_dict_inputs_agree():
    from_dc = make_cache_key("rv1", _query(), _filters())
    from_dict = make_cache_key(
        "rv1",
        {
            "titles": ["Backend Engineer", "Platform Engineer"],
            "search_string": "backend OR platform",
            "seniority": "senior",
            "location": "Bengaluru",
            "country_indeed": "india",
        },
        {
            "location": "Bengaluru",
            "is_remote": True,
            "hours_old": None,
            "results_wanted": 25,
            "country_indeed": None,
        },
    )
    assert from_dc == from_dict


# --------------------------------------------------------------------------- #
# store / get
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_store_and_get_roundtrip(db):
    cache = SearchCache(db, ttl_seconds=3600)
    payload = {"recommendations": [{"title": "Job A"}], "degraded": False}

    key = await cache.store("rv1", _query(), _filters(), payload)
    assert key == make_cache_key("rv1", _query(), _filters())

    got = await cache.get("rv1", _query(), _filters())
    assert got == payload


@pytest.mark.unit
async def test_get_miss_returns_none(db):
    cache = SearchCache(db, ttl_seconds=3600)
    assert await cache.get("rv1", _query(), _filters()) is None


@pytest.mark.unit
async def test_get_is_sensitive_to_inputs(db):
    cache = SearchCache(db, ttl_seconds=3600)
    await cache.store("rv1", _query(), _filters(), {"v": 1})

    # Different filters -> different key -> miss.
    assert await cache.get("rv1", _query(), _filters(is_remote=False)) is None
    # Exact same inputs -> hit.
    assert await cache.get("rv1", _query(), _filters()) == {"v": 1}


# --------------------------------------------------------------------------- #
# TTL
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_ttl_expiry_is_a_miss(db):
    cache = SearchCache(db, ttl_seconds=0)
    await cache.store("rv1", _query(), _filters(), {"v": 1})
    # expires_at == created_at, so any later read is a miss.
    assert await cache.get("rv1", _query(), _filters()) is None


@pytest.mark.unit
async def test_positive_ttl_is_a_hit(db):
    cache = SearchCache(db, ttl_seconds=3600)
    await cache.store("rv1", _query(), _filters(), {"v": 1})
    assert await cache.get("rv1", _query(), _filters()) == {"v": 1}


@pytest.mark.unit
def test_ttl_defaults_to_config(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "JOB_DISCOVERY_CACHE_TTL_SECONDS", 1234)
    cache = SearchCache(Database.from_url(f"sqlite+aiosqlite:///{tmp_path}/x.db"))
    assert cache.ttl_seconds == 1234
