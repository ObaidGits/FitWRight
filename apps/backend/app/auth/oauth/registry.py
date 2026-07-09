"""Provider registry / allow-list — only known providers are routable (Task 7.1).

The OAuth routes are provider-generic (``/auth/oauth/{provider}/…``), so an
allow-list is the boundary that stops an attacker from poking at arbitrary
``{provider}`` values: a name that is not registered is rejected before any work
happens (R4.1). Adding GitHub/Microsoft later is a single :func:`register` call
here — no router or UI change.

A registered provider is built lazily by its **factory** (which reads
configuration), so a provider that is known but not configured (e.g. Google with
no client id/secret) raises :class:`ProviderNotConfigured` on resolve — the
router turns that into a clean "not configured" error while still 404-ing a truly
unknown provider. Built instances are cached so the JWKS cache and HTTP client
are reused across the ``start`` and ``callback`` requests; :meth:`reset` clears
the cache for tests / secret rotation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.auth.oauth.base import OAuthError, OAuthProvider

logger = logging.getLogger(__name__)

__all__ = [
    "ProviderNotConfigured",
    "UnknownProvider",
    "ProviderRegistry",
    "registry",
]


class UnknownProvider(OAuthError):
    """Raised when a ``{provider}`` segment is not on the allow-list."""

    def __init__(self, name: str) -> None:
        super().__init__("unknown_provider", f"unknown OAuth provider {name!r}")
        self.provider = name


class ProviderNotConfigured(OAuthError):
    """Raised when a known provider lacks the configuration to run."""

    def __init__(self, name: str) -> None:
        super().__init__("provider_not_configured", f"{name} OAuth is not configured")
        self.provider = name


class ProviderRegistry:
    """A small allow-list of ``name → factory`` OAuth providers."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], OAuthProvider]] = {}
        self._cache: dict[str, OAuthProvider] = {}

    def register(self, name: str, factory: Callable[[], OAuthProvider]) -> None:
        """Register (or override) a provider ``factory`` under ``name``."""
        self._factories[name] = factory
        self._cache.pop(name, None)

    def is_known(self, name: str) -> bool:
        """Whether ``name`` is on the allow-list (routable at all)."""
        return name in self._factories

    def names(self) -> tuple[str, ...]:
        """The registered provider names (the allow-list)."""
        return tuple(self._factories)

    def resolve(self, name: str) -> OAuthProvider:
        """Return the built provider for ``name``.

        Raises :class:`UnknownProvider` if it is not on the allow-list, or
        :class:`ProviderNotConfigured` if its factory reports missing config.
        Successful builds are cached (JWKS/HTTP client reuse).
        """
        if name not in self._factories:
            raise UnknownProvider(name)
        if name not in self._cache:
            try:
                self._cache[name] = self._factories[name]()
            except ProviderNotConfigured:
                raise
            except OAuthError:
                # A provider that guards its own config (e.g. GoogleOAuthProvider
                # raising on missing creds) normalizes to "not configured".
                raise ProviderNotConfigured(name)
        return self._cache[name]

    def reset(self) -> None:
        """Clear cached built instances (test helper / secret rotation)."""
        self._cache.clear()


def _google_factory() -> OAuthProvider:
    """Build the Google provider from settings; fail if it is not configured."""
    from app.auth.oauth.google import GoogleOAuthProvider
    from app.config import settings

    if not settings.google_oauth_configured or not settings.oauth_redirect_uri.strip():
        raise ProviderNotConfigured("google")
    return GoogleOAuthProvider(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        redirect_uri=settings.oauth_redirect_uri.strip(),
    )


# Process-wide allow-list. Google is registered now; a second provider is one
# more ``registry.register(...)`` call (no router/UI change).
registry = ProviderRegistry()
registry.register("google", _google_factory)
