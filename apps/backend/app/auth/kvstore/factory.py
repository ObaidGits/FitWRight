"""Adapter selection for the pluggable ``KVStore`` (ADR-6 / ADR-14).

``KVSTORE_URL`` chooses the adapter with zero code change:

- empty / ``local`` / ``memory`` / ``inproc``  → :class:`LocalKVStore`
  (single-worker local dev default);
- ``redis://…`` / ``rediss://…``               → :class:`RedisKVStore`
  (Upstash free or managed Redis premium);
- ``db`` / ``database`` / ``sqlite`` / ``db://`` → :class:`DBKVStore`
  (the "no Redis at all" fallback; requires the primary DB engine).

Config/env parsing (reading ``KVSTORE_URL`` off settings, wiring the app's DB
engine) belongs to the config surface task; this factory only maps an already-
resolved URL string to an adapter so both the app and tests share one selector.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.kvstore.base import KVStore
from app.auth.kvstore.db import DBKVStore
from app.auth.kvstore.local import LocalKVStore
from app.auth.kvstore.redis_store import RedisKVStore

__all__ = ["kvstore_from_url", "url_needs_db_engine"]

_LOCAL_ALIASES = frozenset({"", "local", "memory", "inproc", "in-proc", "local://"})
_DB_ALIASES = frozenset({"db", "database", "sqlite", "db://", "database://"})


def url_needs_db_engine(url: str | None) -> bool:
    """Whether ``url`` selects the DB-backed adapter (so a DB engine is needed).

    Lets callers (the runtime wiring) avoid eagerly initializing the database
    engine when a local/Redis store is selected, while keeping the scheme rules
    in one place alongside :func:`kvstore_from_url`.
    """
    lowered = (url or "").strip().lower()
    return (
        lowered in _DB_ALIASES
        or lowered.startswith("db://")
        or lowered.startswith("database://")
    )


def kvstore_from_url(
    url: str | None,
    *,
    db_engine: AsyncEngine | None = None,
) -> KVStore:
    """Return the ``KVStore`` adapter selected by ``url``.

    Args:
        url: The resolved ``KVSTORE_URL`` value (may be ``None``/empty for local).
        db_engine: The primary database's async engine, required only when a
            DB-backed store is selected (the app passes its own engine so KV
            data lives in the same database).

    Raises:
        ValueError: If a DB-backed store is requested without ``db_engine``, or
            the scheme is unrecognized.
    """
    normalized = (url or "").strip()
    lowered = normalized.lower()

    if lowered in _LOCAL_ALIASES:
        return LocalKVStore()

    if lowered.startswith("redis://") or lowered.startswith("rediss://"):
        return RedisKVStore.from_url(normalized)

    if lowered in _DB_ALIASES or lowered.startswith("db://") or lowered.startswith("database://"):
        if db_engine is None:
            raise ValueError(
                "KVSTORE_URL selects the DB-backed KVStore but no database engine "
                "was provided to kvstore_from_url(db_engine=...)."
            )
        return DBKVStore(db_engine)

    raise ValueError(
        f"Unrecognized KVSTORE_URL {normalized!r}. Use '' / 'local' / 'memory', "
        "a 'redis://' or 'rediss://' URL, or 'db' / 'database' for the DB fallback."
    )
