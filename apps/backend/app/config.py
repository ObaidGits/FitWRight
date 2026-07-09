"""Application configuration using pydantic-settings."""

import json
import logging
import secrets
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

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
    before writing — they belong to the encrypted store only.
    """
    config = dict(config)
    config.pop("api_keys", None)
    config.pop("api_key", None)
    _write_config_json(config)


def resolve_key_user_id(user_id: str | None = None) -> str:
    """Resolve the user whose encrypted key store to read/write (ADR-4, R10.6).

    Priority: an explicit ``user_id`` (a request thread it) → the request-scoped
    effective user id published by the auth dependency → the bootstrap owner
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

    # Legacy plaintext keys are pre-multi-user data → they belong to the
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

    # Hard timeout (seconds) for a single resume tailoring/improve request — the
    # backend wraps the improve flow in asyncio.wait_for(timeout=this). It MUST be
    # kept in sync with the two frontend layers (Next.js `proxyTimeout` and the
    # client AbortController, both driven by NEXT_PUBLIC_REQUEST_TIMEOUT_MS):
    # whichever layer is shortest aborts first, so raising only one silently fails
    # (this is why issue #776's backend-only workaround didn't work). Local LLMs
    # (Ollama, llama.cpp, …) often need longer than the 240s default; bounded to
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
    # param" — the default for maximum compatibility. LiteLLM drops this
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

    # Session signing / CSRF derivation secret (+ previous key for zero-downtime
    # rotation, R16.3). Required in hosted mode; in local mode a strong ephemeral
    # value is generated per process (never persisted, never used in hosted).
    session_secret: str = ""
    session_secret_prev: str = ""

    # Keyed HMAC secret for hashing client IPs in sessions/audit (R12.5). Same
    # required-in-hosted / ephemeral-in-local rules as ``session_secret``.
    ip_hash_secret: str = ""

    # Google OAuth (provider-abstracted, R4). All optional — OAuth is only wired
    # when both client id and secret are present. A partial pair is rejected.
    google_client_id: str = ""
    google_client_secret: str = ""
    # Exact-match allow-listed redirect URI (R4.7). Required when OAuth is
    # configured on a hosted deployment.
    oauth_redirect_uri: str = ""

    # Bootstrap owner (migration 0004 assigns all existing data to this user).
    owner_email: str = "owner@localhost"
    owner_password: str = ""

    # Email verification (R5). Tri-state: unset → default ON for hosted, OFF for
    # single-user/local. Read the resolved value via ``email_verification_enabled``.
    email_verification: bool | None = None

    # Session lifetimes in seconds (R3.3, R17.1). Absolute cap, the longer cap
    # used when "remember me" is chosen (R2.6), and the idle timeout.
    session_absolute_ttl: int = 60 * 60 * 12  # 12 hours
    remember_me_ttl: int = 60 * 60 * 24 * 30  # 30 days
    idle_ttl: int = 60 * 60 * 2  # 2 hours

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

    # Pluggable shared-state store (ADR-6). Empty → in-process local adapter
    # (single-worker dev). ``redis://``/``rediss://`` → Redis/Upstash. ``db`` →
    # DB-backed fallback (no Redis at all).
    kvstore_url: str = ""

    # Primary database (ADR-13). Empty → the local SQLite file (unchanged
    # behavior). Hosted MUST set a Postgres URL (ephemeral disk wipes SQLite).
    database_url: str = ""
    db_pool_size: int = 5
    db_use_pooler: bool = True

    # Free/premium infra toggles (ADR-14). Conservative free-tier-safe defaults.
    storage_provider: Literal["local", "cloudinary", "s3"] = "local"
    scheduler_mode: Literal["external_cron", "internal"] = "external_cron"

    # Shared secret guarding the internal machine endpoints (session reaper +
    # auth metrics) — ADR-15. On the free tier (`SCHEDULER_MODE=external_cron`) an
    # external scheduler (GitHub Actions / cron-job.org) calls
    # ``POST /api/v1/internal/run-jobs`` with this token in the
    # ``X-Internal-Job-Token`` header; the same token guards
    # ``GET /api/v1/internal/metrics``. Compared in constant time. Empty (the
    # zero-config local default) means the internal endpoints reject *every*
    # caller (no token can match), so they never expose data unauthenticated —
    # local dev never needs them (the reaper only runs where scheduled). The
    # ``internal`` premium loop does not use the token (it calls the reaper
    # in-process).
    internal_job_token: str = ""

    # Interval (seconds) between reaper batches when ``SCHEDULER_MODE=internal``
    # (the premium in-process scheduled worker). Hourly by default; bounded like
    # the other lifetimes so a typo can't create a zero/never interval.
    reaper_interval_seconds: int = 60 * 60  # 1 hour

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
                    "(local single-user mode only — sessions reset on restart).",
                    env_name,
                )
            else:
                errors.append(f"{env_name} is required when SINGLE_USER_MODE is off")

        if self.session_secret_prev and len(self.session_secret_prev) < _MIN_SECRET_LENGTH:
            errors.append(
                f"SESSION_SECRET_PREV must be at least {_MIN_SECRET_LENGTH} characters"
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
            elif not self.single_user_mode and not self.oauth_redirect_uri.strip().startswith(
                "https://"
            ):
                errors.append("OAUTH_REDIRECT_URI must be an https:// URL in hosted mode")

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
                    "(SQLite is local-dev only — ADR-13)"
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
