"""Internal machine endpoints: session reaper + auth metrics (ADR-15).

These are **machine-to-machine** endpoints, not user endpoints:

- ``POST /internal/run-jobs`` runs one single-flighted reaper batch
  (:meth:`SessionService.reap`) and returns a small JSON summary. On the free
  tier (``SCHEDULER_MODE=external_cron``) an external scheduler (GitHub Actions /
  cron-job.org) calls this on a schedule; the premium ``internal`` mode calls the
  same reaper from an in-process loop instead (see ``app.scheduler``).
- ``GET /internal/metrics`` exposes the in-process :class:`AuthMetrics` snapshot
  (login/signup/verification/reset/oauth/step-up counters + session-cache hit
  ratio) as JSON for an operator/monitoring poll.

Both are guarded by a **shared secret** (``INTERNAL_JOB_TOKEN``) supplied in the
``X-Internal-Job-Token`` header and compared in **constant time**. They require
no user session (a machine has none) and therefore are naturally outside the
per-session CSRF check (the ``AuthMiddleware`` only enforces CSRF when a session
principal is present). A missing token → 401, a wrong token → 403, and when no
token is configured (the zero-config local default) *every* caller is rejected
so auth metrics / job control are never exposed unauthenticated.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Header, Request
from fastapi import status as http_status

from app.auth.metrics import get_metrics
from app.auth.sessions import get_session_service
from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["internal"])

# The header an external scheduler / operator presents the shared secret in.
_TOKEN_HEADER = "X-Internal-Job-Token"


def _authorize(provided: str | None) -> None:
    """Constant-time shared-secret check for the internal endpoints.

    - No configured ``INTERNAL_JOB_TOKEN`` → reject everyone (401): the endpoints
      must never serve data / run jobs unauthenticated, and locally they are
      simply unused.
    - Missing header → 401 ``unauthorized``.
    - Present but wrong → 403 ``forbidden``.
    """
    configured = settings.internal_job_token
    if not configured:
        raise ApiError(
            http_status.HTTP_401_UNAUTHORIZED,
            "unauthorized",
            "Internal endpoints are not enabled.",
        )
    if not provided:
        raise ApiError(
            http_status.HTTP_401_UNAUTHORIZED,
            "unauthorized",
            "Missing internal job token.",
        )
    if not secrets.compare_digest(provided, configured):
        raise ApiError(
            http_status.HTTP_403_FORBIDDEN,
            "forbidden",
            "Invalid internal job token.",
        )


@router.post("/run-jobs")
async def run_jobs(
    request: Request,
    x_internal_job_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Run one reaper batch (single-flighted) and return a JSON summary.

    Safe to call concurrently / on overlapping schedules: the reaper acquires a
    non-blocking KVStore lock, so a second concurrent call simply returns
    all-zero counts rather than double-running.
    """
    _authorize(x_internal_job_token)
    counts = await get_session_service().reap()
    logger.info("Internal run-jobs reaped %s", counts)
    return {"status": "ok", "reaped": counts}


@router.get("/metrics")
async def metrics(
    x_internal_job_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Return the in-process auth metrics snapshot (JSON) for monitoring."""
    _authorize(x_internal_job_token)
    return get_metrics().snapshot()
