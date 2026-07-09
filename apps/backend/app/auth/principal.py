"""Principal, RBAC capabilities, request deps, and middleware (Task 2.3).

This module turns a resolved session into an authorization decision and wires
the request-time plumbing (design `§RBAC & capabilities`, `§Session mechanics`,
R8.1/8.2/9.1/12.1/12.2/12.3):

- :class:`Principal` — the immutable ``{user_id, role, capabilities, aal,
  step_up_at, …}`` attached to each authenticated request.
- :func:`capabilities_for` — the **single** role→capability map (extension point
  for future ``support``/``superadmin``/org roles).
- FastAPI dependencies — :func:`get_principal` (401 if unauthenticated),
  :func:`require_capability` (403 if the capability is absent, audited), and
  :func:`require_step_up` (401 ``step_up_required`` outside the sudo window).
- :class:`SecurityHeadersMiddleware` — HSTS, ``X-Content-Type-Options``,
  ``Referrer-Policy``, and a strict CSP with ``frame-ancestors 'none'`` (R12.3).
- :class:`AuthMiddleware` — resolves the ``__Host-`` session cookie into
  ``request.state.principal`` and enforces the per-session double-submit CSRF
  check on state-changing requests (GET/HEAD/OPTIONS exempt; logout included).
  CSRF enforcement is gated so local zero-config (``SINGLE_USER_MODE``) boot and
  the existing unauthenticated routes keep working; hosted turns it on.
- Cookie helpers — set/clear the ``__Host-`` session cookie and the JS-readable
  ``csrf`` cookie with the correct hardened attributes (R12.1).
- ``auth_csrf_router`` — ``GET /auth/csrf`` issuing the pre-session token.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi import status as http_status
from fastapi.exceptions import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth import csrf as csrf_mod
from app.auth.sessions import ResolvedSession
from app.config import Settings, settings

logger = logging.getLogger(__name__)

__all__ = [
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
    "SAFE_METHODS",
]

# HTTP methods that never mutate state and are therefore CSRF-exempt (R12.2).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class Capabilities:
    """Server-checked permission strings (extension point for new roles)."""

    ADMIN_READ = "admin.read"
    ADMIN_MANAGE = "admin.manage"


# Single source of truth for role → capabilities (R8.1). Adding a role or a
# capability is a one-line change here; nothing else hard-codes a role name.
_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    "user": frozenset(),
    "admin": frozenset({Capabilities.ADMIN_READ, Capabilities.ADMIN_MANAGE}),
}


def capabilities_for(role: str) -> frozenset[str]:
    """Return the capability set for ``role`` (empty for an unknown role)."""
    return _ROLE_CAPABILITIES.get(role, frozenset())


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity + authorization context for a request."""

    user_id: str
    session_id: str
    role: str
    capabilities: frozenset[str]
    aal: str
    step_up_at: str | None
    email: str
    name: str
    status: str
    email_verified: bool
    csrf_secret: str = field(repr=False, default="")

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def stepped_up_within(self, window_seconds: int, *, now: datetime | None = None) -> bool:
        """Whether the last step-up is recent enough for a sensitive action (R9.1)."""
        if not self.step_up_at:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            stepped = datetime.fromisoformat(self.step_up_at)
        except (ValueError, TypeError):
            return False
        if stepped.tzinfo is None:
            stepped = stepped.replace(tzinfo=timezone.utc)
        return (now - stepped).total_seconds() <= window_seconds

    @classmethod
    def from_resolved(cls, resolved: ResolvedSession) -> "Principal":
        return cls(
            user_id=resolved.user_id,
            session_id=resolved.session_id,
            role=resolved.role,
            capabilities=capabilities_for(resolved.role),
            aal=resolved.aal,
            step_up_at=resolved.step_up_at,
            email=resolved.email,
            name=resolved.name,
            status=resolved.status,
            email_verified=resolved.email_verified,
            csrf_secret=resolved.csrf_secret,
        )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_optional_principal(request: Request) -> Principal | None:
    """Return the principal the middleware attached, or ``None`` if anonymous."""
    return getattr(request.state, "principal", None)


def get_principal(request: Request) -> Principal:
    """Require an authenticated principal; raise 401 otherwise."""
    principal = get_optional_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="authentication_required",
        )
    return principal


async def get_effective_user_id(request: Request) -> str:
    """Resolve the owning ``user_id`` every owned-resource endpoint scopes to.

    - **Hosted** (multi-user): the authenticated principal's ``user_id``; an
      anonymous request raises 401 (no principal ⇒ no owned data).
    - **Local** (``SINGLE_USER_MODE``): the implicit bootstrap owner, lazily
      ensured (created + owned-row backfill) so local zero-config behaves exactly
      like today while still routing every query through the ``user_id`` scope.

    The resolved id is also published on the request-scoped context var so the
    synchronous api-key resolution path (``llm.py``) resolves the caller's key
    (R10.6) without threading ``user_id`` through the entire LLM call graph.
    """
    from app.auth.context import set_current_user_id

    principal = get_optional_principal(request)
    if principal is not None:
        set_current_user_id(principal.user_id)
        return principal.user_id

    if settings.single_user_mode:
        from app.auth.owner import ensure_owner

        owner_id = await ensure_owner()
        set_current_user_id(owner_id)
        return owner_id

    raise HTTPException(
        status_code=http_status.HTTP_401_UNAUTHORIZED,
        detail="authentication_required",
    )


async def require_verified_user_id(request: Request) -> str:
    """Resolve the effective ``user_id`` **and** gate unverified accounts (R5.6).

    A drop-in replacement for :func:`get_effective_user_id` on *sensitive*,
    provider-cost endpoints (resume tailoring/generation). It resolves the owning
    user id exactly like :func:`get_effective_user_id` (so the LLM api-key
    context var is still published and anonymous hosted requests still 401), then
    — only when email verification is required for this deployment — refuses an
    account whose email is unverified with ``403 email_verification_required``.

    Verification is a *gate on sensitive actions*, not a block on basic use
    (design `§Email verification`): browsing, upload, and listing stay ungated.
    OAuth sign-ups arrive already verified, so they are never gated. In
    ``SINGLE_USER_MODE`` (local) verification is off and the bootstrap owner is
    verified, so this behaves exactly like :func:`get_effective_user_id`.
    """
    user_id = await get_effective_user_id(request)
    if settings.email_verification_enabled:
        principal = get_optional_principal(request)
        # A principal is absent only on the single-user owner path (verified),
        # which is also verification-off; a real hosted principal must be verified.
        if principal is not None and not principal.email_verified:
            from app.errors import ApiError

            raise ApiError(
                403,
                "email_verification_required",
                "Verify your email address to use this feature.",
            )
    return user_id


def require_capability(capability: str):
    """Build a dependency that requires ``capability`` (403 if missing).

    Anonymous callers get 401 (via :func:`get_principal`); an authenticated
    caller lacking the capability gets 403 and an ``authz.denied`` audit entry.
    """

    async def _dep(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_capability(capability):
            await _audit_denied(request, principal, capability)
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="forbidden",
            )
        return principal

    return _dep


def require_step_up(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    """Require a recent step-up (sudo) window; raise 401 ``step_up_required``.

    Used to gate sensitive actions (password/email change, revoke-all). The
    window is ``STEP_UP_WINDOW`` seconds from configuration (R9.1).
    """
    if not principal.stepped_up_within(settings.step_up_window):
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="step_up_required",
        )
    return principal


async def _audit_denied(request: Request, principal: Principal, capability: str) -> None:
    """Best-effort ``authz.denied`` audit for a capability failure."""
    try:
        from app.auth.audit import AuditEvent, get_audit_service

        await get_audit_service().record(
            AuditEvent.AUTHZ_DENIED,
            actor_user_id=principal.user_id,
            request_id=getattr(request.state, "request_id", None),
            meta={"capability": capability, "path": request.url.path},
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("Failed to audit authz denial", exc_info=True)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def set_session_cookies(
    response: Response,
    *,
    raw_token: str,
    session_id: str,
    csrf_secret: str,
    config: Settings | None = None,
    max_age: int | None = None,
) -> None:
    """Set the hardened ``__Host-`` session cookie + JS-readable ``csrf`` cookie.

    The session cookie is ``HttpOnly, Secure, SameSite, Path=/`` with **no**
    Domain (required by the ``__Host-`` prefix). The CSRF cookie is readable by
    JS (double-submit) and carries ``HMAC(csrf_secret, session_id)`` (R12.1).

    ``max_age`` is the cookie lifetime hint (seconds); it defaults to the
    remember-me cap and is set to the shorter absolute cap for a non-remembered
    session (the authoritative lifetime is always the server-side
    ``expires_at``). It bounds both cookies identically.
    """
    config = config or settings
    cookie_max_age = config.remember_me_ttl if max_age is None else max_age
    response.set_cookie(
        key=config.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path="/",
        max_age=cookie_max_age,
    )
    response.set_cookie(
        key=config.csrf_cookie_name,
        value=csrf_mod.derive_csrf_token(session_id, csrf_secret),
        httponly=False,
        secure=config.cookie_secure,
        samesite=config.cookie_samesite,
        path="/",
        max_age=cookie_max_age,
    )


def clear_session_cookies(response: Response, *, config: Settings | None = None) -> None:
    """Clear the session + CSRF cookies (logout)."""
    config = config or settings
    response.delete_cookie(config.session_cookie_name, path="/")
    response.delete_cookie(config.csrf_cookie_name, path="/")


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# Strict, Next.js-compatible CSP. ``frame-ancestors 'none'`` blocks clickjacking;
# ``'unsafe-inline'`` on styles is required by many CSS-in-JS setups, scripts
# stay locked down.
_CSP = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "img-src 'self' data: https:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach transport/hardening headers to every response (R12.3)."""

    def __init__(self, app: ASGIApp, *, config: Settings | None = None) -> None:
        super().__init__(app)
        self._config = config or settings

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Content-Security-Policy", _CSP)
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # HSTS only over HTTPS (and only meaningful in hosted/secure mode).
        if self._config.cookie_secure:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class AuthMiddleware(BaseHTTPMiddleware):
    """Resolve the session cookie → ``request.state.principal`` + CSRF guard.

    Resolution is best-effort (an anonymous request simply gets no principal);
    the authoritative 401/403 happen in the route dependencies. CSRF is enforced
    on state-changing methods when a session is present. To keep local zero-
    config boot and the existing unauthenticated routes working, per-session CSRF
    enforcement is skipped in ``SINGLE_USER_MODE`` (hosted turns it on).
    """

    def __init__(self, app: ASGIApp, *, config: Settings | None = None) -> None:
        super().__init__(app)
        self._config = config or settings

    async def dispatch(self, request: Request, call_next):
        from app.auth.sessions import get_session_service

        session_service = get_session_service()
        raw_token = request.cookies.get(self._config.session_cookie_name)
        resolved: ResolvedSession | None = None
        if raw_token:
            try:
                resolved = await session_service.resolve(raw_token)
            except Exception:  # pragma: no cover - resolution must not 500 a request
                logger.warning("Session resolution failed", exc_info=True)
                resolved = None

        principal = Principal.from_resolved(resolved) if resolved else None
        request.state.principal = principal

        # CSRF: per-session double-submit on mutations (hosted only, so local
        # zero-config and existing unauthenticated routes are unaffected).
        if (
            not self._config.single_user_mode
            and principal is not None
            and request.method.upper() not in SAFE_METHODS
        ):
            header_token = request.headers.get("X-CSRF-Token")
            if not csrf_mod.verify_csrf_token(
                header_token, principal.session_id, principal.csrf_secret
            ):
                return _json_error(
                    http_status.HTTP_403_FORBIDDEN, "csrf_failed"
                )

        return await call_next(request)


def _json_error(status_code: int, code: str) -> Response:
    """Minimal JSON error response used inside middleware."""
    from starlette.responses import JSONResponse

    return JSONResponse(status_code=status_code, content={"detail": code})


# ---------------------------------------------------------------------------
# GET /auth/csrf — pre-session token (login-CSRF defense)
# ---------------------------------------------------------------------------

auth_csrf_router = APIRouter(prefix="/auth", tags=["auth"])


@auth_csrf_router.get("/csrf")
async def issue_csrf(response: Response) -> dict[str, str]:
    """Issue a pre-session double-submit CSRF token for login/signup (R12.2).

    The signed token is set as the JS-readable ``csrf`` cookie **and** returned
    in the body; the client submits it back in ``X-CSRF-Token`` on the login/
    signup POST, and the endpoint verifies both the signature and the cookie/
    header match before establishing a session (login-CSRF defense).
    """
    token = csrf_mod.issue_presession_token(settings.session_secret)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
        max_age=3600,
    )
    return {"csrfToken": token}
