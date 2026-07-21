"""Admin capability guards + per-admin rate limiting (Task 1.1, R1.*/14.2).

Every ``/api/v1/admin/*`` route depends on :func:`require_admin_read` or
:func:`require_admin_manage`. The guard, in order:

1. **Kill-switch** - when ``ADMIN_ENABLED`` is off the whole surface 404s
   (``admin_disabled``), so the rollout can ship flag-off then flip it on.
2. **AuthN** - an anonymous caller -> 401; the denial is audited ``authz.denied``
   with the route (R1.1) and counted for the compromised-admin/scraping signal.
3. **Per-request status recheck** - a principal only exists if the session
   resolved (which already re-checks ``status == active`` against the DB and is
   evicted on disable), but we re-assert it here as defense in depth so a
   disabled/soft-deleted admin can never act (R1.2).
4. **Capability** - reads need ``admin.read``, mutations ``admin.manage`` (R1.3);
   a lacking capability -> 403 + ``authz.denied`` audit.
5. **Per-admin rate limit** - separate read/write buckets keyed by the admin's
   user id (R14.2); abnormal volume -> 429 + audited so scraping / a compromised
   admin is visible. Fails **open** on a KVStore blip (the caller is already an
   authenticated admin - a store outage must not lock admins out of ops).

Mutations additionally require the P1 CSRF token, which is enforced by the
``AuthMiddleware`` on every state-changing request once a session is present.
"""

from __future__ import annotations

import logging

from fastapi import Request

from app.admin.metrics import get_admin_metrics
from app.auth import Capabilities, Principal, get_optional_principal
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.auth.sessions import get_session_service
from app.config import settings
from app.errors import ApiError
from app.routers._auth_deps import client_ip

logger = logging.getLogger(__name__)

__all__ = ["require_admin_read", "require_admin_manage"]

# Per-admin fixed-window buckets (R14.2). Reads are generous (dashboards poll +
# paginate); writes are stricter (a burst of mutations is a compromised-admin
# signal). Tunable knobs live here so they are the single place to adjust.
_READ_RULE = RateLimitRule(limit=240, window_seconds=60)
_WRITE_RULE = RateLimitRule(limit=60, window_seconds=60)


async def _audit_denied(request: Request, actor_user_id: str | None, *, capability: str | None) -> None:
    """Best-effort ``authz.denied`` audit for an admin access failure (R1.1)."""
    try:
        meta = {"path": request.url.path}
        if capability:
            meta["capability"] = capability
        await get_audit_service().record(
            AuditEvent.AUTHZ_DENIED,
            actor_user_id=actor_user_id,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=get_session_service().hash_ip(client_ip(request)),
            meta=meta,
        )
    except Exception:  # pragma: no cover - audit must not break the flow
        logger.debug("Failed to audit admin authz denial", exc_info=True)
    get_admin_metrics().record_authz_denied()


def _guard(capability: str, *, write: bool):
    async def _dep(request: Request) -> Principal:
        # 1) kill-switch
        if not settings.admin_enabled:
            raise ApiError(404, "admin_disabled", "The admin surface is disabled.")

        # 2) authN
        principal = get_optional_principal(request)
        if principal is None:
            await _audit_denied(request, None, capability=None)
            raise ApiError(401, "unauthorized", "Authentication required.")

        # 3) per-request status recheck (defense in depth - R1.2)
        if principal.status != "active":
            await _audit_denied(request, principal.user_id, capability=capability)
            raise ApiError(403, "forbidden", "This action is not permitted.")

        # 4) capability
        if not principal.has_capability(capability):
            await _audit_denied(request, principal.user_id, capability=capability)
            raise ApiError(403, "forbidden", "This action is not permitted.")

        # 5) per-admin rate limit (read/write buckets - R14.2)
        route_class = "admin_write" if write else "admin_read"
        rule = _WRITE_RULE if write else _READ_RULE
        result = await get_rate_limiter().check(
            route_class, f"admin:{principal.user_id}", rule, fail_closed=False
        )
        # Deliberate fail-OPEN on a KVStore outage (R14.2 tradeoff): an admin is
        # already authenticated, and locking every admin out of ops during a
        # cache blip would block incident response (e.g. disabling a compromised
        # account). The degraded state is made observable - counted + logged -
        # so an operator can see "admin ops proceeding unthrottled" during an
        # outage rather than it happening silently.
        if result.fail_closed:
            get_admin_metrics().incr("ratelimit_degraded")
            logger.warning(
                "Admin rate limiter degraded (KVStore unavailable); failing open for %s",
                route_class,
            )
        if not result.allowed:
            try:
                await get_audit_service().record(
                    "admin.rate_limited",
                    actor_user_id=principal.user_id,
                    request_id=getattr(request.state, "request_id", None),
                    meta={"path": request.url.path, "bucket": route_class},
                )
            except Exception:  # pragma: no cover
                logger.debug("Failed to audit admin rate limit", exc_info=True)
            raise ApiError(
                429,
                "rate_limited",
                "Too many admin requests. Please slow down.",
                headers={"Retry-After": str(result.retry_after or rule.window_seconds)},
            )
        return principal

    return _dep


require_admin_read = _guard(Capabilities.ADMIN_READ, write=False)
require_admin_manage = _guard(Capabilities.ADMIN_MANAGE, write=True)
