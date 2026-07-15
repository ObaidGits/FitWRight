"""Unit tests for the composition root (IMPLEMENTATION_PLAN Phase 3).

Verifies the container is the single access seam that returns the same
process-wide instances as the underlying builders (delegation identity), warms
the pure adapters, and exposes deployment context.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.auth.runtime as runtime
from app.platform import DeploymentProfile, get_container, reset_container
from app.platform.composition import Container


@pytest.fixture(autouse=True)
def _reset():
    """Reset the composition-root cache around each test (Phase 3 owner of adapters)."""
    reset_container()
    yield
    reset_container()


class TestContainerSingleton:
    def test_get_container_is_singleton(self):
        assert get_container() is get_container()

    def test_reset_container_rebuilds(self):
        first = get_container()
        reset_container()
        assert get_container() is not first


class TestDelegationIdentity:
    def test_kvstore_delegates_to_runtime(self):
        assert get_container().kvstore() is runtime.get_kvstore()

    def test_pure_adapters_delegate_to_runtime(self):
        c = get_container()
        assert c.email_sender() is runtime.get_email_sender()
        assert c.captcha_verifier() is runtime.get_captcha_verifier()
        assert c.breached_password_check() is runtime.get_breached_password_check()

    def test_storage_delegates(self):
        from app.storage.provider import get_storage_provider

        assert get_container().storage() is get_storage_provider()


class TestWarmup:
    def test_warmup_reports_pure_adapter_types(self):
        summary = get_container().warmup()
        assert set(summary) == {"email_sender", "captcha_verifier", "breached_password_check"}
        # The reported class names match the actually-resolved instances.
        c = get_container()
        assert summary["email_sender"] == type(c.email_sender()).__name__
        assert summary["captcha_verifier"] == type(c.captcha_verifier()).__name__
        assert summary["breached_password_check"] == type(c.breached_password_check()).__name__


class TestDeploymentContext:
    def test_profile_is_a_deployment_profile(self):
        assert isinstance(get_container().profile(), DeploymentProfile)

    def test_profile_reflects_container_settings(self):
        # profile() drives identity-provider selection; it must read the
        # container's (live) settings, not a captured/stale value.
        fake = SimpleNamespace(resolved_profile=DeploymentProfile.DESKTOP)
        assert Container(fake).profile() is DeploymentProfile.DESKTOP
