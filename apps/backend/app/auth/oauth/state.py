"""Signed, short-lived transient OAuth state cookie + PKCE helpers (Task 7.1).

The authorization-code + PKCE flow needs three secrets to survive the round-trip
to the IdP and back — ``state`` (CSRF/replay), ``nonce`` (id_token binding), and
the PKCE ``code_verifier`` — plus an optional validated ``next`` path. They must
never be exposed to JavaScript and must expire quickly (design `§Google OAuth`,
ADR-5, R4.1).

They are packed into a **single signed, httpOnly, ``SameSite=Lax`` transient
cookie** using ``itsdangerous`` (:class:`~itsdangerous.URLSafeTimedSerializer`),
signed with ``SESSION_SECRET`` and read back with a dual-key window
(``SESSION_SECRET`` + ``SESSION_SECRET_PREV``) so secret rotation is seamless
(R16.3). The serializer's embedded timestamp enforces the 5-minute TTL on read
(``max_age``), so a stale or forged cookie is rejected without any server-side
storage — keeping the flow stateless and multi-worker-safe (ADR-6).

Using one signed blob (rather than several cookies) makes the transient state
atomic: it is set once at ``/start`` and cleared as a unit on both success and
failure at ``/callback`` (R4.6).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import Settings, settings as app_settings

__all__ = [
    "OAUTH_TXN_COOKIE",
    "OAUTH_TXN_TTL_SECONDS",
    "OAuthTransaction",
    "generate_state",
    "generate_nonce",
    "generate_pkce_verifier",
    "pkce_challenge",
    "serialize_transaction",
    "deserialize_transaction",
]

# Transient cookie name. Not ``__Host-`` prefixed so it still works in local
# non-secure (http) dev; it is httpOnly + SameSite=Lax + short-lived regardless.
OAUTH_TXN_COOKIE = "oauth_txn"

# 5-minute lifetime for the whole round-trip (design `§Google OAuth`).
OAUTH_TXN_TTL_SECONDS = 300

# itsdangerous salt namespacing this serializer's tokens.
_TXN_SALT = "oauth-transient-v1"


@dataclass(frozen=True, slots=True)
class OAuthTransaction:
    """The transient state carried across the IdP round-trip."""

    provider: str
    state: str
    nonce: str
    verifier: str
    next: str | None = None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "p": self.provider,
            "s": self.state,
            "n": self.nonce,
            "v": self.verifier,
            "x": self.next,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "OAuthTransaction":
        return cls(
            provider=payload["p"],
            state=payload["s"],
            nonce=payload["n"],
            verifier=payload["v"],
            next=payload.get("x"),
        )


def _serializer(config: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt=_TXN_SALT)


def generate_state(*, nbytes: int = 32) -> str:
    """Random opaque ``state`` (CSRF/replay defense)."""
    return secrets.token_urlsafe(nbytes)


def generate_nonce(*, nbytes: int = 32) -> str:
    """Random opaque ``nonce`` bound into the id_token."""
    return secrets.token_urlsafe(nbytes)


def generate_pkce_verifier(*, nbytes: int = 48) -> str:
    """Random PKCE ``code_verifier`` (RFC 7636: 43-128 URL-safe chars)."""
    # 48 bytes → 64 base64url chars, comfortably inside the 43-128 window.
    return secrets.token_urlsafe(nbytes)


def pkce_challenge(verifier: str) -> str:
    """S256 PKCE ``code_challenge`` = base64url(sha256(verifier)), unpadded."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def serialize_transaction(txn: OAuthTransaction, *, config: Settings | None = None) -> str:
    """Sign + serialize the transaction into the transient cookie value."""
    config = config or app_settings
    return _serializer(config).dumps(txn.to_payload())


def deserialize_transaction(
    value: str | None, *, config: Settings | None = None
) -> OAuthTransaction | None:
    """Verify + parse a transient cookie value, or ``None`` if invalid/expired.

    Enforces the 5-minute TTL (``max_age``) and accepts a signature from either
    the current or the previous session secret (rotation window). Any tampering,
    expiry, or malformed payload yields ``None`` so the callback fails closed.
    """
    if not value:
        return None
    config = config or app_settings
    candidates = [config.session_secret]
    if config.session_secret_prev:
        candidates.append(config.session_secret_prev)
    for secret in candidates:
        serializer = URLSafeTimedSerializer(secret, salt=_TXN_SALT)
        try:
            payload = serializer.loads(value, max_age=OAUTH_TXN_TTL_SECONDS)
        except (SignatureExpired, BadSignature):
            continue
        except Exception:  # noqa: BLE001 - malformed token → fail closed
            continue
        try:
            return OAuthTransaction.from_payload(payload)
        except (KeyError, TypeError):
            return None
    return None
