"""Identity provider port (ARCHITECTURE §9 identity, §14; IMPLEMENTATION_PLAN Phase 5).

The one genuinely behavioral deployment fork is *who the caller is when the
request carries no authenticated principal*:

- **Hosted / multi-user:** nobody. An anonymous request has no owned data, so it
  must 401. -> :class:`SessionIdentityProvider` (owner fallback is ``None``).
- **Local / single-user:** the implicit bootstrap owner, lazily ensured. ->
  :class:`OwnerIdentityProvider`.

Consolidating this behind a port means ``get_effective_user_id`` (and the health
owner-resolve path) stop reading the deployment-mode boolean directly; they
consume the provider the composition root selects by profile. The adapters
themselves are dumb - they do NOT read the deployment mode - so the deployment
axis stays in the composition seam (ARCHITECTURE §18.5).
"""

from __future__ import annotations

import abc

__all__ = [
    "IdentityProvider",
    "SessionIdentityProvider",
    "OwnerIdentityProvider",
]


class IdentityProvider(abc.ABC):
    """How to resolve the effective owner when no principal is on the request."""

    @abc.abstractmethod
    async def resolve_owner_fallback(self) -> str | None:
        """Return the fallback owner ``user_id`` for a principal-less request.

        ``None`` means "there is no implicit owner" -> the caller must 401.
        """


class SessionIdentityProvider(IdentityProvider):
    """Hosted/multi-user: no implicit owner - anonymous requests are unauthenticated."""

    async def resolve_owner_fallback(self) -> str | None:
        return None


class OwnerIdentityProvider(IdentityProvider):
    """Local/single-user: the bootstrap owner, lazily ensured (created + backfilled).

    Delegates to :func:`app.auth.owner.ensure_owner` so behavior is identical to
    the pre-migration single-user owner-fallback branch.
    """

    async def resolve_owner_fallback(self) -> str | None:
        from app.auth.owner import ensure_owner

        return await ensure_owner()
