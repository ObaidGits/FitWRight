"""Email/password auth + session API (Task 4.1).

Endpoints (all under ``/api/v1``, ADR-7 envelope):

- ``POST /auth/signup`` — create a user (+ session, or ``pending_verification``
  when email verification is on). Enumeration-safe: uniform response + timing
  (dummy Argon2 on the existing-email branch), no account disclosure (R1.*,
  Property 4).
- ``POST /auth/login`` — session with optional remember-me. Requires the
  pre-session CSRF token from ``GET /auth/csrf`` (login-CSRF defense), returns a
  single generic ``invalid_credentials`` for both unknown emails and wrong
  passwords (constant-time verify + dummy hash), rotates the session id
  (fixation defense), and applies rate-limit + lockout (R2.*).
- ``POST /auth/logout`` — revoke the current session + evict its cache and clear
  cookies. CSRF-protected (per-session double-submit, enforced by
  ``AuthMiddleware`` in hosted mode) (R3.1).
- ``POST /auth/logout-all`` — revoke every session for the user; requires a
  recent step-up (R3.2, R9.1).
- ``GET /auth/session`` — the caller's ``SafeUser`` (+ ``aal``) or 401.

Only :class:`~app.schemas.auth.SafeUser` is ever returned for a user (R7.5).
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.auth import (
    Principal,
    clear_session_cookies,
    get_email_sender,
    get_optional_principal,
    get_password_service,
    get_session_service,
    get_token_service,
    presession_double_submit_ok,
    set_session_cookies,
    validate_next_path,
)
from app.auth.oauth import (
    OAUTH_TXN_COOKIE,
    OAUTH_TXN_TTL_SECONDS,
    OAuthError,
    OAuthTransaction,
    ProviderNotConfigured,
    UnknownProvider,
    deserialize_transaction,
    generate_nonce,
    generate_pkce_verifier,
    generate_state,
    link_or_create_user,
    pkce_challenge,
    registry as oauth_registry,
    serialize_transaction,
)
from app.auth.oauth.linking import LinkAction
from app.auth.accounts import (
    create_user,
    get_by_email,
    get_by_id,
    get_password_hash,
    mark_email_verified,
    normalize_email,
    set_password_hash,
)
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.email import (
    build_password_reset_email,
    build_verification_email,
    send_email_safe,
)
from app.auth.metrics import get_metrics
from app.auth.ratelimit import get_rate_limiter
from app.config import settings
from app.errors import ApiError
from app.routers._auth_deps import client_ip, require_session, require_stepped_up_session
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SafeUser,
    SignupPendingResponse,
    SignupRequest,
    StepUpRequest,
    UniformAckResponse,
    VerificationConfirmRequest,
    VerificationRequestRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _rate_limited(retry_after: int) -> ApiError:
    """Build a 429 ``rate_limited`` envelope with a ``Retry-After`` header.

    Uniform regardless of whether the account exists (no enumeration, R13.4): the
    same status/code/``Retry-After`` shape is returned for a per-IP limit, a
    per-account lockout, or a KVStore-outage fail-closed denial.
    """
    get_metrics().record_rate_limited()
    return ApiError(
        429,
        "rate_limited",
        "Too many requests. Please try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


async def _enforce_captcha(
    limiter, failures: int, token: str | None, *, remote_ip: str | None
) -> None:
    """Require + verify a CAPTCHA once past the soft threshold (R13.2).

    Below the soft failure threshold nothing is required. Past it, the pluggable
    verifier decides — which, when no provider is configured, **fails open**
    (allows) by design. A configured verifier that rejects (missing/invalid
    token) raises ``403 captcha_required``. The decision depends only on the
    windowed failure count + the submitted token, never on whether the account
    exists, so it is enumeration-safe.
    """
    result = await limiter.captcha_gate(failures, token, remote_ip=remote_ip)
    if not result.allowed:
        get_metrics().record_captcha_required()
        raise ApiError(
            403,
            "captcha_required",
            "Please complete the verification challenge and try again.",
        )


def _verify_presession_csrf(request: Request) -> None:
    """Enforce the pre-session double-submit CSRF token (login-CSRF, R2.5/12.2).

    login/signup happen before a session exists, so the per-session middleware
    check does not apply; the client must first ``GET /auth/csrf`` and echo the
    token in both the ``csrf`` cookie and the ``X-CSRF-Token`` header.
    """
    cookie = request.cookies.get(settings.csrf_cookie_name)
    header = request.headers.get("X-CSRF-Token")
    if not presession_double_submit_ok(
        cookie, header, settings.session_secret, secret_prev=settings.session_secret_prev
    ):
        raise ApiError(403, "csrf_failed", "Invalid or missing CSRF token.")


def _safe_user(principal_or_record, *, aal: str) -> SafeUser:
    """Project an :class:`AccountRecord` into a ``SafeUser`` (never leaks a hash)."""
    rec = principal_or_record
    return SafeUser.build(
        id=rec.id,
        name=rec.name,
        email=rec.email,
        role=rec.role,
        status=rec.status,
        email_verified=rec.email_verified,
        aal=aal,
        avatar_url=rec.avatar_url,
    )


# ---------------------------------------------------------------------------
# POST /auth/signup
# ---------------------------------------------------------------------------


@router.post("/signup", response_model=None)
async def signup(request: Request, response: Response, payload: SignupRequest):
    """Create an account (enumeration-safe, uniform response + timing)."""
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("signup", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    _verify_presession_csrf(request)

    # Past a soft threshold of signup attempts from this IP, require a CAPTCHA
    # (when a provider is configured; fail-open otherwise) — abuse control R13.2.
    await _enforce_captcha(limiter, rl.count, payload.captcha_token, remote_ip=ip)

    passwords = get_password_service()
    # Password policy is evaluated first: it depends only on the *submitted*
    # password, so failing here reveals nothing about whether the email exists.
    policy = await passwords.validate_new_password(
        payload.password, email=str(payload.email), name=payload.name
    )
    if not policy.ok:
        raise ApiError(
            400,
            policy.code,
            "Password does not meet the security policy.",
            details={"unmet": list(policy.unmet)},
        )

    existing = await get_by_email(str(payload.email))
    audit = get_audit_service()
    ip_hash = get_session_service().hash_ip(ip)

    if settings.email_verification_enabled:
        # Hosted: never disclose existence. Both branches do exactly one Argon2
        # operation and return the identical pending response (Property 4).
        if existing is None:
            hashed = passwords.hash_password(payload.password)
            record = await create_user(
                email=str(payload.email),
                name=payload.name,
                password_hash=hashed,
                status="pending_verification",
            )
            await audit.record(
                AuditEvent.SIGNUP, actor_user_id=record.id, ip_hash=ip_hash
            )
            get_metrics().record_signup()
        else:
            # Existing email: run the dummy hash so timing matches the create
            # branch, then fall through to the same response.
            passwords.verify_password(None, payload.password)
        return SignupPendingResponse()

    # Verification off (single-user/local): sign the user in immediately (R1.4).
    if existing is not None:
        passwords.verify_password(None, payload.password)  # equalize timing
        raise ApiError(409, "email_unavailable", "That email is unavailable.")

    hashed = passwords.hash_password(payload.password)
    from datetime import datetime, timezone

    record = await create_user(
        email=str(payload.email),
        name=payload.name,
        password_hash=hashed,
        status="active",
        email_verified_at=datetime.now(timezone.utc).isoformat(),
    )
    session_service = get_session_service()
    raw_token, info = await session_service.create_session(
        record.id, remember_me=False, ip=ip, user_agent=request.headers.get("user-agent")
    )
    _set_cookies(response, raw_token, info, remember_me=False)
    await audit.record(AuditEvent.SIGNUP, actor_user_id=record.id, ip_hash=ip_hash)
    get_metrics().record_signup()
    return _safe_user(record, aal=info.aal)


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------


@router.post("/login", response_model=SafeUser)
async def login(request: Request, response: Response, payload: LoginRequest) -> SafeUser:
    """Authenticate and start a fresh session (fixation-safe)."""
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("login", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    _verify_presession_csrf(request)

    normalized = normalize_email(str(payload.email))
    account_key = f"login:{normalized}"
    lock = await limiter.is_locked_out(account_key)
    if lock.locked:
        raise _rate_limited(lock.retry_after)

    # Past the soft failure threshold for this account, require a CAPTCHA (when a
    # provider is configured; fail-open otherwise). The failure count is keyed on
    # the normalized email whether or not the account exists, so this stays
    # enumeration-safe (R13.2/13.4).
    await _enforce_captcha(limiter, lock.failures, payload.captcha_token, remote_ip=ip)

    existing = await get_by_email(normalized)
    passwords = get_password_service()
    session_service = get_session_service()
    audit = get_audit_service()
    ip_hash = session_service.hash_ip(ip)

    stored_hash = await get_password_hash(existing.id) if existing is not None else None
    # Always runs an Argon2 verify (dummy hash when the account/hash is absent)
    # so unknown emails are not measurably faster (Property 4, R2.2).
    verified = passwords.verify_password(stored_hash, payload.password)

    if not verified:
        result = await limiter.register_failure(account_key)
        await audit.record(
            AuditEvent.LOGIN_FAILED,
            target_user_id=existing.id if existing else None,
            ip_hash=ip_hash,
        )
        get_metrics().record_login_failure()
        if result.locked:
            get_metrics().record_lockout()
            raise _rate_limited(result.retry_after)
        raise ApiError(401, "invalid_credentials", "Invalid email or password.")

    # Correct password but the account cannot be used. Only reachable by someone
    # who already knows the password, so surfacing the reason is acceptable (R2.4).
    if existing is not None and existing.status != "active":
        await audit.record(
            AuditEvent.LOGIN_FAILED, target_user_id=existing.id, ip_hash=ip_hash
        )
        raise ApiError(403, "account_disabled", "This account is disabled.")

    assert existing is not None  # verified=True implies a real account + hash
    await limiter.clear_failures(account_key)

    # Session-fixation defense: never reuse an id the client already holds — mint
    # a brand-new session (and revoke any pre-existing one) on every login (R2.1).
    old_token = request.cookies.get(settings.session_cookie_name)
    if old_token:
        try:
            await session_service.revoke_by_token(old_token)
        except Exception:  # pragma: no cover - revoke must not block login
            logger.debug("Failed to revoke pre-login session", exc_info=True)

    raw_token, info = await session_service.create_session(
        existing.id,
        remember_me=payload.remember_me,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookies(response, raw_token, info, remember_me=payload.remember_me)
    await audit.record(AuditEvent.LOGIN, actor_user_id=existing.id, ip_hash=ip_hash)
    get_metrics().record_login_success()
    return _safe_user(existing, aal=info.aal)


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(require_session),
) -> MessageResponse:
    """Revoke the current session (+ cache eviction) and clear cookies (R3.1)."""
    session_service = get_session_service()
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        await session_service.revoke_by_token(raw_token)
    clear_session_cookies(response)
    await get_audit_service().record(AuditEvent.LOGOUT, actor_user_id=principal.user_id)
    return MessageResponse(message="Logged out.")


# ---------------------------------------------------------------------------
# POST /auth/logout-all
# ---------------------------------------------------------------------------


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    response: Response,
    principal: Principal = Depends(require_stepped_up_session),
) -> MessageResponse:
    """Revoke every session for the user; requires a recent step-up (R3.2/9.1)."""
    session_service = get_session_service()
    count = await session_service.revoke_all_for_user(principal.user_id)
    clear_session_cookies(response)
    await get_audit_service().record(
        AuditEvent.LOGOUT_ALL, actor_user_id=principal.user_id, meta={"revoked": count}
    )
    return MessageResponse(message="All sessions revoked.", count=count)


# ---------------------------------------------------------------------------
# GET /auth/session
# ---------------------------------------------------------------------------


@router.get("/session", response_model=SafeUser)
async def current_session(request: Request) -> SafeUser:
    """Return the caller's ``SafeUser`` (+ ``aal``), or 401 if unauthenticated."""
    principal = get_optional_principal(request)
    if principal is None:
        raise ApiError(401, "unauthorized", "Not authenticated.")
    return SafeUser.build(
        id=principal.user_id,
        name=principal.name,
        email=principal.email,
        role=principal.role,
        status=principal.status,
        email_verified=principal.email_verified,
        aal=principal.aal,
    )


def _set_cookies(response: Response, raw_token: str, info, *, remember_me: bool) -> None:
    """Set the ``__Host-`` session + per-session CSRF cookies for a new session."""
    max_age = settings.remember_me_ttl if remember_me else settings.session_absolute_ttl
    set_session_cookies(
        response,
        raw_token=raw_token,
        session_id=info.id,
        csrf_secret=info.csrf_secret,
        max_age=max_age,
    )


# ---------------------------------------------------------------------------
# POST /auth/verify/request  (email verification — send/resend)
# ---------------------------------------------------------------------------


@router.post("/verify/request", response_model=UniformAckResponse)
async def verify_request(
    request: Request, payload: VerificationRequestRequest
) -> UniformAckResponse:
    """(Re)send an email-verification link — uniform, enumeration-safe (R5.1/5.3/5.5).

    Resolves the target account from the authenticated session (if any) or the
    submitted email. Rate-limited per IP + per account. When the account exists
    and is still unverified, a hashed single-use TTL token is issued (invalidating
    prior unused tokens) and emailed. The response is always the same
    acknowledgement, so it never reveals whether the address is registered
    (Property 4).
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("verify", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    principal = get_optional_principal(request)
    if principal is not None:
        record = await get_by_id(principal.user_id)
    elif payload.email:
        record = await get_by_email(str(payload.email))
    else:
        # No session and no email supplied: still uniform (nothing to disclose).
        return UniformAckResponse()

    if record is not None:
        # Per-account rate limit (email amplification defense, R5.3).
        acct_rl = await limiter.check("verify", f"acct:{record.id}")
        if not acct_rl.allowed:
            raise _rate_limited(acct_rl.retry_after)
        # Only issue for an account that still needs verifying; an already-verified
        # account silently no-ops so the response stays uniform.
        if not record.email_verified:
            raw_token = await get_token_service().issue_verification(record.id)
            # Fail-safe: a provider outage must not 500 (which would leak that the
            # address is registered) — the response stays a uniform ack (design
            # §Reliability). The token is already persisted, so a resend still works.
            await send_email_safe(
                get_email_sender(),
                build_verification_email(
                    to=record.email,
                    raw_token=raw_token,
                    base_url=settings.frontend_base_url,
                ),
            )
            get_metrics().record_verification_sent()
    return UniformAckResponse()


# ---------------------------------------------------------------------------
# POST /auth/verify/confirm  (email verification — redeem token)
# ---------------------------------------------------------------------------


@router.post("/verify/confirm", response_model=UniformAckResponse)
async def verify_confirm(
    request: Request, payload: VerificationConfirmRequest
) -> UniformAckResponse:
    """Redeem a verification token: mark verified + activate (R5.2).

    Single-use — the token is consumed atomically. A missing/used/expired token
    all collapse to one generic ``invalid_token`` error (uniform, R5.5). On
    success the account's ``email_verified_at`` is set and a
    ``pending_verification`` account transitions to ``active``.
    """
    result = await get_token_service().consume_verification(payload.token)
    if not result.ok:
        raise ApiError(400, "invalid_token", "This verification link is invalid or has expired.")

    record = await mark_email_verified(result.user_id)
    if record is None:  # pragma: no cover - token FK implies a live user
        raise ApiError(400, "invalid_token", "This verification link is invalid or has expired.")

    await get_audit_service().record(
        AuditEvent.EMAIL_VERIFIED,
        actor_user_id=record.id,
        ip_hash=get_session_service().hash_ip(client_ip(request)),
    )
    get_metrics().record_verification_confirmed()
    return UniformAckResponse(status="verified")


# ---------------------------------------------------------------------------
# POST /auth/password/forgot  (request a reset link)
# ---------------------------------------------------------------------------


@router.post("/password/forgot", response_model=UniformAckResponse)
async def password_forgot(
    request: Request, payload: ForgotPasswordRequest
) -> UniformAckResponse:
    """Request a password-reset link — uniform, enumeration-safe (R6.1/6.5).

    Always returns the same acknowledgement. Only when the email is registered is
    a hashed single-use short-TTL reset token issued (invalidating prior unused
    reset tokens) and emailed. OAuth-only accounts are included, since reset can
    *set* a password for them (R6.3). Rate-limited per IP + per account.
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("reset", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    get_metrics().record_reset_requested()
    record = await get_by_email(str(payload.email))
    if record is not None:
        acct_rl = await limiter.check("reset", f"acct:{record.id}")
        if not acct_rl.allowed:
            raise _rate_limited(acct_rl.retry_after)
        raw_token = await get_token_service().issue_reset(record.id)
        # Fail-safe: a provider outage must not 500 (which would break the uniform
        # response and leak that the address is registered) — design §Reliability.
        await send_email_safe(
            get_email_sender(),
            build_password_reset_email(
                to=record.email,
                raw_token=raw_token,
                base_url=settings.frontend_base_url,
            ),
        )
    return UniformAckResponse()


# ---------------------------------------------------------------------------
# POST /auth/password/reset  (redeem token + set new password)
# ---------------------------------------------------------------------------


@router.post("/password/reset", response_model=SafeUser)
async def password_reset(
    request: Request, response: Response, payload: ResetPasswordRequest
) -> SafeUser:
    """Redeem a reset token and set a new password (R6.2/6.3).

    Validates the new password against policy + breach check, sets the hash
    (linking password auth for an OAuth-only account, R6.3), **revokes every
    existing session** for the user, issues a fresh session, and audits the
    reset. The token is single-use; missing/used/expired all collapse to one
    generic ``invalid_token`` error.
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("reset", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    passwords = get_password_service()
    session_service = get_session_service()
    tokens = get_token_service()

    # Validate the token read-only first so a bad new password does not burn the
    # single-use link (better UX); the atomic single-use guarantee comes from the
    # consume below.
    peek = await tokens.peek_reset(payload.token)
    if not peek.ok:
        raise ApiError(400, "invalid_token", "This reset link is invalid or has expired.")

    record = await get_by_id(peek.user_id)
    if record is None:  # pragma: no cover - token FK implies a live user
        raise ApiError(400, "invalid_token", "This reset link is invalid or has expired.")

    policy = await passwords.validate_new_password(
        payload.password, email=record.email, name=record.name
    )
    if not policy.ok:
        raise ApiError(
            400,
            policy.code,
            "Password does not meet the security policy.",
            details={"unmet": list(policy.unmet)},
        )

    # Atomically consume the token (single-use). A concurrent redemption that won
    # the race leaves this one invalid.
    consumed = await tokens.consume_reset(payload.token)
    if not consumed.ok:
        raise ApiError(400, "invalid_token", "This reset link is invalid or has expired.")

    hashed = passwords.hash_password(payload.password)
    await set_password_hash(record.id, hashed)

    # Revoke ALL of the user's sessions — a reset invalidates every device (R6.2).
    await session_service.revoke_all_for_user(record.id)

    ip_hash = session_service.hash_ip(ip)
    await get_audit_service().record(
        AuditEvent.PASSWORD_RESET, actor_user_id=record.id, ip_hash=ip_hash
    )
    get_metrics().record_reset_completed()

    # Start a fresh session so the user is signed in immediately after reset.
    raw_token, info = await session_service.create_session(
        record.id, remember_me=False, ip=ip, user_agent=request.headers.get("user-agent")
    )
    _set_cookies(response, raw_token, info, remember_me=False)

    # A successful reset also proves email ownership, so activate a still-pending
    # account (the reset link was delivered to that address).
    refreshed = await mark_email_verified(record.id) if record.status == "pending_verification" else record
    final = refreshed or record
    return _safe_user(final, aal=info.aal)


# ---------------------------------------------------------------------------
# POST /auth/step-up  (open a sudo window by re-authenticating)
# ---------------------------------------------------------------------------


@router.post("/step-up", response_model=SafeUser)
async def step_up(
    request: Request,
    payload: StepUpRequest,
    principal: Principal = Depends(require_session),
) -> SafeUser:
    """Re-verify the current user's password to open a step-up (sudo) window.

    Sensitive actions (password/email change, revoke-all) require a *recent*
    re-authentication (R9.1); this endpoint is how the client obtains one. It
    re-verifies the password (constant-time; MFA is a future additive factor),
    then bumps the session's ``step_up_at`` (+ ``aal``) and **evicts the session
    cache** so the fresh window is visible on the very next request
    (write-through eviction, R3.4). Rate-limited per IP + per account and audited
    as ``auth.step_up`` (R9.3).
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("step_up", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)
    acct_rl = await limiter.check("step_up", f"acct:{principal.user_id}")
    if not acct_rl.allowed:
        raise _rate_limited(acct_rl.retry_after)

    passwords = get_password_service()
    session_service = get_session_service()
    audit = get_audit_service()
    ip_hash = session_service.hash_ip(ip)

    stored_hash = await get_password_hash(principal.user_id)
    # Always run an Argon2 verify (dummy hash when the account has no local
    # password) so the branch is constant-effort.
    if not passwords.verify_password(stored_hash, payload.password):
        await audit.record(
            AuditEvent.STEP_UP,
            actor_user_id=principal.user_id,
            ip_hash=ip_hash,
            meta={"result": "failed"},
        )
        get_metrics().record_step_up(success=False)
        raise ApiError(401, "invalid_credentials", "Invalid password.")

    # Bump step_up_at (+ set aal1 explicitly) and evict the cache so the next
    # request resolves the fresh step-up window immediately.
    await session_service.bump_step_up(principal.session_id, aal="aal1")
    await audit.record(
        AuditEvent.STEP_UP, actor_user_id=principal.user_id, ip_hash=ip_hash
    )
    get_metrics().record_step_up(success=True)

    record = await get_by_id(principal.user_id)
    if record is None:  # pragma: no cover - a live session implies a live user
        raise ApiError(401, "unauthorized", "Account not found.")
    return _safe_user(record, aal="aal1")


# ---------------------------------------------------------------------------
# POST /auth/password/change  (change password from within a session)
# ---------------------------------------------------------------------------


@router.post("/password/change", response_model=SafeUser)
async def password_change(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    principal: Principal = Depends(require_stepped_up_session),
) -> SafeUser:
    """Change the password from within a stepped-up session (R7.3, R9.1).

    Requires a recent step-up (``require_stepped_up_session`` → 401
    ``step_up_required`` otherwise, Property 6). Re-verifies the current password
    (constant-time), enforces policy + breach check on the new one, and rehashes.
    Then, as a **session-fixation defense (design §Session mechanics — "new
    session id on … password change")**, it revokes *every* session for the user
    (killing any stolen session on another device, R7.3) and mints a **fresh,
    rotated** session for the initiating device — so the user stays signed in but
    on a brand-new session id, never one an attacker could have fixed. Audited as
    ``password_changed``. Rate-limited per account.
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("step_up", f"acct:{principal.user_id}")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    passwords = get_password_service()
    session_service = get_session_service()
    audit = get_audit_service()
    ip_hash = session_service.hash_ip(ip)

    record = await get_by_id(principal.user_id)
    if record is None:  # pragma: no cover - a live session implies a live user
        raise ApiError(401, "unauthorized", "Account not found.")

    stored_hash = await get_password_hash(principal.user_id)
    if not passwords.verify_password(stored_hash, payload.current_password):
        raise ApiError(401, "invalid_credentials", "Current password is incorrect.")

    policy = await passwords.validate_new_password(
        payload.new_password, email=record.email, name=record.name
    )
    if not policy.ok:
        raise ApiError(
            400,
            policy.code,
            "Password does not meet the security policy.",
            details={"unmet": list(policy.unmet)},
        )

    hashed = passwords.hash_password(payload.new_password)
    await set_password_hash(principal.user_id, hashed)

    # Fixation defense: revoke EVERY session (incl. the current one) — logging out
    # other devices (R7.3) — then rotate to a brand-new session for the initiating
    # device so its id changes on this privilege event while it stays signed in.
    revoked = await session_service.revoke_all_for_user(principal.user_id)
    raw_token, info = await session_service.create_session(
        principal.user_id,
        remember_me=False,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    _set_cookies(response, raw_token, info, remember_me=False)
    await audit.record(
        AuditEvent.PASSWORD_CHANGED,
        actor_user_id=principal.user_id,
        ip_hash=ip_hash,
        meta={"revoked_sessions": revoked},
    )
    return _safe_user(record, aal=info.aal)


# ---------------------------------------------------------------------------
# Google OAuth (provider-abstracted) — Task 7
# ---------------------------------------------------------------------------


def _set_txn_cookie(response: Response, txn: OAuthTransaction) -> None:
    """Set the signed, httpOnly, 5-minute transient OAuth-state cookie (R4.1)."""
    response.set_cookie(
        key=OAUTH_TXN_COOKIE,
        value=serialize_transaction(txn),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
        max_age=OAUTH_TXN_TTL_SECONDS,
    )


def _clear_txn_cookie(response: Response) -> None:
    """Clear the transient OAuth-state cookie (on both success and failure, R4.6)."""
    response.delete_cookie(OAUTH_TXN_COOKIE, path="/")


def _frontend_url(path: str) -> str:
    """Build an absolute frontend URL for a validated same-origin ``path``."""
    base = settings.frontend_base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def _oauth_failure(reason: str = "unknown") -> RedirectResponse:
    """Redirect to the frontend login with ``oauth_failed`` + cleared cookies (R4.6).

    Every callback failure — bad/missing state, PKCE/exchange failure, id_token
    verification failure, unverified provider email, or a refused (anti-hijack)
    link — collapses to this single generic *user-facing* outcome so nothing
    about *why* is disclosed. No session is created. The ``reason`` is recorded
    server-side only, for the ``oauth-failure-by-reason`` metric (R16.1).
    """
    get_metrics().record_oauth_failure(reason)
    response = RedirectResponse(
        _frontend_url("/login?error=oauth_failed"), status_code=302
    )
    _clear_txn_cookie(response)
    return response


@router.get("/oauth/{provider}/start")
async def oauth_start(provider: str, request: Request, next: str | None = None):
    """Begin OAuth: generate state/nonce/PKCE, store transient cookie, redirect (R4.1).

    Only allow-listed providers are routable (unknown → 404); a known-but-
    unconfigured provider (e.g. Google with no credentials) returns a clean
    ``oauth_not_configured`` error so local zero-config boot is unaffected. The
    ``state``/``nonce``/PKCE ``code_verifier`` (+ validated ``next``) are packed
    into a single signed, httpOnly, 5-minute transient cookie; the browser is
    then redirected to the provider's fixed authorize endpoint.
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("oauth", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    try:
        impl = oauth_registry.resolve(provider)
    except UnknownProvider:
        raise ApiError(404, "unknown_provider", "Unknown sign-in provider.")
    except ProviderNotConfigured:
        raise ApiError(400, "oauth_not_configured", "This sign-in method is not available.")

    state = generate_state()
    nonce = generate_nonce()
    verifier = generate_pkce_verifier()
    challenge = pkce_challenge(verifier)
    safe_next = validate_next_path(next)

    authorize_url = impl.authorize_url(
        state=state, nonce=nonce, challenge=challenge, next=safe_next
    )
    txn = OAuthTransaction(
        provider=provider, state=state, nonce=nonce, verifier=verifier, next=safe_next
    )
    response = RedirectResponse(authorize_url, status_code=302)
    _set_txn_cookie(response, txn)
    return response


@router.get("/oauth/{provider}/callback")
async def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = None,
    state: str | None = None,
):
    """Finish OAuth: verify state, exchange, verify id_token, link/create, session.

    Server-side only (tokens never reach the browser). The flow: constant-time
    ``state`` check against the signed transient cookie → PKCE code exchange →
    full id_token verification (JWKS w/ rotation, iss/aud/exp/iat±skew, nonce) →
    require ``email_verified`` → apply the safe link/create rules (R4.4) → issue a
    fresh (fixation-safe) session → clear transient cookies → redirect to the
    validated ``next`` or ``/home``. Any failure clears the transient cookies,
    creates no session, and redirects with ``oauth_failed`` (R4.6).
    """
    ip = client_ip(request)
    limiter = get_rate_limiter()
    rl = await limiter.check("oauth", ip or "unknown")
    if not rl.allowed:
        raise _rate_limited(rl.retry_after)

    # Unknown provider is a routing error (clean 404); everything else that can
    # go wrong from here on collapses to the uniform oauth_failed redirect.
    if not oauth_registry.is_known(provider):
        raise ApiError(404, "unknown_provider", "Unknown sign-in provider.")

    txn = deserialize_transaction(request.cookies.get(OAUTH_TXN_COOKIE))
    # Validate the transient state: present, same provider, and a constant-time
    # match against the ``state`` query param (CSRF/replay defense, R4.2/4.6).
    if (
        txn is None
        or txn.provider != provider
        or not state
        or not hmac.compare_digest(txn.state, state)
    ):
        return _oauth_failure("state_mismatch")

    if not code:
        return _oauth_failure("missing_code")

    audit = get_audit_service()
    session_service = get_session_service()
    ip_hash = session_service.hash_ip(ip)

    try:
        impl = oauth_registry.resolve(provider)
        tokens = await impl.exchange(code, txn.verifier)
        info = await impl.verify_id_token(tokens.id_token, txn.nonce)
    except (OAuthError, ProviderNotConfigured, UnknownProvider) as exc:
        logger.info("OAuth callback failed for %s: %s", provider, getattr(exc, "reason", exc))
        return _oauth_failure(getattr(exc, "reason", None) or "verify_failed")

    # The provider email must be verified before we trust it for linking (R4.5).
    if not info.email_verified:
        logger.info("OAuth callback rejected: provider email not verified (%s)", provider)
        return _oauth_failure("email_unverified")

    principal = get_optional_principal(request)
    result = await link_or_create_user(
        provider=provider,
        subject=info.sub,
        email=info.email,
        provider_email_verified=info.email_verified,
        authenticated_user_id=principal.user_id if principal else None,
        name=info.name,
    )
    if not result.ok or result.user_id is None:
        # Refused link (anti-hijack) — require login-first linking (R4.4).
        return _oauth_failure("link_refused")

    if result.action in (LinkAction.LINKED, LinkAction.CREATED):
        await audit.record(
            AuditEvent.OAUTH_LINK,
            actor_user_id=result.user_id,
            ip_hash=ip_hash,
            meta={"provider": provider, "action": result.action.value},
        )

    # Fixation defense: revoke any pre-existing cookie session, then mint a new one.
    old_token = request.cookies.get(settings.session_cookie_name)
    if old_token:
        try:
            await session_service.revoke_by_token(old_token)
        except Exception:  # pragma: no cover - revoke must not block sign-in
            logger.debug("Failed to revoke pre-oauth session", exc_info=True)

    raw_token, session_info = await session_service.create_session(
        result.user_id,
        remember_me=False,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await audit.record(AuditEvent.LOGIN, actor_user_id=result.user_id, ip_hash=ip_hash)
    get_metrics().record_oauth_success()
    get_metrics().record_login_success()

    destination = _frontend_url(txn.next or "/home")
    response = RedirectResponse(destination, status_code=302)
    _set_cookies(response, raw_token, session_info, remember_me=False)
    _clear_txn_cookie(response)
    return response
