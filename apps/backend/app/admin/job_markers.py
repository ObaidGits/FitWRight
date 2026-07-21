"""Per-job run markers persisted to KV (Req 3.4, 8.8, 8.9).

The System Health page (Req 3.4) and the Background Jobs panel (Req 8) need to
show, for each background job, *when it last ran*, *whether it succeeded*, *how
long it took*, *whether it is running right now*, and *how long a run is normally
expected to take* - all without any per-event storage. This module is the single
writer of those **run markers**: a small, secret-free JSON snapshot per job,
stored via the shared :class:`~app.admin.metric_store.MetricStore` named-snapshot
KV path (so it lives in the same KVStore and namespace as every other admin
snapshot and is pruned/TTL-free just like them).

Storage key namespace (stable - the Jobs panel reader, task 7.1, MUST read the
same keys):

    MetricStore snapshot name  ->  ``job_marker:{job_name}``
    Underlying KVStore key      ->  ``admin:snapshot:job_marker:{job_name}``

``job_name`` is one of the stable identifiers used by the tiles/panel:
``"rollup"``, ``"purge"``, ``"audit_retention"`` - the three jobs
:func:`app.admin.jobs.run_admin_jobs` actually runs. (Req 3.4 also lists
``reaper``/``outbox``; those are driven from a *separate* path - the session
reaper loop / outbox worker - and record their own markers there, not here. This
module only owns markers for the jobs run through ``run_admin_jobs``.)

Marker schema (all timestamps are UTC ISO-8601 strings; every field is a name,
timestamp, status, or duration - never any secret or row content):

    {
      "job":                       str,          # stable job name
      "last_run":                  str,          # UTC ISO - when the last run STARTED
      "last_outcome":              str|null,     # "success" | "failure" | "skipped"
      "lag_seconds":               float|null,   # scheduled->actual start lag; see below
      "next_run":                  str|null,     # next scheduled run (UTC ISO) or null
      "last_success":              str|null,     # UTC ISO of the last SUCCESS run's start; preserved across non-success runs
      "running_since":             str|null,     # UTC ISO set at start, cleared (null) on completion
      "last_duration_seconds":     float|null,   # measured duration of the last completed run
      "expected_duration_seconds": float|null,   # EWMA of completed-run durations (typical duration)
      "updated_at":                str,          # UTC ISO of the last marker write
    }

Derivations (kept honest - we only record what we can actually observe):

- **last_run** - the wall-clock instant the run started (captured by
  :func:`mark_job_started`).
- **last_outcome** - derived from the job's return dict by
  :func:`outcome_from_result`: ``status == "ok"`` -> ``"success"``;
  ``"locked"``/``"disabled"`` -> ``"skipped"`` (the job intentionally did no work);
  a raised exception or any other/error status -> ``"failure"``.
- **lag_seconds** - the scheduled-to-actual start lag. These jobs are triggered by
  ``POST /internal/run-jobs`` (external cron under ``SCHEDULER_MODE`` - ADR-15) or
  the in-process scheduler loop, **neither of which passes a precise "scheduled"
  reference into the job**. With no schedule reference we cannot compute a true
  lag, so we record ``lag_seconds`` as ``null`` (documented gap) rather than
  fabricate a value. If a caller ever learns a scheduled time it can pass
  ``scheduled_iso`` to :func:`mark_job_started` and the lag will be computed.
- **next_run** - the next scheduled run. Under external-cron ``SCHEDULER_MODE``
  the schedule lives outside the app (cron/GitHub Actions) and is unknown to the
  process, so ``next_run`` is ``null`` unless a caller supplies ``next_run_iso``.
- **last_success** - the start timestamp of the most recent run whose outcome was
  ``"success"``; **preserved** across later non-success runs (only advanced on a
  fresh success), so operators can always see when the job last actually worked
  (Req 8.8).
- **running_since** - set to the run's start when :func:`mark_job_started` fires
  and cleared to ``null`` by :func:`record_job_run` on completion. Because
  ``run_admin_jobs`` runs jobs sequentially and synchronously, a marker observed
  with a non-null ``running_since`` (and no matching completion) signals a job
  that is *currently in progress* - the "currently running" case the panel reads
  (Req 8.8).
- **expected_duration_seconds** - a typical run duration derived from prior
  markers via an exponentially-weighted moving average (EWMA) of completed-run
  durations. ``null`` until the first completed run establishes a baseline
  (Req 8.9).

Every write is **best-effort**: a KV failure is logged and swallowed so marker
persistence can never break or abort the underlying job (mirrors the existing
best-effort gauge/flush-marker writes).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

__all__ = [
    "JOB_MARKER_PREFIX",
    "job_marker_name",
    "outcome_from_result",
    "mark_job_started",
    "record_job_run",
]

# MetricStore snapshot-name prefix for per-job run markers. The full underlying
# KVStore key is ``admin:snapshot:job_marker:{job_name}`` (MetricStore adds the
# ``admin:snapshot:`` namespace). Task 7.1's JobsPanel MUST read these same keys.
JOB_MARKER_PREFIX = "job_marker"

# EWMA smoothing factor for the expected (typical) run duration. Weights the most
# recent completed run at ``alpha`` and the prior estimate at ``1 - alpha`` so a
# single outlier run cannot dominate the "typical" figure while it still adapts.
_EWMA_ALPHA = 0.3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def job_marker_name(job_name: str) -> str:
    """Return the MetricStore snapshot name for ``job_name``'s run marker."""
    return f"{JOB_MARKER_PREFIX}:{job_name}"


def outcome_from_result(result: object) -> str:
    """Derive the run outcome from a job's return value (Req 3.4).

    ``status == "ok"`` -> ``"success"``; ``"locked"``/``"disabled"`` -> ``"skipped"``
    (the job intentionally did no work - held single-flight lock or kill-switch
    off); anything else (including a non-dict result or an error status) ->
    ``"failure"``. Exceptions are handled by the caller, which records
    ``"failure"`` directly.
    """
    if isinstance(result, dict):
        status = result.get("status")
        if status == "ok":
            return "success"
        if status in ("locked", "disabled"):
            return "skipped"
    return "failure"


def _parse_seconds(start_iso: str, end_iso: str) -> float | None:
    """Return ``end - start`` in seconds, or ``None`` if either is unparseable."""
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except (TypeError, ValueError):
        return None
    delta = (end - start).total_seconds()
    # Clamp tiny negative clock jitter to 0; a real negative span is meaningless.
    return delta if delta >= 0 else 0.0


async def mark_job_started(
    store,
    job_name: str,
    *,
    start_iso: str | None = None,
    scheduled_iso: str | None = None,
    next_run_iso: str | None = None,
) -> None:
    """Record that ``job_name`` has STARTED (sets ``running_since``) - Req 8.8.

    Reads the prior marker (to preserve ``last_success``/``expected_duration`` and
    any known ``next_run``), then writes a marker with ``last_run`` and
    ``running_since`` set to the run's start. A non-null ``running_since`` with no
    later completion is what signals a currently-running job to the panel.

    ``scheduled_iso`` (if the caller knows the scheduled start) lets us compute a
    real ``lag_seconds``; absent it, lag is left ``null`` (documented gap - the
    external-cron/scheduler triggers pass no schedule reference). ``next_run_iso``
    similarly overrides the persisted ``next_run`` when a schedule is known.

    Best-effort: any failure is logged and swallowed so it can never abort the job.
    """
    start_iso = start_iso or _now_iso()
    try:
        prior = await store.snapshot_get(job_marker_name(job_name)) or {}

        lag_seconds = prior.get("lag_seconds")
        if scheduled_iso is not None:
            lag_seconds = _parse_seconds(scheduled_iso, start_iso)

        next_run = next_run_iso if next_run_iso is not None else prior.get("next_run")

        marker = {
            "job": job_name,
            "last_run": start_iso,
            # Outcome is not yet known for this run; keep the prior outcome so the
            # panel shows the last *completed* outcome while this run is in flight.
            "last_outcome": prior.get("last_outcome"),
            "lag_seconds": lag_seconds,
            "next_run": next_run,
            "last_success": prior.get("last_success"),
            "running_since": start_iso,
            "last_duration_seconds": prior.get("last_duration_seconds"),
            "expected_duration_seconds": prior.get("expected_duration_seconds"),
            "updated_at": _now_iso(),
        }
        await store.snapshot_put(job_marker_name(job_name), marker)
    except Exception:  # pragma: no cover - marker writes are best-effort
        logger.debug("Failed to write job-start marker for %s", job_name, exc_info=True)


async def record_job_run(
    store,
    job_name: str,
    *,
    start_iso: str,
    end_iso: str | None = None,
    outcome: str,
    next_run_iso: str | None = None,
) -> None:
    """Record that ``job_name`` COMPLETED, updating its run marker - Req 3.4/8.8/8.9.

    Reads the prior marker, computes ``duration = end - start``, then writes the
    merged marker: ``last_run`` = this run's start, ``last_outcome`` = ``outcome``,
    ``last_success`` advanced to this run's start only when ``outcome`` is
    ``"success"`` (otherwise preserved), ``running_since`` cleared to ``null``
    (the run is done), ``last_duration_seconds`` = this run's duration, and
    ``expected_duration_seconds`` updated as an EWMA of completed-run durations.

    ``lag_seconds`` and ``next_run`` are carried from the prior marker (both
    ``null`` under external-cron unless a caller supplied them at start), with
    ``next_run`` overridable via ``next_run_iso``.

    Best-effort: any failure is logged and swallowed so it can never abort the job.
    """
    end_iso = end_iso or _now_iso()
    try:
        prior = await store.snapshot_get(job_marker_name(job_name)) or {}

        duration = _parse_seconds(start_iso, end_iso)

        # expected (typical) duration: EWMA over completed-run durations (Req 8.9).
        prior_expected = prior.get("expected_duration_seconds")
        if duration is None:
            expected = prior_expected
        elif prior_expected is None:
            expected = duration
        else:
            expected = _EWMA_ALPHA * duration + (1 - _EWMA_ALPHA) * prior_expected

        # last_success: only advance on a success; preserve otherwise (Req 8.8).
        last_success = prior.get("last_success")
        if outcome == "success":
            last_success = start_iso

        next_run = next_run_iso if next_run_iso is not None else prior.get("next_run")

        marker = {
            "job": job_name,
            "last_run": start_iso,
            "last_outcome": outcome,
            "lag_seconds": prior.get("lag_seconds"),
            "next_run": next_run,
            "last_success": last_success,
            # Cleared on completion - a null running_since means "not running".
            "running_since": None,
            "last_duration_seconds": duration,
            "expected_duration_seconds": expected,
            "updated_at": _now_iso(),
        }
        await store.snapshot_put(job_marker_name(job_name), marker)
    except Exception:  # pragma: no cover - marker writes are best-effort
        logger.debug("Failed to write job-completion marker for %s", job_name, exc_info=True)
