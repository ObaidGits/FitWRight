"""Engine/dialect selection tests for the portable data layer (ADR-13, C-1).

These lock in the runtime DB-portability fix: the async/sync engine builders
select their dialect from the *resolved database URL* (not a hardcoded SQLite
path), SQLite keeps its PRAGMAs while Postgres does not, Postgres pooling honors
``db_pool_size``/``db_use_pooler`` (transaction-pooler-safe), and — the crux of
audit finding C-1 — ``Database`` actually consumes ``effective_database_url``.
"""

from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.pool import NullPool, QueuePool

from app import db_engine
from app.config import settings
from app.database import Database
from app.db_engine import (
    _apply_sqlite_pragmas,
    is_sqlite_url,
    make_async_engine,
    make_sync_engine,
    resolve_database_url,
)

pytestmark = pytest.mark.unit


class TestUrlResolution:
    def test_path_builds_sqlite_urls(self, tmp_path):
        p = tmp_path / "x.db"
        assert resolve_database_url(p, async_=True) == f"sqlite+aiosqlite:///{p}"
        assert resolve_database_url(p, async_=False) == f"sqlite:///{p}"

    def test_bare_postgres_normalized_to_drivers(self):
        url = "postgresql://user:pw@host:5432/db"
        assert resolve_database_url(url, async_=True) == "postgresql+asyncpg://user:pw@host:5432/db"
        assert resolve_database_url(url, async_=False) == "postgresql+psycopg://user:pw@host:5432/db"

    def test_postgres_shorthand_and_psycopg2_normalized(self):
        assert resolve_database_url("postgres://h/db", async_=True) == "postgresql+asyncpg://h/db"
        assert (
            resolve_database_url("postgresql+psycopg2://h/db", async_=False)
            == "postgresql+psycopg://h/db"
        )

    def test_sqlite_url_pinned_to_correct_driver(self):
        assert resolve_database_url("sqlite:////tmp/x.db", async_=True) == "sqlite+aiosqlite:////tmp/x.db"
        assert (
            resolve_database_url("sqlite+aiosqlite:////tmp/x.db", async_=False)
            == "sqlite:////tmp/x.db"
        )

    def test_is_sqlite_url(self):
        assert is_sqlite_url("sqlite+aiosqlite:///x.db")
        assert not is_sqlite_url("postgresql+asyncpg://h/db")


class TestSqliteEngine:
    def test_sync_engine_dialect_and_pragmas_registered(self, tmp_path):
        engine = make_sync_engine(tmp_path / "x.db")
        try:
            assert engine.dialect.name == "sqlite"
            # PRAGMA listener attached for SQLite.
            assert event.contains(engine, "connect", _apply_sqlite_pragmas)
            with engine.connect() as conn:
                fk = conn.exec_driver_sql("PRAGMA foreign_keys").scalar()
                jm = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
            assert fk == 1
            assert str(jm).lower() == "wal"
        finally:
            engine.dispose()

    def test_async_engine_dialect_and_pragmas_registered(self, tmp_path):
        engine = make_async_engine(tmp_path / "x.db")
        assert engine.dialect.name == "sqlite"
        assert engine.dialect.driver == "aiosqlite"
        assert event.contains(engine.sync_engine, "connect", _apply_sqlite_pragmas)


class TestPostgresEngine:
    _URL = "postgresql://user:pw@host:5432/db"

    def test_async_engine_uses_asyncpg_without_pragmas(self):
        engine = make_async_engine(self._URL)
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "asyncpg"
        # No SQLite PRAGMA hook on Postgres.
        assert not event.contains(engine.sync_engine, "connect", _apply_sqlite_pragmas)

    def test_sync_engine_uses_psycopg_without_pragmas(self):
        engine = make_sync_engine(self._URL)
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg"
        assert not event.contains(engine, "connect", _apply_sqlite_pragmas)

    def test_pooler_mode_is_transaction_safe(self, monkeypatch):
        """Neon/PgBouncer pooling: WARM client pool + server-side prepared
        statements disabled.

        The transaction-safety property (no server-side prepared statements —
        unsafe when pgbouncer multiplexes a connection across backends) is
        preserved via the connect_args, NOT via NullPool. We now keep a warm
        client-side pool (perf: avoids a full TCP+TLS+startup reconnect per DB
        operation to the external pooler); pgbouncer is designed to hold many
        persistent client connections and multiplex them.
        """
        monkeypatch.setattr(settings, "db_use_pooler", True)
        monkeypatch.setattr(settings, "db_pool_size", 5)
        async_engine = make_async_engine(self._URL)
        sync_engine = make_sync_engine(self._URL)

        # No longer NullPool — a warm, sized pool is kept.
        assert not isinstance(async_engine.pool, NullPool)
        assert not isinstance(sync_engine.pool, NullPool)
        assert async_engine.pool.size() == 5
        assert sync_engine.pool.size() == 5
        assert async_engine.dialect.driver == "asyncpg"
        assert sync_engine.dialect.driver == "psycopg"

        # Transaction-pool safety is still enforced via connect_args:
        async_opts = db_engine._pg_async_options()
        assert async_opts["connect_args"]["statement_cache_size"] == 0
        assert async_opts["connect_args"]["prepared_statement_cache_size"] == 0
        assert callable(async_opts["connect_args"]["prepared_statement_name_func"])
        assert async_opts["pool_pre_ping"] is True

        sync_opts = db_engine._pg_sync_options()
        assert sync_opts["connect_args"]["prepare_threshold"] is None
        assert sync_opts["pool_pre_ping"] is True

    def test_direct_mode_uses_sized_pool(self, monkeypatch):
        monkeypatch.setattr(settings, "db_use_pooler", False)
        monkeypatch.setattr(settings, "db_pool_size", 7)
        async_engine = make_async_engine(self._URL)
        sync_engine = make_sync_engine(self._URL)
        assert isinstance(async_engine.pool, QueuePool)
        assert isinstance(sync_engine.pool, QueuePool)
        assert async_engine.pool.size() == 7
        assert sync_engine.pool.size() == 7


class TestDatabaseConsumesEffectiveUrl:
    """Audit C-1: the runtime must actually wire to ``effective_database_url``."""

    def test_local_default_resolves_sqlite(self):
        db = Database()
        assert is_sqlite_url(db._async_url)
        assert is_sqlite_url(db._sync_url)
        assert db.db_path is not None  # local SQLite file present

    def test_postgres_database_url_is_consumed(self, monkeypatch):
        monkeypatch.setattr(settings, "database_url", "postgresql://user:pw@host:5432/db")
        db = Database()
        assert db._async_url == "postgresql+asyncpg://user:pw@host:5432/db"
        assert db._sync_url == "postgresql+psycopg://user:pw@host:5432/db"
        # Hosted has no local database file to create.
        assert db.db_path is None

    def test_explicit_path_override_wins(self, tmp_path):
        db = Database(db_path=tmp_path / "override.db")
        assert db._async_url == f"sqlite+aiosqlite:///{tmp_path / 'override.db'}"
        assert db.db_path == tmp_path / "override.db"


class TestInitModelsSyncGuard:
    def test_init_models_sync_noop_on_non_sqlite(self):
        """On Postgres, schema is Alembic-owned; init_models_sync must not touch it."""
        engine = make_sync_engine(TestPostgresEngine._URL)
        # Must return without connecting or issuing DDL (host is unreachable, so
        # any attempt to connect would raise). A clean return proves the guard.
        assert db_engine.init_models_sync(engine) is None
