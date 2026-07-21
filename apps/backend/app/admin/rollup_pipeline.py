"""Rollup pipeline: an ordered list of independent Rollup_Steps (Task 2.1).

The Rollup_Job does **not** accumulate inline rollup logic. It builds an ordered
:data:`PIPELINE` of :class:`RollupStep`s and executes them; this module only
*coordinates* - it owns no business logic. Each step lives next to its owning
service, and every step is:

- **independent** - one step never depends on another's in-run side effects;
- **idempotent** per closed UTC day - re-running a day is a no-op where the work
  is already done, so a re-run never double-counts;
- **resumable** - a crashed/partial run is recovered on the next run by re-scan;
- **failure-isolated** - one step failing never aborts the others (R2.5).

Each step reports a :class:`StepResult` (name + ok/failed + optional error) and
the orchestrator additionally wraps any exception that escapes a step, so
per-step failure is always observable to the caller without inspecting a step's
internals (R2.5 = per-step failure isolation with an observable indication). The
pipeline file stays tiny by design; steps are appended by their owning services
in later tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "RollupStep",
    "StepResult",
    "ExistingRollupStep",
    "EXISTING_ROLLUP_STEP",
    "MetricsFlushStep",
    "METRICS_FLUSH_STEP",
    "AiFlushStep",
    "AI_FLUSH_STEP",
    "DbSizeSampleStep",
    "DB_SIZE_SAMPLE_STEP",
    "StorageSnapshotStep",
    "STORAGE_SNAPSHOT_STEP",
    "SecurityAggregateStep",
    "SECURITY_AGGREGATE_STEP",
    "ResumeSnapshotStep",
    "RESUME_SNAPSHOT_STEP",
    "MetricsPruneStep",
    "METRICS_PRUNE_STEP",
    "PIPELINE",
    "run_rollup_pipeline",
]


@dataclass(frozen=True, slots=True)
class StepResult:
    """Outcome of a single :class:`RollupStep` run.

    Captures the step ``name`` plus an ``ok``/failed flag and an optional
    ``error`` string, so the orchestrator can log and surface per-step failure
    (R2.5) without knowing anything about the step's internals.
    """

    name: str
    ok: bool
    error: str | None = None

    @classmethod
    def success(cls, name: str) -> "StepResult":
        """A step that completed cleanly."""
        return cls(name=name, ok=True)

    @classmethod
    def failure(cls, name: str, error: BaseException | str) -> "StepResult":
        """A step that failed, carrying an observable error indication."""
        return cls(name=name, ok=False, error=str(error))


@runtime_checkable
class RollupStep(Protocol):
    """One independent, idempotent, resumable, failure-isolated rollup unit.

    ``run(day)`` performs the step's work for a single closed UTC day
    (``YYYY-MM-DD``). It must be self-contained and safe to re-run: re-running a
    day that is already done is a no-op (idempotent), and a partial prior run is
    recovered by re-scanning (resumable). A step may either return a failed
    :class:`StepResult` (e.g. a per-key partial failure it handled internally) or
    raise; either way the orchestrator isolates the failure from other steps.
    """

    name: str

    async def run(self, day: str) -> StepResult:  # pragma: no cover - protocol
        ...


class ExistingRollupStep:
    """First pipeline step - the pre-existing generic rollup + reconciliation.

    A transitional wrapper that routes the pre-pipeline rollup work through the
    pipeline **unchanged**. It delegates to the existing
    :class:`~app.admin.metrics_service.MetricsService` entrypoints - ``run_rollup``
    (the generic ``metrics_daily`` registry UPSERTs for the closed day(s) + the
    ``_TOTALS_DAY`` totals-snapshot refresh that also backs usage-series) and
    ``reconcile_counters`` (denormalized usage-counter drift correction) - so the
    rows it produces are byte-for-byte the same as the old inline implementation.
    The step adds nothing but the pipeline's ordering and per-step failure
    isolation around that existing work.

    The existing rollup recovers ``lookback_days`` closed days internally (for
    missed-run recovery), so the pipeline's per-day ``day`` argument is not used to
    bound the work here - it is preserved for the day-scoped steps added later.
    The last run's result dicts are captured on :attr:`rollup` / :attr:`reconcile`
    so the Rollup_Job can preserve its historical return shape.
    """

    name = "existing_rollup"

    def __init__(self, *, lookback_days: int = 3) -> None:
        self.lookback_days = lookback_days
        self.rollup: dict | None = None
        self.reconcile: dict | None = None

    async def run(self, day: str) -> StepResult:
        # Reset captured results so a failed run never surfaces a prior run's dicts.
        self.rollup = None
        self.reconcile = None
        # Lazy import avoids an import cycle at module load (this module is imported
        # by the job runner, which also imports MetricsService).
        from app.admin.metrics_service import get_metrics_service

        svc = get_metrics_service()
        self.rollup = await svc.run_rollup(lookback_days=self.lookback_days)
        self.reconcile = await svc.reconcile_counters()
        return StepResult.success(self.name)


# Process-wide instance of the transitional existing-rollup step. The Rollup_Job
# references it directly to (a) set the per-run ``lookback_days`` and (b) read the
# captured ``rollup``/``reconcile`` result dicts after the pipeline runs, so its
# public return shape is unchanged. Single-flighted by the job's KVStore lock, so
# this shared instance is only ever driven by one run at a time.
EXISTING_ROLLUP_STEP = ExistingRollupStep()


class MetricsPruneStep:
    """Last pipeline step - bound the only new durable grower (Req 15.6).

    Deletes ``metrics_daily`` rows older than ``admin_metrics_retention_days``
    (a closed-day retention window), **excluding** the reserved ``_TOTALS_DAY``
    totals-snapshot sentinel row(s), which must never be pruned. The cutoff is
    ``today (UTC) - retention_days`` as a ``YYYY-MM-DD`` day string; rows whose
    ``day_utc`` sorts before it are removed via the generic
    :meth:`~app.admin.metric_store.MetricStore.prune_before` primitive (the store
    stays logic-free - this step owns the retention policy).

    Runs **last** so retention never races the flush/aggregate/snapshot steps
    that (re)write closed days earlier in the run. Idempotent: once the aged rows
    are gone a re-run deletes nothing new (the delete is bounded by a fixed
    cutoff, not by prior runs).
    """

    name = "metrics_prune"

    async def run(self, day: str) -> StepResult:
        # Lazy imports avoid an import cycle at module load and keep the pipeline
        # file dependency-light (it is imported by the job runner).
        from app.admin.metric_store import get_metric_store
        from app.admin.metrics_service import _TOTALS_DAY
        from app.config import settings

        try:
            retention_days = int(settings.admin_metrics_retention_days)
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=retention_days)
            ).strftime("%Y-%m-%d")
            await get_metric_store().prune_before(cutoff, exclude_days=(_TOTALS_DAY,))
        except Exception as exc:  # observable per-step failure (R2.5)
            return StepResult.failure(self.name, exc)
        return StepResult.success(self.name)


# Process-wide instance of the metrics-retention prune step (see EXISTING_ROLLUP_STEP).
METRICS_PRUNE_STEP = MetricsPruneStep()

# The MetricsFlushStep lives next to its owning MetricsService (its business logic
# is metrics-specific), so we import its process-wide singleton here to slot it
# into the ordered PIPELINE. This top-level import is safe: ``metrics_service``
# imports ``StepResult`` from this module *lazily* (inside the step's ``run``), so
# there is no import cycle at load time.
from app.admin.metrics_service import MetricsFlushStep, METRICS_FLUSH_STEP  # noqa: E402

# The AiFlushStep lives next to its owning AiMetricsService (its logic is AI
# specific), so we import its process-wide singleton here to slot it into the
# ordered PIPELINE. Safe top-level import: ``ai_metrics`` imports ``StepResult``
# from this module *lazily* (inside the step's ``run``), so there is no cycle at
# load time - the same pattern used for MetricsFlushStep.
from app.admin.ai_metrics import AiFlushStep, AI_FLUSH_STEP  # noqa: E402

# The DbSizeSampleStep + StorageSnapshotStep live next to their owning
# StorageMetricsService (their logic is storage-specific), so we import their
# process-wide singletons here to slot them into the ordered PIPELINE. Safe
# top-level import: ``storage_metrics`` imports ``StepResult`` from this module
# *lazily* (inside each step's ``run``), so there is no cycle at load time - the
# same pattern used for MetricsFlushStep / AiFlushStep.
from app.admin.storage_metrics import (  # noqa: E402
    DbSizeSampleStep,
    DB_SIZE_SAMPLE_STEP,
    StorageSnapshotStep,
    STORAGE_SNAPSHOT_STEP,
)

# The SecurityAggregateStep lives next to its owning SecurityMetricsService (its
# logic is security-specific), so we import its process-wide singleton here to
# slot it into the ordered PIPELINE. Safe top-level import: ``security_metrics``
# imports ``StepResult`` from this module *lazily* (inside the step's ``run``), so
# there is no cycle at load time - the same pattern used for the flush/snapshot
# steps above.
from app.admin.security_metrics import (  # noqa: E402
    SecurityAggregateStep,
    SECURITY_AGGREGATE_STEP,
)

# The ResumeSnapshotStep is the resume-analytics rollup *writer*. It performs
# cross-user aggregate reads (via AdminRepo) and therefore lives in the admin/
# observability rollup infrastructure (``app.admin.resume_rollup``) alongside the
# only sanctioned cross-user reader - NOT in the Product-Analytics context, which
# consumes the resulting snapshot purely through Metric_Store (Req 19.4). Safe
# top-level import: ``resume_rollup`` imports ``StepResult`` from this module
# *lazily* (inside the step's ``run``), so there is no cycle at load time.
from app.admin.resume_rollup import (  # noqa: E402
    ResumeSnapshotStep,
    RESUME_SNAPSHOT_STEP,
)

# Ordered list of step instances executed by the Rollup_Job. The existing rollup
# runs first and the metrics prune runs last (retention must not race the
# flush/aggregate/snapshot steps that rewrite closed days). The metrics flush runs
# right after the existing rollup, followed by the db-size sample, the AI flush,
# and the storage snapshot; further independent steps (security aggregate, resume
# snapshot, ...) are appended by their owning services in later tasks *before* the
# prune step (their relative order among the flush/aggregate/snapshot steps is not
# behavior-critical - only "existing rollup first, prune last"). Keeping this list
# - and this file - tiny is intentional; the pipeline only coordinates.
PIPELINE: list[RollupStep] = [
    EXISTING_ROLLUP_STEP,
    METRICS_FLUSH_STEP,
    DB_SIZE_SAMPLE_STEP,
    AI_FLUSH_STEP,
    STORAGE_SNAPSHOT_STEP,
    SECURITY_AGGREGATE_STEP,
    RESUME_SNAPSHOT_STEP,
    METRICS_PRUNE_STEP,
]


async def run_rollup_pipeline(day: str) -> list[StepResult]:
    """Execute every step in :data:`PIPELINE` for ``day``, isolating failures.

    Steps run in declared order. A step that raises is caught, logged, and
    recorded as a failed :class:`StepResult`; a step that returns a failed result
    is logged as well. Either way the pipeline continues with the remaining
    steps, so one step's failure never aborts the others (R2.5). Returns one
    :class:`StepResult` per step so the caller (the Rollup_Job) can log and
    surface per-step outcomes.
    """
    results: list[StepResult] = []
    for step in PIPELINE:
        try:
            result = await step.run(day)
        except Exception as exc:  # failure isolated per step (R2.5)
            logger.exception("Rollup step %r failed for day %s", step.name, day)
            result = StepResult.failure(step.name, exc)
        else:
            if not result.ok:
                logger.error(
                    "Rollup step %r reported failure for day %s: %s",
                    result.name,
                    day,
                    result.error,
                )
        results.append(result)
    return results
