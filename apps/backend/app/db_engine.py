"""Database engine/session plumbing for the SQLAlchemy data layer (ADR-13).

Every ``Database`` instance owns two engines built from these factories: one
**async** engine for the document tables and one **sync** engine for the
encrypted ``api_keys`` table read on the synchronous LLM hot path. Both are
resolved from the *same* database URL so the two views never diverge.

The dialect is selected from the resolved URL, **not** a hardcoded path:

* **SQLite** (``sqlite+aiosqlite://`` async / ``sqlite://`` sync) - the local,
  zero-config default. WAL/busy_timeout/foreign_keys PRAGMAs are applied per
  connection (SQLite only). Schema evolves locally via ``create_all`` +
  ``init_models_sync`` (see below).
* **Postgres** (``postgresql+asyncpg://`` async / ``postgresql+psycopg://`` sync)
  - the hosted target (Neon). Pooling is configured from ``settings.db_pool_size``
  / ``settings.db_use_pooler``; no SQLite PRAGMAs and no local schema mutation
  (Postgres schema is owned by Alembic).

Passing a :class:`pathlib.Path` still builds a SQLite file URL (keeps the
isolated-temp-file test ergonomics); passing a URL string selects the dialect;
passing ``None`` resolves ``settings.effective_database_url``.
"""

import logging
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Iterator

try:  # Linux production/local target; fallback still serializes threads.
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX developer platform
    fcntl = None  # type: ignore[assignment]

from sqlalchemy import Column, UniqueConstraint, create_engine, event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.schema import CreateIndex
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import settings
from app.models import Base

logger = logging.getLogger(__name__)

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


# -- TLS / SSL resolution (Supabase & any TLS-requiring Postgres) ------------
#
# asyncpg.connect() rejects the libpq ``sslmode`` keyword, so a Supabase URL
# carrying ``?sslmode=require`` would raise on connect if passed through. We
# therefore strip libpq SSL params out of the URL and translate them into a
# deterministic, driver-appropriate connect arg: an ``ssl`` value (bool /
# SSLContext) for asyncpg, and a ``sslmode`` string for psycopg (which does
# understand it). Precedence: explicit ``DB_SSL`` setting > URL sslmode/ssl >
# hosted default (``require``) > None (driver default; local non-TLS Postgres).

_SSL_VERIFY_MODES = frozenset({"verify-ca", "verify-full"})


def _extract_pg_ssl_mode(url: str) -> tuple[str, str | None]:
    """Strip libpq SSL params (and driver-incompatible pooler hints) from a PG
    URL; return ``(clean_url, mode)``.

    ``mode`` is a resolved libpq-style sslmode string (``require``,
    ``verify-full``, ``disable`` ...) or ``None`` when SSL is unspecified.

    Besides ``sslmode``/``ssl``, this also drops ``pgbouncer`` - a
    Prisma/PgBouncer hint (``?pgbouncer=true``) that Supabase's copy-paste
    connection strings sometimes carry. Neither asyncpg nor psycopg accept it as
    a connect kwarg, and SQLAlchemy forwards unknown query params to the driver,
    which then raises ``connect() got an unexpected keyword argument
    'pgbouncer'``. Transaction-pooler safety is already handled via
    ``DB_USE_POOLER`` (disabled prepared statements + NullPool), so the flag is
    redundant here and safe to strip.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    # Query params understood by neither asyncpg nor psycopg as connect kwargs;
    # forwarding them raises a TypeError, so they are dropped during cleaning.
    _DROP_PARAMS = frozenset({"pgbouncer"})

    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True) if parts.query else []
    url_mode: str | None = None
    kept: list[tuple[str, str]] = []
    for key, value in pairs:
        lk = key.lower()
        if lk == "sslmode":
            url_mode = value.strip().lower() or None
        elif lk == "ssl":
            lv = value.strip().lower()
            if lv in ("true", "1", "require"):
                url_mode = "require"
            elif lv in ("false", "0", "disable"):
                url_mode = "disable"
            else:
                url_mode = lv or None
        elif lk in _DROP_PARAMS:
            continue  # incompatible pooler hint - drop (see docstring)
        else:
            kept.append((key, value))
    clean = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )

    configured = (settings.db_ssl or "").strip().lower() or None
    mode = configured or url_mode
    if mode is None and not settings.single_user_mode:
        # Hosted deployments talk to managed Postgres over TLS - secure default.
        mode = "require"
    return clean, mode


def _asyncpg_ssl_arg(mode: str | None):
    """Translate a libpq sslmode into an asyncpg ``ssl`` connect arg.

    ``verify-ca``/``verify-full`` -> a CA-verifying default context; ``require``
    (and any other encrypt-but-don't-verify value) -> an encrypted, non-verifying
    context (Supabase pooler presents a cert that may not chain to a public CA);
    ``disable`` -> ``False``; unspecified/prefer/allow -> ``None`` (driver default).
    """
    if mode == "disable":
        return False
    if mode is None or mode in ("prefer", "allow"):
        return None
    import ssl as _ssl

    ctx = _ssl.create_default_context()
    if mode not in _SSL_VERIFY_MODES:
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE
    return ctx


def _sqlite_url_from_path(path: Path, *, async_: bool) -> str:
    """Build a SQLite URL from a file path. Absolute paths yield four slashes."""
    driver = "sqlite+aiosqlite" if async_ else "sqlite"
    return f"{driver}:///{path}"


def _normalize_url(url: str, *, async_: bool) -> str:
    """Normalize a database URL to the driver required for the requested engine.

    Mirrors ``alembic/env.py``: a bare ``postgresql://`` (or ``postgres://``,
    ``postgresql+psycopg2://``, ...) is rewritten to ``postgresql+asyncpg://`` for
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

    ``None`` -> ``settings.effective_database_url`` (single source of truth so the
    runtime and Alembic agree); a :class:`Path` -> a SQLite file URL; a ``str`` ->
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
    side prepared statements are unsafe - a pooled connection may serve a
    different backend between Parse and Bind - so we disable asyncpg's statement
    cache (``statement_cache_size=0``) *and* SQLAlchemy's asyncpg prepared-
    statement cache (``prepared_statement_cache_size=0``). Non-pooler Postgres
    uses an in-process pool sized from ``db_pool_size`` with liveness pre-ping.

    We ALSO randomize the prepared-statement name (``prepared_statement_name_func``)
    with a UUID. Under a transaction pooler, a fixed name like ``__asyncpg_stmt_5__``
    can collide with an orphaned statement of the same name left on a pooled
    server connection by a *previous* client session that was killed before it
    could DEALLOCATE (e.g. a worker restart / OOM kill). That collision surfaces
    as ``DuplicatePreparedStatementError`` and wedges every connection until the
    pooler recycles the physical server connection. A UUID name can never
    collide, making restarts against a shared pooler safe.

    **Warm client-side pool (perf, ADR-13 amendment).** We keep a small
    ``QueuePool`` of live client connections to the external pooler instead of
    ``NullPool``. ``NullPool`` disposed every connection after use, so each
    logical DB operation paid a full TCP+TLS+startup handshake to the pooler
    (~2.6 s to a cross-region Supabase pooler; ~10-40 ms co-located) - the
    dominant request-latency source. A transaction pooler (pgbouncer) is
    explicitly designed to hold many persistent client connections and multiplex
    them onto fewer server backends per-transaction, so a warm client pool is the
    intended usage for a long-lived server (only serverless should use NullPool).
    ``pool_pre_ping`` validates a connection on checkout (so a connection the
    pooler dropped on its idle timeout is transparently replaced, not surfaced as
    an error), and ``pool_recycle`` proactively rotates connections before that
    timeout. Server-side prepared statements stay disabled (transaction-pool
    safety), so nothing about correctness changes - only the reconnect cost.
    """
    if settings.db_use_pooler:
        from uuid import uuid4

        return {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_pool_size,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "connect_args": {
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
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
    statements (``prepare_threshold=None``). We keep a warm client-side pool
    (not ``NullPool``) for the same reason as the async engine: this engine is
    read on the *synchronous* LLM hot path (``get_llm_config`` -> encrypted
    ``api_keys`` read), and a per-read reconnect to a cross-region pooler added
    seconds to every LLM-touching request. ``pool_pre_ping`` + ``pool_recycle``
    keep the warm connections safe against the pooler's idle timeout.
    """
    # The sync engine is a cold path (audit B1/D1): the decrypted-key cache
    # serves the LLM hot path, so this engine only sees cache-miss reads and rare
    # writes. Keep a small idle pool (``db_sync_pool_size``) but let overflow
    # absorb a cold-cache burst up to the async engine's size.
    sync_pool_size = max(1, settings.db_sync_pool_size)
    overflow = max(sync_pool_size, settings.db_pool_size)
    if settings.db_use_pooler:
        return {
            "pool_size": sync_pool_size,
            "max_overflow": overflow,
            "pool_pre_ping": True,
            "pool_recycle": 1800,
            "connect_args": {"prepare_threshold": None},
        }
    return {
        "pool_size": sync_pool_size,
        "max_overflow": overflow,
        "pool_pre_ping": True,
    }


# -- engine factories -------------------------------------------------------


def make_async_engine(source: Path | str | None = None) -> AsyncEngine:
    """Create the async engine for the document tables (SQLite or Postgres).

    ``source`` may be a :class:`Path` (SQLite file - used by isolated tests), a
    URL string, or ``None`` (resolve ``settings.effective_database_url``).
    """
    url = resolve_database_url(source, async_=True)
    if is_sqlite_url(url):
        engine = create_async_engine(url, future=True)
        event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
        return engine
    clean_url, ssl_mode = _extract_pg_ssl_mode(url)
    options = _pg_async_options()
    ssl_arg = _asyncpg_ssl_arg(ssl_mode)
    if ssl_arg is not None:
        options.setdefault("connect_args", {})["ssl"] = ssl_arg
    return create_async_engine(clean_url, future=True, **options)


def make_sync_engine(source: Path | str | None = None) -> Engine:
    """Create the sync engine used for the encrypted api_keys table.

    Key reads happen synchronously (``get_llm_config`` -> ``load_config_file`` ->
    ``resolve_api_key``), so a sync engine avoids threading async through
    ``llm.py``. It points at the same database as the async engine (SQLite file
    on both engines locally; the same Postgres server hosted).
    """
    url = resolve_database_url(source, async_=False)
    if is_sqlite_url(url):
        engine = create_engine(url, future=True)
        event.listen(engine, "connect", _apply_sqlite_pragmas)
        return engine
    clean_url, ssl_mode = _extract_pg_ssl_mode(url)
    options = _pg_sync_options()
    if ssl_mode is not None:
        # psycopg v3 understands libpq ``sslmode`` directly as a connect kwarg.
        options.setdefault("connect_args", {}).setdefault("sslmode", ssl_mode)
    return create_engine(clean_url, future=True, **options)


# -- additive local SQLite schema reconciliation -----------------------------


def _column_default_literal(col: Column) -> str | None:
    """Return a SQL literal for a column's constant default, or ``None``.

    Only *constant* defaults are usable in a SQLite ``ALTER TABLE ADD COLUMN``
    (callables like ``_utcnow_iso`` are applied by the ORM at insert time, not by
    the DB). Handles Python-side scalar defaults and simple ``server_default``
    text (e.g. ``"1"``). Booleans map to ``0``/``1`` (SQLite has no bool type).
    """
    default = col.default
    if default is not None and getattr(default, "is_scalar", False):
        val = default.arg
        if isinstance(val, bool):
            return "1" if val else "0"
        if isinstance(val, (int, float)):
            return repr(val)
        if isinstance(val, str):
            return "'" + val.replace("'", "''") + "'"
    server_default = col.server_default
    if server_default is not None:
        arg = getattr(server_default, "arg", None)
        text = getattr(arg, "text", None) if arg is not None else None
        if text is None and arg is not None:
            text = str(arg)
        if text:
            return text
    return None


def _sqlite_add_column_ddl(engine: Engine, col: Column) -> str:
    """Build the ``"name" TYPE [NOT NULL] [DEFAULT x]`` clause for an ADD COLUMN.

    The column is added as **nullable** unless a constant default is available -
    SQLite requires a default to add a NOT NULL column to a populated table, so
    when none exists we drop the NOT NULL locally (the additive back-fill can
    then never fail; hosted Postgres still enforces the real constraint via
    Alembic). The type is compiled for the SQLite dialect from the model column.
    """
    type_sql = col.type.compile(dialect=engine.dialect)
    default_sql = _column_default_literal(col)
    clause = f'"{col.name}" {type_sql}'
    if not col.nullable and default_sql is not None:
        clause += f" NOT NULL DEFAULT {default_sql}"
    elif default_sql is not None:
        clause += f" DEFAULT {default_sql}"
    return clause


def _sqlite_type_affinity(declared_type: str) -> str:
    """Return SQLite's storage affinity for a declared type."""
    normalized = declared_type.upper()
    if "INT" in normalized:
        return "INTEGER"
    if any(token in normalized for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if "BLOB" in normalized or not normalized:
        return "BLOB"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return "NUMERIC"


def _diagnose_sqlite_schema_drift(conn: Connection, engine: Engine) -> None:
    """Warn about model drift that additive startup healing cannot repair.

    This is deliberately read-only. Rebuilding a table to change primary keys,
    types, nullability, uniqueness, or foreign keys can lose data and therefore
    remains an explicit migration/operator action.
    """
    for table in Base.metadata.sorted_tables:
        info = conn.exec_driver_sql(
            f'PRAGMA table_info("{table.name}")'
        ).mappings().all()
        if not info:
            continue
        actual = {row["name"]: row for row in info}
        problems: list[str] = []

        expected_pk = [column.name for column in table.primary_key.columns]
        actual_pk = [
            row["name"]
            for row in sorted(info, key=lambda item: int(item["pk"] or 0))
            if int(row["pk"] or 0) > 0
        ]
        if actual_pk != expected_pk:
            problems.append(f"primary_key expected={expected_pk} actual={actual_pk}")

        for column in table.columns:
            live = actual.get(column.name)
            if live is None:
                problems.append(f"missing_column={column.name}")
                continue
            expected_type = column.type.compile(dialect=engine.dialect)
            if _sqlite_type_affinity(str(live["type"] or "")) != _sqlite_type_affinity(
                expected_type
            ):
                problems.append(
                    f"type {column.name} expected={expected_type} actual={live['type']}"
                )
            if (
                not column.nullable
                and not column.primary_key
                and int(live["notnull"] or 0) != 1
            ):
                problems.append(f"nullable={column.name}")

        index_rows = conn.exec_driver_sql(
            f'PRAGMA index_list("{table.name}")'
        ).mappings().all()
        actual_index_names = {row["name"] for row in index_rows if row["name"]}
        for index in table.indexes:
            if index.unique and index.name and index.name not in actual_index_names:
                problems.append(f"missing_unique_index={index.name}")

        actual_unique_columns: set[tuple[str, ...]] = set()
        for index_row in index_rows:
            if not int(index_row["unique"] or 0) or not index_row["name"]:
                continue
            columns = conn.exec_driver_sql(
                f'PRAGMA index_info("{index_row["name"]}")'
            ).mappings().all()
            names = tuple(row["name"] for row in columns if row["name"] is not None)
            if names:
                actual_unique_columns.add(names)
        for constraint in table.constraints:
            if isinstance(constraint, UniqueConstraint):
                expected = tuple(column.name for column in constraint.columns)
                if expected not in actual_unique_columns:
                    problems.append(
                        f"missing_unique_constraint={constraint.name or expected}"
                    )

        actual_foreign_keys = {
            (
                row["from"],
                row["table"],
                row["to"],
                str(row["on_delete"] or "").upper(),
            )
            for row in conn.exec_driver_sql(
                f'PRAGMA foreign_key_list("{table.name}")'
            ).mappings()
        }
        for column in table.columns:
            for foreign_key in column.foreign_keys:
                target = foreign_key.column
                expected_fk = (
                    column.name,
                    target.table.name,
                    target.name,
                    str(foreign_key.ondelete or "").upper(),
                )
                if expected_fk not in actual_foreign_keys:
                    problems.append(
                        "missing_foreign_key="
                        f"{column.name}->{target.table.name}.{target.name}"
                    )

        if problems:
            logger.warning(
                "Local SQLite non-repairable schema drift on %s: %s. "
                "Preserving data; run an explicit migration or rebuild from a backup.",
                table.name,
                "; ".join(problems),
            )


_SQLITE_SCHEMA_THREAD_LOCK = threading.Lock()


@contextmanager
def _sqlite_schema_init_lock(engine: Engine) -> Iterator[None]:
    """Serialize SQLite schema initialization across threads and workers.

    ``MetaData.create_all(checkfirst=True)`` performs a check followed by DDL;
    two Uvicorn workers can both pass the check and race on CREATE/ALTER. A
    stable sibling lock file protects the entire additive reconciliation phase.
    The file is intentionally retained so a late process never locks a replaced
    inode. In-memory databases only need the in-process lock.
    """
    with _SQLITE_SCHEMA_THREAD_LOCK:
        database = engine.url.database
        if fcntl is None or not database or database == ":memory:":
            yield
            return
        db_path = Path(database).expanduser().resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = db_path.with_name(f"{db_path.name}.schema.lock")
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _serialize_sqlite_schema_init(func: Any) -> Any:
    """Decorate SQLite initialization without indenting the reconciliation."""

    @wraps(func)
    def wrapped(engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            func(engine)
            return
        with _sqlite_schema_init_lock(engine):
            func(engine)

    return wrapped


@_serialize_sqlite_schema_init
def init_models_sync(engine: Engine) -> None:
    """Create/evolve the schema locally (SQLite only); no-op on Postgres.

    **SQLite (local, zero-config):** ``create_all`` creates missing tables
    (fresh local DBs get the full ``user_id``-scoped schema) but never ALTERs
    existing ones. The additive steps below keep older local databases - created
    before a column existed - loadable and, critically, add the ``user_id``
    scope column to any owned table missing it. This is the local ``create_all``
    equivalent of Alembic migration 0003; the runtime owner backfill
    (``app.auth.owner.ensure_owner``) then claims those rows for the bootstrap
    owner so single-user local keeps working with zero data loss.

    **Postgres (hosted):** the schema is owned by the Alembic migration chain
    (``0001``->``0006``). Running ``create_all``/``ALTER`` here would race and
    diverge from the migrations, so this function is a **no-op** on Postgres -
    guarded on the engine dialect. SQLite-specific ``PRAGMA table_info`` calls
    would not even be valid SQL on Postgres.
    """
    if engine.dialect.name != "sqlite":
        # Postgres schema is owned by Alembic; never create_all/ALTER/PRAGMA here.
        return

    Base.metadata.create_all(engine)

    # General additive reconciler: ``create_all`` creates *missing tables* with
    # their full current schema, but SQLite never ALTERs an EXISTING table to add
    # columns introduced later. So an older local DB keeps a table's original
    # column set and the first query selecting a newer column 500s with
    # "no such column" (seen on users.avatar_key, resumes.template_settings,
    # resumes.version, ...). Here we compare every model table to the live table
    # and additively ADD any missing column, derived from the model itself, so
    # ALL past *and future* local schema drift self-heals on boot with zero data
    # loss. This is the SQLite equivalent of the hosted Alembic chain; Postgres
    # returns above and is untouched.
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            info = conn.exec_driver_sql(
                f'PRAGMA table_info("{table.name}")'
            ).mappings().all()
            if not info:
                # Table absent (shouldn't happen post create_all) - skip; a later
                # create_all covers it. Never ALTER a table we didn't confirm.
                continue
            existing = {column["name"] for column in info}
            for col in table.columns:
                if col.name in existing or col.primary_key:
                    # PKs can't be added via ALTER; fresh tables already have them.
                    continue
                ddl = _sqlite_add_column_ddl(engine, col)
                conn.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
                logger.info("Local SQLite schema heal: added %s.%s", table.name, col.name)

        # ``create_all`` also creates indexes only when it creates the table;
        # it does not repair indexes missing from an existing local table. Walk
        # model metadata so every current and future ordinary lookup index is
        # restored, including composite and expression indexes. Unique indexes
        # are deliberately excluded: a stale local database may contain legacy
        # duplicates, and attempting to enforce a newly introduced uniqueness
        # invariant during startup would make the whole application unbootable.
        # Those changes require an explicit, data-aware migration instead.
        for table in Base.metadata.sorted_tables:
            if not conn.exec_driver_sql(
                f'PRAGMA table_info("{table.name}")'
            ).first():
                continue
            existing_indexes = {
                row["name"]
                for row in conn.exec_driver_sql(
                    f'PRAGMA index_list("{table.name}")'
                ).mappings()
                if row["name"]
            }
            for index in sorted(table.indexes, key=lambda item: item.name or ""):
                if index.unique or not index.name or index.name in existing_indexes:
                    continue
                conn.execute(CreateIndex(index, if_not_exists=True))
                existing_indexes.add(index.name)
                logger.info(
                    "Local SQLite schema heal: added index %s on %s",
                    index.name,
                    table.name,
                )

        _diagnose_sqlite_schema_drift(conn, engine)
