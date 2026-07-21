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
principal is present). A missing token -> 401, a wrong token -> 403, and when no
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

    - No configured ``INTERNAL_JOB_TOKEN`` -> reject everyone (401): the endpoints
      must never serve data / run jobs unauthenticated, and locally they are
      simply unused.
    - Missing header -> 401 ``unauthorized``.
    - Present but wrong -> 403 ``forbidden``.
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
    # P2 Admin scheduled jobs (rollup + purge) - single-flighted, idempotent, and
    # safe to run on every call (rollup only writes closed days; purge only acts
    # on grace-elapsed users). Kept best-effort so a job hiccup never fails the
    # reaper cron.
    admin_jobs: dict[str, object] = {}
    try:
        from app.admin.jobs import run_admin_jobs

        admin_jobs = await run_admin_jobs()
    except Exception:  # pragma: no cover - defensive; jobs are best-effort
        logger.exception("Internal run-jobs: admin jobs failed")
        admin_jobs = {"status": "error"}
    # P3 productivity jobs (outbox drain + notification emails + scheduler scans +
    # retention) - single-flighted + idempotent, best-effort so a hiccup never
    # fails the reaper cron.
    productivity: dict[str, object] = {}
    try:
        from app.events.jobs import run_productivity_jobs
        from app.retention.jobs import run_retention_jobs

        productivity = await run_productivity_jobs()
        productivity["retention"] = await run_retention_jobs()
    except Exception:  # pragma: no cover - defensive; jobs are best-effort
        logger.exception("Internal run-jobs: productivity jobs failed")
        productivity = {"status": "error"}
    return {
        "status": "ok",
        "reaped": counts,
        "admin": admin_jobs,
        "productivity": productivity,
    }


@router.get("/metrics")
async def metrics(
    x_internal_job_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Return the in-process auth + admin metrics snapshot (JSON) for monitoring.

    The auth counters stay at the top level (stable shape for existing pollers);
    the P2 admin metrics snapshot is added under the ``admin`` key (R12.1).

    In-process counters are per-worker (an accepted operational boundary - scrape
    every worker to aggregate). The operationally-critical **purge backlog** and
    soft-deleted totals are instead computed **live from the DB** here, so they
    are authoritative and worker-independent regardless of which worker answers
    (audit L3).
    """
    _authorize(x_internal_job_token)
    from datetime import datetime, timedelta, timezone

    from app.admin.metrics import get_admin_metrics
    from app.admin.metrics_service import live_admin_gauges
    from app.config import settings as _settings

    snapshot = get_metrics().snapshot()
    admin_snapshot = get_admin_metrics().snapshot()
    try:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=_settings.admin_delete_grace_days)
        ).isoformat()
        # Read live gauges through the admin module's own service (Amendment E).
        admin_snapshot.setdefault("gauges", {})
        admin_snapshot["gauges"].update(await live_admin_gauges(cutoff))
    except Exception:  # pragma: no cover - monitoring must not fail on a DB blip
        logger.debug("Failed to compute live admin gauges", exc_info=True)
    snapshot["admin"] = admin_snapshot

    # P3 productivity metrics: in-process counters + live outbox backlog/DLQ
    # gauges (worker-independent, DB-computed) for indexer/notifier lag alerts.
    try:
        from app.events.outbox import outbox_stats
        from app.productivity.metrics import get_productivity_metrics

        productivity = get_productivity_metrics().snapshot()
        productivity["gauges"] = await outbox_stats()
        snapshot["productivity"] = productivity
    except Exception:  # pragma: no cover - monitoring must not fail on a blip
        logger.debug("Failed to compute productivity metrics", exc_info=True)

    # P4 resilience metrics: streaming (first-token latency, cancel/reap/active),
    # autosave conflict/idempotency counters (design §Observability).
    try:
        from app.resilience.metrics import get_resilience_metrics

        snapshot["resilience"] = get_resilience_metrics().snapshot()
    except Exception:  # pragma: no cover - monitoring must not fail on a blip
        logger.debug("Failed to compute resilience metrics", exc_info=True)

    # JD extraction adapter health (circuit-breaker states + self-healing status).
    try:
        from app.jd.health import adapter_health_snapshot

        snapshot["jd_adapter_health"] = await adapter_health_snapshot()
    except Exception:  # pragma: no cover - monitoring must not fail on a blip
        logger.debug("Failed to compute JD adapter health", exc_info=True)

    return snapshot
