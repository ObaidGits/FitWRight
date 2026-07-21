"""Isolated cross-user read path for the admin surface (Task 1.2).

This is the **single** module permitted to query the database without a
``user_id`` scope - the only place cross-user reads happen in the whole product.
It is CI-allowlisted in ``app/scripts/check_scoping.py`` precisely so that every
*other* repository stays user-scoped and a reviewer knows exactly where to look
for cross-tenant access. It exposes typed, read-only aggregate + list methods and
holds **no write methods** (lifecycle writes go through their services; the purge
delete goes through the user-scoped ``Database.purge_user_owned_data`` facade).

Everything returned is an aggregate (a count/series) or allowlisted
user-management metadata - never resume/JD content, secrets, tokens, or hashes
(Property 2). API-key presence is surfaced only as a boolean.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.admin.cursor import decode_cursor, encode_cursor
from app.admin.metric_registry import (
    SEC_ADMIN_LOGIN,
    SEC_AUTHZ_DENIED,
    SEC_LOGIN_FAILED,
    SEC_RATE_LIMITED,
    SEC_SUSPICIOUS,
)
from app.auth.audit import AuditEvent
from app.models import (
    ApiKey,
    Application,
    AuditLog,
    Improvement,
    Resume,
    ResumeVersion,
    Session as SessionRow,
    User,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AdminRepo",
    "AdminUserRowData",
    "UserActivity",
    "build_user_row_data",
    "get_admin_repo",
    "reset_admin_repo",
]

# Valid list filter vocabularies (anything else is ignored -> predictable results).
_VALID_STATUS = frozenset({"active", "disabled", "pending_verification"})
_VALID_ROLE = frozenset({"user", "admin"})


@dataclass(frozen=True, slots=True)
class AdminUserRowData:
    """Raw list-row data assembled from allowlisted ``users`` columns only."""

    id: str
    name: str
    email: str
    role: str
    status: str
    email_verified: bool
    created_at: str
    deleted_at: str | None
    resume_count: int
    application_count: int
    last_active_at: str | None


@dataclass(frozen=True, slots=True)
class UserActivity:
    """A target user's activity summary (counts only, content-free)."""

    resume_count: int
    tailored_count: int
    application_count: int
    last_active_at: str | None
    ai_configured: bool
    signup_method: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_user_row_data(row: User) -> AdminUserRowData:
    """Map a ``users`` ORM row to the allowlisted admin list-row data.

    The single place a ``users`` row is projected for the admin surface, so a new
    column never rides along (only these fields are copied - Property 2).
    """
    return AdminUserRowData(
        id=row.id,
        name=row.name,
        email=row.email,
        role=row.role,
        status=row.status,
        email_verified=row.email_verified_at is not None,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
        resume_count=row.resume_count or 0,
        application_count=row.application_count or 0,
        last_active_at=row.last_active_at,
    )


class AdminRepo:
    """Cross-user, read-only aggregate + list access for admin."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # -- user list (keyset paginated, index-usable search) -------------------

    async def list_users(
        self,
        *,
        cursor: str | None = None,
        q: str | None = None,
        status: str | None = None,
        role: str | None = None,
        verified: bool | None = None,
        deleted: bool = False,
        limit: int = 50,
    ) -> tuple[list[AdminUserRowData], str | None]:
        """Return one keyset page of users + the next cursor (``None`` at end).

        Sort is ``created_at desc, id desc`` (stable tie-break by id). Search is
        index-usable: case-insensitive **prefix** match on email OR name - never
        a ``%q%`` substring scan (R4.2). ``deleted=False`` hides soft-deleted
        users; ``deleted=True`` surfaces only them (for restore, R4.4).
        """
        limit = max(1, min(100, limit))
        decoded = decode_cursor(cursor)  # raises CursorError on tamper

        stmt = select(User)
        conditions = []

        if deleted:
            conditions.append(User.deleted_at.is_not(None))
        else:
            conditions.append(User.deleted_at.is_(None))

        if status in _VALID_STATUS:
            conditions.append(User.status == status)
        if role in _VALID_ROLE:
            conditions.append(User.role == role)
        if verified is True:
            conditions.append(User.email_verified_at.is_not(None))
        elif verified is False:
            conditions.append(User.email_verified_at.is_(None))

        if q:
            # Prefix only (index-usable), never %q%. Email is stored normalized
            # lowercase, so match the **bare** column (no lower() wrapper) - that
            # is what lets the `email text_pattern_ops` btree index serve the
            # prefix on Postgres. Name is mixed-case, so match `lower(name)`,
            # served by the `lower(name) text_pattern_ops` expression index.
            like = f"{q.lower()}%"
            conditions.append(
                or_(
                    User.email.like(like),
                    func.lower(User.name).like(like),
                )
            )

        if decoded is not None:
            c_created, c_id = decoded
            # (created_at, id) strictly-less-than in the desc ordering.
            conditions.append(
                or_(
                    User.created_at < c_created,
                    and_(User.created_at == c_created, User.id < c_id),
                )
            )

        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)

        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()

        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = (
            encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
        )
        return [self._row_data(r) for r in page], next_cursor

    @staticmethod
    def _row_data(row: User) -> AdminUserRowData:
        return build_user_row_data(row)

    # -- user detail activity (content-free counts) --------------------------

    async def user_activity(self, user_id: str) -> UserActivity:
        """Compute a target user's activity summary (counts + booleans only)."""
        async with self._session_factory() as session:
            resume_count = int(
                (
                    await session.execute(
                        select(func.count()).select_from(Resume).where(Resume.user_id == user_id)
                    )
                ).scalar()
                or 0
            )
            tailored_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Improvement)
                        .where(Improvement.user_id == user_id)
                    )
                ).scalar()
                or 0
            )
            application_count = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(Application)
                        .where(Application.user_id == user_id)
                    )
                ).scalar()
                or 0
            )
            ai_configured = bool(
                (
                    await session.execute(
                        select(ApiKey.provider).where(ApiKey.user_id == user_id).limit(1)
                    )
                ).first()
            )
            last_active_at = (
                await session.execute(select(User.last_active_at).where(User.id == user_id))
            ).scalar()
            # signup method: OAuth-only accounts (no password hash) are "oauth".
            pw_hash = (
                await session.execute(select(User.password_hash).where(User.id == user_id))
            ).scalar()
        return UserActivity(
            resume_count=resume_count,
            tailored_count=tailored_count,
            application_count=application_count,
            last_active_at=last_active_at,
            ai_configured=ai_configured,
            signup_method="password" if pw_hash else "oauth",
        )

    # -- overview stats (indexed aggregates) ---------------------------------

    async def overview_stats(self, *, active_window_days: int = 30, signup_period_days: int = 30) -> dict[str, int]:
        """Compute the overview stats via indexed aggregate queries (R2.1/2.3)."""
        now = _now()
        active_cutoff = (now - timedelta(days=active_window_days)).isoformat()
        signup_cutoff = (now - timedelta(days=signup_period_days)).isoformat()
        async with self._session_factory() as session:
            total_users = await self._count(session, select(func.count()).select_from(User).where(User.deleted_at.is_(None)))
            disabled_users = await self._count(
                session,
                select(func.count()).select_from(User).where(
                    User.status == "disabled", User.deleted_at.is_(None)
                ),
            )
            active_users = await self._count(
                session,
                select(func.count(func.distinct(SessionRow.user_id))).where(
                    SessionRow.last_seen_at >= active_cutoff
                ),
            )
            total_resumes = await self._count(session, select(func.count()).select_from(Resume))
            resumes_tailored = await self._count(session, select(func.count()).select_from(Improvement))
            applications = await self._count(session, select(func.count()).select_from(Application))
            cover_letters = await self._count(
                session,
                select(func.count()).select_from(Resume).where(Resume.cover_letter.is_not(None)),
            )
            interview_preps = await self._count(
                session,
                select(func.count()).select_from(Resume).where(Resume.interview_prep.is_not(None)),
            )
            outreach = await self._count(
                session,
                select(func.count()).select_from(Resume).where(Resume.outreach_message.is_not(None)),
            )
            signups = await self._count(
                session,
                select(func.count()).select_from(User).where(User.created_at >= signup_cutoff),
            )
        return {
            "totalUsers": total_users,
            "activeUsers": active_users,
            "disabledUsers": disabled_users,
            "totalResumes": total_resumes,
            "resumesTailored": resumes_tailored,
            "applications": applications,
            "coverLettersGenerated": cover_letters,
            "interviewPrepsGenerated": interview_preps,
            "outreachGenerated": outreach,
            "signups": signups,
        }

    # -- metric registry: daily (closed-day) + live-today -------------------

    async def metric_for_day(self, metric: str, day_start: str, day_end: str) -> int:
        """Compute a registry metric's value for the UTC day ``[day_start, day_end)``.

        Definitions (authoritative, UTC days):
        - ``signups``       = users created that day
        - ``active_users``  = distinct users with session activity that day
        - ``resumes_tailored`` = improvements created that day
        """
        async with self._session_factory() as session:
            if metric == "signups":
                return await self._count(
                    session,
                    select(func.count()).select_from(User).where(
                        User.created_at >= day_start, User.created_at < day_end
                    ),
                )
            if metric == "active_users":
                return await self._count(
                    session,
                    select(func.count(func.distinct(SessionRow.user_id))).where(
                        SessionRow.last_seen_at >= day_start, SessionRow.last_seen_at < day_end
                    ),
                )
            if metric == "resumes_tailored":
                return await self._count(
                    session,
                    select(func.count()).select_from(Improvement).where(
                        Improvement.created_at >= day_start, Improvement.created_at < day_end
                    ),
                )
            raise ValueError(f"unknown_metric: {metric}")

    # -- rollup-time aggregates (called ONLY by rollup steps, never a request) --
    #
    # The methods below back the nightly/periodic Rollup_Steps (SecurityAggregateStep
    # 13.1, DbSizeSampleStep/StorageSnapshotStep 12.1, ResumeSnapshotStep 17.1).
    # They are cross-user aggregate reads that are intentionally *not* O(1) (a
    # day-bounded event scan, whole-table counts, a JSON group-by), which is why
    # they live here (the allowlisted module) and are invoked off the request path
    # only. Every return value is a count/aggregate - never resume/JD content,
    # secrets, tokens, or hashes (Property 2).

    async def security_daily(self, day_start: str, day_end: str) -> dict[str, int]:
        """Count Security_Critical_Event ``audit_log`` rows for the UTC day
        ``[day_start, day_end)``, keyed by the ``SEC_*`` Metric_Keys (Req 9.1).

        Mapping (audit event -> SEC_* key):
        - ``SEC_LOGIN_FAILED`` <- ``AuditEvent.LOGIN_FAILED`` (``auth.login_failed``).
        - ``SEC_ADMIN_LOGIN``  <- ``AuditEvent.LOGIN`` (``login``) rows whose actor is
          currently an ``admin``. There is no dedicated "admin login" event, so the
          cleanest correct signal is a ``login`` row joined to ``users`` on
          ``actor_user_id`` filtered to ``role == 'admin'``. Role is evaluated at
          rollup time (current role), which is acceptable for a daily aggregate.
        - ``SEC_AUTHZ_DENIED`` <- ``AuditEvent.AUTHZ_DENIED`` (``authz.denied``).
        - ``SEC_RATE_LIMITED`` <- **0 (documented gap).** Rate-limit denials are
          emitted only as an in-process metric (``AuthMetrics.record_rate_limited``),
          never as an ``audit_log`` row, so there is no event to count here. The key
          is still returned (as 0) so the rollup writes a stable, complete row; it
          can be sourced from the flushed auth metric in a later wave.
        - ``SEC_SUSPICIOUS``   <- **0 (documented gap).** No suspicious/blocked audit
          event exists in the ``AuditEvent`` catalog today (WAF/SSRF/bot signals live
          in productivity metrics, not the audit trail), so this is returned as 0.

        The main event counts use a single day-bounded ``GROUP BY event`` served by
        ``ix_audit_log_event_ts``; admin logins use one small indexed join. Off the
        request path (rollup-time), a day-bounded scan is acceptable (Req 9.1).
        """
        counted_events = (
            AuditEvent.LOGIN_FAILED,
            AuditEvent.AUTHZ_DENIED,
        )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog.event, func.count())
                    .where(
                        AuditLog.ts >= day_start,
                        AuditLog.ts < day_end,
                        AuditLog.event.in_(counted_events),
                    )
                    .group_by(AuditLog.event)
                )
            ).all()
            by_event = {event: int(n) for event, n in rows}

            # Admin logins: `login` audit rows whose actor is currently an admin.
            admin_login = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .join(User, User.id == AuditLog.actor_user_id)
                        .where(
                            AuditLog.ts >= day_start,
                            AuditLog.ts < day_end,
                            AuditLog.event == AuditEvent.LOGIN,
                            User.role == "admin",
                        )
                    )
                ).scalar()
                or 0
            )

        return {
            SEC_LOGIN_FAILED: by_event.get(AuditEvent.LOGIN_FAILED, 0),
            SEC_ADMIN_LOGIN: admin_login,
            SEC_AUTHZ_DENIED: by_event.get(AuditEvent.AUTHZ_DENIED, 0),
            # Documented gaps - no audit-log signal exists for these today.
            SEC_RATE_LIMITED: 0,
            SEC_SUSPICIOUS: 0,
        }

    async def resume_source_counts(self) -> dict[str, int]:
        """Point-in-time resume source split - generated / imported / tailored /
        deleted (Req 14.1/14.2).

        ``resumes`` has no explicit ``source``/``origin`` column, so the split uses
        the best available structural proxies:
        - ``tailored``  = ``COUNT(improvements)`` - a tailoring result row per
          tailored resume (matches the existing ``resumes_tailored`` overview stat).
        - ``imported``  = non-tailored resumes (``parent_id IS NULL``) that carry a
          persisted ``original_markdown`` - that column is set only on the
          upload/parse path, so its presence marks a file-imported resume.
        - ``generated`` = the remaining non-tailored resumes (``parent_id IS NULL``
          AND ``original_markdown IS NULL``) - builder/profile-generated resumes.
        - ``deleted``   = **0 (documented gap).** Resumes are hard-deleted (purge
          cascade); there is no soft-delete column on ``resumes``, so a point-in-time
          snapshot cannot recover deleted rows. ``RESUMES_DELETED`` is instead an
          event-time daily counter (product analytics) incremented at deletion.
        """
        async with self._session_factory() as session:
            tailored = await self._count(session, select(func.count()).select_from(Improvement))
            imported = await self._count(
                session,
                select(func.count()).select_from(Resume).where(
                    Resume.parent_id.is_(None), Resume.original_markdown.is_not(None)
                ),
            )
            generated = await self._count(
                session,
                select(func.count()).select_from(Resume).where(
                    Resume.parent_id.is_(None), Resume.original_markdown.is_(None)
                ),
            )
        return {
            "generated": generated,
            "imported": imported,
            "tailored": tailored,
            "deleted": 0,
        }

    async def popular_templates(self) -> list[tuple[str, int]]:
        """Raw grouped resume counts per template id (Req 14.2).

        The chosen template is not a column: it lives inside the ``resumes``
        ``template_settings`` JSON blob under the ``"template"`` key (e.g.
        ``"swiss-single"``). This returns the **raw** ``(template_id, count)``
        grouping for every resume that has a non-null template value; the
        ``ResumeMetricsService`` (Task 17.2) applies the top-10 slice and the
        ascending-name tie-break. Resumes with no persisted template (older rows)
        are excluded rather than bucketed under a synthetic default.
        """
        template_expr = Resume.template_settings["template"].as_string()
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(template_expr, func.count())
                    .where(template_expr.is_not(None))
                    .group_by(template_expr)
                )
            ).all()
        return [(str(name), int(n)) for name, n in rows if name is not None]

    async def storage_counts(self) -> dict[str, int]:
        """Counts backing the storage panel: avatars, resumes, resume versions
        (Req 7.2).

        - ``avatarCount``        = users with a non-null ``avatar_key`` (a stored
          avatar object exists - matches the orphan-GC provenance key).
        - ``resumeCount``        = all ``resumes`` rows.
        - ``resumeVersionCount`` = all ``resume_versions`` snapshots.
        """
        async with self._session_factory() as session:
            avatar_count = await self._count(
                session,
                select(func.count()).select_from(User).where(User.avatar_key.is_not(None)),
            )
            resume_count = await self._count(session, select(func.count()).select_from(Resume))
            resume_version_count = await self._count(
                session, select(func.count()).select_from(ResumeVersion)
            )
        return {
            "avatarCount": avatar_count,
            "resumeCount": resume_count,
            "resumeVersionCount": resume_version_count,
        }

    async def db_size_bytes(self) -> int | None:
        """Return the database size in bytes via a single dialect-aware query
        (Req 7.1), or ``None`` when it cannot be determined.

        - **Postgres:** ``SELECT pg_database_size(current_database())``.
        - **SQLite** (local/tests): ``PRAGMA page_count * PRAGMA page_size``.
        - Any other dialect, or a query failure, returns ``None`` so the caller
          (DbSizeSampleStep) can retain the last sample and mark it stale (Req 7.6).
        """
        async with self._session_factory() as session:
            dialect = session.get_bind().dialect.name
            try:
                if dialect == "postgresql":
                    value = (
                        await session.execute(
                            select(func.pg_database_size(func.current_database()))
                        )
                    ).scalar()
                    return int(value) if value is not None else None
                if dialect == "sqlite":
                    page_count = (await session.execute(text("PRAGMA page_count"))).scalar()
                    page_size = (await session.execute(text("PRAGMA page_size"))).scalar()
                    if page_count is None or page_size is None:
                        return None
                    return int(page_count) * int(page_size)
            except Exception:  # pragma: no cover - defensive; caller marks stale
                logger.warning("db_size_bytes query failed for dialect=%s", dialect, exc_info=True)
                return None
        return None

    # -- purge backlog gauge -------------------------------------------------

    async def purge_backlog(self, grace_cutoff_iso: str) -> int:
        """Count soft-deleted users already past the grace cutoff (purge-due)."""
        async with self._session_factory() as session:
            return await self._count(
                session,
                select(func.count()).select_from(User).where(
                    User.deleted_at.is_not(None), User.deleted_at < grace_cutoff_iso
                ),
            )

    async def soft_deleted_count(self) -> int:
        """Count all soft-deleted (awaiting-purge or within-grace) users."""
        async with self._session_factory() as session:
            return await self._count(
                session,
                select(func.count()).select_from(User).where(User.deleted_at.is_not(None)),
            )

    # -- counter reconciliation (drift correction for the rollup) -----------

    async def user_ids_after(self, after_id: str | None, limit: int) -> list[str]:
        """One id-ordered batch of user ids > ``after_id`` (keyset, for chunking)."""
        stmt = select(User.id).order_by(User.id).limit(limit)
        if after_id is not None:
            stmt = stmt.where(User.id > after_id)
        async with self._session_factory() as session:
            return list((await session.execute(stmt)).scalars().all())

    async def resume_counts_for_users(self, user_ids: list[str]) -> dict[str, int]:
        """Exact resume row-count for a bounded batch of users (chunked reconcile)."""
        if not user_ids:
            return {}
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Resume.user_id, func.count())
                    .where(Resume.user_id.in_(user_ids))
                    .group_by(Resume.user_id)
                )
            ).all()
        return {uid: int(n) for uid, n in rows if uid is not None}

    async def application_counts_for_users(self, user_ids: list[str]) -> dict[str, int]:
        """Exact application row-count for a bounded batch of users (chunked reconcile)."""
        if not user_ids:
            return {}
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(Application.user_id, func.count())
                    .where(Application.user_id.in_(user_ids))
                    .group_by(Application.user_id)
                )
            ).all()
        return {uid: int(n) for uid, n in rows if uid is not None}

    # -- audit view (append-only; cross-cutting, centralized here) -----------

    async def list_audit(
        self,
        *,
        cursor: str | None = None,
        event: str | None = None,
        actor: str | None = None,
        target: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
    ) -> tuple[list[AuditLog], str | None]:
        """Return one keyset page of audit rows (ts desc, id desc) + next cursor."""
        limit = max(1, min(100, limit))
        decoded = decode_cursor(cursor)
        conditions = []
        if event:
            conditions.append(AuditLog.event == event)
        if actor:
            conditions.append(AuditLog.actor_user_id == actor)
        if target:
            conditions.append(AuditLog.target_user_id == target)
        if date_from:
            conditions.append(AuditLog.ts >= date_from)
        if date_to:
            conditions.append(AuditLog.ts < date_to)
        if decoded is not None:
            c_ts, c_id = decoded
            conditions.append(
                or_(AuditLog.ts < c_ts, and_(AuditLog.ts == c_ts, AuditLog.id < c_id))
            )
        stmt = select(AuditLog)
        if conditions:
            stmt = stmt.where(and_(*conditions))
        stmt = stmt.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit + 1)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1].ts, page[-1].id) if has_more and page else None
        return page, next_cursor

    async def recent_audit_for_target(self, user_id: str, *, limit: int = 20) -> list[AuditLog]:
        """The most recent audit rows where the user is actor or target."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(AuditLog)
                    .where(
                        or_(
                            AuditLog.target_user_id == user_id,
                            AuditLog.actor_user_id == user_id,
                        )
                    )
                    .order_by(AuditLog.ts.desc(), AuditLog.id.desc())
                    .limit(max(1, min(50, limit)))
                )
            ).scalars().all()
        return list(rows)

    # -- helpers -------------------------------------------------------------

    @staticmethod
    async def _count(session: AsyncSession, stmt) -> int:
        return int((await session.execute(stmt)).scalar() or 0)


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_repo: AdminRepo | None = None


def get_admin_repo() -> AdminRepo:
    """Return the process-wide :class:`AdminRepo` (bound to the app DB)."""
    global _repo
    if _repo is None:
        from app.database import db

        _repo = AdminRepo(db.session_factory)
    return _repo


def reset_admin_repo() -> None:
    """Drop the cached instance (test helper)."""
    global _repo
    _repo = None
