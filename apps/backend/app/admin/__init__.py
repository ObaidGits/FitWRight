"""P2 Admin subsystem.

Capability-gated admin surface built on the P1 RBAC/audit/session foundation:
an overview dashboard + daily rollup, cursor-paginated user search + audited
detail, a recoverable grace-period deletion + resumable purge (audit retained),
role management with an atomic active-admin guard, and an append-only audit
view. Cross-user reads — the only ones allowed in the product — are centralized
in the isolated, CI-allowlisted :class:`app.admin.repo.AdminRepo`.
"""

from app.admin.metrics import (
    AdminMetrics,
    AdminMetricsMiddleware,
    get_admin_metrics,
    reset_admin_metrics,
)

__all__ = [
    "AdminMetrics",
    "AdminMetricsMiddleware",
    "get_admin_metrics",
    "reset_admin_metrics",
]
