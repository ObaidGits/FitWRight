"""Startup config-error banner (app.config._format_settings_error).

Reproduces the exact failure the user hit: SINGLE_USER_MODE=false with a
SQLite DATABASE_URL raises pydantic's ValidationError, which by default prints
as a 10-line traceback (repr'd input dict, `[type=value_error, ...]`, a docs
URL) with the real reason - the one our own model_validator wrote - buried in
the middle. These tests pin that the banner keeps only that reason.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, _format_settings_error


def _settings_kwargs(**overrides):
    base = dict(
        single_user_mode=False,
        database_url="sqlite+aiosqlite:///./data/app.db",
        session_secret="a" * 40,
        ip_hash_secret="b" * 40,
        app_encryption_key="c" * 40,
        internal_job_token="d" * 20,
        google_client_id="",
        google_client_secret="",
        oauth_redirect_uri="",
        _env_file=None,
    )
    base.update(overrides)
    return base


class TestFormatSettingsError:
    def test_reproduces_the_users_exact_failure(self):
        with pytest.raises(ValidationError) as excinfo:
            Settings(**_settings_kwargs())

        banner = _format_settings_error(excinfo.value)

        # The real reason survives, verbatim.
        assert "DATABASE_URL must be a Postgres URL when SINGLE_USER_MODE is off" in banner
        assert "ADR-13" in banner
        # Pydantic's own wrapper noise is gone.
        assert "Value error," not in banner
        assert "type=value_error" not in banner
        assert "errors.pydantic.dev" not in banner
        # The bare "Invalid auth configuration:" label is a header, not a
        # reason - it must not become a bullet of its own.
        assert "  - Invalid auth configuration:" not in banner
        # The horizontal rule must actually be one line of 70 "=", not 70
        # separate "\n=" repetitions (an operator-precedence bug this pins).
        assert "=" * 70 in banner
        assert "\n=\n" not in banner
        # A concrete instruction, not just a restated error.
        assert ".env" in banner

    def test_multiple_errors_all_appear_as_separate_bullets(self):
        with pytest.raises(ValidationError) as excinfo:
            Settings(**_settings_kwargs(
                database_url="sqlite+aiosqlite:///./data/app.db",
                session_secret="short",
            ))

        banner = _format_settings_error(excinfo.value)

        assert "DATABASE_URL must be a Postgres URL" in banner
        assert "SESSION_SECRET must be at least" in banner
        # Each distinct problem is its own bullet, not concatenated into one line.
        assert banner.count("  - ") >= 2

    def test_banner_has_no_python_traceback_markers(self):
        with pytest.raises(ValidationError) as excinfo:
            Settings(**_settings_kwargs())

        banner = _format_settings_error(excinfo.value)

        assert "Traceback" not in banner
        assert "pydantic_core" not in banner
        assert "site-packages" not in banner

    def test_valid_hosted_config_does_not_raise(self):
        # Sanity check the fixture itself is otherwise valid, isolating that
        # failures above come from the deliberate violation, not the fixture.
        settings = Settings(**_settings_kwargs(
            database_url="postgresql+asyncpg://user:pass@host:5432/db",
        ))
        assert settings.single_user_mode is False
