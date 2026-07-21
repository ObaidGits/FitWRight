"""Read-only configuration diagnostics for the admin surface (Task 8.1).

``ConfigService.diagnostics()`` assembles the :class:`ConfigDiagnostics` payload
served by ``GET /admin/config`` (Req 10): the environment name, the active AI
provider(s), the storage/email providers, every meaningful feature flag, the
maintenance-mode state, ``SCHEDULER_MODE``, the delete grace period, the
kill-switch states, and the version identifiers.

**Strictly read-only (Req 10.3 / 21.7).** This service exposes exactly one
method - ``diagnostics()`` - and offers NO create/update/delete/toggle operation.
It only reads ``app.config.settings`` and ``app.__version__``; it never writes.

**Secret-free (Req 10.4 / 10.5).** No secret or credential value is ever placed
in the payload. Each configured secret surfaces solely as a boolean presence
indicator in ``configured`` whose *key* deliberately avoids every
:data:`~app.admin.schemas.FORBIDDEN_SUBSTRINGS` entry (so, e.g., the session
signing secret is reported as ``sessionSigningConfigured``, the ip-hash HMAC key
as ``ipHmacConfigured``, the internal job token as ``internalJobAuthConfigured``)
- no secret name or value ever serializes, and the model passes
``assert_no_forbidden_fields``.

**Bounded-context purity (Req 19.2/19.3/19.5).** This Domain_Metrics_Service
depends ONLY on shared primitives: ``app.config.settings``, the ``app.admin``
schemas, and the ``app.__version__`` constant. It imports no other
Domain_Metrics_Service, so the import-graph guard
(``tests/architecture/test_admin_import_graph.py``) holds.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import __version__
from app.admin.schemas import ConfigDiagnostics
from app.config import settings

__all__ = ["ConfigService", "get_config_service", "reset_config_service"]


def _now_iso() -> str:
    """Current UTC time as an ISO-8601 string (matches the other admin models)."""
    return datetime.now(timezone.utc).isoformat()


class ConfigService:
    """Compose the read-only :class:`ConfigDiagnostics` payload (Req 10).

    Synchronous by design: it only reads process configuration + the version
    constant, touching neither the database nor any network, so it is a cheap
    O(1) read with no I/O to await.
    """

    # -- public API ----------------------------------------------------------

    def diagnostics(self) -> ConfigDiagnostics:
        """Assemble the full diagnostics payload from settings + version.

        Every field is derived from ``app.config.settings`` (plus
        ``app.__version__``); no secret value is included - secrets appear only
        as the boolean presence indicators in ``configured`` (Req 10.4).
        """
        return ConfigDiagnostics(
            env=self._env(),
            activeAiProviders=self._active_ai_providers(),
            storageProvider=str(settings.storage_provider),
            emailProvider=self._email_provider(),
            featureFlags=self._feature_flags(),
            maintenanceMode=self._maintenance_mode(),
            schedulerMode=str(settings.scheduler_mode),
            gracePeriodDays=int(settings.admin_delete_grace_days),
            killSwitches=self._kill_switches(),
            versions=self._versions(),
            configured=self._configured(),
            computedAt=_now_iso(),
        )

    # -- field builders ------------------------------------------------------

    @staticmethod
    def _env() -> str:
        """The active deployment profile name (same source as HealthService).

        Read through the composition root's identity/profile seam
        (``app.platform``) rather than the settings axis directly, so the
        deployment-mode decision stays contained to the sanctioned seam
        (ARCHITECTURE §18.5 / ``tests/architecture/test_profile_containment``).
        """
        try:
            from app.platform import get_container

            return get_container().profile().value
        except Exception:  # pragma: no cover - defensive; never fail diagnostics
            return "unknown"

    @staticmethod
    def _active_ai_providers() -> list[str]:
        """The active AI provider name(s) - names only, never keys (Req 10.4).

        The backend selects a single default LLM provider via ``LLM_PROVIDER``
        (per-user provider keys live in the encrypted store and are intentionally
        NOT read here, so this stays a sync, settings-only, secret-free read).
        Reported as a one-element list to match the schema's list shape and to
        leave room for multi-provider setups without a shape change.
        """
        provider = str(settings.llm_provider).strip()
        return [provider] if provider else []

    @staticmethod
    def _email_provider() -> str:
        """The configured email provider; a blank value is the dev logging sender."""
        provider = (settings.email_provider or "").strip()
        return provider or "log"

    @staticmethod
    def _feature_flags() -> dict[str, bool]:
        """Meaningful product/feature flags with their boolean state.

        Keys are camelCase and deliberately secret-free. These are the
        dark-launch / product-surface toggles (ADR-14 ``*_enabled`` flags) plus
        the deployment-shape booleans an operator needs to see at a glance.
        Genuine operational kill-switches live in ``killSwitches`` instead.
        """
        from app.platform import get_container

        return {
            # Single-user vs multi-user is a property of the resolved deployment
            # profile (the single source of truth), read through the composition
            # root's seam rather than the raw settings axis (ARCHITECTURE §18.5).
            "singleUserMode": get_container().profile().is_local,
            "emailVerification": bool(settings.email_verification_enabled),
            "adminEnabled": bool(settings.admin_enabled),
            "versionHistory": bool(settings.version_history_enabled),
            "profile": bool(settings.profile_enabled),
            "search": bool(settings.search_enabled),
            "notifications": bool(settings.notifications_enabled),
            "sseNotifications": bool(settings.sse_notifications),
            "reminders": bool(settings.reminders_enabled),
            "interviews": bool(settings.interviews_enabled),
            "agenda": bool(settings.agenda_enabled),
            "jdV2": bool(settings.jd_v2_enabled),
            "jdPdf": bool(settings.jd_pdf_enabled),
            "jdOcr": bool(settings.jd_ocr_enabled),
            "jdI18n": bool(settings.jd_i18n_enabled),
            "jdRobotsCheck": bool(settings.jd_robots_check_enabled),
            "jdCostMonitoring": bool(settings.jd_cost_monitoring_enabled),
            "jdExtensionFallback": bool(settings.jd_extension_fallback_enabled),
            "jdMlScoring": bool(settings.jd_ml_scoring_enabled),
            "streamingAi": bool(settings.streaming_ai_enabled),
            "offlineSupport": bool(settings.offline_support_enabled),
            "advancedAutosave": bool(settings.advanced_autosave_enabled),
        }

    @staticmethod
    def _maintenance_mode() -> bool:
        """Maintenance-mode state.

        The current configuration has no maintenance-mode toggle (Task 8.2's
        MaintenanceService triggers maintenance *actions* - re-running existing
        jobs - not a platform-wide maintenance *mode*). There is therefore no
        honest signal to derive it from, so this reports ``False``. Documented
        gap: introduce a dedicated ``MAINTENANCE_MODE`` setting if/when a
        read-through maintenance state is added.
        """
        return False

    @staticmethod
    def _kill_switches() -> dict[str, bool]:
        """Operational kill-switches (capability on/off), by camelCase name.

        These are the flags whose explicit purpose is to disable a capability
        without a redeploy (ADR-14): destructive user actions, the SSRF-hardened
        JD-from-URL import, notification email delivery, and the employer webhook
        ingest. Keys are secret-free.
        """
        return {
            "adminDestructiveActions": bool(settings.admin_destructive_actions),
            "jdFromUrl": bool(settings.jd_from_url_enabled),
            "notificationsEmail": bool(settings.notifications_email_enabled),
            "jdWebhook": bool(settings.jd_webhook_enabled),
        }

    @staticmethod
    def _versions() -> dict[str, str]:
        """Secret-free version identifiers.

        Only the backend application version is trivially and cheaply available
        in-process; the Alembic head/applied revisions are surfaced by the health
        panel (which already probes them) rather than re-read here to keep this a
        pure, sync, no-I/O read.
        """
        return {"backend": str(__version__)}

    @staticmethod
    def _configured() -> dict[str, bool]:
        """Presence booleans for each configured secret/credential (Req 10.4).

        Each entry is ``True`` when a non-empty value is configured and ``False``
        otherwise. NO secret value is ever included - only the boolean. Keys are
        chosen to avoid every FORBIDDEN_SUBSTRINGS entry (``secret``/``token``/
        ``hash``/``apikey``/``credential``/...), which is why they read
        ``sessionSigningConfigured`` / ``ipHmacConfigured`` /
        ``internalJobAuthConfigured`` rather than the raw secret names.
        """
        return {
            # Any global AI provider key configured via LLM_API_KEY (per-user
            # keys in the encrypted store are intentionally not consulted here).
            "aiConfigured": bool(str(settings.llm_api_key).strip()),
            # SMTP delivery credentials present (host + auth password).
            "smtpConfigured": bool(
                str(settings.email_smtp_host).strip()
                and str(settings.email_smtp_password).strip()
            ),
            # Transactional email API key present (e.g. Resend).
            "emailApiConfigured": bool(str(settings.email_api_key).strip()),
            # Cloudinary object-storage credentials fully present.
            "cloudinaryConfigured": bool(settings.cloudinary_configured),
            # Google OAuth client id + secret both present.
            "oauthConfigured": bool(settings.google_oauth_configured),
            # Session-signing secret present.
            "sessionSigningConfigured": bool(str(settings.session_secret).strip()),
            # IP-hash HMAC key present.
            "ipHmacConfigured": bool(str(settings.ip_hash_secret).strip()),
            # Internal machine-endpoint auth token present.
            "internalJobAuthConfigured": bool(str(settings.internal_job_token).strip()),
            # CAPTCHA provider secret present.
            "captchaConfigured": bool(str(settings.captcha_secret).strip()),
            # Employer-webhook HMAC secret present.
            "jdWebhookConfigured": bool(str(settings.jd_webhook_secret).strip()),
        }


# ---------------------------------------------------------------------------
# Process-wide instance (mirrors the other admin service accessors)
# ---------------------------------------------------------------------------

_service: ConfigService | None = None


def get_config_service() -> ConfigService:
    """Return the process-wide :class:`ConfigService`."""
    global _service
    if _service is None:
        _service = ConfigService()
    return _service


def reset_config_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
