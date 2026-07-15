"""Unit tests for the capability model (IMPLEMENTATION_PLAN Phase 1).

Capability detection/validation is duck-typed over a settings-like object, so
these use a hermetic fake with sane single-user defaults.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.platform.capabilities import (
    Capability,
    capability_report,
    detect_capabilities,
    required_capabilities,
    validate_deployment,
)
from app.platform.profiles import DeploymentProfile


def fake(**overrides) -> SimpleNamespace:
    """Settings-like object; defaults describe a zero-config desktop deployment."""
    base = dict(
        single_user_mode=True,
        deployment_profile="",
        effective_database_url="sqlite:///local.db",
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
    base.update(overrides)
    return SimpleNamespace(**base)


class TestDetectCapabilities:
    def test_desktop_defaults_detect_nothing_external(self):
        assert detect_capabilities(fake()) == set()

    def test_multi_user_detected(self):
        assert Capability.MULTI_USER in detect_capabilities(fake(single_user_mode=False))

    def test_postgres_detected(self):
        caps = detect_capabilities(fake(effective_database_url="postgresql+asyncpg://h/db"))
        assert Capability.PERSISTENT_POSTGRES in caps

    def test_postgres_bare_scheme_detected(self):
        caps = detect_capabilities(fake(effective_database_url="postgres://h/db"))
        assert Capability.PERSISTENT_POSTGRES in caps

    def test_shared_cache_implies_shared_rate_limiting(self):
        caps = detect_capabilities(fake(kvstore_url="rediss://user:pass@host:6379"))
        assert Capability.SHARED_CACHE in caps
        assert Capability.RATE_LIMITING_SHARED in caps

    def test_local_kv_is_not_shared(self):
        caps = detect_capabilities(fake(kvstore_url="db"))
        assert Capability.SHARED_CACHE not in caps

    def test_object_storage_requires_configured_cloudinary(self):
        assert Capability.OBJECT_STORAGE not in detect_capabilities(
            fake(storage_provider="cloudinary", cloudinary_configured=False)
        )
        assert Capability.OBJECT_STORAGE in detect_capabilities(
            fake(storage_provider="cloudinary", cloudinary_configured=True)
        )

    def test_smtp_email_needs_host_and_from(self):
        assert Capability.OUTBOUND_EMAIL not in detect_capabilities(
            fake(email_provider="smtp", email_smtp_host="", email_from="")
        )
        assert Capability.OUTBOUND_EMAIL in detect_capabilities(
            fake(email_provider="smtp", email_smtp_host="smtp.x.com", email_from="a@x.com")
        )

    def test_api_email_needs_key(self):
        assert Capability.OUTBOUND_EMAIL in detect_capabilities(
            fake(email_provider="resend", email_api_key="re_x")
        )

    def test_oauth_and_verification_and_scheduler(self):
        caps = detect_capabilities(
            fake(
                google_oauth_configured=True,
                email_verification_enabled=True,
                scheduler_mode="external_cron",
                internal_job_token="x" * 16,
            )
        )
        assert Capability.OAUTH_LOGIN in caps
        assert Capability.EMAIL_VERIFICATION in caps
        assert Capability.EXTERNAL_SCHEDULER in caps

    def test_external_scheduler_needs_token(self):
        caps = detect_capabilities(fake(scheduler_mode="external_cron", internal_job_token=""))
        assert Capability.EXTERNAL_SCHEDULER not in caps


class TestRequiredCapabilities:
    def test_local_profiles_require_nothing(self):
        for p in (
            DeploymentProfile.DESKTOP,
            DeploymentProfile.DEVELOPMENT,
            DeploymentProfile.TEST,
            DeploymentProfile.CI,
        ):
            assert required_capabilities(p) == set()

    def test_multi_user_profiles_require_postgres_and_multi_user(self):
        for p in (
            DeploymentProfile.SAAS,
            DeploymentProfile.ENTERPRISE,
            DeploymentProfile.SELF_HOSTED,
        ):
            assert required_capabilities(p) == {
                Capability.MULTI_USER,
                Capability.PERSISTENT_POSTGRES,
            }


class TestValidateDeployment:
    def test_desktop_is_valid_with_zero_config(self):
        assert validate_deployment(fake()) == []

    def test_saas_valid_with_postgres(self):
        s = fake(single_user_mode=False, effective_database_url="postgresql+asyncpg://h/db")
        assert validate_deployment(s) == []

    def test_saas_missing_postgres_reports_error(self):
        s = fake(single_user_mode=False, effective_database_url="sqlite:///x")
        errors = validate_deployment(s)
        assert any("persistent_postgres" in e for e in errors)

    def test_explicit_profile_contradicting_mode_is_error(self):
        # saas profile declared, but single-user mode → contradiction.
        s = fake(single_user_mode=True, deployment_profile="saas")
        errors = validate_deployment(s)
        assert any("contradicts" in e for e in errors)

    def test_contradiction_and_missing_capability_both_reported(self):
        s = fake(
            single_user_mode=True,
            deployment_profile="saas",
            effective_database_url="sqlite:///x",
        )
        errors = validate_deployment(s)
        assert len(errors) >= 2


class TestStartupValidation:
    """The narrow fail-fast gate used by main.py lifespan (contradiction only)."""

    def test_desktop_startup_ok(self):
        from app.platform.capabilities import startup_validation

        assert startup_validation(fake()) == []

    def test_hosted_on_sqlite_does_NOT_fail_startup(self):
        # Missing-Postgres is owned by Settings at construction, not re-enforced
        # here (would misfire for post-construction-patched settings/tests).
        from app.platform.capabilities import startup_validation

        s = fake(single_user_mode=False, effective_database_url="sqlite:///x")
        assert startup_validation(s) == []

    def test_contradiction_fails_startup(self):
        from app.platform.capabilities import startup_validation

        s = fake(single_user_mode=True, deployment_profile="saas")
        errors = startup_validation(s)
        assert any("contradicts" in e for e in errors)

    def test_invalid_explicit_profile_fails_startup(self):
        # Fail-fast, never silently fall back to the derived profile (Golden Rule 6).
        from app.platform.capabilities import startup_validation

        errors = startup_validation(fake(deployment_profile="prod-typo"))
        assert any("not a valid profile" in e for e in errors)

    def test_valid_explicit_profile_passes_startup(self):
        from app.platform.capabilities import startup_validation

        assert startup_validation(fake(single_user_mode=True, deployment_profile="desktop")) == []


class TestCapabilityReport:
    def test_report_shape_desktop(self):
        r = capability_report(fake())
        assert r["profile"] == "desktop"
        assert r["multi_user"] is False
        assert r["required"] == []
        assert r["missing"] == []
        assert r["valid"] is True

    def test_report_flags_missing_for_bad_saas(self):
        r = capability_report(
            fake(single_user_mode=False, effective_database_url="sqlite:///x")
        )
        assert r["profile"] == "saas"
        assert "persistent_postgres" in r["missing"]
        assert r["valid"] is False
