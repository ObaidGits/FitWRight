"""Startup provider diagnostics (observability / operability).

Emits a single, **secret-free** structured report at boot enumerating which
concrete providers the process resolved from configuration: database dialect +
TLS mode + pooling + auto-migrate, KVStore backend, email/captcha/breach
providers, storage provider, scheduler mode, and deployment mode. This turns
"which adapters am I actually running?" — normally answerable only by reading
env + code — into one log line, and surfaces silent dev-safe fallbacks (e.g.
``EMAIL_PROVIDER=resend`` degraded to the log sender because the key is missing)
that would otherwise be invisible until a user hit the path.

Only names/modes/booleans are reported — never URLs, keys, secrets, or hosts.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["build_startup_report", "log_startup_report"]


def _db_summary(settings) -> dict[str, Any]:
    url = settings.effective_database_url
    if url.startswith("sqlite"):
        return {"dialect": "sqlite", "auto_migrate": False, "ssl": "n/a", "pooler": False}
    # Resolve the effective TLS mode without touching the URL's secrets.
    try:
        from app.db_engine import _extract_pg_ssl_mode

        _clean, ssl_mode = _extract_pg_ssl_mode(url)
    except Exception:  # pragma: no cover - defensive
        ssl_mode = None
    return {
        "dialect": "postgresql",
        "auto_migrate": bool(settings.db_auto_migrate),
        "migration_url_override": bool((settings.migration_database_url or "").strip()),
        "ssl": ssl_mode or "driver-default",
        "pooler": bool(settings.db_use_pooler),
        "pool_size": settings.db_pool_size,
    }


def _kvstore_summary(settings) -> str:
    url = (settings.kvstore_url or "").strip().lower()
    if not url:
        return "in-process (local)"
    if url == "db":
        return "database-backed"
    if url.startswith("rediss://"):
        return "redis (TLS)"
    if url.startswith("redis://"):
        return "redis"
    return "custom"


def _email_summary(settings) -> str:
    provider = (settings.email_provider or "").strip().lower()
    if not provider or provider in {"log", "none", "dev"}:
        return "log (dev — no delivery)"
    if provider == "smtp":
        return "smtp" if (settings.email_smtp_host and settings.email_from) else "smtp→log (misconfigured)"
    if provider == "resend":
        return "resend" if (settings.email_api_key and settings.email_from) else "resend→log (misconfigured)"
    return f"{provider}→log (unknown)"


def _storage_summary(settings) -> str:
    choice = settings.storage_provider
    if choice == "cloudinary":
        return "cloudinary" if settings.cloudinary_configured else "cloudinary→local (misconfigured)"
    return choice


def build_startup_report(settings) -> dict[str, Any]:
    """Assemble the secret-free provider report (pure; unit-testable)."""
    # Explicit deployment profile + capability contract (ARCHITECTURE §5).
    # Imported lazily to keep ``platform`` out of diagnostics import time.
    from app.platform import capability_report

    return {
        "mode": "single_user" if settings.single_user_mode else "hosted",
        "profile": capability_report(settings),
        "email_verification": settings.email_verification_enabled,
        "database": _db_summary(settings),
        "kvstore": _kvstore_summary(settings),
        "email": _email_summary(settings),
        "captcha": (settings.captcha_provider or "none").strip().lower() or "none",
        "breach_check": (settings.breach_provider or "none").strip().lower() or "none",
        "storage": _storage_summary(settings),
        "scheduler": settings.scheduler_mode,
        "admin_enabled": settings.admin_enabled,
    }


def log_startup_report(settings) -> dict[str, Any]:
    """Build and log the startup provider report at INFO. Returns it too."""
    report = build_startup_report(settings)
    logger.info("Startup provider report: %s", report)
    return report
