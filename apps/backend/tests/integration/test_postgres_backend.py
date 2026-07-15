"""Real-Postgres validation for the portable data layer (ADR-13, audit C-1).

The audit flagged that the runtime was never exercised against Postgres. This
suite closes that gap end-to-end against a **real** Postgres:

1. run the Alembic chain ``upgrade head`` on Postgres (the hosted schema owner);
2. perform a scoped CRUD round-trip through the async ``Database`` facade wired
   to the Postgres URL (asyncpg engine + psycopg sync engine) — proving the
   runtime actually talks to Postgres, not the local SQLite file;
3. run ``downgrade base`` to prove the chain reverses on Postgres too.

It is **best-effort and gated**: it uses ``TEST_DATABASE_URL`` if set, otherwise
spins up a disposable Postgres via Docker, and **skips with a clear reason** if
neither is available (no Docker, no image pull, unreachable server).
"""

import os
import shutil
import subprocess
import time
import uuid

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

pytestmark = pytest.mark.integration

_PG_IMAGE = "postgres:16-alpine"
_PG_PASSWORD = "fitwright_test"
_PG_DB = "fitwright_test"
_READY_TIMEOUT_S = 45


def _alembic_ini_path() -> str:
    from pathlib import Path

    # tests/integration/ -> apps/backend/alembic.ini
    return str(Path(__file__).resolve().parents[2] / "alembic.ini")


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _wait_until_ready(url: str) -> bool:
    """Poll a sync psycopg connection until the server accepts queries."""
    import psycopg

    deadline = time.time() + _READY_TIMEOUT_S
    last_err: Exception | None = None
    # psycopg wants a libpq URL (no SQLAlchemy driver suffix).
    libpq_url = url.replace("postgresql+psycopg://", "postgresql://", 1)
    while time.time() < deadline:
        try:
            with psycopg.connect(libpq_url, connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return True
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last_err = exc
            time.sleep(1.0)
    if last_err is not None:
        print(f"Postgres never became ready: {last_err}")
    return False


@pytest.fixture(scope="module")
def pg_url() -> str:
    """A reachable Postgres URL, or skip with a clear reason.

    Precedence: an explicit ``TEST_DATABASE_URL`` (CI/dev supplies a server) →
    a disposable Docker container → skip.
    """
    explicit = os.environ.get("TEST_DATABASE_URL")
    if explicit:
        if not _wait_until_ready(explicit):
            pytest.skip(f"TEST_DATABASE_URL set but server not reachable: {explicit}")
        yield explicit
        return

    if not _docker_available():
        pytest.skip(
            "No TEST_DATABASE_URL and Docker is unavailable; skipping real-Postgres "
            "validation (set TEST_DATABASE_URL to run against an existing server)."
        )

    container = f"fitwright-pg-{uuid.uuid4().hex[:12]}"
    run = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", container,
            "-e", f"POSTGRES_PASSWORD={_PG_PASSWORD}",
            "-e", f"POSTGRES_DB={_PG_DB}",
            "-P",
            _PG_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"Could not start Postgres container (docker run failed): {run.stderr.strip()}")

    try:
        port_proc = subprocess.run(
            ["docker", "port", container, "5432/tcp"],
            capture_output=True,
            text=True,
        )
        if port_proc.returncode != 0 or not port_proc.stdout.strip():
            pytest.skip(f"Could not resolve mapped Postgres port: {port_proc.stderr.strip()}")
        # e.g. "0.0.0.0:49153" (may be multiple lines for v4/v6) -> take the port.
        host_port = port_proc.stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        url = f"postgresql://postgres:{_PG_PASSWORD}@127.0.0.1:{host_port}/{_PG_DB}"

        if not _wait_until_ready(url):
            pytest.skip("Postgres container started but never became ready in time.")
        yield url
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True)


@pytest.fixture
def alembic_cfg_pg(pg_url, monkeypatch):
    """Alembic config pointed at the real Postgres via ALEMBIC_DATABASE_URL."""
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", pg_url)
    monkeypatch.setattr(settings, "owner_email", "owner@example.com", raising=False)
    monkeypatch.setattr(settings, "owner_password", "correct horse battery staple 123", raising=False)
    return Config(_alembic_ini_path())


class TestPostgresMigrationChain:
    def test_upgrade_head_then_downgrade_base(self, alembic_cfg_pg, pg_url):
        """The full chain applies and reverses cleanly on real Postgres."""
        import psycopg

        command.upgrade(alembic_cfg_pg, "head")

        libpq = pg_url.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(libpq) as conn:
            # Core tables exist after head.
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                ).fetchall()
            }
            assert {"users", "resumes", "jobs", "applications", "api_keys"} <= tables
            # A bootstrap owner was created by migration 0004.
            owner_count = conn.execute(
                "SELECT COUNT(*) FROM users WHERE email='owner@example.com'"
            ).fetchone()[0]
            assert owner_count == 1

        command.downgrade(alembic_cfg_pg, "base")
        with psycopg.connect(libpq) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                ).fetchall()
            }
            assert "users" not in tables
            assert "resumes" not in tables

    def test_admin_schema_and_concurrent_indexes_on_postgres(self, alembic_cfg_pg, pg_url):
        """P2 Admin migrations 0007/0008 apply on real Postgres (audit M4).

        Verifies the admin columns + ``metrics_daily`` + the ``CONCURRENTLY``
        ``text_pattern_ops`` search indexes and the ``last_seen_at`` index are
        actually created on Postgres (the SQLite migration suite cannot exercise
        the ``CONCURRENTLY``/opclass path), and that the chain still reverses.
        """
        import psycopg

        command.upgrade(alembic_cfg_pg, "head")
        libpq = pg_url.replace("postgresql+psycopg://", "postgresql://", 1)
        with psycopg.connect(libpq) as conn:
            cols = {
                row[0]
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='users'"
                ).fetchall()
            }
            assert {"deleted_at", "resume_count", "application_count", "last_active_at"} <= cols

            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                ).fetchall()
            }
            assert "metrics_daily" in tables

            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT indexname FROM pg_indexes WHERE schemaname='public'"
                ).fetchall()
            }
            assert "ix_users_email_pattern" in indexes
            assert "ix_users_name_lower_pattern" in indexes
            assert "ix_sessions_last_seen_at" in indexes
            assert "ix_users_role_status" in indexes
            assert "ix_users_created_at_id" in indexes

            # The pattern index uses the text_pattern_ops opclass (prefix LIKE).
            opclass = conn.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname='ix_users_email_pattern'"
            ).fetchone()[0]
            assert "text_pattern_ops" in opclass

        # Reverse the two admin migrations and re-apply (clean round-trip).
        command.downgrade(alembic_cfg_pg, "0006")
        with psycopg.connect(libpq) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
                ).fetchall()
            }
            assert "metrics_daily" not in tables
        command.upgrade(alembic_cfg_pg, "head")
        command.downgrade(alembic_cfg_pg, "base")


class TestPostgresRuntimeCrud:
    async def test_scoped_crud_round_trip_on_postgres(self, alembic_cfg_pg, pg_url):
        """The runtime Database facade performs real scoped CRUD on Postgres.

        This is the crux of audit C-1: with a Postgres URL, the app must read and
        write Postgres — not the local SQLite file.
        """
        import asyncio

        from app.database import Database
        from app.models import User

        # Alembic's online migrations call ``asyncio.run`` internally, which
        # cannot run inside this test's running event loop — run it in a thread.
        await asyncio.to_thread(command.upgrade, alembic_cfg_pg, "head")

        database = Database(db_path=pg_url)
        try:
            # The async engine must be asyncpg (Postgres), not SQLite.
            assert database.async_engine.dialect.name == "postgresql"
            assert database.async_engine.dialect.driver == "asyncpg"
            assert database.db_path is None  # no local SQLite file

            # Create a user to satisfy the owned-row FK, then round-trip a resume.
            user_id = str(uuid.uuid4())
            async with database.session_factory() as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"crud-{user_id}@example.com",
                        name="PG CRUD",
                        role="user",
                        status="active",
                    )
                )
                await session.commit()

            created = await database.create_resume(user_id, content="# On Postgres")
            fetched = await database.get_resume(user_id, created["resume_id"])
            assert fetched is not None
            assert fetched["content"] == "# On Postgres"
            assert [r["resume_id"] for r in await database.list_resumes(user_id)] == [
                created["resume_id"]
            ]

            # Encrypted api_keys sync (psycopg) hot path round-trips on Postgres.
            database.set_api_key_ciphertext(user_id, "openai", "ct-pg")
            assert database.get_api_key_ciphertexts(user_id) == {"openai": "ct-pg"}

            # Cross-user isolation still holds on Postgres.
            other = str(uuid.uuid4())
            async with database.session_factory() as session:
                session.add(
                    User(
                        id=other,
                        email=f"other-{other}@example.com",
                        name="Other",
                        role="user",
                        status="active",
                    )
                )
                await session.commit()
            assert await database.get_resume(other, created["resume_id"]) is None
        finally:
            await database.close()
        # Leave the schema in place for other module tests; teardown drops the DB.
