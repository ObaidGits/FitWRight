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
    LargeBinary,
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
    # Persisted appearance for this resume (the chosen template + customization:
    # engine, page size, margins, spacing, fonts, accent, photo, etc. — the
    # frontend ``TemplateSettings`` shape). Nullable for backward compatibility:
    # a resume created before the template system falls back to the app default.
    # This is a rendering artifact, NOT resume content, so writing it never bumps
    # the optimistic-concurrency ``version`` (see ``update_resume``).
    template_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Optimistic-concurrency token (P4 Resilience R3.1). Bumped by every write
    # via an atomic single-row conditional UPDATE (version CAS): a write carries
    # the ``base_version`` it read; the server applies the change only when the
    # stored version still matches, otherwise returns 409 with the current
    # version+data. Defaults to 1; migration 0014 backfills existing rows.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
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
    # Server-generated storage key for the current avatar object (orphan GC keys
    # off this + avatar_url — P3 §H, R13). NULL ⇒ no stored object.
    avatar_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # Canonical profile-image metadata (Photo System, migration 0018). Only
    # metadata lives in the DB — never binary. ``avatar_checksum`` is the SHA-256
    # of the original upload (content-addressed dedup: a re-upload of the same
    # file is a no-op). Dimensions/aspect/colour drive responsive rendering,
    # skeletons, and CLS-free layout. All nullable; a pre-Photo-System avatar
    # simply has NULLs here until its next upload.
    avatar_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_checksum: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_format: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avatar_dominant_color: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_updated_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Extended profile (P3 §H, R14): optional, validated, reused to prefill
    # resumes. ``links`` is a small JSON list of ``{label, url}`` (host/scheme
    # validated, length-bounded at the service layer).
    headline: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    links: Mapped[list | None] = mapped_column(JSON, nullable=True)
    email_verified_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Reserved for MFA/WebAuthn readiness (R9.2); no enforcement in P1.
    mfa_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ---- P2 Admin (soft-delete grace period + denormalized usage counters) ----
    # Soft-delete marker (ADR admin §Deletion). NULL ⇒ live; a non-null iso ts
    # starts the grace period after which the PurgeJob irreversibly erases the
    # user. Indexed for the purge scan + the ``deleted`` admin filter (R8.1).
    deleted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Denormalized usage counters (R11.3): maintained incrementally by the owning
    # services and reconciled by the RollupJob, so the admin user list never does
    # a per-row N+1 count. Non-null with a 0 default.
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    application_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Last observed session activity (from ``sessions.last_seen_at``), used for
    # the "active users (last N days)" overview stat + detail last-active display.
    last_active_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ux_users_email", "email", unique=True),
        Index("ix_users_status", "status"),
        # Active-admin count (role=admin, status=active) — the lockout guard.
        Index("ix_users_role_status", "role", "status"),
        # List sort + keyset cursor (created_at desc, id desc).
        Index("ix_users_created_at_id", "created_at", "id"),
        # Purge scan + ``deleted`` filter.
        Index("ix_users_deleted_at", "deleted_at"),
        # Active-user windowed distinct + detail last-active.
        Index("ix_users_last_active_at", "last_active_at"),
        # Case-insensitive **prefix** name search (admin H2 fix). Expression
        # index on lower(name); on Postgres the migration additionally declares
        # the ``text_pattern_ops`` opclass so `lower(name) LIKE 'x%'` is
        # index-served. Email prefix search uses the existing ``ux_users_email``
        # (bare, lowercase-normalized column) + a Postgres text_pattern_ops index.
        Index("ix_users_name_lower", text("lower(name)")),
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
        # Active-user calc + revoke (admin overview / usage-series active_users).
        Index("ix_sessions_user_revoked_seen", "user_id", "revoked_at", "last_seen_at"),
        # Active-users range filter on last_seen_at alone (leading column) so the
        # daily/windowed distinct never scans the whole table (admin M1 fix).
        Index("ix_sessions_last_seen_at", "last_seen_at"),
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
        # Admin audit view filter by target + the per-user detail recent events.
        Index("ix_audit_log_target_ts", "target_user_id", "ts"),
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


# ===========================================================================
# P2 Admin — daily metrics rollup
# ===========================================================================


class MetricsDaily(Base):
    """One row per ``(day_utc, metric)`` for a CLOSED UTC day (admin rollup).

    The ``RollupJob`` computes each registry metric's value for a just-closed
    UTC calendar day via an indexed aggregate query and UPSERTs it here, so the
    admin dashboards + usage-series read O(1) from this table for historical
    days and compute only the current partial day live (never double-counting —
    the rollup only ever writes closed days). ``value`` is a non-negative count;
    ``computed_at`` records when the row was (re)computed.
    """

    __tablename__ = "metrics_daily"

    day_utc: Mapped[str] = mapped_column(String, primary_key=True)
    metric: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (Index("ix_metrics_daily_metric_day", "metric", "day_utc"),)


# ===========================================================================
# P3 Productivity — Version history (design §A, Requirements 1–3)
# ===========================================================================


class ResumeVersion(Base):
    """An immutable, compressed snapshot of a resume's ``processed_data``.

    Snapshots are captured on meaningful changes — the initial parse
    (``source=original``), each accepted AI generation (``source=ai``), and
    manual saves (``source=manual``). The processed_data JSON is **gzip-
    compressed** into ``data_gz`` (so 50 snapshots × millions of resumes stay
    small) and identical consecutive states are de-duplicated by ``content_hash``
    (Requirement 1.2). Every row is scoped to ``(user_id, resume_id)`` (ADR-4);
    the ``original`` snapshot is always retained while the per-resume cap prunes
    the oldest non-``original`` rows (Requirement 1.3).

    ``resume_versions`` is an **owned table** (registered in
    ``app.repository.Repo.OWNED_TABLES`` and the scoping guard), so every query
    against it lives in the ``app.database`` facade, scoped by ``user_id``.
    """

    __tablename__ = "resume_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_id: Mapped[str] = mapped_column(String, nullable=False)
    # One of: original | ai | manual (validated at the service layer).
    source: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    # sha256 hex of the canonical-JSON of processed_data; drives dedupe.
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    # gzip(json.dumps(processed_data, sort_keys, separators)) — the payload.
    data_gz: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Resume appearance at capture time (frontend ``TemplateSettings`` shape) so
    # restore reapplies the historical template, not just the content. Nullable:
    # pre-existing snapshots restore content only (prior behavior).
    template_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Uncompressed byte size of the JSON payload (metadata-only list display).
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # Primary list + "latest snapshot" lookup (dedupe/undo/prune) — newest
        # first within a resume, tie-broken by id for a stable keyset cursor.
        Index(
            "ix_resume_versions_scope_created",
            "user_id",
            "resume_id",
            "created_at",
            "id",
        ),
    )


# ===========================================================================
# P3 Productivity — Shared event platform + Notifications (design §Platform/§B)
# ===========================================================================


class Outbox(Base):
    """Transactional domain-event outbox (design §Platform, R16.1).

    A write emits an event row here **in the same transaction** as the
    originating change; async consumers (the notifier, the search indexer)
    process rows at-least-once and are **idempotent by ``id``**. This decouples
    producers from consumers so a consumer failure never fails the user's write.

    Not an *owned* table in the request sense (consumers scan it cross-user, like
    ``sessions``/``audit_log``); ``user_id`` is carried on the row so consumers
    can attribute the derived notification/search-doc to the right user.
    ``processed_at`` NULL ⇒ unprocessed; ``attempts`` bounds retries before an
    event is parked (dead-lettered) via ``dead_at``.
    """

    __tablename__ = "outbox"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    processed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dead_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        # Consumer cursor: scan unprocessed rows oldest-first. A partial index on
        # processed_at IS NULL would be ideal on PG; the composite covers both.
        Index("ix_outbox_processed_created", "processed_at", "created_at", "id"),
        Index("ix_outbox_dead_at", "dead_at"),
    )


class Notification(Base):
    """A user-scoped, content-safe notification (design §B, R4.1).

    ``body`` never contains resume/JD content or secrets — only a title + a
    deep-link (``node_type``/``node_id``). ``dedupe_key`` makes scheduled/derived
    notifications idempotent (unique per user); ``group_key`` collapses related
    items in the UI. ``read``/``dismissed`` drive the list + unread counter.
    """

    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, default="system")
    priority: Mapped[str] = mapped_column(String, nullable=False, default="normal")
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_type: Mapped[str | None] = mapped_column(String, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    group_key: Mapped[str | None] = mapped_column(String, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Email-delivery bookkeeping (None ⇒ not applicable / not yet sent).
    emailed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # List: unread-first, newest-first, filter by category (keyset cursor).
        Index("ix_notifications_user_created", "user_id", "created_at", "id"),
        Index("ix_notifications_user_read", "user_id", "read", "dismissed"),
        # Idempotency for scheduled/derived notifications (R5.2).
        Index(
            "ux_notifications_user_dedupe",
            "user_id",
            "dedupe_key",
            unique=True,
            sqlite_where=text("dedupe_key IS NOT NULL"),
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )


class NotificationPref(Base):
    """Per-user, per-category delivery preferences (design §B, R6.1).

    PK ``(user_id, category)``. ``in_app``/``email`` toggle each channel; absence
    of a row means the built-in defaults apply (in-app on, email off — resolved
    in the service, so a new category needs no backfill).
    """

    __tablename__ = "notification_prefs"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[str] = mapped_column(String, primary_key=True)
    in_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class UserUnreadCount(Base):
    """Denormalized O(1) unread badge counter (design §B, R4.2).

    Incremented on notification create, decremented on read/dismiss, clamped at
    zero. Avoids a COUNT scan per 30–60s poll. Reconcilable from the
    ``notifications`` table if it ever drifts (retention/reconcile job).
    """

    __tablename__ = "user_unread_counts"

    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    unread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    digest: Mapped[str] = mapped_column(String, nullable=False, default="off")
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


# ===========================================================================
# P3 Productivity — Global search (design §C, Requirements 7–8)
# ===========================================================================


class SearchDocument(Base):
    """A user-scoped, content-safe search document (design §C, R7.1).

    Populated **asynchronously from the outbox** by the SearchIndexer (never in
    the write path). ``title`` + ``body`` are content-safe projections of a
    source node (resume/job/application) — never secrets. PK is the node ref
    ``(node_type, node_id)`` so re-indexing is an idempotent upsert; ``user_id``
    scopes every query **in SQL** (R7.2). On SQLite an FTS5 mirror
    (``search_fts``) accelerates ranked matching; on Postgres a GIN
    ``to_tsvector`` expression index does (both created by migration 0011 /
    the local DDL hook).

    Owned table (registered in ``Repo.OWNED_TABLES`` + the scoping guard); ORM
    access is centralized in the allow-listed ``app/search/repo.py``.
    """

    __tablename__ = "search_documents"

    node_type: Mapped[str] = mapped_column(String, primary_key=True)
    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_search_documents_user", "user_id"),
        # Scope + date filter/sort.
        Index("ix_search_documents_user_updated", "user_id", "updated_at"),
    )


# SQLite FTS5 acceleration for search (design §C). On SQLite the search read
# path matches against an ``search_fts`` external-content FTS5 mirror kept in
# lock-step with ``search_documents`` by triggers (so the indexer only writes the
# base table — the triggers maintain the index). Postgres uses a GIN
# ``to_tsvector`` expression index instead (migration 0011). Created here via a
# dialect-guarded ``after_create`` DDL hook so local zero-config boot (create_all)
# gets FTS with no migration; hosted gets it from the migration's SQLite branch
# (a no-op on Postgres).
from sqlalchemy import event as _sa_event  # noqa: E402

_SQLITE_FTS_DDL: tuple[str, ...] = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
    "title, body, content='search_documents', content_rowid='rowid', tokenize='unicode61')",
    "CREATE TRIGGER IF NOT EXISTS search_documents_ai AFTER INSERT ON search_documents BEGIN "
    "INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body); END",
    "CREATE TRIGGER IF NOT EXISTS search_documents_ad AFTER DELETE ON search_documents BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body); END",
    "CREATE TRIGGER IF NOT EXISTS search_documents_au AFTER UPDATE ON search_documents BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body); "
    "INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body); END",
)


@_sa_event.listens_for(SearchDocument.__table__, "after_create")
def _create_sqlite_search_fts(target, connection, **kw):  # pragma: no cover - DDL glue
    """Create the SQLite FTS5 mirror + sync triggers (SQLite only)."""
    if connection.dialect.name != "sqlite":
        return
    for stmt in _SQLITE_FTS_DDL:
        connection.exec_driver_sql(stmt)


# ===========================================================================
# P3 Productivity — Reminders + Interviews (design §E/§F, Requirements 10–11)
# ===========================================================================


class Reminder(Base):
    """A follow-up reminder on an application (design §E, R10).

    ``due_at`` is stored in **UTC**; ``tz`` (IANA) is for display only.
    ``recurrence`` is a bounded rrule-lite string (``daily`` / ``weekly`` /
    ``every:N:days`` etc. with an optional ``until``); recurring reminders
    **materialize the next occurrence on fire** (no infinite rows). ``status``
    drives the claim-based scheduler: ``pending`` → ``firing`` (claimed) →
    ``fired``; ``snoozed`` reschedules ``due_at``; ``cancelled`` is terminal.
    Owned + parent-ownership checked (the application must belong to the user).
    """

    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String, nullable=False)
    due_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO
    tz: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurrence: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    # Claim bookkeeping: the instant a scanner claimed this row (pending→firing),
    # so a crashed claim can be reclaimed after a lease timeout.
    claimed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    fired_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # Scheduler scan: due pending/snoozed rows, oldest first.
        Index("ix_reminders_status_due", "status", "due_at"),
        Index("ix_reminders_user_app", "user_id", "application_id"),
        Index("ix_reminders_user_due", "user_id", "due_at"),
    )


class Interview(Base):
    """A scheduled interview on an application (design §F, R11).

    ``starts_at`` is **UTC**; ``tz`` (IANA) drives DST-correct display + ICS.
    ``lead_times`` is a JSON list of minutes-before (e.g. ``[1440, 60]``) at
    which "upcoming" notifications fire; each (interview, lead) pair is
    idempotent via the notification ``dedupe_key``. ``fired_leads`` records which
    lead buckets already fired (so reschedule re-arms correctly). ``status`` is
    ``scheduled`` | ``cancelled``.
    """

    __tablename__ = "interviews"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(String, nullable=False)
    starts_at: Mapped[str] = mapped_column(String, nullable=False)  # UTC ISO
    tz: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    duration_min: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="screen")
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    lead_times: Mapped[list] = mapped_column(JSON, default=list)
    fired_leads: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False, default="scheduled")
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_interviews_status_starts", "status", "starts_at"),
        Index("ix_interviews_user_app", "user_id", "application_id"),
        Index("ix_interviews_user_starts", "user_id", "starts_at"),
    )


# ===========================================================================
# Professional Profile System (design: docs/architecture/PROFILE_SYSTEM_PLAN.md)
# ===========================================================================


class Profile(Base):
    """The canonical, document-oriented professional profile (one per user).

    The entire structured profile lives in one native-JSON column ``data`` (a
    ``ProfileData`` document — see ``app/profile/schemas.py``): professional
    identity, experience/education/projects, canonical skills, certifications,
    achievements, links, custom sections, section ordering, AI memory, plus a
    compact ``meta.provenance`` map. This mirrors ``resumes.processed_data`` so
    the profile shares validators, the render/projection engine, and the gzip
    version-snapshot infrastructure with zero new serialization formats
    (ADR — document-oriented profile).

    ``completeness`` caches the weighted completion score for O(1) list reads;
    ``version`` is the optimistic-concurrency (CAS) token bumped atomically by
    every write (same pattern as ``resumes.version``). Exactly one profile per
    user is enforced by the ``UNIQUE`` on ``user_id``.

    ``profiles`` is an **owned table** (registered in
    ``app.repository.Repo.OWNED_TABLES``); every query lives in the
    ``app.database`` facade, scoped by ``user_id`` (ADR-4).
    """

    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Canonical ProfileData document (JSONB on Postgres → future GIN-indexable
    # for skill/keyword search with no schema change).
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Cached 0..100 completion score (cheap list reads); recomputed on every write.
    completeness: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Optimistic-concurrency token (version CAS); bumped atomically per write.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Public sharing (P7). ``public_slug`` is the globally-unique URL segment
    # (nullable until first publish); ``visibility`` gates the public endpoint:
    # private (default, 404 publicly) | unlisted (link-only, noindex) | public
    # (indexable). The column is the authoritative publish state (indexed for a
    # fast, JSON-free slug lookup); ``data.identity.careerVisibility`` remains the
    # user's stated preference.
    public_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String, nullable=False, default="private", server_default="private"
    )
    # Public page theme (P-final): minimal (default) | modern | developer.
    public_theme: Mapped[str] = mapped_column(
        String, nullable=False, default="minimal", server_default="minimal"
    )
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # One profile per user (single-source-of-truth invariant).
        Index("ux_profiles_user_id", "user_id", unique=True),
        # Globally-unique public slug + fast anonymous lookup by slug.
        Index("ux_profiles_public_slug", "public_slug", unique=True),
    )


class ProfileVersion(Base):
    """An immutable, compressed snapshot of a profile's ``data`` document.

    Mirrors :class:`ResumeVersion` exactly: gzip-compressed JSON payload in
    ``data_gz``, content-hash dedupe, per-user/per-profile scoping, cap + prune
    (the oldest ``migration``/first snapshot is retained). ``source`` is one of
    ``manual | import | merge | ai | migration`` (validated at the service
    layer). Owned table (registered in ``Repo.OWNED_TABLES``); all access via the
    ``app.database`` facade, scoped by ``user_id``.
    """

    __tablename__ = "profile_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[str] = mapped_column(String, nullable=False)
    # One of: manual | import | merge | ai | migration (validated at service).
    source: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    # sha256 hex of the canonical-JSON of data; drives dedupe.
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    # gzip(json.dumps(data, sort_keys, separators)) — the payload.
    data_gz: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # Newest-first keyset list + "latest snapshot" dedupe/prune lookup.
        Index(
            "ix_profile_versions_scope_created",
            "user_id",
            "profile_id",
            "created_at",
            "id",
        ),
    )


# ===========================================================================
# Persistent AI Analysis Cache — the "Universal Analysis Object"
# ===========================================================================


class AnalysisArtifact(Base):
    """A cached, reusable result of an expensive AI/analysis operation.

    This is the generic "compute once, reuse everywhere" substrate that lets the
    app avoid recomputing identical LLM/analysis work (resume parsing, job
    analysis, …). It is *complementary* to :class:`ResumeVersion` /
    :class:`ProfileVersion` (which are user-facing edit history) — this table is
    an internal cache keyed by the **content** and **algorithm version** of an
    operation, so an identical input under an unchanged prompt+model resolves to
    a stored result instead of another API call.

    Reuse key: ``(user_id, artifact_type, source_id, checksum, version)`` is
    unique — a lookup on that tuple is an exact cache hit. ``checksum`` is the
    SHA-256 of the canonical input; ``version`` encodes the prompt+model+algo so
    a prompt/model change simply misses (lazy regeneration — version awareness).

    Invalidation: ``source_id`` is the primary owning resource (e.g. a content
    hash or a ``job_id``) and ``related_id`` an optional secondary dependency
    (e.g. the ``resume_id`` a job-fit analysis was computed against). Editing a
    resource deletes every artifact whose ``source_id`` **or** ``related_id``
    matches it, so dependency-aware invalidation is a single indexed delete.

    Owned table (registered in ``Repo.OWNED_TABLES``): every query lives in the
    ``app.database`` facade, scoped by ``user_id``.
    """

    __tablename__ = "analysis_artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # What kind of result this is: resume_parse | job_analysis | tailor_preview | …
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    # Primary owning resource key (content hash for content-addressed dedup, or a
    # resource id like job_id for invalidation).
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    # Optional secondary dependency (e.g. the resume_id a fit analysis used).
    related_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # SHA-256 of the canonical input that produced ``analysis_data``.
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    # Composite algorithm version (prompt|model|algo); a change ⇒ cache miss.
    version: Mapped[str] = mapped_column(String, nullable=False)
    # ready | failed (a failed artifact is not reused but records the attempt).
    status: Mapped[str] = mapped_column(String, nullable=False, default="ready")
    # The cached result payload.
    analysis_data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Optional 0..100 confidence for surfaces that display it.
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # Exact cache-hit lookup + upsert target (Property: at most one row per
        # reuse key). Unique so concurrent producers converge on one artifact.
        Index(
            "ux_analysis_artifacts_key",
            "user_id",
            "artifact_type",
            "source_id",
            "checksum",
            "version",
            unique=True,
        ),
        # Dependency-aware invalidation: delete by primary owning resource.
        Index("ix_analysis_artifacts_source", "user_id", "source_id"),
    )
