"""Unit tests for deployment profiles (IMPLEMENTATION_PLAN Phase 1).

Pure-function tests. Profile logic is duck-typed over a settings-like object, so
these use a lightweight fake (no ``.env``, no framework) plus one real-``Settings``
smoke test for the derived (backward-compatible) path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.platform.profiles import (
    DeploymentProfile,
    is_consistent_with_mode,
    resolve_profile,
)


def fake(**overrides) -> SimpleNamespace:
    base = {"single_user_mode": True, "deployment_profile": ""}
    base.update(overrides)
    return SimpleNamespace(**base)


class TestResolveProfile:
    def test_derives_desktop_from_single_user(self):
        assert resolve_profile(fake(single_user_mode=True)) is DeploymentProfile.DESKTOP

    def test_derives_saas_from_multi_user(self):
        assert resolve_profile(fake(single_user_mode=False)) is DeploymentProfile.SAAS

    def test_explicit_profile_wins_over_derivation(self):
        s = fake(single_user_mode=False, deployment_profile="enterprise")
        assert resolve_profile(s) is DeploymentProfile.ENTERPRISE

    def test_explicit_profile_is_case_and_dash_insensitive(self):
        assert resolve_profile(fake(deployment_profile="SELF-HOSTED")) is (
            DeploymentProfile.SELF_HOSTED
        )

    def test_invalid_explicit_profile_falls_back_to_derivation(self):
        # A bogus value must not crash — it falls back to the derived profile.
        assert resolve_profile(fake(single_user_mode=True, deployment_profile="nonsense")) is (
            DeploymentProfile.DESKTOP
        )

    def test_missing_deployment_profile_attr_is_tolerated(self):
        s = SimpleNamespace(single_user_mode=True)  # no deployment_profile attr
        assert resolve_profile(s) is DeploymentProfile.DESKTOP


class TestProfileProperties:
    @pytest.mark.parametrize(
        "profile,multi",
        [
            (DeploymentProfile.DESKTOP, False),
            (DeploymentProfile.DEVELOPMENT, False),
            (DeploymentProfile.TEST, False),
            (DeploymentProfile.CI, False),
            (DeploymentProfile.SAAS, True),
            (DeploymentProfile.ENTERPRISE, True),
            (DeploymentProfile.SELF_HOSTED, True),
        ],
    )
    def test_is_multi_user(self, profile, multi):
        assert profile.is_multi_user is multi
        assert profile.is_local is (not multi)


class TestConsistency:
    def test_local_profile_consistent_with_single_user(self):
        assert is_consistent_with_mode(DeploymentProfile.DESKTOP, single_user_mode=True)

    def test_multi_user_profile_consistent_with_hosted(self):
        assert is_consistent_with_mode(DeploymentProfile.SAAS, single_user_mode=False)

    def test_saas_with_single_user_is_inconsistent(self):
        assert not is_consistent_with_mode(DeploymentProfile.SAAS, single_user_mode=True)

    def test_desktop_with_hosted_is_inconsistent(self):
        assert not is_consistent_with_mode(DeploymentProfile.DESKTOP, single_user_mode=False)


class TestRealSettingsSmoke:
    def test_real_settings_desktop_resolves(self):
        # Real Settings, forced single-user + local DB (init kwargs override .env).
        from app.config import Settings

        s = Settings(single_user_mode=True, deployment_profile="", database_url="")
        assert s.resolved_profile is DeploymentProfile.DESKTOP
