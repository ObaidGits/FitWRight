"""Security-panel domain service home + its rollup step (Req 9).

This module is the security bounded-context's owned home. It holds the job-time
:class:`SecurityAggregateStep` that *populates* the daily security signals
(Task 13.1) and the :class:`SecurityMetricsService` (Task 13.2) whose ``view()``
read model *assembles* them for ``GET /api/v1/admin/security``. Co-locating the
step with its owning service mirrors ``ai_metrics.py`` (``AiFlushStep`` next to
``AiMetricsService``) and ``storage_metrics.py`` (``DbSizeSampleStep`` /
``StorageSnapshotStep`` next to ``StorageMetricsService``): a step lives beside
the domain it serves, and the pipeline only imports the step *singleton*.

**Bounded-context purity (Req 19.2/19.3/19.5).** As a Domain_Metrics_Service
module this depends ONLY on shared primitives - the Metric_Store, the
Metric_Registry (the ``SEC_*`` keys), the ``AdminRepo``, and config - never on
another Domain_Metrics_Service. The import-graph fitness test (Task 5.3) enforces
this.

**Off the request path (Req 21.5).** The step runs only inside the Rollup_Job. It
reads cross-user audit aggregates via :class:`~app.admin.repo.AdminRepo` (allowed
at rollup time - a day-bounded ``audit_log`` scan), never on a request path.

``StepResult`` is imported **lazily** inside ``run`` (the cycle-safe pattern used
by ``AiFlushStep`` / ``DbSizeSampleStep``): ``rollup_pipeline`` imports this module
at load time to assemble ``PIPELINE``, so this module must not import
``rollup_pipeline`` at the top level.
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

# Security-view fields that have no durable aggregate source today and are
# therefore surfaced as explicitly not-instrumented rather than a misleading 0
# (see ``SecurityMetricsService.view``). Kept as a module constant so the guard
# is a single, documented source of truth.
_NOT_INSTRUMENTED: tuple[str, ...] = ("rateLimited", "suspicious")


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
    SEC_SUSPICIOUS}`` (rate-limited / suspicious are currently ``0`` - a documented
    gap in 5.2, but still returned so the row is complete) - and **UPSERTs** each
    key for that day via :meth:`MetricStore.upsert`.

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
    """Security-view read model assembled from the ``SEC_*`` aggregates only.

    :meth:`view` builds the :class:`~app.admin.schemas.SecurityView` served by
    ``GET /api/v1/admin/security``. It is a cohesive, single-responsibility
    Domain_Metrics_Service that depends **only** on shared primitives - the
    shared :class:`~app.admin.metric_store.MetricStore`, the static
    :mod:`app.admin.metric_registry` (the five ``SEC_*`` keys), and the response
    schema - never on another Domain_Metrics_Service (import-graph guard,
    Req 19.2/19.3/19.5).

    **NEVER scans ``audit_log`` on the request path (Req 9.6/9.7) - structurally
    guaranteed.** The service holds *only* a ``MetricStore`` (no ``AdminRepo``,
    no session factory), so there is no collaborator through which it *could*
    reach ``audit_log``. Every count is read from a durable ``metrics_daily``
    ``SEC_*`` Metric_Key via ``MetricStore.sum`` - the exact same indexed
    ``(metric, day)`` read the other observability panels use. The day-bounded
    ``audit_log`` scan that *produces* these aggregates lives exclusively in the
    job-time :class:`SecurityAggregateStep`, never here.

    **O(1) read, cost independent of ``audit_log`` row count (Req 9.7).** A
    single ``view`` call issues exactly five ``MetricStore.sum`` reads, one per
    ``SEC_*`` key, each over a fixed two-day range. Nothing grows with the number
    of audit rows or users.

    ---

    ## The trailing-24h window as a last-2-UTC-days aggregate sum (Req 9.3/9.5)

    Req 9.3 asks for the counts "over the trailing 24-hour window measured from
    request time". The only durable source is the ``SEC_*`` ``metrics_daily``
    keys, which are **per-UTC-day** aggregates populated by
    :class:`SecurityAggregateStep` for **closed** days (yesterday and older -
    today is not aggregated until it closes). Daily is the finest durable
    granularity we keep, so a strict, minute-accurate trailing-24h from "now" is
    not directly expressible.

    **Resolution (documented approximation).** We sum each ``SEC_*`` key over the
    **last two UTC days** - today (partial) + yesterday - via
    ``MetricStore.sum([key], day_from=yesterday, day_to=today)``. The real
    trailing-24h window always straddles at most these two calendar days, so
    their combined daily aggregate is the honest closest proxy the daily
    granularity allows. This is an **aggregate-only approximation**: it inherits
    the design's accepted closed-day eventual consistency - today's events are
    counted only after the rollup aggregates the day (they surface after the next
    rollup), so immediately after UTC midnight the number leans on yesterday's
    closed value until today is rolled up. We deliberately choose the two-day sum
    over "yesterday only" because it better covers the trailing-24h span and
    keeps today's activity visible once rolled up. We never scan ``audit_log`` to
    make this more precise (Req 9.5/9.6) - daily aggregates are the durable
    contract.

    ## Zero when no data (Req 9.5)

    ``MetricStore.sum`` returns ``0`` for a key with no stored rows in the range,
    so every count is ``0`` when its aggregate has no data - with **no** fallback
    to scanning ``audit_log`` rows (Req 9.5/9.6). ``SEC_*`` values are
    non-negative by construction (daily counts), so all counts are non-negative
    ints.

    ``windowHours`` is fixed at ``24``; ``computedAt`` is the current UTC time as
    an ISO-8601 string.
    """

    # Trailing-24h proxy spans at most two UTC calendar days (today + yesterday).
    _WINDOW_DAYS = 2
    _WINDOW_HOURS = 24

    def __init__(self, *, metric_store=None) -> None:
        # Optional injected read collaborator (tests); otherwise the process-wide
        # MetricStore singleton is resolved lazily. The service holds ONLY the
        # shared MetricStore - no AdminRepo / session - which structurally
        # guarantees it can never scan ``audit_log`` on the request path
        # (Req 9.6/9.7) and depends on no other Domain_Metrics_Service
        # (import-graph guard, Req 19.2/19.3/19.5).
        self._metric_store = metric_store

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def view(self) -> "SecurityView":
        """Return the 24h security counts from the ``SEC_*`` aggregates (Req 9.3-9.7).

        Sums each ``SEC_*`` Metric_Key over the last two UTC days (today +
        yesterday) via the shared ``MetricStore`` as the trailing-24h proxy - see
        the class docstring for the approximation rationale and the documented
        closed-day eventual-consistency tradeoff. Reads ONLY the ``MetricStore``;
        never scans ``audit_log`` (Req 9.6), returns ``0`` for empty aggregates
        (Req 9.5), and runs a fixed five reads regardless of row count (Req 9.7).
        """
        from app.admin.schemas import SecurityView

        store = self._get_metric_store()
        now = _now()
        day_to = _day_str(now)
        day_from = _day_str(now - timedelta(days=self._WINDOW_DAYS - 1))

        login_failed = await store.sum([SEC_LOGIN_FAILED], day_from, day_to)
        admin_login = await store.sum([SEC_ADMIN_LOGIN], day_from, day_to)
        authz_denied = await store.sum([SEC_AUTHZ_DENIED], day_from, day_to)
        rate_limited = await store.sum([SEC_RATE_LIMITED], day_from, day_to)
        suspicious = await store.sum([SEC_SUSPICIOUS], day_from, day_to)

        return SecurityView(
            windowHours=self._WINDOW_HOURS,
            loginFailed=login_failed,
            adminLogin=admin_login,
            authzDenied=authz_denied,
            rateLimited=rate_limited,
            suspicious=suspicious,
            # Honesty over a fabricated zero (Req 9.3 / audit): these two have no
            # durable aggregate source today - rate-limit denials are recorded
            # only as an in-process auth counter (never flushed per-day) and there
            # is no security-level "suspicious/blocked request" audit event - so
            # they are surfaced as explicitly not-instrumented rather than a
            # misleading 0. Wiring either would require new instrumentation (a
            # Non-Goal, Req 21.4) or cross-context coupling; when a durable source
            # is added, remove the name here and it becomes a real count.
            notInstrumented=list(_NOT_INSTRUMENTED),
            computedAt=now.isoformat(),
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
