"""Runtime construction of auth infrastructure from configuration (ADR-6/14).

Task 0.2 shipped the pluggable pieces (``KVStore``, ``EmailSender``,
``CaptchaVerifier``, ``BreachedPasswordCheck``) with dev-safe defaults. This
module is the *wiring* layer that turns configuration (``app.config.settings``)
into concrete, ready-to-use instances and exposes them as lazily-constructed
process singletons plus FastAPI-style dependency callables the later auth waves
depend on.

Selection rules (all "value change, not code change" — ADR-14):

- ``KVSTORE_URL`` picks the KVStore adapter; the DB-backed fallback reuses the
  app's own async engine so KV data lives in the same database (ADR-6).
- ``EMAIL_PROVIDER`` / ``CAPTCHA_PROVIDER`` / ``BREACH_PROVIDER`` pick the
  provider adapter; empty selects the shipped dev-safe default. Recognized
  providers (``smtp``/``resend`` email, ``turnstile`` CAPTCHA, ``hibp`` breach)
  build their real adapter. A misconfigured provider (recognized but missing the
  credentials real delivery needs) or an unrecognized value never raises — it
  **gracefully degrades** to the dev-safe default with a single logged warning
  naming the problem, so a missing/mis-set provider can never crash construction
  or stop the app from booting (completion-pass provider contract).

Everything is constructed from an explicit ``Settings`` argument so the builders
are unit-testable in isolation; the module-level singletons bind to the live
``settings`` object.
"""

from __future__ import annotations

import logging

from app.auth.breach import (
    BreachedPasswordCheck,
    HibpBreachedPasswordCheck,
    NoopBreachedPasswordCheck,
)
from app.auth.captcha import (
    AllowAllCaptchaVerifier,
    CaptchaVerifier,
    TurnstileCaptchaVerifier,
)
from app.auth.email import (
    EmailSender,
    LoggingEmailSender,
    ResendEmailSender,
    SmtpEmailSender,
)
from app.auth.kvstore import KVStore, kvstore_from_url, url_needs_db_engine
from app.config import (
    _BREACH_DISABLED_ALIASES,
    _CAPTCHA_DISABLED_ALIASES,
    _EMAIL_DEFAULT_ALIASES,
    Settings,
)

logger = logging.getLogger(__name__)

__all__ = [
    "build_kvstore",
    "build_email_sender",
    "build_captcha_verifier",
    "build_breached_password_check",
    "get_kvstore",
    "get_email_sender",
    "get_captcha_verifier",
    "get_breached_password_check",
    "close_kvstore",
]


# ---------------------------------------------------------------------------
# Builders (pure: Settings in, adapter out)
# ---------------------------------------------------------------------------


def build_kvstore(config: Settings) -> KVStore:
    """Construct the ``KVStore`` adapter selected by ``KVSTORE_URL``.

    The app's own async engine is passed only when the DB-backed adapter is
    selected, so a local/Redis store never forces database initialization.
    """
    db_engine = None
    if url_needs_db_engine(config.kvstore_url):
        # Imported lazily to avoid a config→database import cycle at module load.
        from app.database import db

        db_engine = db.async_engine
    return kvstore_from_url(config.kvstore_url, db_engine=db_engine)


def build_email_sender(config: Settings) -> EmailSender:
    """Construct the ``EmailSender`` selected by ``EMAIL_PROVIDER``.

    ``smtp``/``resend`` build the real adapter; missing delivery credentials or
    an unrecognized value gracefully degrade to the dev logging sender (never
    raise), so the app always boots (ADR-14, completion-pass provider contract).
    """
    provider = (config.email_provider or "").strip().lower()
    if provider in _EMAIL_DEFAULT_ALIASES:
        return LoggingEmailSender()

    if provider == "smtp":
        if not config.email_smtp_host or not config.email_from:
            logger.warning(
                "EMAIL_PROVIDER=smtp but EMAIL_SMTP_HOST/EMAIL_FROM are missing; "
                "falling back to the dev logging sender (no email will be delivered). "
                "Set SMTP host + EMAIL_FROM to enable real delivery."
            )
            return LoggingEmailSender()
        return SmtpEmailSender(
            host=config.email_smtp_host,
            port=config.email_smtp_port,
            username=config.email_smtp_user,
            password=config.email_smtp_password,
            sender=config.email_from,
            use_tls=config.email_smtp_use_tls,
        )

    if provider == "resend":
        if not config.email_api_key or not config.email_from:
            logger.warning(
                "EMAIL_PROVIDER=resend but EMAIL_API_KEY/EMAIL_FROM are missing; "
                "falling back to the dev logging sender (no email will be delivered). "
                "Set EMAIL_API_KEY + EMAIL_FROM to enable real delivery."
            )
            return LoggingEmailSender()
        return ResendEmailSender(api_key=config.email_api_key, sender=config.email_from)

    logger.warning(
        "EMAIL_PROVIDER=%r is not a recognized email provider (supported: smtp, "
        "resend); falling back to the dev logging sender.",
        config.email_provider,
    )
    return LoggingEmailSender()


def build_captcha_verifier(config: Settings) -> CaptchaVerifier:
    """Construct the ``CaptchaVerifier`` selected by ``CAPTCHA_PROVIDER``.

    ``turnstile`` builds the real verifier; a missing secret or an unrecognized
    value gracefully degrades to allow-all (feature effectively off) with a
    logged warning (never raises) — a CAPTCHA misconfig must not block auth.
    """
    provider = (config.captcha_provider or "").strip().lower()
    if provider in _CAPTCHA_DISABLED_ALIASES:
        return AllowAllCaptchaVerifier()

    if provider == "turnstile":
        if not config.captcha_secret:
            logger.warning(
                "CAPTCHA_PROVIDER=turnstile but CAPTCHA_SECRET is missing; "
                "falling back to allow-all (CAPTCHA effectively disabled). "
                "Set CAPTCHA_SECRET to enable the challenge."
            )
            return AllowAllCaptchaVerifier()
        return TurnstileCaptchaVerifier(secret=config.captcha_secret)

    logger.warning(
        "CAPTCHA_PROVIDER=%r is not a recognized CAPTCHA provider (supported: "
        "turnstile; hCaptcha/reCAPTCHA are documented future variants); falling "
        "back to allow-all (CAPTCHA effectively disabled).",
        config.captcha_provider,
    )
    return AllowAllCaptchaVerifier()


def build_breached_password_check(config: Settings) -> BreachedPasswordCheck:
    """Construct the ``BreachedPasswordCheck`` selected by ``BREACH_PROVIDER``.

    ``hibp`` builds the real HIBP k-anonymity check (no credentials needed); an
    unrecognized value gracefully degrades to the no-op check with a logged
    warning (never raises).
    """
    provider = (config.breach_provider or "").strip().lower()
    if provider in _BREACH_DISABLED_ALIASES:
        return NoopBreachedPasswordCheck()

    if provider == "hibp":
        return HibpBreachedPasswordCheck()

    logger.warning(
        "BREACH_PROVIDER=%r is not a recognized breach provider (supported: hibp); "
        "falling back to the no-op check (breached-password check disabled).",
        config.breach_provider,
    )
    return NoopBreachedPasswordCheck()


# ---------------------------------------------------------------------------
# Dependency callables — thin delegates to the composition root (Phase 3).
# ---------------------------------------------------------------------------
#
# Construction + caching of these adapters lives ONLY in the composition root
# (``app.platform.composition.Container``), which calls the pure ``build_*``
# functions above. These ``get_*`` functions remain as the stable call surface
# the rest of the app already imports, now delegating to the container so there
# is a single instantiation site (ARCHITECTURE §2/§10; IMPLEMENTATION_PLAN
# Phase 3). The ``build_*`` functions above take an explicit ``Settings``
# argument (passed by the container); they no longer read the module-level
# ``settings`` singleton.


def get_kvstore() -> KVStore:
    """Return the process-wide ``KVStore`` (owned by the composition root)."""
    from app.platform import get_container

    return get_container().kvstore()


def get_email_sender() -> EmailSender:
    """Return the process-wide ``EmailSender`` (owned by the composition root)."""
    from app.platform import get_container

    return get_container().email_sender()


def get_captcha_verifier() -> CaptchaVerifier:
    """Return the process-wide ``CaptchaVerifier`` (owned by the composition root)."""
    from app.platform import get_container

    return get_container().captcha_verifier()


def get_breached_password_check() -> BreachedPasswordCheck:
    """Return the process-wide ``BreachedPasswordCheck`` (owned by the composition root)."""
    from app.platform import get_container

    return get_container().breached_password_check()


async def close_kvstore() -> None:
    """Release the KVStore's resources on shutdown (delegates to the container).

    The DB-backed adapter shares the app's async engine (disposed by
    ``Database.close``), so the container closes only a non-DB store.
    """
    from app.platform import get_container

    await get_container().aclose()
