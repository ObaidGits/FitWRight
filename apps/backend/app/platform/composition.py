"""Composition root (ARCHITECTURE §2, §10; IMPLEMENTATION_PLAN Phase 3).

The single, documented place the application assembles its infrastructure
adapters/services. New code obtains infrastructure from the :class:`Container`
rather than importing scattered ``get_*()`` service-locators directly, so there
is one seam to reason about and one place future wiring changes land.

Migration stance (Phase 3 is *additive first*): during this step the container
**delegates** to the existing builders/service-locators (which carry a test
contract on their module globals - e.g. ``app.auth.runtime._kvstore``). This
establishes the seam with zero behavior change. A later, separately-gated step
inverts the cache so the ``get_*()`` functions delegate to the container and
construction logic lives only here (the plan's Phase 3 exit criteria).

Rules:
- Lazy imports inside methods keep ``platform`` free of heavy/cyclic imports at
  module load and keep the domain unable to pull infrastructure transitively.
- The container is a process singleton; ``reset_container()`` is a test hook.
- The domain (``app.services``) must never import this module (fitness §18).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class Container:
    """Single construction site for infrastructure adapters + the identity provider.

    Owns the process-wide instances of the pluggable adapters (KVStore, mailer,
    captcha, breach check, storage) - built once via the pure ``build_*``
    functions and cached here - plus profile/capability resolution and the
    profile-selected identity provider. Composed higher-level services (session,
    rate-limit, audit, token, password) live in their own modules and are reached
    through their own accessors; the container deliberately does not re-expose
    them (Complexity Budget - no unused indirection).
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        # The single instantiation cache for infrastructure adapters. Built on
        # first use via the pure ``build_*`` functions (Phase 3 exit: adapters
        # are constructed only here, never scattered across call sites).
        self._instances: dict[str, Any] = {}

    # -- deployment context -------------------------------------------------

    def profile(self):
        """The resolved :class:`~app.platform.profiles.DeploymentProfile`.

        Used to select the identity provider; profile/capability *reporting* is
        done by ``app.platform.capabilities`` directly (diagnostics), so the
        container intentionally exposes only what it needs.
        """
        return self._settings.resolved_profile

    # -- infrastructure ports (constructed + cached here - the single site) --

    def _get_or_build(self, name: str, builder):
        """Return the cached adapter for ``name`` or build+cache it via ``builder``."""
        instance = self._instances.get(name)
        if instance is None:
            instance = builder(self._settings)
            self._instances[name] = instance
        return instance

    def kvstore(self):
        from app.auth.runtime import build_kvstore

        return self._get_or_build("kvstore", build_kvstore)

    def email_sender(self):
        from app.auth.runtime import build_email_sender

        return self._get_or_build("email_sender", build_email_sender)

    def captcha_verifier(self):
        from app.auth.runtime import build_captcha_verifier

        return self._get_or_build("captcha_verifier", build_captcha_verifier)

    def breached_password_check(self):
        from app.auth.runtime import build_breached_password_check

        return self._get_or_build("breached_password_check", build_breached_password_check)

    def storage(self):
        from app.storage.provider import build_storage_provider

        return self._get_or_build("storage", build_storage_provider)

    def override(self, name: str, instance: Any) -> None:
        """Test hook: force a specific adapter instance (e.g. a fault-injecting fake)."""
        self._instances[name] = instance

    async def aclose(self) -> None:
        """Release adapter resources on shutdown (currently the KVStore).

        The DB-backed KVStore shares the app's async engine (disposed by the
        database layer), so only a non-DB store is closed here.
        """
        store = self._instances.get("kvstore")
        if store is None:
            return
        from app.auth.kvstore import url_needs_db_engine

        if not url_needs_db_engine(self._settings.kvstore_url):
            await store.close()
        self._instances.pop("kvstore", None)

    def identity_provider(self):
        """Select the :class:`~app.auth.identity.IdentityProvider` by profile.

        Local profiles get the owner-fallback provider; multi-user profiles get
        the session provider (no implicit owner -> anonymous requests 401). This
        is the single place the deployment axis decides identity behavior
        (ARCHITECTURE §9/§18.5); consumers just call ``resolve_owner_fallback``.

        Selection is resolved **live** from the (mutable) settings on every call
        rather than cached, so it always reflects the current profile - the
        container is a process singleton and the former branch read the mode
        live too. The adapters are stateless and trivially cheap to construct.
        """
        from app.auth.identity import OwnerIdentityProvider, SessionIdentityProvider

        return OwnerIdentityProvider() if self.profile().is_local else SessionIdentityProvider()

    # -- startup warmup -----------------------------------------------------

    def warmup(self) -> dict[str, str]:
        """Eagerly construct the *pure* (no-I/O) adapters at boot (fail-fast).

        Only adapters whose construction performs no I/O are warmed here
        (email/captcha/breach). This surfaces a misconfiguration that yields a
        *construction* error at startup rather than on the first request.
        KVStore/Storage are intentionally left lazy - their construction may
        touch the DB engine or network, which belong to their own init paths.
        Returns a secret-free summary of the resolved adapter class names.
        """
        return {
            "email_sender": type(self.email_sender()).__name__,
            "captcha_verifier": type(self.captcha_verifier()).__name__,
            "breached_password_check": type(self.breached_password_check()).__name__,
        }


_container: Container | None = None


def get_container() -> Container:
    """Return the process-wide composition :class:`Container` (built on first use)."""
    global _container
    if _container is None:
        from app.config import settings

        _container = Container(settings)
    return _container


def reset_container() -> None:
    """Drop the cached container (test hook; next ``get_container`` rebuilds it)."""
    global _container
    _container = None
