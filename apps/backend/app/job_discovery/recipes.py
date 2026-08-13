"""Site-recipe CRUD + validation for the Job Discovery feature (task 0.14).

A *site recipe* is a persisted, per-user description of how to scrape a custom
job board (design §4). This module is the thin service layer between the router
(``routers/discovery.py``) and the dialect-agnostic DB accessors in
:mod:`app.database`. It owns two responsibilities the accessors deliberately do
not:

* **Validation** -- required fields, slug format, ``fetch_mode`` enum, and a
  ``{query}`` placeholder in the search-URL template (all pure, so they can be
  unit-tested without a database).
* **Per-user quota** -- a user may own at most ``JOB_DISCOVERY_MAX_RECIPES``
  recipes (:mod:`app.config`); creating a brand-new recipe past the cap is
  refused. Updating an existing recipe never counts against the cap.

Uniqueness is ``(user_id, slug)`` (mirrors the ``site_recipes`` table). Create
refuses to clobber an existing slug; update requires the slug to already exist.

Design reference: ``.kiro/specs/job-discovery/design.md`` §4 (site recipes).
Requirements: 4.1, 9.4, 10.
"""

from __future__ import annotations

import re
from typing import get_args

from app.config import settings
from app.database import Database
from app.job_discovery.models import FetchMode, SiteRecipe

# --------------------------------------------------------------------------- #
# Validation constants
# --------------------------------------------------------------------------- #
# Slug: lowercase alphanumerics with single interior hyphens; no leading /
# trailing / doubled hyphen. Bounded so it fits the ``String(255)`` column with
# room to spare and reads as a URL-safe identifier.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX_LEN = 64

# The set of accepted fetch strategies, derived from the ``FetchMode`` Literal so
# this can never drift from the canonical model.
VALID_FETCH_MODES: tuple[str, ...] = get_args(FetchMode)

# The search-URL template must contain this placeholder; the site-recipe
# connector renders it with the URL-encoded query at fetch time (design §4).
QUERY_PLACEHOLDER = "{query}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class RecipeError(Exception):
    """Base class for recipe-service errors."""


class RecipeValidationError(RecipeError):
    """A recipe failed a field/format validation rule."""


class RecipeNotFoundError(RecipeError):
    """No recipe exists for the given (user_id, slug)."""


class RecipeConflictError(RecipeError):
    """A recipe with the same slug already exists for the user."""


class RecipeLimitError(RecipeError):
    """Creating this recipe would exceed the per-user recipe cap."""


# --------------------------------------------------------------------------- #
# Validation (pure)
# --------------------------------------------------------------------------- #
def validate_recipe(recipe: SiteRecipe) -> None:
    """Validate a :class:`SiteRecipe`, raising :class:`RecipeValidationError`.

    Pure and side-effect free so validation rules are unit-testable without a
    database. Called by both :func:`create_recipe` and :func:`update_recipe`
    against the fully-resolved recipe just before it is persisted.
    """
    errors: list[str] = []

    # Required, non-empty string fields.
    for attr in ("user_id", "name", "slug", "base_url", "search_url_template"):
        value = getattr(recipe, attr, None)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{attr} is required")

    # Slug format (only meaningful once slug is a non-empty string).
    if isinstance(recipe.slug, str) and recipe.slug:
        if len(recipe.slug) > SLUG_MAX_LEN:
            errors.append(f"slug must be at most {SLUG_MAX_LEN} characters")
        if not SLUG_RE.match(recipe.slug):
            errors.append(
                "slug must be lowercase alphanumerics separated by single "
                "hyphens (e.g. 'acme-careers')"
            )

    # fetch_mode enum.
    if recipe.fetch_mode not in VALID_FETCH_MODES:
        errors.append(
            f"fetch_mode must be one of {sorted(VALID_FETCH_MODES)}, "
            f"got {recipe.fetch_mode!r}"
        )

    # The template must carry the {query} placeholder to be renderable.
    if (
        isinstance(recipe.search_url_template, str)
        and recipe.search_url_template.strip()
        and QUERY_PLACEHOLDER not in recipe.search_url_template
    ):
        errors.append(
            f"search_url_template must contain the {QUERY_PLACEHOLDER} placeholder"
        )

    # schema, when present, must be a JSON object.
    if recipe.schema is not None and not isinstance(recipe.schema, dict):
        errors.append("schema must be a JSON object (dict)")

    if errors:
        raise RecipeValidationError("; ".join(errors))


def _max_recipes(max_recipes: int | None) -> int:
    """Resolve the per-user cap, defaulting to the configured value."""
    return settings.JOB_DISCOVERY_MAX_RECIPES if max_recipes is None else max_recipes


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #
async def list_recipes(db: Database, user_id: str) -> list[SiteRecipe]:
    """Return all recipes owned by ``user_id`` (ordered by slug)."""
    return await db.list_site_recipe(user_id)


async def get_recipe(db: Database, user_id: str, slug: str) -> SiteRecipe:
    """Return the single recipe for (user_id, slug) or raise :class:`RecipeNotFoundError`."""
    for recipe in await db.list_site_recipe(user_id):
        if recipe.slug == slug:
            return recipe
    raise RecipeNotFoundError(f"no recipe {slug!r} for user {user_id!r}")


async def create_recipe(
    db: Database,
    user_id: str,
    *,
    name: str,
    slug: str,
    base_url: str,
    search_url_template: str,
    schema: dict | None = None,
    fetch_mode: FetchMode = "http",
    enabled: bool = True,
    max_recipes: int | None = None,
) -> SiteRecipe:
    """Create a new recipe for ``user_id``.

    Validates the recipe, refuses to clobber an existing slug
    (:class:`RecipeConflictError`), and enforces the per-user cap
    (:class:`RecipeLimitError`) counting only *new* recipes.
    """
    recipe = SiteRecipe(
        user_id=user_id,
        name=name,
        slug=slug,
        base_url=base_url,
        search_url_template=search_url_template,
        schema=schema or {},
        fetch_mode=fetch_mode,
        enabled=enabled,
    )
    validate_recipe(recipe)

    existing = await db.list_site_recipe(user_id)
    if any(r.slug == slug for r in existing):
        raise RecipeConflictError(
            f"recipe {slug!r} already exists for user {user_id!r}"
        )

    cap = _max_recipes(max_recipes)
    if len(existing) >= cap:
        raise RecipeLimitError(
            f"recipe limit reached ({cap}); delete one before adding another"
        )

    return await db.upsert_site_recipe(recipe)


async def update_recipe(
    db: Database,
    user_id: str,
    slug: str,
    *,
    name: str | None = None,
    base_url: str | None = None,
    search_url_template: str | None = None,
    schema: dict | None = None,
    fetch_mode: FetchMode | None = None,
    enabled: bool | None = None,
) -> SiteRecipe:
    """Partially update an existing recipe.

    Only the provided fields change; the slug (identity) and owner are fixed.
    Raises :class:`RecipeNotFoundError` if the recipe does not exist. The merged
    recipe is re-validated before persisting. Does not affect the per-user cap.
    """
    current = await get_recipe(db, user_id, slug)

    merged = SiteRecipe(
        id=current.id,
        user_id=current.user_id,
        name=current.name if name is None else name,
        slug=current.slug,
        base_url=current.base_url if base_url is None else base_url,
        search_url_template=(
            current.search_url_template
            if search_url_template is None
            else search_url_template
        ),
        schema=current.schema if schema is None else schema,
        fetch_mode=current.fetch_mode if fetch_mode is None else fetch_mode,
        enabled=current.enabled if enabled is None else enabled,
        created_at=current.created_at,
        updated_at=current.updated_at,
    )
    validate_recipe(merged)

    return await db.upsert_site_recipe(merged)


async def delete_recipe(db: Database, user_id: str, slug: str) -> None:
    """Delete the recipe for (user_id, slug) or raise :class:`RecipeNotFoundError`."""
    deleted = await db.delete_site_recipe(user_id, slug)
    if not deleted:
        raise RecipeNotFoundError(f"no recipe {slug!r} for user {user_id!r}")


__all__ = [
    "QUERY_PLACEHOLDER",
    "SLUG_MAX_LEN",
    "SLUG_RE",
    "VALID_FETCH_MODES",
    "RecipeConflictError",
    "RecipeError",
    "RecipeLimitError",
    "RecipeNotFoundError",
    "RecipeValidationError",
    "create_recipe",
    "delete_recipe",
    "get_recipe",
    "list_recipes",
    "update_recipe",
    "validate_recipe",
]
