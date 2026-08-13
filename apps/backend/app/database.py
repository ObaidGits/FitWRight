"""SQLAlchemy data layer for FitWright (SQLite local, Postgres hosted - ADR-13).

This is a behavior-preserving replacement for the original TinyDB wrapper. The
``Database`` facade keeps the same method names/signatures and returns **plain
dicts** (never ORM rows), so the ~50 call sites only needed ``await`` added.

Two engines back one database, resolved from ``settings.effective_database_url``:
- an **async** engine (``aiosqlite`` / ``asyncpg``) for the document tables and
  applications;
- a **sync** engine (SQLite DBAPI / ``psycopg`` v3) for the encrypted
  ``api_keys`` table, which is read on the synchronous LLM hot path
  (``get_llm_config`` -> ``resolve_api_key``).

Locally both engines point at one SQLite file (zero-config); hosted they point
at the same Postgres server (schema owned by Alembic). See ``app.db_engine`` for
dialect selection and pooling.
"""

import asyncio
import json
import logging
import shutil
import weakref
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from sqlalchemy import and_, delete, func, select, text, update as sa_update
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
    DiscoveryCache,
    DiscoveryResult,
    DiscoveryRun,
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
    SiteRecipeModel,
    TailorPreview,
    User,
    UserErrorReport,
    UserLlmConfig,
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


def _invalidate_api_key_cache(user_id: str) -> None:
    """Drop the process-level decrypted-key cache for ``user_id`` after a write.

    Hooked into every key mutation so the LLM hot-path cache (app.config) never
    serves a rotated/cleared key. Lazy import + defensive: cache invalidation
    must never break a key write.
    """
    try:
        from app.config import invalidate_api_key_cache

        invalidate_api_key_cache(user_id)
    except Exception:  # pragma: no cover - invalidation must never break a write
        pass


class Database:
    """Async SQLAlchemy facade for FitWright data.

    Every owned-resource method takes a **mandatory** ``user_id`` and routes its
    query through :class:`app.repository.Repo` so cross-user reads/writes are
    impossible (ADR-4, R10.2). A foreign or absent id resolves to ``None`` (the
    router turns that into a 404 - no existence disclosure, R10.3). This is the
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
        # Per-owner in-process locks avoid needless local contention; weak
        # values disappear after the last waiter releases its lock, so this map
        # cannot grow forever with historical user IDs. Correctness does not
        # depend on these locks: `_master_transaction` acquires a database-level
        # owner/write lock for cross-worker serialization.
        self._master_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._master_locks_guard = asyncio.Lock()

    @classmethod
    def from_url(cls, url: str | None = None) -> "Database":
        """Construct a Database from an explicit URL (used by discovery tests)."""
        return cls(url)

    async def dispose(self) -> None:
        """Alias for close() — compatibility with discovery tests."""
        await self.close()

    async def _master_lock(self, user_id: str) -> asyncio.Lock:
        """Return a self-evicting local contention lock for ``user_id``."""
        async with self._master_locks_guard:
            lock = self._master_locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._master_locks[user_id] = lock
            return lock

    @asynccontextmanager
    async def _master_transaction(
        self, user_id: str
    ) -> AsyncIterator[AsyncSession]:
        """Serialize one owner's master mutation at the storage layer.

        PostgreSQL locks the durable owner row, which coordinates every Uvicorn
        worker. SQLite uses ``BEGIN IMMEDIATE`` because ``FOR UPDATE`` is ignored
        there; acquiring the write lock before reading prevents two processes
        from both observing an empty master slot.
        """
        async with self._session() as session:
            dialect = session.bind.dialect.name if session.bind is not None else ""
            if dialect == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
                try:
                    yield session
                    await session.commit()
                except BaseException:
                    await session.rollback()
                    raise
                return

            async with session.begin():
                await session.execute(
                    select(User.id).where(User.id == user_id).with_for_update()
                )
                yield session

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

    @staticmethod
    def _user_error_report_to_dict(row: UserErrorReport) -> dict[str, Any]:
        return {
            "id": row.id,
            "user_id": row.user_id,
            "client_report_id": row.client_report_id,
            "issue_type": row.issue_type,
            "message": row.message,
            "error_code": row.error_code,
            "http_status": row.http_status,
            "retryable": row.retryable,
            "api_method": row.api_method,
            "api_route": row.api_route,
            "operation_request_id": row.operation_request_id,
            "api_request_id": row.api_request_id,
            "pipeline_stage": row.pipeline_stage,
            "stream_phase": row.stream_phase,
            "fallback_safe": row.fallback_safe,
            "created_at": row.created_at,
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
        """Create a resume and assign master status transactionally per owner.

        The local lock reduces contention, while ``_master_transaction`` is the
        cross-worker correctness boundary. A failed/processing master is
        demoted and the replacement inserted in the same transaction, so no
        observer can see a committed half-transition.
        """
        lock = await self._master_lock(user_id)
        async with lock:
            resume_id = str(uuid4())
            now = _now()
            is_master = False
            try:
                async with self._master_transaction(user_id) as session:
                    current_result = await session.execute(
                        Repo.scoped(
                            select(Resume).where(Resume.is_master.is_(True)),
                            Resume,
                            user_id,
                        )
                    )
                    current = current_result.scalars().first()
                    is_master = current is None
                    if current is not None and current.processing_status in (
                        "failed",
                        "processing",
                    ):
                        current.is_master = False
                        await session.flush()
                        is_master = True

                    session.add(
                        Resume(
                            resume_id=resume_id,
                            user_id=user_id,
                            content=content,
                            content_type=content_type,
                            filename=filename,
                            is_master=is_master,
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
                    self._emit_search_event(
                        session, "resume.upserted", user_id, resume_id
                    )
            except IntegrityError:
                # The partial unique index is the final backstop if an owner row
                # was missing/legacy and therefore could not be locked. Recover
                # only when another committed master proves this was that race;
                # unrelated integrity failures must remain visible.
                if not is_master or await self.get_master_resume(user_id) is None:
                    raise
                return await self.create_resume(
                    user_id,
                    content=content,
                    content_type=content_type,
                    filename=filename,
                    is_master=False,
                    processed_data=processed_data,
                    processing_status=processing_status,
                    cover_letter=cover_letter,
                    outreach_message=outreach_message,
                    interview_prep=interview_prep,
                    original_markdown=original_markdown,
                    title=title,
                    template_settings=template_settings,
                )

            doc: dict[str, Any] = {
                "resume_id": resume_id,
                "content": content,
                "content_type": content_type,
                "filename": filename,
                "is_master": is_master,
                "parent_id": None,
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
        """Atomic optimistic-concurrency update (version CAS - P4 R3.1/3.4).

        Applies ``updates`` only when the stored ``version`` still equals
        ``base_version``; the read-check-write happens in a single transaction so
        two concurrent writers with the same base cannot both succeed (exactly
        one wins; Property 1). ``version`` is bumped by one on success and is
        never settable through ``updates``.

        Returns a ``(status, resume_dict)`` tuple:

        - ``("updated", <dict>)`` - CAS matched and the write was applied.
        - ``("conflict", <current_dict>)`` - the base version was stale; the
          returned dict is the *current* server state so the caller can build the
          409 ``{your_base_version, current_version, current_data}`` payload.
        - ``("not_found", None)`` - no such resume for this user.

        The guard is a single-row **conditional UPDATE**
        (``... SET ..., version = version + 1 WHERE resume_id = ? AND user_id = ?
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
        """List full resume records owned by ``user_id``."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Resume), Resume, user_id).order_by(Resume.created_at)
            )
            return [self._resume_to_dict(row) for row in result.scalars().all()]

    async def list_resume_summaries(
        self, user_id: str, *, include_master: bool = False
    ) -> list[dict[str, Any]]:
        """List only fields required by the resume picker/list response."""
        stmt = Repo.scoped(
            select(
                Resume.resume_id,
                Resume.filename,
                Resume.is_master,
                Resume.parent_id,
                Resume.processing_status,
                Resume.created_at,
                Resume.updated_at,
                Resume.title,
            ),
            Resume,
            user_id,
        )
        if not include_master:
            stmt = stmt.where(Resume.is_master.is_(False))
        async with self._session() as session:
            result = await session.execute(stmt.order_by(Resume.updated_at.desc()))
            return [dict(row) for row in result.mappings().all()]

    async def list_resume_ids(self, user_id: str) -> list[str]:
        """Return owned resume IDs without hydrating document/blob columns."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(select(Resume.resume_id), Resume, user_id).order_by(
                    Resume.resume_id
                )
            )
            return list(result.scalars().all())

    async def set_master_resume(self, user_id: str, resume_id: str) -> bool:
        """Set one owned resume as master under a cross-worker owner lock."""
        lock = await self._master_lock(user_id)
        async with lock:
            async with self._master_transaction(user_id) as session:
                target = await self._get_owned_resume(session, user_id, resume_id)
                if target is None:
                    logger.warning("Cannot set master: resume %s not found", resume_id)
                    return False

                current = await session.execute(
                    Repo.scoped(
                        select(Resume).where(Resume.is_master.is_(True)),
                        Resume,
                        user_id,
                    )
                )
                for row in current.scalars().all():
                    if row.resume_id != resume_id:
                        row.is_master = False
                # Flush demotions before promotion for the partial unique index.
                await session.flush()
                target.is_master = True
                return True

    # -- Resume version history (P3 §A, R1-R3) ------------------------------

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
        is unique, so this is at most one row - an exact content+algorithm hit.
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
                    # Lost an insert race on the unique reuse key - reload and
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
        ``resource_id`` - so editing a resume invalidates both the artifacts
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
            # exceeds ``cap`` (rows is newest->oldest).
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
            # rows are newest->oldest; find the first ``ai`` then the next row.
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
            # captured no template -> leave the current one untouched.
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

    # ===================================================================== #
    # Application field registry (the learning loop)
    # ===================================================================== #

    async def upsert_application_field(
        self,
        user_id: str,
        *,
        label: str,
        label_normalized: str,
        field_type: str = "text",
        options: list[str] | None = None,
        status: str = "needs_answer",
        source: str = "learned",
        company: str | None = None,
        last_seen_url: str | None = None,
        last_seen_ats: str | None = None,
        last_seen_at: str | None = None,
    ) -> bool:
        """Record that a form asked this question. Returns True if newly created.

        Matching is on the normalized label within the same scope, so the same
        question seen fifty times is one row with ``times_seen`` at fifty rather
        than fifty rows - which is the difference between a usable Settings page
        and an unusable one.

        An existing answer is never overwritten here. A later form offering
        different options or a different input type must not silently discard what
        the user already told us; only the sighting metadata is refreshed.
        """
        from sqlalchemy import select

        from app.models import ApplicationField, _utcnow_iso

        # A reported field is global unless it was seen with a company attached.
        scope = "company" if company else "global"
        async with self._session() as session:
            async with session.begin():
                existing = (
                    await session.execute(
                        select(ApplicationField).where(
                            (ApplicationField.user_id == user_id)
                            & (ApplicationField.label_normalized == label_normalized)
                            & (ApplicationField.scope == scope)
                            & (ApplicationField.company == company)
                        )
                    )
                ).scalar_one_or_none()

                if existing is not None:
                    existing.times_seen = (existing.times_seen or 0) + 1
                    existing.last_seen_at = last_seen_at or _utcnow_iso()
                    if last_seen_url:
                        existing.last_seen_url = last_seen_url
                    if last_seen_ats:
                        existing.last_seen_ats = last_seen_ats
                    # Options can legitimately grow between postings; take the
                    # richer set so Settings can render every choice offered.
                    if options and len(options) > len(existing.options or []):
                        existing.options = options
                    # A field we once could not answer but just filled is answered
                    # now; the reverse must NOT reopen a question already settled.
                    if status == "answered" and existing.status == "needs_answer":
                        existing.status = "answered"
                    existing.updated_at = _utcnow_iso()
                    return False

                session.add(
                    ApplicationField(
                        user_id=user_id,
                        label=label,
                        label_normalized=label_normalized,
                        field_type=field_type,
                        options=options or None,
                        status=status,
                        source=source,
                        scope=scope,
                        company=company,
                        times_seen=1,
                        last_seen_at=last_seen_at or _utcnow_iso(),
                        last_seen_url=last_seen_url,
                        last_seen_ats=last_seen_ats,
                    )
                )
                return True

    async def list_application_fields(
        self, user_id: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Known fields. Unanswered first, then by how often they come up."""
        from sqlalchemy import case, select

        from app.models import ApplicationField, _utcnow_iso

        stmt = select(ApplicationField).where(ApplicationField.user_id == user_id)
        if status:
            stmt = stmt.where(ApplicationField.status == status)
        stmt = stmt.order_by(
            # Anything awaiting an answer belongs at the top of Settings.
            case((ApplicationField.status == "needs_answer", 0), else_=1),
            ApplicationField.times_seen.desc(),
            ApplicationField.label,
        )
        async with self._session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._application_field_to_dict(r) for r in rows]

    async def update_application_field(
        self, user_id: str, field_id: str, changes: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply a partial update. Returns the updated row, or None if not found."""
        from sqlalchemy import select

        from app.models import ApplicationField, _utcnow_iso

        allowed = {
            "label",
            "label_normalized",
            "field_type",
            "options",
            "value",
            "profile_path",
            "scope",
            "company",
            "status",
            "synonyms",
        }
        async with self._session() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ApplicationField).where(
                            (ApplicationField.user_id == user_id)
                            & (ApplicationField.id == field_id)
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None

                # Enforce the value-or-pointer rule here, not just in the router:
                # any caller (an importer, a future endpoint) that sets one must
                # not be able to leave the other behind as a stale answer.
                if changes.get("profile_path"):
                    changes["value"] = None
                elif changes.get("value") is not None:
                    changes["profile_path"] = None

                # Giving a field an answer takes it out of the review queue, even
                # if the caller forgot to say so - otherwise Settings would keep
                # asking for something already answered.
                answered_now = changes.get("value") is not None or changes.get("profile_path")
                if answered_now and "status" not in changes:
                    changes["status"] = "answered"

                for key, value in changes.items():
                    if key in allowed:
                        setattr(row, key, value)
                # A global answer has no company; clearing scope must clear it too,
                # or the unique constraint would treat it as a company row.
                if changes.get("scope") == "global":
                    row.company = None
                row.updated_at = _utcnow_iso()
                result = self._application_field_to_dict(row)
            return result

    async def delete_application_field(self, user_id: str, field_id: str) -> bool:
        from sqlalchemy import delete

        from app.models import ApplicationField, _utcnow_iso

        async with self._session() as session:
            async with session.begin():
                result = await session.execute(
                    delete(ApplicationField).where(
                        (ApplicationField.user_id == user_id)
                        & (ApplicationField.id == field_id)
                    )
                )
                return bool(result.rowcount)

    async def merge_application_fields(
        self, user_id: str, keep_id: str, drop_id: str
    ) -> dict[str, Any] | None:
        """Fold ``drop_id``'s wording into ``keep_id``'s synonyms and delete it.

        The kept row's answer wins; the dropped row contributes only its label (and
        any synonyms it had already absorbed), plus its sighting count so the
        merged row reflects how often the question really appears.
        """
        from sqlalchemy import select

        from app.models import ApplicationField, _utcnow_iso

        async with self._session() as session:
            async with session.begin():
                rows = (
                    (
                        await session.execute(
                            select(ApplicationField).where(
                                (ApplicationField.user_id == user_id)
                                & (ApplicationField.id.in_([keep_id, drop_id]))
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_id = {r.id: r for r in rows}
                keep, drop = by_id.get(keep_id), by_id.get(drop_id)
                if keep is None or drop is None:
                    return None

                synonyms = list(keep.synonyms or [])
                for candidate in [drop.label_normalized, *(drop.synonyms or [])]:
                    if candidate and candidate != keep.label_normalized:
                        if candidate not in synonyms:
                            synonyms.append(candidate)
                keep.synonyms = synonyms
                keep.times_seen = (keep.times_seen or 0) + (drop.times_seen or 0)
                keep.updated_at = _utcnow_iso()
                await session.delete(drop)
                result = self._application_field_to_dict(keep)
            return result

    async def set_application_field_value(
        self,
        user_id: str,
        *,
        label_normalized: str,
        company: str | None,
        value: Any,
    ) -> bool:
        """Set the answer for a field addressed by its label, not its id.

        The extension knows the label a form used, never our row id, so this is
        how "save what I just typed" lands. Unlike ``upsert_application_field``
        this DOES overwrite an existing answer: the user has explicitly said what
        it should be, and their latest word wins.

        A field pointing at the Profile is left alone. Overwriting it would
        reintroduce the stale-copy problem the pointer exists to prevent - the
        answer belongs to the Profile, and Settings is where that link is changed.
        """
        from sqlalchemy import select

        from app.models import ApplicationField, _utcnow_iso

        scope = "company" if company else "global"
        async with self._session() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(ApplicationField).where(
                            (ApplicationField.user_id == user_id)
                            & (ApplicationField.label_normalized == label_normalized)
                            & (ApplicationField.scope == scope)
                            & (ApplicationField.company == company)
                        )
                    )
                ).scalar_one_or_none()
                if row is None or row.profile_path:
                    return False
                row.value = value
                row.status = "answered"
                row.updated_at = _utcnow_iso()
                return True

    def _application_field_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": row.id,
            "label": row.label,
            "label_normalized": row.label_normalized,
            "synonyms": row.synonyms or [],
            "field_type": row.field_type,
            "options": row.options or [],
            "value": row.value,
            "profile_path": row.profile_path,
            "scope": row.scope,
            "company": row.company,
            "status": row.status,
            "source": row.source,
            "times_seen": row.times_seen,
            "last_seen_at": row.last_seen_at,
            "last_seen_url": row.last_seen_url,
            "last_seen_ats": row.last_seen_ats,
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
        """Anonymous lookup by public slug (no user scoping - public surface).

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
        ``metadata_json`` so dynamic analysis fields (``job_keywords``,
        ``company``/``role``, ...) round-trip through ``get_job`` as top-level
        keys. Tailoring confirmation state lives in ``tailor_previews``.
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

    # -- Durable tailoring preview/confirmation -----------------------------

    async def create_tailor_preview(
        self,
        user_id: str,
        *,
        resume_id: str,
        job_id: str,
        prompt_id: str,
        payload_hash: str,
        ttl_seconds: int = 30 * 60,
        request_id: str | None = None,
        preview_id: str | None = None,
        result_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist an unconsumed preview after scoped ownership validation.

        Every preview gets independent UUID capability and request identifiers,
        so concurrent previews for the same resume/job/prompt coexist instead of
        overwriting shared job metadata. ``None`` means the source resume or job
        disappeared (or belongs to another user).
        """
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        preview_id = preview_id or str(uuid4())
        request_id = request_id or str(uuid4())
        expires_at = (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        async with self._session() as session:
            async with session.begin():
                source = await self._get_owned_resume(session, user_id, resume_id)
                job = await self._get_owned_job(session, user_id, job_id)
                if source is None or job is None:
                    return None
                session.add(
                    TailorPreview(
                        preview_id=preview_id,
                        request_id=request_id,
                        user_id=user_id,
                        resume_id=resume_id,
                        job_id=job_id,
                        prompt_id=prompt_id,
                        payload_hash=payload_hash,
                        result_payload=result_payload,
                        created_at=now,
                        expires_at=expires_at,
                    )
                )
        return {
            "preview_id": preview_id,
            "request_id": request_id,
            "user_id": user_id,
            "resume_id": resume_id,
            "job_id": job_id,
            "prompt_id": prompt_id,
            "payload_hash": payload_hash,
            "result_payload": result_payload,
            "created_at": now,
            "expires_at": expires_at,
            "consumed_at": None,
        }

    async def get_tailor_preview(
        self, user_id: str, preview_id: str
    ) -> dict[str, Any] | None:
        """Return one owned preview (test/diagnostic helper; never consumes it)."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(TailorPreview).where(TailorPreview.preview_id == preview_id),
                    TailorPreview,
                    user_id,
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            return {
                "preview_id": row.preview_id,
                "request_id": row.request_id,
                "user_id": row.user_id,
                "resume_id": row.resume_id,
                "job_id": row.job_id,
                "prompt_id": row.prompt_id,
                "payload_hash": row.payload_hash,
                "result_payload": row.result_payload,
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "consumed_at": row.consumed_at,
            }

    async def get_tailor_preview_result(
        self, user_id: str, request_id: str
    ) -> dict[str, Any] | None:
        """Recover one completed, live, unconsumed preview by client request ID."""
        async with self._session() as session:
            result = await session.execute(
                Repo.scoped(
                    select(TailorPreview).where(
                        TailorPreview.request_id == request_id,
                        TailorPreview.expires_at > _now(),
                        TailorPreview.consumed_at.is_(None),
                        TailorPreview.result_payload.is_not(None),
                    ),
                    TailorPreview,
                    user_id,
                )
            )
            row = result.scalars().first()
            if row is None or not isinstance(row.result_payload, dict):
                return None
            return row.result_payload

    async def prune_expired_tailor_previews(
        self, *, now_iso: str | None = None, batch_size: int = 1000
    ) -> int:
        """Delete at most ``batch_size`` expired preview capabilities.

        The ordered ID subquery is portable across SQLite/PostgreSQL and avoids
        an unbounded DELETE transaction after scheduler downtime. Repeated runs
        are idempotent and eventually drain any backlog.
        """
        cutoff = now_iso or _now()
        bounded_batch = max(1, min(int(batch_size), 10_000))
        async with self._session() as session:
            async with session.begin():
                expired_ids = (
                    select(TailorPreview.preview_id)
                    .where(TailorPreview.expires_at <= cutoff)
                    .order_by(TailorPreview.expires_at, TailorPreview.preview_id)
                    .limit(bounded_batch)
                )
                result = await session.execute(
                    delete(TailorPreview).where(
                        TailorPreview.preview_id.in_(expired_ids)
                    )
                )
                return int(result.rowcount or 0)

    async def confirm_tailor_preview(
        self,
        user_id: str,
        *,
        preview_id: str,
        resume_id: str,
        job_id: str,
        payload_hash: str,
        improved_data: dict[str, Any],
        improved_text: str,
        improvements: list[dict[str, Any]],
        cover_letter: str | None,
        outreach_message: str | None,
        interview_prep: str | None,
        title: str | None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Consume one matching preview and persist all confirmation rows atomically.

        The conditional UPDATE is the concurrency gate. It matches owner, source,
        job, payload hash, unconsumed state, and expiry in the database; only its
        single winner may insert the tailored resume and dependent rows. Any
        exception rolls back the consume and every insert together.
        """
        now = _now()
        result_payload: dict[str, Any] | None = None
        async with self._session() as session:
            async with session.begin():
                source = await self._get_owned_resume(session, user_id, resume_id)
                job = await self._get_owned_job(session, user_id, job_id)
                if source is None or job is None:
                    return "not_found", None

                consumed = await session.execute(
                    sa_update(TailorPreview)
                    .where(
                        TailorPreview.preview_id == preview_id,
                        TailorPreview.user_id == user_id,
                        TailorPreview.resume_id == resume_id,
                        TailorPreview.job_id == job_id,
                        TailorPreview.payload_hash == payload_hash,
                        TailorPreview.consumed_at.is_(None),
                        TailorPreview.expires_at > now,
                    )
                    .values(consumed_at=now)
                )
                if consumed.rowcount != 1:
                    return "invalid_preview", None

                tailored_resume_id = str(uuid4())
                confirmation_id = str(uuid4())
                application_id = str(uuid4())
                filename = f"tailored_{source.filename or 'resume'}"
                session.add(
                    Resume(
                        resume_id=tailored_resume_id,
                        user_id=user_id,
                        content=improved_text,
                        content_type="json",
                        filename=filename,
                        is_master=False,
                        parent_id=resume_id,
                        processed_data=improved_data,
                        processing_status="ready",
                        cover_letter=cover_letter,
                        outreach_message=outreach_message,
                        interview_prep=interview_prep,
                        title=title,
                        template_settings=source.template_settings,
                        created_at=now,
                        updated_at=now,
                    )
                )

                from app.versions.service import compress_processed_data

                blob, size_bytes, content_hash = compress_processed_data(improved_data)
                session.add(
                    ResumeVersion(
                        user_id=user_id,
                        resume_id=tailored_resume_id,
                        source="ai",
                        label=None,
                        content_hash=content_hash,
                        data_gz=blob,
                        size_bytes=size_bytes,
                        template_settings=source.template_settings,
                        created_at=now,
                    )
                )
                session.add(
                    Improvement(
                        request_id=confirmation_id,
                        user_id=user_id,
                        original_resume_id=resume_id,
                        tailored_resume_id=tailored_resume_id,
                        job_id=job_id,
                        improvements=improvements,
                        created_at=now,
                    )
                )

                position = await self._next_position(session, user_id, "applied")
                metadata = job.metadata_json or {}
                company = metadata.get("company") if isinstance(metadata, dict) else None
                role = title or (metadata.get("role") if isinstance(metadata, dict) else None)
                session.add(
                    Application(
                        application_id=application_id,
                        user_id=user_id,
                        job_id=job_id,
                        resume_id=tailored_resume_id,
                        master_resume_id=resume_id,
                        status="applied",
                        company=company,
                        role=role,
                        applied_at=now,
                        position=position,
                        created_at=now,
                        updated_at=now,
                    )
                )

                await self._adjust_user_counter(session, user_id, "resume_count", +1)
                await self._adjust_user_counter(session, user_id, "application_count", +1)
                self._emit_search_event(
                    session, "resume.upserted", user_id, tailored_resume_id
                )
                self._emit_search_event(
                    session, "application.upserted", user_id, application_id
                )
                session.add(
                    Outbox(
                        user_id=user_id,
                        event_type="ai.generation_done",
                        payload={"resume_id": tailored_resume_id},
                        created_at=now,
                    )
                )
                await session.flush()
                result_payload = {
                    "request_id": confirmation_id,
                    "preview_id": preview_id,
                    "resume_id": tailored_resume_id,
                    "application_id": application_id,
                }
        return "created", result_payload

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
                # constraint - return the existing card instead of duplicating.
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

    async def get_application_detail(
        self, user_id: str, application_id: str
    ) -> dict[str, Any] | None:
        """Load a card, JD text, and resume deliverables in one narrow query."""
        stmt = (
            Repo.scoped(
                select(
                    Application,
                    Job.content.label("job_content"),
                    Resume.resume_id.label("detail_resume_id"),
                    Resume.cover_letter,
                    Resume.outreach_message,
                    Resume.interview_prep,
                ),
                Application,
                user_id,
            )
            .outerjoin(
                Job,
                and_(
                    Job.job_id == Application.job_id,
                    Job.user_id == user_id,
                ),
            )
            .outerjoin(
                Resume,
                and_(
                    Resume.resume_id == Application.resume_id,
                    Resume.user_id == user_id,
                ),
            )
            .where(Application.application_id == application_id)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            row = result.first()
            if row is None:
                return None
            detail = self._application_to_dict(row[0])
            detail["job_content"] = row.job_content
            detail["resume"] = (
                {
                    "resume_id": row.detail_resume_id,
                    "cover_letter": row.cover_letter,
                    "outreach_message": row.outreach_message,
                    "interview_prep": row.interview_prep,
                }
                if row.detail_resume_id is not None
                else None
            )
            return detail

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

    # -- Durable per-user LLM configuration -------------------------------

    def get_user_llm_config(self, user_id: str) -> dict[str, Any] | None:
        """Return one user's non-secret provider/model selection."""
        with self._sync() as session:
            row = session.execute(
                Repo.scoped(select(UserLlmConfig), UserLlmConfig, user_id)
            ).scalars().first()
            if row is None:
                return None
            return {
                "provider": row.provider,
                "model": row.model,
                "api_base": row.api_base,
                "reasoning_effort": row.reasoning_effort,
            }

    def set_user_llm_config(
        self,
        user_id: str,
        *,
        provider: str,
        model: str,
        api_base: str | None,
        reasoning_effort: str,
    ) -> None:
        """Atomically upsert one user's non-secret LLM selection."""
        value = {
            "user_id": user_id,
            "provider": provider,
            "model": model,
            "api_base": api_base,
            "reasoning_effort": reasoning_effort,
            "updated_at": _now(),
        }
        with self._sync() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            elif dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:  # pragma: no cover - supported deployments are SQLite/Postgres
                row = session.execute(
                    Repo.scoped(select(UserLlmConfig), UserLlmConfig, user_id)
                ).scalars().first()
                if row is None:
                    session.add(UserLlmConfig(**value))
                else:
                    for field, field_value in value.items():
                        if field != "user_id":
                            setattr(row, field, field_value)
                session.commit()
                return

            stmt = dialect_insert(UserLlmConfig).values(**value)
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[UserLlmConfig.user_id],
                    set_={
                        "provider": stmt.excluded.provider,
                        "model": stmt.excluded.model,
                        "api_base": stmt.excluded.api_base,
                        "reasoning_effort": stmt.excluded.reasoning_effort,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
            )
            session.commit()

    # -- Encrypted per-user API keys ---------------------------------------

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
        _invalidate_api_key_cache(user_id)

    def delete_api_key(self, user_id: str, provider: str) -> None:
        """Delete one provider's key for ``user_id`` (sync)."""
        with self._sync() as session:
            row = self._owned_api_key(session, user_id, provider)
            if row is not None:
                session.delete(row)
                session.commit()
        _invalidate_api_key_cache(user_id)

    def clear_api_keys(self, user_id: str) -> None:
        """Delete all of ``user_id``'s stored keys (sync)."""
        with self._sync() as session:
            session.execute(Repo.scoped(delete(ApiKey), ApiKey, user_id))
            session.commit()
        _invalidate_api_key_cache(user_id)

    def patch_api_key_ciphertexts(
        self,
        user_id: str,
        updates: dict[str, str | None],
    ) -> None:
        """Atomically patch only the specified provider keys for ``user_id``.

        Unlike replace-all semantics, disjoint concurrent requests cannot erase
        each other's providers. Each non-empty ciphertext is upserted on the
        composite ``(provider, user_id)`` primary key and ``None`` deletes only
        that provider, all within one transaction.
        """
        if not updates:
            return
        with self._sync() as session:
            now = _now()
            deletes = [provider for provider, value in updates.items() if not value]
            if deletes:
                session.execute(
                    delete(ApiKey).where(
                        ApiKey.user_id == user_id,
                        ApiKey.provider.in_(deletes),
                    )
                )

            values = [
                {
                    "provider": provider,
                    "user_id": user_id,
                    "ciphertext": ciphertext,
                    "updated_at": now,
                }
                for provider, ciphertext in updates.items()
                if ciphertext
            ]
            dialect = session.get_bind().dialect.name
            for value in values:
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert as dialect_insert
                elif dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as dialect_insert
                else:  # pragma: no cover - supported deployments are SQLite/Postgres
                    row = self._owned_api_key(session, user_id, value["provider"])
                    if row is None:
                        session.add(ApiKey(**value))
                    else:
                        row.ciphertext = value["ciphertext"]
                        row.updated_at = now
                    continue
                stmt = dialect_insert(ApiKey).values(**value)
                session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=[ApiKey.provider, ApiKey.user_id],
                        set_={
                            "ciphertext": stmt.excluded.ciphertext,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                )
            session.commit()
        _invalidate_api_key_cache(user_id)

    def replace_api_keys(self, user_id: str, ciphertexts: dict[str, str]) -> None:
        """Atomically replace ``user_id``'s key store (clear + insert in one txn).

        A single transaction means a failure mid-write can't leave the store
        half-cleared and wipe a user's previously saved keys. Only this user's
        keys are cleared/replaced - other users' keys are untouched (R10.6).
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
        _invalidate_api_key_cache(user_id)

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

    async def create_user_error_report(
        self, user_id: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Create or return an owner-scoped report by client idempotency key."""
        lookup = Repo.scoped(
            select(UserErrorReport).where(
                UserErrorReport.client_report_id == report["client_report_id"]
            ),
            UserErrorReport,
            user_id,
        )
        async with self._session() as session:
            existing = (await session.execute(lookup)).scalars().first()
            if existing is not None:
                return self._user_error_report_to_dict(existing)

            row = UserErrorReport(
                id=str(uuid4()),
                user_id=user_id,
                created_at=_now(),
                **report,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                # A concurrent retry may win the per-user client-report key.
                await session.rollback()
                existing = (await session.execute(lookup)).scalars().first()
                if existing is None:
                    raise
                row = existing
            return self._user_error_report_to_dict(row)

    async def reset_database(self, user_id: str) -> None:
        """Reset ``user_id``'s document data and clear uploads.

        Clears the user's resumes/jobs/improvements **and** tracker applications
        (leaving orphaned cards after a full data reset would be a bug). Scoped
        to ``user_id`` so a reset never touches another user's data. Encrypted
        ``api_keys`` are preserved - matching the pre-existing behavior where a
        reset never wiped the user's stored credentials.
        """
        async with self._session() as session:
            await session.execute(Repo.scoped(delete(Application), Application, user_id))
            await session.execute(Repo.scoped(delete(Improvement), Improvement, user_id))
            await session.execute(Repo.scoped(delete(TailorPreview), TailorPreview, user_id))
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
        exactly when the change is durable - a consumer failure never fails the
        user's write. Payload is a lightweight ``{node_id}``; the indexer
        re-reads current, content-safe fields from the source at index time.
        """
        session.add(
            Outbox(user_id=user_id, event_type=event_type, payload={"node_id": node_id}, created_at=_now())
        )

    async def purge_user_owned_data(self, user_id: str) -> dict[str, int]:
        """Irreversibly delete every owned row for ``user_id`` (admin purge, R8.3).

        Deletes the user's owned rows in **FK-safe order** (improvements ->
        applications -> jobs -> resumes -> api_keys) inside a single transaction, so
        the purge is atomic per user. Set-based deletes (no per-row N+1) scoped to
        ``user_id`` via ``Repo.scoped`` - the same tenant-isolation boundary as
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
                ("user_error_reports", UserErrorReport),
                ("improvements", Improvement),
                ("applications", Application),
                ("tailor_previews", TailorPreview),
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


    # ------------------------------------------------------------------ #
    # Job Discovery accessors (discovery_cache + site_recipes)
    # ------------------------------------------------------------------ #

    async def get_discovery_cache(self, cache_key: str) -> Any | None:
        """Return cached payload if present and unexpired, else None."""
        from datetime import datetime, timezone
        async with self._session() as session:
            row = await session.get(DiscoveryCache, cache_key)
        if row is None:
            return None
        # Expiry check: stored as ISO string
        now_iso = datetime.now(timezone.utc).isoformat()
        if row.expires_at <= now_iso:
            return None
        return row.payload

    async def put_discovery_cache(
        self, cache_key: str, payload: Any, ttl_seconds: int
    ) -> None:
        """Insert or replace a cache row with a fresh TTL."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=max(0, int(ttl_seconds)))).isoformat()
        async with self._session() as session:
            async with session.begin():
                existing = await session.get(DiscoveryCache, cache_key)
                if existing:
                    existing.payload = payload
                    existing.created_at = now.isoformat()
                    existing.expires_at = expires_at
                else:
                    session.add(DiscoveryCache(
                        cache_key=cache_key,
                        payload=payload,
                        created_at=now.isoformat(),
                        expires_at=expires_at,
                    ))

    async def list_site_recipe(self, user_id: str) -> list:
        """Return all recipes owned by user_id as SiteRecipe dataclasses, ordered by slug."""
        from sqlalchemy import select
        from app.job_discovery.models import SiteRecipe
        async with self._session() as session:
            stmt = (
                select(SiteRecipeModel)
                .where(SiteRecipeModel.user_id == user_id)
                .order_by(SiteRecipeModel.slug)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [
            SiteRecipe(
                id=r.id,
                user_id=r.user_id,
                name=r.name,
                slug=r.slug,
                base_url=r.base_url,
                search_url_template=r.search_url_template,
                schema=r.schema_json or {},
                fetch_mode=r.fetch_mode,
                enabled=r.enabled,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]

    async def upsert_site_recipe(self, recipe_data) -> Any:
        """Insert or update a recipe by (user_id, slug). Accepts SiteRecipe dataclass."""
        from datetime import datetime, timezone
        from sqlalchemy import select
        from app.job_discovery.models import SiteRecipe
        now_iso = datetime.now(timezone.utc).isoformat()
        user_id = recipe_data.user_id
        slug = recipe_data.slug
        async with self._session() as session:
            async with session.begin():
                stmt = select(SiteRecipeModel).where(
                    (SiteRecipeModel.user_id == user_id)
                    & (SiteRecipeModel.slug == slug)
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing:
                    existing.name = recipe_data.name
                    existing.base_url = recipe_data.base_url
                    existing.search_url_template = recipe_data.search_url_template
                    existing.schema_json = recipe_data.schema or {}
                    existing.fetch_mode = recipe_data.fetch_mode
                    existing.enabled = recipe_data.enabled
                    existing.updated_at = now_iso
                    await session.flush()
                    row = existing
                else:
                    row = SiteRecipeModel(
                        user_id=user_id,
                        name=recipe_data.name,
                        slug=slug,
                        base_url=recipe_data.base_url,
                        search_url_template=recipe_data.search_url_template,
                        schema_json=recipe_data.schema or {},
                        fetch_mode=recipe_data.fetch_mode,
                        enabled=recipe_data.enabled,
                        created_at=now_iso,
                        updated_at=now_iso,
                    )
                    session.add(row)
                    await session.flush()
        return SiteRecipe(
            id=row.id,
            user_id=row.user_id,
            name=row.name,
            slug=row.slug,
            base_url=row.base_url,
            search_url_template=row.search_url_template,
            schema=row.schema_json or {},
            fetch_mode=row.fetch_mode,
            enabled=row.enabled,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def delete_site_recipe(self, user_id: str, slug: str) -> bool:
        """Delete a recipe by (user_id, slug). Returns True if a row was deleted."""
        from sqlalchemy import delete as sa_delete
        async with self._session() as session:
            async with session.begin():
                stmt = sa_delete(SiteRecipeModel).where(
                    (SiteRecipeModel.user_id == user_id)
                    & (SiteRecipeModel.slug == slug)
                )
                result = await session.execute(stmt)
        return (result.rowcount or 0) > 0


    # ------------------------------------------------------------------ #
    # Discovery Feed accessors (runs + results)
    # ------------------------------------------------------------------ #

    async def get_or_create_discovery_run(
        self, user_id: str, resume_id: str, interval_hours: int = 24
    ) -> dict[str, Any]:
        """Get or create a discovery run schedule for a user+resume pair."""
        from datetime import datetime, timezone
        from sqlalchemy import select
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._session() as session:
            async with session.begin():
                stmt = select(DiscoveryRun).where(
                    (DiscoveryRun.user_id == user_id)
                    & (DiscoveryRun.resume_id == resume_id)
                )
                result = await session.execute(stmt)
                row = result.scalar_one_or_none()
                if row:
                    return self._run_to_dict(row)
                run = DiscoveryRun(
                    user_id=user_id,
                    resume_id=resume_id,
                    enabled=True,
                    interval_hours=interval_hours,
                    next_run_at=now_iso,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
                session.add(run)
                await session.flush()
                return self._run_to_dict(run)

    async def get_discovery_run(self, user_id: str, resume_id: str) -> dict[str, Any] | None:
        """Get a discovery run by user+resume."""
        from sqlalchemy import select
        async with self._session() as session:
            stmt = select(DiscoveryRun).where(
                (DiscoveryRun.user_id == user_id)
                & (DiscoveryRun.resume_id == resume_id)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return self._run_to_dict(row) if row else None

    async def list_due_discovery_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """Find enabled runs whose next_run_at <= now (due for execution)."""
        from datetime import datetime, timezone
        from sqlalchemy import select
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._session() as session:
            stmt = (
                select(DiscoveryRun)
                .where(
                    (DiscoveryRun.enabled == True)  # noqa: E712
                    & (DiscoveryRun.next_run_at <= now_iso)
                )
                .order_by(DiscoveryRun.next_run_at)
                .limit(limit)
            )
            result = await session.execute(stmt)
            return [self._run_to_dict(r) for r in result.scalars().all()]

    async def update_discovery_run_status(
        self, run_id: str, *, status: str, error: str | None = None,
        results_count: int = 0, next_run_at: str | None = None,
    ) -> None:
        """Update a run after execution."""
        from datetime import datetime, timezone
        from sqlalchemy import update
        now_iso = datetime.now(timezone.utc).isoformat()
        values: dict[str, Any] = {
            "last_status": status,
            "last_run_at": now_iso,
            "updated_at": now_iso,
            "results_count": results_count,
        }
        if error is not None:
            values["last_error"] = error[:500]
        if next_run_at:
            values["next_run_at"] = next_run_at
        async with self._session() as session:
            async with session.begin():
                await session.execute(
                    update(DiscoveryRun).where(DiscoveryRun.id == run_id).values(**values)
                )

    async def toggle_discovery_run(self, user_id: str, resume_id: str, enabled: bool) -> bool:
        """Enable/disable a discovery run. Returns True if found."""
        from datetime import datetime, timezone
        from sqlalchemy import update
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._session() as session:
            async with session.begin():
                result = await session.execute(
                    update(DiscoveryRun)
                    .where(
                        (DiscoveryRun.user_id == user_id)
                        & (DiscoveryRun.resume_id == resume_id)
                    )
                    .values(enabled=enabled, updated_at=now_iso)
                )
                return (result.rowcount or 0) > 0

    async def upsert_discovery_results(
        self, user_id: str, run_id: str, results: list[dict[str, Any]]
    ) -> int:
        """Insert new results, skipping duplicates by fingerprint. Returns count inserted."""
        from datetime import datetime, timezone
        from sqlalchemy import select

        from app.job_discovery.normalize import group_fingerprint as _group_fingerprint

        now_iso = datetime.now(timezone.utc).isoformat()
        inserted = 0
        async with self._session() as session:
            async with session.begin():
                for r in results:
                    # Check if fingerprint already exists for this user
                    exists = await session.execute(
                        select(DiscoveryResult.id).where(
                            (DiscoveryResult.user_id == user_id)
                            & (DiscoveryResult.fingerprint == r["fingerprint"])
                        )
                    )
                    if exists.scalar_one_or_none():
                        continue
                    session.add(DiscoveryResult(
                        user_id=user_id,
                        run_id=run_id,
                        fingerprint=r["fingerprint"],
                        # Computed here rather than trusted from the caller, so
                        # every row gets one however it arrived - server harvest,
                        # extension capture or bulk scrape.
                        group_fingerprint=(
                            _group_fingerprint(
                                r.get("title", ""), r.get("company", ""), r.get("location", "")
                            )
                            # No title means a failed extraction; a key built from
                            # nothing would merge unrelated rows.
                            if r.get("title")
                            else None
                        ),
                        source=r.get("source", ""),
                        title=r.get("title", ""),
                        company=r.get("company", ""),
                        location=r.get("location", ""),
                        url=r.get("url", ""),
                        is_remote=r.get("is_remote"),
                        description=r.get("description"),
                        salary=r.get("salary"),
                        posted_at=r.get("posted_at"),
                        match_score=r.get("match_score", 0),
                        matched_keywords=r.get("matched", []),
                        missing_keywords=r.get("missing", []),
                        partial=r.get("partial", False),
                        status="new",
                        seen=False,
                        created_at=now_iso,
                    ))
                    inserted += 1
        return inserted

    def _discovery_feed_conditions(
        self,
        user_id: str,
        *,
        status: str | None,
        sources: list[str] | None,
        query: str | None,
        location: str | None,
        is_remote: bool | None,
        min_score: float | None,
        posted_within_hours: int | None,
    ) -> list[Any]:
        """Build the WHERE terms shared by the feed list and its count.

        Shared deliberately: when the list and the count filter differently the
        UI shows "3 of 228" and pagination walks off the end of the real result
        set, which is exactly the class of bug this replaces.

        Every parameter is keyword-only with no default, so a new filter cannot
        be wired into the list and forgotten in the count - the call simply
        fails instead of quietly disagreeing.
        """
        from sqlalchemy import and_, func, or_

        conditions: list[Any] = [DiscoveryResult.user_id == user_id]
        if status:
            conditions.append(DiscoveryResult.status == status)
        if sources:
            conditions.append(DiscoveryResult.source.in_(sources))
        if is_remote:
            conditions.append(DiscoveryResult.is_remote.is_(True))
        if min_score is not None:
            # Stored on the same 0..100 scale the UI prints, so no conversion.
            conditions.append(DiscoveryResult.match_score >= min_score)
        if posted_within_hours is not None and posted_within_hours > 0:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=posted_within_hours)
            ).isoformat()
            # posted_at is written by datetime.isoformat(), so a lexicographic
            # compare is a chronological one.
            #
            # Boards that publish no date leave posted_at NULL. Dropping those
            # rows would hide a job harvested twenty minutes ago from a "last
            # 24 hours" filter, so fall back to when we discovered it - the
            # nearest honest proxy we hold.
            conditions.append(
                or_(
                    DiscoveryResult.posted_at >= cutoff,
                    and_(
                        DiscoveryResult.posted_at.is_(None),
                        DiscoveryResult.created_at >= cutoff,
                    ),
                )
            )
        if location:
            conditions.append(
                func.lower(DiscoveryResult.location).contains(location.strip().lower())
            )
        if query:
            # Match the words a person would recognise the job by. Every token
            # must appear somewhere in the title or the company, so "python dev"
            # does not match a job that merely mentions Python in one of them.
            for token in query.lower().split():
                conditions.append(
                    or_(
                        func.lower(DiscoveryResult.title).contains(token),
                        func.lower(DiscoveryResult.company).contains(token),
                    )
                )
        return conditions

    def _dedupe_representative_ids(self, conditions: list[Any]) -> Any:
        """Subquery of the one row id to keep per job.

        Duplicates have to be removed *inside* the query, not after paging. The
        same opening harvested in two runs sits far apart in creation order, so a
        page-local collapse catches almost none of them - measured on a real feed,
        zero of 33 duplicate groups fell on the same page of 100.

        The survivor is the highest-scoring row, then the newest: whichever board
        gave us the most to work with. Rows with no group key are their own group,
        so nothing is ever merged on missing data.
        """
        from sqlalchemy import func, select

        group_key = func.coalesce(DiscoveryResult.group_fingerprint, DiscoveryResult.id)
        ranked = (
            select(
                DiscoveryResult.id.label("id"),
                func.row_number()
                .over(
                    partition_by=group_key,
                    order_by=[
                        DiscoveryResult.match_score.desc(),
                        DiscoveryResult.created_at.desc(),
                    ],
                )
                .label("rank"),
            )
            .where(*conditions)
            .subquery()
        )
        return select(ranked.c.id).where(ranked.c.rank == 1)

    async def get_discovery_feed(
        self, user_id: str, *, status: str | None = None,
        sources: list[str] | None = None, query: str | None = None,
        location: str | None = None, is_remote: bool | None = None,
        min_score: float | None = None, posted_within_hours: int | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Get paginated feed results for a user, newest first.

        One row per job: duplicates of the same opening across boards or across
        harvest runs are removed in the query, so a page holds twenty distinct
        jobs rather than twenty rows that might be twelve.
        """
        from sqlalchemy import select
        async with self._session() as session:
            conditions = self._discovery_feed_conditions(
                user_id,
                status=status,
                sources=sources,
                query=query,
                location=location,
                is_remote=is_remote,
                min_score=min_score,
                posted_within_hours=posted_within_hours,
            )
            stmt = select(DiscoveryResult).where(
                DiscoveryResult.id.in_(self._dedupe_representative_ids(conditions))
            )
            stmt = stmt.order_by(DiscoveryResult.created_at.desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            return [self._result_to_dict(r) for r in result.scalars().all()]

    async def annotate_duplicate_sources(
        self, user_id: str, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Tell each row which other boards carry the same job.

        The feed query already returns one row per job, so the siblings it stood in
        for are no longer on the page - they have to be looked up. Worth the one
        extra query: naming the other boards is what makes collapsing trustworthy
        rather than the feed appearing to lose jobs.
        """
        from sqlalchemy import select

        keys = [r["group_fingerprint"] for r in rows if r.get("group_fingerprint")]
        if not keys:
            return rows

        async with self._session() as session:
            siblings = (
                await session.execute(
                    select(DiscoveryResult.group_fingerprint, DiscoveryResult.source).where(
                        (DiscoveryResult.user_id == user_id)
                        & DiscoveryResult.group_fingerprint.in_(keys)
                    )
                )
            ).all()

        by_key: dict[str, list[str]] = {}
        for key, source in siblings:
            by_key.setdefault(key, []).append(source or "")

        annotated: list[dict[str, Any]] = []
        for row in rows:
            key = row.get("group_fingerprint")
            all_sources = by_key.get(key, []) if key else []
            others = sorted({s for s in all_sources if s and s != row.get("source")})
            # Only labelled when another *board* carries it. Two copies from the
            # same board are just two harvest runs of one listing: correctly
            # collapsed, and "also on hirist" while reading a hirist row would be
            # noise pretending to be information.
            if not others:
                annotated.append(row)
                continue
            annotated.append(
                {**row, "also_on": others, "duplicate_count": len(all_sources)}
            )
        return annotated

    async def count_discovery_feed(
        self, user_id: str, *, status: str | None = None,
        sources: list[str] | None = None, query: str | None = None,
        location: str | None = None, is_remote: bool | None = None,
        min_score: float | None = None, posted_within_hours: int | None = None,
    ) -> int:
        """Count feed results for a user under the same filters as the list.

        Counts distinct *jobs*, not rows, because the list now returns distinct
        jobs. Counting rows here would resurrect the exact bug the shared
        conditions were introduced to kill: "20 of 300" while paging walks off the
        end of a 207-item set.
        """
        from sqlalchemy import select, func
        async with self._session() as session:
            conditions = self._discovery_feed_conditions(
                user_id,
                status=status,
                sources=sources,
                query=query,
                location=location,
                is_remote=is_remote,
                min_score=min_score,
                posted_within_hours=posted_within_hours,
            )
            representative = self._dedupe_representative_ids(conditions).subquery()
            stmt = select(func.count()).select_from(representative)
            result = await session.execute(stmt)
            return result.scalar() or 0

    async def get_discovery_result(
        self, user_id: str, result_id: str
    ) -> dict[str, Any] | None:
        """One feed row, scoped to its owner."""
        from sqlalchemy import select

        async with self._session() as session:
            row = (
                await session.execute(
                    select(DiscoveryResult).where(
                        (DiscoveryResult.id == result_id)
                        & (DiscoveryResult.user_id == user_id)
                    )
                )
            ).scalar_one_or_none()
            return self._result_to_dict(row) if row else None

    async def set_discovery_result_job(
        self, user_id: str, result_id: str, job_id: str
    ) -> None:
        """Remember which job-description row this feed result created."""
        from sqlalchemy import update as sa_update

        async with self._session() as session:
            async with session.begin():
                await session.execute(
                    sa_update(DiscoveryResult)
                    .where(
                        (DiscoveryResult.id == result_id)
                        & (DiscoveryResult.user_id == user_id)
                    )
                    .values(job_id=job_id)
                )

    # ----------------------------------------------------------------------- #
    # Application queries for the apply queue, submissions and outcomes.
    #
    # These live here rather than in `app/applications/` because ADR-4 puts every
    # owned-table query in the repository layer: a query written in a feature
    # module is one that can forget its `user_id` filter, and the scoping guard
    # rejects them for that reason. Each method takes `user_id` first and filters
    # on it, so the scope cannot be omitted by a caller.
    #
    # They return plain dicts. Handing ORM objects out of the session would leave
    # callers holding rows that detach the moment the session closes.
    # ----------------------------------------------------------------------- #
    @staticmethod
    def _application_to_dict(row: Application) -> dict[str, Any]:
        return {
            "application_id": row.application_id,
            "job_id": row.job_id,
            "resume_id": row.resume_id,
            # The base resume a tailored one descends from. Omitting it made the
            # tracker report None for every card that had one.
            "master_resume_id": row.master_resume_id,
            "status": row.status,
            "company": row.company,
            "role": row.role,
            "applied_at": row.applied_at,
            "position": row.position,
            "notes": row.notes,
            "submitted_answers": row.submitted_answers or {},
            "submitted_resume_version_id": row.submitted_resume_version_id,
            "submitted_via": row.submitted_via,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def get_application_row(
        self, user_id: str, application_id: str
    ) -> dict[str, Any] | None:
        """One application, scoped to its owner."""
        from sqlalchemy import select

        async with self._session() as session:
            row = (
                await session.execute(
                    select(Application).where(
                        (Application.user_id == user_id)
                        & (Application.application_id == application_id)
                    )
                )
            ).scalar_one_or_none()
            return self._application_to_dict(row) if row else None

    async def list_application_rows(
        self,
        user_id: str,
        *,
        statuses: list[str] | None = None,
        order_by_position: bool = False,
    ) -> list[dict[str, Any]]:
        """Applications for a user, optionally filtered by status.

        One method rather than four near-identical ones: the queue, the duplicate
        guard, the CSV export and the outcomes view all want "this user's
        applications, maybe filtered", and duplicating that query per caller is how
        one of them ends up missing the scope.
        """
        from sqlalchemy import select

        stmt = select(Application).where(Application.user_id == user_id)
        if statuses:
            stmt = stmt.where(Application.status.in_(statuses))
        stmt = (
            stmt.order_by(Application.position, Application.created_at)
            if order_by_position
            else stmt.order_by(Application.created_at.desc())
        )

        async with self._session() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._application_to_dict(r) for r in rows]

    async def record_application_submission(
        self,
        user_id: str,
        application_id: str,
        *,
        answers: dict[str, Any] | None,
        resume_version_id: str | None,
        submitted_via: str,
        applied_at: str,
        advance_statuses: tuple[str, ...],
    ) -> dict[str, Any] | None:
        """Store what was submitted and mark the application applied.

        ``advance_statuses`` are the statuses that may move to ``applied``. Anything
        further along the pipeline keeps its own status: a late submission record
        must not drag an application back from interview to applied.
        """
        from sqlalchemy import select

        async with self._session() as session:
            async with session.begin():
                row = (
                    await session.execute(
                        select(Application).where(
                            (Application.user_id == user_id)
                            & (Application.application_id == application_id)
                        )
                    )
                ).scalar_one_or_none()
                if row is None:
                    return None

                row.submitted_answers = answers or {}
                row.submitted_resume_version_id = resume_version_id
                row.submitted_via = submitted_via
                if row.status in advance_statuses:
                    row.status = "applied"
                if not row.applied_at:
                    row.applied_at = applied_at
                row.updated_at = applied_at
                return self._application_to_dict(row)

    async def set_application_positions(self, user_id: str, ordered_ids: list[str]) -> int:
        """Set queue order from a list of ids. Returns how many moved.

        Ids the user does not own are simply absent from the scoped read, so a
        stale tab cannot reorder someone else's queue or fail the whole request.
        """
        from sqlalchemy import select

        async with self._session() as session:
            async with session.begin():
                rows = (
                    (
                        await session.execute(
                            select(Application).where(
                                (Application.user_id == user_id)
                                & (Application.application_id.in_(ordered_ids))
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                by_id = {r.application_id: r for r in rows}
                moved = 0
                for position, application_id in enumerate(ordered_ids):
                    row = by_id.get(application_id)
                    if row is None:
                        continue
                    if row.position != position:
                        row.position = position
                        moved += 1
                return moved

    async def delete_saved_application_for_job(self, user_id: str, job_id: str) -> int:
        """Remove a queued (``saved``) application for a job. Returns rows removed.

        Only ``saved`` rows: anything further along is history the user earned, and
        dismissing a listing is no reason to destroy the record that they applied.
        """
        from sqlalchemy import delete as sa_delete

        async with self._session() as session:
            async with session.begin():
                result = await session.execute(
                    sa_delete(Application).where(
                        (Application.user_id == user_id)
                        & (Application.job_id == job_id)
                        & (Application.status == "saved")
                    )
                )
                return result.rowcount or 0

    async def get_resume_names(self, user_id: str, resume_ids: list[str]) -> dict[str, str]:
        """Filenames for the given resumes, scoped to their owner."""
        from sqlalchemy import select

        if not resume_ids:
            return {}
        async with self._session() as session:
            rows = (
                await session.execute(
                    select(Resume.resume_id, Resume.filename).where(
                        (Resume.user_id == user_id) & (Resume.resume_id.in_(resume_ids))
                    )
                )
            ).all()
            return {rid: name for rid, name in rows if name}

    async def get_master_resume_ids(self, user_id: str, resume_ids: list[str]) -> set[str]:
        """Which of ``resume_ids`` are the user's master resume.

        Used to tell a tailored resume from the master when deciding what to attach
        to an application: a queued-but-untailored job points at the master, and
        announcing that as "tailored" would be a lie.
        """
        from sqlalchemy import select

        if not resume_ids:
            return set()
        async with self._session() as session:
            rows = (
                await session.execute(
                    select(Resume.resume_id).where(
                        (Resume.user_id == user_id)
                        & Resume.resume_id.in_(resume_ids)
                        & Resume.is_master.is_(True)
                    )
                )
            ).scalars().all()
            return set(rows)

    async def backfill_group_fingerprints(self, limit: int = 20000) -> int:
        """Give older feed rows the group key duplicate collapsing needs.

        Rows harvested before this column existed have none, so without a backfill
        the deduplication would only ever help future searches - while the feed the
        user already has stays 25% repeats. Idempotent and bounded: only NULL rows
        are touched, so re-running costs one indexed scan and changes nothing.
        """
        from sqlalchemy import select

        from app.job_discovery.normalize import group_fingerprint as _group_fingerprint

        updated = 0
        async with self._session() as session:
            async with session.begin():
                rows = (
                    (
                        await session.execute(
                            select(DiscoveryResult)
                            .where(DiscoveryResult.group_fingerprint.is_(None))
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
                for row in rows:
                    if not row.title:
                        # A row with no title is a failed extraction, not a job.
                        # Grouping on an empty title would merge unrelated rows.
                        continue
                    # Company is not required: some boards (We Work Remotely) put
                    # the employer inside the title and leave the field blank, and
                    # demanding one left those rows as permanent duplicates.
                    row.group_fingerprint = _group_fingerprint(
                        row.title, row.company or "", row.location or ""
                    )
                    updated += 1
        return updated

    async def count_scored_discovery_results(self, user_id: str) -> int:
        """How many feed rows carry a real match score.

        Scores exist only for jobs matched against a resume; a keyword harvest
        stores 0.0. Without this count the UI would offer a "70%+ match" filter
        that silently returns nothing on a feed where nothing has been scored -
        a control that looks broken because it is being honest.
        """
        from sqlalchemy import func, select

        async with self._session() as session:
            result = await session.execute(
                select(func.count(DiscoveryResult.id)).where(
                    (DiscoveryResult.user_id == user_id) & (DiscoveryResult.match_score > 0)
                )
            )
            return result.scalar() or 0

    async def count_unseen_discovery_results(self, user_id: str) -> int:
        """Count new unseen results since last visit."""
        from sqlalchemy import select, func
        async with self._session() as session:
            result = await session.execute(
                select(func.count(DiscoveryResult.id)).where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.seen == False)  # noqa: E712
                )
            )
            return result.scalar() or 0

    async def mark_discovery_results_seen(self, user_id: str) -> None:
        """Mark all unseen results as seen for a user."""
        from sqlalchemy import update
        async with self._session() as session:
            async with session.begin():
                await session.execute(
                    update(DiscoveryResult)
                    .where(
                        (DiscoveryResult.user_id == user_id)
                        & (DiscoveryResult.seen == False)  # noqa: E712
                    )
                    .values(seen=True)
                )

    @staticmethod
    def _run_to_dict(row: DiscoveryRun) -> dict[str, Any]:
        return {
            "id": row.id, "user_id": row.user_id, "resume_id": row.resume_id,
            "enabled": row.enabled, "interval_hours": row.interval_hours,
            "last_run_at": row.last_run_at, "next_run_at": row.next_run_at,
            "last_status": row.last_status, "last_error": row.last_error,
            "results_count": row.results_count,
            "created_at": row.created_at, "updated_at": row.updated_at,
        }

    @staticmethod
    def _result_to_dict(row: DiscoveryResult) -> dict[str, Any]:
        return {
            "id": row.id, "user_id": row.user_id, "run_id": row.run_id,
            "fingerprint": row.fingerprint, "source": row.source,
            "title": row.title, "company": row.company, "location": row.location,
            "url": row.url, "is_remote": row.is_remote,
            "description": row.description, "salary": row.salary,
            "posted_at": row.posted_at, "match_score": row.match_score,
            "matched_keywords": row.matched_keywords or [],
            "missing_keywords": row.missing_keywords or [],
            "partial": row.partial, "status": row.status, "seen": row.seen,
            "job_id": row.job_id,
            "group_fingerprint": row.group_fingerprint,
            "created_at": row.created_at,
        }


# Global database instance
db = Database()
