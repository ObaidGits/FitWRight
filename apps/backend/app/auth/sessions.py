"""Server-managed sessions: create/rotate/revoke/resolve + cache (Task 2.2).

The DB is the source of truth for a session; the KVStore is an O(1) cache in
front of it (design `§Session mechanics`, ADR-1/2, R2.1/3.1/3.3/3.4/3.6/12.4/
12.5/17.1/17.3). Key decisions implemented here:

- **Opaque token, hashed at rest.** ``create_session`` mints a 32-byte
  base64url token; only ``sha256(token)`` is stored (``token_hash``). The raw
  token is returned once to the caller for the ``__Host-`` cookie and never
  persisted — a DB leak cannot mint or resume sessions (Property 3).
- **Fixation defense via rotation.** ``rotate_session`` revokes the old row and
  issues a brand-new id/token for the same user (used on login, password change,
  role change).
- **Sliding expiry, write-behind.** Resolution extends ``expires_at`` from the
  idle timeout, bounded by the absolute cap (larger when ``remember_me``), but
  only writes when the last write is older than a refresh window — so there is
  no DB write on every request (R17.1). A crash loses at most one window.
- **Prompt revocation via write-through eviction.** logout / revoke / logout-all
  / role-change / disable / password-change delete the KVStore key(s), so a
  revoked or now-disabled session is rejected within one request cycle (R3.4,
  Property 3), and every resolution re-checks ``revoked_at`` + ``expires_at`` +
  ``user.status == active``.
- **Keyed ``ip_hash``.** IPs are stored as a salted HMAC (``IP_HASH_SECRET``) so
  they are not brute-forceable (R12.5).
- **Reaper.** ``reap`` batch-deletes expired / long-revoked sessions and expired
  verification/reset tokens (R17.3), single-flighted via the KVStore lock.

The service is injected with a session factory + KVStore + settings + clock, so
it is fully unit-testable; :func:`get_session_service` wires the process-wide
instance to the app DB and KVStore.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import (
    EmailChangeToken,
    EmailVerificationToken,
    PasswordResetToken,
    Session as SessionRow,
    User,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SessionInfo",
    "ResolvedSession",
    "SessionService",
    "hash_token",
    "parse_device_label",
    "get_session_service",
    "reset_session_service",
]

# Raw token size (bytes) before base64url encoding — 256 bits of entropy.
_TOKEN_BYTES = 32

# Cache TTL (seconds) for a resolved-session snapshot. Short by design: the DB is
# authoritative and write-through eviction handles revoke/disable, so the cache
# only needs to absorb bursts, not persist state.
_CACHE_TTL_SECONDS = 30

# Sessions revoked longer than this are eligible for reaping (kept briefly so a
# "why was I logged out" audit/debug window exists).
_REVOKED_GRACE_SECONDS = 60 * 60 * 24  # 24h


def hash_token(raw_token: str) -> str:
    """Return ``sha256(raw_token)`` hex — the value stored in ``token_hash``."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    """Mint a URL-safe 256-bit opaque session token."""
    return base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES)).rstrip(b"=").decode("ascii")


def parse_device_label(user_agent: str | None) -> str | None:
    """Best-effort human device label from a User-Agent (e.g. "Chrome on macOS").

    Deliberately tiny and dependency-free: it recognizes the common browser/OS
    pairs for the device list (R3.5) and falls back to a bounded, sanitized
    snippet. Never raises.
    """
    if not user_agent:
        return None
    ua = user_agent
    lowered = ua.lower()

    browser = None
    for needle, label in (
        ("edg/", "Edge"),
        ("opr/", "Opera"),
        ("chrome/", "Chrome"),
        ("crios/", "Chrome"),
        ("firefox/", "Firefox"),
        ("fxios/", "Firefox"),
        ("safari/", "Safari"),
    ):
        if needle in lowered:
            browser = label
            break

    os_name = None
    for needle, label in (
        ("windows nt", "Windows"),
        ("iphone", "iOS"),
        ("ipad", "iPadOS"),
        ("android", "Android"),
        ("mac os x", "macOS"),
        ("macintosh", "macOS"),
        ("cros", "ChromeOS"),
        ("linux", "Linux"),
    ):
        if needle in lowered:
            os_name = label
            break

    if browser and os_name:
        return f"{browser} on {os_name}"
    if browser:
        return browser
    if os_name:
        return os_name
    snippet = " ".join(ua.split())[:60]
    return snippet or None


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """A safe, dict-friendly view of a session row (never exposes the token)."""

    id: str
    user_id: str
    csrf_secret: str
    aal: str
    step_up_at: str | None
    remember_me: bool
    device_label: str | None
    ip_hash: str | None
    created_at: str
    last_seen_at: str
    expires_at: str
    revoked_at: str | None


@dataclass(frozen=True, slots=True)
class ResolvedSession:
    """Result of resolving a cookie token to an active session + its user.

    Carries exactly what building a ``Principal`` needs; the raw token and
    password hash never appear here.
    """

    session_id: str
    user_id: str
    csrf_secret: str
    aal: str
    step_up_at: str | None
    role: str
    status: str
    email: str
    name: str
    email_verified: bool
    expires_at: str


class SessionService:
    """Lifecycle + resolution for server-managed sessions."""

    _CACHE_PREFIX = "session"
    _REAP_LOCK_KEY = "session:reaper"

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        kvstore,
        *,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._kv = kvstore
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Only write-behind when the last write is older than this, to avoid a DB
        # write per request while keeping the sliding window fresh (R17.1).
        self._refresh_window = max(60, settings.idle_ttl // 20)

    # -- helpers -------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _now_iso(self) -> str:
        return self._now().isoformat()

    def _cache_key(self, token_hash: str) -> str:
        return f"{self._CACHE_PREFIX}:{token_hash}"

    def _absolute_ttl(self, remember_me: bool) -> int:
        return (
            self._settings.remember_me_ttl if remember_me else self._settings.session_absolute_ttl
        )

    def hash_ip(self, ip: str | None) -> str | None:
        """Keyed-HMAC of a client IP using ``IP_HASH_SECRET`` (R12.5).

        Returns ``None`` for a missing IP. The keyed HMAC (not a bare hash)
        makes the small IP space non-brute-forceable.
        """
        if not ip:
            return None
        return hmac.new(
            self._settings.ip_hash_secret.encode("utf-8"),
            ip.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # -- create / rotate -----------------------------------------------------

    async def create_session(
        self,
        user_id: str,
        *,
        remember_me: bool = False,
        ip: str | None = None,
        user_agent: str | None = None,
        aal: str = "aal1",
        step_up_at: str | None = None,
    ) -> tuple[str, SessionInfo]:
        """Create a session for ``user_id``; return ``(raw_token, SessionInfo)``.

        The raw token is returned exactly once (for the cookie) and is not
        recoverable afterwards — only its ``sha256`` is stored.
        """
        raw_token = _generate_raw_token()
        token_hash = hash_token(raw_token)
        now = self._now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=self._settings.idle_ttl)).isoformat()

        row = SessionRow(
            id=str(uuid4()),
            token_hash=token_hash,
            user_id=user_id,
            csrf_secret=secrets.token_urlsafe(32),
            aal=aal,
            step_up_at=step_up_at,
            remember_me=remember_me,
            device_label=parse_device_label(user_agent),
            ip_hash=self.hash_ip(ip),
            created_at=now_iso,
            last_seen_at=now_iso,
            expires_at=expires_at,
            revoked_at=None,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
        return raw_token, _row_to_info(row)

    async def rotate_session(
        self,
        old_raw_token: str,
        *,
        ip: str | None = None,
        user_agent: str | None = None,
        preserve_step_up: bool = False,
    ) -> tuple[str, SessionInfo] | None:
        """Revoke the session behind ``old_raw_token`` and issue a fresh one.

        Session-fixation defense: the new session has a new id and token. Returns
        the new ``(raw_token, SessionInfo)``, or ``None`` if the old token does
        not resolve to a live session.
        """
        old_hash = hash_token(old_raw_token)
        async with self._session_factory() as session:
            result = await session.execute(
                select(SessionRow).where(SessionRow.token_hash == old_hash)
            )
            old = result.scalars().first()
            if old is None or old.revoked_at is not None:
                return None
            user_id = old.user_id
            aal = old.aal
            step_up_at = old.step_up_at if preserve_step_up else None
            remember_me = old.remember_me
            old.revoked_at = self._now_iso()
            await session.commit()
        # Evict the old token's cache entry so it dies immediately.
        await self._evict(old_hash)
        return await self.create_session(
            user_id,
            remember_me=remember_me,
            ip=ip,
            user_agent=user_agent,
            aal=aal,
            step_up_at=step_up_at,
        )

    async def bump_step_up(self, session_id: str, *, aal: str | None = None) -> bool:
        """Record a successful step-up on a session (sets ``step_up_at``/aal).

        Evicts the cache so the fresh step-up window is reflected on the next
        resolution. Returns ``False`` if the session is missing/revoked.
        """
        async with self._session_factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is None or row.revoked_at is not None:
                return False
            row.step_up_at = self._now_iso()
            if aal is not None:
                row.aal = aal
            token_hash = row.token_hash
            await session.commit()
        await self._evict(token_hash)
        return True

    # -- revoke --------------------------------------------------------------

    async def revoke_session(self, session_id: str) -> bool:
        """Revoke one session by id (+ evict its cache). Idempotent."""
        async with self._session_factory() as session:
            row = await session.get(SessionRow, session_id)
            if row is None:
                return False
            token_hash = row.token_hash
            if row.revoked_at is None:
                row.revoked_at = self._now_iso()
                await session.commit()
        await self._evict(token_hash)
        return True

    async def revoke_by_token(self, raw_token: str) -> bool:
        """Revoke the session behind a raw cookie token (used by logout)."""
        token_hash = hash_token(raw_token)
        async with self._session_factory() as session:
            result = await session.execute(
                select(SessionRow).where(SessionRow.token_hash == token_hash)
            )
            row = result.scalars().first()
            if row is None:
                await self._evict(token_hash)
                return False
            if row.revoked_at is None:
                row.revoked_at = self._now_iso()
                await session.commit()
        await self._evict(token_hash)
        return True

    async def revoke_all_for_user(
        self, user_id: str, *, except_session_id: str | None = None
    ) -> int:
        """Revoke every active session for a user (+ evict caches).

        Used by logout-all, password reset/change, role change, and disable.
        ``except_session_id`` keeps the current session alive (password change
        revokes *other* sessions). Returns the number revoked.
        """
        now_iso = self._now_iso()
        revoked_hashes: list[str] = []
        async with self._session_factory() as session:
            result = await session.execute(
                select(SessionRow).where(
                    SessionRow.user_id == user_id,
                    SessionRow.revoked_at.is_(None),
                )
            )
            for row in result.scalars().all():
                if except_session_id is not None and row.id == except_session_id:
                    continue
                row.revoked_at = now_iso
                revoked_hashes.append(row.token_hash)
            await session.commit()
        for token_hash in revoked_hashes:
            await self._evict(token_hash)
        return len(revoked_hashes)

    async def _evict(self, token_hash: str) -> None:
        """Delete the KVStore cache entry for a token hash (best-effort)."""
        try:
            await self._kv.delete(self._cache_key(token_hash))
        except Exception:
            logger.warning("Failed to evict session cache entry", exc_info=True)

    # -- resolve -------------------------------------------------------------

    async def resolve(self, raw_token: str | None) -> ResolvedSession | None:
        """Resolve a raw cookie token to a live session, or ``None``.

        Path: token → ``sha256`` → KVStore cache → DB fallback → assert
        ``revoked_at IS NULL`` AND ``now < expires_at`` AND ``user.status ==
        active``. On the DB path a valid session is cached (short TTL) and its
        sliding expiry is extended write-behind. Any invalid state evicts the
        cache defensively so a stale entry cannot linger.
        """
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        cache_key = self._cache_key(token_hash)
        now = self._now()

        from app.auth.metrics import get_metrics

        metrics = get_metrics()

        # 1) cache
        try:
            cached = await self._kv.get(cache_key)
        except Exception:
            cached = None  # cache is best-effort; fall through to DB
        if cached is not None:
            resolved = _decode_snapshot(cached)
            if resolved is not None and self._snapshot_valid(resolved, now):
                metrics.record_session_cache(hit=True)
                return resolved
            # Stale/invalid snapshot — drop it and re-check the DB.
            await self._evict(token_hash)
        # A missing or stale/invalid cache entry is a cache miss → DB fallback.
        metrics.record_session_cache(hit=False)

        # 2) DB fallback
        async with self._session_factory() as session:
            result = await session.execute(
                select(SessionRow).where(SessionRow.token_hash == token_hash)
            )
            row = result.scalars().first()
            if row is None:
                return None
            # Revoked or past its absolute/idle expiry ⇒ dead (R3.4).
            if row.revoked_at is not None or not _now_lt_iso(now, row.expires_at):
                return None
            user = await session.get(User, row.user_id)
            if user is None or user.status != "active":
                return None

            # sliding expiry (write-behind)
            expires_at = row.expires_at
            if self._should_refresh(row, now):
                expires_at = self._extend_expiry(row, now)
                row.last_seen_at = now.isoformat()
                row.expires_at = expires_at
                # Keep the admin "last active" watermark fresh on the same
                # write-behind cadence (P2 Admin — no extra query/write per
                # request). The active-users *stat* reads sessions.last_seen_at
                # directly; this column powers the per-user display only.
                user.last_active_at = now.isoformat()
                await session.commit()

            resolved = ResolvedSession(
                session_id=row.id,
                user_id=row.user_id,
                csrf_secret=row.csrf_secret,
                aal=row.aal,
                step_up_at=row.step_up_at,
                role=user.role,
                status=user.status,
                email=user.email,
                name=user.name,
                email_verified=user.email_verified_at is not None,
                expires_at=expires_at,
            )

        # cache the fresh snapshot (best-effort)
        try:
            await self._kv.set(
                cache_key, _encode_snapshot(resolved), ttl_seconds=_CACHE_TTL_SECONDS
            )
        except Exception:
            logger.debug("Failed to cache session snapshot", exc_info=True)
        return resolved

    def _snapshot_valid(self, resolved: ResolvedSession, now: datetime) -> bool:
        """A cached snapshot is usable only while unexpired and its user active.

        Revoke/disable/role-change all *evict* the key (write-through), so a
        surviving snapshot is trustworthy for its short TTL; we still re-check
        expiry and status here as defense in depth (R3.4).
        """
        if resolved.status != "active":
            return False
        return _now_lt_iso(now, resolved.expires_at)

    def _should_refresh(self, row: SessionRow, now: datetime) -> bool:
        """Whether to write-behind the sliding window for this resolution."""
        try:
            last_seen = _parse_iso(row.last_seen_at)
        except ValueError:
            return True
        return (now - last_seen).total_seconds() >= self._refresh_window

    def _extend_expiry(self, row: SessionRow, now: datetime) -> str:
        """New ``expires_at`` = min(now + idle_ttl, created_at + absolute_cap)."""
        idle_deadline = now + timedelta(seconds=self._settings.idle_ttl)
        try:
            created = _parse_iso(row.created_at)
        except ValueError:
            created = now
        absolute_deadline = created + timedelta(seconds=self._absolute_ttl(row.remember_me))
        return min(idle_deadline, absolute_deadline).isoformat()

    # -- device list ---------------------------------------------------------

    async def list_active_sessions(self, user_id: str) -> list[SessionInfo]:
        """Active (unrevoked, unexpired) sessions for a user's device list."""
        now = self._now()
        async with self._session_factory() as session:
            result = await session.execute(
                select(SessionRow)
                .where(
                    SessionRow.user_id == user_id,
                    SessionRow.revoked_at.is_(None),
                )
                .order_by(SessionRow.last_seen_at.desc())
            )
            return [
                _row_to_info(row)
                for row in result.scalars().all()
                if _now_lt_iso(now, row.expires_at)
            ]

    # -- reaper --------------------------------------------------------------

    async def reap(self) -> dict[str, int]:
        """Batch-delete expired/long-revoked sessions + expired tokens (R17.3).

        Single-flighted via the KVStore lock so overlapping schedulers do not
        double-run. Returns per-table deletion counts (0s if the lock was held).
        """
        counts = {
            "sessions": 0,
            "email_tokens": 0,
            "reset_tokens": 0,
            "email_change_tokens": 0,
        }
        lock = self._kv.lock(self._REAP_LOCK_KEY, ttl_seconds=60, blocking=False)
        async with lock as acquired:
            if not acquired:
                return counts
            now = self._now()
            now_iso = now.isoformat()
            revoked_cutoff = (now - timedelta(seconds=_REVOKED_GRACE_SECONDS)).isoformat()
            async with self._session_factory() as session:
                sessions_stmt = delete(SessionRow).where(
                    (SessionRow.expires_at < now_iso)
                    | (
                        SessionRow.revoked_at.is_not(None)
                        & (SessionRow.revoked_at < revoked_cutoff)
                    )
                )
                counts["sessions"] = int(
                    (await session.execute(sessions_stmt)).rowcount or 0
                )
                counts["email_tokens"] = int(
                    (
                        await session.execute(
                            delete(EmailVerificationToken).where(
                                EmailVerificationToken.expires_at < now_iso
                            )
                        )
                    ).rowcount
                    or 0
                )
                counts["reset_tokens"] = int(
                    (
                        await session.execute(
                            delete(PasswordResetToken).where(
                                PasswordResetToken.expires_at < now_iso
                            )
                        )
                    ).rowcount
                    or 0
                )
                counts["email_change_tokens"] = int(
                    (
                        await session.execute(
                            delete(EmailChangeToken).where(
                                EmailChangeToken.expires_at < now_iso
                            )
                        )
                    ).rowcount
                    or 0
                )
                await session.commit()
        return counts


# ---------------------------------------------------------------------------
# Row/snapshot helpers
# ---------------------------------------------------------------------------


def _row_to_info(row: SessionRow) -> SessionInfo:
    return SessionInfo(
        id=row.id,
        user_id=row.user_id,
        csrf_secret=row.csrf_secret,
        aal=row.aal,
        step_up_at=row.step_up_at,
        remember_me=row.remember_me,
        device_label=row.device_label,
        ip_hash=row.ip_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def _encode_snapshot(resolved: ResolvedSession) -> str:
    return json.dumps(
        {
            "sid": resolved.session_id,
            "uid": resolved.user_id,
            "csrf": resolved.csrf_secret,
            "aal": resolved.aal,
            "step_up_at": resolved.step_up_at,
            "role": resolved.role,
            "status": resolved.status,
            "email": resolved.email,
            "name": resolved.name,
            "email_verified": resolved.email_verified,
            "expires_at": resolved.expires_at,
        }
    )


def _decode_snapshot(raw: str) -> ResolvedSession | None:
    try:
        data = json.loads(raw)
        return ResolvedSession(
            session_id=data["sid"],
            user_id=data["uid"],
            csrf_secret=data["csrf"],
            aal=data["aal"],
            step_up_at=data.get("step_up_at"),
            role=data["role"],
            status=data["status"],
            email=data["email"],
            name=data["name"],
            email_verified=bool(data.get("email_verified")),
            expires_at=data["expires_at"],
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _parse_iso(value: str) -> datetime:
    """Parse a stored ISO timestamp to an aware UTC datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now_lt_iso(now: datetime, iso_value: str) -> bool:
    """True if ``now < iso_value`` (i.e. not yet expired). Malformed → expired."""
    try:
        return now < _parse_iso(iso_value)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_service: SessionService | None = None


def get_session_service() -> SessionService:
    """Return the process-wide :class:`SessionService` (bound to app DB + KV)."""
    global _service
    if _service is None:
        from app.auth.runtime import get_kvstore
        from app.config import settings
        from app.database import db

        _service = SessionService(db.session_factory, get_kvstore(), settings=settings)
    return _service


def reset_session_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
