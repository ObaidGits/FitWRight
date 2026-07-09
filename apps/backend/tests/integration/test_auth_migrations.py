"""Migration-chain tests for the P1 auth foundation (Task 1.2–1.4, 10.3).

These run the real Alembic chain (0001 → **0006**) against a **seeded throwaway
copy** of the schema — never the developer's real database. They assert the
core guarantees from the design's "Migration plan" and Property 7:

- data preservation: every pre-existing owned row survives upgrade *and*
  downgrade (R14.1, R14.2);
- backfill: a bootstrap owner (admin/active/verified) is created and every owned
  row + api_key is assigned to it, idempotently (R10.5, R14.1);
- enforcement: ``user_id`` becomes NOT NULL, the single-master invariant is
  per-user, and ``api_keys`` becomes per-user (R10.4);
- reversibility: each step has a working downgrade with no owned-row loss on the
  way down (R14.2).

The chain is exercised through Alembic's public command API against a temp-file
SQLite database selected via ``ALEMBIC_DATABASE_URL`` (the same override the ops
runbook uses to migrate a copy before touching production).
"""

import sqlite3

import pytest
from alembic import command
from alembic.config import Config

from app.config import settings

pytestmark = pytest.mark.integration

# Owner identity used by the backfill under test.
_OWNER_EMAIL_RAW = "Owner@Example.COM"
_OWNER_EMAIL_NORMALIZED = "owner@example.com"
_OWNER_PASSWORD = "correct horse battery staple 123"

_OWNED_TABLES = ("resumes", "jobs", "improvements", "applications", "api_keys")

# Representative pre-auth data seeded at the 0001 baseline.
_SEED_SQL = """
INSERT INTO resumes (resume_id,content,content_type,is_master,processing_status,created_at,updated_at)
  VALUES ('r1','# Master','md',1,'ready','2024-01-01T00:00:00+00:00','2024-01-01T00:00:00+00:00');
INSERT INTO resumes (resume_id,content,content_type,is_master,processing_status,created_at,updated_at)
  VALUES ('r2','# Tailored','md',0,'ready','2024-01-02T00:00:00+00:00','2024-01-02T00:00:00+00:00');
INSERT INTO jobs (job_id,content,created_at,metadata_json)
  VALUES ('j1','Engineer role','2024-01-01T00:00:00+00:00','{}');
INSERT INTO improvements (request_id,original_resume_id,tailored_resume_id,job_id,improvements,created_at)
  VALUES ('imp1','r1','r2','j1','[]','2024-01-03T00:00:00+00:00');
INSERT INTO applications (application_id,job_id,resume_id,status,position,created_at,updated_at)
  VALUES ('a1','j1','r2','applied',0,'2024-01-03T00:00:00+00:00','2024-01-03T00:00:00+00:00');
INSERT INTO api_keys (provider,ciphertext,updated_at)
  VALUES ('openai','ct-openai','2024-01-01T00:00:00+00:00');
INSERT INTO api_keys (provider,ciphertext,updated_at)
  VALUES ('google','ct-google','2024-01-01T00:00:00+00:00');
"""

_EXPECTED_COUNTS = {
    "resumes": 2,
    "jobs": 1,
    "improvements": 1,
    "applications": 1,
    "api_keys": 2,
}


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in _OWNED_TABLES
    }


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    """Point Alembic at a throwaway SQLite copy and set the bootstrap owner.

    ``ALEMBIC_DATABASE_URL`` overrides the resolved app URL (see ``alembic/env.py``)
    so no test can ever touch the real dev database. The owner is injected on the
    live ``settings`` singleton that migration 0004 reads.
    """
    db_path = tmp_path / "migration_copy.db"
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setattr(settings, "owner_email", _OWNER_EMAIL_RAW, raising=False)
    monkeypatch.setattr(settings, "owner_password", _OWNER_PASSWORD, raising=False)

    cfg = Config(str(_alembic_ini_path()))
    cfg.attributes["_db_path"] = str(db_path)
    return cfg


def _alembic_ini_path() -> "object":
    from pathlib import Path

    # tests/integration/ -> apps/backend/alembic.ini
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def _connect(cfg: Config) -> sqlite3.Connection:
    conn = sqlite3.connect(cfg.attributes["_db_path"])
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_baseline(cfg: Config) -> dict[str, int]:
    """Upgrade to the 0001 baseline and seed representative pre-auth data."""
    command.upgrade(cfg, "0001")
    conn = _connect(cfg)
    try:
        conn.executescript(_SEED_SQL)
        conn.commit()
        return _row_counts(conn)
    finally:
        conn.close()


class TestMigrationChain:
    def test_upgrade_head_preserves_all_rows(self, alembic_cfg):
        before = _seed_baseline(alembic_cfg)
        assert before == _EXPECTED_COUNTS

        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            assert _row_counts(conn) == before
        finally:
            conn.close()

    def test_backfill_creates_owner_and_assigns_rows(self, alembic_cfg):
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            owners = conn.execute(
                "SELECT id, email, role, status, email_verified_at, password_hash "
                "FROM users"
            ).fetchall()
            assert len(owners) == 1
            owner_id, email, role, status, verified_at, password_hash = owners[0]
            assert email == _OWNER_EMAIL_NORMALIZED  # NFKC + lowercase + trim
            assert role == "admin"
            assert status == "active"
            assert verified_at is not None
            # OWNER_PASSWORD was set -> an Argon2id hash is stored (never plaintext).
            assert password_hash and password_hash.startswith("$argon2")
            assert _OWNER_PASSWORD not in password_hash

            for table in _OWNED_TABLES:
                unassigned = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NOT ?", (owner_id,)
                ).fetchone()[0]
                assert unassigned == 0, f"{table} has rows not assigned to the owner"
        finally:
            conn.close()

    def test_owner_password_null_when_unset(self, alembic_cfg, monkeypatch):
        # OAuth-only bootstrap: no OWNER_PASSWORD -> password_hash stays NULL.
        monkeypatch.setattr(settings, "owner_password", "", raising=False)
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            password_hash = conn.execute(
                "SELECT password_hash FROM users"
            ).fetchone()[0]
            assert password_hash is None
        finally:
            conn.close()

    def test_enforced_constraints_after_head(self, alembic_cfg):
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            api_keys_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='api_keys'"
            ).fetchone()[0]
            assert "PRIMARY KEY (user_id, provider)" in api_keys_sql

            master_idx_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='ux_resumes_single_master'"
            ).fetchone()[0]
            assert "user_id" in master_idx_sql and "is_master" in master_idx_sql

            apps_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='applications'"
            ).fetchone()[0]
            assert "uq_application_user_job_resume" in apps_sql
        finally:
            conn.close()

    def test_user_id_not_null_enforced(self, alembic_cfg):
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO resumes "
                    "(resume_id,content,content_type,is_master,processing_status,"
                    " created_at,updated_at) "
                    "VALUES ('rX','x','md',0,'ready','t','t')"
                )
                conn.commit()
        finally:
            conn.close()

    def test_single_master_is_per_user(self, alembic_cfg):
        """Property 2: at most one master resume *per user*."""
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            owner_id = conn.execute("SELECT id FROM users").fetchone()[0]

            # A second master for the SAME user violates the partial-unique index.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO resumes "
                    "(resume_id,content,content_type,is_master,processing_status,"
                    " created_at,updated_at,user_id) "
                    "VALUES ('rDup','x','md',1,'ready','t','t',?)",
                    (owner_id,),
                )
                conn.commit()
            conn.rollback()

            # A master for a DIFFERENT user is allowed (invariant is per-user).
            conn.execute(
                "INSERT INTO users (id,email,name,role,status,mfa_enrolled,created_at,updated_at) "
                "VALUES ('u2','second@example.com','Second','user','active',0,'t','t')"
            )
            conn.execute(
                "INSERT INTO resumes "
                "(resume_id,content,content_type,is_master,processing_status,"
                " created_at,updated_at,user_id) "
                "VALUES ('rU2','x','md',1,'ready','t','t','u2')"
            )
            conn.commit()
            masters = conn.execute(
                "SELECT COUNT(*) FROM resumes WHERE is_master=1"
            ).fetchone()[0]
            assert masters == 2  # one per user
        finally:
            conn.close()


class TestEmailChangeTokens0006:
    """Migration 0006 (email_change_tokens) sign-off — presence + reversibility."""

    def test_email_change_tokens_present_after_head(self, alembic_cfg):
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='email_change_tokens'"
            ).fetchone()
            assert sql is not None, "0006 did not create email_change_tokens"
            create_sql = sql[0]
            # Hashed single-use TTL token keyed by token_hash, carries new_email.
            assert "PRIMARY KEY (token_hash)" in create_sql
            assert "new_email" in create_sql
            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(email_change_tokens)"
                ).fetchall()
            }
            assert {"token_hash", "user_id", "new_email", "expires_at", "used_at"} <= cols
            # The supporting indexes exist (user_id + expires_at, for reaping).
            indexes = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='email_change_tokens'"
                ).fetchall()
            }
            assert "ix_email_change_tokens_user_id" in indexes
            assert "ix_email_change_tokens_expires_at" in indexes
        finally:
            conn.close()

    def test_0006_is_reversible_and_preserves_owned_rows(self, alembic_cfg):
        before = _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        # Reverse just 0006 → the table is gone, owned rows untouched.
        command.downgrade(alembic_cfg, "0005")
        conn = _connect(alembic_cfg)
        try:
            assert (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='email_change_tokens'"
                ).fetchone()
                is None
            )
            assert _row_counts(conn) == before
        finally:
            conn.close()

        # Re-apply 0006 → table returns (forward/back is a clean round-trip).
        command.upgrade(alembic_cfg, "head")
        conn = _connect(alembic_cfg)
        try:
            assert (
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='email_change_tokens'"
                ).fetchone()
                is not None
            )
            assert _row_counts(conn) == before
        finally:
            conn.close()


class TestReversibility:
    def test_full_chain_up_head_then_down_base_on_seeded_copy(self, alembic_cfg):
        """Property 7 end-to-end: up→head preserves 100% of owned rows across the
        WHOLE chain (incl. 0006), then down→base tears everything down cleanly.

        Owned rows are preserved on the way down until 0001→base finally drops
        the owned tables themselves (the baseline schema is removed at base).
        """
        before = _seed_baseline(alembic_cfg)

        # Up to the very head of the chain (0006) — nothing lost.
        command.upgrade(alembic_cfg, "head")
        conn = _connect(alembic_cfg)
        try:
            assert _row_counts(conn) == before
        finally:
            conn.close()

        # Down to the 0001 baseline — every owned row still survives (only the
        # auth/kv/token tables + the user_id scoping are reversed above 0001).
        command.downgrade(alembic_cfg, "0001")
        conn = _connect(alembic_cfg)
        try:
            assert _row_counts(conn) == before
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Auth-era tables are gone; the original owned tables remain.
            assert "users" not in tables and "email_change_tokens" not in tables
            assert {"resumes", "jobs", "applications"} <= tables
        finally:
            conn.close()

        # All the way down to base — the entire schema is torn down cleanly.
        command.downgrade(alembic_cfg, "base")
        conn = _connect(alembic_cfg)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "resumes" not in tables and "users" not in tables
        finally:
            conn.close()

    def test_downgrade_to_0002_preserves_rows_and_removes_owner(self, alembic_cfg):
        before = _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        # Reverse enforcement (0005), backfill (0004) and scoping columns (0003).
        command.downgrade(alembic_cfg, "0002")

        conn = _connect(alembic_cfg)
        try:
            assert _row_counts(conn) == before  # no owned-row loss on the way down
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
            resume_cols = [
                row[1] for row in conn.execute("PRAGMA table_info(resumes)").fetchall()
            ]
            assert "user_id" not in resume_cols  # 0003 fully reversed
        finally:
            conn.close()

    def test_downgrade_to_base_succeeds(self, alembic_cfg):
        _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")
        command.downgrade(alembic_cfg, "base")

        conn = _connect(alembic_cfg)
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            # Every table created by the chain is gone (except alembic's bookkeeping).
            assert "users" not in tables
            assert "resumes" not in tables
            assert "kv" not in tables
        finally:
            conn.close()

    def test_backfill_is_idempotent_across_reruns(self, alembic_cfg):
        before = _seed_baseline(alembic_cfg)
        command.upgrade(alembic_cfg, "head")

        # Re-run the backfill path: reverse to just before it, then re-apply.
        command.downgrade(alembic_cfg, "0003")
        command.upgrade(alembic_cfg, "head")

        conn = _connect(alembic_cfg)
        try:
            assert _row_counts(conn) == before
            # Still exactly one owner (no duplicate bootstrap user).
            assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
            owner_id = conn.execute("SELECT id FROM users").fetchone()[0]
            for table in _OWNED_TABLES:
                unassigned = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id IS NOT ?", (owner_id,)
                ).fetchone()[0]
                assert unassigned == 0
        finally:
            conn.close()
