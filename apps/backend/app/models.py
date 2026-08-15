"""SQLAlchemy ORM models for FitWright.

A single declarative ``Base`` backs all tables (doc tables migrated from
TinyDB plus the new ``applications`` and ``api_keys`` tables). The facade in
``app/database.py`` converts ORM rows to plain dicts so the rest of the app
never sees ORM objects - preserving the TinyDB-era contracts.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Float,
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
    # engine, page size, margins, spacing, fonts, accent, photo, etc. - the
    # frontend ``TemplateSettings`` shape). Nullable for backward compatibility:
    # a resume created before the template system falls back to the app default.
    # This is a rendering artifact, NOT resume content, so writing it never bumps
    # the optimistic-concurrency ``version`` (see ``update_resume``).
    template_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Overall ATS match score (0-100) for the job this resume was tailored
    # against, captured at confirm time. NULL means "no score", which is the
    # permanent state for a master resume: it has no job to be measured against,
    # and that is different from scoring zero. Migration 0032 adds it nullable.
    # Only the composite is stored - the sub-scores and keyword lists remain a
    # per-request computation, since they are derived and would go stale as the
    # scoring rules change.
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
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
        # storage layer; facade mutations also lock the durable owner row (or a
        # SQLite immediate write transaction) before reading the current master.
        # Reconciled to the enforced hosted shape (migration 0005) now that Task
        # 3 threads ``user_id`` through the repository.
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

    Stable columns are first-class; pipeline analysis metadata such as
    ``job_keywords``, ``company``, and ``role`` lives in ``metadata_json``. The
    durable preview/confirm handshake is stored separately in ``tailor_previews``.
    The facade flattens metadata to top-level keys on read and merges non-core
    keys into it on update, reproducing TinyDB semantics.
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


class TailorPreview(Base):
    """Durable, owner-scoped, single-use tailoring confirmation capability."""

    __tablename__ = "tailor_previews"

    preview_id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    resume_id: Mapped[str] = mapped_column(
        String, ForeignKey("resumes.resume_id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[str] = mapped_column(
        String, ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    prompt_id: Mapped[str] = mapped_column(String, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Full validated preview envelope used only for bounded recovery when the
    # stream completed but its terminal SSE event was lost in transit.
    result_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    consumed_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "request_id", name="uq_tailor_preview_user_request"),
        Index(
            "ix_tailor_preview_consume",
            "preview_id",
            "user_id",
            "resume_id",
            "job_id",
            "payload_hash",
            "consumed_at",
            "expires_at",
        ),
        Index(
            "ix_tailor_preview_scope_created",
            "user_id",
            "resume_id",
            "job_id",
            "prompt_id",
            "created_at",
        ),
        Index("ix_tailor_preview_expires_at", "expires_at"),
    )


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
    # --- Submission audit trail (Phase 5) ---------------------------------- #
    # What was actually sent, captured at submit time. Kept because it answers
    # questions nothing else can: what did I tell them my notice period was, which
    # resume version did they see, and which answers correlate with callbacks.
    submitted_answers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submitted_resume_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # extension | manual | api - how the application reached the employer.
    submitted_via: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


class ApplicationField(Base):
    """One question an application form asked, and the user's answer to it.

    The learning loop's store. Every form the extension fills reports the fields
    it saw; anything it could not answer lands here as ``needs_answer`` and shows
    up in Settings, so answering it once teaches every future form.

    Two design rules are load-bearing:

    * **A row holds a value OR a pointer, never both.** When a question maps onto
      something the Profile already models, ``profile_path`` is set (e.g.
      ``identity.workAuthorization``) and ``value`` stays null - the answer is
      read live from the Profile. Copying it here instead would leave a stale
      duplicate that silently wins after the user edits their Profile.
    * **Type and scope are set at creation.** Without them Settings degenerates
      into a flat list of hundreds of raw ATS labels; with them the page can
      group, render the right input, and collapse synonyms.
    """

    __tablename__ = "application_fields"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The label as the site wrote it, plus a normalized form for matching.
    label: Mapped[str] = mapped_column(String, nullable=False)
    label_normalized: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Other normalized labels seen for the same question ("Years of Python",
    # "Python (years)"), merged into this row so Settings shows one entry.
    synonyms: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # text | textarea | select | radio | checkbox | date | number | file
    field_type: Mapped[str] = mapped_column(String, nullable=False, default="text")
    # For select/radio: the options the form offered, so Settings can render the
    # same choices instead of a free-text box that will not match.
    options: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # The answer, when this question is not something the Profile models.
    value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Dotted path into the profile document, when it is. Mutually exclusive with
    # ``value`` - see the class docstring.
    profile_path: Mapped[str | None] = mapped_column(String, nullable=True)

    # global | company - a company-scoped answer wins over a global one.
    scope: Mapped[str] = mapped_column(String, nullable=False, default="global")
    company: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # needs_answer | answered | ignored ("never ask me this")
    status: Mapped[str] = mapped_column(String, nullable=False, default="needs_answer", index=True)
    # learned (seen on a form) | user (added in Settings) | builtin
    source: Mapped[str] = mapped_column(String, nullable=False, default="learned")

    # How often this question has been encountered, so Settings can lead with
    # what actually matters instead of one-off junk.
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Where it was last seen, for the review card's "appeared on" line.
    last_seen_url: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_ats: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # One row per question per scope. A company-specific answer coexists with
        # the global one; a second global row for the same label does not.
        UniqueConstraint(
            "user_id", "label_normalized", "scope", "company", name="uq_appfield_user_label_scope"
        ),
        Index("ix_appfield_user_status", "user_id", "status"),
    )


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


class UserLlmConfig(Base):
    """Durable per-user non-secret LLM selection and endpoint settings.

    API-key ciphertext remains in :class:`ApiKey`; this row stores the provider,
    model, custom base URL, and reasoning preference that select which key to
    use. Keeping both halves in the database prevents container/dyno filesystem
    replacement from making a valid saved key appear missing after deployment.
    """

    __tablename__ = "user_llm_configs"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    api_base: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Empty string is an intentional "provider default" choice and prevents the
    # one-shot GPT-5 compatibility migration from reapplying after a user clears it.
    reasoning_effort: Mapped[str] = mapped_column(
        String, nullable=False, default="", server_default=""
    )
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)


# ===========================================================================
# Auth foundation (P1 Multi-User Foundation) - new tables
# ===========================================================================
#
# These back authentication, sessions, RBAC, verification/reset, and the
# append-only audit log. All ids are UUID4 strings and all timestamps are
# zero-padded UTC ISO strings (lexically comparable), matching the TinyDB-era
# convention used by the document tables above. Created via ``create_all``
# locally (zero-config boot) and via Alembic ``0002`` on hosted Postgres - both
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
    # off this + avatar_url - P3 §H, R13). NULL => no stored object.
    avatar_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # Canonical profile-image metadata (Photo System, migration 0018). Only
    # metadata lives in the DB - never binary. ``avatar_checksum`` is the SHA-256
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
    # Soft-delete marker (ADR admin §Deletion). NULL => live; a non-null iso ts
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
        # Active-admin count (role=admin, status=active) - the lockout guard.
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


class UserErrorReport(Base):
    """Privacy-safe, owner-scoped client error report metadata."""

    __tablename__ = "user_error_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    client_report_id: Mapped[str] = mapped_column(String(100), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    api_method: Mapped[str] = mapped_column(String(8), nullable=False)
    api_route: Mapped[str] = mapped_column(String(100), nullable=False)
    operation_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    api_request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pipeline_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stream_phase: Mapped[str | None] = mapped_column(String(16), nullable=True)
    fallback_safe: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "client_report_id", name="uq_user_error_reports_user_client"
        ),
        Index("ix_user_error_reports_created_at_id", "created_at", "id"),
        Index(
            "ix_user_error_reports_user_created_at_id", "user_id", "created_at", "id"
        ),
        CheckConstraint(
            "length(client_report_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_client_report_id_length",
        ),
        CheckConstraint(
            "length(message) BETWEEN 1 AND 500",
            name="ck_user_error_reports_message_length",
        ),
        CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_user_error_reports_error_code_length",
        ),
        CheckConstraint(
            "http_status IS NULL OR (http_status BETWEEN 100 AND 599)",
            name="ck_user_error_reports_http_status",
        ),
        CheckConstraint(
            "issue_type = 'tailor_generation_failed'",
            name="ck_user_error_reports_issue_type",
        ),
        CheckConstraint(
            "api_method IN ('GET','POST')",
            name="ck_user_error_reports_api_method",
        ),
        CheckConstraint(
            "api_route IN ("
            "'/jobs/upload',"
            "'/resumes/improve/preview/stream',"
            "'/resumes/improve/preview',"
            "'/resumes/improve/preview/result/{requestId}')",
            name="ck_user_error_reports_api_route",
        ),
        CheckConstraint(
            "operation_request_id IS NULL OR length(operation_request_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_operation_request_id_length",
        ),
        CheckConstraint(
            "api_request_id IS NULL OR length(api_request_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_api_request_id_length",
        ),
        CheckConstraint(
            "pipeline_stage IS NULL OR pipeline_stage IN "
            "('keywords','plan','rewrite','refine','score')",
            name="ck_user_error_reports_pipeline_stage",
        ),
        CheckConstraint(
            "stream_phase IS NULL OR stream_phase IN "
            "('open','before-event','after-event')",
            name="ck_user_error_reports_stream_phase",
        ),
    )


class OAuthIdentity(Base):
    """A verified external identity linked to a :class:`User`.

    Composite primary key ``(provider, subject)`` - a provider's stable ``sub``
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

    Only ``sha256(raw token)`` is stored in ``token_hash`` - the raw token lives
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


class AdminInvite(Base):
    """A hashed, single-use, TTL-bound invitation to create an ADMIN account.

    The secure "admin signup" primitive (Option B). An existing admin issues an
    invite bound to a specific email; only the ``sha256`` of the random token is
    stored (never the raw token), mirroring the verification/reset token tables.
    Redeeming the invite at ``/auth/signup`` proves control of the invited inbox
    (equivalent to email verification) and creates the account with ``role`` from
    the invite - the role NEVER comes from the signup request body. Redemption
    and revocation/supersession are distinct lifecycle states. A claim is
    enforced atomically with matching email, unused, unrevoked, and unexpired
    predicates. ``id`` is the safe public handle for list/revoke; the
    ``token_hash`` is never exposed.
    """

    __tablename__ = "admin_invites"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # Normalized email the invite is bound to (redemption email must match).
    email: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    # The admin who issued it (audit reference; not an FK so a purged admin never
    # cascades away the audit trail).
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    used_by: Mapped[str | None] = mapped_column(String, nullable=True)
    # Revocation is distinct from redemption. ``revoke_reason`` is either the
    # fixed lifecycle value ``manual`` or ``superseded``; no free-form/request
    # content is stored.
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_admin_invites_token_hash", "token_hash", unique=True),
        Index("ix_admin_invites_email", "email"),
        Index("ix_admin_invites_expires_at", "expires_at"),
        Index("ix_admin_invites_created_at", "created_at"),
    )


# ===========================================================================
# P2 Admin - daily metrics rollup
# ===========================================================================


class MetricsDaily(Base):
    """One row per ``(day_utc, metric)`` for a CLOSED UTC day (admin rollup).

    The ``RollupJob`` computes each registry metric's value for a just-closed
    UTC calendar day via an indexed aggregate query and UPSERTs it here, so the
    admin dashboards + usage-series read O(1) from this table for historical
    days and compute only the current partial day live (never double-counting -
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
# P3 Productivity - Version history (design §A, Requirements 1-3)
# ===========================================================================


class ResumeVersion(Base):
    """An immutable, compressed snapshot of a resume's ``processed_data``.

    Snapshots are captured on meaningful changes - the initial parse
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
    # gzip(json.dumps(processed_data, sort_keys, separators)) - the payload.
    data_gz: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Resume appearance at capture time (frontend ``TemplateSettings`` shape) so
    # restore reapplies the historical template, not just the content. Nullable:
    # pre-existing snapshots restore content only (prior behavior).
    template_settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Uncompressed byte size of the JSON payload (metadata-only list display).
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        # Primary list + "latest snapshot" lookup (dedupe/undo/prune) - newest
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
# P3 Productivity - Shared event platform + Notifications (design §Platform/§B)
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
    ``processed_at`` NULL => unprocessed; ``attempts`` bounds retries before an
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

    ``body`` never contains resume/JD content or secrets - only a title + a
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
    # Email-delivery bookkeeping (None => not applicable / not yet sent).
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
    of a row means the built-in defaults apply (in-app on, email off - resolved
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
    zero. Avoids a COUNT scan per 30-60s poll. Reconcilable from the
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
# P3 Productivity - Global search (design §C, Requirements 7-8)
# ===========================================================================


class SearchDocument(Base):
    """A user-scoped, content-safe search document (design §C, R7.1).

    Populated **asynchronously from the outbox** by the SearchIndexer (never in
    the write path). ``title`` + ``body`` are content-safe projections of a
    source node (resume/job/application) - never secrets. PK is the node ref
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
# base table - the triggers maintain the index). Postgres uses a GIN
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
# P3 Productivity - Reminders + Interviews (design §E/§F, Requirements 10-11)
# ===========================================================================


class Reminder(Base):
    """A follow-up reminder on an application (design §E, R10).

    ``due_at`` is stored in **UTC**; ``tz`` (IANA) is for display only.
    ``recurrence`` is a bounded rrule-lite string (``daily`` / ``weekly`` /
    ``every:N:days`` etc. with an optional ``until``); recurring reminders
    **materialize the next occurrence on fire** (no infinite rows). ``status``
    drives the claim-based scheduler: ``pending`` -> ``firing`` (claimed) ->
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
    # Claim bookkeeping: the instant a scanner claimed this row (pending->firing),
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
    ``ProfileData`` document - see ``app/profile/schemas.py``): professional
    identity, experience/education/projects, canonical skills, certifications,
    achievements, links, custom sections, section ordering, AI memory, plus a
    compact ``meta.provenance`` map. This mirrors ``resumes.processed_data`` so
    the profile shares validators, the render/projection engine, and the gzip
    version-snapshot infrastructure with zero new serialization formats
    (ADR - document-oriented profile).

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
    # Canonical ProfileData document (JSONB on Postgres -> future GIN-indexable
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
    # gzip(json.dumps(data, sort_keys, separators)) - the payload.
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
# Persistent AI Analysis Cache - the "Universal Analysis Object"
# ===========================================================================


class AnalysisArtifact(Base):
    """A cached, reusable result of an expensive AI/analysis operation.

    This is the generic "compute once, reuse everywhere" substrate that lets the
    app avoid recomputing identical LLM/analysis work (resume parsing, job
    analysis, ...). It is *complementary* to :class:`ResumeVersion` /
    :class:`ProfileVersion` (which are user-facing edit history) - this table is
    an internal cache keyed by the **content** and **algorithm version** of an
    operation, so an identical input under an unchanged prompt+model resolves to
    a stored result instead of another API call.

    Reuse key: ``(user_id, artifact_type, source_id, checksum, version)`` is
    unique - a lookup on that tuple is an exact cache hit. ``checksum`` is the
    SHA-256 of the canonical input; ``version`` encodes the prompt+model+algo so
    a prompt/model change simply misses (lazy regeneration - version awareness).

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
    # What kind of result this is: resume_parse | job_analysis | tailor_preview | ...
    artifact_type: Mapped[str] = mapped_column(String, nullable=False)
    # Primary owning resource key (content hash for content-addressed dedup, or a
    # resource id like job_id for invalidation).
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    # Optional secondary dependency (e.g. the resume_id a fit analysis used).
    related_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # SHA-256 of the canonical input that produced ``analysis_data``.
    checksum: Mapped[str] = mapped_column(String, nullable=False)
    # Composite algorithm version (prompt|model|algo); a change => cache miss.
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


# ---------------------------------------------------------------------------
# Job Discovery & Recommendations (optional feature, §10.5)
# ---------------------------------------------------------------------------


class DiscoveryCache(Base):
    """Content-addressed search-result cache for the discovery pipeline.

    Keyed by a SHA-256 of (resume_version + query + filters). Expired rows are
    treated as misses by the accessor; eviction is on overwrite or sweep.
    """

    __tablename__ = "discovery_cache"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    expires_at: Mapped[str] = mapped_column(String, nullable=False)


class SiteRecipeModel(Base):
    """Persisted custom-site scraping recipe for the discovery pipeline.

    Uniqueness is (user_id, slug). ``schema`` is the JSON extraction schema
    handed to the LLM extraction strategy.
    """

    __tablename__ = "site_recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    search_url_template: Mapped[str] = mapped_column(Text, nullable=False)
    schema_json: Mapped[Any] = mapped_column("schema", JSON, nullable=False, default=dict)
    fetch_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="http")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint("user_id", "slug", name="uq_site_recipes_user_slug"),
    )


# ---------------------------------------------------------------------------
# Job Discovery Feed (Phase 1 — background discovery + persistent results)
# ---------------------------------------------------------------------------


class DiscoveryRun(Base):
    """Tracks scheduled discovery runs per user.

    Each user with an active resume can have one scheduled run. The background
    worker picks users whose next_run_at <= now and executes discovery for them.
    """

    __tablename__ = "discovery_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    resume_id: Mapped[str] = mapped_column(String(36), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    last_run_at: Mapped[str | None] = mapped_column(String, nullable=True)
    next_run_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint("user_id", "resume_id", name="uq_discovery_runs_user_resume"),
    )


class BoardHealth(Base):
    """Whether a job board is actually working for this user.

    Exists because a dead scraper is silent: the board returns nothing and the
    user blames their search terms. A rolling failure count turns "no results"
    into "Hirist has returned nothing five runs in a row", which is actionable -
    sign in again, or the adapter needs fixing.

    One row per user per board, overwritten in place. This is a status, not a log:
    nobody needs the history, they need to know what is broken now.
    """

    __tablename__ = "board_health"
    __table_args__ = (
        UniqueConstraint("user_id", "board", name="uq_board_health_user_board"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    board: Mapped[str] = mapped_column(String, nullable=False)
    # ok | empty | signed_out | capped | error
    last_status: Mapped[str] = mapped_column(String, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    last_run_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    # The "it used to work" evidence: without this, a board that never worked and
    # one that broke yesterday look identical.
    last_success_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DiscoveryResult(Base):
    """A persisted job listing from a discovery run.

    Results accumulate across runs and form the user's job feed. Deduplicated
    by fingerprint per user (same job won't appear twice).
    """

    __tablename__ = "discovery_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # Job data
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    is_remote: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    salary: Mapped[str | None] = mapped_column(String(100), nullable=True)
    posted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Scoring
    match_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    matched_keywords: Mapped[Any] = mapped_column(JSON, nullable=True)
    missing_keywords: Mapped[Any] = mapped_column(JSON, nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Status tracking (Phase 2 prep)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    seen: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The job-description row created when this was saved, so the feed knows which
    # apply-queue entry belongs to it.
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # URL-free identity: the same posting on three boards shares this. `fingerprint`
    # above stays URL-aware, which is what makes it right for "same listing" and
    # wrong for "same job".
    group_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[str] = mapped_column(String, default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint("user_id", "fingerprint", name="uq_discovery_results_user_fp"),
        Index("ix_discovery_results_user_status", "user_id", "status"),
        Index("ix_discovery_results_user_created", "user_id", "created_at"),
    )


class CreditPack(Base):
    """A buyable bundle, priced from the admin panel (migration 0039).

    Two prices, both integers in the smallest currency unit: ``amount_minor`` is the
    regular price and ``sale_amount_minor`` is the discount, valid only inside its
    window. The operator enters a percentage in the admin form; the computed figure is
    what gets stored, so exactly one integer is ever displayed, charged, and re-checked
    against the payment provider's webhook.

    The sale expires on its own - effective price falls back to the regular price the
    moment the window closes, with no job to run and nothing to forget.
    """

    __tablename__ = "credit_packs"

    #: Operator-chosen slug. Stable on purpose: credit_purchases records it, and those
    #: rows must keep making sense after the pack is edited or withdrawn.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    sale_amount_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sale_label: Mapped[str | None] = mapped_column(String, nullable=True)
    sale_starts_at: Mapped[str | None] = mapped_column(String, nullable=True)
    sale_ends_at: Mapped[str | None] = mapped_column(String, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (Index("ix_credit_packs_active_sort", "active", "sort_order"),)


class FeaturePrice(Base):
    """What one AI action costs, editable from the admin panel (migration 0040).

    This replaced a variable charge. The cost used to be the p95 of what the feature
    had recently consumed in tokens, which is the honest figure for what the OPERATOR
    paid but an unusable one to quote to a user: a range cannot be displayed as a
    price, and a charge that differs from the number the user was shown reads as being
    cheated. One published integer per feature fixes that.

    Token metering is unaffected and still records real consumption - that is what the
    admin spend and margin views are built from. It simply no longer decides what the
    user pays.
    """

    __tablename__ = "feature_prices"

    #: The key the spend path already uses ("resume_tailor"). Stable: the usage ledger
    #: records it and those rows outlive any price edit.
    feature: Mapped[str] = mapped_column(String, primary_key=True)
    #: User-facing name. "Tailored resume", never "resume_tailor".
    label: Mapped[str] = mapped_column(String, nullable=False)
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    #: False = runs without spending credits. Deliberately separate from a price of 0,
    #: so "free on purpose" and "not priced yet" cannot be confused.
    is_charged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (Index("ix_feature_prices_active_sort", "active", "sort_order"),)


class SubscriptionPlan(Base):
    """A monthly tier: its price, its credit allowance, its search ceiling (0040).

    The monthly grant used to be one global env var, so every user necessarily got the
    same allowance and there was no notion of a paid tier at all. A plan row is what
    makes "which package am I on?" answerable - which is the prerequisite for a badge,
    an upgrade screen, and admin plan management.

    ``search_daily_limit`` prices nothing; it caps a FREE action. Job search is not
    charged because metering exploration teaches users to stop exploring, and
    exploring is what produces the applications that are charged. An uncapped search
    would still be an invitation to hammer job boards, so it gets a ceiling instead.
    """

    __tablename__ = "subscription_plans"

    #: Operator-chosen slug ("free", "job_hunt"). Recorded on credit_accounts, so it
    #: must stay stable across edits to the plan's label or price.
    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    #: 0 for the free tier. Smallest currency unit, tax-inclusive, as credit_packs.
    price_minor: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    #: Granted on first touch and re-granted at each monthly period boundary.
    monthly_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Fair-use ceiling for non-billed searches. 0 = none allowed; NULL = uncapped.
    search_daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The tier a new account lands on. Exactly one plan should carry this.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, server_default="100"
    )
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_subscription_plans_active_sort", "active", "sort_order"),
    )


class DailyUsageCounter(Base):
    """Per-day count of a free-but-capped action (migration 0040).

    Generic in ``kind`` on purpose: the next rate-limited-but-unpriced action should
    not need another table. The composite primary key is what makes "one row per user
    per kind per day" a database guarantee rather than application discipline.
    """

    __tablename__ = "daily_usage_counters"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    #: UTC calendar day, "YYYY-MM-DD". A date string, not a timestamp, so the key
    #: itself enforces one row per day.
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    kind: Mapped[str] = mapped_column(String, primary_key=True)
    count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)


class CreditPurchase(Base):
    """One attempt to buy credits (migration 0038).

    Credits are granted ONLY when a verified provider webhook says the money arrived.
    A client-side "payment succeeded" callback is a claim by an untrusted party and can
    be forged by anyone who can read the page's JavaScript; it may update the UI, and it
    must never move a balance.

    ``state`` is forward-only. Providers deliver webhooks out of order, so a late
    ``created`` must not undo a completed ``granted``.
    """

    __tablename__ = "credit_purchases"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    pack_id: Mapped[str] = mapped_column(String, nullable=False)
    #: Recorded here rather than recomputed at grant time: pack pricing changes, and
    #: what this buyer was promised must not change with it.
    credits: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Smallest currency unit (paise, cents). Integer - 0.1 + 0.2 is not 0.3, and that
    #: error compounds across a ledger.
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="INR")
    tax_minor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="created", server_default="created"
    )
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    #: UNIQUE. Providers redeliver webhooks by design; this constraint is what makes a
    #: redelivery a no-op rather than a second grant.
    provider_event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    granted_at: Mapped[str | None] = mapped_column(String, nullable=True)
    refunded_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_credit_purchases_user_created", "user_id", "created_at"),
        Index("ux_credit_purchases_event", "provider_event_id", unique=True),
        Index("ux_credit_purchases_invoice", "invoice_number", unique=True),
        Index("ix_credit_purchases_state", "state"),
    )


class AiChannel(Base):
    """One configured, credentialled route to a model (migration 0033).

    Two channels may target the same provider and model with different
    credentials (e.g. two OpenAI accounts) - they are independent for health and
    budget purposes, which is why identity is the row, not (provider, model).

    Credentials are deliberately NOT stored here. They live in the existing
    encrypted per-provider key store keyed by channel id, so the codebase keeps
    exactly one encryption path and one place that can leak.
    """

    __tablename__ = "ai_channels"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    api_base: Mapped[str | None] = mapped_column(String, nullable=True)
    # Lower = preferred. Ties break on created_at so ordering is deterministic.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100, server_default="100")
    # active | disabled | draining. ``draining`` serves in-flight requests only and
    # is the required step before deletion, so a channel cannot vanish from under a
    # request already using it.
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="disabled", server_default="disabled"
    )
    # reliable | flaky | unsupported | unknown. An ``unsupported`` channel is barred
    # from features needing valid JSON: a fallback that keeps the app "up" while
    # returning unusable output is worse than an honest error.
    structured_verdict: Mapped[str] = mapped_column(
        String, nullable=False, default="unknown", server_default="unknown"
    )
    monthly_cost_cap_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Encrypted with app.crypto, stored here rather than in `api_keys` because that
    # table's user_id is a FK to users and a channel has no user (migration 0036).
    # Never returned by the API - only ever decrypted for an outbound call, and
    # deliberately absent from _ai_channel_to_dict so it cannot leak by accident.
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint("name", name="uq_ai_channels_name"),
        Index("ix_ai_channels_state_priority", "state", "priority"),
    )


class AiChannelHealth(Base):
    """Runtime health for one channel (migration 0033).

    Split from :class:`AiChannel` on purpose: this row changes constantly and
    automatically, configuration changes rarely and by hand. Keeping them apart
    means a transient provider blip can never rewrite the operator's config, and
    wiping health to clear a bad cooldown cannot lose a key.
    """

    __tablename__ = "ai_channel_health"

    channel_id: Mapped[str] = mapped_column(String, primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # While set, the channel is benched. One probe request is allowed through when
    # it passes - not the full traffic, which would instantly re-break a provider
    # that is still struggling.
    cooling_until: Mapped[str | None] = mapped_column(String, nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Error CLASS only (timeout / rate_limit / server / auth / ...). Never the
    # provider's message: those can carry prompt fragments.
    last_error_class: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)


class AiUsageLedger(Base):
    """One immutable row per AI call, per user (migration 0034).

    Deliberately separate from ``app/admin/ai_metrics.py``, which is intentionally
    anonymous and whose own docstrings reject the token breakdown as a field. That
    module cannot be the billing record; this one cannot be anonymous. Two privacy
    contracts, two tables, and they must not be merged later.

    Append-only: a correction is a new compensating row, never an edit.
    """

    __tablename__ = "ai_usage_ledger"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    feature: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: a call can fail before any channel is chosen (every channel cooling
    # down), and that attempt still deserves a row.
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # True when the provider returned no usage block and these are our estimate. An
    # estimate must never be indistinguishable from a measurement, or reconciling
    # against the provider's invoice is impossible.
    tokens_estimated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    # What the OPERATOR paid. Distinct from credits_charged: the user pays the
    # primary channel's rate even when an expensive fallback served them, because
    # failover is the operator's problem.
    provider_cost_micros: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    credits_charged: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reservation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Request-level provider latency (migration 0037), for per-channel p95. Nullable:
    # rows written before the column existed have no value, and inventing one would
    # corrupt the percentile it exists to compute.
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ok | failed | cancelled. ``failed`` rows exist precisely so a zero charge is
    # provable rather than merely absent.
    outcome: Mapped[str] = mapped_column(String, nullable=False, default="ok", server_default="ok")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
        Index("ix_ai_usage_channel_created", "channel_id", "created_at"),
        Index("ix_ai_usage_feature_created", "feature", "created_at"),
    )


class CreditAccount(Base):
    """A user's credit balance - the authority (migration 0035).

    Balance lives in the database, not in memory. This is explicit because this
    app's existing per-minute rate limiter degrades to per-process when
    ``KVSTORE_URL`` is unset, and it is unset in production. Survivable for a rate
    limit; not survivable for money.

    Available balance is NOT stored. It is derived:
        available = allowance_credits + wallet_credits - reserved_credits
    A separately-maintained "available" column would eventually disagree with its
    own components.
    """

    __tablename__ = "credit_accounts"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    # Which subscription plan this account is on (migration 0040). NULL = "not placed
    # on a plan yet", resolved to the default plan at READ time rather than backfilled,
    # because a backfill misses every account created after it ran. No FK on purpose: a
    # retired plan must not stop its former accounts from rendering.
    plan_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Recurring free grant. Use-it-or-lose-it.
    allowance_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    allowance_period_start: Mapped[str | None] = mapped_column(String, nullable=True)
    # Purchased. Never expires - expiring paid credits is the most resented pattern
    # in prepaid products.
    wallet_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reserved_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lifetime_granted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lifetime_spent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Independent of balance: credits alone do not stop a stolen session draining a
    # funded wallet in one minute.
    velocity_window_start: Mapped[str | None] = mapped_column(String, nullable=True)
    velocity_spent: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # ok | blocked. ``blocked`` after a claw-back took back credits already spent -
    # the one case a balance may legitimately go negative.
    state: Mapped[str] = mapped_column(String, nullable=False, default="ok", server_default="ok")
    # NULL = inherit the global default. An override is ABSOLUTE: raising the global
    # default must never implicitly widen it.
    monthly_allowance_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    velocity_cap_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Per-user kill switch, effective without a deploy.
    ai_disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    updated_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (
        CheckConstraint("reserved_credits >= 0", name="ck_credit_accounts_reserved_nonneg"),
    )


class CreditReservation(Base):
    """A short-lived hold on a balance (migration 0035).

    The hold is what makes concurrency safe: without it, N parallel requests all
    pass the same balance check before any of them settles.
    """

    __tablename__ = "credit_reservations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    feature: Mapped[str] = mapped_column(String, nullable=False)
    credits_reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    # held | settled | released | expired. Forward-only.
    state: Mapped[str] = mapped_column(String, nullable=False, default="held", server_default="held")
    # Makes a retried request reuse its hold instead of taking a second one. The
    # UNIQUE constraint is what enforces that, not an application check.
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    # A crashed worker must not freeze a balance forever.
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)
    settled_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_credit_reservations_idem"),
        Index("ix_credit_reservations_state_expires", "state", "expires_at"),
        Index("ix_credit_reservations_user", "user_id"),
    )


class CreditTransaction(Base):
    """Every balance movement, append-only (migration 0035).

    Exists so "why is my balance this?" is always answerable. The unique
    idempotency key is the single constraint that makes double-charging impossible
    rather than merely unlikely.
    """

    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    # signup_grant | monthly_refill | purchase | spend | refund | admin_adjust |
    # chargeback
    kind: Mapped[str] = mapped_column(String, nullable=False)
    credits_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    # Balance after this movement, so history replays without recomputing from the
    # beginning of time.
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    # Mandatory for admin_adjust: an unexplained manual balance change is
    # indistinguishable from a bug or an abuse.
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False, default=_utcnow_iso)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_credit_transactions_idem"),
        Index("ix_credit_transactions_user_created", "user_id", "created_at"),
    )
