"""Security-panel domain service home + its rollup step (Req 9).

This module owns both the job-time :class:`SecurityAggregateStep`, which keeps
closed-day compatibility aggregates, and :class:`SecurityMetricsService`, whose
request-time view queries the indexed audit event/timestamp window directly for
an exact trailing 24 hours. Both depend on the shared :class:`AdminRepo`; neither
reads event payloads or secrets.

The rollup step remains off the request path and writes idempotent daily totals.
The exact view performs two bounded indexed queries: one grouped event count and
one current-role admin-login count. ``StepResult`` is imported lazily inside the
rollup ``run`` method to avoid a pipeline import cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.admin.metric_registry import (
    SEC_ADMIN_LOGIN,
    SEC_AUTHZ_DENIED,
    SEC_LOGIN_FAILED,
    SEC_RATE_LIMITED,
    SEC_SUSPICIOUS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SecurityAggregateStep",
    "SECURITY_AGGREGATE_STEP",
    "SecurityMetricsService",
    "get_security_metrics_service",
    "reset_security_metrics_service",
]

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_str(dt: datetime) -> str:
    """UTC calendar day as ``YYYY-MM-DD``."""
    return dt.strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[str, str]:
    """Return the ``[start, end)`` UTC ISO bounds for a ``YYYY-MM-DD`` day.

    Mirrors ``MetricsService._day_bounds`` so the day-scoped audit scan uses the
    same closed-day partitioning as the rest of the rollup.
    """
    start_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return start_dt.isoformat(), end_dt.isoformat()


class SecurityAggregateStep:
    """Rollup_Step persisting the daily security aggregates (Req 9.1 / 9.2).

    For each closed UTC day in a small bounded window it asks
    :meth:`AdminRepo.security_daily` (Task 5.2) for that day's ``SEC_*`` counts -
    ``{SEC_LOGIN_FAILED, SEC_ADMIN_LOGIN, SEC_AUTHZ_DENIED, SEC_RATE_LIMITED,
    SEC_SUSPICIOUS}`` from their canonical durable audit events, then **UPSERTs**
    each key for that day via :meth:`MetricStore.upsert`.

    **UPSERT (absolute), not add - and why it is idempotent (Req 9.1).**
    ``security_daily`` recomputes the *full* day's count from ``audit_log`` on every
    run, so the correct write is the absolute value (``upsert``), not an increment
    (``add``). Re-running a closed day therefore recomputes the same count and
    re-UPSERTs the same value - a no-op change (idempotent per closed day). Using
    ``add`` would double-count on a re-run; ``upsert`` cannot.

    **Which day(s).** The pipeline passes ``day`` = the just-closed day (yesterday,
    per ``run_rollup_job``). The step aggregates that day plus a small bounded
    lookback of preceding closed days (:attr:`lookback_days`, default 2 -> the passed
    day and the one before it) so a single missed run self-heals on the next run,
    mirroring how ``MetricsService.run_rollup`` recovers ``lookback_days`` closed
    days. The lookback is fixed and tiny, so the extra work is a couple of
    day-bounded scans - never a growing cost. Today is never written here (only the
    passed closed day and older), so a closed day is never rewritten with a partial
    count.

    **Failure handling / preserve-last (Req 9.2).** Failures are isolated so a bad
    read or write never zeroes a good value:

    - If :meth:`AdminRepo.security_daily` raises for a day, that day's ``SEC_*``
      rows are left **unchanged** (no overwrite, no zero-fill), the failure is
      logged, and the step moves on to the remaining days.
    - Each per-key ``upsert`` is attempted independently. A key whose ``upsert``
      raises is left **unchanged** (its last successfully persisted value is
      preserved), logged, and collected; the remaining keys are still attempted.

    The step returns a failed :class:`StepResult` naming the failed
    ``metric@day`` pairs when anything failed, else success. It never raises out
    (failure-isolated - R2.5).

    Independent, idempotent per closed UTC day, resumable (a failed day/key retries
    on the next run within the lookback window), and failure-isolated per key.
    """

    name = "security_aggregate"

    def __init__(self, *, metric_store=None, repo=None, lookback_days: int = 2) -> None:
        # Optional injected collaborators (tests); otherwise the process-wide
        # singletons are resolved lazily at run time. Depends ONLY on the shared
        # MetricStore + AdminRepo + Metric_Registry - never on another
        # Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5).
        self._store = metric_store
        self._repo = repo
        self.lookback_days = max(1, int(lookback_days))

    def _metric_store(self):
        if self._store is not None:
            return self._store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _admin_repo(self):
        if self._repo is not None:
            return self._repo
        from app.admin.repo import get_admin_repo

        return get_admin_repo()

    def _closed_days(self, day: str) -> list[str]:
        """The bounded set of closed days to (re)aggregate, newest->oldest.

        Starts at the passed just-closed ``day`` and walks back
        ``lookback_days - 1`` further closed days for missed-run recovery. Falls
        back to just the passed ``day`` if it is not a parseable ``YYYY-MM-DD``.
        """
        try:
            base = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return [day]
        return [_day_str(base - timedelta(days=i)) for i in range(self.lookback_days)]

    async def run(self, day: str) -> "StepResult":
        # Lazy import breaks the load-time cycle: ``rollup_pipeline`` imports this
        # module to assemble PIPELINE, so we must not import it at module top.
        from app.admin.rollup_pipeline import StepResult

        store = self._metric_store()
        repo = self._admin_repo()

        failures: list[str] = []

        for closed_day in self._closed_days(day):
            day_start, day_end = _day_bounds(closed_day)

            # -- read that day's aggregates (whole-day recompute from audit_log) --
            try:
                counts = await repo.security_daily(day_start, day_end)
            except Exception:
                # Preserve every SEC_* value for this day unchanged (no overwrite,
                # no zero) and record the failure (Req 9.2). The other days in the
                # lookback window are still attempted.
                logger.exception(
                    "SecurityAggregateStep: security_daily failed for %s; "
                    "preserving last persisted SEC_* values",
                    closed_day,
                )
                failures.append(f"security_daily@{closed_day}")
                continue

            # -- UPSERT each SEC_* key (absolute value -> idempotent per day) ------
            for key, value in counts.items():
                try:
                    await store.upsert(closed_day, key, int(value))
                except Exception:
                    # Per-key isolation: leave this key's last persisted value
                    # unchanged, log, and keep going with the remaining keys.
                    logger.exception(
                        "SecurityAggregateStep: upsert failed for %s on %s; "
                        "preserving last persisted value",
                        key,
                        closed_day,
                    )
                    failures.append(f"{key}@{closed_day}")

        if failures:
            return StepResult.failure(
                self.name, f"security aggregate failed for: {', '.join(failures)}"
            )
        return StepResult.success(self.name)


# Process-wide instance slotted into PIPELINE by ``rollup_pipeline`` (before the
# prune). Single-flighted by the Rollup_Job's KVStore lock, so it is driven by one
# run at a time.
SECURITY_AGGREGATE_STEP = SecurityAggregateStep()


# ---------------------------------------------------------------------------
# Security view read model - the SecurityMetricsService (Req 9.3-9.7)
# ---------------------------------------------------------------------------


class SecurityMetricsService:
    """Build the security panel from exact indexed audit-log window counts.

    ``view`` computes one UTC ``[now - 24h, now)`` interval and delegates to the
    injectable :class:`AdminRepo`. Newly deployed rate-limit and CAPTCHA-denied
    audit instrumentation makes every returned field durable. Admin-login role
    classification joins the current user row, so the response explicitly marks
    its basis as ``current_role_at_query_time``.
    """

    _WINDOW_HOURS = 24

    def __init__(self, *, repo=None) -> None:
        self._repo = repo

    def _get_repo(self):
        if self._repo is not None:
            return self._repo
        from app.admin.repo import get_admin_repo

        return get_admin_repo()

    async def view(self) -> "SecurityView":
        """Return exact counts from the indexed audit trail for ``now - 24h``."""
        from app.admin.schemas import SecurityView

        end = _now()
        start = end - timedelta(hours=self._WINDOW_HOURS)
        counts = await self._get_repo().security_window(start.isoformat(), end.isoformat())

        return SecurityView(
            windowHours=self._WINDOW_HOURS,
            windowStart=start.isoformat(),
            windowEnd=end.isoformat(),
            windowKind="exact_trailing",
            adminLoginRoleBasis="current_role_at_query_time",
            loginFailed=counts.get(SEC_LOGIN_FAILED, 0),
            adminLogin=counts.get(SEC_ADMIN_LOGIN, 0),
            authzDenied=counts.get(SEC_AUTHZ_DENIED, 0),
            rateLimited=counts.get(SEC_RATE_LIMITED, 0),
            suspicious=counts.get(SEC_SUSPICIOUS, 0),
            notInstrumented=[],
            computedAt=end.isoformat(),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.storage_metrics.get_storage_metrics_service)
# ---------------------------------------------------------------------------

_service: "SecurityMetricsService | None" = None


def get_security_metrics_service() -> SecurityMetricsService:
    """Return the process-wide :class:`SecurityMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = SecurityMetricsService()
    return _service


def reset_security_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
