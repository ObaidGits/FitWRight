"""Architecture fitness function: every profile boots or fails fast
(ARCHITECTURE §18 rule 8; IMPLEMENTATION_PLAN Phase 1/11 profile tests).

For each declared :class:`DeploymentProfile`, a correctly-provisioned deployment
validates clean, and an under-provisioned or self-contradictory one is rejected
with a precise error (never a silent degradation). Duck-typed over a settings
-like object so it is hermetic (no ``.env``, no DB, no network).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.platform.capabilities import startup_validation, validate_deployment
from app.platform.profiles import DeploymentProfile


def _settings(profile: DeploymentProfile, *, postgres: bool, single_user: bool):
    return SimpleNamespace(
        single_user_mode=single_user,
        deployment_profile=profile.value,
        effective_database_url=(
            "postgresql+asyncpg://h/db" if postgres else "sqlite:///local.db"
        ),
        kvstore_url="",
        storage_provider="local",
        cloudinary_configured=False,
        email_provider="",
        email_smtp_host="",
        email_from="",
        email_api_key="",
        google_oauth_configured=False,
        email_verification_enabled=False,
        scheduler_mode="external_cron",
        internal_job_token="",
    )


LOCAL_PROFILES = [
    DeploymentProfile.DESKTOP,
    DeploymentProfile.DEVELOPMENT,
    DeploymentProfile.TEST,
    DeploymentProfile.CI,
]
MULTI_USER_PROFILES = [
    DeploymentProfile.SAAS,
    DeploymentProfile.ENTERPRISE,
    DeploymentProfile.SELF_HOSTED,
]


@pytest.mark.parametrize("profile", LOCAL_PROFILES)
def test_local_profiles_boot_with_zero_config(profile):
    s = _settings(profile, postgres=False, single_user=True)
    assert validate_deployment(s) == []
    assert startup_validation(s) == []


@pytest.mark.parametrize("profile", MULTI_USER_PROFILES)
def test_multi_user_profiles_boot_when_provisioned(profile):
    s = _settings(profile, postgres=True, single_user=False)
    assert validate_deployment(s) == []
    assert startup_validation(s) == []


@pytest.mark.parametrize("profile", MULTI_USER_PROFILES)
def test_multi_user_profiles_fail_fast_without_postgres(profile):
    # validate_deployment (full) reports the missing capability; the Settings
    # constructor is the hard boot gate for this in a real deployment.
    s = _settings(profile, postgres=False, single_user=False)
    errors = validate_deployment(s)
    assert any("persistent_postgres" in e for e in errors)


@pytest.mark.parametrize("profile", MULTI_USER_PROFILES)
def test_profile_contradicting_single_user_fails_fast(profile):
    # A multi-user profile declared with SINGLE_USER_MODE=true is rejected at
    # startup (the check the Settings validator cannot make).
    s = _settings(profile, postgres=True, single_user=True)
    assert startup_validation(s), "expected a contradiction error"
