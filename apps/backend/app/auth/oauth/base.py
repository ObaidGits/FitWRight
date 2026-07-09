"""Provider-agnostic OAuth interface + shared types (Task 7.1).

Google sign-in is implemented behind a small, stable ``OAuthProvider`` contract
so a second provider (GitHub/Microsoft, …) is a new implementation + one registry
entry with **no router or UI change** (design `§OAuth provider interface`, R4.1).
Everything a provider must do is expressed here:

- :meth:`OAuthProvider.authorize_url` — build the IdP consent-screen URL for the
  authorization-code + PKCE flow (``response_type=code``, ``state``, ``nonce``,
  ``code_challenge``); ``next`` is accepted for interface symmetry but is carried
  in the transient cookie, not the URL (SSRF-safe: no user-supplied endpoints).
- :meth:`OAuthProvider.exchange` — swap the authorization ``code`` (+ the PKCE
  ``code_verifier``) for the provider's tokens, **server-side only**.
- :meth:`OAuthProvider.verify_id_token` — fully verify the ``id_token``
  (signature via rotating JWKS, ``iss``/``aud``/``exp``/``iat`` with bounded clock
  skew, ``nonce``) and return the trusted :class:`OAuthUserInfo` claims.

Errors raised by any step are normalized to :class:`OAuthError` so the callback
can collapse every failure into a single ``oauth_failed`` outcome (R4.6) without
leaking *why*.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

__all__ = [
    "OAuthError",
    "OAuthTokens",
    "OAuthUserInfo",
    "OAuthProvider",
]


class OAuthError(Exception):
    """Any OAuth-flow failure (exchange/verify/config).

    ``reason`` is a short machine tag for server-side logs/metrics
    (``oauth-failure-by-reason``, R16.1); it is **never** surfaced to the browser
    — the callback always renders the single generic ``oauth_failed`` (R4.6).
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        super().__init__(message or reason)


@dataclass(frozen=True, slots=True)
class OAuthTokens:
    """Tokens returned by the code exchange.

    Only ``id_token`` is used in P1 (identity proof); access/refresh tokens are
    accepted so the shape is provider-complete but are **discarded** after
    id_token verification — no provider API calls are made beyond sign-in and
    the tokens never reach the browser (R4.5).
    """

    id_token: str
    access_token: str | None = None
    refresh_token: str | None = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class OAuthUserInfo:
    """The trusted identity claims extracted from a verified ``id_token``.

    Produced only *after* signature + ``iss``/``aud``/``exp``/``iat``/``nonce``
    validation, so every field here is safe to trust for linking/creation (R4.2).
    """

    sub: str
    email: str
    email_verified: bool
    name: str | None = None


class OAuthProvider(abc.ABC):
    """The stable, provider-agnostic OAuth contract (R4.1)."""

    #: Registry key / URL segment (e.g. ``"google"``). Set by each implementation.
    name: str = ""

    @abc.abstractmethod
    def authorize_url(
        self,
        *,
        state: str,
        nonce: str,
        challenge: str,
        next: str | None = None,
    ) -> str:
        """Build the IdP authorize URL for the auth-code + PKCE flow.

        ``state``/``nonce`` are CSRF/replay defenses echoed back and verified in
        the callback; ``challenge`` is the S256 PKCE ``code_challenge``. ``next``
        is accepted for interface symmetry but is persisted in the signed
        transient cookie (not the URL), so the redirect target is never
        attacker-influenced (SSRF-safe).
        """

    @abc.abstractmethod
    async def exchange(self, code: str, verifier: str) -> OAuthTokens:
        """Exchange an authorization ``code`` (+ PKCE ``verifier``) for tokens.

        Runs entirely server-side against the provider's fixed token endpoint.
        Raises :class:`OAuthError` on any failure.
        """

    @abc.abstractmethod
    async def verify_id_token(self, id_token: str, nonce: str) -> OAuthUserInfo:
        """Fully verify an ``id_token`` and return its trusted claims.

        Verifies the signature against the provider's (rotating, cached) JWKS and
        validates ``iss``/``aud``/``exp``/``iat`` (bounded clock skew) and
        ``nonce``. Raises :class:`OAuthError` if any check fails. The caller
        additionally enforces ``email_verified`` before trusting the email (R4.5).
        """
