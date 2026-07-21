"""ORM-level tests for the P1 auth-foundation models (Task 1.1).

Complements ``test_auth_migrations`` (the Alembic path) by checking the ORM
metadata and the ``create_all`` path that backs local zero-config boot. Both
paths must produce an equivalent schema, so these assert the new tables, their
columns/indexes, and the nullable ``user_id`` added to the owned tables.
"""

import sqlalchemy as sa

from app.db_engine import make_sync_engine
from app.models import (
    AuditLog,
    Base,
    EmailVerificationToken,
    OAuthIdentity,
    PasswordResetToken,
    Session,
    User,
)

_NEW_TABLES = {
    "users",
    "oauth_identities",
    "sessions",
    "audit_log",
    "email_verification_tokens",
    "password_reset_tokens",
}
_OWNED_TABLES = ("resumes", "jobs", "improvements", "applications", "api_keys")


class TestMetadata:
    def test_new_auth_tables_registered(self):
        assert _NEW_TABLES <= set(Base.metadata.tables)

    def test_user_columns(self):
        cols = User.__table__.columns
        assert cols["password_hash"].nullable is True  # OAuth-only accounts
        assert cols["mfa_enrolled"].nullable is False  # reserved MFA flag
        assert cols["role"].default.arg == "user"
        assert cols["status"].default.arg == "active"
        # Email is uniquely indexed (normalized uniqueness).
        email_indexes = [ix for ix in User.__table__.indexes if ix.name == "ux_users_email"]
        assert email_indexes and email_indexes[0].unique is True

    def test_oauth_identity_composite_pk(self):
        pk = [c.name for c in OAuthIdentity.__table__.primary_key.columns]
        assert pk == ["provider", "subject"]

    def test_session_token_hash_unique_and_readiness_columns(self):
        cols = Session.__table__.columns
        # MFA/step-up readiness columns exist.
        assert "aal" in cols and "step_up_at" in cols and "remember_me" in cols
        assert "device_label" in cols and "ip_hash" in cols
        token_idx = [ix for ix in Session.__table__.indexes if ix.name == "ux_sessions_token_hash"]
        assert token_idx and token_idx[0].unique is True

    def test_audit_log_is_append_only_shape(self):
        cols = AuditLog.__table__.columns
        # actor/target are plain nullable columns (no FK) so audit survives deletes.
        assert cols["actor_user_id"].nullable is True
        assert not cols["actor_user_id"].foreign_keys
        assert "meta" in cols

    def test_token_tables_hashed_pk(self):
        assert list(EmailVerificationToken.__table__.primary_key.columns.keys()) == ["token_hash"]
        assert list(PasswordResetToken.__table__.primary_key.columns.keys()) == ["token_hash"]

    def test_owned_tables_have_user_id_fk(self):
        # Document tables keep ``user_id`` nullable during the P1 transition;
        # ``api_keys`` is reconciled to the enforced per-user shape (composite
        # PK ``(user_id, provider)`` - R10.6), so its ``user_id`` is NOT NULL.
        for table in _OWNED_TABLES:
            col = Base.metadata.tables[table].columns["user_id"]
            assert col.foreign_keys, f"{table}.user_id should reference users.id"
            if table == "api_keys":
                assert col.nullable is False, "api_keys.user_id is part of the per-user PK"
                assert col.primary_key is True
            else:
                assert col.nullable is True, f"{table}.user_id should be nullable (transitional)"


class TestCreateAll:
    def test_create_all_builds_full_schema(self, tmp_path):
        """Local zero-config boot (create_all) builds every table, incl. auth."""
        engine = make_sync_engine(tmp_path / "create_all.db")
        try:
            Base.metadata.create_all(engine)
            insp = sa.inspect(engine)
            tables = set(insp.get_table_names())
            assert _NEW_TABLES <= tables
            for table in _OWNED_TABLES:
                cols = {c["name"] for c in insp.get_columns(table)}
                assert "user_id" in cols
        finally:
            engine.dispose()

    def test_create_all_owned_user_id_nullable_keeps_zero_config(self, tmp_path):
        """A resume can still be created without a user_id (transitional state)."""
        engine = make_sync_engine(tmp_path / "zeroconf.db")
        try:
            Base.metadata.create_all(engine)
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO resumes "
                        "(resume_id,content,content_type,is_master,processing_status,"
                        " created_at,updated_at) "
                        "VALUES ('r1','x','md',0,'ready','t','t')"
                    )
                )
                count = conn.execute(sa.text("SELECT COUNT(*) FROM resumes")).scalar()
            assert count == 1
        finally:
            engine.dispose()
