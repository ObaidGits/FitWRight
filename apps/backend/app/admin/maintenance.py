"""Minimal safe maintenance actions for the admin surface (Task 8.2).

``MaintenanceService`` is a **thin dispatcher** over the existing, single-flighted
background jobs. It exposes EXACTLY four idempotent ``admin.manage`` actions and
nothing else (Req 18.1/18.5): each one only re-invokes an existing single-flighted
job (or the equally single-flighted totals-snapshot refresh) — there is no path
that runs arbitrary SQL, edits configuration/flags, flushes the whole cache,
controls deployment, or performs database maintenance.

Action → underlying single-flighted work (Req 18.3):

- ``refresh-metrics`` → :meth:`MetricsService.refresh_totals_snapshot` guarded by
  the Rollup_Job's own ``admin:rollup`` lock (acquired non-blocking, mirroring
  :func:`~app.admin.jobs.run_rollup_job`). This recomputes only the O(1) overview
  totals snapshot — a lighter "refresh cached metrics" than a full rollup — while
  still re-using the rollup single-flight lock so it can never race a running
  rollup (and reports ``already_running`` when that lock is held).
- ``run-rollup``   → :func:`~app.admin.jobs.run_rollup_job` (full rollup pipeline).
- ``run-cleanup``  → :func:`~app.admin.jobs.run_purge_job` (the grace-elapsed
  soft-deleted-user purge; the existing ``ADMIN_DESTRUCTIVE_ACTIONS`` kill-switch
  still gates it — a ``disabled`` result is surfaced through unchanged).
- ``run-retention``→ :func:`~app.admin.jobs.run_audit_retention_job`.

Every underlying job is idempotent + resumable and guarded by its own KVStore
single-flight lock with a TTL, so a duplicate invocation causes no extra effect
beyond the single run (Req 18.3), and a held lock yields ``already_running``
rather than a second run (Req 18.4). The jobs' native ``{"status": ...}`` shapes
are mapped to the small maintenance vocabulary:

    ``ok``       → ``started``
    ``locked``   → ``already_running``
    ``disabled`` → ``disabled``

**Structural "no other action exists" (Req 18.5).** The allowed actions live in a
single frozen mapping (:data:`MaintenanceService.ACTIONS`, a ``MappingProxyType``);
:meth:`MaintenanceService.run` dispatches only through it, so the action set is
exactly these four and cannot be extended at runtime. The router exposes one POST
route per entry and no other maintenance operation.

**Bounded-context purity (Req 19.2/19.3/19.5).** This Domain_Metrics_Service
depends only on the existing job entrypoints (``app.admin.jobs``), the shared
:class:`MetricsService` snapshot refresh (``app.admin.metrics_service``), the
shared KVStore, and the admin schemas — it imports no *other*
Domain_Metrics_Service, so the import-graph guard holds.

Rate limiting + audit are enforced at the router boundary: every route depends on
``require_admin_manage`` (which applies the per-admin *write* rate-limit bucket,
Req 18.2) and records an ``admin.maintenance_action`` audit entry with
``raise_on_error=True`` so a failed audit surfaces as an error and success is
never reported without a traceable record (Req 18.6).
"""

from __future__ import annotations

import logging
from types import MappingProxyType

from app.admin.jobs import (
    ROLLUP_LOCK_KEY,
    run_audit_retention_job,
    run_purge_job,
    run_rollup_job,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MaintenanceService",
    "MaintenanceAction",
    "ALLOWED_ACTIONS",
    "get_maintenance_service",
    "reset_maintenance_service",
]


class MaintenanceAction:
    """The four (and only four) allowed maintenance action names (Req 18.1)."""

    REFRESH_METRICS = "refresh-metrics"
    RUN_ROLLUP = "run-rollup"
    RUN_CLEANUP = "run-cleanup"
    RUN_RETENTION = "run-retention"


# TTL for the non-blocking ``admin:rollup`` lock held during the totals-snapshot
# refresh. The refresh is a handful of aggregate reads + a small UPSERT, so a
# short TTL is ample; auto-expiry frees a crashed holder before the next run.
_REFRESH_LOCK_TTL = 120


def _map_job_status(result: dict) -> str:
    """Map a job's native ``status`` to the maintenance vocabulary (Req 18.3/18.4).

    ``ok`` → ``started``; ``locked`` → ``already_running`` (single-flight lock was
    already held); ``disabled`` → ``disabled`` (kill-switch gated). Any other/absent
    status is treated conservatively as ``started`` (the job ran).
    """
    status = (result or {}).get("status")
    if status == "locked":
        return "already_running"
    if status == "disabled":
        return "disabled"
    return "started"


class MaintenanceService:
    """Thin dispatcher exposing exactly four single-flighted maintenance actions."""

    def __init__(self, *, kvstore=None) -> None:
        # Optional injected KVStore (tests); otherwise the process-wide singleton
        # is resolved lazily so the jobs pick up the same store.
        self._kv = kvstore

    def _kvstore(self):
        if self._kv is not None:
            return self._kv
        from app.auth.runtime import get_kvstore

        return get_kvstore()

    # -- the four actions ----------------------------------------------------

    async def refresh_metrics(self) -> dict:
        """Re-invoke the cached-metrics refresh (totals snapshot), single-flighted.

        Acquires the Rollup_Job's ``admin:rollup`` lock **non-blocking** (mirroring
        :func:`~app.admin.jobs.run_rollup_job`) so it can never race a running
        rollup; if the lock is already held it returns ``already_running`` without
        starting a second refresh (Req 18.4). Recomputes only the O(1) overview
        totals snapshot — the "refresh cached metrics" action — and is idempotent
        (re-running simply recomputes the same snapshot).
        """
        # Imported lazily: ``metrics_service`` is a shared primitive, but keeping
        # the import local mirrors the jobs module and avoids any load-order edge.
        from app.admin.metrics_service import get_metrics_service

        kv = self._kvstore()
        lock = kv.lock(ROLLUP_LOCK_KEY, ttl_seconds=_REFRESH_LOCK_TTL, blocking=False)
        async with lock as acquired:
            if not acquired:
                return {"status": "already_running"}
            await get_metrics_service().refresh_totals_snapshot()
            return {"status": "started"}

    async def run_rollup(self) -> dict:
        """Re-invoke the full rollup job (single-flighted via ``admin:rollup``)."""
        return {"status": _map_job_status(await run_rollup_job(kvstore=self._kv))}

    async def run_cleanup(self) -> dict:
        """Re-invoke the purge/cleanup job (single-flighted via ``admin:purge``).

        Preserves the ``ADMIN_DESTRUCTIVE_ACTIONS`` kill-switch: when destructive
        actions are off the underlying job is a no-op and this returns ``disabled``.
        """
        return {"status": _map_job_status(await run_purge_job(kvstore=self._kv))}

    async def run_retention(self) -> dict:
        """Re-invoke the audit-retention job (single-flighted ``admin:audit_retention``)."""
        return {"status": _map_job_status(await run_audit_retention_job(kvstore=self._kv))}

    # -- frozen dispatch (Req 18.5: exactly these four, nothing else) --------

    #: The complete, immutable action map. Its keys ARE the allowed action set;
    #: nothing outside it can be dispatched, structurally enforcing Req 18.5.
    ACTIONS: MappingProxyType = MappingProxyType(
        {
            MaintenanceAction.REFRESH_METRICS: "refresh_metrics",
            MaintenanceAction.RUN_ROLLUP: "run_rollup",
            MaintenanceAction.RUN_CLEANUP: "run_cleanup",
            MaintenanceAction.RUN_RETENTION: "run_retention",
        }
    )

    async def run(self, action: str) -> dict:
        """Dispatch ``action`` through the frozen :data:`ACTIONS` map only.

        Raises :class:`KeyError` for any action outside the fixed four — the router
        only ever passes one of the four constants, so this is a defensive guard
        that keeps the "no other action exists" invariant true even if a caller
        is added later.
        """
        method_name = self.ACTIONS[action]  # KeyError => unknown action (Req 18.5)
        return await getattr(self, method_name)()


# ---------------------------------------------------------------------------
# Process-wide instance (mirrors the other admin service accessors)
# ---------------------------------------------------------------------------

_service: MaintenanceService | None = None


def get_maintenance_service() -> MaintenanceService:
    """Return the process-wide :class:`MaintenanceService`."""
    global _service
    if _service is None:
        _service = MaintenanceService()
    return _service


def reset_maintenance_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None


#: Convenience export of the four allowed action names (frozenset) for tests +
#: callers that want to assert the action set without touching the service.
ALLOWED_ACTIONS: frozenset[str] = frozenset(MaintenanceService.ACTIONS.keys())
