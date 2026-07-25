"""Engine/dialect selection tests for the portable data layer (ADR-13, C-1).

These lock in the runtime DB-portability fix: the async/sync engine builders
select their dialect from the *resolved database URL* (not a hardcoded SQLite
path), SQLite keeps its PRAGMAs while Postgres does not, Postgres pooling honors
``db_pool_size``/``db_use_pooler`` (transaction-pooler-safe), and - the crux of
audit finding C-1 - ``Database`` actually consumes ``effective_database_url``.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
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

        The transaction-safety property (no server-side prepared statements -
        unsafe when pgbouncer multiplexes a connection across backends) is
        preserved via the connect_args, NOT via NullPool. We now keep a warm
        client-side pool (perf: avoids a full TCP+TLS+startup reconnect per DB
        operation to the external pooler); pgbouncer is designed to hold many
        persistent client connections and multiplex them.
        """
        monkeypatch.setattr(settings, "db_use_pooler", True)
        monkeypatch.setattr(settings, "db_pool_size", 5)
        monkeypatch.setattr(settings, "db_sync_pool_size", 2)
        async_engine = make_async_engine(self._URL)
        sync_engine = make_sync_engine(self._URL)

        # No longer NullPool - a warm, sized pool is kept.
        assert not isinstance(async_engine.pool, NullPool)
        assert not isinstance(sync_engine.pool, NullPool)
        assert async_engine.pool.size() == 5
        # D1: the sync engine (encrypted api_keys) is a COLD path behind the
        # decrypted-key cache, so it keeps a small idle pool while overflow rides
        # up to db_pool_size for cold-cache bursts.
        assert sync_engine.pool.size() == 2
        assert db_engine._pg_sync_options()["max_overflow"] == 5
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
        monkeypatch.setattr(settings, "db_sync_pool_size", 3)
        async_engine = make_async_engine(self._URL)
        sync_engine = make_sync_engine(self._URL)
        assert isinstance(async_engine.pool, QueuePool)
        assert isinstance(sync_engine.pool, QueuePool)
        assert async_engine.pool.size() == 7
        # D1: sync engine keeps a small idle pool; overflow rides to db_pool_size.
        assert sync_engine.pool.size() == 3
        assert db_engine._pg_sync_options()["max_overflow"] == 7


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
    def test_concurrent_sqlite_initializers_are_serialized(self, tmp_path):
        db_path = tmp_path / "concurrent-init.db"
        barrier = threading.Barrier(6)

        def initialize() -> None:
            engine = make_sync_engine(db_path)
            try:
                barrier.wait(timeout=5)
                db_engine.init_models_sync(engine)
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(initialize) for _ in range(6)]
            for future in futures:
                future.result(timeout=20)

        engine = make_sync_engine(db_path)
        try:
            with engine.connect() as conn:
                tables = {
                    row["name"]
                    for row in conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).mappings()
                }
            assert {"users", "resumes", "tailor_previews"} <= tables
            assert db_path.with_name(f"{db_path.name}.schema.lock").is_file()
        finally:
            engine.dispose()

    def test_repairs_all_non_unique_model_indexes_idempotently(self, tmp_path):
        """Old local tables gain lookup indexes without risking new uniqueness.

        ``create_all`` does not add indexes to an already-existing table. The
        reconciler must preserve its rows, restore ordinary/model expression
        indexes, skip unsafe unique indexes, and remain a no-op on a second run.
        """
        engine = make_sync_engine(tmp_path / "legacy-indexes.db")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL,
                        name TEXT NOT NULL
                    )
                    """
                )
                conn.exec_driver_sql(
                    "INSERT INTO users (id, email, name) VALUES (?, ?, ?)",
                    ("legacy-user", "legacy@example.com", "Legacy User"),
                )

            db_engine.init_models_sync(engine)
            with engine.connect() as conn:
                first_indexes = conn.exec_driver_sql(
                    'PRAGMA index_list("users")'
                ).mappings().all()
                preserved = conn.exec_driver_sql(
                    "SELECT id, email, name FROM users WHERE id = ?",
                    ("legacy-user",),
                ).one()

            first_names = {row["name"] for row in first_indexes}
            assert preserved == (
                "legacy-user",
                "legacy@example.com",
                "Legacy User",
            )
            assert {
                "ix_users_status",
                "ix_users_role_status",
                "ix_users_created_at_id",
                "ix_users_name_lower",
            } <= first_names
            # Startup healing must not enforce uniqueness against unknown legacy
            # data; explicit migrations own those constraints.
            assert "ux_users_email" not in first_names

            db_engine.init_models_sync(engine)
            with engine.connect() as conn:
                second_indexes = conn.exec_driver_sql(
                    'PRAGMA index_list("users")'
                ).mappings().all()
                row_count = conn.exec_driver_sql(
                    "SELECT COUNT(*) FROM users WHERE id = ?", ("legacy-user",)
                ).scalar_one()

            assert [row["name"] for row in second_indexes] == [
                row["name"] for row in first_indexes
            ]
            assert row_count == 1
        finally:
            engine.dispose()

    def test_warns_without_mutating_nonrepairable_sqlite_drift(
        self, tmp_path, caplog
    ):
        engine = make_sync_engine(tmp_path / "legacy-drift.db")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE resumes (
                        resume_id INTEGER,
                        user_id TEXT,
                        content TEXT NOT NULL
                    )
                    """
                )
                conn.exec_driver_sql(
                    "INSERT INTO resumes (resume_id, user_id, content) "
                    "VALUES (1, 'owner', 'preserve me')"
                )

            with caplog.at_level(logging.WARNING, logger="app.db_engine"):
                db_engine.init_models_sync(engine)

            messages = [record.getMessage() for record in caplog.records]
            resume_warning = next(
                message for message in messages if "drift on resumes" in message
            )
            assert "primary_key" in resume_warning
            assert "type resume_id" in resume_warning
            assert "missing_unique_index=ux_resumes_single_master" in resume_warning
            assert "missing_foreign_key=user_id->users.id" in resume_warning
            assert "preserve me" not in resume_warning

            with engine.connect() as conn:
                preserved = conn.exec_driver_sql(
                    "SELECT resume_id, user_id, content FROM resumes"
                ).one()
            assert preserved == (1, "owner", "preserve me")
        finally:
            engine.dispose()

    def test_fresh_sqlite_schema_has_no_nonrepairable_drift(self, tmp_path, caplog):
        engine = make_sync_engine(tmp_path / "fresh.db")
        try:
            with caplog.at_level(logging.WARNING, logger="app.db_engine"):
                db_engine.init_models_sync(engine)
            assert "non-repairable schema drift" not in caplog.text
        finally:
            engine.dispose()

    def test_init_models_sync_noop_on_non_sqlite(self):
        """On Postgres, schema is Alembic-owned; init_models_sync must not touch it."""
        engine = make_sync_engine(TestPostgresEngine._URL)
        # Must return without connecting or issuing DDL (host is unreachable, so
        # any attempt to connect would raise). A clean return proves the guard.
        assert db_engine.init_models_sync(engine) is None
