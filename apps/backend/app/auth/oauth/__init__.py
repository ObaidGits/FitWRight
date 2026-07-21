"""Provider-abstracted OAuth (Google now; GitHub/Microsoft later) - Task 7.

Module layout (design `§OAuth provider interface`):

- ``base`` - the provider-agnostic :class:`OAuthProvider` contract + shared types
  (:class:`OAuthTokens`, :class:`OAuthUserInfo`, :class:`OAuthError`).
- ``google`` - the Google OpenID Connect implementation (auth-code + PKCE, full
  id_token verification against a rotating JWKS with clock-skew tolerance).
- ``registry`` - the ``name -> factory`` allow-list so only known providers are
  routable; adding a provider is one :func:`register` call.
- ``state`` - signed, httpOnly, 5-minute transient cookie carrying
  ``state``/``nonce``/PKCE ``verifier`` (+ optional ``next``) plus PKCE helpers.
- ``linking`` - the safe link/create decision (Property 5 / R4.4).
"""

from app.auth.oauth.base import (
    OAuthError,
    OAuthProvider,
    OAuthTokens,
    OAuthUserInfo,
)
from app.auth.oauth.google import GoogleOAuthProvider
from app.auth.oauth.linking import (
    LinkAction,
    LinkResult,
    link_or_create_user,
)
from app.auth.oauth.registry import (
    ProviderNotConfigured,
    ProviderRegistry,
    UnknownProvider,
    registry,
)
from app.auth.oauth.state import (
    OAUTH_TXN_COOKIE,
    OAUTH_TXN_TTL_SECONDS,
    OAuthTransaction,
    deserialize_transaction,
    generate_nonce,
    generate_pkce_verifier,
    generate_state,
    pkce_challenge,
    serialize_transaction,
)

__all__ = [
    # base
    "OAuthProvider",
    "OAuthTokens",
    "OAuthUserInfo",
    "OAuthError",
    # google
    "GoogleOAuthProvider",
    # registry
    "ProviderRegistry",
    "UnknownProvider",
    "ProviderNotConfigured",
    "registry",
    # state / pkce
    "OAuthTransaction",
    "OAUTH_TXN_COOKIE",
    "OAUTH_TXN_TTL_SECONDS",
    "generate_state",
    "generate_nonce",
    "generate_pkce_verifier",
    "pkce_challenge",
    "serialize_transaction",
    "deserialize_transaction",
    # linking
    "LinkAction",
    "LinkResult",
    "link_or_create_user",
]
