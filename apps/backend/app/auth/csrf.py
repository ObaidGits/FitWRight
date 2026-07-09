"""CSRF token derivation, pre-session tokens, and ``next`` validation (Task 2.3).

Two distinct CSRF defenses live here (design `§Session mechanics`, R12.1/12.2):

1. **Per-session double-submit** — once a session exists, the JS-readable
   ``csrf`` cookie carries ``HMAC(session.csrf_secret, session.id)``. Every state-
   changing request must echo that value in the ``X-CSRF-Token`` header; the
   server recomputes and compares in constant time. Because the value is bound to
   a server-side secret (``csrf_secret``, stored on the session row) an attacker
   who can only *set* a cookie cannot forge a matching header.
2. **Pre-session token** — login and signup happen before any session exists, so
   they are protected against *login-CSRF* by a signed double-submit token from
   ``GET /auth/csrf``: a random nonce plus ``HMAC(SESSION_SECRET, nonce)``. The
   value is placed in a cookie and returned in the body; the client submits it in
   the header, and the server verifies the signature and the cookie/header match.
   Signing lets the stateless server reject forged tokens without storing them,
   and the dual-key (``SESSION_SECRET`` + ``SESSION_SECRET_PREV``) verify window
   makes secret rotation seamless (R16.3).

Also here: :func:`validate_next_path`, the shared open-redirect guard used by
login and OAuth — a safe ``next`` must be a single-leading-slash, same-origin app
path (``//host``, ``https://…``, and backslash tricks are rejected, R11.4).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

logger = logging.getLogger(__name__)

__all__ = [
    "derive_csrf_token",
    "verify_csrf_token",
    "issue_presession_token",
    "verify_presession_token",
    "presession_double_submit_ok",
    "validate_next_path",
]

# Separator between the nonce and its signature in a pre-session token.
_TOKEN_SEP = "."


def _hmac_hex(key: str, message: str) -> str:
    """Hex HMAC-SHA256 of ``message`` under ``key`` (both UTF-8)."""
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Per-session CSRF (double-submit bound to the session's secret)
# ---------------------------------------------------------------------------


def derive_csrf_token(session_id: str, csrf_secret: str) -> str:
    """Derive the per-session CSRF cookie value = ``HMAC(csrf_secret, session_id)``.

    The session's ``csrf_secret`` (random per session) is the HMAC key, so the
    token is unforgeable without reading the session row and cannot be replayed
    across sessions.
    """
    return _hmac_hex(csrf_secret, session_id)


def verify_csrf_token(provided: str | None, session_id: str, csrf_secret: str) -> bool:
    """Constant-time check that ``provided`` matches the derived session token."""
    if not provided:
        return False
    expected = derive_csrf_token(session_id, csrf_secret)
    return hmac.compare_digest(provided, expected)


# ---------------------------------------------------------------------------
# Pre-session CSRF (signed double-submit for login/signup — login-CSRF defense)
# ---------------------------------------------------------------------------


def issue_presession_token(secret: str, *, nonce_bytes: int = 32) -> str:
    """Issue a signed pre-session token: ``<nonce>.<hmac(secret, nonce)>``.

    The nonce is a fresh random value; the signature binds it to
    ``SESSION_SECRET`` so a forged token (or one signed with an unknown key) is
    rejected without any server-side storage.
    """
    nonce = secrets.token_urlsafe(nonce_bytes)
    signature = _hmac_hex(secret, nonce)
    return f"{nonce}{_TOKEN_SEP}{signature}"


def verify_presession_token(
    token: str | None, secret: str, *, secret_prev: str = ""
) -> bool:
    """Verify a pre-session token's signature (constant time, dual-key).

    ``secret_prev`` supports zero-downtime secret rotation: a token signed with
    the previous key still validates during the rollover window (R16.3). This
    checks *signature validity only*; the caller additionally enforces that the
    cookie value and the header value are equal (the double-submit half).
    """
    if not token:
        return False
    nonce, _, signature = token.partition(_TOKEN_SEP)
    if not nonce or not signature:
        return False
    for key in (secret, secret_prev):
        if not key:
            continue
        if hmac.compare_digest(signature, _hmac_hex(key, nonce)):
            return True
    return False


def presession_double_submit_ok(
    cookie_value: str | None,
    header_value: str | None,
    secret: str,
    *,
    secret_prev: str = "",
) -> bool:
    """Full pre-session check: valid signature AND cookie == header (constant time)."""
    if not cookie_value or not header_value:
        return False
    if not hmac.compare_digest(cookie_value, header_value):
        return False
    return verify_presession_token(cookie_value, secret, secret_prev=secret_prev)


# ---------------------------------------------------------------------------
# Open-redirect guard for `next`
# ---------------------------------------------------------------------------


def validate_next_path(next_path: str | None) -> str | None:
    """Return a safe same-origin app path, or ``None`` if ``next`` is unsafe.

    Rules (R11.4): the value must be a non-empty string that starts with a single
    ``/`` and is **not** a scheme-relative (``//host``) or protocol
    (``https:``) URL, and must contain no backslash (which some browsers
    normalize to ``/``) or control characters. Anything else — including an
    absolute URL to another origin — is rejected so a crafted ``next`` cannot
    bounce the user off-site after login.
    """
    if not next_path or not isinstance(next_path, str):
        return None
    # Reject control characters (incl. CR/LF/tab) and whitespace that could be
    # used to smuggle a second header or confuse the parser.
    if any(ord(ch) < 0x20 or ch == "\x7f" for ch in next_path):
        return None
    if "\\" in next_path:
        return None
    # Must be an absolute *path* on this origin: exactly one leading slash.
    # ``//host`` (scheme-relative) and any absolute URL are thereby rejected —
    # an absolute URL like ``https://evil`` does not start with ``/`` at all.
    if not next_path.startswith("/") or next_path.startswith("//"):
        return None
    return next_path
