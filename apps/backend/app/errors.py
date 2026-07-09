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

__all__ = ["ApiError", "error_envelope", "install_error_handlers"]


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


async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, details=exc.details),
        headers=exc.headers,
    )


def install_error_handlers(app) -> None:
    """Register the :class:`ApiError` → envelope handler on ``app``."""
    app.add_exception_handler(ApiError, _handle_api_error)
