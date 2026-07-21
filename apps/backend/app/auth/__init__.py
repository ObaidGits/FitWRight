"""Authentication foundation package (P1 Multi-User Foundation).

This package houses the pluggable infrastructure the auth flows are built on:

- ``kvstore`` - a pluggable key/value store (session cache, rate-limit counters,
  transient OAuth state, single-flight locks) with in-process, Redis/Upstash,
  and DB-backed adapters selected by ``KVSTORE_URL`` (ADR-6).
- ``email`` - a pluggable ``EmailSender`` (default: dev console/log adapter).
- ``captcha`` - a pluggable ``CaptchaVerifier`` (default: fail-open allow).
- ``breach`` - a pluggable ``BreachedPasswordCheck`` (default: fail-open allow).

Later waves add passwords, sessions, csrf, oauth, verification, reset, ratelimit,
stepup, principal, and audit modules alongside these (see design.md).
"""

from app.auth.breach import (
    BreachedPasswordCheck,
    BreachResult,
    HibpBreachedPasswordCheck,
    HttpxHibpRangeClient,
    NoopBreachedPasswordCheck,
)
from app.auth.captcha import (
    AllowAllCaptchaVerifier,
    CaptchaResult,
    CaptchaVerifier,
    HttpxSiteverifyClient,
    TurnstileCaptchaVerifier,
)
from app.auth.email import (
    EmailMessage,
    EmailSender,
    HttpxResendClient,
    LoggingEmailSender,
    ResendEmailSender,
    SmtpEmailSender,
)
from app.auth.kvstore import (
    DBKVStore,
    KVLock,
    KVStore,
    LocalKVStore,
    RedisKVStore,
    kvstore_from_url,
    url_needs_db_engine,
)
from app.auth.audit import (
    AuditEvent,
    AuditService,
    get_audit_service,
    sanitize_log_value,
    sanitize_meta,
)
from app.auth.csrf import (
    derive_csrf_token,
    issue_presession_token,
    presession_double_submit_ok,
    validate_next_path,
    verify_csrf_token,
    verify_presession_token,
)
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyResult,
    PasswordService,
    get_password_service,
)
from app.auth.principal import (
    AuthMiddleware,
    Capabilities,
    Principal,
    SecurityHeadersMiddleware,
    auth_csrf_router,
    capabilities_for,
    clear_session_cookies,
    get_effective_user_id,
    get_optional_principal,
    get_principal,
    require_capability,
    require_step_up,
    require_verified_user_id,
    set_session_cookies,
)
from app.auth.ratelimit import (
    AUTH_RULES,
    LockoutResult,
    RateLimiter,
    RateLimitResult,
    RateLimitRule,
    get_rate_limiter,
)
from app.auth.runtime import (
    build_breached_password_check,
    build_captcha_verifier,
    build_email_sender,
    build_kvstore,
    close_kvstore,
    get_breached_password_check,
    get_captcha_verifier,
    get_email_sender,
    get_kvstore,
)
from app.auth.sessions import (
    ResolvedSession,
    SessionInfo,
    SessionService,
    get_session_service,
    hash_token,
    parse_device_label,
)
from app.auth.tokens import (
    TokenConsumeResult,
    TokenService,
    get_token_service,
    hash_token_value,
)

__all__ = [
    # KVStore
    "KVStore",
    "KVLock",
    "LocalKVStore",
    "DBKVStore",
    "RedisKVStore",
    "kvstore_from_url",
    "url_needs_db_engine",
    # Runtime wiring (singletons + dependency callables)
    "build_kvstore",
    "build_email_sender",
    "build_captcha_verifier",
    "build_breached_password_check",
    "get_kvstore",
    "get_email_sender",
    "get_captcha_verifier",
    "get_breached_password_check",
    "close_kvstore",
    # Email
    "EmailSender",
    "EmailMessage",
    "LoggingEmailSender",
    "SmtpEmailSender",
    "ResendEmailSender",
    "HttpxResendClient",
    # Captcha
    "CaptchaVerifier",
    "CaptchaResult",
    "AllowAllCaptchaVerifier",
    "TurnstileCaptchaVerifier",
    "HttpxSiteverifyClient",
    # Breached password
    "BreachedPasswordCheck",
    "BreachResult",
    "NoopBreachedPasswordCheck",
    "HibpBreachedPasswordCheck",
    "HttpxHibpRangeClient",
    # Passwords (Task 2.1)
    "PasswordService",
    "PasswordPolicyResult",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "get_password_service",
    # Sessions (Task 2.2)
    "SessionService",
    "SessionInfo",
    "ResolvedSession",
    "hash_token",
    "parse_device_label",
    "get_session_service",
    # Single-use tokens (Task 5)
    "TokenService",
    "TokenConsumeResult",
    "hash_token_value",
    "get_token_service",
    # CSRF + next validation (Task 2.3)
    "derive_csrf_token",
    "verify_csrf_token",
    "issue_presession_token",
    "verify_presession_token",
    "presession_double_submit_ok",
    "validate_next_path",
    # Principal / RBAC / middleware (Task 2.3)
    "Principal",
    "Capabilities",
    "capabilities_for",
    "get_principal",
    "get_optional_principal",
    "get_effective_user_id",
    "require_verified_user_id",
    "require_capability",
    "require_step_up",
    "SecurityHeadersMiddleware",
    "AuthMiddleware",
    "set_session_cookies",
    "clear_session_cookies",
    "auth_csrf_router",
    # Rate limiting + audit (Task 2.4)
    "RateLimiter",
    "RateLimitRule",
    "RateLimitResult",
    "LockoutResult",
    "AUTH_RULES",
    "get_rate_limiter",
    "AuditService",
    "AuditEvent",
    "sanitize_meta",
    "sanitize_log_value",
    "get_audit_service",
]
