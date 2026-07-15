"""Startup provider-report tests (shape + secret-free guarantee)."""

from __future__ import annotations

from app import config as app_config
from app.diagnostics import build_startup_report


def test_report_has_expected_keys():
    report = build_startup_report(app_config.settings)
    for key in (
        "mode", "database", "kvstore", "email", "captcha",
        "breach_check", "storage", "scheduler",
    ):
        assert key in report


def test_local_sqlite_defaults(monkeypatch):
    monkeypatch.setattr(app_config.settings, "database_url", "")
    monkeypatch.setattr(app_config.settings, "single_user_mode", True)
    report = build_startup_report(app_config.settings)
    assert report["mode"] == "single_user"
    assert report["database"]["dialect"] == "sqlite"


def test_hosted_postgres_reports_ssl_and_pooler(monkeypatch):
    monkeypatch.setattr(app_config.settings, "single_user_mode", False)
    monkeypatch.setattr(
        app_config.settings, "database_url",
        "postgresql://u:secretpw@h:6543/db?sslmode=require",
    )
    monkeypatch.setattr(app_config.settings, "db_ssl", "")
    monkeypatch.setattr(app_config.settings, "db_use_pooler", True)
    report = build_startup_report(app_config.settings)
    assert report["database"]["dialect"] == "postgresql"
    assert report["database"]["ssl"] == "require"
    assert report["database"]["pooler"] is True


def test_report_contains_no_secrets(monkeypatch):
    """The report must never contain URLs, passwords, or keys."""
    monkeypatch.setattr(
        app_config.settings, "database_url",
        "postgresql://user:SUPERSECRET@dbhost:6543/db?sslmode=require",
    )
    monkeypatch.setattr(app_config.settings, "cloudinary_api_secret", "CLOUDSECRET")
    monkeypatch.setattr(app_config.settings, "email_api_key", "re_KEYSECRET")
    blob = repr(build_startup_report(app_config.settings))
    for secret in ("SUPERSECRET", "CLOUDSECRET", "re_KEYSECRET", "dbhost"):
        assert secret not in blob
