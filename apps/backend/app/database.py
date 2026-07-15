"""SQLAlchemy data layer for FitWright (SQLite local, Postgres hosted — ADR-13).

This is a behavior-preserving replacement for the original TinyDB wrapper. The
``Database`` facade keeps the same method names/signatures and returns **plain
dicts** (never ORM rows), so the ~50 call sites only needed ``await`` added.

Two engines back one database, resolved from ``settings.effective_database_url``:
- an **async** engine (``aiosqlite`` / ``asyncpg``) for the document tables and
  applications;
- a **sync** engine (SQLite DBAPI / ``psycopg`` v3) for the encrypted
  ``api_keys`` table, which is read on the synchronous LLM hot path
  (``get_llm_config`` → ``resolve_api_key``).

Locally both engines point at one SQLite file (zero-config); hosted they point
at the same Postgres server (schema owned by Alembic). See ``app.db_engine`` for
dialect selection and pooling.
"""

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, func, select, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db_engine import (
    init_models_sync,
    is_sqlite_url,
    make_async_engine,
    make_sync_engine,
    resolve_database_url,
)
from app.models import (
    AnalysisArtifact,
    ApiKey,
    Application,
    Improvement,
    Interview,
    Job,
    Notification,
    NotificationPref,
    Outbox,
    Profile,
    ProfileVersion,
    Reminder,
    Resume,
    ResumeVersion,
    SearchDocument,
    User,
    UserUnreadCount,
)
from app.repository import Repo

logger = logging.getLogger(__name__)

# Columns that are first-class on the jobs table; everything else the pipeline
# attaches dynamically is stored in ``metadata_json`` (see Job model).
_JOB_CORE_FIELDS = frozenset({"job_id", "content", "resume_id", "created_at"})

# Application status columns (stable keys, decoupled from i18n labels).
APPLICATION_STATUSES: tuple[str, ...] = (
    "saved",
    "applied",
    "no_response",
    "response",
    "interview",
    "accepted",
    "rejected",
)


def _now() -> str:
    """Current UTC time as an ISO-8601 string (TinyDB-era format)."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Async SQLAlchemy facade for FitWright data.

    Every owned-resource method takes a **mandatory** ``user_id`` and routes its
    query through :class:`app.repository.Repo` so cross-user reads/writes are
    impossible (ADR-4, R10.2). A foreign or absent id resolves to ``None`` (the
    router turns that into a 404 — no existence disclosure, R10.3). This is the
    multi-tenant isolation boundary; see ``app/scripts/check_scoping.py`` for the
    CI guard that forbids unscoped owned queries.
    """

    def __init__(self, db_path: Path | str | None = None):
        # Resolve the database from ``settings.effective_database_url`` (ADR-13)
        # so the runtime and Alembic agree on which database the app talks to.
        # ``db_path`` is an explicit override used by tests: a ``Path`` builds a
        # SQLite file URL; a URL string selects the dialect (SQLite/Postgres).
        # ``None`` resolves the effective URL (local SQLite, hosted Postgres).
        self._db_source = db_path
        self._async_url = resolve_database_url(db_path, async_=True)
        self._sync_url = resolve_database_url(db_path, async_=False)
        # For a local SQLite file, ensure the parent directory exists (zero-
        # config boot). Postgres has no local file to create.
        if isinstance(db_path, Path):
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_path: Path | None = db_path
        elif is_sqlite_url(self._sync_url):
            self.db_path = Path(self._sync_url.split(":///", 1)[1])
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            # Postgres (hosted): no local database file.
            self.db_path = None
        self._async_engine = None
        self._async_session_factory: async_sessionmaker[AsyncSession] | None = None
        self._sync_engine = None
        self._sync_session_factory: sessionmaker[Session] | None = None
        self._initialized = False
        # Per-user master-resume promotion locks. The single-master invariant is
        # now **per user** (R10.4), so serialization is per user rather than a
        # single global lock. Instance-scoped so isolated test databases never
        # share lock state. The partial unique ``(user_id, is_master)`` index is
        # the storage-level backstop.
        self._master_locks: dict[str, asyncio.Lock] = {}
        self._master_locks_guard = asyncio.Lock()

    async def _master_lock(self, user_id: str) -> asyncio.Lock:
        """Return the promotion lock for ``user_id`` (created on first use)."""
        async with self._master_locks_guard:
            lock = self._master_locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._master_locks[user_id] = lock
            return lock

    # -- engine / session plumbing ------------------------------------------

    def _ensure_initialized(self) -> None:
        """Create engines and tables once (idempotent).

        Tables are created via the **sync** engine so both the sync (api_keys)
        and async (docs) paths see them immediately, without needing an event
        loop. Both engines point at the same file.
        """
        if self._initialized:
            return
        self._sync_engine = make_sync_engine(self._sync_url)
        self._sync_session_factory = sessionmaker(self._sync_engine, expire_on_commit=False)
        # Local schema evolution (SQLite only); a no-op on Postgres, whose schema
        # is owned by the Alembic migration chain (ADR-13).
        init_models_sync(self._sync_engine)
        self._async_engine = make_async_engine(self._async_url)
        self._async_session_factory = async_sessionmaker(
            self._async_engine, expire_on_commit=False
        )
        self._initialized = True

    @property
    def _session(self) -> async_sessionmaker[AsyncSession]:
        self._ensure_initialized()
        assert self._async_session_factory is not None
        return self._async_session_factory

    @property
    def _sync(self) -> sessionmaker[Session]:
        self._ensure_initialized()
        assert self._sync_session_factory is not None
        return self._sync_session_factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Public accessor for the async session factory.

        The auth service layer (sessions, audit) issues its own queries against
        the same database and needs a session maker. Exposed as a stable public
        property (initialization is idempotent) so services never reach into the
        private ``_session`` attribute.
        """
        return self._session

    @property
    def async_engine(self) -> AsyncEngine:
        """The initialized async engine backing the document tables.

        Exposed so the DB-backed ``KVStore`` fallback (ADR-6) can persist its
        ``kv`` table in the *same* database as the rest of the app (see
        ``app.auth.runtime``). Initialization is idempotent, so callers can ask
        for the engine without worrying about boot ordering.
        """
        self._ensure_initialized()
        assert self._async_engine is not None
        return self._async_engine

    async def close(self) -> None:
        """Dispose engines and release file handles."""
        if self._async_engine is not None:
            await self._async_engine.dispose()
            self._async_engine = None
            self._async_session_factory = None
        if self._sync_engine is not None:
            self._sync_engine.dispose()
            self._sync_engine = None
            self._sync_session_factory = None
        self._initialized = False

    # -- row -> dict converters ---------------------------------------------

    @staticmethod
    def _resume_to_dict(row: Resume) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "resume_id": row.resume_id,
            "content": row.content,
            "content_type": row.content_type,
            "filename": row.filename,
            "is_master": row.is_master,
            "parent_id": row.parent_id,
            "processed_data": row.processed_data,
            "processing_status": row.processing_status,
            "cover_letter": row.cover_letter,
            "outreach_message": row.outreach_message,
            "interview_prep": row.interview_prep,
            "title": row.title,
            "template_settings": getattr(row, "template_settings", None),
            # Optimistic-concurrency token (P4 R3.1). Older rows created before
            # migration 0014 read back via the server_default (1); ``getattr``
            # keeps the facade safe if a detached/legacy row lacks the attribute.
            "version": getattr(row, "version", 1) or 1,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        # Preserve TinyDB absence semantics: omit the key entirely when None.
        if row.original_markdown is not None:
            doc["original_markdown"] = row.original_markdown
        return doc

    @staticmethod
    def _job_to_dict(row: Job) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "job_id": row.job_id,
            "content": row.content,
            "resume_id": row.resume_id,
            "created_at": row.created_at,
        }
        meta = row.metadata_json or {}
        if isinstance(meta, dict):
            doc.update(meta)  # flatten dynamic fields to top level
        return doc

    @staticmethod
    def _improvement_to_dict(row: Improvement) -> dict[str, Any]:
        return {
            "request_id": row.request_id,
            "original_resume_id": row.original_resume_id,
            "tailored_resume_id": row.tailored_resume_id,
            "job_id": row.job_id,
            "improvements": row.improvements,
            "created_at": row.created_at,
        }

    @staticmethod
    def _application_to_dict(row: Application) -> dict[str, Any]:
        return {
            "application_id": row.application_id,
            "job_id": row.job_id,
            "resume_id": row.resume_id,
            "master_resume_id": row.master_resume_id,
            "status": row.status,
            "company": row.company,
            "role": row.role,
            "applied_at": row.applied_at,
            "notes": row.notes,
            "position": row.position,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    # -- Resume operations --------------------------------------------------

    async def _get_owned_resume(
        self, session: AsyncSession, user_id: str, resume_id: str
    ) -> Resume | None:
        """Load a resume by id scoped to ``user_id`` (None if absent/foreign)."""
        result = await session.execute(
            Repo.scoped(select(Resume).where(Resume.resume_id == resume_id), Resume, user_id)
        )
        return result.scalars().first()

    async def create_resume(
        self,
        user_id: str,
        content: str,
        content_type: str = "md",
        filename: str | None = None,
        is_master: bool = False,
        parent_id: str | None = None,
        processed_data: dict[str, Any] | None = None,
        processing_status: str = "pending",
        cover_letter: str | None = None,
        outreach_message: str | None = None,
        title: str | None = None,
        original_markdown: str | None = None,
        interview_prep: str | None = None,
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new resume entry owned by ``user_id``.

        processing_status: "pending", "processing", "ready", "failed"
        """
        resume_id = str(uuid4())
        now = _now()
        async with self._session() as session:
            session.add(
                Resume(
                    resume_id=resume_id,
                    user_id=user_id,
                    content=content,
                    content_type=content_type,
                    filename=filename,
                    is_master=is_master,
                    parent_id=parent_id,
                    processed_data=processed_data,
                    processing_status=processing_status,
                    cover_letter=cover_letter,
                    outreach_message=outreach_message,
                    interview_prep=interview_prep,
                    title=title,
                    original_markdown=original_markdown,
                    template_settings=template_settings,
                    created_at=now,
                    updated_at=now,
                )
            )
            await self._adjust_user_counter(session, user_id, "resume_count", +1)
            self._emit_search_event(session, "resume.upserted", user_id, resume_id)
            await session.commit()

        doc: dict[str, Any] = {
            "resume_id": resume_id,
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "is_master": is_master,
            "parent_id": parent_id,
            "processed_data": processed_data,
            "processing_status": processing_status,
            "cover_letter": cover_letter,
            "outreach_message": outreach_message,
            "interview_prep": interview_prep,
            "title": title,
            "template_settings": template_settings,
            "created_at": now,
            "updated_at": now,
        }
        if original_markdown is not None:
            doc["original_markdown"] = original_markdown
        return doc

    async def create_resume_atomic_master(
        self,
        user_id: str,
        content: str,
        content_type: str = "md",
        filename: str | None = None,
        processed_data: dict[str, Any] | None = None,
        processing_status: str = "pending",
        cover_letter: str | None = None,
        outreach_message: str | None = None,
        original_markdown: str | None = None,
        title: str | None = None,
        interview_prep: str | None = None,
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new resume with atomic master assignment for ``user_id``.

        Uses a **per-user** asyncio.Lock to prevent race conditions when multiple
        uploads for the same user happen concurrently and both try to become
        master (the single-master invariant is per user — R10.4).
        """
        lock = await self._master_lock(user_id)
        async with lock:
            current_master = await self.get_master_resume(user_id)
            is_master = current_master is None

            # Recovery: if the current master is stuck failed/processing, demote
            # it so this upload can become the new master.
            if current_master and current_master.get("processing_status") in (
                "failed",
                "processing",
            ):
                async with self._session() as session:
                    row = await self._get_owned_resume(
                        session, user_id, current_master["resume_id"]
                    )
                    if row is not None:
                        row.is_master = False
                        await session.commit()
                is_master = True

            return await self.create_resume(
                user_id,
                content=content,
                content_type=content_type,
                filename=filename,
                is_master=is_master,
                processed_data=processed_data,
                processing_status=processing_status,
                cover_letter=cover_letter,
                outreach_message=outreach_message,
                interview_prep=interview_prep,
                original_markdown=original_markdown,
                title=title,
                template_settings=template_settings,
            )

    async def get_resume(self, user_id: str, resume_id: str) -> dict[str, Any] | None:
        """Get a resume by ID, scoped to ``user_id`` (None if absent/foreign)."""
        async with self._session() as session:
            row = await self._get_owned_resume(session, user_id, resume_id)
            return self._resume_to_dict(row) if row else None

    async def get_master_resume(self, user_id: str) -> dict[str, Any] | None:
        """Get the user's master resume if it exists."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(Resume).where(Resume.is_master.is_(True)), Resume, user_id
                )
            )
            row = result.scalars().first()
            return self._resume_to_dict(row) if row else None

    async def update_resume(
        self, user_id: str, resume_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Update a resume owned by ``user_id``.

        Bumps the optimistic-concurrency ``version`` (P4 R3.1) **only** when the
        CAS-protected editor content changes (``content`` / ``processed_data``).
        Auxiliary AI-artifact writes (cover letter, outreach, interview prep,
        title) do not touch the editor's optimistic lock, so persisting them
        never invalidates an open editor's base version (which would otherwise
        surface a spurious conflict on the next autosave). ``version`` itself is
        never settable via ``updates``.

        Raises:
            ValueError: If the resume is not found for this user.
        """
        updates = {k: v for k, v in updates.items() if k != "version"}
        bumps_version = "content" in updates or "processed_data" in updates
        async with self._session() as session:
            row = await self._get_owned_resume(session, user_id, resume_id)
            if row is None:
                raise ValueError(f"Resume not found: {resume_id}")
            for key, value in updates.items():
                if hasattr(row, key):
                    setattr(row, key, value)
                else:
                    logger.warning("Ignoring unknown resume field on update: %s", key)
            if bumps_version:
                row.version = (getattr(row, "version", 1) or 1) + 1
            row.updated_at = _now()
            self._emit_search_event(session, "resume.upserted", user_id, resume_id)
            await session.commit()
            return self._resume_to_dict(row)

    async def update_resume_cas(
        self,
        user_id: str,
        resume_id: str,
        updates: dict[str, Any],
        *,
        base_version: int,
    ) -> tuple[str, dict[str, Any] | None]:
        """Atomic optimistic-concurrency update (version CAS — P4 R3.1/3.4).

        Applies ``updates`` only when the stored ``version`` still equals
        ``base_version``; the read-check-write happens in a single transaction so
        two concurrent writers with the same base cannot both succeed (exactly
        one wins; Property 1). ``version`` is bumped by one on success and is
        never settable through ``updates``.

        Returns a ``(status, resume_dict)`` tuple:

        - ``("updated", <dict>)`` — CAS matched and the write was applied.
        - ``("conflict", <current_dict>)`` — the base version was stale; the
          returned dict is the *current* server state so the caller can build the
          409 ``{your_base_version, current_version, current_data}`` payload.
        - ``("not_found", None)`` — no such resume for this user.

        The guard is a single-row **conditional UPDATE**
        (``... SET …, version = version + 1 WHERE resume_id = ? AND user_id = ?
        AND version = :base``) rather than a read-check-write, so it is atomic at
        the storage layer: two concurrent writers with the same base cannot both
        match (the first bumps the version, the second's guard then matches zero
        rows). This is what makes Property 1 hold even under true concurrency.
        """
        # Only real Resume columns may be set; ``version``/``updated_at`` are
        # managed here, never by the caller.
        column_names = {c.key for c in Resume.__table__.columns}
        clean = {
            k: v
            for k, v in updates.items()
            if k in column_names and k not in ("version", "updated_at")
        }
        for k in updates:
            if k not in clean and k not in ("version", "updated_at"):
                logger.warning("Ignoring unknown resume field on CAS update: %s", k)

        async with self._session() as session:
            stmt = (
                sa_update(Resume)
                .where(
                    Resume.resume_id == resume_id,
                    Resume.user_id == user_id,
                    Resume.version == base_version,
                )
                .values(version=Resume.version + 1, updated_at=_now(), **clean)
            )
            result = await session.execute(stmt)
            if result.rowcount == 1:
                self._emit_search_event(session, "resume.upserted", user_id, resume_id)
                await session.commit()
                row = await self._get_owned_resume(session, user_id, resume_id)
                return "updated", self._resume_to_dict(row) if row else None
            # Zero rows changed: either the resume doesn't exist for this user
            # (404) or the base version was stale (409). Distinguish by a scoped
            # read of the current state (no write performed).
            await session.rollback()
            row = await self._get_owned_resume(session, user_id, resume_id)
            if row is None:
                return "not_found", None
            return "conflict", self._resume_to_dict(row)

    async def delete_resume(self, user_id: str, resume_id: str) -> bool:
        """Delete a resume owned by ``user_id``. Returns False if absent/foreign."""
        async with self._session() as session:
            row = await self._get_owned_resume(session, user_id, resume_id)
            if row is None:
                return False
            await session.delete(row)
            await self._adjust_user_counter(session, user_id, "resume_count", -1)
            self._emit_search_event(session, "resume.deleted", user_id, resume_id)
            await session.commit()
            return True

    async def list_resumes(self, user_id: str) -> list[dict[str, Any]]:
        """List all resumes owned by ``user_id``."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Resume), Resume, user_id).order_by(Resume.created_at)
            )
            return [self._resume_to_dict(row) for row in result.scalars().all()]

    async def set_master_resume(self, user_id: str, resume_id: str) -> bool:
        """Set the user's master resume, unsetting their existing master.

        Returns False if the resume doesn't exist for this user. Demote-then-
        promote happens in a single transaction so the per-user partial unique
        index is never violated.
        """
        async with self._session() as session:
            target = await self._get_owned_resume(session, user_id, resume_id)
            if target is None:
                logger.warning("Cannot set master: resume %s not found", resume_id)
                return False

            current = await session.execute(
                Repo.scoped(
                    select(Resume).where(Resume.is_master.is_(True)), Resume, user_id
                )
            )
            for row in current.scalars().all():
                if row.resume_id != resume_id:
                    row.is_master = False
            # Flush the demotions before promoting to satisfy the unique index.
            await session.flush()
            target.is_master = True
            await session.commit()
            return True

    # -- Resume version history (P3 §A, R1–R3) ------------------------------

    @staticmethod
    def _version_meta(row: ResumeVersion) -> dict[str, Any]:
        """Metadata-only projection of a snapshot (never includes ``data_gz``)."""
        return {
            "id": row.id,
            "resume_id": row.resume_id,
            "source": row.source,
            "label": row.label,
            "content_hash": row.content_hash,
            "size_bytes": row.size_bytes,
            "created_at": row.created_at,
        }

    async def create_resume_version(
        self,
        user_id: str,
        resume_id: str,
        *,
        source: str,
        label: str | None,
        content_hash: str,
        data_gz: bytes,
        size_bytes: int,
        template_settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert an immutable snapshot for ``(user_id, resume_id)`` (R1.1)."""
        async with self._session() as session:
            row = ResumeVersion(
                user_id=user_id,
                resume_id=resume_id,
                source=source,
                label=label,
                content_hash=content_hash,
                data_gz=data_gz,
                size_bytes=size_bytes,
                template_settings=template_settings,
            )
            session.add(row)
            await session.commit()
            return self._version_meta(row)

    # -- Persistent AI analysis cache (Universal Analysis Object) -----------

    @staticmethod
    def _artifact_to_dict(row: AnalysisArtifact) -> dict[str, Any]:
        return {
            "id": row.id,
            "artifact_type": row.artifact_type,
            "source_id": row.source_id,
            "related_id": row.related_id,
            "checksum": row.checksum,
            "version": row.version,
            "status": row.status,
            "analysis_data": row.analysis_data,
            "confidence": row.confidence,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def get_analysis_artifact(
        self,
        user_id: str,
        *,
        artifact_type: str,
        source_id: str,
        checksum: str,
        version: str,
    ) -> dict[str, Any] | None:
        """Return the cached artifact for an exact reuse key (None on miss).

        The reuse key ``(user_id, artifact_type, source_id, checksum, version)``
        is unique, so this is at most one row — an exact content+algorithm hit.
        """
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(AnalysisArtifact).where(
                        AnalysisArtifact.artifact_type == artifact_type,
                        AnalysisArtifact.source_id == source_id,
                        AnalysisArtifact.checksum == checksum,
                        AnalysisArtifact.version == version,
                    ),
                    AnalysisArtifact,
                    user_id,
                )
            )
            row = result.scalars().first()
            return self._artifact_to_dict(row) if row else None

    async def put_analysis_artifact(
        self,
        user_id: str,
        *,
        artifact_type: str,
        source_id: str,
        checksum: str,
        version: str,
        analysis_data: dict[str, Any] | None,
        related_id: str | None = None,
        confidence: int | None = None,
        status: str = "ready",
    ) -> dict[str, Any]:
        """Upsert a cached artifact on its reuse key.

        Idempotent: a concurrent producer that races to insert the same reuse
        key collapses onto the existing row (the unique index converts the
        second insert into an update of the stored payload).
        """
        now = _now()
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(AnalysisArtifact).where(
                        AnalysisArtifact.artifact_type == artifact_type,
                        AnalysisArtifact.source_id == source_id,
                        AnalysisArtifact.checksum == checksum,
                        AnalysisArtifact.version == version,
                    ),
                    AnalysisArtifact,
                    user_id,
                )
            )
            row = result.scalars().first()
            if row is None:
                row = AnalysisArtifact(
                    user_id=user_id,
                    artifact_type=artifact_type,
                    source_id=source_id,
                    related_id=related_id,
                    checksum=checksum,
                    version=version,
                    status=status,
                    analysis_data=analysis_data,
                    confidence=confidence,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    # Lost an insert race on the unique reuse key — reload and
                    # update the winner's row so the result is still stored.
                    await session.rollback()
                    result = await session.execute(
                        Repo.scoped(
                            select(AnalysisArtifact).where(
                                AnalysisArtifact.artifact_type == artifact_type,
                                AnalysisArtifact.source_id == source_id,
                                AnalysisArtifact.checksum == checksum,
                                AnalysisArtifact.version == version,
                            ),
                            AnalysisArtifact,
                            user_id,
                        )
                    )
                    row = result.scalars().first()
                    if row is None:
                        raise
                    row.analysis_data = analysis_data
                    row.related_id = related_id
                    row.confidence = confidence
                    row.status = status
                    row.updated_at = _now()
                    await session.commit()
            else:
                row.analysis_data = analysis_data
                row.related_id = related_id
                row.confidence = confidence
                row.status = status
                row.updated_at = now
                await session.commit()
            return self._artifact_to_dict(row)

    async def invalidate_analysis_artifacts(
        self,
        user_id: str,
        resource_id: str,
        *,
        artifact_types: list[str] | None = None,
    ) -> int:
        """Delete artifacts that depend on ``resource_id`` (dependency-aware).

        Matches rows whose ``source_id`` **or** ``related_id`` is
        ``resource_id`` — so editing a resume invalidates both the artifacts
        keyed directly on it and the multi-source artifacts (e.g. a job-fit
        analysis) that merely referenced it. When ``artifact_types`` is given,
        only those kinds are invalidated (so a resume edit can drop tailoring/
        fit caches while leaving unrelated kinds intact). Returns the count.
        """
        stmt = delete(AnalysisArtifact).where(
            (AnalysisArtifact.source_id == resource_id)
            | (AnalysisArtifact.related_id == resource_id)
        )
        if artifact_types:
            stmt = stmt.where(AnalysisArtifact.artifact_type.in_(artifact_types))
        async with self._session() as session:
            result = await session.execute(Repo.scoped(stmt, AnalysisArtifact, user_id))
            await session.commit()
            return int(result.rowcount or 0)

    async def get_latest_resume_version(
        self, user_id: str, resume_id: str
    ) -> dict[str, Any] | None:
        """Return the newest snapshot metadata for a resume (dedupe/undo)."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(ResumeVersion).where(ResumeVersion.resume_id == resume_id),
                    ResumeVersion,
                    user_id,
                )
                .order_by(ResumeVersion.created_at.desc(), ResumeVersion.id.desc())
                .limit(1)
            )
            row = result.scalars().first()
            return self._version_meta(row) if row else None

    async def list_resume_versions(
        self,
        user_id: str,
        resume_id: str,
        *,
        limit: int = 51,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """Metadata-only keyset page (newest first), ``data_gz`` never loaded (R3.1)."""
        stmt = Repo.scoped(
            select(ResumeVersion).where(ResumeVersion.resume_id == resume_id),
            ResumeVersion,
            user_id,
        )
        if cursor:
            created_at, _, cid = cursor.partition("|")
            # Keyset: rows strictly older than the cursor (created_at, id) desc.
            stmt = stmt.where(
                (ResumeVersion.created_at < created_at)
                | ((ResumeVersion.created_at == created_at) & (ResumeVersion.id < cid))
            )
        stmt = stmt.order_by(
            ResumeVersion.created_at.desc(), ResumeVersion.id.desc()
        ).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return [self._version_meta(row) for row in result.scalars().all()]

    async def get_resume_version(
        self, user_id: str, version_id: str
    ) -> dict[str, Any] | None:
        """Return a single snapshot incl. ``data_gz`` (None if absent/foreign)."""
        async with self._session() as session:
            row = await session.get(ResumeVersion, version_id)
            if not Repo.owns(row, user_id):
                return None
            return {
                **self._version_meta(row),
                "data_gz": row.data_gz,
                "template_settings": getattr(row, "template_settings", None),
            }

    async def count_resume_versions(self, user_id: str, resume_id: str) -> int:
        """Count snapshots for a resume (scoped)."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(func.count())
                    .select_from(ResumeVersion)
                    .where(ResumeVersion.resume_id == resume_id),
                    ResumeVersion,
                    user_id,
                )
            )
            return int(result.scalar() or 0)

    async def prune_resume_versions(
        self, user_id: str, resume_id: str, cap: int
    ) -> int:
        """Prune oldest non-``original`` snapshots beyond ``cap`` (R1.3).

        The single oldest ``original`` snapshot is always retained. Returns the
        number of rows deleted. Idempotent and scoped.
        """
        if cap < 1:
            return 0
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        Repo.scoped(
                            select(ResumeVersion).where(
                                ResumeVersion.resume_id == resume_id
                            ),
                            ResumeVersion,
                            user_id,
                        ).order_by(
                            ResumeVersion.created_at.desc(), ResumeVersion.id.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) <= cap:
                return 0
            # Retain the oldest ``original`` snapshot no matter what, then fill
            # the remaining budget with the newest rows so the TOTAL never
            # exceeds ``cap`` (rows is newest→oldest).
            originals = [r for r in rows if r.source == "original"]
            protected_id = originals[-1].id if originals else None
            keep: set[str] = set()
            if protected_id is not None:
                keep.add(protected_id)
            for row in rows:  # newest first
                if len(keep) >= cap:
                    break
                keep.add(row.id)
            deleted = 0
            for row in rows:
                if row.id not in keep:
                    await session.delete(row)
                    deleted += 1
            if deleted:
                await session.commit()
            return deleted

    async def find_snapshot_before_last_ai(
        self, user_id: str, resume_id: str
    ) -> dict[str, Any] | None:
        """Return the snapshot immediately preceding the last ``ai`` snapshot (R2.2).

        "Preceding" = the newest snapshot created strictly before the most recent
        ``ai`` snapshot. Returns metadata only; ``None`` when there is no ``ai``
        snapshot or nothing precedes it.
        """
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        Repo.scoped(
                            select(ResumeVersion).where(
                                ResumeVersion.resume_id == resume_id
                            ),
                            ResumeVersion,
                            user_id,
                        ).order_by(
                            ResumeVersion.created_at.desc(), ResumeVersion.id.desc()
                        )
                    )
                )
                .scalars()
                .all()
            )
            # rows are newest→oldest; find the first ``ai`` then the next row.
            for i, row in enumerate(rows):
                if row.source == "ai" and i + 1 < len(rows):
                    return self._version_meta(rows[i + 1])
            return None

    async def restore_resume_version(
        self,
        user_id: str,
        resume_id: str,
        *,
        processed_data: dict[str, Any],
        template_settings: dict[str, Any] | None = None,
        expected_updated_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Apply restored ``processed_data`` to a resume with an optional CAS (R2.1/2.3).

        Returns the updated resume dict, or ``None`` on a CAS conflict / missing
        resume (the caller maps to 409/404). The read-check-write happens in one
        transaction so concurrent restores are last-writer-safe (no corruption).
        """
        async with self._session() as session:
            row = await self._get_owned_resume(session, user_id, resume_id)
            if row is None:
                return None
            if expected_updated_at is not None and row.updated_at != expected_updated_at:
                return None
            row.processed_data = processed_data
            # Keep the stored serialization in lock-step with processed_data so
            # ``content`` never drifts from the structured data (mirrors the
            # manual-save path which writes both).
            row.content = json.dumps(processed_data, indent=2)
            row.content_type = "json"
            row.processing_status = "ready"
            # Restore the snapshot's appearance too (Bug #3). Older snapshots
            # captured no template → leave the current one untouched.
            if template_settings is not None:
                row.template_settings = template_settings
            row.updated_at = _now()
            await session.commit()
            return self._resume_to_dict(row)

    # -- Professional Profile (docs/architecture/PROFILE_SYSTEM_PLAN.md) ----

    @staticmethod
    def _profile_to_dict(row: Profile) -> dict[str, Any]:
        """Plain-dict projection of a profile row (never an ORM object)."""
        return {
            "id": row.id,
            "user_id": row.user_id,
            "data": row.data,
            "completeness": row.completeness,
            "version": row.version,
            "public_slug": row.public_slug,
            "visibility": row.visibility,
            "public_theme": row.public_theme,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def get_profile(self, user_id: str) -> dict[str, Any] | None:
        """Return the user's profile (one per user), or ``None`` if not created."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Profile), Profile, user_id)
            )
            row = result.scalars().first()
            return self._profile_to_dict(row) if row else None

    async def create_profile(
        self,
        user_id: str,
        *,
        data: dict[str, Any],
        completeness: int = 0,
    ) -> dict[str, Any]:
        """Create the user's profile row (single-source-of-truth, one per user).

        Uses a per-user lock + idempotent read so a concurrent first-load does
        not violate the ``UNIQUE(user_id)`` invariant (returns the existing row
        instead of raising).
        """
        lock = await self._master_lock(f"profile:{user_id}")
        async with lock:
            existing = await self.get_profile(user_id)
            if existing is not None:
                return existing
            now = _now()
            async with self._session() as session:
                row = Profile(
                    user_id=user_id,
                    data=data,
                    completeness=completeness,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                try:
                    await session.commit()
                except IntegrityError:
                    # Lost a race despite the lock (e.g. cross-process): fall back
                    # to the now-present row.
                    await session.rollback()
                    return await self.get_profile(user_id)  # type: ignore[return-value]
                return self._profile_to_dict(row)

    async def update_profile_cas(
        self,
        user_id: str,
        *,
        data: dict[str, Any],
        completeness: int,
        base_version: int,
    ) -> tuple[str, dict[str, Any] | None]:
        """Atomic optimistic-concurrency profile update (version CAS).

        Mirrors :meth:`update_resume_cas`: a single conditional UPDATE guarded by
        ``version == base_version`` so two concurrent writers with the same base
        cannot both succeed. Returns ``("updated", dict)`` /
        ``("conflict", current_dict)`` / ``("not_found", None)``.
        """
        async with self._session() as session:
            stmt = (
                sa_update(Profile)
                .where(
                    Profile.user_id == user_id,
                    Profile.version == base_version,
                )
                .values(
                    data=data,
                    completeness=completeness,
                    version=Profile.version + 1,
                    updated_at=_now(),
                )
            )
            result = await session.execute(stmt)
            if result.rowcount == 1:
                await session.commit()
                refreshed = await session.execute(
                    Repo.scoped(select(Profile), Profile, user_id)
                )
                row = refreshed.scalars().first()
                return "updated", self._profile_to_dict(row) if row else None
            await session.rollback()
            existing = await session.execute(
                Repo.scoped(select(Profile), Profile, user_id)
            )
            row = existing.scalars().first()
            if row is None:
                return "not_found", None
            return "conflict", self._profile_to_dict(row)

    # -- Public sharing (P7) ------------------------------------------------

    async def slug_exists(self, slug: str, *, exclude_user_id: str | None = None) -> bool:
        """Whether ``slug`` is already claimed (optionally ignoring one owner)."""
        async with self._session() as session:
            stmt = select(Profile).where(Profile.public_slug == slug)
            result = await session.execute(stmt)
            row = result.scalars().first()
            if row is None:
                return False
            if exclude_user_id is not None and row.user_id == exclude_user_id:
                return False
            return True

    async def set_profile_publication(
        self,
        user_id: str,
        *,
        public_slug: str | None,
        visibility: str,
        public_theme: str | None = None,
    ) -> dict[str, Any] | None:
        """Set the profile's publish state (slug + visibility + theme) for ``user_id``.

        Returns the updated profile dict, or ``None`` if the user has no profile.
        Slug uniqueness is enforced at the DB (unique index) and pre-checked by
        the caller; a lost race raises ``IntegrityError`` which surfaces as a
        retryable 409 upstream.
        """
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Profile), Profile, user_id)
            )
            row = result.scalars().first()
            if row is None:
                return None
            if public_slug is not None:
                row.public_slug = public_slug
            row.visibility = visibility
            if public_theme is not None:
                row.public_theme = public_theme
            row.updated_at = _now()
            await session.commit()
            return self._profile_to_dict(row)

    async def get_profile_by_slug(self, slug: str) -> dict[str, Any] | None:
        """Anonymous lookup by public slug (no user scoping — public surface).

        Returns the profile dict regardless of visibility; the caller enforces
        the private/unlisted/public gate. ``None`` if the slug is unclaimed.
        """
        async with self._session() as session:
            result = await session.execute(
                select(Profile).where(Profile.public_slug == slug)
            )
            row = result.scalars().first()
            return self._profile_to_dict(row) if row else None

    # -- Profile version snapshots (mirror resume_versions) -----------------

    @staticmethod
    def _profile_version_meta(row: ProfileVersion) -> dict[str, Any]:
        """Metadata-only projection of a profile snapshot (no ``data_gz``)."""
        return {
            "id": row.id,
            "profile_id": row.profile_id,
            "source": row.source,
            "label": row.label,
            "content_hash": row.content_hash,
            "size_bytes": row.size_bytes,
            "created_at": row.created_at,
        }

    async def create_profile_version(
        self,
        user_id: str,
        profile_id: str,
        *,
        source: str,
        label: str | None,
        content_hash: str,
        data_gz: bytes,
        size_bytes: int,
    ) -> dict[str, Any]:
        """Insert an immutable snapshot for ``(user_id, profile_id)``."""
        async with self._session() as session:
            row = ProfileVersion(
                user_id=user_id,
                profile_id=profile_id,
                source=source,
                label=label,
                content_hash=content_hash,
                data_gz=data_gz,
                size_bytes=size_bytes,
            )
            session.add(row)
            await session.commit()
            return self._profile_version_meta(row)

    async def get_latest_profile_version(
        self, user_id: str, profile_id: str
    ) -> dict[str, Any] | None:
        """Return the newest profile snapshot metadata (dedupe/debounce check)."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(ProfileVersion).where(
                        ProfileVersion.profile_id == profile_id
                    ),
                    ProfileVersion,
                    user_id,
                )
                .order_by(
                    ProfileVersion.created_at.desc(), ProfileVersion.id.desc()
                )
                .limit(1)
            )
            row = result.scalars().first()
            return self._profile_version_meta(row) if row else None

    async def list_profile_versions(
        self,
        user_id: str,
        profile_id: str,
        *,
        limit: int = 51,
        cursor: str | None = None,
    ) -> list[dict[str, Any]]:
        """Metadata-only keyset page (newest first); ``data_gz`` never loaded."""
        stmt = Repo.scoped(
            select(ProfileVersion).where(ProfileVersion.profile_id == profile_id),
            ProfileVersion,
            user_id,
        )
        if cursor:
            created_at, _, cid = cursor.partition("|")
            stmt = stmt.where(
                (ProfileVersion.created_at < created_at)
                | (
                    (ProfileVersion.created_at == created_at)
                    & (ProfileVersion.id < cid)
                )
            )
        stmt = stmt.order_by(
            ProfileVersion.created_at.desc(), ProfileVersion.id.desc()
        ).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return [self._profile_version_meta(row) for row in result.scalars().all()]

    async def get_profile_version(
        self, user_id: str, version_id: str
    ) -> dict[str, Any] | None:
        """Return a single profile snapshot incl. ``data_gz`` (None if foreign)."""
        async with self._session() as session:
            row = await session.get(ProfileVersion, version_id)
            if not Repo.owns(row, user_id):
                return None
            return {**self._profile_version_meta(row), "data_gz": row.data_gz}

    async def prune_profile_versions(
        self, user_id: str, profile_id: str, cap: int
    ) -> int:
        """Prune oldest snapshots beyond ``cap``; the oldest ``migration`` kept.

        Mirrors :meth:`prune_resume_versions` (which protects ``original``): here
        the baseline snapshot is the ``migration`` source. Returns rows deleted.
        """
        if cap < 1:
            return 0
        async with self._session() as session:
            rows = (
                (
                    await session.execute(
                        Repo.scoped(
                            select(ProfileVersion).where(
                                ProfileVersion.profile_id == profile_id
                            ),
                            ProfileVersion,
                            user_id,
                        ).order_by(
                            ProfileVersion.created_at.desc(),
                            ProfileVersion.id.desc(),
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) <= cap:
                return 0
            baselines = [r for r in rows if r.source == "migration"]
            protected_id = baselines[-1].id if baselines else None
            keep: set[str] = set()
            if protected_id is not None:
                keep.add(protected_id)
            for row in rows:  # newest first
                if len(keep) >= cap:
                    break
                keep.add(row.id)
            deleted = 0
            for row in rows:
                if row.id not in keep:
                    await session.delete(row)
                    deleted += 1
            if deleted:
                await session.commit()
            return deleted

    # -- Job operations -----------------------------------------------------

    async def _get_owned_job(
        self, session: AsyncSession, user_id: str, job_id: str
    ) -> Job | None:
        """Load a job by id scoped to ``user_id`` (None if absent/foreign)."""
        result = await session.execute(
            Repo.scoped(select(Job).where(Job.job_id == job_id), Job, user_id)
        )
        return result.scalars().first()

    async def create_job(
        self, user_id: str, content: str, resume_id: str | None = None
    ) -> dict[str, Any]:
        """Create a new job description entry owned by ``user_id``."""
        job_id = str(uuid4())
        now = _now()
        async with self._session() as session:
            session.add(
                Job(
                    job_id=job_id,
                    user_id=user_id,
                    content=content,
                    resume_id=resume_id,
                    created_at=now,
                    metadata_json={},
                )
            )
            self._emit_search_event(session, "job.upserted", user_id, job_id)
            await session.commit()
        return {
            "job_id": job_id,
            "content": content,
            "resume_id": resume_id,
            "created_at": now,
        }

    async def get_job(self, user_id: str, job_id: str) -> dict[str, Any] | None:
        """Get a job by ID scoped to ``user_id`` (dynamic fields flattened)."""
        async with self._session() as session:
            row = await self._get_owned_job(session, user_id, job_id)
            return self._job_to_dict(row) if row else None

    async def update_job(
        self, user_id: str, job_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a job owned by ``user_id``.

        Core columns are set directly; every other key is merged into
        ``metadata_json`` so dynamic pipeline fields (``preview_hash``,
        ``job_keywords``, ``company``/``role``, …) round-trip through
        ``get_job`` as top-level keys.
        """
        async with self._session() as session:
            row = await self._get_owned_job(session, user_id, job_id)
            if row is None:
                return None
            meta = dict(row.metadata_json or {})
            for key, value in updates.items():
                if key in _JOB_CORE_FIELDS:
                    setattr(row, key, value)
                else:
                    meta[key] = value
            # Reassign so SQLAlchemy detects the JSON mutation.
            row.metadata_json = meta
            self._emit_search_event(session, "job.upserted", user_id, job_id)
            await session.commit()
            return self._job_to_dict(row)

    async def delete_job(self, user_id: str, job_id: str) -> bool:
        """Delete a job owned by ``user_id`` (cleans up an orphaned manual-add job)."""
        async with self._session() as session:
            row = await self._get_owned_job(session, user_id, job_id)
            if row is None:
                return False
            await session.delete(row)
            self._emit_search_event(session, "job.deleted", user_id, job_id)
            await session.commit()
            return True

    async def list_jobs(self, user_id: str) -> list[dict[str, Any]]:
        """List all jobs owned by ``user_id`` (search rebuild)."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Job), Job, user_id).order_by(Job.created_at)
            )
            return [self._job_to_dict(row) for row in result.scalars().all()]

    # -- Improvement operations ---------------------------------------------

    async def create_improvement(
        self,
        user_id: str,
        original_resume_id: str,
        tailored_resume_id: str,
        job_id: str,
        improvements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create an improvement result entry owned by ``user_id``."""
        request_id = str(uuid4())
        now = _now()
        async with self._session() as session:
            session.add(
                Improvement(
                    request_id=request_id,
                    user_id=user_id,
                    original_resume_id=original_resume_id,
                    tailored_resume_id=tailored_resume_id,
                    job_id=job_id,
                    improvements=improvements,
                    created_at=now,
                )
            )
            await session.commit()
        return {
            "request_id": request_id,
            "original_resume_id": original_resume_id,
            "tailored_resume_id": tailored_resume_id,
            "job_id": job_id,
            "improvements": improvements,
            "created_at": now,
        }

    async def get_improvement_by_tailored_resume(
        self, user_id: str, tailored_resume_id: str
    ) -> dict[str, Any] | None:
        """Get an improvement record by tailored resume ID, scoped to ``user_id``."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(Improvement).where(
                        Improvement.tailored_resume_id == tailored_resume_id
                    ),
                    Improvement,
                    user_id,
                )
            )
            row = result.scalars().first()
            return self._improvement_to_dict(row) if row else None

    # -- Application (tracker) operations -----------------------------------

    async def _get_owned_application(
        self, session: AsyncSession, user_id: str, application_id: str
    ) -> Application | None:
        """Load an application by id scoped to ``user_id`` (None if absent/foreign)."""
        result = await session.execute(
            Repo.scoped(
                select(Application).where(Application.application_id == application_id),
                Application,
                user_id,
            )
        )
        return result.scalars().first()

    async def _next_position(
        self, session: AsyncSession, user_id: str, status: str
    ) -> int:
        result = await session.execute(
            Repo.scoped(
                select(func.count())
                .select_from(Application)
                .where(Application.status == status),
                Application,
                user_id,
            )
        )
        return int(result.scalar() or 0)

    async def _renumber(
        self, session: AsyncSession, user_id: str, status: str
    ) -> None:
        """Renumber a user's column positions to a contiguous 0..n-1 sequence."""
        result = await session.execute(
            Repo.scoped(
                select(Application).where(Application.status == status),
                Application,
                user_id,
            ).order_by(Application.position, Application.created_at)
        )
        for index, row in enumerate(result.scalars().all()):
            if row.position != index:
                row.position = index

    async def create_application(
        self,
        user_id: str,
        job_id: str,
        resume_id: str,
        master_resume_id: str | None = None,
        status: str = "applied",
        company: str | None = None,
        role: str | None = None,
        applied_at: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a tracker card for ``user_id``, deduped on (user, job, resume).

        If a card for the same job+resume already exists for this user it is
        returned as-is (survives double-submit / retried confirms).
        """
        async with self._session() as session:
            existing = await session.execute(
                Repo.scoped(
                    select(Application).where(
                        Application.job_id == job_id,
                        Application.resume_id == resume_id,
                    ),
                    Application,
                    user_id,
                )
            )
            found = existing.scalars().first()
            if found is not None:
                return self._application_to_dict(found)

            now = _now()
            if applied_at is None and status != "saved":
                applied_at = now
            position = await self._next_position(session, user_id, status)
            row = Application(
                application_id=str(uuid4()),
                user_id=user_id,
                job_id=job_id,
                resume_id=resume_id,
                master_resume_id=master_resume_id,
                status=status,
                company=company,
                role=role,
                applied_at=applied_at,
                notes=notes,
                position=position,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await self._adjust_user_counter(session, user_id, "application_count", +1)
            self._emit_search_event(session, "application.upserted", user_id, row.application_id)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent create won the (job_id, resume_id) unique
                # constraint — return the existing card instead of duplicating.
                await session.rollback()
                dup = await session.execute(
                    Repo.scoped(
                        select(Application).where(
                            Application.job_id == job_id,
                            Application.resume_id == resume_id,
                        ),
                        Application,
                        user_id,
                    )
                )
                found = dup.scalars().first()
                if found is not None:
                    logger.debug(
                        "Deduped concurrent application create for job=%s resume=%s",
                        job_id,
                        resume_id,
                    )
                    return self._application_to_dict(found)
                raise
            return self._application_to_dict(row)

    async def list_applications(
        self, user_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        """List a user's applications ordered by (status, position)."""
        async with self._session() as session:
            stmt = Repo.scoped(select(Application), Application, user_id)
            if status is not None:
                stmt = stmt.where(Application.status == status)
            stmt = stmt.order_by(Application.status, Application.position)
            result = await session.execute(stmt)
            return [self._application_to_dict(row) for row in result.scalars().all()]

    async def get_application(
        self, user_id: str, application_id: str
    ) -> dict[str, Any] | None:
        """Get an application by ID scoped to ``user_id``."""
        async with self._session() as session:
            row = await self._get_owned_application(session, user_id, application_id)
            return self._application_to_dict(row) if row else None

    async def update_application(
        self, user_id: str, application_id: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a user's application; renumber columns when status/position change.

        ``position`` is interpreted as the desired index within the (possibly
        new) ``status`` column; siblings are renumbered server-side so the
        column stays a contiguous 0..n-1 sequence.
        """
        async with self._session() as session:
            row = await self._get_owned_application(session, user_id, application_id)
            if row is None:
                return None

            old_status = row.status
            new_status = updates.get("status", old_status)
            target_position = updates.get("position", None)

            for key in ("company", "role", "applied_at", "notes"):
                if key in updates:
                    setattr(row, key, updates[key])

            moved = "status" in updates or "position" in updates
            if moved:
                row.status = new_status
                # Park it out of the way, renumber both columns, then reinsert.
                row.position = 10_000_000
                await session.flush()
                if old_status != new_status:
                    await self._renumber(session, user_id, old_status)
                # Renumber the target column excluding this row, then splice in.
                siblings = await session.execute(
                    Repo.scoped(
                        select(Application).where(
                            Application.status == new_status,
                            Application.application_id != application_id,
                        ),
                        Application,
                        user_id,
                    ).order_by(Application.position, Application.created_at)
                )
                ordered = list(siblings.scalars().all())
                if target_position is None or target_position > len(ordered):
                    target_position = len(ordered)
                if target_position < 0:
                    target_position = 0
                ordered.insert(target_position, row)
                for index, item in enumerate(ordered):
                    item.position = index

            row.updated_at = _now()
            self._emit_search_event(session, "application.upserted", user_id, application_id)
            await session.commit()
            return self._application_to_dict(row)

    async def bulk_update_applications(
        self, user_id: str, application_ids: list[str], status: str
    ) -> int:
        """Move many of a user's applications to the end of ``status``.

        Returns the count actually moved (foreign/absent ids are skipped).
        """
        moved = 0
        async with self._session() as session:
            affected_old: set[str] = set()
            for application_id in application_ids:
                row = await self._get_owned_application(session, user_id, application_id)
                if row is None:
                    continue
                affected_old.add(row.status)
                row.status = status
                row.position = 20_000_000 + moved  # provisional, renumbered below
                row.updated_at = _now()
                moved += 1
            await session.flush()
            for old_status in affected_old - {status}:
                await self._renumber(session, user_id, old_status)
            await self._renumber(session, user_id, status)
            await session.commit()
        return moved

    async def delete_application(self, user_id: str, application_id: str) -> bool:
        """Delete a user's application; renumber its column."""
        async with self._session() as session:
            row = await self._get_owned_application(session, user_id, application_id)
            if row is None:
                return False
            status = row.status
            await session.delete(row)
            await self._adjust_user_counter(session, user_id, "application_count", -1)
            self._emit_search_event(session, "application.deleted", user_id, application_id)
            await session.flush()
            await self._renumber(session, user_id, status)
            await session.commit()
            return True

    async def bulk_delete_applications(
        self, user_id: str, application_ids: list[str]
    ) -> int:
        """Delete many of a user's applications; renumber affected columns."""
        deleted = 0
        async with self._session() as session:
            affected: set[str] = set()
            for application_id in application_ids:
                row = await self._get_owned_application(session, user_id, application_id)
                if row is None:
                    continue
                affected.add(row.status)
                await session.delete(row)
                deleted += 1
            if deleted:
                await self._adjust_user_counter(
                    session, user_id, "application_count", -deleted
                )
            await session.flush()
            for status in affected:
                await self._renumber(session, user_id, status)
            await session.commit()
        return deleted

    # -- Encrypted API key store (sync; read on the LLM hot path) -----------

    def _owned_api_key(self, session: Session, user_id: str, provider: str) -> ApiKey | None:
        """Load one provider key scoped to ``user_id`` (sync; None if absent/foreign)."""
        return session.execute(
            Repo.scoped(
                select(ApiKey).where(ApiKey.provider == provider), ApiKey, user_id
            )
        ).scalars().first()

    def get_api_key_ciphertexts(self, user_id: str) -> dict[str, str]:
        """Return ``{provider: ciphertext}`` for ``user_id``'s stored keys (sync).

        Per-user (R10.6): one user's provider key never appears in another
        user's key set, so it can never serve another user's LLM calls.
        """
        with self._sync() as session:
            rows = session.execute(
                Repo.scoped(select(ApiKey), ApiKey, user_id)
            ).scalars().all()
            return {row.provider: row.ciphertext for row in rows}

    def set_api_key_ciphertext(self, user_id: str, provider: str, ciphertext: str) -> None:
        """Upsert one provider's ciphertext for ``user_id`` (sync)."""
        with self._sync() as session:
            row = self._owned_api_key(session, user_id, provider)
            if row is None:
                session.add(
                    ApiKey(
                        provider=provider,
                        user_id=user_id,
                        ciphertext=ciphertext,
                        updated_at=_now(),
                    )
                )
            else:
                row.ciphertext = ciphertext
                row.updated_at = _now()
            session.commit()

    def delete_api_key(self, user_id: str, provider: str) -> None:
        """Delete one provider's key for ``user_id`` (sync)."""
        with self._sync() as session:
            row = self._owned_api_key(session, user_id, provider)
            if row is not None:
                session.delete(row)
                session.commit()

    def clear_api_keys(self, user_id: str) -> None:
        """Delete all of ``user_id``'s stored keys (sync)."""
        with self._sync() as session:
            session.execute(Repo.scoped(delete(ApiKey), ApiKey, user_id))
            session.commit()

    def replace_api_keys(self, user_id: str, ciphertexts: dict[str, str]) -> None:
        """Atomically replace ``user_id``'s key store (clear + insert in one txn).

        A single transaction means a failure mid-write can't leave the store
        half-cleared and wipe a user's previously saved keys. Only this user's
        keys are cleared/replaced — other users' keys are untouched (R10.6).
        """
        with self._sync() as session:
            session.execute(Repo.scoped(delete(ApiKey), ApiKey, user_id))
            now = _now()
            for provider, ciphertext in ciphertexts.items():
                if ciphertext:
                    session.add(
                        ApiKey(
                            provider=provider,
                            user_id=user_id,
                            ciphertext=ciphertext,
                            updated_at=now,
                        )
                    )
            session.commit()

    # -- Stats / maintenance ------------------------------------------------

    async def get_stats(self, user_id: str) -> dict[str, Any]:
        """Get database statistics scoped to ``user_id``."""
        async with self._session() as session:
            resumes = await session.scalar(
                Repo.scoped(select(func.count()).select_from(Resume), Resume, user_id)
            )
            jobs = await session.scalar(
                Repo.scoped(select(func.count()).select_from(Job), Job, user_id)
            )
            improvements = await session.scalar(
                Repo.scoped(
                    select(func.count()).select_from(Improvement), Improvement, user_id
                )
            )
            master = await session.execute(
                Repo.scoped(
                    select(Resume.resume_id).where(Resume.is_master.is_(True)),
                    Resume,
                    user_id,
                ).limit(1)
            )
            return {
                "total_resumes": int(resumes or 0),
                "total_jobs": int(jobs or 0),
                "total_improvements": int(improvements or 0),
                "has_master_resume": master.first() is not None,
            }

    async def reset_database(self, user_id: str) -> None:
        """Reset ``user_id``'s document data and clear uploads.

        Clears the user's resumes/jobs/improvements **and** tracker applications
        (leaving orphaned cards after a full data reset would be a bug). Scoped
        to ``user_id`` so a reset never touches another user's data. Encrypted
        ``api_keys`` are preserved — matching the pre-existing behavior where a
        reset never wiped the user's stored credentials.
        """
        async with self._session() as session:
            await session.execute(Repo.scoped(delete(Application), Application, user_id))
            await session.execute(Repo.scoped(delete(Improvement), Improvement, user_id))
            await session.execute(Repo.scoped(delete(Job), Job, user_id))
            await session.execute(Repo.scoped(delete(Resume), Resume, user_id))
            # Reset the denormalized usage counters for this user (R11.3).
            user = await session.get(User, user_id)
            if user is not None:
                user.resume_count = 0
                user.application_count = 0
            await session.commit()

        uploads_dir = settings.data_dir / "uploads"
        if uploads_dir.exists():
            shutil.rmtree(uploads_dir)
            uploads_dir.mkdir(parents=True, exist_ok=True)

    async def _adjust_user_counter(
        self, session: AsyncSession, user_id: str, field: str, delta: int
    ) -> None:
        """Best-effort increment of a denormalized ``users`` usage counter (R11.3).

        Keeps ``users.resume_count`` / ``users.application_count`` fresh for the
        admin list without a per-row N+1 count. ``users`` is non-owned, so this
        is a plain (unscoped-by-design) update of the owner's own counter within
        the caller's transaction; the RollupJob reconciliation corrects any drift.
        Clamped at zero so a double-delete can't drive a counter negative.
        """
        user = await session.get(User, user_id)
        if user is None:
            return
        current = getattr(user, field, 0) or 0
        setattr(user, field, max(0, current + delta))

    @staticmethod
    def _emit_search_event(session, event_type: str, user_id: str | None, node_id: str) -> None:
        """Enqueue a search-index domain event in the caller's transaction (R7.1).

        Transactional outbox: the event commits atomically with the owning
        change, so the async SearchIndexer (and any future consumer) sees it
        exactly when the change is durable — a consumer failure never fails the
        user's write. Payload is a lightweight ``{node_id}``; the indexer
        re-reads current, content-safe fields from the source at index time.
        """
        session.add(
            Outbox(user_id=user_id, event_type=event_type, payload={"node_id": node_id}, created_at=_now())
        )

    async def purge_user_owned_data(self, user_id: str) -> dict[str, int]:
        """Irreversibly delete every owned row for ``user_id`` (admin purge, R8.3).

        Deletes the user's owned rows in **FK-safe order** (improvements →
        applications → jobs → resumes → api_keys) inside a single transaction, so
        the purge is atomic per user. Set-based deletes (no per-row N+1) scoped to
        ``user_id`` via ``Repo.scoped`` — the same tenant-isolation boundary as
        every other owned mutation, and idempotent (a second run deletes nothing).
        Returns per-table deletion counts. Non-owned rows (sessions,
        oauth_identities, the user row itself) are handled by the purge job, which
        deletes them after this in its own FK-safe order; ``audit_log`` is **never**
        purged (R8.4). Future P3 owned tables (resume_versions/notifications) are
        added to this ordered list when they land.
        """
        counts: dict[str, int] = {}
        async with self._session() as session:
            for label, model in (
                ("improvements", Improvement),
                ("applications", Application),
                ("resume_versions", ResumeVersion),
                ("notifications", Notification),
                ("notification_prefs", NotificationPref),
                ("user_unread_counts", UserUnreadCount),
                ("reminders", Reminder),
                ("interviews", Interview),
                ("search_documents", SearchDocument),
                ("jobs", Job),
                ("resumes", Resume),
                ("api_keys", ApiKey),
            ):
                result = await session.execute(Repo.scoped(delete(model), model, user_id))
                counts[label] = int(result.rowcount or 0)
            # Outbox rows are a system table (no FK); prune this user's events so
            # no orphaned events linger post-purge.
            outbox_result = await session.execute(
                delete(Outbox).where(Outbox.user_id == user_id)
            )
            counts["outbox"] = int(outbox_result.rowcount or 0)
            await session.commit()
        return counts


# Global database instance
db = Database()
