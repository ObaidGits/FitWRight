"""Unit tests for the IdentityProvider port (IMPLEMENTATION_PLAN Phase 5).

Verifies the two adapters' behavior and that the composition root selects the
correct adapter per profile — the behavior-preserving replacement for the old
``if settings.single_user_mode`` owner-fallback branch.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.auth.identity import (
    IdentityProvider,
    OwnerIdentityProvider,
    SessionIdentityProvider,
)
from app.platform import DeploymentProfile
from app.platform.composition import Container


class TestAdapters:
    async def test_session_provider_has_no_owner_fallback(self):
        assert await SessionIdentityProvider().resolve_owner_fallback() is None

    async def test_owner_provider_delegates_to_ensure_owner(self):
        with patch("app.auth.owner.ensure_owner", new=AsyncMock(return_value="owner-123")):
            assert await OwnerIdentityProvider().resolve_owner_fallback() == "owner-123"

    def test_adapters_are_identity_providers(self):
        assert isinstance(SessionIdentityProvider(), IdentityProvider)
        assert isinstance(OwnerIdentityProvider(), IdentityProvider)


def _fake(profile_value: str, is_local: bool):
    return SimpleNamespace(
        resolved_profile=SimpleNamespace(is_local=is_local, value=profile_value)
    )


class TestContainerSelection:
    def test_local_profile_selects_owner_provider(self):
        c = Container(_fake("desktop", is_local=True))
        assert isinstance(c.identity_provider(), OwnerIdentityProvider)

    def test_multi_user_profile_selects_session_provider(self):
        c = Container(_fake("saas", is_local=False))
        assert isinstance(c.identity_provider(), SessionIdentityProvider)

    def test_identity_provider_resolves_live_when_profile_changes(self):
        # Not cached: flipping the profile flips the selected adapter (this is
        # what makes it correct under a process-singleton container + runtime
        # mode changes — the regression that broke the hosted 401 tests).
        settings_like = _fake("desktop", is_local=True)
        c = Container(settings_like)
        assert isinstance(c.identity_provider(), OwnerIdentityProvider)
        settings_like.resolved_profile = SimpleNamespace(is_local=False, value="saas")
        assert isinstance(c.identity_provider(), SessionIdentityProvider)


class TestRealProfileSmoke:
    def test_saas_real_profile_maps_local_flag(self):
        # DeploymentProfile.is_local drives selection; sanity-check the mapping.
        assert DeploymentProfile.DESKTOP.is_local is True
        assert DeploymentProfile.SAAS.is_local is False
