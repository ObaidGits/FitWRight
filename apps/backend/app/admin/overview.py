"""Overview KPI-card read model - the OverviewService (Req 13).

This module is the overview bounded-context's owned home. Its
:class:`OverviewService` builds the :class:`~app.admin.schemas.OverviewKpis`
served by ``GET /api/v1/admin/kpis`` (Task 14.2): a handful of headline KPI
cards, each a :class:`~app.admin.schemas.KpiValue` (a value + an explicit
``unavailable`` marker) so the dashboard degrades gracefully instead of
hard-failing when one source is unavailable (Req 13.7).

**Bounded-context purity (Req 19.2/19.3/19.5) - the import-graph is the
constraint.** As a Domain_Metrics_Service module (``app.admin.overview`` is in
the fitness-test's ``DOMAIN_SERVICES`` set - Task 5.3) this may depend ONLY on
shared primitives and MUST NOT import another Domain_Metrics_Service
(``ai_metrics`` / ``errors_metrics`` / ``security_metrics`` / ...). It therefore
sources every KPI from shared primitives only:

- **totalUsers** <- the O(1) ``_TOTALS_DAY`` totals snapshot, read via
  ``MetricsService.stats()`` (``app.admin.metrics_service`` is NOT a
  Domain_Metrics_Service in the 5.3 set, so importing ``get_metrics_service`` is
  allowed; ``stats()`` reads the O(1) snapshot, never a full-table COUNT).
- **newUsersToday** <- a live current-day count via
  ``AdminRepo.metric_for_day("signups", ...)`` - ``AdminRepo`` is a shared
  primitive. This is the documented "durable key + live day" pattern: the rollup
  only holds *closed* days, so today's partial day is computed live (a single
  day-bounded, index-served count).
- **aiCallsToday** <- the durable ``AI_CALLS`` Metric_Key summed over today via
  ``MetricStore.sum`` (see the eventual-consistency note on :meth:`_ai_calls_today`).
- **errorRate24h** <- the durable ``REQUEST_*`` Metric_Keys via ``MetricStore.sum``.
- **purgeBacklog** <- the in-process ``AdminMetrics`` ``purge_backlog`` gauge.

Every read is O(1) (Req 13.6): a fixed, bounded number of snapshot / summed-key
reads plus one day-bounded signups count, none of which grows with user or row
count. All day / window boundaries are UTC (Req 13.3), and each KPI is computed
in isolation so one failing source never fails the whole response (Req 13.7).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.admin.metric_registry import (
    AI_CALLS,
    REQUEST_2XX,
    REQUEST_4XX,
    REQUEST_5XX,
)

logger = logging.getLogger(__name__)

__all__ = [
    "OverviewService",
    "get_overview_service",
    "reset_overview_service",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_str(dt: datetime) -> str:
    """UTC calendar day as ``YYYY-MM-DD`` (mirrors the rollup's day format)."""
    return dt.strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[str, str]:
    """Return the ``[start, end)`` UTC ISO bounds for a ``YYYY-MM-DD`` day.

    Mirrors ``MetricsService._day_bounds``/``AdminRepo`` day partitioning so the
    live current-day count uses the exact same UTC boundaries as the rollup
    (Req 13.3): ``00:00:00`` of ``day`` (inclusive) -> ``00:00:00`` of the next
    day (exclusive).
    """
    start_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return start_dt.isoformat(), end_dt.isoformat()


class OverviewService:
    """Overview KPI-card read model assembled from shared primitives only (Req 13).

    :meth:`kpis` builds the :class:`~app.admin.schemas.OverviewKpis`. It is a
    cohesive, single-responsibility Domain_Metrics_Service that depends ONLY on
    shared primitives - the shared :class:`~app.admin.metric_store.MetricStore`
    (durable ``AI_CALLS`` / ``REQUEST_*`` reads), the :class:`~app.admin.repo.AdminRepo`
    (the live "new users today" count), the totals snapshot via
    ``MetricsService.stats()``, the in-process ``AdminMetrics`` purge-backlog
    gauge, the static :mod:`app.admin.metric_registry`, and the response schema -
    never on another Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5;
    Task 5.3). Collaborators are injectable for tests; otherwise the process-wide
    singletons are resolved lazily.

    ---

    ## Per-KPI source mapping + every documented rule

    ``totalUsers`` (Req 13.1/13.4)
        The O(1) ``_TOTALS_DAY`` totals snapshot, read via
        ``MetricsService.stats()`` (``metrics_service`` is not a
        Domain_Metrics_Service, so this import is import-graph-clean). ``stats()``
        never runs a full-table COUNT on the request path - it reads the
        precomputed snapshot (cache -> snapshot rows). Its ``stale`` flag is also
        the source of the overall :attr:`OverviewKpis.stale` (see below).

    ``newUsersToday`` (Req 13.2/13.3)
        Users created during the **current UTC day**, counted **live** via
        ``AdminRepo.metric_for_day("signups", day_start, day_end)`` for today's
        UTC bounds. This is the "durable key + live day" pattern: the rollup only
        holds closed days, so the current partial day is computed live (one
        day-bounded, index-served count).

    ``aiCallsToday`` (Req 13.2/13.3/13.5)
        AI calls during the current UTC day, summed from the **durable**
        ``AI_CALLS`` Metric_Key via ``MetricStore.sum([AI_CALLS], today, today)``.
        See :meth:`_ai_calls_today` for the durable-only decision (Req 13.5 asks
        for durable + the live day counter, but reading the live counter would
        require importing ``AiMetricsService`` - a forbidden cross-domain import;
        we source the durable key only and document the eventual consistency).

    ``errorRate24h`` (Req 13.2/13.3)
        The server-error rate over the **trailing 24h** as a percentage bounded
        ``0.00``-``100.00`` (2dp). Computed from the durable ``REQUEST_*`` keys via
        ``MetricStore.sum`` over the last two UTC days (today + yesterday) - the
        same two-day trailing-24h proxy the security view uses, since daily is the
        finest durable granularity. ``errors`` = ``REQUEST_5XX`` (server errors);
        ``total`` = ``REQUEST_2XX + REQUEST_4XX + REQUEST_5XX``. When ``total == 0``
        the rate is ``0.00`` (0 requests => 0% error, a computable value - not
        "unavailable", which is reserved for a source that *cannot* be computed,
        Req 13.7).

    ``purgeBacklog`` (Req 13.2)
        The count of purge-due soft-deleted users, read from the in-process
        ``AdminMetrics`` ``purge_backlog`` gauge (last-write-wins, maintained by
        the cleanup job / ``live_admin_gauges``) - a pure in-memory read, no query.

    ``stale`` (Req 13.9 UI staleness signal)
        Overall staleness reflects the totals snapshot: it is the ``stale`` flag
        returned by ``MetricsService.stats()`` (True when the snapshot is older
        than a day / a rollup was missed). If ``stats()`` cannot be read at all,
        ``stale`` stays ``False`` (staleness cannot be confirmed) while
        ``totalUsers`` is reported unavailable.

    ``computedAt``
        Current UTC time as an ISO-8601 string.

    **Partial-response behaviour (Req 13.7).** Each KPI is computed in its own
    ``try/except``; a source that fails or cannot be computed yields
    ``KpiValue(value=None, unavailable=True)`` while every other KPI still
    returns its value - the request never fails as a whole.
    """

    # Trailing-24h proxy spans at most two UTC calendar days (today + yesterday),
    # matching SecurityMetricsService's documented daily-granularity approximation.
    _WINDOW_DAYS = 2

    def __init__(
        self,
        *,
        metric_store=None,
        admin_repo=None,
        metrics_service=None,
        admin_metrics=None,
    ) -> None:
        # Optional injected collaborators (tests); otherwise the process-wide
        # singletons are resolved lazily. All are shared primitives / non-domain
        # services - never another Domain_Metrics_Service (import-graph guard).
        self._metric_store = metric_store
        self._admin_repo = admin_repo
        self._metrics_service = metrics_service
        self._admin_metrics = admin_metrics

    # -- collaborator resolution (lazy singletons) ---------------------------

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _get_admin_repo(self):
        if self._admin_repo is not None:
            return self._admin_repo
        from app.admin.repo import get_admin_repo

        return get_admin_repo()

    def _get_metrics_service(self):
        if self._metrics_service is not None:
            return self._metrics_service
        from app.admin.metrics_service import get_metrics_service

        return get_metrics_service()

    def _get_admin_metrics(self):
        if self._admin_metrics is not None:
            return self._admin_metrics
        from app.admin.metrics import get_admin_metrics

        return get_admin_metrics()

    # -- public read model ---------------------------------------------------

    async def kpis(self) -> "OverviewKpis":
        """Return the Overview KPI cards from shared primitives only (Req 13).

        Assembles each KPI in isolation (per-KPI ``unavailable`` on failure -
        Req 13.7) using UTC day/window boundaries (Req 13.3). Every read is O(1)
        (Req 13.6). See the class docstring for the full source mapping.
        """
        from app.admin.schemas import KpiValue, OverviewKpis

        now = _now()

        total_users, stale = await self._total_users_and_stale()
        new_users_today = await self._new_users_today(now)
        ai_calls_today = await self._ai_calls_today(now)
        error_rate_24h = await self._error_rate_24h(now)
        purge_backlog = self._purge_backlog()

        return OverviewKpis(
            totalUsers=total_users,
            newUsersToday=new_users_today,
            aiCallsToday=ai_calls_today,
            errorRate24h=error_rate_24h,
            purgeBacklog=purge_backlog,
            computedAt=now.isoformat(),
            stale=stale,
        )

    # -- per-KPI computations (each isolated; Req 13.7) ----------------------

    async def _total_users_and_stale(self) -> "tuple[KpiValue, bool]":
        """``totalUsers`` from the O(1) totals snapshot + the overall stale flag.

        Reads ``MetricsService.stats()`` (the O(1) ``_TOTALS_DAY`` snapshot, never
        a full-table COUNT - Req 13.4/13.6). On failure the KPI is unavailable and
        ``stale`` stays ``False`` (staleness cannot be confirmed).
        """
        from app.admin.schemas import KpiValue

        try:
            stats = await self._get_metrics_service().stats()
            total = float(int(stats["totalUsers"]))
            stale = bool(stats.get("stale", False))
            return KpiValue(value=total), stale
        except Exception:
            logger.debug("Overview KPI: totalUsers snapshot read failed", exc_info=True)
            return KpiValue(value=None, unavailable=True), False

    async def _new_users_today(self, now: datetime) -> "KpiValue":
        """``newUsersToday`` - users created during the current UTC day (live).

        The "durable key + live day" pattern (Req 13.2/13.3): a single
        day-bounded, index-served ``signups`` count via ``AdminRepo`` for today's
        UTC bounds (the rollup only holds closed days).
        """
        from app.admin.schemas import KpiValue

        try:
            day_start, day_end = _day_bounds(_day_str(now))
            count = await self._get_admin_repo().metric_for_day(
                "signups", day_start, day_end
            )
            return KpiValue(value=float(max(0, int(count))))
        except Exception:
            logger.debug("Overview KPI: newUsersToday count failed", exc_info=True)
            return KpiValue(value=None, unavailable=True)

    async def _ai_calls_today(self, now: datetime) -> "KpiValue":
        """``aiCallsToday`` - durable ``AI_CALLS`` summed over the current UTC day.

        **Durable-only decision (Req 13.5 vs import-graph, Req 19.2/13.5).** Req
        13.5 specifies "durable AI Metric_Keys combined with the current live day
        counter". The live day counter lives in the in-process ``AiMetricsService``
        accumulator, but importing it here would be a cross-domain
        Domain_Metrics_Service import forbidden by the import-graph fitness test
        (Task 5.3) - ``OverviewService`` may not import ``ai_metrics``. So we source
        AI-calls-today from the **durable** ``AI_CALLS`` Metric_Key only, summed for
        today via ``MetricStore.sum([AI_CALLS], today, today)`` (a shared
        primitive). Documented tradeoff: today's not-yet-flushed in-process AI
        activity surfaces after the next metrics flush (acceptable eventual
        consistency, consistent with the design's closed-day contract). This is a
        deliberate deviation to preserve bounded-context purity.
        """
        from app.admin.schemas import KpiValue

        try:
            today = _day_str(now)
            total = await self._get_metric_store().sum([AI_CALLS], today, today)
            return KpiValue(value=float(max(0, int(total))))
        except Exception:
            logger.debug("Overview KPI: aiCallsToday sum failed", exc_info=True)
            return KpiValue(value=None, unavailable=True)

    async def _error_rate_24h(self, now: datetime) -> "KpiValue":
        """``errorRate24h`` - server-error rate (0.00-100.00) over trailing 24h.

        Trailing-24h proxy = the last two UTC days (today + yesterday), the finest
        durable granularity (mirrors the security view). ``errors`` = ``REQUEST_5XX``
        (server errors); ``total`` = ``REQUEST_2XX + REQUEST_4XX + REQUEST_5XX``, both
        summed from the durable Metric_Keys via ``MetricStore.sum`` (Req 13.2/13.3).
        ``total == 0`` => ``0.00`` (0 requests is 0% error - a computable value, not
        "unavailable"). The result is rounded to 2dp and clamped to ``0.00``-``100.00``
        (Req 13.2 bound).
        """
        from app.admin.schemas import KpiValue

        try:
            store = self._get_metric_store()
            day_to = _day_str(now)
            day_from = _day_str(now - timedelta(days=self._WINDOW_DAYS - 1))

            errors = await store.sum([REQUEST_5XX], day_from, day_to)
            total = await store.sum(
                [REQUEST_2XX, REQUEST_4XX, REQUEST_5XX], day_from, day_to
            )
            if total <= 0:
                rate = 0.0
            else:
                rate = round(errors / total * 100, 2)
                rate = max(0.0, min(100.0, rate))
            return KpiValue(value=rate)
        except Exception:
            logger.debug("Overview KPI: errorRate24h compute failed", exc_info=True)
            return KpiValue(value=None, unavailable=True)

    def _purge_backlog(self) -> "KpiValue":
        """``purgeBacklog`` - the in-process ``AdminMetrics`` ``purge_backlog`` gauge.

        A pure in-memory read (no DB query): ``get_admin_metrics().snapshot()``'s
        ``gauges['purge_backlog']`` (last-write-wins, maintained by the cleanup
        job / ``live_admin_gauges``). Unavailable if the gauge has never been set
        or cannot be read (Req 13.7).
        """
        from app.admin.schemas import KpiValue

        try:
            snapshot = self._get_admin_metrics().snapshot()
            gauges = snapshot.get("gauges", {}) if isinstance(snapshot, dict) else {}
            if "purge_backlog" not in gauges:
                # Never set -> cannot report a count (unavailable, not a false 0).
                return KpiValue(value=None, unavailable=True)
            return KpiValue(value=float(max(0.0, float(gauges["purge_backlog"]))))
        except Exception:
            logger.debug("Overview KPI: purgeBacklog gauge read failed", exc_info=True)
            return KpiValue(value=None, unavailable=True)


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.security_metrics.get_security_metrics_service)
# ---------------------------------------------------------------------------

_service: "OverviewService | None" = None


def get_overview_service() -> OverviewService:
    """Return the process-wide :class:`OverviewService` (built on first use)."""
    global _service
    if _service is None:
        _service = OverviewService()
    return _service


def reset_overview_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
