"""Structured JSON logging + per-request context (Task 9.2, R16.1).

The design (`§Observability & operations`, R16.1) requires structured logs that
carry a ``request_id`` and - when known - a ``user_id``, and that **never** leak
secrets/tokens/PII beyond the user id. This module provides three cooperating
pieces:

- **Request context.** Two :class:`contextvars.ContextVar`s hold the current
  ``request_id`` and ``user_id``. They are set once per request by
  :class:`RequestContextMiddleware` and read by the log formatter, so every log
  line emitted anywhere in the request - service, router, or middleware -
  is correlated without threading an id through every call.
- **JSON formatter.** :class:`JsonLogFormatter` renders each record as one JSON
  object (``ts``/``level``/``logger``/``msg`` + ``request_id``/``user_id`` when
  present) and runs every field through the shared audit sanitizer
  (:func:`app.auth.audit.sanitize_log_value`) so a stray token in a log argument
  is scrubbed and a newline can't forge a second log line.
- **Middleware.** :class:`RequestContextMiddleware` mints/propagates the
  ``request_id`` (honoring an inbound ``X-Request-ID``), publishes it on
  ``request.state`` + the context var, echoes it back in the ``X-Request-ID``
  response header, and - after the inner auth middleware has resolved the
  principal - records the request against :mod:`app.auth.metrics` and emits one
  structured access log line (method, path, status, principal's ``user_id``).

Only the ``user_id`` identity is ever logged (R16.1); emails, tokens, cookies,
and passwords never are - the sanitizer drops secret-bearing keys and the access
log deliberately records only ``method``/``path``/``status``/``user_id``.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

__all__ = [
    "REQUEST_ID_HEADER",
    "request_id_var",
    "user_id_var",
    "JsonLogFormatter",
    "configure_json_logging",
    "RequestContextMiddleware",
]

REQUEST_ID_HEADER = "X-Request-ID"

# Per-request correlation ids. Defaults are empty strings so a log emitted
# outside any request (startup/shutdown) simply omits them.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
user_id_var: ContextVar[str] = ContextVar("user_id", default="")

# Standard LogRecord attributes we never copy into the JSON "extra" bag.
_RESERVED_LOGRECORD_KEYS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Render a log record as a single sanitized JSON object.

    Includes the current ``request_id``/``user_id`` (when set), the exception
    text if any, and any explicit ``extra=`` fields - each passed through the
    audit sanitizer so no secret/PII or log-injection newline survives.
    """

    def format(self, record: logging.LogRecord) -> str:
        from app.auth.audit import sanitize_log_value, sanitize_meta

        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": sanitize_log_value(record.getMessage()),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        user_id = user_id_var.get()
        if user_id:
            payload["user_id"] = user_id

        if record.exc_info:
            payload["exc"] = sanitize_log_value(self.formatException(record.exc_info))

        # Gather explicit ``extra=`` fields (skipping reserved LogRecord attrs)
        # and route them through the audit sanitizer, which **drops** any
        # secret-bearing key (password/token/cookie/...) and sanitizes every value
        # - so a stray secret in a log call never reaches the sink.
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOGRECORD_KEYS
            and not key.startswith("_")
            and key not in payload
        }
        for key, value in (sanitize_meta(extras) or {}).items():
            payload.setdefault(key, value)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_json_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root handler (idempotent).

    Applies the structured formatter to the root logger's handlers (creating one
    if none exist) so both app and third-party logs are emitted as JSON with the
    request correlation fields. Safe to call more than once.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        root.addHandler(handler)
    formatter = JsonLogFormatter()
    for handler in root.handlers:
        handler.setFormatter(formatter)
    root.setLevel(level)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a ``request_id``, correlate logs, and emit the access log + metrics.

    Runs *outside* the auth middleware so the ``request_id`` context var is set
    before any auth-flow log/audit fires, and so the principal (resolved by the
    inner auth middleware) is available *after* ``call_next`` for the access log
    and per-request metrics. The inbound ``X-Request-ID`` is honored when present
    (trusted proxy correlation) and always echoed back on the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._logger = logging.getLogger("app.access")

    async def dispatch(self, request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
        request.state.request_id = request_id
        rid_token = request_id_var.set(request_id)
        uid_token = user_id_var.set("")

        start = time.perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            # Echo the correlation id back so a client/proxy can tie logs together.
            response.headers.setdefault(REQUEST_ID_HEADER, request_id)
            return response
        finally:
            # The inner auth middleware has now resolved the principal (if any).
            principal = getattr(request.state, "principal", None)
            user_id = getattr(principal, "user_id", "") if principal else ""
            if user_id:
                user_id_var.set(user_id)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            self._logger.info(
                "request_completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                    # user_id is also carried by the context var; included
                    # explicitly so it lands on this line even for anon requests.
                    "principal_user_id": user_id or None,
                },
            )
            user_id_var.reset(uid_token)
            request_id_var.reset(rid_token)
