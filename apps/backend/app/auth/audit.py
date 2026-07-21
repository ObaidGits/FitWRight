"""Append-only security audit writer with meta sanitization (Task 2.4).

Every security-relevant event (signup, login, login_failed, logout, logout_all,
password_changed, password_reset, email_verified, email_changed, role_changed,
user_disabled, oauth_link, session_revoked, step_up) is recorded as an immutable
:class:`app.models.AuditLog` row (R16.2). The writer is defensive by design:

- **Never persists secrets/PII.** ``meta`` is passed through
  :func:`sanitize_meta`, which drops any key that looks like a secret/token and
  bounds/one-lines every string value. The only identity stored is ``user_id``
  (in the dedicated actor/target columns), matching R16.1 ("no secrets/PII
  beyond user_id").
- **Prevents log injection.** String values are stripped of CR/LF and other
  control characters and length-bounded, so a crafted email/name/user-agent
  cannot forge a second audit line or blow up a row (R13.4-adjacent hardening).
- **Fails soft.** An audit write must never break the user-facing flow it is
  observing; a persistence error is logged and swallowed.

The service is injectable with a session factory + clock for isolated testing;
:func:`get_audit_service` returns the process-wide instance bound to the app DB.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditLog

logger = logging.getLogger(__name__)

__all__ = [
    "AuditEvent",
    "AuditService",
    "sanitize_meta",
    "sanitize_log_value",
    "get_audit_service",
    "reset_audit_service",
]

# Maximum stored length for any single sanitized string value and for the whole
# meta blob's key count - bounds a single audit row's size.
_MAX_VALUE_LENGTH = 500
_MAX_META_KEYS = 50

# Substrings that mark a key as secret-bearing; such keys are dropped wholesale
# rather than stored (even sanitized), so a token can never land in the audit
# trail via a mis-passed meta dict.
_SECRET_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "csrf",
    "api_key",
    "apikey",
    "private",
    "credential",
    "session",
    "code_verifier",
    "client_secret",
)


class AuditEvent:
    """Canonical event names (R16.2). String constants, grouped for discoverability."""

    SIGNUP = "signup"
    LOGIN = "login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFIED = "email_verified"
    EMAIL_CHANGED = "email_changed"
    ROLE_CHANGED = "role.changed"
    USER_DISABLED = "user_disabled"
    OAUTH_LINK = "oauth_link"
    SESSION_REVOKED = "session_revoked"
    STEP_UP = "auth.step_up"
    AUTHZ_DENIED = "authz.denied"
    # --- P2 Admin lifecycle + sensitive reads (dotted, admin-namespaced) ---
    ADMIN_USER_VIEWED = "admin.user_viewed"
    # Sensitive config-diagnostics read (admin-panel-upgrade Req 10/15.3/15.9).
    ADMIN_CONFIG_VIEWED = "admin.config_viewed"
    # Maintenance action invocation (admin-panel-upgrade Req 18.2/18.6): one of the
    # four fixed, idempotent ``admin.manage`` actions that only re-invoke an
    # existing single-flighted job. The specific action is stored in meta.
    ADMIN_MAINTENANCE_ACTION = "admin.maintenance_action"
    ADMIN_USER_DISABLED = "user.disabled"
    ADMIN_USER_ENABLED = "user.enabled"
    ADMIN_USER_SOFT_DELETED = "user.soft_deleted"
    ADMIN_USER_RESTORED = "user.restored"
    ADMIN_USER_PURGED = "user.purged"
    ADMIN_SETTING_CHANGED = "admin.setting_changed"


def sanitize_log_value(value: Any, *, max_length: int = _MAX_VALUE_LENGTH) -> Any:
    """Make ``value`` safe to store/log.

    Strings are stripped of control characters (CR/LF/tab and the rest of the
    C0/C1 range plus DEL) - collapsing them to spaces so a value cannot inject a
    newline into a log line - then length-bounded. Non-string scalars pass
    through; nested containers are sanitized recursively.
    """
    if isinstance(value, str):
        cleaned = "".join(
            " " if (ord(ch) < 0x20 or ord(ch) == 0x7F) else ch for ch in value
        )
        cleaned = cleaned.strip()
        if len(cleaned) > max_length:
            cleaned = cleaned[: max_length - 3] + "..."
        return cleaned
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, dict):
        return sanitize_meta(value, _depth=1)
    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item, max_length=max_length) for item in value[:_MAX_META_KEYS]]
    # Fallback: stringify unknown types, then sanitize.
    return sanitize_log_value(str(value), max_length=max_length)


def sanitize_meta(meta: dict[str, Any] | None, *, _depth: int = 0) -> dict[str, Any] | None:
    """Return a sanitized copy of ``meta`` safe for the audit trail.

    Keys whose name matches a secret marker are dropped entirely; remaining
    values are passed through :func:`sanitize_log_value`. The key count is
    bounded. Returns ``None`` for a ``None``/empty input.
    """
    if not meta:
        return None
    if _depth > 4:  # guard against pathological nesting
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in meta.items():
        if len(cleaned) >= _MAX_META_KEYS:
            break
        key_str = str(key)
        lowered = key_str.casefold()
        if any(marker in lowered for marker in _SECRET_KEY_MARKERS):
            # Drop secret-bearing keys outright (do not even store a redaction
            # of the value - the key's presence is enough of a hint).
            continue
        safe_key = sanitize_log_value(key_str, max_length=100)
        cleaned[str(safe_key)] = sanitize_log_value(value)
    return cleaned or None


class AuditService:
    """Persists sanitized, append-only audit rows."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def record(
        self,
        event: str,
        *,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        ip_hash: str | None = None,
        request_id: str | None = None,
        meta: dict[str, Any] | None = None,
        raise_on_error: bool = False,
    ) -> None:
        """Append one audit row.

        Fails soft by default (``raise_on_error=False``): an audit write must
        never break the user-facing flow it observes, so a persistence error is
        logged and swallowed. A Sensitive_Endpoint that MUST treat a failed
        audit as a hard error (admin-panel-upgrade Req 15.9 - the access is only
        legitimate if it is traceable) passes ``raise_on_error=True`` so the
        exception propagates and the caller can refuse to report success.
        """
        row = AuditLog(
            id=str(uuid4()),
            ts=self._clock().isoformat(),
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            event=str(event),
            ip_hash=ip_hash,
            request_id=request_id,
            meta=sanitize_meta(meta),
        )
        try:
            async with self._session_factory() as session:
                session.add(row)
                await session.commit()
        except Exception:  # pragma: no cover - defensive; audit must not break flows
            logger.exception("Failed to write audit row for event=%s", event)
            if raise_on_error:
                raise


# ---------------------------------------------------------------------------
# Process-wide instance bound to the app database
# ---------------------------------------------------------------------------

_service: AuditService | None = None


def get_audit_service() -> AuditService:
    """Return the process-wide :class:`AuditService` (bound to the app DB)."""
    global _service
    if _service is None:
        from app.database import db

        _service = AuditService(db.session_factory)
    return _service


def reset_audit_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
