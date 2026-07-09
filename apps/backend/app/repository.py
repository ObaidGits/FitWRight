"""Centralized owned-resource scoping (ADR-4, R10.2/10.6/10.8).

Every owned-table query in the data layer is composed through :class:`Repo`, so
the *scope key* — the column(s) that decide ownership — lives in exactly one
place. Today that key is ``user_id``; a future team/org scope (R10.8) becomes a
``(org_id, user_id)`` change **here** and every owned query picks it up without
touching a single call site.

Two composition helpers cover the whole data layer:

- :meth:`Repo.scoped` — append ``WHERE user_id = :uid`` to a ``select`` /
  ``update`` / ``delete`` statement built for an owned model.
- :meth:`Repo.owns` — the in-Python ownership predicate used after a bare
  primary-key load (``session.get``) to decide 404 vs. hit.

The set of owned tables is declared once in :data:`Repo.OWNED_TABLES` and is what
the CI scoping guard (``app/scripts/check_scoping.py``) enforces against: no
owned-table query may be issued outside this repository layer, and every
repository method that builds one must be scoped by ``user_id``.
"""

from __future__ import annotations

from typing import TypeVar

from sqlalchemy.sql import Delete, Select, Update

from app.models import ApiKey, Application, Improvement, Job, Resume

__all__ = ["Repo"]

_Statement = TypeVar("_Statement", Select, Update, Delete)


class Repo:
    """The single source of truth for how owned rows are scoped to a user."""

    # The ownership scope key. A future org scope is a localized change here:
    # add ``org_id`` to this tuple and extend :meth:`scoped`/:meth:`owns`.
    SCOPE_KEYS: tuple[str, ...] = ("user_id",)

    # Every table that carries an ownership scope (ADR-4). The scoping guard
    # forbids any query against these outside the repository layer.
    OWNED_TABLES: frozenset[str] = frozenset(
        {
            Resume.__tablename__,
            Job.__tablename__,
            Improvement.__tablename__,
            Application.__tablename__,
            ApiKey.__tablename__,
        }
    )

    @classmethod
    def scoped(cls, statement: _Statement, model: type, user_id: str) -> _Statement:
        """Return ``statement`` narrowed to rows owned by ``user_id``.

        Works for ``select`` / ``update`` / ``delete`` statements built for an
        owned ``model``. ``user_id`` is mandatory — passing ``None`` is a
        programming error (unscoped owned access) and raises immediately rather
        than silently returning another user's rows.
        """
        if user_id is None:
            raise ValueError(
                f"Refusing to build an unscoped query for {model.__name__}: "
                "user_id is required (owned-resource isolation, R10.2)."
            )
        for key in cls.SCOPE_KEYS:
            statement = statement.where(getattr(model, key) == user_id)
        return statement

    @classmethod
    def owns(cls, row: object, user_id: str) -> bool:
        """Whether ``row`` is owned by ``user_id`` across every scope key.

        Used after a bare primary-key load so a foreign row is treated as
        absent (404, no existence disclosure — R10.3).
        """
        if row is None or user_id is None:
            return False
        return all(getattr(row, key, None) == user_id for key in cls.SCOPE_KEYS)
