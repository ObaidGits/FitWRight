"""Issue/verify/revoke bearer tokens for the MCP mount.

Deliberately mirrors the Session trust model rather than the single-use email
tokens: MCP tokens are long-lived, revocable, and per-user. Raw tokens exist
only in the client's config; the DB keeps sha256 only.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from app.auth.sessions import _now_lt_iso
from app.models import McpToken, User

logger = logging.getLogger(__name__)


class McpTokenLimitError(Exception):
    """The user already holds the configured maximum of non-revoked tokens."""


def _hash(raw: str) -> str:
    """sha256 of the raw token - the only form ever persisted."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _now_iso() -> str:
    """Current UTC time as ISO-8601 (lexical comparison, TinyDB-era contract)."""
    return datetime.now(timezone.utc).isoformat()


class McpTokenService:
    def __init__(self, session_factory) -> None:
        self._sf = session_factory
        self._last_touch: dict[str, str] = {}  # token_id -> iso; throttles last_used_at writes

    async def issue(self, user_id: str, label: str, *, ttl_days: int = 0) -> tuple[dict, str]:
        """Mint a token; returns (public record without hash, raw token).

        ``ttl_days=0`` (default) means no expiry. The raw ``fw_``-prefixed token
        is returned exactly once and never persisted.

        Raises :class:`McpTokenLimitError` when the user already holds
        ``settings.mcp_max_tokens_per_user`` non-revoked tokens - an
        unauthenticated client cannot grow the verification table (or the
        Settings list) without bound.
        """
        from app.config import settings

        raw = f"fw_{secrets.token_urlsafe(32)}"
        expires = None
        if ttl_days:
            expires = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
        async with self._sf() as s:
            active = (await s.execute(
                select(func.count()).select_from(McpToken).where(
                    McpToken.user_id == user_id, McpToken.revoked_at.is_(None)
                )
            )).scalar_one()
            if active >= settings.mcp_max_tokens_per_user:
                raise McpTokenLimitError(
                    f"Active-token limit reached ({settings.mcp_max_tokens_per_user}). "
                    "Revoke an existing token before creating a new one."
                )
            row = McpToken(token_hash=_hash(raw), user_id=user_id, label=label,
                           created_at=_now_iso(), expires_at=expires)
            s.add(row)
            await s.commit()
            return self._public(row), raw

    async def verify(self, raw: str) -> dict | None:
        """Active token row ({id, user_id, label}) or None. Also stamps last_used_at.

        The token's owner must still be an ``active`` user (same rule the
        session path enforces, ``SessionService.resolve``): a disabled or
        soft-deleted account loses its MCP access on the very next request,
        not only its browser sessions (R6.1).
        """
        if not raw.startswith("fw_"):
            return None
        now = datetime.now(timezone.utc)
        async with self._sf() as s:
            row = (await s.execute(
                select(McpToken).where(McpToken.token_hash == _hash(raw))
            )).scalar_one_or_none()
            if row is None or row.revoked_at is not None:
                return None
            # Fail-closed expiry (house helper, app.auth.sessions): a malformed
            # ``expires_at`` reads as expired, and "expiring this second" is
            # already expired (now < expires_at is the only live window).
            if row.expires_at and not _now_lt_iso(now, row.expires_at):
                return None
            # One extra indexed PK lookup per request: the owner row decides.
            # DB errors propagate (the verifier fails closed on them).
            user = await s.get(User, row.user_id)
            if user is None or user.status != "active" or user.deleted_at is not None:
                return None
        # Telemetry must never take a valid auth down (Task 4 routes every MCP
        # request through verify()): a failed last_used_at write is logged and
        # swallowed, and the token still authenticates.
        try:
            await self.touch(row.id)
        except Exception:
            logger.debug("mcp token last_used_at stamp failed", exc_info=True)
        return {"id": row.id, "user_id": row.user_id, "label": row.label}

    async def revoke(self, user_id: str, token_id: str) -> bool:
        """Owner-scoped revoke: another user's id returns False and revokes nothing."""
        async with self._sf() as s:
            res = await s.execute(
                update(McpToken)
                .where(McpToken.id == token_id, McpToken.user_id == user_id,
                       McpToken.revoked_at.is_(None))
                .values(revoked_at=_now_iso())
            )
            await s.commit()
            return res.rowcount > 0

    async def list_for_user(self, user_id: str) -> list[dict]:
        """All of a user's tokens (masked: never includes token_hash)."""
        async with self._sf() as s:
            rows = (await s.execute(
                select(McpToken).where(McpToken.user_id == user_id)
                .order_by(McpToken.created_at.desc())
            )).scalars().all()
        return [self._public(r) for r in rows]

    async def touch(self, token_id: str) -> None:
        """Best-effort last_used_at, throttled to one write/minute per token."""
        now = _now_iso()
        if self._last_touch.get(token_id, "") > now[:16]:  # same minute
            return
        self._last_touch[token_id] = now
        async with self._sf() as s:
            await s.execute(
                update(McpToken).where(McpToken.id == token_id).values(last_used_at=now)
            )
            await s.commit()

    @staticmethod
    def _public(row: McpToken) -> dict:
        """Row as a safe dict: token_hash is deliberately absent."""
        return {"id": row.id, "user_id": row.user_id, "label": row.label,
                "created_at": row.created_at, "last_used_at": row.last_used_at,
                "expires_at": row.expires_at, "revoked_at": row.revoked_at}


# ---------------------------------------------------------------------------
# Process-wide instance bound to the app database
# ---------------------------------------------------------------------------

_service: McpTokenService | None = None


def get_mcp_token_service() -> McpTokenService:
    """Return the process-wide :class:`McpTokenService` (bound to the app DB)."""
    global _service
    if _service is None:
        from app.database import db

        _service = McpTokenService(db.session_factory)
    return _service


def reset_mcp_token_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
