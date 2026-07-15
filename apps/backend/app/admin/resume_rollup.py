"""Resume-analytics rollup writer — the cross-user snapshot step (Req 14.1/14.2).

**Why this lives in ``app.admin`` and not ``app.analytics``.** Computing the
resume source split + popular templates requires *cross-user* aggregate reads,
and the only module permitted to issue unscoped owned-table queries is the
heavily-reviewed :class:`~app.admin.repo.AdminRepo` (enforced by
``app/scripts/check_scoping.py``; a foreign module may not even import a
module-private ``repo`` — ``tests/architecture/test_module_ownership.py``).

So the **writer** (this rollup step, which reads cross-user data through
``AdminRepo`` and persists a ``metrics_daily`` KV snapshot) is co-located with
``AdminRepo`` in the admin/observability rollup infrastructure, while the
Product-Analytics **reader** (``ResumeMetricsService`` in
``app.analytics.resume_metrics``) consumes the resulting snapshot **only through
the shared Metric_Store** — exactly the cross-context seam Req 19.4 prescribes
("a Product-Analytics endpoint reads Observability keys only through the shared
Metric_Store read helpers"). The two bounded contexts therefore share nothing
but the Metric_Store snapshot; there is no analytics→admin import.

``StepResult`` is imported **lazily** inside ``run`` (the cycle-safe pattern used
by every rollup step): ``rollup_pipeline`` imports this module at load time to
assemble ``PIPELINE``, so this module must not import ``rollup_pipeline`` at the
top level.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

__all__ = [
    "ResumeSnapshotStep",
    "RESUME_SNAPSHOT_STEP",
    "RESUME_SNAPSHOT_NAME",
]

# The named Metric_Store KV snapshot that holds the resume source split +
# popular templates. This writer populates it; ``ResumeMetricsService`` reads it
# back through the same Metric_Store. Keep this literal in sync with the reader's
# copy in ``app.analytics.resume_metrics`` (a stable persisted KV name).
RESUME_SNAPSHOT_NAME = "resume_snapshot"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResumeSnapshotStep:
    """Snapshot the resume source split + popular templates (Req 14.1/14.2).

    Computes the current totals via :meth:`AdminRepo.resume_source_counts`
    (generated / imported / tailored / deleted) and the top-10 templates via
    :meth:`AdminRepo.popular_templates`, then persists both into a single named
    KV snapshot (:data:`RESUME_SNAPSHOT_NAME`) via :meth:`MetricStore.snapshot_put`.

    The Product-Analytics ``ResumeMetricsService`` reads this snapshot on the
    request path via ``snapshot_get(RESUME_SNAPSHOT_NAME)`` — never the DB.

    Failure-isolated: any error is returned as a failed ``StepResult`` (never
    raised). Idempotent: a re-run simply overwrites the same snapshot key.
    """

    name = "resume_snapshot"

    def __init__(self, *, metric_store=None, repo=None) -> None:
        # Optional injected collaborators (tests); otherwise the process-wide
        # singletons are resolved lazily at run time.
        self._store = metric_store
        self._repo = repo

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

    async def run(self, day: str) -> "StepResult":  # noqa: ARG002 - snapshot is "as of now"
        # Lazy import breaks the load-time cycle (see module docstring).
        from app.admin.rollup_pipeline import StepResult

        try:
            repo = self._admin_repo()
            store = self._metric_store()

            # 1. Resume source split (Req 14.1)
            source_counts = await repo.resume_source_counts()

            # 2. Popular templates — top-10 ranked by usage count (Req 14.2)
            raw_templates = await repo.popular_templates()
            # Sort descending by count, then ascending by name for tie-break,
            # and take the top 10.
            sorted_templates = sorted(
                raw_templates, key=lambda t: (-t[1], t[0])
            )[:10]
            popular_templates = [
                {"template": name, "count": count}
                for name, count in sorted_templates
            ]

            # 3. Persist as a named snapshot
            payload = {
                "sourceCounts": {
                    "generated": int(source_counts.get("generated", 0)),
                    "imported": int(source_counts.get("imported", 0)),
                    "tailored": int(source_counts.get("tailored", 0)),
                    "deleted": int(source_counts.get("deleted", 0)),
                },
                "popularTemplates": popular_templates,
                "sampledAt": _now().isoformat(),
            }
            await store.snapshot_put(RESUME_SNAPSHOT_NAME, payload)
        except Exception as exc:  # observable per-step failure (R2.5)
            return StepResult.failure(self.name, exc)
        return StepResult.success(self.name)


# Process-wide instance slotted into PIPELINE by ``rollup_pipeline``. Single-
# flighted by the Rollup_Job's KVStore lock — one run at a time.
RESUME_SNAPSHOT_STEP = ResumeSnapshotStep()
