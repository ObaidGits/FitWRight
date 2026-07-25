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
            RESUMES_DELETED,
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

        # Read each durable event series once. The deletion series serves two
        # purposes below: it supplies the selected-window deleted event total
        # (hard-deleted rows cannot appear in the snapshot), and it makes the
        # fixed ``growth`` field a true net-change series.
        event_keys = (
            RESUMES_GENERATED,
            RESUMES_IMPORTED,
            RESUMES_TAILORED,
            RESUMES_DELETED,
        )
        event_series = {key: await store.series(key, window) for key in event_keys}

        # The snapshot is a current-inventory view. Its source counts are a
        # mutually exclusive partition of live resume rows, so deletion events
        # are deliberately excluded from both the split and its denominator.
        computed_at = _now().isoformat(timespec="seconds")
        snapshot = await store.snapshot_get(_RESUME_SNAPSHOT) or {}
        if not isinstance(snapshot, dict):
            snapshot = {}
        source_counts = snapshot.get("sourceCounts", {})
        if not isinstance(source_counts, dict):
            source_counts = {}

        generated = int(source_counts.get("generated", 0))
        imported = int(source_counts.get("imported", 0))
        tailored = int(source_counts.get("tailored", 0))
        inventory_total = generated + imported + tailored

        def pct(n: int) -> float:
            return round(n / inventory_total * 100, 1) if inventory_total > 0 else 0.0

        source_split = ResumeSourceSplit(
            generated=generated,
            imported=imported,
            tailored=tailored,
            generatedPct=pct(generated),
            importedPct=pct(imported),
            tailoredPct=pct(tailored),
        )

        # Both inventory sections describe the same current snapshot. Preserve a
        # valid, timezone-aware sampling timestamp; malformed/legacy snapshots
        # fall back to this response's computation time rather than emitting an
        # unusable date.
        sampled_at = snapshot.get("sampledAt")
        try:
            if not isinstance(sampled_at, str) or not sampled_at.strip():
                raise ValueError("missing sampledAt")
            parsed_sampled_at = datetime.fromisoformat(
                sampled_at.replace("Z", "+00:00")
            )
            if parsed_sampled_at.tzinfo is None or parsed_sampled_at.utcoffset() is None:
                raise ValueError("sampledAt must include a UTC offset")
            snapshot_as_of = sampled_at
        except (TypeError, ValueError):
            snapshot_as_of = computed_at

        # Sort defensively at the response boundary: old/manually populated
        # snapshots are not guaranteed to have been ordered by the writer.
        # These counts are current resume inventory by template, not selected-
        # window usage. Apply the top-10 cut after the deterministic tie-break.
        popular_raw = snapshot.get("popularTemplates", [])
        popular_sorted = sorted(
            popular_raw,
            key=lambda item: (-int(item["count"]), str(item["template"])),
        )
        top_templates = [
            TemplateCount(name=str(item["template"]), count=int(item["count"]))
            for item in popular_sorted[:10]
        ]

        # Net resume inventory change per UTC day. Successful creation events add
        # inventory and successful hard deletions remove it, so daily values and
        # the selected-window total may legitimately be negative.
        growth_by_day: dict[str, int] = {}
        for key in event_keys:
            direction = -1 if key == RESUMES_DELETED else 1
            for day, value in event_series[key]:
                growth_by_day[day] = growth_by_day.get(day, 0) + direction * int(value)

        growth = [
            SeriesPoint(date=day, value=value)
            for day, value in sorted(growth_by_day.items())
        ]
        deleted_in_window = sum(
            int(value) for _, value in event_series[RESUMES_DELETED]
        )
        net_change = sum(point.value for point in growth)

        return ResumeAnalytics(
            window=window,
            sourceSplit=source_split,
            topTemplates=top_templates,
            deletedInWindow=deleted_in_window,
            netChange=net_change,
            inventoryAsOf=snapshot_as_of,
            templatesAsOf=snapshot_as_of,
            growth=growth,
            computedAt=computed_at,
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
