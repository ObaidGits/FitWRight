"""ADR-7 API error envelope for the versioned (`/api/v1`) surface.

Phase-2 endpoints return a standard error envelope::

    { "error": { "code": "snake_case", "message": "human", "details"?: {...} } }

Client-facing messages are generic; specifics are logged server-side. The
legacy (pre-P1) routers keep FastAPI's default ``{"detail": ...}`` shape, so this
envelope is opt-in via :class:`ApiError`: raising it anywhere renders the
envelope with the right status code (and optional headers such as
``Retry-After``), while a plain ``HTTPException`` still renders ``detail``.

Install the handler once on the app with :func:`install_error_handlers`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["ApiError", "error_envelope", "install_error_handlers", "_handle_unexpected_error"]


class ApiError(Exception):
    """A domain error rendered as the ADR-7 envelope.

    ``code`` is the stable machine-readable reason (e.g. ``invalid_credentials``,
    ``rate_limited``); ``message`` is a generic, non-leaky human string;
    ``details`` is an optional, already-sanitized map. ``headers`` lets a caller
    attach response headers (used for ``Retry-After`` on ``rate_limited``).
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message or code.replace("_", " ")
        self.details = details
        self.headers = headers
        super().__init__(self.message)


def error_envelope(
    code: str, message: str, *, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build the ADR-7 envelope body (``details`` omitted when ``None``)."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    # One centralized durable audit event per rendered rate-limit denial. The
    # writer is fail-soft, and only non-PII request correlation / actor ID (when
    # already resolved by auth middleware) is stored; no route input or secret is
    # copied into the audit trail. Exact counts begin at deployment of this code.
    if exc.status_code == 429 and exc.code == "rate_limited":
        try:
            from app.auth.audit import AuditEvent, get_audit_service

            principal = getattr(request.state, "principal", None)
            await get_audit_service().record(
                AuditEvent.RATE_LIMITED,
                actor_user_id=getattr(principal, "user_id", None),
                request_id=getattr(request.state, "request_id", None),
            )
        except Exception:  # pragma: no cover - auth response must never fail on audit
            logger.exception("Failed to audit a rate-limit denial")

    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, details=exc.details),
        headers=exc.headers,
    )


def install_error_handlers(app) -> None:
    """Register the :class:`ApiError` -> envelope handler on ``app``."""
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for bugs/timeouts that reach no other handler.

    Without this, FastAPI's own default (``debug=False``) renders a bare
    ``{"detail": "Internal Server Error"}`` with no ``request_id`` in the body -
    a user reporting "it just says error" has nothing to hand back to the
    developer, and the developer has to correlate by timestamp instead of a
    lookup key. The full exception (message + type) is still logged server-side
    with the same request_id, so nothing about the cause is lost - only the
    client response is generic, per ADR-7 (specifics logged, not returned).
    """
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "Unhandled exception on %s %s (request_id=%s): %s: %s",
        request.method,
        request.url.path,
        request_id,
        type(exc).__name__,
        exc,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content=error_envelope(
            "internal_error",
            "Something went wrong on our end. If this keeps happening, "
            "please report it with the request ID below.",
            details={"request_id": request_id} if request_id else None,
        ),
        headers={"X-Request-ID": request_id} if request_id else None,
    )
