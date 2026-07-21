"""Google OAuth 2.0 / OpenID Connect provider (Task 7.1/7.2).

Implements :class:`~app.auth.oauth.base.OAuthProvider` against Google's fixed,
well-known endpoints (SSRF-safe - no user-supplied URLs ever reach here):

- authorize:  ``https://accounts.google.com/o/oauth2/v2/auth``
- token:      ``https://oauth2.googleapis.com/token``
- JWKS:       ``https://www.googleapis.com/oauth2/v3/certs``

Security decisions (design `§Google OAuth`, `§Security` OAuth rows, ADR-5):

- **Auth-code + PKCE.** :meth:`authorize_url` requests ``response_type=code``
  with ``state``, ``nonce``, and the S256 ``code_challenge``;
  :meth:`exchange` completes it server-side with the ``code_verifier``.
- **Full id_token verification.** :meth:`verify_id_token` checks the RS256
  signature against Google's **rotating** JWKS (refetched once on an unknown
  ``kid``), then ``iss`` ∈ {accounts.google.com, https://accounts.google.com},
  ``aud`` == client id, ``exp``/``iat`` within a bounded **clock-skew leeway**,
  and ``nonce`` equality - before any claim is trusted (R4.2).
- **Testability / no live calls.** The token-exchange HTTP client, the JWKS
  provider, and the clock are all **injected**, so unit tests drive a mock IdP
  and a mock JWKS with a fixed clock and never touch Google (design
  "Testability" constraint). The process defaults build ``httpx``-backed clients
  with an in-process JWKS cache.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from collections.abc import Callable

from app.auth.oauth.base import OAuthError, OAuthProvider, OAuthTokens, OAuthUserInfo

logger = logging.getLogger(__name__)

__all__ = [
    "GOOGLE_AUTHORIZE_ENDPOINT",
    "GOOGLE_TOKEN_ENDPOINT",
    "GOOGLE_JWKS_URI",
    "GOOGLE_ISSUERS",
    "HttpxTokenClient",
    "HttpxJwksClient",
    "GoogleOAuthProvider",
]

# Fixed Google endpoints (never user-supplied - SSRF-safe).
GOOGLE_AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# Default tolerance (seconds) for ``exp``/``iat`` to absorb clock skew (R4.2).
_DEFAULT_LEEWAY_SECONDS = 60


# ---------------------------------------------------------------------------
# Injected collaborators (default httpx-backed; mocked in tests)
# ---------------------------------------------------------------------------


class HttpxTokenClient:
    """Default token-exchange client: form-POST to a fixed endpoint via httpx."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def post_form(self, url: str, data: dict[str, str]) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, data=data)
        if resp.status_code != 200:
            raise OAuthError("token_exchange_failed", f"token endpoint {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:  # pragma: no cover - malformed provider response
            raise OAuthError("token_exchange_failed", "non-JSON token response") from exc


class HttpxJwksClient:
    """Default JWKS provider: fetches a fixed JWKS URI via httpx, caches in-proc.

    Google rotates signing keys, so :meth:`get_jwks` refetches on demand
    (``force_refresh``) when the caller sees an unknown ``kid`` (R4.2 rotation
    handling). The cache is a plain in-process dict - fine because a JWKS is
    public and cheap to refetch, and correctness never depends on it.
    """

    def __init__(self, jwks_uri: str = GOOGLE_JWKS_URI, *, timeout: float = 10.0) -> None:
        self._jwks_uri = jwks_uri
        self._timeout = timeout
        self._cache: dict | None = None

    async def _fetch(self) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._jwks_uri)
        if resp.status_code != 200:
            raise OAuthError("jwks_fetch_failed", f"jwks endpoint {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:  # pragma: no cover
            raise OAuthError("jwks_fetch_failed", "non-JSON jwks response") from exc

    async def get_jwks(self, *, force_refresh: bool = False) -> dict:
        if force_refresh or self._cache is None:
            self._cache = await self._fetch()
        return self._cache


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def _b64url_json(segment: str) -> dict:
    """Decode a base64url JWT segment to a JSON object (no signature check)."""
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))


def _unverified_header(id_token: str) -> dict:
    try:
        header_segment = id_token.split(".", 1)[0]
        header = _b64url_json(header_segment)
        if not isinstance(header, dict):
            raise ValueError
        return header
    except Exception as exc:  # noqa: BLE001
        raise OAuthError("malformed_id_token", "cannot parse id_token header") from exc


def _find_key(jwks: dict, kid: str | None) -> dict | None:
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    if not keys:
        return None
    if kid is None:
        return keys[0] if len(keys) == 1 else None
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class GoogleOAuthProvider(OAuthProvider):
    """Google OpenID Connect provider (auth-code + PKCE, full id_token verify)."""

    name = "google"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_client: object | None = None,
        jwks_client: object | None = None,
        clock: Callable[[], float] | None = None,
        leeway_seconds: int = _DEFAULT_LEEWAY_SECONDS,
        authorize_endpoint: str = GOOGLE_AUTHORIZE_ENDPOINT,
        token_endpoint: str = GOOGLE_TOKEN_ENDPOINT,
        issuers: frozenset[str] = GOOGLE_ISSUERS,
    ) -> None:
        if not client_id or not client_secret or not redirect_uri:
            # Guard against constructing a half-configured provider.
            raise OAuthError("provider_not_configured", "google is not fully configured")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_client = token_client or HttpxTokenClient()
        self._jwks_client = jwks_client or HttpxJwksClient()
        # ``clock`` returns epoch seconds (float); default = wall clock.
        self._clock = clock or time.time
        self._leeway = leeway_seconds
        self._authorize_endpoint = authorize_endpoint
        self._token_endpoint = token_endpoint
        self._issuers = issuers

    # -- authorize -----------------------------------------------------------

    def authorize_url(
        self,
        *,
        state: str,
        nonce: str,
        challenge: str,
        next: str | None = None,
    ) -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Ask Google not to return refresh tokens we would only discard.
            "access_type": "online",
            "prompt": "select_account",
        }
        return f"{self._authorize_endpoint}?{urlencode(params)}"

    # -- exchange ------------------------------------------------------------

    async def exchange(self, code: str, verifier: str) -> OAuthTokens:
        if not code:
            raise OAuthError("missing_code", "authorization code is required")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "redirect_uri": self._redirect_uri,
            "code_verifier": verifier,
        }
        try:
            payload = await self._token_client.post_form(self._token_endpoint, data)
        except OAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - network/other -> normalized
            raise OAuthError("token_exchange_failed", str(exc)) from exc

        if not isinstance(payload, dict) or payload.get("error"):
            raise OAuthError("token_exchange_failed", "token endpoint returned an error")
        id_token = payload.get("id_token")
        if not id_token or not isinstance(id_token, str):
            raise OAuthError("missing_id_token", "no id_token in token response")
        return OAuthTokens(
            id_token=id_token,
            access_token=payload.get("access_token"),
            refresh_token=payload.get("refresh_token"),
            raw=payload,
        )

    # -- verify --------------------------------------------------------------

    async def verify_id_token(self, id_token: str, nonce: str) -> OAuthUserInfo:
        if not id_token:
            raise OAuthError("missing_id_token", "id_token is required")
        header = _unverified_header(id_token)
        kid = header.get("kid")

        # Resolve the signing key, refetching JWKS once on an unknown kid so a
        # key rotation between issuance and verification is handled (R4.2).
        jwks = await self._jwks_client.get_jwks()
        if _find_key(jwks, kid) is None:
            jwks = await self._jwks_client.get_jwks(force_refresh=True)
            if _find_key(jwks, kid) is None:
                raise OAuthError("unknown_signing_key", f"no JWKS key for kid={kid!r}")

        claims = self._verify_signature(id_token, jwks)
        self._validate_claims(claims, nonce)

        email = claims.get("email")
        sub = claims.get("sub")
        if not sub or not email:
            raise OAuthError("incomplete_claims", "id_token missing sub/email")
        return OAuthUserInfo(
            sub=str(sub),
            email=str(email),
            email_verified=bool(claims.get("email_verified", False)),
            name=(str(claims["name"]) if claims.get("name") else None),
        )

    def _verify_signature(self, id_token: str, jwks: dict) -> dict:
        """Verify the RS256 signature against the JWKS; return the claims dict."""
        from authlib.jose import jwt as jose_jwt
        from authlib.jose.errors import JoseError

        try:
            claims = jose_jwt.decode(id_token, jwks)
        except JoseError as exc:
            raise OAuthError("bad_signature", "id_token signature invalid") from exc
        except Exception as exc:  # noqa: BLE001 - key mismatch/other decode failure
            raise OAuthError("bad_signature", "id_token could not be verified") from exc
        return dict(claims)

    def _validate_claims(self, claims: dict, nonce: str) -> None:
        """Validate iss/aud/exp/iat (± leeway) and nonce (R4.2)."""
        iss = claims.get("iss")
        if iss not in self._issuers:
            raise OAuthError("bad_issuer", f"unexpected iss {iss!r}")

        aud = claims.get("aud")
        # ``aud`` may be a string or a list per the OIDC spec.
        aud_ok = aud == self._client_id or (
            isinstance(aud, (list, tuple)) and self._client_id in aud
        )
        if not aud_ok:
            raise OAuthError("bad_audience", "id_token audience mismatch")

        now = float(self._clock())
        exp = claims.get("exp")
        if exp is None:
            raise OAuthError("missing_exp", "id_token has no exp")
        try:
            if now > float(exp) + self._leeway:
                raise OAuthError("expired", "id_token is expired")
        except (TypeError, ValueError) as exc:
            raise OAuthError("bad_exp", "id_token exp is invalid") from exc

        iat = claims.get("iat")
        if iat is not None:
            try:
                if now < float(iat) - self._leeway:
                    raise OAuthError("bad_iat", "id_token issued in the future")
            except (TypeError, ValueError) as exc:
                raise OAuthError("bad_iat", "id_token iat is invalid") from exc

        if not nonce or claims.get("nonce") != nonce:
            raise OAuthError("nonce_mismatch", "id_token nonce mismatch")
