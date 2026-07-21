"""Unit tests for Postgres TLS/sslmode resolution (app.db_engine).

These guard the Supabase-critical behavior that asyncpg rejects a raw
``sslmode`` kwarg: the URL's libpq SSL params must be stripped and translated
into an asyncpg ``ssl`` connect arg / psycopg ``sslmode`` string, with a secure
hosted default. Pure-function tests - no database or network.
"""

from __future__ import annotations

import ssl as _ssl

import pytest

from app import config as app_config
from app.db_engine import _asyncpg_ssl_arg, _extract_pg_ssl_mode


@pytest.fixture
def local_mode(monkeypatch):
    """single-user + no explicit DB_SSL (the local default)."""
    monkeypatch.setattr(app_config.settings, "single_user_mode", True)
    monkeypatch.setattr(app_config.settings, "db_ssl", "")


@pytest.fixture
def hosted_mode(monkeypatch):
    monkeypatch.setattr(app_config.settings, "single_user_mode", False)
    monkeypatch.setattr(app_config.settings, "db_ssl", "")


def test_sslmode_is_stripped_from_url(local_mode):
    clean, mode = _extract_pg_ssl_mode(
        "postgresql+asyncpg://u:p@h:5432/db?sslmode=require"
    )
    assert "sslmode" not in clean
    assert clean == "postgresql+asyncpg://u:p@h:5432/db"
    assert mode == "require"


def test_ssl_true_param_maps_to_require(local_mode):
    _clean, mode = _extract_pg_ssl_mode("postgresql://u:p@h/db?ssl=true")
    assert mode == "require"


def test_local_without_sslmode_is_none(local_mode):
    clean, mode = _extract_pg_ssl_mode("postgresql://u:p@h/db")
    assert mode is None
    assert clean == "postgresql://u:p@h/db"


def test_hosted_default_requires_ssl(hosted_mode):
    """Hosted mode with no explicit sslmode must default to require (secure)."""
    _clean, mode = _extract_pg_ssl_mode("postgresql://u:p@h/db")
    assert mode == "require"


def test_db_ssl_setting_overrides_url(monkeypatch):
    monkeypatch.setattr(app_config.settings, "single_user_mode", True)
    monkeypatch.setattr(app_config.settings, "db_ssl", "verify-full")
    _clean, mode = _extract_pg_ssl_mode("postgresql://u:p@h/db?sslmode=disable")
    assert mode == "verify-full"


def test_pgbouncer_param_is_stripped(local_mode):
    """A Supabase/Prisma ``?pgbouncer=true`` hint must be dropped - asyncpg and
    psycopg reject it as a connect kwarg (regression: ``connect() got an
    unexpected keyword argument 'pgbouncer'``)."""
    clean, _mode = _extract_pg_ssl_mode(
        "postgresql+asyncpg://u:p@h:6543/db?pgbouncer=true"
    )
    assert "pgbouncer" not in clean
    assert clean == "postgresql+asyncpg://u:p@h:6543/db"


def test_pgbouncer_stripped_but_sslmode_kept(local_mode):
    """Dropping ``pgbouncer`` must not disturb SSL resolution when both are present."""
    clean, mode = _extract_pg_ssl_mode(
        "postgresql://u:p@h:6543/db?pgbouncer=true&sslmode=require"
    )
    assert "pgbouncer" not in clean
    assert "sslmode" not in clean
    assert clean == "postgresql://u:p@h:6543/db"
    assert mode == "require"


def test_asyncpg_ssl_arg_disable_is_false():
    assert _asyncpg_ssl_arg("disable") is False


def test_asyncpg_ssl_arg_none_and_prefer_are_none():
    assert _asyncpg_ssl_arg(None) is None
    assert _asyncpg_ssl_arg("prefer") is None
    assert _asyncpg_ssl_arg("allow") is None


def test_asyncpg_ssl_arg_require_is_non_verifying_context():
    ctx = _asyncpg_ssl_arg("require")
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.check_hostname is False
    assert ctx.verify_mode == _ssl.CERT_NONE


def test_asyncpg_ssl_arg_verify_full_verifies():
    ctx = _asyncpg_ssl_arg("verify-full")
    assert isinstance(ctx, _ssl.SSLContext)
    assert ctx.check_hostname is True
    assert ctx.verify_mode == _ssl.CERT_REQUIRED
