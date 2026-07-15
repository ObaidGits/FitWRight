"""Metric registry, daily rollup, dashboards, and counter reconciliation (Task 2).

Implements the design's "Metrics & rollup" section:

- **Metric registry** (:data:`METRIC_REGISTRY`) with exact UTC-day definitions;
  an unknown metric raises so the router returns 400 ``unknown_metric`` (R3.3).
- ``GET /stats`` support: overview stats from indexed aggregates + a bounded
  live-today read, cached in the KVStore (60s TTL) with ``computed_at``; on a
  live/cache failure the last cached value is returned with ``stale:true`` rather
  than erroring the dashboard (R2.2/2.4).
- ``GET /usage-series`` support: closed UTC days are read from ``metrics_daily``
  (falling back to a live compute before the first rollup so the chart isn't
  gappy), and the current partial day is always computed live and appended —
  never double-counted, since the rollup only ever writes closed days (Property 6).
- :meth:`run_rollup` — UPSERTs each registry metric for the just-closed day(s)
  (idempotent, safe to re-run); :meth:`backfill` populates a historical range;
  :meth:`reconcile_counters` corrects drift in the denormalized usage counters.

The rollup/reconciliation are single-flighted by the caller (the job runner's
KVStore lock), so the select-then-write UPSERT here is race-free.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.admin.metric_registry import AI_CALLS, REQUEST_2XX, REQUEST_4XX, REQUEST_5XX
from app.admin.repo import AdminRepo, get_admin_repo
from app.models import MetricsDaily, User

# NOTE: ``StepResult`` is imported LAZILY inside :meth:`MetricsFlushStep.run`
# (not at module top-level) to avoid an import cycle: ``rollup_pipeline`` imports
# ``METRICS_FLUSH_STEP`` from this module to build its static ``PIPELINE`` list,
# so this module must not import from ``rollup_pipeline`` at load time.

logger = logging.getLogger(__name__)

__all__ = [
    "METRIC_REGISTRY",
    "UnknownMetricError",
    "MetricsService",
    "get_metrics_service",
    "reset_metrics_service",
    "MetricsFlushStep",
    "METRICS_FLUSH_STEP",
]

# The documented, extensible metric registry (R3.1). Adding a *computed* metric
# is a one-line change here + a branch in ``AdminRepo.metric_for_day``.
METRIC_REGISTRY: frozenset[str] = frozenset({"signups", "active_users", "resumes_tailored"})

# Durable usage-series keys (admin-panel-upgrade Req 4.9). Unlike the computed
# METRIC_REGISTRY metrics (derived live via ``AdminRepo.metric_for_day``), these
# are static ``metrics_daily`` Metric_Keys written by a Rollup_Step, so the
# usage-series serves them DIRECTLY from ``metrics_daily`` (no ``metric_for_day``
# compute). The current partial day is served live from the owning in-process
# accumulator so today isn't stale (see ``_live_durable_today``). ``AI_CALLS`` is
# registered here (Task 9.3); other durable keys (SEC_LOGIN_FAILED, REQUEST_5XX,
# RESUMES_*, FEAT_*) are added by their own tasks.
_DURABLE_USAGE_SERIES: frozenset[str] = frozenset({AI_CALLS})

_ALLOWED_WINDOWS: frozenset[int] = frozenset({7, 30, 90})
_STATS_CACHE_KEY = "admin:stats"
_STATS_CACHE_TTL = 60  # seconds (R2.2)

# Reserved ``metrics_daily.day_utc`` sentinel under which the overview TOTALS
# snapshot is stored (one row per stat key). It can never collide with a real
# ``YYYY-MM-DD`` day, so the daily series queries are unaffected. The RollupJob
# recomputes these (off the hot path) and ``/stats`` reads them O(1) — the
# dashboard never runs a full-table COUNT on a request path (R2.3, R11.2).
_TOTALS_DAY = "_totals_"

# The overview stat keys persisted in the totals snapshot.
_TOTALS_KEYS = (
    "totalUsers",
    "activeUsers",
    "disabledUsers",
    "totalResumes",
    "resumesTailored",
    "applications",
    "coverLettersGenerated",
    "interviewPrepsGenerated",
    "outreachGenerated",
    "signups",
)


class UnknownMetricError(ValueError):
    """Raised for a metric outside the registry (router → 400 unknown_metric)."""


async def live_admin_gauges(cutoff_iso: str) -> dict[str, int]:
    """Worker-independent purge/soft-delete gauges (admin-owned read use-case).

    Exposed so the machine ``/internal/metrics`` endpoint reads these live from
    the admin module's own repo *through* the admin module — foreign modules
    never import ``app.admin.repo`` directly (ARCHITECTURE Amendment E).
    """
    repo = get_admin_repo()
    return {
        "purge_backlog": await repo.purge_backlog(cutoff_iso),
        "soft_deleted_total": await repo.soft_deleted_count(),
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _day_bounds(day: str) -> tuple[str, str]:
    """Return the ``[start, end)`` UTC iso bounds for a ``YYYY-MM-DD`` day."""
    start_dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=1)
    return start_dt.isoformat(), end_dt.isoformat()


class MetricsService:
    """Rollup + dashboards + counter reconciliation."""

    def __init__(self, session_factory: async_sessionmaker, repo: AdminRepo, *, kvstore=None) -> None:
        self._session_factory = session_factory
        self._repo = repo
        self._kv = kvstore

    def _kvstore(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    # -- overview stats ------------------------------------------------------

    async def stats(self, *, active_window_days: int = 30) -> dict:
        """Return the overview stats dict (+ ``computedAt``/``stale``), O(1).

        Read path (never a full-table scan — R2.3/R11.2):
        1. KVStore cache (60s) → return.
        2. The precomputed **totals snapshot** in ``metrics_daily`` (a handful of
           PK reads) → return, marking ``stale`` if the snapshot is older than a
           day (missed rollup — R2.4).
        3. Only if the snapshot has never been written (first-ever call before
           any rollup) do we compute it live once and persist it (documented
           one-time bootstrap), so steady state is always O(1).

        The expensive aggregates are otherwise computed exclusively by the
        RollupJob, off the request path (:meth:`refresh_totals_snapshot`).
        """
        from app.admin.metrics import get_admin_metrics

        cache_key = _STATS_CACHE_KEY
        kv = self._kvstore()
        metrics = get_admin_metrics()

        # 1) hot cache
        try:
            cached = await kv.get(cache_key)
        except Exception:
            cached = None
        if cached is not None:
            try:
                data = json.loads(cached)
                metrics.record_cache(hit=True)
                self._record_staleness(metrics, data.get("computedAt"))
                return data
            except (ValueError, TypeError):
                pass
        metrics.record_cache(hit=False)

        # 2) precomputed snapshot (O(1)); on a DB outage degrade to the last
        #    known value with stale:true rather than erroring the dashboard (R2.4).
        try:
            snapshot = await self._read_totals_snapshot()
            if snapshot is None:
                # 3) one-time bootstrap before the first rollup.
                snapshot = await self.refresh_totals_snapshot(active_window_days=active_window_days)
        except Exception:
            logger.exception("Overview stats snapshot read failed; attempting stale fallback")
            fallback = await self._read_last_known(kv)
            if fallback is not None:
                fallback["stale"] = True
                return fallback
            raise

        computed_at = snapshot["computedAt"]
        stale = self._is_snapshot_stale(computed_at)
        data = {**{k: snapshot.get(k, 0) for k in _TOTALS_KEYS}, "computedAt": computed_at, "stale": stale}
        self._record_staleness(metrics, computed_at)
        try:
            await kv.set(cache_key, json.dumps(data), ttl_seconds=_STATS_CACHE_TTL)
            # Persist a never-expiring "last known" copy for the R2.4 fallback.
            await kv.set(f"{_STATS_CACHE_KEY}:last", json.dumps(data))
        except Exception:
            logger.debug("Failed to cache admin stats", exc_info=True)
        return data

    @staticmethod
    async def _read_last_known(kv) -> dict | None:
        try:
            raw = await kv.get(f"{_STATS_CACHE_KEY}:last")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    @staticmethod
    def _record_staleness(metrics, computed_at: str | None) -> None:
        if not computed_at:
            return
        try:
            age = (_now() - datetime.fromisoformat(computed_at)).total_seconds()
            metrics.set_dashboard_staleness(max(0.0, age))
        except (ValueError, TypeError):
            pass

    @staticmethod
    def _is_snapshot_stale(computed_at: str) -> bool:
        """A totals snapshot older than ~25h means a rollup was missed (R2.4)."""
        try:
            age = (_now() - datetime.fromisoformat(computed_at)).total_seconds()
        except (ValueError, TypeError):
            return True
        return age > 25 * 3600

    async def _read_totals_snapshot(self) -> dict | None:
        """Read the O(1) totals snapshot from ``metrics_daily`` (None if absent)."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MetricsDaily.metric, MetricsDaily.value, MetricsDaily.computed_at).where(
                        MetricsDaily.day_utc == _TOTALS_DAY
                    )
                )
            ).all()
        if not rows:
            return None
        snapshot = {metric: int(value) for metric, value, _ in rows}
        snapshot["computedAt"] = max(computed_at for _, _, computed_at in rows)
        return snapshot

    async def refresh_totals_snapshot(self, *, active_window_days: int = 30) -> dict:
        """Recompute + persist the overview totals snapshot (RollupJob path).

        This is the ONE place the expensive cross-table aggregates run, and it is
        only ever invoked by the background rollup (single-flighted) or the
        one-time hot-path bootstrap — never on a steady-state request.
        """
        base = await self._repo.overview_stats(active_window_days=active_window_days)
        now_iso = _now().isoformat()
        async with self._session_factory() as session:
            for metric in _TOTALS_KEYS:
                value = int(base.get(metric, 0))
                existing = await session.get(MetricsDaily, (_TOTALS_DAY, metric))
                if existing is None:
                    session.add(
                        MetricsDaily(
                            day_utc=_TOTALS_DAY, metric=metric, value=value, computed_at=now_iso
                        )
                    )
                else:
                    existing.value = value
                    existing.computed_at = now_iso
            await session.commit()
        return {**base, "computedAt": now_iso}

    # -- usage series --------------------------------------------------------

    async def usage_series(self, metric: str, window: int) -> dict:
        """Return the daily series for ``metric`` over ``window`` days (R3.1/3.2).

        Two metric families are served (admin-panel-upgrade Req 4.9):

        - **Computed** metrics (:data:`METRIC_REGISTRY`) — each day is derived
          live via ``AdminRepo.metric_for_day`` (today + any closed day missing
          from the rollup); closed days present in the rollup are read from
          ``metrics_daily``.
        - **Durable** metrics (:data:`_DURABLE_USAGE_SERIES`, e.g. ``AI_CALLS``) —
          static ``metrics_daily`` keys written by a Rollup_Step; every closed day
          is read straight from ``metrics_daily`` (never ``metric_for_day``, which
          has no branch for them), and the current partial day is served live from
          the owning in-process accumulator so today isn't stale.
        """
        is_durable = metric in _DURABLE_USAGE_SERIES
        if metric not in METRIC_REGISTRY and not is_durable:
            raise UnknownMetricError(metric)
        if window not in _ALLOWED_WINDOWS:
            window = 30

        now = _now()
        today = _day_str(now)
        days = [_day_str(now - timedelta(days=i)) for i in range(window - 1, -1, -1)]

        # Bulk-read the closed days present in the rollup.
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(MetricsDaily.day_utc, MetricsDaily.value).where(
                        MetricsDaily.metric == metric, MetricsDaily.day_utc.in_(days)
                    )
                )
            ).all()
        rolled = {day: int(v) for day, v in rows}

        points = []
        for day in days:
            if is_durable:
                # Durable metrics_daily key: read the stored value directly (0 if
                # no row yet). Today additionally folds in the live accumulator.
                value = rolled.get(day, 0)
                if day == today:
                    value += self._live_durable_today(metric)
            elif day == today:
                # Current partial day: always live (never from the rollup).
                start, end = _day_bounds(day)
                value = await self._repo.metric_for_day(metric, start, end)
            elif day in rolled:
                value = rolled[day]
            else:
                # Closed day missing from the rollup (pre-first-run) → live fallback.
                start, end = _day_bounds(day)
                value = await self._repo.metric_for_day(metric, start, end)
            points.append({"date": day, "value": value})

        return {
            "metric": metric,
            "window": window,
            "points": points,
            "computedAt": now.isoformat(),
        }

    @staticmethod
    def _live_durable_today(metric: str) -> int:
        """Live current-day value for a durable usage-series key (0 if none).

        Keeps the current partial day fresh for durable metrics_daily keys, whose
        today's counts have not yet been flushed by the Rollup_Step. For
        ``AI_CALLS`` this reads the in-process ``AiMetricsService`` accumulator.
        Imported lazily to avoid a load-time import cycle; fails soft to 0.
        """
        if metric == AI_CALLS:
            try:
                from app.admin.ai_metrics import get_ai_metrics_service

                snap = get_ai_metrics_service().snapshot()
                return int(snap.get("calls", 0) or 0)
            except Exception:
                logger.debug("Live AI_CALLS read failed for usage-series", exc_info=True)
                return 0
        return 0

    # -- rollup --------------------------------------------------------------

    async def run_rollup(self, *, lookback_days: int = 3) -> dict:
        """UPSERT each registry metric for the last ``lookback_days`` CLOSED days.

        Only closed UTC days are written (today is always live), so re-running is
        safe and never double-counts (R3.2/10.3). ``lookback_days`` > 1 lets a
        run recover a missed prior run. Returns per-day/metric write counts.
        """
        now = _now()
        written = 0
        # Closed days are yesterday and older.
        for i in range(1, lookback_days + 1):
            day = _day_str(now - timedelta(days=i))
            start, end = _day_bounds(day)
            for metric in sorted(METRIC_REGISTRY):
                value = await self._repo.metric_for_day(metric, start, end)
                await self._upsert_daily(day, metric, value)
                written += 1
        # Refresh the O(1) overview totals snapshot (the expensive cross-table
        # aggregates run here, off the request path — H1/M1 fix).
        await self.refresh_totals_snapshot()
        # Freshness gauge: days since the most recent rolled-up day.
        from app.admin.metrics import get_admin_metrics

        get_admin_metrics().set_rollup_lag_days(0.0)
        return {"written": written, "lookback_days": lookback_days}

    async def backfill(self, from_day: str, to_day: str) -> dict:
        """Idempotently populate ``metrics_daily`` for ``[from_day, to_day]``.

        Both bounds are inclusive ``YYYY-MM-DD`` UTC days; the current/future
        days are skipped (only closed days are rolled up). Safe to re-run.
        """
        start = datetime.strptime(from_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(to_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = _day_str(_now())
        written = 0
        cursor = start
        while cursor <= end:
            day = _day_str(cursor)
            if day < today:  # only closed days
                d_start, d_end = _day_bounds(day)
                for metric in sorted(METRIC_REGISTRY):
                    value = await self._repo.metric_for_day(metric, d_start, d_end)
                    await self._upsert_daily(day, metric, value)
                    written += 1
            cursor += timedelta(days=1)
        return {"written": written}

    async def _upsert_daily(self, day: str, metric: str, value: int) -> None:
        """Insert-or-update one ``metrics_daily`` row (single-flighted by caller)."""
        now_iso = _now().isoformat()
        async with self._session_factory() as session:
            existing = await session.get(MetricsDaily, (day, metric))
            if existing is None:
                session.add(
                    MetricsDaily(day_utc=day, metric=metric, value=int(value), computed_at=now_iso)
                )
            else:
                existing.value = int(value)
                existing.computed_at = now_iso
            await session.commit()

    # -- counter reconciliation ---------------------------------------------

    async def reconcile_counters(self, *, batch_size: int = 500) -> dict:
        """Correct drift in ``users.resume_count`` / ``application_count`` (R11.3).

        **Chunked + resumable** (M3 fix): users are walked in id-ordered keyset
        batches; for each batch the exact counts are computed with a bounded
        ``WHERE user_id IN (batch)`` group-by (via the isolated ``AdminRepo``) and
        only drifted rows are written. Memory and transaction size are bounded by
        ``batch_size`` regardless of total user count. Users with no owned rows
        converge to 0.
        """
        fixed = 0
        scanned = 0
        after: str | None = None
        while True:
            ids = await self._repo.user_ids_after(after, batch_size)
            if not ids:
                break
            after = ids[-1]
            scanned += len(ids)
            resume_counts = await self._repo.resume_counts_for_users(ids)
            app_counts = await self._repo.application_counts_for_users(ids)
            async with self._session_factory() as session:
                rows = (
                    await session.execute(select(User).where(User.id.in_(ids)))
                ).scalars().all()
                batch_fixed = 0
                for user in rows:
                    rc = resume_counts.get(user.id, 0)
                    ac = app_counts.get(user.id, 0)
                    if (user.resume_count or 0) != rc or (user.application_count or 0) != ac:
                        user.resume_count = rc
                        user.application_count = ac
                        batch_fixed += 1
                if batch_fixed:
                    await session.commit()
                fixed += batch_fixed
            if len(ids) < batch_size:
                break
        return {"reconciled": fixed, "scanned": scanned}


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_service: MetricsService | None = None


def get_metrics_service() -> MetricsService:
    """Return the process-wide :class:`MetricsService` (bound to the app DB)."""
    global _service
    if _service is None:
        from app.database import db

        _service = MetricsService(db.session_factory, get_admin_repo())
    return _service


def reset_metrics_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None


# ---------------------------------------------------------------------------
# MetricsFlushStep — durable flush of ephemeral in-process AdminMetrics (Req 2)
# ---------------------------------------------------------------------------
#
# The in-process ``AdminMetrics`` counters (``request_2xx`` / ``request_4xx`` /
# ``request_5xx``) are **cumulative since process start** and are **not**
# day-partitioned. This step bridges them into the durable per-(day, key)
# ``metrics_daily`` rows via :class:`~app.admin.metric_store.MetricStore`,
# satisfying Requirement 2 as follows:
#
# - **Per-day attribution (Req 2.2).** The step always flushes to the *current*
#   accumulating UTC day (``today``), computed inside the step — NOT the ``day``
#   argument the pipeline passes (that is the just-closed "yesterday" the
#   day-scoped aggregate steps roll up). The in-process counters accumulate for
#   the running process, so their new activity belongs to today.
#
# - **Cross-worker summation, no per-worker/per-event rows (Req 2.4).** Each
#   worker holds its own cumulative counters and its own per-process
#   ``last_flushed`` baseline. On each run a worker computes
#   ``delta = current_cumulative - last_flushed`` and calls
#   ``MetricStore.add(today, key, delta)`` — an atomic in-UPSERT increment. Every
#   worker adds only its *own* new delta, and the UPSERT sums them into the single
#   ``(day, key)`` row. No per-worker or per-event row is ever written.
#
# - **Restart durability (Req 2.3).** Deltas are persisted cumulatively into
#   ``metrics_daily``; a restart only resets the in-process baseline, never the
#   durable row, so previously flushed days keep their persisted totals.
#
# - **Per-key failure isolation (Req 2.5).** A failed ``add`` for one key is
#   caught, its key collected, and its ``last_flushed`` baseline left UNadvanced
#   (so the same delta is retried on the next run and the persisted value is left
#   unchanged); the remaining keys are still flushed. The step returns a failed
#   :class:`StepResult` naming the failed key(s).
#
# - **Closed-day re-run is a no-op (Req 2.6).** Because the step only ever targets
#   ``today``, a re-run never re-writes a closed day's row — the deltas for a day
#   were all added while that day was current. A per-day ``flushed_at`` KV marker
#   records finalization (observability + a guard against re-flushing a day).
#
# Day-boundary handling: the step tracks ``(_last_flushed_day, _last_flushed)``.
# When ``today`` differs from ``_last_flushed_day`` (a new UTC day, or first run),
# the baseline is re-seeded to the *current* cumulative values and nothing is
# added for the crossing. This prevents cross-day bleed (activity that accrued on
# the prior day after its last flush is not mis-attributed to the new day); the
# prior day keeps whatever was flushed while it was the current day. This is the
# documented, consistent approximation for un-partitioned cumulative counters.
#
# AI_CALLS ownership: AI-call counts are intentionally NOT flushed here. Per the
# design (Req 4.2 / Task 9.2) the ``AI_*`` keys — including ``AI_CALLS`` and the
# five per-provider keys — are owned exclusively by ``AiFlushStep``. Flushing
# ``AI_CALLS`` from two steps would double-count it, so this step keeps single
# ownership per key and flushes only the request status buckets. Admin-action
# durable keys were dropped by design (they remain in-process only).

# Durable Metric_Key -> in-process ``AdminMetrics`` counter source name.
_FLUSH_KEY_SOURCES: tuple[tuple[str, str], ...] = (
    (REQUEST_2XX, "request_2xx"),
    (REQUEST_4XX, "request_4xx"),
    (REQUEST_5XX, "request_5xx"),
)

# Named-KV prefix for the per-day ``flushed_at`` marker (Req 2.6 observability).
_FLUSH_MARKER_PREFIX = "metrics_flush"


class MetricsFlushStep:
    """Rollup_Step flushing in-process ``AdminMetrics`` request buckets (Req 2).

    Independent, idempotent per closed UTC day (only ``today`` is ever written),
    resumable (a failed key retries next run), and failure-isolated per key. See
    the module-level notes above for the delta/day-boundary reasoning and the
    AI_CALLS single-ownership decision.
    """

    name = "metrics_flush"

    def __init__(self, *, metric_store=None) -> None:
        # Optional injected store (tests); otherwise the process-wide singleton.
        self._store = metric_store
        # Per-process flush baseline. ``_last_flushed`` maps durable key ->
        # cumulative counter value at the last successful flush; advanced only on
        # a successful ``add`` so a failed key retries its delta next run.
        self._last_flushed_day: str | None = None
        self._last_flushed: dict[str, int] = {}

    def _metric_store(self):
        if self._store is not None:
            return self._store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def run(self, day: str) -> "StepResult":  # noqa: ARG002 - see day note above
        from app.admin.metrics import get_admin_metrics

        # Lazy import breaks the load-time cycle (rollup_pipeline imports this
        # module to assemble PIPELINE); StepResult is only needed at call time.
        from app.admin.rollup_pipeline import StepResult

        store = self._metric_store()
        today = _day_str(_now())  # accumulating day (NOT the passed closed day)

        snapshot = get_admin_metrics().snapshot()
        counters = snapshot.get("counters", {}) if isinstance(snapshot, dict) else {}

        # Day boundary (or first run): re-seed the baseline to the current
        # cumulative values so prior-day activity does not bleed into today.
        if self._last_flushed_day != today:
            self._last_flushed = {
                key: int(counters.get(src, 0)) for key, src in _FLUSH_KEY_SOURCES
            }
            self._last_flushed_day = today

        failed_keys: list[str] = []
        for key, src in _FLUSH_KEY_SOURCES:
            current = int(counters.get(src, 0))
            delta = current - int(self._last_flushed.get(key, 0))
            if delta <= 0:
                # Nothing new (or a counter reset via a restart baseline); skip.
                continue
            try:
                await store.add(today, key, delta)
            except Exception:  # per-key failure isolation (Req 2.5)
                logger.exception(
                    "MetricsFlushStep failed to flush %s for %s", key, today
                )
                failed_keys.append(key)
                # Leave the baseline UNadvanced so this delta retries next run and
                # the persisted value for the failed key is left unchanged.
                continue
            self._last_flushed[key] = current

        # Per-day ``flushed_at`` marker: idempotency/finalization signal (Req 2.6)
        # + observability. Best-effort — a marker write failure never fails the
        # step (the durable counter adds are the source of truth).
        try:
            await store.snapshot_put(
                f"{_FLUSH_MARKER_PREFIX}:{today}",
                {
                    "day": today,
                    "flushed_at": _now().isoformat(),
                    "last_flushed": dict(self._last_flushed),
                    "failed_keys": failed_keys,
                },
            )
        except Exception:
            logger.debug(
                "MetricsFlushStep marker write failed for %s", today, exc_info=True
            )

        if failed_keys:
            return StepResult.failure(
                self.name, f"metrics flush failed for keys: {', '.join(failed_keys)}"
            )
        return StepResult.success(self.name)


# Process-wide instance appended to PIPELINE by Task 4.2 (before the prune step).
# Single-flighted by the Rollup_Job's KVStore lock, so the shared per-process
# baseline is only ever driven by one run at a time within a worker.
METRICS_FLUSH_STEP = MetricsFlushStep()
