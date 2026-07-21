"""Errors summary read model - the ``ErrorsMetricsService`` (Req 5).

A cohesive, single-responsibility Domain_Metrics_Service (design §Bounded
Contexts) that assembles the grouped **errors summary** for the admin dashboard:
grouped 4xx/5xx counts, a top-10 failing-route-class list, by-source failure
counts, and a daily error-count trend. It exposes exactly one read method,
:meth:`ErrorsMetricsService.summary`, and depends **only** on the shared
:class:`~app.admin.metric_store.MetricStore`, the static
:mod:`app.admin.metric_registry`, and the response schemas - never on another
Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5; Property 9).

**Aggregate-only, never raw logs (Req 5, Property 4).** Every number is read
from a durable ``metrics_daily`` Metric_Key via ``MetricStore`` (a bounded set of
indexed ``(metric, day)`` reads) - there is no log line, stack trace, per-request
row, or ``audit_log`` scan anywhere in this module. The dashboard is an
operational summary, not a log/trace explorer (Non-Goal, Req 21.2).

**O(1) read (Req 5.7).** A single ``summary`` call issues a fixed, bounded number
of reads independent of user/row count: one ``MetricStore.sum`` for ``REQUEST_4XX``,
one for ``REQUEST_5XX``, one for ``AI_FAILURE``, plus two bounded ``series`` reads
(``REQUEST_4XX`` + ``REQUEST_5XX``) for the trend. No cost grows with data volume.

---

## Exact source mapping (and every documented gap)

``counts4xx`` / ``counts5xx`` (Req 5.1)
    ``sum(REQUEST_4XX)`` / ``sum(REQUEST_5XX)`` over the trailing ``window`` days,
    from ``metrics_daily`` only (both are non-negative ints by construction).

    **Live-today handling (documented).** Unlike ``AiMetricsService`` - whose
    in-process accumulator holds only *today's not-yet-flushed* counts (it is
    reset/consumed on every flush) and can therefore be safely added to the
    durable window sum - the in-process ``AdminMetrics`` request counters are
    **cumulative since process start** and are **not** windowed. Adding them to a
    windowed durable sum would massively over-count (it would add the whole
    process lifetime, not just today). We therefore read the **durable window
    only**. The consequence, documented for operators: errors that occurred today
    become visible in this summary only after the next rollup flush
    (``MetricsFlushStep``) persists them to ``metrics_daily`` - the same
    eventually-consistent "today is live after flush" tradeoff the design accepts
    for cumulative counters (design §Self-critique, accepted residual (a)).

``topRouteClasses`` (Req 5.2) - **returns ``[]`` (documented gap)**
    Req 5.2 wants the top failing route-classes "computed from aggregated
    route-class buckets". **There is no durable per-route-class failure
    Metric_Key**, and one cannot be added without violating the bounded-cardinality
    guarantee (Req 20 / Property 8): a durable key per arbitrary route-class is an
    unbounded, runtime-derived key family, which the Metric_Registry explicitly
    forbids. The only per-route-class signal that exists is the in-process
    ``AdminMetrics._latency`` bucket, which tracks request **count** (and latency)
    per route-class - **not failure count** - and only for the small set of admin
    route heads, in-process and non-durable. Surfacing request *volume* as a proxy
    would be misleading for a field defined as "by **failure** count", so we do
    **not** do that. Instead we return an empty list, which Req 5.2 explicitly
    permits ("SHALL return fewer than 10 entries when fewer route-classes have
    recorded failures") - currently none are bucketed durably. Adding real
    per-route-class failure bucketing would require a **bounded route-class enum**
    plus one static durable key per class; that is intentionally not added here to
    keep metric cardinality bounded and storage minimal.

``bySource`` (Req 5.3) - failure counts by API / job / storage / AI
    Populated from the real durable signals; absent sources report ``0`` (Req 5.3
    "absent sources report a count of zero"). Sources are independent instrument
    points and may overlap (e.g. an AI failure may also surface as a 5xx); they
    are reported independently, not de-duplicated.

    - ``api`` = ``counts4xx + counts5xx`` - all request failures observed at the
      API layer over the window. (Chosen over "5xx only" so the by-source ``api``
      figure matches the trend total, which is 4xx+5xx per day; the pure
      server-error count remains separately visible as ``counts5xx``.)
    - ``ai`` = ``sum(AI_FAILURE)`` over the window - a real durable signal.
      ``AI_TIMEOUTS`` is **not** added: a timed-out AI call is recorded as
      ``ok=False`` and thus already counted in ``AI_FAILURE`` (see
      ``AiMetricsService.record_call``), so adding timeouts would double-count.
    - ``job`` = ``0`` (**documented gap**). Background-job failures have no durable
      windowed Metric_Key - job outcomes live in KV run markers, not a windowed
      metric - so per Req 5.3 an absent source reports ``0``. A best-effort read of
      run markers is intentionally avoided (not windowed, would misreport).
    - ``storage`` = ``0`` (**documented gap**). No durable storage-failure signal
      exists; reported as ``0`` per Req 5.3.

``trend`` (Req 5.4)
    A daily total-error series over the window, one ``SeriesPoint`` per day,
    oldest->newest, sourced from ``metrics_daily``: ``value = REQUEST_4XX +
    REQUEST_5XX`` for each day (aligns with the 4xx+5xx "total errors" definition
    used for ``bySource.api``). Built from two bounded ``MetricStore.series`` reads
    summed per day. ``window`` is validated to 7/30/90 by the endpoint (Task 10.2);
    this service accepts any positive int and emits one point per day for
    ``window`` days.

``computedAt``
    Current UTC time as an ISO-8601 string. ``window`` is echoed back unchanged.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.admin.metric_registry import (
    AI_FAILURE,
    REQUEST_4XX,
    REQUEST_5XX,
)
from app.admin.schemas import (
    ErrorsBySource,
    ErrorsSummary,
    RouteClassFailures,
    SeriesPoint,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ErrorsMetricsService",
    "get_errors_metrics_service",
    "reset_errors_metrics_service",
]


class ErrorsMetricsService:
    """Grouped 4xx/5xx errors summary from durable buckets (Req 5).

    Reads only durable ``metrics_daily`` Metric_Keys through the shared
    :class:`~app.admin.metric_store.MetricStore`; holds no cross-domain
    dependency. The optional injected ``metric_store`` lets tests supply an
    isolated store; otherwise the process-wide singleton is resolved lazily.
    """

    def __init__(self, *, metric_store=None) -> None:
        # Optional injected read collaborator (tests); otherwise the process-wide
        # MetricStore singleton is resolved lazily. The service depends ONLY on
        # the shared MetricStore + Metric_Registry + schemas - never on another
        # Domain_Metrics_Service (import-graph guard, Req 19.2/19.3/19.5).
        self._metric_store = metric_store

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def summary(self, window: int) -> ErrorsSummary:
        """Return the errors summary for the trailing ``window`` days (Req 5.1-5.4).

        Assembles grouped 4xx/5xx counts, the (currently empty) top-route-class
        list, by-source failure counts, and a daily error-count trend - all from
        durable ``metrics_daily`` keys via the shared ``MetricStore``. See the
        module docstring for the exact source mapping and every documented gap.

        **O(1) read (Req 5.7).** A fixed, bounded number of indexed reads runs
        regardless of data volume: three ``sum`` reads (4xx / 5xx / AI failures)
        plus two bounded ``series`` reads for the trend. No row scan, no raw logs.
        """
        store = self._get_metric_store()
        now = datetime.now(timezone.utc)
        win = max(1, int(window))
        day_to = now.strftime("%Y-%m-%d")
        day_from = (now - timedelta(days=win - 1)).strftime("%Y-%m-%d")

        # -- grouped request-failure counts (Req 5.1), durable window only ----
        # Cumulative in-process AdminMetrics counters are intentionally NOT folded
        # in (they are not windowed - see module docstring "Live-today handling").
        counts_4xx = await store.sum([REQUEST_4XX], day_from, day_to)
        counts_5xx = await store.sum([REQUEST_5XX], day_from, day_to)

        # -- by-source failure counts (Req 5.3) -------------------------------
        # api = all request failures; ai = durable AI failures (timeouts already
        # counted in AI_FAILURE); job/storage have no durable signal -> 0.
        ai_failures = await store.sum([AI_FAILURE], day_from, day_to)
        by_source = ErrorsBySource(
            api=counts_4xx + counts_5xx,
            job=0,       # documented gap: no durable windowed job-failure key
            storage=0,   # documented gap: no durable storage-failure signal
            ai=ai_failures,
        )

        # -- top failing route-classes (Req 5.2) ------------------------------
        # Empty by design: no durable per-route-class FAILURE bucket exists, and
        # adding one per arbitrary route would break bounded cardinality (Req 20).
        # Req 5.2 explicitly permits fewer than 10 entries. See module docstring.
        top_route_classes: list[RouteClassFailures] = []

        # -- daily error-count trend (Req 5.4) --------------------------------
        # total errors per day = REQUEST_4XX + REQUEST_5XX, oldest->newest. Both
        # series cover the same trailing window, so they align index-by-index.
        series_4xx = await store.series(REQUEST_4XX, win)
        series_5xx = await store.series(REQUEST_5XX, win)
        by_day_5xx = {day: value for day, value in series_5xx}
        trend: list[SeriesPoint] = [
            SeriesPoint(date=day, value=value + by_day_5xx.get(day, 0))
            for day, value in series_4xx
        ]

        return ErrorsSummary(
            window=int(window),
            counts4xx=counts_4xx,
            counts5xx=counts_5xx,
            topRouteClasses=top_route_classes,
            bySource=by_source,
            trend=trend,
            # Honesty over a misleading empty/zero (audit fix): these have no
            # durable source today - there is no bounded per-route-class failure
            # bucket (adding one per arbitrary route would break bounded
            # cardinality, Req 20), and no durable job/storage failure counter.
            # The UI renders "Not instrumented" for them rather than implying
            # "zero failures". ``bySource.api`` (request 4xx+5xx) and
            # ``bySource.ai`` (AI_FAILURE) ARE real and are never listed.
            notInstrumented=["topRouteClasses", "bySource.job", "bySource.storage"],
            computedAt=now.isoformat(),
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (mirrors app.admin.ai_metrics.get_ai_metrics_service)
# ---------------------------------------------------------------------------

_service: ErrorsMetricsService | None = None


def get_errors_metrics_service() -> ErrorsMetricsService:
    """Return the process-wide :class:`ErrorsMetricsService` (built on first use)."""
    global _service
    if _service is None:
        _service = ErrorsMetricsService()
    return _service


def reset_errors_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
