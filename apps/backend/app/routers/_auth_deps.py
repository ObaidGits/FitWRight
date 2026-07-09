"""Shared FastAPI dependencies for the auth/user routers (Task 4).

These mirror :func:`app.auth.principal.get_principal` /
:func:`~app.auth.principal.require_step_up` but raise the ADR-7
:class:`~app.errors.ApiError` envelope (``unauthorized`` / ``step_up_required``)
so the versioned auth surface is uniform. The principal itself is resolved by
``AuthMiddleware`` and read from ``request.state`` (no DB round-trip here).
"""

from __future__ import annotations

from fastapi import Depends, Request

from app.auth import Principal, get_optional_principal
from app.config import settings
from app.errors import ApiError

__all__ = ["require_session", "require_stepped_up_session", "client_ip"]


def require_session(request: Request) -> Principal:
    """Require an authenticated session; 401 ``unauthorized`` otherwise."""
    principal = get_optional_principal(request)
    if principal is None:
        raise ApiError(401, "unauthorized", "Authentication required")
    return principal


def require_stepped_up_session(
    principal: Principal = Depends(require_session),
) -> Principal:
    """Require a recent step-up (sudo) window; 401 ``step_up_required`` otherwise.

    Gates sensitive actions (revoke-all, password/email change) so a merely-
    hijacked session without a recent re-auth cannot perform them (R9.1).
    """
    if not principal.stepped_up_within(settings.step_up_window):
        raise ApiError(401, "step_up_required", "Recent re-authentication required")
    return principal


def client_ip(request: Request) -> str | None:
    """Best-effort client IP for rate-limit keys and ``ip_hash``.

    Honors the first ``X-Forwarded-For`` hop (the app runs behind a proxy/LB in
    hosted mode) and falls back to the direct peer address.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else None
