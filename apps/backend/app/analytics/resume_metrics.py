"""Resume analytics read model (Req 14) - Product Analytics bounded context.

Holds the :class:`ResumeMetricsService` that serves ``GET /admin/analytics/resumes``
(source split + popular templates + growth).

**Bounded-context purity (Req 19.2/19.3/19.4/19.5).** This Product-Analytics
service depends ONLY on the shared primitives - the Metric_Store and the
Metric_Registry. It reads the resume source-split / popular-templates snapshot
that the admin/observability rollup writer
(:class:`app.admin.resume_rollup.ResumeSnapshotStep`) produced, purely through
``Metric_Store.snapshot_get`` - the sanctioned cross-context read seam (Req
19.4). It performs **no cross-user DB read** itself (those live only in the
heavily-reviewed ``AdminRepo``, driven by the rollup writer) and imports no other
Domain_Metrics_Service, so the import-graph fitness test (Task 5.3) holds.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

__all__ = [
    "ResumeMetricsService",
    "get_resume_metrics_service",
    "reset_resume_metrics_service",
]

# The named Metric_Store KV snapshot holding the resume source split + popular
# templates, populated by ``app.admin.resume_rollup.ResumeSnapshotStep``. Keep
# this literal in sync with the writer's ``RESUME_SNAPSHOT_NAME`` (a stable
# persisted KV name); the two contexts share only this Metric_Store snapshot.
_RESUME_SNAPSHOT = "resume_snapshot"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ResumeMetricsService:
    """Resume analytics from pre-computed snapshot + durable keys (Req 14).

    Reads the ``"resume_snapshot"`` KV blob (source counts + popular templates)
    persisted by the rollup writer and combines it with the zero-filled daily
    growth series from the ``RESUMES_*`` durable keys. All reads are O(1) - no
    live DB queries at request time (Req 14.5).
    """

    def __init__(self, *, metric_store=None) -> None:
        self._metric_store = metric_store

    def _get_metric_store(self):
        if self._metric_store is not None:
            return self._metric_store
        from app.admin.metric_store import get_metric_store

        return get_metric_store()

    async def analytics(self, window: int):
        """Return :class:`ResumeAnalytics` for the given window (7/30/90)."""
        from app.admin.metric_registry import (
            RESUMES_GENERATED,
            RESUMES_IMPORTED,
            RESUMES_TAILORED,
        )
        from app.admin.schemas import (
            ResumeAnalytics,
            ResumeSourceSplit,
            SeriesPoint,
            TemplateCount,
        )

        if window not in (7, 30, 90):
            raise ValueError(f"window must be one of [7, 30, 90], got {window}")

        store = self._get_metric_store()

        # Source split from snapshot (pre-computed by the rollup writer)
        snapshot = await store.snapshot_get(_RESUME_SNAPSHOT) or {}
        source_counts = snapshot.get("sourceCounts", {})

        generated = int(source_counts.get("generated", 0))
        imported = int(source_counts.get("imported", 0))
        tailored = int(source_counts.get("tailored", 0))
        deleted = int(source_counts.get("deleted", 0))
        total = generated + imported + tailored + deleted

        def pct(n: int) -> float:
            return round(n / total * 100, 1) if total > 0 else 0.0

        source_split = ResumeSourceSplit(
            generated=generated,
            imported=imported,
            tailored=tailored,
            deleted=deleted,
            generatedPct=pct(generated),
            importedPct=pct(imported),
            tailoredPct=pct(tailored),
            deletedPct=pct(deleted),
        )

        # Popular templates from snapshot (top 10, already sorted by the writer)
        popular_raw = snapshot.get("popularTemplates", [])
        top_templates = [
            TemplateCount(name=t["template"], count=int(t["count"]))
            for t in popular_raw[:10]
        ]

        # Resume-growth series (Req 14.3): resumes *created* per calendar day
        # = generated + imported + tailored. Deletions are not growth, so
        # RESUMES_DELETED is intentionally excluded here.
        keys = [RESUMES_GENERATED, RESUMES_IMPORTED, RESUMES_TAILORED]
        all_series: dict[str, int] = {}
        for key in keys:
            raw = await store.series(key, window)
            for day, val in raw:
                all_series[day] = all_series.get(day, 0) + val

        growth = [SeriesPoint(date=d, value=v) for d, v in sorted(all_series.items())]

        return ResumeAnalytics(
            window=window,
            sourceSplit=source_split,
            topTemplates=top_templates,
            growth=growth,
            computedAt=_now().isoformat(timespec="seconds"),
        )


_resume_service: ResumeMetricsService | None = None


def get_resume_metrics_service() -> ResumeMetricsService:
    """Return the process-wide ResumeMetricsService singleton."""
    global _resume_service  # noqa: PLW0603
    if _resume_service is None:
        _resume_service = ResumeMetricsService()
    return _resume_service


def reset_resume_metrics_service() -> None:
    """Reset the singleton (test teardown)."""
    global _resume_service  # noqa: PLW0603
    _resume_service = None
