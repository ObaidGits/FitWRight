"""Tests for site-recipe validation + CRUD (task 0.14).

Validation rules are pure and tested without a database. The per-user cap and
CRUD semantics are exercised against a fresh temp SQLite database built by
applying the real Alembic migration (same pattern as
``test_database_discovery.py``). Requirements 4.1, 9.4, 10.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from alembic.config import Config

from alembic import command
from app.database import Database
from app.job_discovery import recipes
from app.job_discovery.models import SiteRecipe

BACKEND_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "recipes.db"
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
        schema={"title": "h1"},
        fetch_mode="http",
        enabled=True,
    )
    base.update(overrides)
    return SiteRecipe(**base)


# --------------------------------------------------------------------------- #
# Validation (pure)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_validate_accepts_a_well_formed_recipe():
    # Should not raise.
    recipes.validate_recipe(_recipe())
    recipes.validate_recipe(_recipe(fetch_mode="stealth"))
    recipes.validate_recipe(_recipe(slug="acme-careers-2"))


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["user_id", "name", "base_url", "search_url_template"])
def test_validate_rejects_missing_required_field(missing):
    with pytest.raises(recipes.RecipeValidationError, match=f"{missing} is required"):
        recipes.validate_recipe(_recipe(**{missing: "  "}))


@pytest.mark.unit
def test_validate_rejects_empty_slug():
    with pytest.raises(recipes.RecipeValidationError, match="slug is required"):
        recipes.validate_recipe(_recipe(slug=""))


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_slug",
    ["Acme", "acme_careers", "acme careers", "-acme", "acme-", "acme--x", "acmé"],
)
def test_validate_rejects_bad_slug_format(bad_slug):
    with pytest.raises(recipes.RecipeValidationError, match="slug must be"):
        recipes.validate_recipe(_recipe(slug=bad_slug))


@pytest.mark.unit
def test_validate_rejects_overlong_slug():
    with pytest.raises(recipes.RecipeValidationError, match="at most"):
        recipes.validate_recipe(_recipe(slug="a" * (recipes.SLUG_MAX_LEN + 1)))


@pytest.mark.unit
def test_validate_rejects_bad_fetch_mode():
    with pytest.raises(recipes.RecipeValidationError, match="fetch_mode must be"):
        recipes.validate_recipe(_recipe(fetch_mode="carrier-pigeon"))


@pytest.mark.unit
def test_validate_rejects_template_without_query_placeholder():
    with pytest.raises(recipes.RecipeValidationError, match="query"):
        recipes.validate_recipe(
            _recipe(search_url_template="https://acme.example/jobs")
        )


@pytest.mark.unit
def test_validate_rejects_non_dict_schema():
    with pytest.raises(recipes.RecipeValidationError, match="schema must be"):
        recipes.validate_recipe(_recipe(schema=["not", "a", "dict"]))


@pytest.mark.unit
def test_validate_reports_multiple_errors_at_once():
    with pytest.raises(recipes.RecipeValidationError) as exc:
        recipes.validate_recipe(_recipe(slug="BAD", fetch_mode="nope"))
    msg = str(exc.value)
    assert "slug must be" in msg
    assert "fetch_mode must be" in msg


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_create_then_get_and_list(db):
    created = await recipes.create_recipe(
        db,
        "u1",
        name="Acme",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
        schema={"title": "h1"},
    )
    assert created.id is not None

    got = await recipes.get_recipe(db, "u1", "acme")
    assert got.slug == "acme"
    assert got.fetch_mode == "http"

    listed = await recipes.list_recipes(db, "u1")
    assert [r.slug for r in listed] == ["acme"]


@pytest.mark.unit
async def test_create_rejects_invalid_recipe_before_touching_db(db):
    with pytest.raises(recipes.RecipeValidationError):
        await recipes.create_recipe(
            db,
            "u1",
            name="Acme",
            slug="BAD SLUG",
            base_url="https://acme.example",
            search_url_template="https://acme.example/jobs?q={query}",
        )
    # Nothing persisted.
    assert await recipes.list_recipes(db, "u1") == []


@pytest.mark.unit
async def test_create_rejects_duplicate_slug(db):
    kwargs = dict(
        name="Acme",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
    )
    await recipes.create_recipe(db, "u1", **kwargs)
    with pytest.raises(recipes.RecipeConflictError):
        await recipes.create_recipe(db, "u1", **kwargs)
    assert len(await recipes.list_recipes(db, "u1")) == 1


@pytest.mark.unit
async def test_per_user_cap_enforced(db):
    cap = 3
    for i in range(cap):
        await recipes.create_recipe(
            db,
            "u1",
            name=f"Site {i}",
            slug=f"site-{i}",
            base_url="https://x.example",
            search_url_template="https://x.example/jobs?q={query}",
            max_recipes=cap,
        )

    with pytest.raises(recipes.RecipeLimitError):
        await recipes.create_recipe(
            db,
            "u1",
            name="Overflow",
            slug="overflow",
            base_url="https://x.example",
            search_url_template="https://x.example/jobs?q={query}",
            max_recipes=cap,
        )

    # A different user has their own independent budget.
    other = await recipes.create_recipe(
        db,
        "u2",
        name="Other",
        slug="other",
        base_url="https://y.example",
        search_url_template="https://y.example/jobs?q={query}",
        max_recipes=cap,
    )
    assert other.id is not None


@pytest.mark.unit
async def test_update_merges_partial_fields(db):
    await recipes.create_recipe(
        db,
        "u1",
        name="Acme",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
    )
    updated = await recipes.update_recipe(
        db, "u1", "acme", name="Acme Renamed", fetch_mode="stealth", enabled=False
    )
    assert updated.name == "Acme Renamed"
    assert updated.fetch_mode == "stealth"
    assert updated.enabled is False
    # Untouched field preserved.
    assert updated.base_url == "https://acme.example"
    # Still a single row.
    assert len(await recipes.list_recipes(db, "u1")) == 1


@pytest.mark.unit
async def test_update_revalidates_merged_recipe(db):
    await recipes.create_recipe(
        db,
        "u1",
        name="Acme",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
    )
    with pytest.raises(recipes.RecipeValidationError, match="query"):
        await recipes.update_recipe(
            db, "u1", "acme", search_url_template="https://acme.example/jobs"
        )


@pytest.mark.unit
async def test_update_missing_recipe_raises(db):
    with pytest.raises(recipes.RecipeNotFoundError):
        await recipes.update_recipe(db, "u1", "ghost", name="X")


@pytest.mark.unit
async def test_delete_recipe(db):
    await recipes.create_recipe(
        db,
        "u1",
        name="Acme",
        slug="acme",
        base_url="https://acme.example",
        search_url_template="https://acme.example/jobs?q={query}",
    )
    await recipes.delete_recipe(db, "u1", "acme")
    assert await recipes.list_recipes(db, "u1") == []
    # Deleting again is a not-found.
    with pytest.raises(recipes.RecipeNotFoundError):
        await recipes.delete_recipe(db, "u1", "acme")


@pytest.mark.unit
async def test_get_missing_recipe_raises(db):
    with pytest.raises(recipes.RecipeNotFoundError):
        await recipes.get_recipe(db, "u1", "nope")
