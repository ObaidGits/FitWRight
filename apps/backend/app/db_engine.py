"""Database engine/session plumbing for the SQLAlchemy data layer (ADR-13).

Every ``Database`` instance owns two engines built from these factories: one
**async** engine for the document tables and one **sync** engine for the
encrypted ``api_keys`` table read on the synchronous LLM hot path. Both are
resolved from the *same* database URL so the two views never diverge.

The dialect is selected from the resolved URL, **not** a hardcoded path:

* **SQLite** (``sqlite+aiosqlite://`` async / ``sqlite://`` sync) — the local,
  zero-config default. WAL/busy_timeout/foreign_keys PRAGMAs are applied per
  connection (SQLite only). Schema evolves locally via ``create_all`` +
  ``init_models_sync`` (see below).
* **Postgres** (``postgresql+asyncpg://`` async / ``postgresql+psycopg://`` sync)
  — the hosted target (Neon). Pooling is configured from ``settings.db_pool_size``
  / ``settings.db_use_pooler``; no SQLite PRAGMAs and no local schema mutation
  (Postgres schema is owned by Alembic).

Passing a :class:`pathlib.Path` still builds a SQLite file URL (keeps the
isolated-temp-file test ergonomics); passing a URL string selects the dialect;
passing ``None`` resolves ``settings.effective_database_url``.
"""

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Base

__all__ = [
    "Base",
    "make_async_engine",
    "make_sync_engine",
    "init_models_sync",
    "resolve_database_url",
    "is_sqlite_url",
]


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Set per-connection SQLite PRAGMAs (SQLite only).

    WAL improves concurrent read/write between the async (doc tables) and sync
    (api_keys) engines pointed at the same file; ``busy_timeout`` rides out the
    brief lock contention that creates; ``foreign_keys`` enforces relational
    integrity (off by default in SQLite). These are never applied on Postgres.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


# -- URL resolution ---------------------------------------------------------

# Postgres URL prefixes we normalize to a specific driver. Order matters: the
# already-driverful prefixes must be checked before the bare ones.
_PG_PREFIXES: tuple[str, ...] = (
    "postgresql+asyncpg://",
    "postgresql+psycopg://",
    "postgresql+psycopg2://",
    "postgresql://",
    "postgres://",
)


def is_sqlite_url(url: str) -> bool:
    """Whether ``url`` targets SQLite (as opposed to Postgres)."""
    return url.startswith("sqlite")


def _sqlite_url_from_path(path: Path, *, async_: bool) -> str:
    """Build a SQLite URL from a file path. Absolute paths yield four slashes."""
    driver = "sqlite+aiosqlite" if async_ else "sqlite"
    return f"{driver}:///{path}"


def _normalize_url(url: str, *, async_: bool) -> str:
    """Normalize a database URL to the driver required for the requested engine.

    Mirrors ``alembic/env.py``: a bare ``postgresql://`` (or ``postgres://``,
    ``postgresql+psycopg2://``, …) is rewritten to ``postgresql+asyncpg://`` for
    the async engine and ``postgresql+psycopg://`` for the sync engine. SQLite
    URLs are pinned to ``aiosqlite`` (async) / the default DBAPI (sync).
    """
    if is_sqlite_url(url):
        if async_:
            if url.startswith("sqlite+"):
                return url
            return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        if url.startswith("sqlite+aiosqlite://"):
            return url.replace("sqlite+aiosqlite://", "sqlite://", 1)
        return url

    target = "asyncpg" if async_ else "psycopg"
    for prefix in _PG_PREFIXES:
        if url.startswith(prefix):
            return f"postgresql+{target}://" + url[len(prefix):]
    return url


def resolve_database_url(source: Path | str | None, *, async_: bool) -> str:
    """Resolve ``source`` to a concrete, driver-correct database URL.

    ``None`` → ``settings.effective_database_url`` (single source of truth so the
    runtime and Alembic agree); a :class:`Path` → a SQLite file URL; a ``str`` →
    normalized to the async/sync driver appropriate for the engine being built.
    """
    if source is None:
        source = settings.effective_database_url
    if isinstance(source, Path):
        return _sqlite_url_from_path(source, async_=async_)
    return _normalize_url(source, async_=async_)


# -- Postgres pooling options -----------------------------------------------


def _pg_async_options() -> dict[str, Any]:
    """Engine kwargs for the async (asyncpg) Postgres engine.

    When ``db_use_pooler`` is set (Neon/PgBouncer transaction pooling), server-
    side prepared statements are unsafe — a pooled connection may serve a
    different backend between Parse and Bind — so we disable asyncpg's statement
    cache (``statement_cache_size=0``) *and* SQLAlchemy's asyncpg prepared-
    statement cache (``prepared_statement_cache_size=0``), and defer pooling to
    the external pooler via :class:`~sqlalchemy.pool.NullPool`. Otherwise we use
    an in-process pool sized from ``db_pool_size`` with liveness pre-ping.
    """
    if settings.db_use_pooler:
        return {
            "poolclass": NullPool,
            "connect_args": {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
            },
        }
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_pool_size,
        "pool_pre_ping": True,
    }


def _pg_sync_options() -> dict[str, Any]:
    """Engine kwargs for the sync (psycopg v3) Postgres engine.

    Under a transaction pooler, disable psycopg's server-side prepared
    statements (``prepare_threshold=None``) and defer pooling to the external
    pooler (:class:`~sqlalchemy.pool.NullPool`); otherwise use an in-process
    pool sized from ``db_pool_size`` with liveness pre-ping.
    """
    if settings.db_use_pooler:
        return {
            "poolclass": NullPool,
            "connect_args": {"prepare_threshold": None},
        }
    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_pool_size,
        "pool_pre_ping": True,
    }


# -- engine factories -------------------------------------------------------


def make_async_engine(source: Path | str | None = None) -> AsyncEngine:
    """Create the async engine for the document tables (SQLite or Postgres).

    ``source`` may be a :class:`Path` (SQLite file — used by isolated tests), a
    URL string, or ``None`` (resolve ``settings.effective_database_url``).
    """
    url = resolve_database_url(source, async_=True)
    if is_sqlite_url(url):
        engine = create_async_engine(url, future=True)
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
        return engine
    return create_async_engine(url, future=True, **_pg_async_options())


def make_sync_engine(source: Path | str | None = None) -> Engine:
    """Create the sync engine used for the encrypted api_keys table.

    Key reads happen synchronously (``get_llm_config`` → ``load_config_file`` →
    ``resolve_api_key``), so a sync engine avoids threading async through
    ``llm.py``. It points at the same database as the async engine (SQLite file
    on both engines locally; the same Postgres server hosted).
    """
    url = resolve_database_url(source, async_=False)
    if is_sqlite_url(url):
        engine = create_engine(url, future=True)
        event.listen(engine, "connect", _apply_sqlite_pragmas)
        return engine
    return create_engine(url, future=True, **_pg_sync_options())


# Owned tables that must carry a ``user_id`` scope column locally (ADR-4). This
# mirrors the owned-table set enforced by ``app.repository.Repo`` and Alembic
# migration 0003 on hosted.
_OWNED_TABLES_LOCAL: tuple[str, ...] = (
    "resumes",
    "jobs",
    "improvements",
    "applications",
    "api_keys",
)


def init_models_sync(engine: Engine) -> None:
    """Create/evolve the schema locally (SQLite only); no-op on Postgres.

    **SQLite (local, zero-config):** ``create_all`` creates missing tables
    (fresh local DBs get the full ``user_id``-scoped schema) but never ALTERs
    existing ones. The additive steps below keep older local databases — created
    before a column existed — loadable and, critically, add the ``user_id``
    scope column to any owned table missing it. This is the local ``create_all``
    equivalent of Alembic migration 0003; the runtime owner backfill
    (``app.auth.owner.ensure_owner``) then claims those rows for the bootstrap
    owner so single-user local keeps working with zero data loss.

    **Postgres (hosted):** the schema is owned by the Alembic migration chain
    (``0001``→``0006``). Running ``create_all``/``ALTER`` here would race and
    diverge from the migrations, so this function is a **no-op** on Postgres —
    guarded on the engine dialect. SQLite-specific ``PRAGMA table_info`` calls
    would not even be valid SQL on Postgres.
    """
    if engine.dialect.name != "sqlite":
        # Postgres schema is owned by Alembic; never create_all/ALTER/PRAGMA here.
        return

    Base.metadata.create_all(engine)

    with engine.begin() as conn:
        columns = conn.exec_driver_sql("PRAGMA table_info(resumes)").mappings().all()
        if columns and "interview_prep" not in {column["name"] for column in columns}:
            conn.exec_driver_sql("ALTER TABLE resumes ADD COLUMN interview_prep TEXT")

        # Add a nullable ``user_id`` to any owned table missing it (older DBs).
        # Kept nullable + index-only here (a PK/NOT NULL change on an existing
        # SQLite table needs a rebuild — hosted does that via migration 0005).
        for table in _OWNED_TABLES_LOCAL:
            info = conn.exec_driver_sql(f"PRAGMA table_info({table})").mappings().all()
            if info and "user_id" not in {column["name"] for column in info}:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN user_id TEXT")
                conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id "
                    f"ON {table} (user_id)"
                )
