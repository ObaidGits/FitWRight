"""Background-jobs panel read service (Task 7.1, Requirement 8).

``JobsPanelService.panel()`` assembles the :class:`~app.admin.schemas.JobsPanel`
served by ``GET /api/v1/admin/jobs`` — a per-job status table plus the
worker-independent queue/backlog gauges the operator needs to see whether the
background jobs are running, stuck, or backed up.

**O(1), never a row scan (Req 8.4 — <500ms).** Every field is read from data
that is already pre-computed off the request path:

- Per-job state comes from the KV **run markers** written by
  :mod:`app.admin.job_markers` (``job_marker:{job_name}`` snapshots) — a handful
  of KV point reads, one per job.
- The purge backlog comes from the in-process ``AdminMetrics`` gauge (set by the
  rollup/purge jobs), falling back to the worker-independent
  :func:`~app.admin.metrics_service.live_admin_gauges` bounded count when the
  gauge has not been populated in this worker yet.
- Lock state is a single best-effort KV point read per job.

No path here scans ``audit_log``/``users``/``metrics_daily`` rows.

**Job set.** Only the three jobs run through
:func:`app.admin.jobs.run_admin_jobs` — ``rollup``, ``purge``,
``audit_retention`` — have run markers and single-flight lock keys, so those are
the rows surfaced. ``reaper``/``outbox`` are driven from a *separate* path (the
session reaper loop / the productivity outbox worker in the Product bounded
context) and do not publish admin run markers or an ``AdminMetrics`` gauge, so
they are intentionally **not** fabricated here (surfacing them would require
cross-context coupling and a live query, violating Req 8.4/21). The outbox/queue
length is likewise surfaced as *unavailable* (Req 8.7) until a worker-independent
admin gauge for it exists.

**Requirement → field mapping (documented derivations).**

- *Execution state {running, failed, completed} (Req 8.1).* ``JobRow`` has no
  explicit ``state`` enum — by design (Task 5.1). The panel expresses state via
  existing fields, and the frontend (Task 7.2) derives the badge from them:
    - **running**  ⇔ ``runningSince`` is not null (or ``lockState == "held"``),
    - **failed**   ⇔ ``lastOutcome == "failure"``,
    - **completed** ⇔ ``lastOutcome in ("success", "skipped")`` and not running.
  No schema field is added, avoiding churn to the shared model + its secret-free
  test.
- *Retry count (Req 8.1).* These jobs are **single-flighted and resumable**, not
  retried within a run: a crash mid-run is recovered by the *next* scheduled
  invocation, and no retry counter is recorded in the markers. There is therefore
  no per-run retry count to surface, and ``JobRow`` deliberately carries no
  ``retryCount`` field (documented gap — adding a nullable/zero field would only
  ever report 0). If a genuine bounded-retry mechanism is introduced later, a
  nullable ``retryCount`` can be added to ``JobRow`` + the frontend type together.
- *last/next execution (Req 8.1/8.5).* ``lastRun`` = marker ``last_run``;
  ``nextRun`` = marker ``next_run`` (**null when unscheduled** under external
  cron — Req 8.5).
- *last-success / running-since / current & expected duration (Req 8.8/8.9).*
  From the marker's ``last_success`` / ``running_since`` /
  ``expected_duration_seconds``; ``currentDurationSeconds`` = ``now -
  running_since`` while running, else null.
- *potentially-stuck (Req 8.10).* Computed from the marker + config only: when a
  job is running, it is stuck if the current duration exceeds
  ``expected_duration * admin_job_stuck_multiplier`` (when an expected duration
  exists) or the absolute ``admin_job_stuck_ceiling_seconds`` fallback otherwise.
- *lock state {held, free} (Req 8.3).* A best-effort presence check of the job's
  single-flight KV lock key; *unavailable* (``None``) rather than a crash when it
  cannot be determined (Req 8.7).

**Bounded-context purity (Req 19).** This service depends only on shared
primitives (Metric_Store, ``AdminMetrics`` gauges, the KVStore, config, the job
lock-key constants, and the schemas). It imports no other Domain_Metrics_Service,
so the import-graph guard holds.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from app.admin.job_markers import job_marker_name
from app.admin.jobs import (
    AUDIT_RETENTION_LOCK_KEY,
    PURGE_LOCK_KEY,
    ROLLUP_LOCK_KEY,
)
from app.admin.metrics import get_admin_metrics
from app.admin.schemas import JobRow, JobsPanel
from app.config import settings

logger = logging.getLogger(__name__)

__all__ = ["JobsPanelService", "get_jobs_panel_service", "reset_jobs_panel_service"]

# The jobs run through ``run_admin_jobs`` — the only ones with KV run markers +
# a single-flight lock key. ``(job_name, lock_key)`` pairs; job_name matches the
# stable marker name written by ``app.admin.job_markers``.
_JOBS: tuple[tuple[str, str], ...] = (
    ("rollup", ROLLUP_LOCK_KEY),
    ("purge", PURGE_LOCK_KEY),
    ("audit_retention", AUDIT_RETENTION_LOCK_KEY),
)

# Candidate ``AdminMetrics`` gauge names for a worker-independent queue/outbox
# length. None exist today (the outbox backlog lives in the Product bounded
# context), so queueLength is surfaced as unavailable until one is added.
_QUEUE_GAUGE_NAMES: tuple[str, ...] = ("queue_length", "outbox_length", "queue_backlog")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seconds_between(start_iso: str, now: datetime) -> float | None:
    """Return ``now - start`` in seconds (clamped ≥ 0), or ``None`` if unparseable."""
    try:
        start = datetime.fromisoformat(start_iso)
    except (TypeError, ValueError):
        return None
    delta = (now - start).total_seconds()
    return delta if delta >= 0 else 0.0


def _int_or_none(value: object) -> int | None:
    """Coerce a stored number to ``int`` (markers hold floats), else ``None``."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _lock_state(kvstore, lock_key: str) -> str | None:
    """Best-effort held/free check of a single-flight lock key (Req 8.3/8.7).

    The KVStore lock is represented differently per adapter, so we inspect the
    concrete adapter and read where that adapter records a held lock:

    - Redis: ``SET lock:{key} <token> NX PX`` → probe ``get("lock:{key}")``.
    - DB:    a ``kv`` row keyed ``\\x00lock\\x00{key}`` → probe ``get`` on it
             (``get`` already honors the row's TTL, so an expired lock reads free).
    - Local: the in-process ``_lock_until`` map (not on the public key path) →
             read it directly and compare against the monotonic clock.

    Returns ``"held"`` / ``"free"``, or ``None`` (unavailable) for an unknown
    adapter or any error — a lock probe must never crash the panel (Req 8.7).
    """
    try:
        from app.auth.kvstore.db import DBKVStore, _LOCK_PREFIX
        from app.auth.kvstore.local import LocalKVStore
        from app.auth.kvstore.redis_store import RedisKVStore
    except Exception:  # pragma: no cover - adapter import should not fail
        logger.debug("Lock-state adapter import failed", exc_info=True)
        return None

    try:
        if isinstance(kvstore, RedisKVStore):
            return "held" if await kvstore.get(f"lock:{lock_key}") is not None else "free"
        if isinstance(kvstore, DBKVStore):
            return "held" if await kvstore.get(f"{_LOCK_PREFIX}{lock_key}") is not None else "free"
        if isinstance(kvstore, LocalKVStore):
            entry = kvstore._lock_until.get(lock_key)
            if entry is None:
                return "free"
            expiry, _token = entry
            return "held" if time.monotonic() < expiry else "free"
    except Exception:  # pragma: no cover - best-effort; unavailable, not a crash
        logger.debug("Lock-state probe failed for %s", lock_key, exc_info=True)
        return None
    # Unknown adapter type — cannot determine, surface as unavailable.
    return None


class JobsPanelService:
    """Compose the :class:`JobsPanel` from KV run markers + live gauges (Req 8).

    Dependencies are optionally injected (tests); otherwise the process-wide
    Metric_Store + KVStore are resolved lazily so importing this module never
    forces DB/engine initialization.
    """

    def __init__(self, *, metric_store=None, kvstore=None) -> None:
        self._store = metric_store
        self._kv = kvstore

    def _metric_store(self):
        if self._store is not None:
            return self._store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    def _kvstore(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    # -- public API ----------------------------------------------------------

    async def panel(self) -> JobsPanel:
        """Assemble the full jobs panel (O(1) — one KV read per job + gauges)."""
        store = self._metric_store()
        kvstore = self._kvstore()
        now = _now()

        jobs: list[JobRow] = []
        stale = False
        for job_name, lock_key in _JOBS:
            try:
                marker = await store.snapshot_get(job_marker_name(job_name))
            except Exception:  # a marker read failure degrades gracefully (Req 8.7)
                logger.debug("Job marker read failed for %s", job_name, exc_info=True)
                marker = None
                stale = True
            lock_state = await _lock_state(kvstore, lock_key)
            jobs.append(self._build_row(job_name, marker, lock_state, now))

        purge_backlog, purge_unavailable = await self._purge_backlog(now)
        queue_length, queue_unavailable = self._queue_length()

        return JobsPanel(
            jobs=jobs,
            queueLength=queue_length,
            queueLengthUnavailable=queue_unavailable,
            purgeBacklog=purge_backlog,
            purgeBacklogUnavailable=purge_unavailable,
            computedAt=now.isoformat(),
            stale=stale,
        )

    # -- per-job row ---------------------------------------------------------

    def _build_row(
        self, name: str, marker: dict | None, lock_state: str | None, now: datetime
    ) -> JobRow:
        """Project one job's run marker (+ lock state) into a :class:`JobRow`."""
        if not marker:
            # No marker yet (job has never run in this deployment). Everything is
            # unknown except the lock state we just probed.
            return JobRow(name=name, lockState=lock_state)

        running_since = marker.get("running_since")
        current_float: float | None = None
        if running_since:
            current_float = _seconds_between(running_since, now)

        expected = marker.get("expected_duration_seconds")
        current_duration = int(current_float) if current_float is not None else None

        return JobRow(
            name=name,
            lastRun=marker.get("last_run"),
            lastOutcome=marker.get("last_outcome"),
            lagSeconds=_int_or_none(marker.get("lag_seconds")),
            nextRun=marker.get("next_run"),
            lastSuccess=marker.get("last_success"),
            runningSince=running_since,
            currentDurationSeconds=current_duration,
            expectedDurationSeconds=_int_or_none(expected),
            potentiallyStuck=self._is_stuck(current_float, expected),
            lockState=lock_state,
        )

    @staticmethod
    def _is_stuck(current_seconds: float | None, expected: object) -> bool:
        """Potentially-stuck detection from markers + config only (Req 8.10).

        A job is only ever "stuck" while it is running. When an expected duration
        exists, stuck ⇔ current > expected * multiplier; otherwise stuck ⇔ current
        exceeds the absolute ceiling. No new monitoring is introduced.
        """
        if current_seconds is None:
            return False
        try:
            expected_val = float(expected) if expected is not None else None
        except (TypeError, ValueError):
            expected_val = None
        if expected_val is not None and expected_val > 0:
            return current_seconds > expected_val * settings.admin_job_stuck_multiplier
        return current_seconds > settings.admin_job_stuck_ceiling_seconds

    # -- panel-level gauges --------------------------------------------------

    async def _purge_backlog(self, now: datetime) -> tuple[int | None, bool]:
        """Purge backlog from the in-process gauge, else the live bounded count.

        Prefer the truly O(1) ``AdminMetrics`` gauge (populated by the rollup/purge
        jobs). On a worker where it has not been set yet, fall back to the
        worker-independent :func:`live_admin_gauges` (an indexed count, not a
        scan). On failure of both, surface *unavailable* rather than a fake 0
        (Req 8.7).
        """
        try:
            gauges = get_admin_metrics().snapshot().get("gauges", {})
            if "purge_backlog" in gauges:
                return int(gauges["purge_backlog"]), False
        except Exception:  # pragma: no cover - gauge read is best-effort
            logger.debug("Purge-backlog gauge read failed", exc_info=True)

        try:
            from app.admin.metrics_service import live_admin_gauges

            cutoff = (now - timedelta(days=settings.admin_delete_grace_days)).isoformat()
            live = await live_admin_gauges(cutoff)
            return int(live["purge_backlog"]), False
        except Exception:
            logger.debug("Live purge-backlog read failed", exc_info=True)
            return None, True

    def _queue_length(self) -> tuple[int | None, bool]:
        """Queue/outbox length from an ``AdminMetrics`` gauge, else unavailable.

        No worker-independent admin gauge for the outbox/queue length exists — the
        outbox backlog is owned by the Product bounded context and reading it here
        would require a cross-context live query (a Non-Goal + not O(1)). Until an
        admin gauge is published, this is surfaced as unavailable (Req 8.7).
        """
        try:
            gauges = get_admin_metrics().snapshot().get("gauges", {})
            for key in _QUEUE_GAUGE_NAMES:
                if key in gauges:
                    return int(gauges[key]), False
        except Exception:  # pragma: no cover - gauge read is best-effort
            logger.debug("Queue-length gauge read failed", exc_info=True)
        return None, True


# ---------------------------------------------------------------------------
# Process-wide instance (mirrors the other admin service accessors)
# ---------------------------------------------------------------------------

_service: JobsPanelService | None = None


def get_jobs_panel_service() -> JobsPanelService:
    """Return the process-wide :class:`JobsPanelService`."""
    global _service
    if _service is None:
        _service = JobsPanelService()
    return _service


def reset_jobs_panel_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
