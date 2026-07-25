"""Admin-invite service (secure admin signup - Option B).

An existing admin issues a single-use, TTL-bound, email-bound invitation to
create an ``admin`` account. Only the ``sha256`` of a cryptographically random
token is persisted (never the raw token), mirroring the verification/reset
token tables. Redemption happens at ``/auth/signup`` with the raw token:

- the token must exist, be unexpired, unused, and its bound email must match the
  signup email (proving control of the invited inbox, ~ email verification);
- single-use is enforced ATOMICALLY (``UPDATE ... WHERE used_at IS NULL``), so a
  token can never mint two accounts even under concurrent redemption;
- the created account's role comes ONLY from the invite - never the request body.

Security notes:
- Raw tokens are compared by their hash; we never store or log the raw value.
- ``token_hash`` is never returned by any endpoint; ``id`` is the public handle.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select, update

from app.auth.owner import normalize_email
from app.models import AdminInvite

logger = logging.getLogger(__name__)

__all__ = [
    "AdminInviteRecord",
    "create_invite",
    "list_invites",
    "revoke_invite",
    "claim_invite",
    "hash_invite_token",
]

# A generous default; the exact lifetime comes from settings at call time.
_DEFAULT_TTL_HOURS = 72


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _resolve_db(db):
    if db is not None:
        return db
    from app.database import db as default_db

    return default_db


def hash_invite_token(raw_token: str) -> str:
    """SHA-256 hex of the raw token (what we persist + look up by)."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdminInviteRecord:
    """A safe, token-free projection of an invite row (never carries the hash)."""

    id: str
    email: str
    role: str
    created_by: str | None
    expires_at: str
    used_at: str | None
    used_by: str | None
    revoked_at: str | None
    revoked_by: str | None
    revoke_reason: str | None
    created_at: str

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= _now_iso()

    @property
    def status(self) -> str:
        # A completed redemption is immutable and takes precedence. Revocation
        # reason distinguishes an automatic reissue from a manual admin action.
        if self.is_used:
            return "used"
        if self.revoked_at is not None:
            return "superseded" if self.revoke_reason == "superseded" else "revoked"
        if self.is_expired:
            return "expired"
        return "active"


def _to_record(row: AdminInvite) -> AdminInviteRecord:
    return AdminInviteRecord(
        id=row.id,
        email=row.email,
        role=row.role,
        created_by=row.created_by,
        expires_at=row.expires_at,
        used_at=row.used_at,
        used_by=row.used_by,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        revoke_reason=row.revoke_reason,
        created_at=row.created_at,
    )


async def create_invite(
    *,
    email: str,
    created_by: str | None,
    role: str = "admin",
    ttl_hours: int | None = None,
    db=None,
) -> tuple[str, AdminInviteRecord]:
    """Issue a new invite bound to ``email``; returns ``(raw_token, record)``.

    Any prior active invite for the same email is explicitly marked
    ``superseded`` so reissuing preserves truthful history while leaving only
    one redeemable link.
    """
    db = _resolve_db(db)
    normalized = normalize_email(email)
    if ttl_hours is None:
        try:
            from app.config import settings

            ttl_hours = int(settings.admin_invite_ttl_hours)
        except Exception:  # pragma: no cover - defensive
            ttl_hours = _DEFAULT_TTL_HOURS
    ttl_hours = max(1, min(720, ttl_hours))

    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_invite_token(raw_token)
    now = _now()
    now_iso = now.isoformat()
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()

    async with db.session_factory() as session:
        # Supersede only currently active links. Used, revoked, superseded, and
        # expired rows remain truthful lifecycle history.
        await session.execute(
            update(AdminInvite)
            .where(
                AdminInvite.email == normalized,
                AdminInvite.used_at.is_(None),
                AdminInvite.revoked_at.is_(None),
                AdminInvite.expires_at > now_iso,
            )
            .values(
                revoked_at=now_iso,
                revoked_by=created_by,
                revoke_reason="superseded",
            )
        )
        row = AdminInvite(
            id=str(uuid4()),
            token_hash=token_hash,
            email=normalized,
            role=role,
            created_by=created_by,
            expires_at=expires_at,
            used_at=None,
            used_by=None,
            revoked_at=None,
            revoked_by=None,
            revoke_reason=None,
            created_at=now_iso,
        )
        session.add(row)
        await session.commit()
        record = _to_record(row)

    return raw_token, record


async def list_invites(*, limit: int = 100, db=None) -> list[AdminInviteRecord]:
    """Return bounded recent invite lifecycle history, newest first."""
    db = _resolve_db(db)
    bounded_limit = max(1, min(200, int(limit)))
    async with db.session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AdminInvite)
                    .order_by(AdminInvite.created_at.desc())
                    .limit(bounded_limit)
                )
            )
            .scalars()
            .all()
        )
    return [_to_record(r) for r in rows]


async def revoke_invite(
    invite_id: str,
    *,
    revoked_by: str | None,
    reason: str = "manual",
    db=None,
) -> bool:
    """Revoke an active invite and retain fixed actor/reason provenance.

    Idempotent: used, expired, revoked, superseded, or absent rows return False.
    """
    db = _resolve_db(db)
    now_iso = _now_iso()
    safe_reason = "superseded" if reason == "superseded" else "manual"
    async with db.session_factory() as session:
        result = await session.execute(
            update(AdminInvite)
            .where(
                AdminInvite.id == invite_id,
                AdminInvite.used_at.is_(None),
                AdminInvite.revoked_at.is_(None),
                AdminInvite.expires_at > now_iso,
            )
            .values(
                revoked_at=now_iso,
                revoked_by=revoked_by,
                revoke_reason=safe_reason,
            )
        )
        await session.commit()
        return bool(result.rowcount and result.rowcount > 0)


async def claim_invite(
    *, raw_token: str, email: str, used_by: str | None = None, db=None
) -> tuple[str, str | None]:
    """Validate + ATOMICALLY consume an invite. Returns ``(status, role)``.

    ``status`` is one of ``ok`` | ``not_found`` | ``expired`` | ``used`` |
    ``revoked`` | ``superseded`` | ``email_mismatch``. On ``ok`` the invite is
    atomically marked used (single-use) and its ``role`` is returned; otherwise
    ``role`` is ``None`` and nothing is mutated. The email match is part of the validation so a leaked link cannot be
    redeemed for a different address.
    """
    db = _resolve_db(db)
    token_hash = hash_invite_token(raw_token)
    normalized = normalize_email(email)

    async with db.session_factory() as session:
        row = (
            await session.execute(
                select(AdminInvite).where(AdminInvite.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        if row is None:
            return "not_found", None
        if row.used_at is not None:
            return "used", None
        if row.revoked_at is not None:
            status = "superseded" if row.revoke_reason == "superseded" else "revoked"
            return status, None
        now_iso = _now_iso()
        if row.expires_at <= now_iso:
            return "expired", None
        if row.email != normalized:
            return "email_mismatch", None

        # Atomic single-use claim: every eligibility predicate is repeated so a
        # concurrent redemption, revoke, supersession, or expiry cannot win too.
        result = await session.execute(
            update(AdminInvite)
            .where(
                AdminInvite.id == row.id,
                AdminInvite.email == normalized,
                AdminInvite.used_at.is_(None),
                AdminInvite.revoked_at.is_(None),
                AdminInvite.expires_at > now_iso,
            )
            .values(used_at=now_iso, used_by=used_by)
        )
        await session.commit()
        if not (result.rowcount and result.rowcount > 0):
            # Lost the race - another request consumed it first.
            return "used", None
        return "ok", row.role


async def record_invite_redeemer(*, raw_token: str, used_by: str, db=None) -> None:
    """Best-effort: attach the created account's id to the consumed invite.

    Called AFTER the invited account is created (the id isn't known at claim
    time). Non-critical audit linkage; never raises into the signup path.
    """
    db = _resolve_db(db)
    token_hash = hash_invite_token(raw_token)
    try:
        async with db.session_factory() as session:
            await session.execute(
                update(AdminInvite)
                .where(AdminInvite.token_hash == token_hash)
                .values(used_by=used_by)
            )
            await session.commit()
    except Exception:  # pragma: no cover - linkage is best-effort
        logger.debug("Failed to link invite redeemer", exc_info=True)
