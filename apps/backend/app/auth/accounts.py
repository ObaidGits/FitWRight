"""User-account persistence for the auth flows (Task 4).

A thin, testable data layer over the (non-owned) ``users`` table that the auth
and profile routers call instead of touching the ORM directly. It returns
immutable :class:`AccountRecord` snapshots (never live ORM rows or the
``password_hash`` outside :func:`get_password_hash`), so callers cannot
accidentally serialize a sensitive column.

Email normalization (NFKC + trim + lowercase) is centralized in
:func:`app.auth.owner.normalize_email` and reused here so lookups and inserts
agree with the migration/owner-bootstrap paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.auth.owner import normalize_email
from app.models import User

__all__ = [
    "AccountRecord",
    "EmailInUseError",
    "normalize_email",
    "email_exists",
    "get_by_email",
    "get_by_id",
    "get_password_hash",
    "create_user",
    "update_name",
    "mark_email_verified",
    "set_password_hash",
    "set_email",
]


class EmailInUseError(Exception):
    """Raised when an email is already registered to another account (R7.4).

    Surfaced when swapping a user's primary email loses the uniqueness race
    (another account claimed the address between the pre-check and the commit);
    the router maps it to ``409 email_unavailable``.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class AccountRecord:
    """A safe snapshot of a ``users`` row (no ``password_hash``)."""

    id: str
    email: str
    name: str
    role: str
    status: str
    avatar_url: str | None
    email_verified_at: str | None
    updated_at: str
    avatar_key: str | None = None
    headline: str | None = None
    location: str | None = None
    links: list | None = None
    # Canonical profile-image metadata (Photo System). Metadata only - never
    # binary. ``avatar_checksum`` powers content-addressed dedup.
    avatar_width: int | None = None
    avatar_height: int | None = None
    avatar_checksum: str | None = None
    avatar_format: str | None = None
    avatar_bytes: int | None = None
    avatar_dominant_color: str | None = None
    avatar_updated_at: str | None = None

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


def _to_record(row: User) -> AccountRecord:
    return AccountRecord(
        id=row.id,
        email=row.email,
        name=row.name,
        role=row.role,
        status=row.status,
        avatar_url=row.avatar_url,
        email_verified_at=row.email_verified_at,
        updated_at=row.updated_at,
        avatar_key=getattr(row, "avatar_key", None),
        headline=getattr(row, "headline", None),
        location=getattr(row, "location", None),
        links=getattr(row, "links", None),
        avatar_width=getattr(row, "avatar_width", None),
        avatar_height=getattr(row, "avatar_height", None),
        avatar_checksum=getattr(row, "avatar_checksum", None),
        avatar_format=getattr(row, "avatar_format", None),
        avatar_bytes=getattr(row, "avatar_bytes", None),
        avatar_dominant_color=getattr(row, "avatar_dominant_color", None),
        avatar_updated_at=getattr(row, "avatar_updated_at", None),
    )


async def set_avatar(
    user_id: str,
    *,
    avatar_url: str,
    avatar_key: str,
    metadata: "dict | None" = None,
    db=None,
) -> tuple[AccountRecord | None, str | None]:
    """Set the avatar URL+key (+ canonical metadata) after a successful store.

    Returns ``(record, old_key)``. The old key is returned so the caller can
    garbage-collect the replaced object (R13.2). The url is only ever set *after*
    a successful upload (no dangling url on storage failure - the caller stores
    first, then calls this). ``metadata`` carries the Photo-System master fields
    (width/height/checksum/format/bytes/dominant_color); when omitted the columns
    are left untouched.
    """
    db = _resolve_db(db)
    meta = metadata or {}
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None, None
        old_key = row.avatar_key
        row.avatar_url = avatar_url
        row.avatar_key = avatar_key
        now = _now_iso()
        if meta:
            row.avatar_width = meta.get("width")
            row.avatar_height = meta.get("height")
            row.avatar_checksum = meta.get("checksum")
            row.avatar_format = meta.get("format")
            row.avatar_bytes = meta.get("byte_size")
            row.avatar_dominant_color = meta.get("dominant_color")
            row.avatar_updated_at = now
        row.updated_at = now
        await session.commit()
        return _to_record(row), old_key


async def clear_avatar(user_id: str, *, db=None) -> tuple[AccountRecord | None, str | None]:
    """Remove the avatar (url/key/metadata) and return ``(record, old_key)``.

    Used by the delete endpoint. The old key is returned so the caller can
    garbage-collect the stored object (best-effort; retention also sweeps).
    """
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None, None
        old_key = row.avatar_key
        row.avatar_url = None
        row.avatar_key = None
        row.avatar_width = None
        row.avatar_height = None
        row.avatar_checksum = None
        row.avatar_format = None
        row.avatar_bytes = None
        row.avatar_dominant_color = None
        row.avatar_updated_at = _now_iso()
        row.updated_at = _now_iso()
        await session.commit()
        return _to_record(row), old_key


async def update_profile(
    user_id: str,
    *,
    headline: str | None,
    location: str | None,
    links: list | None,
    db=None,
) -> AccountRecord | None:
    """Update the extended profile fields (already validated) - R14.1."""
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None
        row.headline = headline
        row.location = location
        row.links = links
        row.updated_at = _now_iso()
        await session.commit()
        return _to_record(row)


async def all_avatar_keys(db=None) -> set[str]:
    """Return every referenced avatar key (orphan-GC reference set - R13.2)."""
    db = _resolve_db(db)
    async with db.session_factory() as session:
        rows = (
            await session.execute(select(User.avatar_key).where(User.avatar_key.is_not(None)))
        ).scalars().all()
        return {k for k in rows if k}


def _resolve_db(db):
    if db is not None:
        return db
    from app.database import db as default_db

    return default_db


async def get_by_email(email: str, *, db=None) -> AccountRecord | None:
    """Look up an account by (normalized) email, or ``None``."""
    db = _resolve_db(db)
    normalized = normalize_email(email)
    async with db.session_factory() as session:
        row = (
            await session.execute(select(User).where(User.email == normalized))
        ).scalars().first()
        return _to_record(row) if row is not None else None


async def email_exists(email: str, *, db=None) -> bool:
    """Whether a (normalized) email is already registered to some account.

    Used by the email-change flow to enforce uniqueness before issuing a
    confirmation link (R7.4). The authoritative guard is the unique index (a
    concurrent claim is caught at commit via :class:`EmailInUseError`); this is
    the fast pre-check for a clean 409.
    """
    return await get_by_email(email, db=db) is not None


async def get_by_id(user_id: str, *, db=None) -> AccountRecord | None:
    """Look up an account by id, or ``None``."""
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        return _to_record(row) if row is not None else None


async def get_password_hash(user_id: str, *, db=None) -> str | None:
    """Return the stored Argon2 hash for a user (``None`` for OAuth-only).

    Isolated here so the hash is fetched deliberately (for verification) and
    never travels alongside an :class:`AccountRecord`.
    """
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        return row.password_hash if row is not None else None


async def create_user(
    *,
    email: str,
    name: str,
    password_hash: str | None,
    status: str,
    role: str = "user",
    email_verified_at: str | None = None,
    db=None,
) -> AccountRecord:
    """Insert a new user (email is normalized) and return its snapshot."""
    db = _resolve_db(db)
    now = _now_iso()
    row = User(
        id=str(uuid4()),
        email=normalize_email(email),
        name=name,
        password_hash=password_hash,
        role=role,
        status=status,
        avatar_url=None,
        email_verified_at=email_verified_at,
        mfa_enrolled=False,
        created_at=now,
        updated_at=now,
    )
    async with db.session_factory() as session:
        session.add(row)
        await session.commit()
    return _to_record(row)


async def update_name(
    user_id: str,
    name: str,
    *,
    expected_updated_at: str | None = None,
    db=None,
) -> tuple[str, AccountRecord | None]:
    """Update a user's display name with optional optimistic concurrency.

    Returns ``(outcome, record)`` where ``outcome`` is one of ``"ok"``,
    ``"conflict"`` (``expected_updated_at`` no longer matches - R Reliability),
    or ``"not_found"``. ``role``/``status`` are never touched here (R7.2).
    """
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return "not_found", None
        if expected_updated_at is not None and row.updated_at != expected_updated_at:
            return "conflict", _to_record(row)
        row.name = name
        row.updated_at = _now_iso()
        await session.commit()
        return "ok", _to_record(row)


async def mark_email_verified(user_id: str, *, db=None) -> AccountRecord | None:
    """Mark a user's email verified and activate a pending account (R5.2).

    Sets ``email_verified_at`` (idempotent - an already-verified timestamp is
    left untouched) and transitions ``pending_verification`` -> ``active``. A
    ``disabled`` account is *not* re-activated here (only the pending state is a
    verification gate). Returns the updated snapshot, or ``None`` if the user is
    gone.
    """
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None
        now = _now_iso()
        if row.email_verified_at is None:
            row.email_verified_at = now
        if row.status == "pending_verification":
            row.status = "active"
        row.updated_at = now
        await session.commit()
        return _to_record(row)


async def set_password_hash(user_id: str, password_hash: str, *, db=None) -> AccountRecord | None:
    """Set a user's Argon2 password hash (R6.2/6.3).

    Used by password reset: it overwrites the stored hash for a password account
    and, for an OAuth-only account (``password_hash IS NULL``), *sets* one -
    linking password auth (R6.3). Returns the updated snapshot, or ``None`` if
    the user is gone.
    """
    db = _resolve_db(db)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None
        row.password_hash = password_hash
        row.updated_at = _now_iso()
        await session.commit()
        return _to_record(row)


async def set_email(user_id: str, new_email: str, *, db=None) -> AccountRecord | None:
    """Switch a user's primary email to the (verified) ``new_email`` (R7.4).

    Only called after the new address has been confirmed via an email-change
    token, so the switch also (re)marks the account verified. The email is
    normalized and uniqueness is enforced by the ``ux_users_email`` unique index:
    if a concurrent request claimed the address first, the commit raises and this
    surfaces :class:`EmailInUseError` (mapped to ``409 email_unavailable``).
    Returns the updated snapshot, or ``None`` if the user is gone.
    """
    from sqlalchemy.exc import IntegrityError

    db = _resolve_db(db)
    normalized = normalize_email(new_email)
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
        if row is None:
            return None
        now = _now_iso()
        row.email = normalized
        # The new address proved ownership via the confirmation link.
        row.email_verified_at = now
        if row.status == "pending_verification":
            row.status = "active"
        row.updated_at = now
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise EmailInUseError(normalized) from exc
        return _to_record(row)
