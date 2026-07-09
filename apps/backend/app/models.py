"""SQLAlchemy ORM models for FitWright.

A single declarative ``Base`` backs all tables (doc tables migrated from
TinyDB plus the new ``applications`` and ``api_keys`` tables). The facade in
``app/database.py`` converts ORM rows to plain dicts so the rest of the app
never sees ORM objects — preserving the TinyDB-era contracts.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _new_uuid() -> str:
    """Return a fresh UUID4 as a string (ids are stored as strings)."""
    return str(uuid4())


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Timestamps are stored as strings (not native datetimes) to preserve the
    TinyDB-era behavior: code compares them lexically and returns them to
    clients verbatim.
    """
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    """Declarative base shared by every table."""


class Resume(Base):
    """A resume document (master or tailored)."""

    __tablename__ = "resumes"

    resume_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owning user (ADR-4). Nullable during the P1 scoping rollout: migration
    # 0003 adds it nullable, 0004 backfills the bootstrap owner, 0005 enforces
    # NOT NULL on hosted. The app threads ``user_id`` through in a later wave;
    # until then it stays nullable so local zero-config boot keeps working.
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String, default="md")
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    processed_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    processing_status: Mapped[str] = mapped_column(String, default="pending")
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    outreach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    interview_prep: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    # original_markdown has *absence* semantics in the TinyDB era: the key was
    # omitted entirely when None. The facade reproduces that by only emitting
    # the key when this column is non-null.
    original_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # At most one master resume **per user** (R10.4, Property 2). Partial
        # unique index on ``(user_id, is_master)`` enforces the invariant at the
        # storage layer; the per-user ``_master_locks`` in the facade remain the
        # primary (race-free) mechanism. Reconciled to the enforced hosted shape
        # (migration 0005) now that Task 3 threads ``user_id`` through the repo.
        Index(
            "ux_resumes_single_master",
            "user_id",
            "is_master",
            unique=True,
            sqlite_where=text("is_master = 1"),
            postgresql_where=text("is_master = true"),
        ),
    )


class Job(Base):
    """A job description.

    Only the stable columns are first-class; everything the pipeline attaches
    dynamically (``job_keywords``, ``job_keywords_hash``, ``preview_hash``,
    ``preview_hashes``, ``preview_prompt_id``, ``company``, ``role``) lives in
    ``metadata_json``. The facade flattens that map to top-level keys on read
    and merges non-core keys into it on update, reproducing TinyDB semantics.
    """

    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owning user (ADR-4); see the note on ``Resume.user_id``.
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    content: Mapped[str] = mapped_column(Text)
    resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Improvement(Base):
    """A tailoring result linking an original resume, a tailored resume, and a job."""

    __tablename__ = "improvements"

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owning user (ADR-4); see the note on ``Resume.user_id``.
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    original_resume_id: Mapped[str] = mapped_column(String)
    tailored_resume_id: Mapped[str] = mapped_column(String, index=True)
    job_id: Mapped[str] = mapped_column(String)
    improvements: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class Application(Base):
    """A Kanban application-tracker card."""

    __tablename__ = "applications"
    __table_args__ = (
        # Concurrency-safe dedupe: a card is unique per (user, job, applied
        # resume). The app-level select-then-insert relies on this to collapse
        # races. Reconciled to the per-user enforced shape (migration 0005) now
        # that Task 3 threads ``user_id`` through the repository layer.
        UniqueConstraint(
            "user_id", "job_id", "resume_id", name="uq_application_user_job_resume"
        ),
    )

    application_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Owning user (ADR-4); see the note on ``Resume.user_id``.
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[str] = mapped_column(String, index=True)
    # The applied/tailored resume shown in the modal and opened by "Edit".
    resume_id: Mapped[str] = mapped_column(String, index=True)
    # Optional base resume the tailored one descends from (powers "stack" grouping).
    master_resume_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="applied", index=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    applied_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class ApiKey(Base):
    """An encrypted LLM provider API key.

    ``provider`` is the *key-store* provider name (e.g. ``google`` for the
    ``gemini`` LLM provider, via ``_PROVIDER_KEY_MAP``). Only ciphertext is
    stored; plaintext exists in memory only at call time.
    """

    __tablename__ = "api_keys"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    # Owning user (ADR-4). Keys are **per user** (R10.6): the primary key is the
    # composite ``(user_id, provider)`` so one user's provider key can never
    # serve another's LLM calls. Reconciled to the enforced hosted shape
    # (migration 0005) now that Task 3.3 threads per-user api-key resolution
    # through ``llm.py``. ``user_id`` is part of the PK and therefore NOT NULL;
    # the bootstrap owner (single-user local / migration 0004 hosted) owns any
    # pre-existing keys.
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    ciphertext: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


# ===========================================================================
# Auth foundation (P1 Multi-User Foundation) — new tables
# ===========================================================================
#
# These back authentication, sessions, RBAC, verification/reset, and the
# append-only audit log. All ids are UUID4 strings and all timestamps are
# zero-padded UTC ISO strings (lexically comparable), matching the TinyDB-era
# convention used by the document tables above. Created via ``create_all``
# locally (zero-config boot) and via Alembic ``0002`` on hosted Postgres — both
# paths produce the same schema.


class User(Base):
    """An application user (email/password and/or OAuth-linked).

    ``password_hash`` is nullable for OAuth-only accounts. ``email`` is stored
    already normalized (NFKC + lowercase + trim) and is globally unique. The
    bootstrap owner (migration 0004) is ``role=admin``, ``status=active`` and
    verified.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Null for OAuth-only accounts (no local password).
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False, default="user")
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    email_verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Reserved for MFA/WebAuthn readiness (R9.2); no enforcement in P1.
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ux_users_email", "email", unique=True),
        Index("ix_users_status", "status"),
    )


class OAuthIdentity(Base):
    """A verified external identity linked to a :class:`User`.

    Composite primary key ``(provider, subject)`` — a provider's stable ``sub``
    is unique within that provider. ``email_at_link`` records the provider email
    seen at link time for auditing.
    """

    __tablename__ = "oauth_identities"

    provider: Mapped[str] = mapped_column(String, primary_key=True)
    subject: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email_at_link: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (Index("ix_oauth_identities_user_id", "user_id"),)


class Session(Base):
    """A server-side session; the DB is the source of truth (KVStore caches it).

    Only ``sha256(raw token)`` is stored in ``token_hash`` — the raw token lives
    only in the ``__Host-`` cookie. ``csrf_secret`` derives the per-session CSRF
    cookie. ``aal``/``step_up_at`` back step-up ("sudo") and MFA readiness;
    ``remember_me`` selects the longer absolute cap; sliding expiry is driven by
    ``last_seen_at`` and bounded by ``expires_at``. A non-null ``revoked_at``
    means the session is dead.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    csrf_secret: Mapped[str] = mapped_column(String, nullable=False)
    aal: Mapped[str] = mapped_column(String, nullable=False, default="aal1")
    step_up_at: Mapped[str | None] = mapped_column(String, nullable=True)
    remember_me: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    device_label: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    last_seen_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ux_sessions_token_hash", "token_hash", unique=True),
        Index("ix_sessions_user_id", "user_id"),
        # Device list (active sessions per user).
        Index("ix_sessions_user_revoked", "user_id", "revoked_at"),
        # Reaper (batch-delete expired rows).
        Index("ix_sessions_expires_at", "expires_at"),
    )


class AuditLog(Base):
    """Append-only security audit trail (R16.2).

    ``actor_user_id``/``target_user_id`` are plain columns (no FK) so an audit
    row survives deletion of the referenced user. ``meta`` is a sanitized JSON
    blob (never secrets/PII beyond ``user_id``).
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    ts: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    actor_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    target_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event: Mapped[str] = mapped_column(String, nullable=False)
    ip_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_audit_log_ts", "ts"),
        Index("ix_audit_log_event_ts", "event", "ts"),
        Index("ix_audit_log_actor_ts", "actor_user_id", "ts"),
    )


class EmailVerificationToken(Base):
    """A hashed, single-use, TTL-bound email-verification token (R5.1).

    Stored as ``sha256(raw)`` (``token_hash`` is the PK); the raw token exists
    only in the emailed link. Issuing a new token invalidates prior unused ones
    for the user.
    """

    __tablename__ = "email_verification_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_email_verification_tokens_user_id", "user_id"),
        Index("ix_email_verification_tokens_expires_at", "expires_at"),
    )


class PasswordResetToken(Base):
    """A hashed, single-use, short-TTL password-reset token (R6.1).

    Same hashing/single-use rules as :class:`EmailVerificationToken`.
    """

    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
    )


class EmailChangeToken(Base):
    """A hashed, single-use, TTL-bound email-*change* token (R7.4).

    Backs the verify-before-switch email-change flow: when a user requests an
    email change (with a recent step-up), a token is issued to the **new**
    address and only its ``sha256`` is stored here alongside the pending
    ``new_email``. The account's primary ``email`` is swapped only after the new
    address is confirmed via this token, so the account never switches to an
    unverified address. Same hashing / single-use / prior-invalidation rules as
    :class:`EmailVerificationToken`; the extra ``new_email`` column is what
    distinguishes an email-change token from a plain verification token.
    """

    __tablename__ = "email_change_tokens"

    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # The address being switched to (already normalized), verified by this token.
    new_email: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_email_change_tokens_user_id", "user_id"),
        Index("ix_email_change_tokens_expires_at", "expires_at"),
    )
