"""Automatic, lock-guarded schema migration at startup (hosted Postgres).

Local/dev on SQLite gets its schema from ``create_all`` (``init_models_sync``),
so migrations are a no-op there. On **hosted Postgres** the schema is owned by
the Alembic chain and *nothing* previously applied it at boot - a fresh database
would start "healthy" and then fail every query on a missing relation. This
module closes that gap: on startup it brings the database up to ``head``.

Safety properties (why this is safe to run on every boot / every worker):

- **Serialized across workers/instances** by a Postgres *session* advisory lock
  (``pg_advisory_lock``). Concurrent uvicorn workers or rolling instances can
  all call this; exactly one migrates while the others block, then find the DB
  already at head and no-op. This is the "migration lock" that prevents two
  processes running the chain at once.
- **Idempotent.** ``alembic upgrade head`` is a no-op when already current.
- **Off the event loop.** Alembic's ``env.py`` runs its own ``asyncio.run``,
  which cannot nest inside the running app loop - so the upgrade executes in a
  worker thread (``asyncio.to_thread``) that has no active loop.
- **Fail-fast.** Any migration error propagates so the app refuses to serve a
  half-migrated/misconfigured database (the caller aborts startup).

Controlled by ``DB_AUTO_MIGRATE`` (default on); set false to manage migrations
out-of-band (e.g. a dedicated release phase) - then this becomes a no-op that
only *verifies* the DB is reachable.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

logger = logging.getLogger(__name__)

__all__ = ["apply_migrations_if_configured"]

# Fixed 64-bit advisory-lock key (arbitrary constant, stable across processes).
# Derived from ASCII "FITWMIGR" so collisions with other advisory locks are
# vanishingly unlikely.
_MIGRATION_LOCK_KEY = 0x4649_5457_4D49_4752  # 'FITWMIGR'

# How long a worker waits for the migration lock before failing fast, and how
# often it polls. The wait must comfortably exceed a full cold migration.
_LOCK_TIMEOUT_SECONDS = 300
_LOCK_POLL_SECONDS = 1.0

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _alembic_config():
    from alembic.config import Config

    if not _ALEMBIC_INI.is_file():  # pragma: no cover - packaging guard
        raise RuntimeError(f"alembic.ini not found at {_ALEMBIC_INI}")
    return Config(str(_ALEMBIC_INI))


def _migration_url() -> str:
    """The connection string migrations run against - the DIRECT endpoint.

    Prefers ``MIGRATION_DATABASE_URL`` (set to the non-pooled 5432 endpoint on
    Supabase/PgBouncer deployments); falls back to ``DATABASE_URL`` when the app
    already talks to a direct connection (local, Neon-direct, ...).
    """
    from app.config import settings

    return (settings.migration_database_url or "").strip() or settings.effective_database_url


def _run_upgrade_with_lock() -> dict:
    """Blocking upgrade under a Postgres advisory lock (runs in a worker thread).

    Runs against the DIRECT endpoint (``_migration_url``): the session advisory
    lock and CREATE INDEX CONCURRENTLY are unsafe through a transaction pooler.
    """
    import os

    from alembic import command

    # A dedicated sync connection holds the advisory lock for the whole upgrade,
    # built against the direct migration URL (inherits resolved SSL connect args).
    from app.db_engine import make_sync_engine

    migration_url = _migration_url()
    engine = make_sync_engine(migration_url)
    acquired = False
    try:
        # AUTOCOMMIT is essential: an idle-in-transaction lock holder would make
        # the migrations' ``CREATE INDEX CONCURRENTLY`` steps wait forever (they
        # block on any concurrent transaction that can see the table). Autocommit
        # keeps the advisory lock session-scoped without holding a snapshot.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # Poll with pg_TRY_advisory_lock rather than the blocking variant: a
            # blocking lock call is an *active query* holding a snapshot, which
            # would make the winner's CREATE INDEX CONCURRENTLY wait on the
            # waiter (a livelock). Between poll attempts the (autocommit)
            # connection is idle and holds no snapshot, so CONCURRENTLY proceeds.
            import time

            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                got = conn.exec_driver_sql(
                    "SELECT pg_try_advisory_lock(%(k)s)", {"k": _MIGRATION_LOCK_KEY}
                ).scalar()
                if got:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        "Timed out waiting for the migration advisory lock "
                        f"({_LOCK_TIMEOUT_SECONDS}s); another instance may be stuck."
                    )
                logger.info("Migration lock held by another instance; waiting...")
                time.sleep(_LOCK_POLL_SECONDS)
            acquired = True
            logger.info("Migration lock acquired; upgrading schema to head")
            # env.py resolves its engine from ALEMBIC_DATABASE_URL first, so
            # point Alembic at the same direct endpoint the lock uses.
            prev = os.environ.get("ALEMBIC_DATABASE_URL")
            os.environ["ALEMBIC_DATABASE_URL"] = migration_url
            try:
                command.upgrade(_alembic_config(), "head")
            finally:
                if prev is None:
                    os.environ.pop("ALEMBIC_DATABASE_URL", None)
                else:
                    os.environ["ALEMBIC_DATABASE_URL"] = prev
            conn.exec_driver_sql(
                "SELECT pg_advisory_unlock(%(k)s)", {"k": _MIGRATION_LOCK_KEY}
            )
            acquired = False
        return {"status": "migrated"}
    finally:
        # Best-effort unlock on the error path (session close also releases it).
        if acquired:  # pragma: no cover - defensive
            try:
                with engine.connect().execution_options(
                    isolation_level="AUTOCOMMIT"
                ) as c2:
                    c2.exec_driver_sql(
                        "SELECT pg_advisory_unlock(%(k)s)", {"k": _MIGRATION_LOCK_KEY}
                    )
            except Exception:
                logger.debug("advisory unlock on error path failed", exc_info=True)
        engine.dispose()


async def _verify_reachable() -> None:
    """Cheap connectivity probe used when auto-migrate is disabled."""
    from app.database import db

    async with db.async_engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def apply_migrations_if_configured() -> dict:
    """Bring a hosted Postgres up to head at startup (SQLite is a no-op).

    Returns a small status dict for logging. Raises on migration failure so the
    caller can abort startup (fail-fast) rather than serve a broken schema.
    """
    from app.config import settings

    url = settings.effective_database_url
    if url.startswith("sqlite"):
        # Local/dev: schema comes from create_all/init_models_sync.
        return {"status": "skipped_sqlite"}

    if not settings.db_auto_migrate:
        # Ops manages migrations out-of-band; still verify the DB is reachable so
        # a bad DATABASE_URL fails fast at boot rather than on first request.
        await _verify_reachable()
        return {"status": "auto_migrate_disabled"}

    result = await asyncio.to_thread(_run_upgrade_with_lock)
    return result
