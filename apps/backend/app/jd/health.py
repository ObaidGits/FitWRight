"""Automated adapter health monitoring + self-healing (§32, §34, Phase 4).

Aggregates the per-adapter circuit-breaker state (from :class:`DriftMonitor`)
into a single health snapshot for observability + alerting. The self-healing
itself lives in the DriftMonitor (half-open probe → close on success, re-open on
failure); this module surfaces the state and computes a fleet-level rollup.

Exposed via the internal metrics endpoint so on-call can see, at a glance, which
platform adapters are degraded and whether they are recovering.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["adapter_health_snapshot", "overall_health"]


async def adapter_health_snapshot() -> dict:
    """Return per-adapter circuit state + a fleet rollup.

    Shape::

        {
          "adapters": {"ashby": {"state": "closed", "success_rate": 1.0, ...}, ...},
          "degraded": ["taleo"],          # circuits open or half-open
          "healthy_count": 12,
          "degraded_count": 1,
          "overall": "healthy" | "degraded",
        }
    """
    from app.jd.adapters.registry import _ADAPTERS

    # Reuse the orchestrator's DriftMonitor singleton so state is consistent with
    # the live request path.
    try:
        from app.jd.orchestrator import _get_drift
        drift = _get_drift()
    except Exception:
        from app.jd.drift import DriftMonitor
        from app.auth.runtime import get_kvstore
        drift = DriftMonitor(get_kvstore())

    adapters: dict[str, dict] = {}
    degraded: list[str] = []
    seen: set[str] = set()
    for adapter in _ADAPTERS:
        pid = adapter.PLATFORM_ID
        if pid in seen:
            continue
        seen.add(pid)
        try:
            stats = await drift.stats(pid)
        except Exception:
            stats = {"state": "unknown", "success": 0, "failure": 0, "success_rate": 1.0, "samples": 0}
        stats["requires_js"] = bool(getattr(adapter, "REQUIRES_JS", False))
        adapters[pid] = stats
        if stats["state"] in ("open", "half-open"):
            degraded.append(pid)

    return {
        "adapters": adapters,
        "degraded": degraded,
        "healthy_count": len(adapters) - len(degraded),
        "degraded_count": len(degraded),
        "overall": "degraded" if degraded else "healthy",
    }


async def overall_health() -> str:
    """Fleet-level health string: 'healthy' or 'degraded'."""
    snap = await adapter_health_snapshot()
    return snap["overall"]
