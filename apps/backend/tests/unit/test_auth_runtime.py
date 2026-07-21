"""Unit tests for auth runtime wiring (Task 0.3).

Covers construction of the KVStore adapter from ``KVSTORE_URL`` and the
Email/Captcha/Breach adapters from provider config, plus the process-singleton
dependency callables.

Requirements: 14.3, 16.3, 17.2 (construction from config).
"""

import pytest

import app.auth.runtime as runtime
from app.auth import (
    AllowAllCaptchaVerifier,
    DBKVStore,
    HibpBreachedPasswordCheck,
    LocalKVStore,
    LoggingEmailSender,
    NoopBreachedPasswordCheck,
    RedisKVStore,
    ResendEmailSender,
    SmtpEmailSender,
    TurnstileCaptchaVerifier,
)
from app.config import Settings
from app.database import Database

pytestmark = pytest.mark.unit


def _local(**overrides) -> Settings:
    # Hermetic provider defaults: pydantic-settings otherwise inherits the
    # developer's real ``.env`` (which may set EMAIL_SMTP_HOST / EMAIL_FROM /
    # EMAIL_API_KEY), which would make the "missing creds degrades to logging"
    # tests machine-dependent (they pass in CI's clean env but fail locally).
    # Clearing the provider creds here makes those tests deterministic; the
    # "with creds" tests still win via explicit ``overrides``. Mirrors the
    # hermetic-defaults pattern in ``tests/unit/test_auth_config.py``.
    hermetic = {
        "email_smtp_host": "",
        "email_smtp_password": "",
        "email_api_key": "",
        "email_from": "",
    }
    return Settings(single_user_mode=True, **{**hermetic, **overrides})


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset the composition-root cache (adapters are owned there - Phase 3)."""
    from app.platform import reset_container

    reset_container()
    yield
    reset_container()


class TestBuildKVStore:
    def test_blank_url_builds_local(self):
        store = runtime.build_kvstore(_local(kvstore_url=""))
        assert isinstance(store, LocalKVStore)

    def test_redis_url_builds_redis_without_connecting(self):
        # from_url is lazy: no network I/O until first command.
        store = runtime.build_kvstore(_local(kvstore_url="redis://localhost:6379/0"))
        assert isinstance(store, RedisKVStore)

    async def test_db_url_builds_db_store_with_shared_engine(self, tmp_path, monkeypatch):
        test_db = Database(db_path=tmp_path / "runtime_kv.db")
        monkeypatch.setattr("app.database.db", test_db)
        try:
            store = runtime.build_kvstore(_local(kvstore_url="db"))
            assert isinstance(store, DBKVStore)
            # The store is usable against the shared engine.
            await store.set("k", "v")
            assert await store.get("k") == "v"
        finally:
            await test_db.close()


class TestBuildProviders:
    def test_email_default_is_logging_sender(self):
        assert isinstance(runtime.build_email_sender(_local()), LoggingEmailSender)

    def test_captcha_default_is_allow_all(self):
        assert isinstance(runtime.build_captcha_verifier(_local()), AllowAllCaptchaVerifier)

    def test_breach_default_is_noop(self):
        assert isinstance(
            runtime.build_breached_password_check(_local()), NoopBreachedPasswordCheck
        )

    # --- Real provider construction (completion-pass provider contract) -----
    #
    # The provider factories previously RAISED for any configured provider
    # (no adapter yet). They now build the real adapter when creds are present
    # and gracefully degrade to the dev-safe default (never raise) when a
    # provider is misconfigured or unrecognized, so a hosted operator setting
    # EMAIL_PROVIDER=resend / CAPTCHA_PROVIDER=turnstile / BREACH_PROVIDER=hibp
    # can never crash construction or stop the app booting.

    def test_smtp_provider_with_creds_builds_smtp_sender(self):
        sender = runtime.build_email_sender(
            _local(email_provider="smtp", email_smtp_host="smtp.example.com", email_from="a@b.co")
        )
        assert isinstance(sender, SmtpEmailSender)

    def test_resend_provider_with_creds_builds_resend_sender(self):
        sender = runtime.build_email_sender(
            _local(email_provider="resend", email_api_key="re_key", email_from="a@b.co")
        )
        assert isinstance(sender, ResendEmailSender)

    def test_smtp_provider_missing_creds_degrades_to_logging(self, caplog):
        with caplog.at_level("WARNING"):
            sender = runtime.build_email_sender(_local(email_provider="smtp"))
        assert isinstance(sender, LoggingEmailSender)
        assert "EMAIL_SMTP_HOST" in caplog.text

    def test_resend_provider_missing_creds_degrades_to_logging(self, caplog):
        with caplog.at_level("WARNING"):
            sender = runtime.build_email_sender(_local(email_provider="resend"))
        assert isinstance(sender, LoggingEmailSender)
        assert "EMAIL_API_KEY" in caplog.text

    def test_unknown_email_provider_degrades_to_logging(self, caplog):
        with caplog.at_level("WARNING"):
            sender = runtime.build_email_sender(_local(email_provider="mailgun"))
        assert isinstance(sender, LoggingEmailSender)
        assert "not a recognized email provider" in caplog.text

    def test_turnstile_provider_with_secret_builds_verifier(self):
        verifier = runtime.build_captcha_verifier(
            _local(captcha_provider="turnstile", captcha_secret="secret")
        )
        assert isinstance(verifier, TurnstileCaptchaVerifier)

    def test_turnstile_provider_missing_secret_degrades_to_allow_all(self, caplog):
        with caplog.at_level("WARNING"):
            verifier = runtime.build_captcha_verifier(_local(captcha_provider="turnstile"))
        assert isinstance(verifier, AllowAllCaptchaVerifier)
        assert "CAPTCHA_SECRET" in caplog.text

    def test_unknown_captcha_provider_degrades_to_allow_all(self, caplog):
        with caplog.at_level("WARNING"):
            verifier = runtime.build_captcha_verifier(_local(captcha_provider="hcaptcha"))
        assert isinstance(verifier, AllowAllCaptchaVerifier)
        assert "not a recognized CAPTCHA provider" in caplog.text

    def test_hibp_provider_builds_real_check(self):
        check = runtime.build_breached_password_check(_local(breach_provider="hibp"))
        assert isinstance(check, HibpBreachedPasswordCheck)

    def test_unknown_breach_provider_degrades_to_noop(self, caplog):
        with caplog.at_level("WARNING"):
            check = runtime.build_breached_password_check(_local(breach_provider="dehashed"))
        assert isinstance(check, NoopBreachedPasswordCheck)
        assert "not a recognized breach provider" in caplog.text

    def test_disabled_aliases_select_defaults(self):
        assert isinstance(
            runtime.build_captcha_verifier(_local(captcha_provider="disabled")),
            AllowAllCaptchaVerifier,
        )
        assert isinstance(
            runtime.build_breached_password_check(_local(breach_provider="none")),
            NoopBreachedPasswordCheck,
        )


class TestSingletons:
    def test_email_sender_singleton_is_cached(self):
        first = runtime.get_email_sender()
        second = runtime.get_email_sender()
        assert first is second

    def test_captcha_singleton_is_cached(self):
        assert runtime.get_captcha_verifier() is runtime.get_captcha_verifier()

    def test_breach_singleton_is_cached(self):
        assert runtime.get_breached_password_check() is runtime.get_breached_password_check()

    def test_kvstore_singleton_is_cached(self):
        # Default settings -> local store, no engine needed.
        assert runtime.get_kvstore() is runtime.get_kvstore()

    async def test_close_kvstore_releases_local_singleton(self):
        store = runtime.get_kvstore()
        assert isinstance(store, LocalKVStore)
        await runtime.close_kvstore()
        # After close the container rebuilds a fresh instance on next access.
        assert runtime.get_kvstore() is not store
