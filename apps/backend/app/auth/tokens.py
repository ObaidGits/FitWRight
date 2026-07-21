"""Hashed, single-use, TTL-bound tokens for verification & reset (Task 5).

Email verification (R5.1) and password reset (R6.1) both hand the user a
*secret link* whose token must be un-forgeable, single-use, short-lived, and
useless to an attacker who reads the database. This module is the one place that
mints and consumes those tokens for both flows, so the security properties live
together and cannot drift apart:

- **Raw token in the link, only the hash at rest.** :meth:`issue` mints a
  256-bit URL-safe random token, returns the *raw* value (for the emailed link),
  and stores only ``sha256(raw)`` (``token_hash`` is the table PK). A database
  leak therefore cannot reconstruct a working link (design `§Data models`,
  R12.4, Property 3-adjacent).
- **Single-use.** :meth:`consume` marks ``used_at`` in the same transaction it
  validates the token, so a token verifies at most once.
- **Prior tokens invalidated on re-issue.** Issuing a new token for a user first
  marks every *unused* token of that kind for that user as used, so an earlier
  link (e.g. from a resend) can no longer be redeemed (R5.3, R6.1).
- **Constant-time hash compare via PK lookup.** The stored value is a SHA-256
  hash and the lookup is by exact hash (indexed PK); no attacker-controlled
  prefix comparison happens against the raw secret (R6.4).

The service is model-agnostic (it operates on
:class:`~app.models.EmailVerificationToken` or
:class:`~app.models.PasswordResetToken`), injected with a session factory + clock
so it is deterministic to unit-test; :func:`get_token_service` wires the
process-wide instance to the app database.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import EmailChangeToken, EmailVerificationToken, PasswordResetToken

logger = logging.getLogger(__name__)

__all__ = [
    "TokenConsumeResult",
    "EmailChangeConsumeResult",
    "TokenService",
    "hash_token_value",
    "get_token_service",
    "reset_token_service",
]

# Raw token entropy (bytes) before base64url encoding - 256 bits.
_TOKEN_BYTES = 32


def hash_token_value(raw_token: str) -> str:
    """Return ``sha256(raw_token)`` hex - the value stored in ``token_hash``."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _generate_raw_token() -> str:
    """Mint a URL-safe 256-bit opaque token for an email link."""
    return (
        base64.urlsafe_b64encode(secrets.token_bytes(_TOKEN_BYTES))
        .rstrip(b"=")
        .decode("ascii")
    )


@dataclass(frozen=True, slots=True)
class TokenConsumeResult:
    """Outcome of consuming a token.

    ``ok`` is the decision. On success ``user_id`` is the token's owner. On
    failure ``reason`` is one of ``"invalid"`` (no such token), ``"used"``
    (already redeemed), or ``"expired"`` - callers collapse all three into a
    single generic error so nothing about *why* is disclosed (R5.5).
    """

    ok: bool
    user_id: str | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class EmailChangeConsumeResult:
    """Outcome of consuming an email-change token.

    Like :class:`TokenConsumeResult` but also carries the pending ``new_email``
    on success, so the caller can swap the account's primary email to the
    verified address (R7.4).
    """

    ok: bool
    user_id: str | None = None
    new_email: str | None = None
    reason: str = ""


class TokenService:
    """Mint/consume hashed single-use TTL tokens for verification + reset."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return self._clock()

    # -- generic issue/consume ----------------------------------------------

    async def _issue(self, model: type, user_id: str, ttl_seconds: int) -> str:
        """Invalidate prior unused tokens for ``user_id``, then mint a new one.

        Returns the *raw* token (only its ``sha256`` is stored).
        """
        now = self._now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        raw_token = _generate_raw_token()
        token_hash = hash_token_value(raw_token)

        async with self._session_factory() as session:
            # Invalidate every still-unused token of this kind for the user so an
            # earlier link (e.g. a prior resend) can no longer be redeemed.
            await session.execute(
                update(model)
                .where(model.user_id == user_id, model.used_at.is_(None))
                .values(used_at=now_iso)
            )
            session.add(
                model(
                    token_hash=token_hash,
                    user_id=user_id,
                    expires_at=expires_at,
                    used_at=None,
                    created_at=now_iso,
                )
            )
            await session.commit()
        return raw_token

    def _row_state(self, row, now: datetime) -> TokenConsumeResult:
        """Classify a token row (invalid/used/expired/ok) without mutating it."""
        if row is None:
            return TokenConsumeResult(ok=False, reason="invalid")
        if row.used_at is not None:
            return TokenConsumeResult(ok=False, reason="used")
        try:
            expires = datetime.fromisoformat(row.expires_at)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return TokenConsumeResult(ok=False, reason="expired")
        if now >= expires:
            return TokenConsumeResult(ok=False, reason="expired")
        return TokenConsumeResult(ok=True, user_id=row.user_id)

    async def _peek(self, model: type, raw_token: str) -> TokenConsumeResult:
        """Validate ``raw_token`` **without** consuming it (read-only).

        Lets a caller (password reset) reject a bad new password *before* burning
        the single-use token, so a typo does not force the user to request a new
        link. The atomic single-use guarantee still comes from :meth:`_consume`.
        """
        if not raw_token:
            return TokenConsumeResult(ok=False, reason="invalid")
        token_hash = hash_token_value(raw_token)
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(model).where(model.token_hash == token_hash)
                )
            ).scalars().first()
            return self._row_state(row, self._now())

    async def _consume(self, model: type, raw_token: str) -> TokenConsumeResult:
        """Validate + single-use-consume ``raw_token`` for ``model``."""
        if not raw_token:
            return TokenConsumeResult(ok=False, reason="invalid")
        token_hash = hash_token_value(raw_token)
        now = self._now()
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(model).where(model.token_hash == token_hash)
                )
            ).scalars().first()
            state = self._row_state(row, now)
            if not state.ok:
                return state
            row.used_at = now.isoformat()
            await session.commit()
        return state

    # -- verification --------------------------------------------------------

    async def issue_verification(self, user_id: str) -> str:
        """Issue an email-verification token; returns the raw token (R5.1/5.3)."""
        return await self._issue(
            EmailVerificationToken, user_id, self._settings.email_verification_ttl
        )

    async def consume_verification(self, raw_token: str) -> TokenConsumeResult:
        """Single-use-consume an email-verification token (R5.2)."""
        return await self._consume(EmailVerificationToken, raw_token)

    # -- reset ---------------------------------------------------------------

    async def issue_reset(self, user_id: str) -> str:
        """Issue a password-reset token; returns the raw token (R6.1)."""
        return await self._issue(
            PasswordResetToken, user_id, self._settings.password_reset_ttl
        )

    async def peek_reset(self, raw_token: str) -> TokenConsumeResult:
        """Validate a reset token without consuming it (policy-check-first UX)."""
        return await self._peek(PasswordResetToken, raw_token)

    async def consume_reset(self, raw_token: str) -> TokenConsumeResult:
        """Single-use-consume a password-reset token (R6.2)."""
        return await self._consume(PasswordResetToken, raw_token)

    # -- email change --------------------------------------------------------

    async def issue_email_change(self, user_id: str, new_email: str) -> str:
        """Issue an email-change token bound to ``new_email`` (R7.4).

        Invalidates any prior unused email-change token for the user (so an
        earlier pending change can no longer be confirmed) and stores the pending
        target address alongside the hashed token. Returns the raw token for the
        emailed confirmation link; only its ``sha256`` is persisted.
        """
        now = self._now()
        now_iso = now.isoformat()
        expires_at = (
            now + timedelta(seconds=self._settings.email_verification_ttl)
        ).isoformat()
        raw_token = _generate_raw_token()
        token_hash = hash_token_value(raw_token)

        async with self._session_factory() as session:
            await session.execute(
                update(EmailChangeToken)
                .where(
                    EmailChangeToken.user_id == user_id,
                    EmailChangeToken.used_at.is_(None),
                )
                .values(used_at=now_iso)
            )
            session.add(
                EmailChangeToken(
                    token_hash=token_hash,
                    user_id=user_id,
                    new_email=new_email,
                    expires_at=expires_at,
                    used_at=None,
                    created_at=now_iso,
                )
            )
            await session.commit()
        return raw_token

    async def consume_email_change(self, raw_token: str) -> EmailChangeConsumeResult:
        """Validate + single-use-consume an email-change token (R7.4).

        On success returns the owning ``user_id`` and the pending ``new_email``
        so the caller can switch the account's primary email to the now-verified
        address. Missing/used/expired all collapse to a generic failure.
        """
        if not raw_token:
            return EmailChangeConsumeResult(ok=False, reason="invalid")
        token_hash = hash_token_value(raw_token)
        now = self._now()
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(EmailChangeToken).where(
                        EmailChangeToken.token_hash == token_hash
                    )
                )
            ).scalars().first()
            state = self._row_state(row, now)
            if not state.ok:
                return EmailChangeConsumeResult(ok=False, reason=state.reason)
            new_email = row.new_email
            row.used_at = now.isoformat()
            await session.commit()
        return EmailChangeConsumeResult(
            ok=True, user_id=state.user_id, new_email=new_email
        )


# ---------------------------------------------------------------------------
# Process-wide instance bound to the app database
# ---------------------------------------------------------------------------

_service: TokenService | None = None


def get_token_service() -> TokenService:
    """Return the process-wide :class:`TokenService` (bound to the app DB)."""
    global _service
    if _service is None:
        from app.config import settings
        from app.database import db

        _service = TokenService(db.session_factory, settings=settings)
    return _service


def reset_token_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
