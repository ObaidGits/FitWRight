"""Application configuration using pydantic-settings."""

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _is_https_or_loopback_http(uri: str) -> bool:
    """Whether an OAuth redirect URI is acceptable in hosted mode.

    Accepts any ``https://`` URL, plus ``http://`` when the host is a loopback
    address (localhost / 127.0.0.1 / ::1). Plaintext http is safe there because
    the traffic never leaves the machine - this is the standard local-dev OAuth
    pattern that Google's console explicitly permits and that RFC 8252 §8.3
    (loopback interface redirection) sanctions. Any non-loopback host must use
    https so OAuth is never carried over the network in cleartext.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(uri)
    except Exception:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http":
        return (parsed.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")
    return False


# Minimum length for operator-supplied cryptographic secrets (session signing,
# ip-hash HMAC key). Short secrets are a real weakness, so we reject them with a
# clear error rather than silently accepting them.
_MIN_SECRET_LENGTH = 16

# Bounds for the tunable session/step-up lifetimes (seconds). Kept generous but
# finite so a typo can't create a never-expiring session or a zero-length one.
_MIN_TTL_SECONDS = 60
_MAX_TTL_SECONDS = 60 * 60 * 24 * 365  # one year

# Adapter-selection aliases for the pluggable auth providers (ADR-14). An empty
# value (the zero-config default) selects the shipped dev-safe adapter.
_EMAIL_DEFAULT_ALIASES = frozenset({"", "log", "logging", "console", "dev"})
_CAPTCHA_DISABLED_ALIASES = frozenset({"", "none", "disabled", "off", "allow"})
_BREACH_DISABLED_ALIASES = frozenset({"", "none", "disabled", "off", "noop"})

# KVSTORE_URL values that select the in-process (non-shared) LocalKVStore. Kept
# in sync with app.auth.kvstore.factory so hosted-mode validation can detect a
# non-shared store (which would split rate-limit/lock/session state per worker).
_KVSTORE_LOCAL_ALIASES = frozenset(
    {"", "local", "memory", "inproc", "in-proc", "local://"}
)


# Path to config file for API key persistence
CONFIG_FILE_PATH = Path(__file__).parent.parent / "data" / "config.json"
ALLOWED_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def _read_config_json() -> dict[str, Any]:
    """Raw read of config.json (no key injection)."""
    if CONFIG_FILE_PATH.exists():
        try:
            return json.loads(CONFIG_FILE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write_config_json(config: dict[str, Any]) -> None:
    """Raw write of config.json (no secret stripping)."""
    CONFIG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE_PATH.write_text(json.dumps(config, indent=2))


def load_config_file(user_id: str | None = None) -> dict[str, Any]:
    """Load non-secret configuration, with the caller's decrypted API keys injected.

    API keys live in the encrypted SQLite store, not config.json. They are
    injected here under ``api_keys`` so ``resolve_api_key(stored, provider)``
    keeps resolving per-provider keys everywhere ``stored`` is built from this
    function. Keys are scoped to ``user_id`` (or the request-scoped effective
    user / bootstrap owner), so a request never sees another user's keys (R10.6).
    ``save_config_file`` strips them again, so they never round-trip to disk.
    """
    config = _read_config_json()
    config["api_keys"] = get_api_keys_from_config(user_id)
    return config


def save_config_file(config: dict[str, Any]) -> None:
    """Save non-secret configuration to config.json.

    Secrets (``api_keys`` map and the legacy single ``api_key``) are stripped
    before writing - they belong to the encrypted store only.
    """
    config = dict(config)
    config.pop("api_keys", None)
    config.pop("api_key", None)
    _write_config_json(config)


def resolve_key_user_id(user_id: str | None = None) -> str:
    """Resolve the user whose encrypted key store to read/write (ADR-4, R10.6).

    Priority: an explicit ``user_id`` (a request thread it) -> the request-scoped
    effective user id published by the auth dependency -> the bootstrap owner
    (single-user/local, or the pre-migration owner on hosted). This is the single
    place that decides *whose* provider keys are in play, so one user's key can
    never serve another's LLM calls.
    """
    if user_id is not None:
        return user_id
    from app.auth.context import get_current_user_id
    from app.auth.owner import resolve_owner_id_sync

    ctx = get_current_user_id()
    if ctx is not None:
        return ctx
    return resolve_owner_id_sync()


def get_api_keys_from_config(user_id: str | None = None) -> dict[str, str]:
    """Get decrypted API keys for a user from the encrypted SQLite store.

    Args:
        user_id: Owner of the keys; defaults to the request-scoped effective
            user (or the bootstrap owner locally) via :func:`resolve_key_user_id`.

    Returns:
        Dictionary with key-store provider names as keys and plaintext keys as
        values (entries that fail to decrypt are omitted).
    """
    from app.crypto import decrypt
    from app.database import db

    uid = resolve_key_user_id(user_id)
    decrypted: dict[str, str] = {}
    for provider, ciphertext in db.get_api_key_ciphertexts(uid).items():
        plaintext = decrypt(ciphertext)
        if plaintext:
            decrypted[provider] = plaintext
    return decrypted


def save_api_keys_to_config(api_keys: dict[str, str], user_id: str | None = None) -> None:
    """Replace a user's encrypted key store with ``api_keys`` (encrypting each).

    Replace-all semantics mirror the legacy ``config["api_keys"] = api_keys``;
    the config router reads-merges-saves the full map. Only the resolved user's
    keys are replaced.
    """
    from app.crypto import encrypt
    from app.database import db

    uid = resolve_key_user_id(user_id)
    # Encrypt everything first, then swap in a single transaction, so a partial
    # failure (encryption error or DB write) can never wipe previously stored
    # keys mid-replace.
    ciphertexts = {provider: encrypt(key) for provider, key in api_keys.items() if key}
    db.replace_api_keys(uid, ciphertexts)


def delete_api_key_from_config(provider: str, user_id: str | None = None) -> None:
    """Delete a specific API key from a user's encrypted store."""
    from app.database import db

    db.delete_api_key(resolve_key_user_id(user_id), provider)


def clear_all_api_keys(user_id: str | None = None) -> None:
    """Clear a user's API keys from the encrypted store and legacy config slots."""
    from app.database import db

    db.clear_api_keys(resolve_key_user_id(user_id))
    # Defensively clear any legacy plaintext remnants from config.json.
    config = _read_config_json()
    if "api_keys" in config or "api_key" in config:
        config.pop("api_keys", None)
        config.pop("api_key", None)
        _write_config_json(config)


def migrate_legacy_keys() -> None:
    """Fold legacy plaintext keys from config.json into the encrypted store.

    Idempotent and non-clobbering: an existing config.json ``api_keys`` map and
    the legacy single ``api_key`` (mapped to its key-store provider via the
    active provider) are written to the encrypted store **only if that provider
    slot is empty**, then removed from config.json. This eliminates the
    legacy-shadow bug where ``resolve_api_key`` returned one shared key for
    every provider.
    """
    config = _read_config_json()
    legacy_map = config.get("api_keys")
    legacy_single = config.get("api_key")
    if not legacy_map and not legacy_single:
        return

    from app.crypto import encrypt
    from app.database import db

    # Legacy plaintext keys are pre-multi-user data -> they belong to the
    # bootstrap owner (same principle as migration 0004 assigning api_keys to
    # the owner).
    owner_id = resolve_key_user_id()
    existing = set(db.get_api_key_ciphertexts(owner_id).keys())

    if isinstance(legacy_map, dict):
        for provider, key in legacy_map.items():
            if key and provider not in existing:
                db.set_api_key_ciphertext(owner_id, provider, encrypt(key))
                existing.add(provider)

    if legacy_single:
        # Map the active LLM provider to its key-store provider name.
        provider = config.get("provider") or settings.llm_provider
        key_provider = _LEGACY_PROVIDER_KEY_MAP.get(provider, provider)
        if key_provider not in existing:
            db.set_api_key_ciphertext(owner_id, key_provider, encrypt(legacy_single))

    # Strip the legacy slots from config.json now that they're in the store.
    config.pop("api_keys", None)
    config.pop("api_key", None)
    _write_config_json(config)


# Mirror of llm._PROVIDER_KEY_MAP, duplicated to avoid importing llm.py (which
# pulls in litellm) at config import time.
_LEGACY_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "openai",
    "openai_compatible": "openai_compatible",
    "anthropic": "anthropic",
    "gemini": "google",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "groq": "groq",
    "ollama": "ollama",
}


def _get_llm_api_key_with_fallback() -> str:
    """Get LLM API key with fallback to config file.

    Priority: Environment variable > config.json > empty string
    """
    import os

    # First check environment variable
    env_key = os.environ.get("LLM_API_KEY", "")
    if env_key:
        return env_key

    # Fallback to config file based on provider
    config_keys = get_api_keys_from_config()
    provider = os.environ.get("LLM_PROVIDER", "openai")

    # Map provider to config key
    provider_map = {
        "openai": "openai",
        "anthropic": "anthropic",
        "gemini": "google",
        "openrouter": "openrouter",
        "deepseek": "deepseek",
        "groq": "groq",
        "ollama": "ollama",
    }

    config_provider = provider_map.get(provider, provider)
    return config_keys.get(config_provider, "")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM Configuration
    llm_provider: Literal[
        "openai",
        "openai_compatible",
        "anthropic",
        "openrouter",
        "gemini",
        "deepseek",
        "groq",
        "ollama",
    ] = "openai"
    llm_model: str = "gpt-5-nano-2025-08-07"
    llm_api_key: str = ""
    llm_api_base: str | None = None  # For Ollama or custom endpoints
    log_llm: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "WARNING"

    @field_validator("llm_provider", mode="before")
    @classmethod
    def set_default_provider(cls, v: Any) -> str:
        """Handle empty string provider by defaulting to openai."""
        if not v or (isinstance(v, str) and not v.strip()):
            return "openai"
        return v

    @field_validator("log_llm", mode="before")
    @classmethod
    def normalize_log_llm_level(cls, v: Any) -> str:
        """Normalize LiteLLM log level from environment values."""
        value = "WARNING" if not v else str(v).strip().upper()
        if value not in ALLOWED_LOG_LEVELS:
            raise ValueError(f"Invalid LOG_LLM: {value}. Allowed: {ALLOWED_LOG_LEVELS}")
        return value

    # Server Configuration
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    frontend_base_url: str = "http://localhost:3000"

    # Hard timeout (seconds) for a single resume tailoring/improve request - the
    # backend wraps the improve flow in asyncio.wait_for(timeout=this). It MUST be
    # kept in sync with the two frontend layers (Next.js `proxyTimeout` and the
    # client AbortController, both driven by NEXT_PUBLIC_REQUEST_TIMEOUT_MS):
    # whichever layer is shortest aborts first, so raising only one silently fails
    # (this is why issue #776's backend-only workaround didn't work). Local LLMs
    # (Ollama, llama.cpp, ...) often need longer than the 240s default; bounded to
    # [30, 1800]s so a stuck request can't hold a worker indefinitely.
    request_timeout_seconds: int = 240

    @field_validator("email_smtp_port", mode="before")
    @classmethod
    def _blank_smtp_port_to_default(cls, v: Any) -> int:
        """Treat a blank ``EMAIL_SMTP_PORT=`` as the default (587)."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 587
        return v

    @field_validator("request_timeout_seconds", mode="before")
    @classmethod
    def clamp_request_timeout(cls, v: Any) -> int:
        """Clamp to [30, 1800] seconds; fall back to 240 on blank/invalid input."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 240
        try:
            seconds = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            # OverflowError guards against inf (int(float("inf"))); ValueError
            # against nan/garbage. A bad env value must never crash startup.
            return 240
        return max(30, min(1800, seconds))

    # Reasoning effort for models that support it (OpenAI gpt-5 family,
    # Anthropic Claude 3.7+, DeepSeek R1, etc.). None means "do not send the
    # param" - the default for maximum compatibility. LiteLLM drops this
    # parameter for providers that don't support it (via drop_params=True).
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def normalize_reasoning_effort(cls, v: Any) -> Any:
        """Treat empty string (common when env var is blank) as None."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, v: Any) -> str:
        """Normalize application log level from environment values."""
        value = "INFO" if not v else str(v).strip().upper()
        if value not in ALLOWED_LOG_LEVELS:
            raise ValueError(f"Invalid LOG_LEVEL: {value}. Allowed: {ALLOWED_LOG_LEVELS}")
        return value

    # CORS Configuration
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @property
    def effective_cors_origins(self) -> list[str]:
        """CORS origins including frontend_base_url for production deployments."""
        origins = list(self.cors_origins)
        url = self.frontend_base_url.strip().rstrip("/")
        if url and url not in origins:
            origins.append(url)
        return origins

    # =====================================================================
    # Auth foundation (P1 Multi-User Foundation)
    # =====================================================================
    # Deployment mode. Local dev defaults to single-user (auto-login owner, no
    # verification) so the app boots with zero config, identical to today.
    # Hosted deployments MUST set SINGLE_USER_MODE=false, which turns on the
    # fail-fast requirements for the real secrets below (R14.3).
    single_user_mode: bool = True

    # Explicit deployment profile (ARCHITECTURE §3/§4; IMPLEMENTATION_PLAN Phase
    # 1). Declared intent - ``desktop``/``saas``/``enterprise``/``self_hosted``/
    # ``development``/``test``/``ci``. Blank -> derived from ``single_user_mode``
    # (desktop when true, saas when false), so existing .env files are unchanged.
    # When set, it must not contradict ``single_user_mode`` (validated at boot).
    deployment_profile: str = ""

    # Session signing / CSRF derivation secret (+ previous key for zero-downtime
    # rotation, R16.3). Required in hosted mode; in local mode a strong ephemeral
    # value is generated per process (never persisted, never used in hosted).
    session_secret: str = ""
    session_secret_prev: str = ""

    # Keyed HMAC secret for hashing client IPs in sessions/audit (R12.5). Same
    # required-in-hosted / ephemeral-in-local rules as ``session_secret``.
    ip_hash_secret: str = ""

    # Symmetric secret used to encrypt provider API keys at rest (``app/crypto``).
    # MUST be stable across restarts/redeploys, otherwise stored ciphertext can no
    # longer be decrypted and users' saved keys appear wiped. On a platform with an
    # ephemeral filesystem (e.g. Heroku) the on-disk ``data/.secret_key`` fallback
    # is regenerated on every release, so this env var is REQUIRED in hosted mode.
    # Locally (single-user) it is optional: the persistent on-disk secret is used.
    # Accepts either a Fernet key (``Fernet.generate_key()``) or any strong string
    # (a stable key is derived from it). Rotating this value orphans existing keys.
    app_encryption_key: str = ""

    # Google OAuth (provider-abstracted, R4). All optional - OAuth is only wired
    # when both client id and secret are present. A partial pair is rejected.
    google_client_id: str = ""
    google_client_secret: str = ""
    # Exact-match allow-listed redirect URI (R4.7). Required when OAuth is
    # configured on a hosted deployment.
    oauth_redirect_uri: str = ""

    # Bootstrap owner (migration 0004 assigns all existing data to this user).
    owner_email: str = "owner@localhost"
    owner_password: str = ""

    # Email verification (R5). Tri-state: unset -> default ON for hosted, OFF for
    # single-user/local. Read the resolved value via ``email_verification_enabled``.
    email_verification: bool | None = None

    # Session lifetimes in seconds (R3.3, R17.1). Absolute cap, the longer cap
    # used when "remember me" is chosen (R2.6), and the idle timeout.
    session_absolute_ttl: int = 60 * 60 * 12  # 12 hours
    remember_me_ttl: int = 60 * 60 * 24 * 30  # 30 days
    # Ordinary sessions remain valid for their full 12-hour browser-cookie cap.
    # Remembered sessions use REMEMBER_ME_TTL as both idle and absolute cap (see
    # SessionService._idle_ttl), so "Keep me signed in" genuinely survives
    # browser restarts/inactivity instead of dying after the old 2-hour window.
    idle_ttl: int = 60 * 60 * 12  # 12 hours

    # Step-up ("sudo") window in seconds for sensitive actions (R9.1).
    step_up_window: int = 60 * 5  # 5 minutes

    # Single-use token TTLs (seconds). Verification tokens are TTL-bound (R5.1);
    # password-reset tokens are deliberately *short*-lived (R6.1).
    email_verification_ttl: int = 60 * 60 * 24  # 24 hours
    password_reset_ttl: int = 60 * 30  # 30 minutes

    # Argon2id parameters (R17.2). argon2-cffi defaults target ~50-100ms; tunable
    # per host. memory_cost is in KiB.
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # 64 MiB
    argon2_parallelism: int = 4

    # Cookie settings (R12.1). ``__Host-`` requires Secure + Path=/ + no Domain;
    # the CSRF cookie is JS-readable. SameSite=Lax by default.
    session_cookie_name: str = "__Host-session"
    csrf_cookie_name: str = "csrf"
    cookie_secure: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Pluggable shared-state store (ADR-6). Empty -> in-process local adapter
    # (single-worker dev). ``redis://``/``rediss://`` -> Redis/Upstash. ``db`` ->
    # DB-backed fallback (no Redis at all).
    kvstore_url: str = ""

    # Primary database (ADR-13). Empty -> the local SQLite file (unchanged
    # behavior). Hosted MUST set a Postgres URL (ephemeral disk wipes SQLite).
    database_url: str = ""
    # Dedicated connection string for DDL/migrations. On Supabase (and any
    # PgBouncer transaction pooler) the runtime uses the POOLED endpoint (6543),
    # but migrations MUST use the DIRECT endpoint (5432): CREATE INDEX
    # CONCURRENTLY cannot run through a transaction pooler and the migration
    # advisory lock is session-scoped (unstable when each statement may hit a
    # different backend). Blank -> fall back to DATABASE_URL (correct when the app
    # already talks to a direct connection, e.g. Neon-direct or local).
    migration_database_url: str = ""
    db_pool_size: int = 5
    db_use_pooler: bool = True
    # Postgres TLS mode (ADR-13). Blank -> derive from the DATABASE_URL's own
    # ``sslmode`` query param, else default to ``require`` in hosted mode and no
    # forced TLS in local single-user mode. Explicit values (disable | prefer |
    # allow | require | verify-ca | verify-full) override the URL/default. This
    # is normalized in ``app.db_engine`` into the per-driver connect arg
    # (asyncpg ``ssl`` context / psycopg ``sslmode``), since asyncpg rejects a
    # raw ``sslmode`` kwarg - required for Supabase's TLS-only endpoints.
    db_ssl: str = ""
    # Automatically run ``alembic upgrade head`` at startup on hosted Postgres
    # (SQLite is unaffected - it uses create_all). Serialized across workers by a
    # Postgres advisory lock and idempotent, so it is safe on every boot. Set
    # false to manage migrations out-of-band (a dedicated release phase); the app
    # then only verifies DB reachability at boot. See app.migrations_runtime.
    db_auto_migrate: bool = True

    # Free/premium infra toggles (ADR-14). Conservative free-tier-safe defaults.
    storage_provider: Literal["local", "cloudinary", "s3"] = "local"
    scheduler_mode: Literal["external_cron", "internal"] = "external_cron"

    # Shared secret guarding the internal machine endpoints (session reaper +
    # auth metrics) - ADR-15. On the free tier (`SCHEDULER_MODE=external_cron`) an
    # external scheduler (GitHub Actions / cron-job.org) calls
    # ``POST /api/v1/internal/run-jobs`` with this token in the
    # ``X-Internal-Job-Token`` header; the same token guards
    # ``GET /api/v1/internal/metrics``. Compared in constant time. Empty (the
    # zero-config local default) means the internal endpoints reject *every*
    # caller (no token can match), so they never expose data unauthenticated -
    # local dev never needs them (the reaper only runs where scheduled). The
    # ``internal`` premium loop does not use the token (it calls the reaper
    # in-process).
    internal_job_token: str = ""

    # Interval (seconds) between reaper batches when ``SCHEDULER_MODE=internal``
    # (the premium in-process scheduled worker). Hourly by default; bounded like
    # the other lifetimes so a typo can't create a zero/never interval.
    reaper_interval_seconds: int = 60 * 60  # 1 hour

    # =====================================================================
    # P2 Admin subsystem (design "Deployment")
    # =====================================================================
    # Master feature flag for the admin surface. When off, the guarded admin
    # router still mounts but every endpoint returns 404 ``admin_disabled`` (the
    # rollout "deploy flag-off -> enable read -> enable manage" step).
    admin_enabled: bool = True
    # Kill-switch for irreversible destructive actions (soft-delete + purge).
    # When off, delete/restore endpoints and the PurgeJob are refused/skipped -
    # the safe default for the initial rollout can flip this off to disable
    # destruction entirely without a redeploy of code (value change, ADR-14).
    admin_destructive_actions: bool = True
    # Grace-period length (days) between soft-delete and irreversible purge.
    # A soft-deleted user is restorable until ``deleted_at + this``; bounded to a
    # sane window so a typo can't create a zero (instant purge) or absurd delay.
    admin_delete_grace_days: int = 7
    # Bounded batch size for ``POST /users/bulk-disable`` (R6.4) - caps the
    # blast radius / request cost of a single bulk action.
    admin_bulk_disable_max: int = 100

    @field_validator(
        "admin_enabled",
        "admin_destructive_actions",
        mode="before",
    )
    @classmethod
    def _blank_admin_bool_to_default(cls, v: Any, info: Any) -> Any:
        """Treat a blank admin bool env var as the field default."""
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator("admin_delete_grace_days", mode="before")
    @classmethod
    def _validate_grace_days(cls, v: Any) -> int:
        """Coerce + bounds-check the grace period to [1, 365] days."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 7
        try:
            days = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 7
        return max(1, min(365, days))

    @field_validator("admin_bulk_disable_max", mode="before")
    @classmethod
    def _validate_bulk_max(cls, v: Any) -> int:
        """Coerce + bounds-check the bulk-disable cap to [1, 1000]."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 100
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 100
        return max(1, min(1000, n))

    # =====================================================================
    # Admin panel observability upgrade (design "admin-panel-upgrade")
    # =====================================================================
    # Tiered audit-log retention windows (Req 1.3/1.4/1.8). A Security_Critical
    # row is deleted once older than the hot window; a Downsamplable row is
    # aggregated-then-deleted once older than the downsample window. Positive
    # integers - a blank/typo env falls back to the documented default so the
    # retention job can never resolve a zero/never window.
    admin_audit_hot_days: int = 365
    admin_audit_downsample_days: int = 90
    # Max audit rows the retention job deletes per invocation (Req 1.7). Bounded
    # to [1, 100000] so a typo can't disable batching (0) or unbound the run.
    admin_audit_retention_batch: int = 1000
    # Age (days) beyond which the MetricsPruneStep deletes `metrics_daily` rows,
    # excluding the totals snapshot (Req 15.6). Positive integer.
    admin_metrics_retention_days: int = 400
    # Minimum interval (minutes) between DB-size samples taken by the rollup's
    # DbSizeSampleStep - the hourly KV guard that keeps sampling off the request
    # path (Req 7). Positive integer.
    admin_db_size_sample_minutes: int = 60
    # Stuck-job detection (Req 8.10): a running job is flagged potentially-stuck
    # when its current duration exceeds `multiplier × expected duration`, or the
    # absolute `ceiling` (seconds) when no expected duration exists. Positive
    # integers; computed from existing run markers, no new monitoring.
    admin_job_stuck_multiplier: int = 3
    admin_job_stuck_ceiling_seconds: int = 3600
    # Minimal threshold alerting (Req 12.5). Percentages are bounded to [0, 100];
    # the cooldown is a positive integer number of seconds an alert stays
    # suppressed while its condition remains continuously true. Every value is
    # read from config, never hard-coded.
    alert_storage_full_pct: int = 90
    alert_error_rate_pct: int = 5
    alert_cooldown_seconds: int = 3600

    @field_validator(
        "admin_audit_hot_days",
        "admin_audit_downsample_days",
        "admin_metrics_retention_days",
        "admin_db_size_sample_minutes",
        "admin_job_stuck_multiplier",
        "admin_job_stuck_ceiling_seconds",
        "alert_cooldown_seconds",
        mode="before",
    )
    @classmethod
    def _validate_admin_positive_int(cls, v: Any, info: Any) -> int:
        """Coerce a positive-int admin setting; blank/typo env -> field default."""
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(1, n)

    @field_validator("admin_audit_retention_batch", mode="before")
    @classmethod
    def _validate_audit_retention_batch(cls, v: Any) -> int:
        """Coerce + bounds-check the audit retention batch to [1, 100000]."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 1000
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 1000
        return max(1, min(100_000, n))

    @field_validator(
        "alert_storage_full_pct",
        "alert_error_rate_pct",
        mode="before",
    )
    @classmethod
    def _validate_alert_percent(cls, v: Any, info: Any) -> int:
        """Coerce + bounds-check an alert percentage to [0, 100]; blank -> default."""
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(0, min(100, n))

    # =====================================================================
    # P3 Productivity - Version history (design §A, Requirements 1-3)
    # =====================================================================
    # Master feature flag for the version-history surface. Off -> the router
    # mounts but every endpoint returns 404 and snapshot capture is skipped, so
    # the feature can be dark-launched / killed without a redeploy (ADR-14).
    version_history_enabled: bool = True
    # Max snapshots retained per resume; the oldest non-``original`` rows are
    # pruned beyond this (the ``original`` is always retained - R1.3). Bounded so
    # a typo can't disable pruning or store thousands of blobs per resume.
    version_history_cap: int = 50
    # Debounce window (seconds) coalescing rapid consecutive ``manual`` saves
    # into a single snapshot (R1.2). 0 disables debouncing.
    version_manual_debounce_seconds: int = 120

    @field_validator("version_history_enabled", mode="before")
    @classmethod
    def _blank_version_bool_to_default(cls, v: Any, info: Any) -> Any:
        """Treat a blank version-history bool env var as the field default."""
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator("version_history_cap", mode="before")
    @classmethod
    def _validate_version_cap(cls, v: Any) -> int:
        """Coerce + bounds-check the snapshot cap to [2, 500]."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 50
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 50
        return max(2, min(500, n))

    @field_validator("version_manual_debounce_seconds", mode="before")
    @classmethod
    def _validate_version_debounce(cls, v: Any) -> int:
        """Coerce + bounds-check the manual-save debounce to [0, 3600] seconds."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 120
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 120
        return max(0, min(3600, n))

    # =====================================================================
    # Professional Profile System (docs/architecture/PROFILE_SYSTEM_PLAN.md)
    # =====================================================================
    # Master flag for the profile surface. Off -> the router mounts but every
    # endpoint returns 404 and snapshot capture is skipped (dark-launch / kill
    # switch without a redeploy - ADR-14).
    profile_enabled: bool = True
    # Max profile snapshots retained per user; oldest non-first rows pruned.
    profile_history_cap: int = 50
    # Debounce window (seconds) coalescing rapid consecutive ``manual`` profile
    # saves into one snapshot. 0 disables debouncing.
    profile_manual_debounce_seconds: int = 120
    # Merge similarity backend: deterministic (default) | hybrid | embedding.
    # hybrid/embedding are inert without an injected semantic/embed fn (no vector
    # infra ships), falling back to deterministic - never fabricating a score.
    profile_similarity_provider: str = "deterministic"

    @field_validator("profile_enabled", mode="before")
    @classmethod
    def _blank_profile_bool_to_default(cls, v: Any, info: Any) -> Any:
        """Treat a blank profile bool env var as the field default."""
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator("profile_history_cap", mode="before")
    @classmethod
    def _validate_profile_cap(cls, v: Any) -> int:
        """Coerce + bounds-check the profile snapshot cap to [2, 500]."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 50
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 50
        return max(2, min(500, n))

    @field_validator("profile_manual_debounce_seconds", mode="before")
    @classmethod
    def _validate_profile_debounce(cls, v: Any) -> int:
        """Coerce + bounds-check the profile manual-save debounce to [0, 3600]."""
        if v is None or (isinstance(v, str) and not v.strip()):
            return 120
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return 120
        return max(0, min(3600, n))

    # =====================================================================
    # P3 Productivity - Notifications + shared platform (design §B/§Platform)
    # =====================================================================
    # Master flag for the global-search surface (router 404s when off).
    search_enabled: bool = True
    # P3 Avatar + profile (design §H). Storage creds (Cloudinary free default when
    # configured) + hardening caps. Local provider is the zero-config dev default.
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    avatar_max_bytes: int = 5 * 1024 * 1024
    avatar_max_dimension: int = 4096
    # Canonical profile-image master (Photo System). The master preserves aspect
    # ratio (no crop) and is downscaled so its longest edge ≤ this many pixels;
    # every render-time crop/shape and every responsive CDN variant derives from
    # it (no re-upload, original never mutated). 1024 keeps a crisp master for
    # high-DPI resume headers while staying small.
    image_master_max_dimension: int = 1024
    image_master_quality: int = 85

    @property
    def cloudinary_configured(self) -> bool:
        return bool(
            self.cloudinary_cloud_name.strip()
            and self.cloudinary_api_key.strip()
            and self.cloudinary_api_secret.strip()
        )

    @field_validator(
        "avatar_max_bytes",
        "avatar_max_dimension",
        "image_master_max_dimension",
        "image_master_quality",
        mode="before",
    )
    @classmethod
    def _validate_avatar_int(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(1, n)

    # PDF rendering resource bounds (headless Chromium is memory/CPU-heavy).
    # ``pdf_max_concurrency`` caps simultaneous renders (backpressure/DoS guard);
    # a request that can't get a slot within ``pdf_render_queue_timeout_seconds``
    # fails fast with 503 instead of piling up and exhausting memory.
    pdf_max_concurrency: int = 2
    pdf_render_queue_timeout_seconds: int = 30

    # Per-user rate limit (events/minute) for expensive LLM generation endpoints
    # (resume parse, cover letter, interview prep, enrichment, resume wizard,
    # JD-from-URL). Guards provider cost/abuse; 0 disables. Enforced via the
    # shared KVStore so it holds across workers/instances (see app.llm_ratelimit).
    llm_rate_per_min_user: int = 20

    @field_validator("pdf_max_concurrency", "pdf_render_queue_timeout_seconds", mode="before")
    @classmethod
    def _validate_pdf_int(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(1, n)

    @field_validator("llm_rate_per_min_user", mode="before")
    @classmethod
    def _validate_llm_rate(cls, v: Any, info: Any) -> int:
        # Allow 0 to disable; blank -> default; negative -> 0.
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(0, n)

    # P3 JD-from-URL (SSRF-hardened import). Kill-switch + rate/concurrency caps.
    jd_from_url_enabled: bool = True
    jd_v2_enabled: bool = True  # v2 extraction pipeline (cascade: API -> JSON-LD -> DOM)
    jd_url_rate_per_min_user: int = 10
    jd_url_rate_per_min_global: int = 120
    jd_url_max_concurrency: int = 4
    jd_url_cache_ttl_seconds: int = 3600
    jd_pipeline_timeout: int = 20  # Global timeout (seconds) for v2 pipeline

    # Phase 3 (Coverage & Polish) feature flags.
    jd_pdf_enabled: bool = True          # PDF extraction pipeline (§20)
    jd_ocr_enabled: bool = False         # Tesseract OCR fallback for scanned PDFs (opt-in; needs deps)
    jd_i18n_enabled: bool = True         # Language detection + i18n section keywords (§21)
    jd_robots_check_enabled: bool = True  # robots.txt policy check before fetch (§26)
    jd_cost_monitoring_enabled: bool = True  # Per-user/global cost caps (§25)
    jd_cost_user_daily_cap_usd: float = 0.5
    jd_cost_global_hourly_break_usd: float = 100.0

    # Phase 4 (Advanced) feature flags.
    jd_extension_fallback_enabled: bool = True   # Accept user's rendered DOM (browser-extension fallback)
    jd_ml_scoring_enabled: bool = False          # ML content scorer as an extra DOM confidence signal
    jd_webhook_enabled: bool = False             # Employer webhook ingestion (zero-scrape authoritative push)
    jd_webhook_secret: str = ""                  # HMAC-SHA256 shared secret for webhook auth (required when enabled)
    jd_distributed_render_max: int = 0           # Global concurrent Playwright renders across workers (0 = per-process only)
    jd_edge_render_url: str = ""                 # External edge-renderer endpoint (blank = local Playwright)

    @field_validator(
        "jd_from_url_enabled", "jd_pdf_enabled", "jd_ocr_enabled",
        "jd_i18n_enabled", "jd_robots_check_enabled", "jd_cost_monitoring_enabled",
        "jd_extension_fallback_enabled", "jd_ml_scoring_enabled", "jd_webhook_enabled",
        mode="before",
    )
    @classmethod
    def _blank_jd_bool_to_default(cls, v: Any, info: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator("jd_distributed_render_max", mode="before")
    @classmethod
    def _validate_jd_render_max(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(0, min(1000, n))

    @field_validator("jd_cost_user_daily_cap_usd", "jd_cost_global_hourly_break_usd", mode="before")
    @classmethod
    def _validate_jd_cost(cls, v: Any, info: Any) -> float:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            f = float(str(v).strip())
        except (TypeError, ValueError, OverflowError):
            return default
        return max(0.0, f)

    @field_validator(
        "jd_url_rate_per_min_user", "jd_url_rate_per_min_global",
        "jd_url_max_concurrency", "jd_url_cache_ttl_seconds", mode="before",
    )
    @classmethod
    def _validate_jd_int(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(1, min(100000, n))

    # P3 reminders / interviews / agenda feature flags + abuse caps.
    reminders_enabled: bool = True
    interviews_enabled: bool = True
    agenda_enabled: bool = True
    max_reminders_per_user: int = 500
    max_interviews_per_user: int = 500

    @field_validator("reminders_enabled", "interviews_enabled", "agenda_enabled", mode="before")
    @classmethod
    def _blank_sched_bool_to_default(cls, v: Any, info: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator("max_reminders_per_user", "max_interviews_per_user", mode="before")
    @classmethod
    def _validate_sched_cap(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        return max(1, min(100000, n))
    # Master flag for the notification surface (router 404s when off).
    notifications_enabled: bool = True
    # Kill-switch for notification *email* delivery (NOTIFICATIONS_EMAIL). Off ->
    # in-app still works; the email worker sends nothing (marks rows skipped).
    notifications_email_enabled: bool = True
    # Transport selector for the frontend badge/center: polling (free) vs sse
    # (premium). Data model + endpoints are identical; only delivery differs.
    sse_notifications: bool = False
    # Client poll interval (seconds) for the unread badge (active tab only).
    notification_poll_interval_seconds: int = 45
    # Retention windows (days). Read/dismissed notifications + processed outbox
    # rows older than these are pruned by the retention job (R17.4).
    notification_retention_days: int = 30
    outbox_retention_days: int = 7

    @field_validator("search_enabled", "notifications_enabled", "notifications_email_enabled", "sse_notifications", mode="before")
    @classmethod
    def _blank_notif_bool_to_default(cls, v: Any, info: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator(
        "notification_poll_interval_seconds",
        "notification_retention_days",
        "outbox_retention_days",
        mode="before",
    )
    @classmethod
    def _validate_notif_int(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        # Poll interval bounded [15, 300]s; retention windows [1, 3650] days.
        if info.field_name == "notification_poll_interval_seconds":
            return max(15, min(300, n))
        return max(1, min(3650, n))

    # =====================================================================
    # P4 Resilience - streaming AI, offline support, advanced autosave
    # =====================================================================
    # Independent ADR-14 free/premium toggles. Each area is dark-launchable and
    # has a documented off/rollback path (design §Deployment). Conservative
    # defaults: autosave on (pure win, degrades to local-draft), streaming and
    # offline off until explicitly enabled per the rollout order.
    streaming_ai_enabled: bool = False
    offline_support_enabled: bool = False
    advanced_autosave_enabled: bool = True

    # Streaming caps (R1.5): per-user concurrent streams, max lifetime, and the
    # heartbeat TTL after which an abandoned stream's slot is reclaimed and the
    # server-side task reaped. Bounded so a typo can't disable the guardrails.
    stream_max_concurrent_per_user: int = 3
    stream_max_lifetime_seconds: int = 300
    stream_heartbeat_seconds: int = 15

    @field_validator(
        "streaming_ai_enabled",
        "offline_support_enabled",
        "advanced_autosave_enabled",
        mode="before",
    )
    @classmethod
    def _blank_resilience_bool_to_default(cls, v: Any, info: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator(
        "stream_max_concurrent_per_user",
        "stream_max_lifetime_seconds",
        "stream_heartbeat_seconds",
        mode="before",
    )
    @classmethod
    def _validate_stream_int(cls, v: Any, info: Any) -> int:
        default = cls.model_fields[info.field_name].default
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        try:
            n = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError):
            return default
        # Concurrency [1, 20]; lifetime [30, 1800]s; heartbeat [5, 120]s.
        if info.field_name == "stream_max_concurrent_per_user":
            return max(1, min(20, n))
        if info.field_name == "stream_max_lifetime_seconds":
            return max(30, min(1800, n))
        return max(5, min(120, n))

    # Pluggable auth providers (ADR-14). Empty selects the shipped dev-safe
    # default adapter; a configured provider is constructed in ``app.auth.runtime``.
    email_provider: str = ""
    email_from: str = ""
    email_api_key: str = ""
    # SMTP transport settings (used when EMAIL_PROVIDER=smtp). All optional at
    # config load; the runtime factory gracefully degrades to the dev logging
    # sender (with a logged warning) when the host is missing so the app boots.
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_smtp_use_tls: bool = True
    # Recipient for public "Contact" form submissions. Falls back to EMAIL_FROM
    # when unset so the feature works out of the box once any sender is wired;
    # if neither is set the submission is still persisted + logged (no delivery).
    contact_recipient_email: str = ""
    captcha_provider: str = ""
    captcha_secret: str = ""
    captcha_site_key: str = ""
    breach_provider: str = ""

    @field_validator("email_verification", mode="before")
    @classmethod
    def _blank_email_verification_to_none(cls, v: Any) -> Any:
        """Treat a blank ``EMAIL_VERIFICATION=`` as unset (resolve by mode)."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator(
        "single_user_mode",
        "cookie_secure",
        "db_use_pooler",
        "db_auto_migrate",
        "email_smtp_use_tls",
        mode="before",
    )
    @classmethod
    def _blank_bool_to_default(cls, v: Any, info: Any) -> Any:
        """Treat a blank env var (e.g. ``SINGLE_USER_MODE=``) as the field default.

        These are non-nullable booleans, so a blank value must fall back to the
        default rather than becoming ``None`` (which would fail type validation).
        """
        if isinstance(v, str) and not v.strip():
            return cls.model_fields[info.field_name].default
        return v

    @field_validator(
        "session_absolute_ttl",
        "remember_me_ttl",
        "idle_ttl",
        "step_up_window",
        "reaper_interval_seconds",
        mode="before",
    )
    @classmethod
    def _validate_ttl(cls, v: Any, info: Any) -> int:
        """Coerce and bounds-check a lifetime value; reject non-positive/garbage."""
        if v is None or (isinstance(v, str) and not v.strip()):
            # Fall back to the field default rather than crashing on a blank var.
            return cls.model_fields[info.field_name].default
        try:
            seconds = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid {info.field_name.upper()}: {v!r} is not a number") from exc
        if not _MIN_TTL_SECONDS <= seconds <= _MAX_TTL_SECONDS:
            raise ValueError(
                f"Invalid {info.field_name.upper()}: {seconds}s is outside "
                f"[{_MIN_TTL_SECONDS}, {_MAX_TTL_SECONDS}]"
            )
        return seconds

    @field_validator(
        "argon2_time_cost", "argon2_memory_cost", "argon2_parallelism", mode="before"
    )
    @classmethod
    def _validate_argon2(cls, v: Any, info: Any) -> int:
        if v is None or (isinstance(v, str) and not v.strip()):
            return cls.model_fields[info.field_name].default
        try:
            value = int(float(str(v).strip()))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"Invalid {info.field_name.upper()}: {v!r} is not a number") from exc
        if value < 1:
            raise ValueError(f"Invalid {info.field_name.upper()}: must be >= 1")
        return value

    @model_validator(mode="after")
    def _validate_auth_surface(self) -> "Settings":
        """Fail-fast on invalid/missing-in-hosted config; safe defaults locally.

        Local (single-user) mode fills strong ephemeral secrets so the app runs
        with zero config; hosted mode requires the real secrets to be present so
        an insecure deployment can never boot silently (R14.3).
        """
        errors: list[str] = []

        # -- secrets: required in hosted, ephemeral in local ------------------
        for field_name, env_name in (
            ("session_secret", "SESSION_SECRET"),
            ("ip_hash_secret", "IP_HASH_SECRET"),
        ):
            value = getattr(self, field_name)
            if value:
                if len(value) < _MIN_SECRET_LENGTH:
                    errors.append(
                        f"{env_name} must be at least {_MIN_SECRET_LENGTH} characters"
                    )
            elif self.single_user_mode:
                # Generate a strong per-process ephemeral secret (never in hosted).
                object.__setattr__(self, field_name, secrets.token_urlsafe(32))
                logger.warning(
                    "%s not set; using an ephemeral per-process value "
                    "(local single-user mode only - sessions reset on restart).",
                    env_name,
                )
            else:
                errors.append(f"{env_name} is required when SINGLE_USER_MODE is off")

        if self.session_secret_prev and len(self.session_secret_prev) < _MIN_SECRET_LENGTH:
            errors.append(
                f"SESSION_SECRET_PREV must be at least {_MIN_SECRET_LENGTH} characters"
            )

        # -- API-key encryption secret: required in hosted so users' provider
        # keys survive restarts/redeploys. Without a stable secret the encrypted
        # keys in the DB become undecryptable after every release on an ephemeral
        # filesystem. Local (single-user) mode uses the persistent on-disk
        # ``data/.secret_key`` fallback, so the env var is optional there.
        if self.app_encryption_key:
            if len(self.app_encryption_key) < _MIN_SECRET_LENGTH:
                errors.append(
                    f"APP_ENCRYPTION_KEY must be at least {_MIN_SECRET_LENGTH} characters"
                )
        elif not self.single_user_mode:
            errors.append(
                "APP_ENCRYPTION_KEY is required when SINGLE_USER_MODE is off "
                "(without it, stored provider API keys cannot be decrypted after a "
                "restart/redeploy on an ephemeral filesystem)"
            )

        # -- internal job token: if set, must be long enough to be a real secret.
        # Left optional (not required in hosted) so a deployment can defer wiring
        # the reaper cron; when empty the internal endpoints reject every caller.
        if self.internal_job_token and len(self.internal_job_token) < _MIN_SECRET_LENGTH:
            errors.append(
                f"INTERNAL_JOB_TOKEN must be at least {_MIN_SECRET_LENGTH} characters"
            )

        # -- Google OAuth: all-or-nothing, redirect required when hosted -------
        has_id = bool(self.google_client_id.strip())
        has_secret = bool(self.google_client_secret.strip())
        if has_id != has_secret:
            errors.append(
                "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set together"
            )
        if has_id and has_secret:
            if not self.oauth_redirect_uri.strip():
                if not self.single_user_mode:
                    errors.append(
                        "OAUTH_REDIRECT_URI is required when Google OAuth is configured"
                    )
            elif not self.single_user_mode and not _is_https_or_loopback_http(
                self.oauth_redirect_uri.strip()
            ):
                errors.append(
                    "OAUTH_REDIRECT_URI must be an https:// URL in hosted mode "
                    "(http is allowed only for localhost/127.0.0.1/::1)"
                )

        # -- owner email sanity ------------------------------------------------
        if self.owner_email and "@" not in self.owner_email:
            errors.append(f"OWNER_EMAIL is not a valid email: {self.owner_email!r}")

        # -- lifetime ordering invariants -------------------------------------
        if self.idle_ttl > self.session_absolute_ttl:
            errors.append(
                "IDLE_TTL must be <= SESSION_ABSOLUTE_TTL "
                f"({self.idle_ttl} > {self.session_absolute_ttl})"
            )
        if self.remember_me_ttl < self.session_absolute_ttl:
            errors.append(
                "REMEMBER_ME_TTL must be >= SESSION_ABSOLUTE_TTL "
                f"({self.remember_me_ttl} < {self.session_absolute_ttl})"
            )

        # -- Argon2 memory must satisfy the p relationship --------------------
        if self.argon2_memory_cost < 8 * self.argon2_parallelism:
            errors.append(
                "ARGON2_MEMORY_COST must be >= 8 * ARGON2_PARALLELISM "
                f"({self.argon2_memory_cost} < {8 * self.argon2_parallelism})"
            )

        # -- hosted requires Postgres (ADR-13; ephemeral disk wipes SQLite) ---
        if not self.single_user_mode:
            url = self.database_url.strip().lower()
            if not url or url.startswith("sqlite"):
                errors.append(
                    "DATABASE_URL must be a Postgres URL when SINGLE_USER_MODE is off "
                    "(SQLite is local-dev only - ADR-13)"
                )

        # -- background-jobs / scheduler wiring must actually be able to run ---
        # (hosted only; local single-user never needs the reaper/notifier).
        if not self.single_user_mode:
            kv = (self.kvstore_url or "").strip().lower()
            kv_is_local = kv in _KVSTORE_LOCAL_ALIASES

            # external_cron drives ALL background work (reaper, search indexing,
            # notification emails, reminders, retention, purge) through
            # POST /api/v1/internal/run-jobs, which rejects every caller when no
            # INTERNAL_JOB_TOKEN is set -> those jobs could NEVER run. Fail fast.
            if self.scheduler_mode == "external_cron" and not self.internal_job_token.strip():
                errors.append(
                    "SCHEDULER_MODE=external_cron requires INTERNAL_JOB_TOKEN so your "
                    "cron can call POST /api/v1/internal/run-jobs; without it, session "
                    "reaping, search indexing, notification/reminder emails, retention "
                    "and purge never run. Set INTERNAL_JOB_TOKEN (>=16 chars) or switch "
                    "to SCHEDULER_MODE=internal with a shared KVSTORE_URL."
                )

            # internal scheduler relies on a SHARED single-flight lock; an
            # in-process KVStore means every replica runs the jobs (split-brain,
            # duplicate work). Require a shared store.
            if self.scheduler_mode == "internal" and kv_is_local:
                errors.append(
                    "SCHEDULER_MODE=internal requires a shared KVSTORE_URL "
                    "(redis:// / rediss://): the reaper/retention single-flight lock "
                    "must be cluster-wide, but an in-process KVStore split-brains "
                    "across replicas (duplicate job runs)."
                )

            # Shared abuse-control + session-cache state is per-process with a
            # local KVStore -> weakened once you run >1 worker/instance. A single
            # container is valid, so warn (not fatal).
            if kv_is_local:
                logger.warning(
                    "Hosted mode with an in-process KVStore (KVSTORE_URL blank): rate "
                    "limits, account lockouts, CAPTCHA gating and the session cache are "
                    "per-process and will NOT be shared across multiple workers/"
                    "instances. Set KVSTORE_URL=redis:// / rediss:// before scaling out."
                )

            # Email verification enabled but no real delivery provider -> users
            # can never receive verification/reset links (only the dev log sees
            # them). Recoverable/product-dependent, so warn loudly (not fatal).
            if self.email_verification_enabled:
                provider = (self.email_provider or "").strip().lower()
                delivers = (
                    provider == "smtp"
                    and bool(self.email_smtp_host.strip())
                    and bool(self.email_from.strip())
                ) or (
                    provider == "resend"
                    and bool(self.email_api_key.strip())
                    and bool(self.email_from.strip())
                )
                if not delivers:
                    logger.warning(
                        "Email verification is ENABLED but no delivery provider is "
                        "configured (EMAIL_PROVIDER/EMAIL_FROM/creds): verification and "
                        "password-reset links will only be written to the server log, "
                        "never delivered. Configure EMAIL_PROVIDER=smtp|resend or set "
                        "EMAIL_VERIFICATION=false."
                    )

        if errors:
            raise ValueError(
                "Invalid auth configuration:\n  - " + "\n  - ".join(errors)
            )
        return self

    @property
    def email_verification_enabled(self) -> bool:
        """Resolved email-verification flag: ON hosted, OFF single-user (R5, R14.3)."""
        if self.email_verification is None:
            return not self.single_user_mode
        return self.email_verification

    @property
    def google_oauth_configured(self) -> bool:
        """Whether Google OAuth credentials are present (both id and secret)."""
        return bool(self.google_client_id.strip() and self.google_client_secret.strip())

    @property
    def resolved_profile(self):
        """The active :class:`~app.platform.profiles.DeploymentProfile`.

        Explicit ``deployment_profile`` wins; otherwise derived from
        ``single_user_mode`` (ARCHITECTURE §3, IMPLEMENTATION_PLAN Phase 1).
        Imported lazily to keep ``platform`` out of ``config`` import time.
        """
        from app.platform.profiles import resolve_profile

        return resolve_profile(self)

    # Paths
    data_dir: Path = Path(__file__).parent.parent / "data"

    @property
    def db_path(self) -> Path:
        """Path to the legacy TinyDB database file (migration source only)."""
        return self.data_dir / "database.json"

    @property
    def sqlite_path(self) -> Path:
        """Path to the SQLite database file (primary data store)."""
        return self.data_dir / "resume_matcher.db"

    @property
    def config_path(self) -> Path:
        """Path to config storage file."""
        return self.data_dir / "config.json"

    @property
    def effective_database_url(self) -> str:
        """Resolved database URL (ADR-13).

        When ``DATABASE_URL`` is unset, fall back to the local SQLite file so
        local dev keeps working with zero config; hosted supplies a pooled
        Postgres URL. The migration/engine wiring consumes this so there is a
        single source of truth for which database the app talks to.
        """
        url = self.database_url.strip()
        if url:
            return url
        return f"sqlite+aiosqlite:///{self.sqlite_path}"

    def get_effective_api_key(self) -> str:
        """Get the effective API key with config file fallback.

        Priority: Environment/settings value > config.json > empty string
        """
        if self.llm_api_key:
            return self.llm_api_key
        return _get_llm_api_key_with_fallback()


settings = Settings()
