"""Unit tests for the auth configuration surface (Task 0.3).

Covers the fail-fast-in-hosted vs safe-ephemeral-in-local contract, the secret
rotation pair, the mode-derived email-verification default, OAuth all-or-nothing
validation, session-lifetime invariants, Argon2 bounds, and the ADR-13
Postgres-required-in-hosted rule.

Requirements: 14.3, 16.3, 17.2.
"""

import pytest
from pydantic import ValidationError

from app.config import _MIN_SECRET_LENGTH, Settings

pytestmark = pytest.mark.unit


def _hosted(**overrides) -> dict:
    """Baseline kwargs for a valid hosted (multi-user) configuration."""
    base = dict(
        single_user_mode=False,
        session_secret="x" * 32,
        ip_hash_secret="y" * 32,
        database_url="postgresql+asyncpg://user:pass@host:5432/db",
        # external_cron (the default) now requires a job token in hosted mode so
        # the scheduler endpoint is actually reachable; provide one for a valid base.
        internal_job_token="z" * 32,
        # Hermetic OAuth defaults: pydantic-settings would otherwise inherit the
        # developer's real .env (GOOGLE_CLIENT_ID/SECRET + a localhost http
        # redirect), making these hosted-mode config tests machine-dependent.
        google_client_id="",
        google_client_secret="",
        oauth_redirect_uri="",
    )
    base.update(overrides)
    return base


class TestLocalSafeDefaults:
    def test_boots_with_zero_config(self):
        # Local single-user mode must construct with no secrets provided.
        s = Settings(single_user_mode=True)
        assert s.single_user_mode is True

    def test_generates_ephemeral_secrets_when_blank(self):
        s = Settings(single_user_mode=True, session_secret="", ip_hash_secret="")
        assert len(s.session_secret) >= _MIN_SECRET_LENGTH
        assert len(s.ip_hash_secret) >= _MIN_SECRET_LENGTH
        # Session and ip-hash secrets are independent values.
        assert s.session_secret != s.ip_hash_secret

    def test_ephemeral_secret_is_stable_within_instance(self):
        s = Settings(single_user_mode=True)
        assert s.session_secret == s.session_secret

    def test_two_instances_get_distinct_ephemeral_secrets(self):
        a = Settings(single_user_mode=True)
        b = Settings(single_user_mode=True)
        assert a.session_secret != b.session_secret

    def test_email_verification_off_in_local(self):
        assert Settings(single_user_mode=True).email_verification_enabled is False

    def test_local_allows_sqlite_database(self):
        # No DATABASE_URL required locally.
        assert Settings(single_user_mode=True).effective_database_url.startswith("sqlite")


class TestHostedRequiredSecrets:
    def test_missing_session_secret_fails(self):
        with pytest.raises(ValidationError, match="SESSION_SECRET"):
            Settings(**_hosted(session_secret=""))

    def test_missing_ip_hash_secret_fails(self):
        with pytest.raises(ValidationError, match="IP_HASH_SECRET"):
            Settings(**_hosted(ip_hash_secret=""))

    def test_hosted_never_generates_ephemeral(self):
        # The error proves no ephemeral fallback happened in hosted mode.
        with pytest.raises(ValidationError):
            Settings(**_hosted(session_secret="", ip_hash_secret=""))

    def test_short_secret_rejected(self):
        with pytest.raises(ValidationError, match="at least"):
            Settings(**_hosted(session_secret="tooshort"))

    def test_valid_hosted_config(self):
        s = Settings(**_hosted())
        assert s.single_user_mode is False
        assert s.session_secret == "x" * 32
        assert s.email_verification_enabled is True

    def test_email_verification_override_in_hosted(self):
        assert Settings(**_hosted(email_verification=False)).email_verification_enabled is False


class TestSecretRotationPair:
    def test_prev_secret_accepted_alongside_current(self):
        s = Settings(**_hosted(session_secret="a" * 32, session_secret_prev="b" * 32))
        assert s.session_secret == "a" * 32
        assert s.session_secret_prev == "b" * 32

    def test_short_prev_secret_rejected(self):
        with pytest.raises(ValidationError, match="SESSION_SECRET_PREV"):
            Settings(**_hosted(session_secret_prev="short"))

    def test_prev_secret_optional(self):
        assert Settings(**_hosted()).session_secret_prev == ""


class TestGoogleOAuthValidation:
    def test_partial_pair_rejected(self):
        # Explicit kwargs (highest precedence) keep this hermetic regardless of
        # the developer's real .env, which may set google creds.
        with pytest.raises(ValidationError, match="together"):
            Settings(
                single_user_mode=True,
                google_client_id="id-only",
                google_client_secret="",
                _env_file=None,
            )

    def test_configured_property(self):
        s = Settings(
            single_user_mode=True,
            google_client_id="id",
            google_client_secret="secret",
        )
        assert s.google_oauth_configured is True

    def test_not_configured_by_default(self):
        s = Settings(
            single_user_mode=True,
            google_client_id="",
            google_client_secret="",
            _env_file=None,
        )
        assert s.google_oauth_configured is False

    def test_hosted_oauth_requires_redirect_uri(self):
        with pytest.raises(ValidationError, match="OAUTH_REDIRECT_URI"):
            Settings(**_hosted(google_client_id="id", google_client_secret="secret"))

    def test_hosted_oauth_redirect_must_be_https(self):
        with pytest.raises(ValidationError, match="https"):
            Settings(
                **_hosted(
                    google_client_id="id",
                    google_client_secret="secret",
                    oauth_redirect_uri="http://example.com/cb",
                )
            )

    def test_hosted_oauth_valid(self):
        s = Settings(
            **_hosted(
                google_client_id="id",
                google_client_secret="secret",
                oauth_redirect_uri="https://example.com/api/v1/auth/oauth/google/callback",
            )
        )
        assert s.google_oauth_configured is True

    def test_hosted_oauth_allows_loopback_http(self):
        # Google + RFC 8252 permit http on loopback for local development; the
        # traffic never leaves the machine, so hosted mode must accept it.
        for uri in (
            "http://localhost:3000/api/v1/auth/oauth/google/callback",
            "http://127.0.0.1:8000/api/v1/auth/oauth/google/callback",
        ):
            s = Settings(
                **_hosted(
                    google_client_id="id",
                    google_client_secret="secret",
                    oauth_redirect_uri=uri,
                )
            )
            assert s.google_oauth_configured is True

    def test_hosted_oauth_rejects_http_non_loopback(self):
        with pytest.raises(ValidationError, match="https"):
            Settings(
                **_hosted(
                    google_client_id="id",
                    google_client_secret="secret",
                    oauth_redirect_uri="http://myapp.example.com/api/v1/auth/oauth/google/callback",
                )
            )


class TestSessionLifetimes:
    def test_defaults_satisfy_ordering(self):
        s = Settings(single_user_mode=True)
        assert s.idle_ttl <= s.session_absolute_ttl <= s.remember_me_ttl

    def test_idle_greater_than_absolute_rejected(self):
        with pytest.raises(ValidationError, match="IDLE_TTL"):
            Settings(single_user_mode=True, idle_ttl=100000, session_absolute_ttl=3600)

    def test_remember_me_less_than_absolute_rejected(self):
        with pytest.raises(ValidationError, match="REMEMBER_ME_TTL"):
            Settings(single_user_mode=True, remember_me_ttl=3600, session_absolute_ttl=7200)

    def test_zero_ttl_rejected(self):
        with pytest.raises(ValidationError):
            Settings(single_user_mode=True, step_up_window=0)

    def test_blank_ttl_falls_back_to_default(self):
        s = Settings(single_user_mode=True, step_up_window="")
        assert s.step_up_window == Settings.model_fields["step_up_window"].default

    def test_garbage_ttl_rejected(self):
        with pytest.raises(ValidationError):
            Settings(single_user_mode=True, step_up_window="abc")


class TestArgon2Params:
    def test_defaults(self):
        s = Settings(single_user_mode=True)
        assert s.argon2_time_cost == 3
        assert s.argon2_memory_cost == 65536
        assert s.argon2_parallelism == 4

    def test_memory_below_parallelism_floor_rejected(self):
        with pytest.raises(ValidationError, match="ARGON2_MEMORY_COST"):
            Settings(single_user_mode=True, argon2_memory_cost=8, argon2_parallelism=4)

    def test_non_positive_rejected(self):
        with pytest.raises(ValidationError):
            Settings(single_user_mode=True, argon2_time_cost=0)


class TestHostedRequiresPostgres:
    def test_empty_database_url_rejected_in_hosted(self):
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings(**_hosted(database_url=""))

    def test_sqlite_database_url_rejected_in_hosted(self):
        with pytest.raises(ValidationError, match="DATABASE_URL"):
            Settings(**_hosted(database_url="sqlite+aiosqlite:///./local.db"))

    def test_postgres_database_url_accepted(self):
        s = Settings(**_hosted())
        assert s.effective_database_url.startswith("postgresql")


class TestOwnerEmail:
    def test_invalid_owner_email_rejected(self):
        with pytest.raises(ValidationError, match="OWNER_EMAIL"):
            Settings(single_user_mode=True, owner_email="not-an-email")

    def test_default_owner_email(self):
        assert Settings(single_user_mode=True).owner_email == "owner@localhost"


class TestHostedSchedulerAndKVStore:
    """Hosted-mode operational fail-fast (B1/B2): background jobs must be able
    to run, and the internal scheduler must have a shared lock store."""

    def test_external_cron_without_token_fails(self):
        with pytest.raises(ValidationError, match="INTERNAL_JOB_TOKEN"):
            Settings(**_hosted(internal_job_token="", scheduler_mode="external_cron"))

    def test_external_cron_with_token_ok(self):
        s = Settings(**_hosted(scheduler_mode="external_cron", internal_job_token="z" * 32))
        assert s.scheduler_mode == "external_cron"

    def test_internal_scheduler_with_local_kvstore_fails(self):
        with pytest.raises(ValidationError, match="shared KVSTORE_URL"):
            Settings(**_hosted(scheduler_mode="internal", kvstore_url=""))

    def test_internal_scheduler_with_redis_ok(self):
        s = Settings(
            **_hosted(scheduler_mode="internal", kvstore_url="rediss://h:6379/0")
        )
        assert s.scheduler_mode == "internal"

    def test_local_single_user_never_requires_token(self):
        # The whole point: zero-config local dev is unaffected by hosted rules.
        s = Settings(single_user_mode=True)
        assert s.internal_job_token == ""
