"""Capability-gated admin API surface (Tasks 1-7).

Every route depends on :func:`~app.admin.deps.require_admin_read` (reads) or
:func:`~app.admin.deps.require_admin_manage` (mutations), which enforce the
kill-switch, authN (401 + audit), per-request status recheck (403), the
capability (403 + audit), and per-admin rate limits (429). Mutations additionally
carry the P1 CSRF token (enforced by ``AuthMiddleware``). Responses are the
explicit allowlisted Pydantic models in :mod:`app.admin.schemas` (Property 2);
lists are keyset-cursor paginated (R11.1); sensitive reads (user detail) are
audited ``admin.user_viewed`` (R5.3). All errors use the ADR-7 envelope.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from app.admin.cursor import CursorError, sanitize_query
from app.admin.deps import require_admin_manage, require_admin_read
from app.admin.lifecycle import (
    ConfirmMismatchError,
    DestructiveDisabledError,
    InvalidValueError,
    LastActiveAdminError,
    LifecycleOutcome,
    SelfActionError,
    UserNotFoundError,
    get_lifecycle_service,
)
from app.admin.ai_metrics import get_ai_metrics_service
from app.analytics.feature_usage import get_feature_usage_service
from app.admin.config_diag import get_config_service
from app.admin.errors_metrics import get_errors_metrics_service
from app.admin.health_service import get_health_service
from app.admin.jobs_panel import get_jobs_panel_service
from app.admin.maintenance import MaintenanceAction, get_maintenance_service
from app.admin.metrics_service import UnknownMetricError, get_metrics_service
from app.admin.overview import get_overview_service
from app.admin.perf_metrics import get_perf_metrics_service
from app.admin.security_metrics import get_security_metrics_service
from app.admin.storage_metrics import get_storage_metrics_service
from app.admin.repo import AdminUserRowData, get_admin_repo
from app.admin.schemas import (
    AdminHealth,
    AiAnalytics,
    AdminStats,
    AdminUserDetail,
    AdminUserList,
    AdminUserRow,
    AuditEntry,
    AuditList,
    AdminInviteList,
    AdminInviteView,
    BulkDisableRequest,
    BulkDisableResult,
    ConfigDiagnostics,
    CreatedInvite,
    CreateInviteRequest,
    DeleteUserRequest,
    ErrorsSummary,
    FeatureUsage,
    JobsPanel,
    MaintenanceResult,
    MutationResult,
    OverviewKpis,
    PatchUserRequest,
    PerformanceSignals,
    ResumeAnalytics,
    SecurityView,
    StoragePanel,
    UsageSeries,
)
from app.auth import Principal
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.sessions import get_session_service
from app.config import settings
from app.errors import ApiError
from app.models import AuditLog, User
from app.routers._auth_deps import client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Response builders (allowlisted projection)
# ---------------------------------------------------------------------------


def _purge_due_at(deleted_at: str | None) -> str | None:
    """The instant a soft-deleted user becomes purge-eligible (deleted_at+grace)."""
    if not deleted_at:
        return None
    from datetime import datetime, timedelta

    try:
        dt = datetime.fromisoformat(deleted_at)
    except (ValueError, TypeError):
        return None
    return (dt + timedelta(days=settings.admin_delete_grace_days)).isoformat()


def _row(data: AdminUserRowData) -> AdminUserRow:
    return AdminUserRow(
        id=data.id,
        name=data.name,
        email=data.email,
        role=data.role,
        status=data.status,
        emailVerified=data.email_verified,
        createdAt=data.created_at,
        deletedAt=data.deleted_at,
        purgeDueAt=_purge_due_at(data.deleted_at),
        resumeCount=data.resume_count,
        applicationCount=data.application_count,
        lastActiveAt=data.last_active_at,
    )


def _audit_entry(row: AuditLog) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        ts=row.ts,
        event=row.event,
        actorUserId=row.actor_user_id,
        targetUserId=row.target_user_id,
        ipHash=row.ip_hash,
        requestId=row.request_id,
        meta=row.meta,
    )


def _ip_hash(request: Request) -> str | None:
    return get_session_service().hash_ip(client_ip(request))


def _map_lifecycle_error(exc: Exception) -> ApiError:
    if isinstance(exc, UserNotFoundError):
        return ApiError(404, "not_found", "User not found.")
    if isinstance(exc, LastActiveAdminError):
        return ApiError(
            409,
            "last_active_admin",
            "This action would remove the last active admin.",
        )
    if isinstance(exc, SelfActionError):
        return ApiError(409, "self_action", "You cannot perform this action on yourself.")
    if isinstance(exc, ConfirmMismatchError):
        return ApiError(400, "confirm_mismatch", "The confirmation did not match.")
    if isinstance(exc, InvalidValueError):
        return ApiError(400, "invalid_value", "Invalid value.")
    if isinstance(exc, DestructiveDisabledError):
        return ApiError(403, "destructive_disabled", "Destructive actions are disabled.")
    return ApiError(400, "error", "The request could not be completed.")


def _outcome_response(outcome: LifecycleOutcome) -> MutationResult:
    return MutationResult(
        changed=outcome.changed,
        user=_row(outcome.row) if outcome.row is not None else None,
    )


def _record_action(action: str, result: str) -> None:
    from app.admin.metrics import get_admin_metrics

    get_admin_metrics().record_action(action, result)


# ---------------------------------------------------------------------------
# Dashboards
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=AdminStats)
async def get_stats(_admin: Principal = Depends(require_admin_read)) -> AdminStats:
    """Overview stats with ``computedAt`` + ``stale`` (rollup + live-today)."""
    data = await get_metrics_service().stats()
    return AdminStats(**data)


@router.get("/health", response_model=AdminHealth)
async def get_admin_health(_admin: Principal = Depends(require_admin_read)) -> AdminHealth:
    """System Health: six subsystem tiles + release fields + jobs table (R3, 17).

    Composed from signals the backend already produces (readiness DB/KVStore
    probes, cached ``/status`` LLM health, storage config, Alembic head-vs-applied)
    under a per-source 2s timeout - never a new infra probe (R3.1/3.6, R21.3/4/5).
    """
    return await get_health_service().compose_health()


@router.get("/jobs", response_model=JobsPanel)
async def get_admin_jobs(_admin: Principal = Depends(require_admin_read)) -> JobsPanel:
    """Background jobs panel from run markers and worker-independent gauges.

    Job timing/stuck state comes from KV markers. Purge backlog uses its gauge
    with an indexed fallback. Queue backlog and dead-letter counts come from the
    authoritative outbox stats query; either is explicitly unavailable if that
    query fails. No audit-log or users-table scan is performed.
    """
    return await get_jobs_panel_service().panel()


@router.get("/config", response_model=ConfigDiagnostics)
async def get_admin_config(
    request: Request,
    admin: Principal = Depends(require_admin_read),
) -> ConfigDiagnostics:
    """Read-only configuration diagnostics (R10): env, providers, flags,
    kill-switches, grace period, versions - secrets as presence booleans only.

    This is a Sensitive_Endpoint (config diagnostics), so the access is audited
    ``admin.config_viewed`` before the payload is returned (R15.3). Per R15.9 the
    audit write is strict: if recording the access fails, the endpoint surfaces
    an error and does NOT return the configuration (the access is only legitimate
    when it is traceable). The endpoint performs no mutation (R10.3 / 21.7).
    """
    diagnostics = get_config_service().diagnostics()
    saved_providers = await get_admin_repo().distinct_configured_ai_providers()
    provider_names = sorted(
        {
            str(provider).strip().lower()
            for provider in [*diagnostics.activeAiProviders, *saved_providers]
            if provider and str(provider).strip()
        }
    )
    diagnostics = diagnostics.model_copy(
        update={
            "activeAiProviders": provider_names,
            "providersSource": (
                "default configuration plus saved credential provider names "
                "(connectivity unverified)"
            ),
        }
    )

    # Audit the sensitive config read (R15.3). A failed audit is a hard error for
    # this endpoint (R15.9): do not report success or return any config data.
    try:
        await get_audit_service().record(
            AuditEvent.ADMIN_CONFIG_VIEWED,
            actor_user_id=admin.user_id,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
            raise_on_error=True,
        )
    except Exception as exc:
        logger.error("Config diagnostics access could not be audited: %s", exc)
        raise ApiError(
            500,
            "audit_failed",
            "The configuration diagnostics access could not be recorded.",
        )

    return diagnostics


@router.get("/ai-analytics", response_model=AiAnalytics)
async def get_admin_ai_analytics(
    window: int = Query(30, ge=1, le=365),
    _admin: Principal = Depends(require_admin_read),
) -> AiAnalytics:
    """AI analytics: call aggregates and provider breakdown for ``window``.

    Durable daily metrics are combined with this process's unflushed activity.
    Success/failure rates and provider call counts are instrumented; selected-
    window cost is not, so ``estimatedCostDollars`` is null and the payload marks
    cost unavailable rather than fabricating a value. The response is allowlisted
    and secret-free.
    """
    return await get_ai_metrics_service().analytics(window)


@router.get("/errors", response_model=ErrorsSummary)
async def get_admin_errors(
    window: int = Query(30),
    _admin: Principal = Depends(require_admin_read),
) -> ErrorsSummary:
    """Errors summary: grouped 4xx/5xx counts + by-source + trend (R5).

    An O(1) read (Req 5.7) served from durable ``metrics_daily`` keys via the
    shared Metric_Store - grouped buckets only, never a raw log/stack/trace/
    exception/replay explorer (Non-Goal, Req 21.2). ``require_admin_read``
    enforces the kill-switch, authN (401), status recheck + capability (403),
    and the per-admin rate limit (429) before any data is read (Req 5.6).

    ``window`` must be one of the fixed dashboard windows {7, 30, 90} (default
    30 when omitted); any other value is rejected with a 400 ``invalid_window``
    (Req 5.5). We deliberately validate to this discrete set with an explicit
    400 rather than a range (``ge``/``le`` would yield a framework 422), matching
    the discrete-window contract the dashboard offers.
    """
    if window not in (7, 30, 90):
        raise ApiError(
            400,
            "invalid_window",
            "The window must be one of 7, 30, or 90 days.",
        )
    return await get_errors_metrics_service().summary(window)


@router.get(
    "/performance",
    response_model=PerformanceSignals,
    response_model_exclude_none=True,
)
async def get_admin_performance(
    _admin: Principal = Depends(require_admin_read),
) -> PerformanceSignals:
    """Performance signals: per-route-class latency + slow routes/jobs (R6).

    The bounded request reads one current ``AdminMetrics`` snapshot (including
    route-class p95/failure observations and cache population) plus fixed KV job
    markers. It performs no row scan and does not mutate instrumentation.
    ``require_admin_read`` enforces the kill-switch, authN (401), status recheck +
    capability (403), and the per-admin rate limit (429) before any data is read
    (Req 15.1).

    ``response_model_exclude_none=True`` drops every ``None`` field from the
    payload - this is the Req 6.5 omission mechanism for the optional host
    metrics (``memoryBytes`` / ``cpuPercent`` / ``diskBytes``), which are a
    Non-Goal (Req 21.4) and are never produced. ``dbQueryTimeMs`` is likewise
    omitted; the client learns it is a wired-but-empty signal from its presence
    in the ``unavailable`` list (Req 6.7). Present-but-empty aggregates
    (route-class latency, ``cacheHitRatio=0.0``) are retained.
    """
    return await get_perf_metrics_service().signals()


@router.get("/storage", response_model=StoragePanel)
async def get_admin_storage(
    _admin: Principal = Depends(require_admin_read),
) -> StoragePanel:
    """Storage panel from rollup snapshots; never a request-time size probe.

    DB size, record counts, and growth include explicit stale/unavailable state.
    Local object bytes are sampled by an off-request-path filesystem walk;
    remote provider usage is unavailable because those adapters expose no usage
    API. No live database-size query or remote object enumeration runs here.
    """
    return await get_storage_metrics_service().panel()


@router.get("/security", response_model=SecurityView)
async def get_admin_security(
    _admin: Principal = Depends(require_admin_read),
) -> SecurityView:
    """Security view from exact indexed audit rows in ``[now - 24h, now)``.

    Counts failed logins, current-role admin logins, authorization denials,
    centralized rate-limit denials, and CAPTCHA enforcement denials. The latter
    two are exact only from deployment of their audit instrumentation onward.
    ``adminLoginRoleBasis`` discloses that admin role is evaluated at query time;
    no daily proxy or rollup lag is used.
    """
    return await get_security_metrics_service().view()


@router.get("/kpis", response_model=OverviewKpis)
async def get_admin_kpis(_admin: Principal = Depends(require_admin_read)) -> OverviewKpis:
    """Overview KPI cards: totals, today's signups/AI calls, error-rate proxy,
    and purge backlog.

    ``errorRate24h`` is named for dashboard compatibility but is a daily-granularity
    proxy: durable request buckets for today plus yesterday, not an exact rolling
    24-hour boundary. Other cards use their documented snapshot/live sources and
    become explicitly unavailable if their source cannot be read.
    """
    return await get_overview_service().kpis()


@router.get("/usage-series", response_model=UsageSeries)
async def get_usage_series(
    metric: str = Query(...),
    window: int = Query(30),
    _admin: Principal = Depends(require_admin_read),
) -> UsageSeries:
    """Daily series for a registry metric over a 7/30/90-day window."""
    try:
        data = await get_metrics_service().usage_series(metric, window)
    except UnknownMetricError:
        raise ApiError(400, "unknown_metric", f"Unknown metric: {sanitize_query(metric)}")
    return UsageSeries(**data)


@router.get("/analytics/feature-usage", response_model=FeatureUsage)
async def get_feature_usage(
    window: int = Query(30),
    _admin: Principal = Depends(require_admin_read),
) -> FeatureUsage:
    """Feature-usage analytics: daily per-feature totals over 7/30/90 days (R16).

    An O(1) read (Req 16.5) served from the durable ``FEAT_*`` ``metrics_daily``
    keys via the shared Metric_Store. Returns zero-filled daily series per
    tracked feature - aggregate totals only, no user-level data (Req 16.6).

    ``window`` must be one of the fixed dashboard windows {7, 30, 90} (default
    30 when omitted); any other value is rejected with a 400 ``invalid_window``
    (Req 16.3). ``require_admin_read`` enforces the kill-switch, authN (401),
    status recheck + capability (403), and the per-admin rate limit (429) before
    any data is read (Req 15.1).
    """
    if window not in (7, 30, 90):
        raise ApiError(
            400,
            "invalid_window",
            "The window must be one of 7, 30, or 90 days.",
        )
    return await get_feature_usage_service().series(window)


@router.get("/analytics/resumes", response_model=ResumeAnalytics)
async def get_resume_analytics(
    window: int = Query(30),
    _admin: Principal = Depends(require_admin_read),
) -> ResumeAnalytics:
    """Resume inventory split, template inventory, and window activity (R14).

    An O(1) read served from the current ``resume_snapshot`` (inventory source
    counts + template counts) plus four zero-filled daily event series. Deletion
    activity and net inventory change are selected-window values; source and
    template sections are point-in-time inventory snapshots.

    ``window`` must be one of the fixed dashboard windows {7, 30, 90} (default
    30 when omitted); any other value is rejected with a 400 ``invalid_window``
    (Req 14.4). No funnels/retention/cohorts (Req 14.6).
    """
    if window not in (7, 30, 90):
        raise ApiError(
            400,
            "invalid_window",
            "The window must be one of 7, 30, or 90 days.",
        )
    from app.analytics.resume_metrics import get_resume_metrics_service

    return await get_resume_metrics_service().analytics(window)


# ---------------------------------------------------------------------------
# Users list + detail
# ---------------------------------------------------------------------------


@router.get("/users", response_model=AdminUserList)
async def list_users(
    cursor: str | None = Query(default=None),
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    role: str | None = Query(default=None),
    verified: bool | None = Query(default=None),
    deleted: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: Principal = Depends(require_admin_read),
) -> AdminUserList:
    """Cursor-paginated user list with index-usable search + filters."""
    try:
        rows, next_cursor = await get_admin_repo().list_users(
            cursor=cursor,
            q=sanitize_query(q),
            status=status,
            role=role,
            verified=verified,
            deleted=deleted,
            limit=limit,
        )
    except CursorError:
        raise ApiError(400, "bad_cursor", "The pagination cursor is invalid.")
    return AdminUserList(items=[_row(r) for r in rows], nextCursor=next_cursor)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user_detail(
    user_id: str,
    request: Request,
    admin: Principal = Depends(require_admin_read),
) -> AdminUserDetail:
    """User detail: profile + activity summary + recent audit (audited read)."""
    from app.database import db  # lazy: honor test DB isolation (monkeypatched)

    repo = get_admin_repo()
    async with db.session_factory() as session:
        row = await session.get(User, user_id)
    if row is None:
        raise ApiError(404, "not_found", "User not found.")

    activity = await repo.user_activity(user_id)
    recent = await repo.recent_audit_for_target(user_id, limit=20)

    # Audit the sensitive cross-user read (R5.3) - traceable admin access.
    await get_audit_service().record(
        AuditEvent.ADMIN_USER_VIEWED,
        actor_user_id=admin.user_id,
        target_user_id=user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_hash=_ip_hash(request),
    )

    return AdminUserDetail(
        id=row.id,
        name=row.name,
        email=row.email,
        role=row.role,
        status=row.status,
        emailVerified=row.email_verified_at is not None,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
        deletedAt=row.deleted_at,
        purgeDueAt=_purge_due_at(row.deleted_at),
        resumeCount=activity.resume_count,
        tailoredCount=activity.tailored_count,
        applicationCount=activity.application_count,
        lastActiveAt=activity.last_active_at,
        signupMethod=activity.signup_method,
        aiConfigured=activity.ai_configured,
        recentAudit=[_audit_entry(a) for a in recent],
    )


# ---------------------------------------------------------------------------
# Lifecycle mutations
# ---------------------------------------------------------------------------


@router.patch("/users/{user_id}", response_model=MutationResult)
async def patch_user(
    user_id: str,
    payload: PatchUserRequest,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Set ``status`` and/or ``role`` (distinct audit events; idempotent)."""
    if payload.status is None and payload.role is None:
        raise ApiError(400, "invalid_value", "Provide a status and/or role to change.")
    svc = get_lifecycle_service()
    ip_hash = _ip_hash(request)
    rid = getattr(request.state, "request_id", None)
    try:
        if payload.role is not None and payload.status is not None:
            # Both fields -> single atomic transaction (no partial apply, M2 fix).
            outcome = await svc.set_role_and_status(
                actor_id=admin.user_id,
                target_id=user_id,
                new_role=payload.role,
                new_status=payload.status,
                request_id=rid,
                ip_hash=ip_hash,
            )
            _record_action("patch", "ok" if outcome.changed else "no_op")
        elif payload.role is not None:
            outcome = await svc.set_role(
                actor_id=admin.user_id,
                target_id=user_id,
                new_role=payload.role,
                request_id=rid,
                ip_hash=ip_hash,
            )
            _record_action("role_change", "ok" if outcome.changed else "no_op")
        else:
            outcome = await svc.set_status(
                actor_id=admin.user_id,
                target_id=user_id,
                new_status=payload.status,  # type: ignore[arg-type]
                request_id=rid,
                ip_hash=ip_hash,
            )
            action = "disable" if payload.status == "disabled" else "enable"
            _record_action(action, "ok" if outcome.changed else "no_op")
    except (LastActiveAdminError, SelfActionError) as exc:
        _record_action(
            "patch", "last_active_admin" if isinstance(exc, LastActiveAdminError) else "self_action"
        )
        raise _map_lifecycle_error(exc)
    except (UserNotFoundError, InvalidValueError) as exc:
        raise _map_lifecycle_error(exc)
    return _outcome_response(outcome)


# ---------------------------------------------------------------------------
# Admin invites (secure admin signup - Option B)
# ---------------------------------------------------------------------------


def _invite_view(rec) -> AdminInviteView:
    """Project an invite lifecycle record (never its token hash)."""
    return AdminInviteView(
        id=rec.id,
        email=rec.email,
        role=rec.role,
        createdBy=rec.created_by,
        createdAt=rec.created_at,
        expiresAt=rec.expires_at,
        status=rec.status,
        usedAt=rec.used_at,
        usedBy=rec.used_by,
        revokedAt=rec.revoked_at,
        revokedBy=rec.revoked_by,
        revokeReason=rec.revoke_reason,
    )


@router.get("/invites", response_model=AdminInviteList)
async def list_invites(
    request: Request,
    admin: Principal = Depends(require_admin_read),
) -> AdminInviteList:
    """List bounded recent admin-invite lifecycle history."""
    from app.auth.admin_invites import list_invites as list_invite_history

    records = await list_invite_history()
    return AdminInviteList(items=[_invite_view(r) for r in records])


@router.post("/invites", response_model=CreatedInvite)
async def create_admin_invite(
    payload: CreateInviteRequest,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> CreatedInvite:
    """Issue a single-use, email-bound admin invite; returns the shareable URL.

    The raw token is embedded in ``inviteUrl`` and shown ONLY in this response
    (never stored or retrievable again). Only ``admin.manage`` can call this,
    and the created invite always mints an ``admin`` account on redemption.
    """
    from urllib.parse import quote

    from app.auth.admin_invites import create_invite

    raw_token, rec = await create_invite(
        email=payload.email,
        created_by=admin.user_id,
        ttl_hours=payload.ttlHours,
    )
    _record_action("invite_create", "ok")
    try:
        await get_audit_service().record(
            AuditEvent.ADMIN_INVITE_CREATED,
            actor_user_id=admin.user_id,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
            meta={"email": rec.email, "invite_id": rec.id, "role": rec.role},
        )
    except Exception:  # pragma: no cover - audit must not break the flow
        logger.debug("Failed to audit invite creation", exc_info=True)

    base = settings.frontend_base_url.rstrip("/")
    invite_url = (
        f"{base}/signup?invite={quote(raw_token, safe='')}"
        f"&email={quote(rec.email, safe='')}"
    )
    return CreatedInvite(
        id=rec.id,
        email=rec.email,
        role=rec.role,
        expiresAt=rec.expires_at,
        inviteUrl=invite_url,
    )


@router.delete("/invites/{invite_id}", response_model=MutationResult)
async def revoke_admin_invite(
    invite_id: str,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Revoke an outstanding invite by id (idempotent -> changed:false if gone)."""
    from app.auth.admin_invites import revoke_invite

    changed = await revoke_invite(
        invite_id,
        revoked_by=admin.user_id,
        reason="manual",
    )
    _record_action("invite_revoke", "ok" if changed else "no_op")
    if changed:
        try:
            await get_audit_service().record(
                AuditEvent.ADMIN_INVITE_REVOKED,
                actor_user_id=admin.user_id,
                request_id=getattr(request.state, "request_id", None),
                ip_hash=_ip_hash(request),
                meta={"invite_id": invite_id},
            )
        except Exception:  # pragma: no cover - audit must not break the flow
            logger.debug("Failed to audit invite revocation", exc_info=True)
    return MutationResult(changed=changed)


@router.post("/users/{user_id}/disable", response_model=MutationResult)
async def disable_user(
    user_id: str,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Explicit disable (idempotent; atomic active-admin guard)."""
    try:
        outcome = await get_lifecycle_service().set_status(
            actor_id=admin.user_id,
            target_id=user_id,
            new_status="disabled",
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
        )
    except LastActiveAdminError as exc:
        _record_action("disable", "last_active_admin")
        raise _map_lifecycle_error(exc)
    except UserNotFoundError as exc:
        raise _map_lifecycle_error(exc)
    _record_action("disable", "ok" if outcome.changed else "no_op")
    return _outcome_response(outcome)


@router.post("/users/{user_id}/enable", response_model=MutationResult)
async def enable_user(
    user_id: str,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Explicit enable (idempotent)."""
    try:
        outcome = await get_lifecycle_service().set_status(
            actor_id=admin.user_id,
            target_id=user_id,
            new_status="active",
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
        )
    except UserNotFoundError as exc:
        raise _map_lifecycle_error(exc)
    _record_action("enable", "ok" if outcome.changed else "no_op")
    return _outcome_response(outcome)


@router.post("/users/bulk-disable", response_model=BulkDisableResult)
async def bulk_disable(
    payload: BulkDisableRequest,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> BulkDisableResult:
    """Bounded batch disable (per-target audit + invariant, R6.4)."""
    if len(payload.ids) > settings.admin_bulk_disable_max:
        raise ApiError(
            400,
            "batch_too_large",
            f"At most {settings.admin_bulk_disable_max} users can be disabled at once.",
        )
    results = await get_lifecycle_service().bulk_disable(
        actor_id=admin.user_id,
        target_ids=payload.ids,
        request_id=getattr(request.state, "request_id", None),
        ip_hash=_ip_hash(request),
    )
    disabled = sum(1 for r in results if r["result"] == "disabled")
    skipped = len(results) - disabled
    _record_action("bulk_disable", "ok")
    return BulkDisableResult(results=results, disabled=disabled, skipped=skipped)


@router.post("/users/{user_id}/delete", response_model=MutationResult)
async def delete_user(
    user_id: str,
    payload: DeleteUserRequest,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Soft-delete with typed-email confirmation (grace-period recoverable)."""
    try:
        outcome = await get_lifecycle_service().soft_delete(
            actor_id=admin.user_id,
            target_id=user_id,
            email_confirm=payload.email,
            destructive_enabled=settings.admin_destructive_actions,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
        )
    except (
        LastActiveAdminError,
        SelfActionError,
        ConfirmMismatchError,
        DestructiveDisabledError,
        UserNotFoundError,
    ) as exc:
        if isinstance(exc, LastActiveAdminError):
            _record_action("delete", "last_active_admin")
        raise _map_lifecycle_error(exc)
    _record_action("delete", "ok" if outcome.changed else "no_op")
    return _outcome_response(outcome)


@router.post("/users/{user_id}/restore", response_model=MutationResult)
async def restore_user(
    user_id: str,
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MutationResult:
    """Restore a soft-deleted user within the grace period (R8.2)."""
    try:
        outcome = await get_lifecycle_service().restore(
            actor_id=admin.user_id,
            target_id=user_id,
            destructive_enabled=settings.admin_destructive_actions,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
        )
    except (DestructiveDisabledError, UserNotFoundError) as exc:
        raise _map_lifecycle_error(exc)
    _record_action("restore", "ok" if outcome.changed else "no_op")
    return _outcome_response(outcome)


# ---------------------------------------------------------------------------
# Audit view
# ---------------------------------------------------------------------------


@router.get("/audit", response_model=AuditList)
async def list_audit(
    cursor: str | None = Query(default=None),
    event: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    target: str | None = Query(default=None),
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: Principal = Depends(require_admin_read),
) -> AuditList:
    """Cursor-paginated, filterable audit view (append-only; no mutate API)."""
    try:
        rows, next_cursor = await get_admin_repo().list_audit(
            cursor=cursor,
            event=sanitize_query(event),
            actor=sanitize_query(actor),
            target=sanitize_query(target),
            date_from=sanitize_query(from_),
            date_to=sanitize_query(to),
            limit=limit,
        )
    except CursorError:
        raise ApiError(400, "bad_cursor", "The pagination cursor is invalid.")
    return AuditList(items=[_audit_entry(r) for r in rows], nextCursor=next_cursor)


# ---------------------------------------------------------------------------
# Maintenance actions (the ONLY writes here beyond user lifecycle) - Req 18
#
# Exactly four ``admin.manage`` POST actions, each of which only re-invokes an
# existing single-flighted job/refresh. ``require_admin_manage`` applies the
# per-admin *write* rate limit (Req 18.2). Each invocation is audited with
# ``raise_on_error=True`` so a failed audit surfaces as an error and success is
# never reported without a traceable record (Req 18.6). No destructive/SQL/
# config-edit action is exposed (Req 18.5).
# ---------------------------------------------------------------------------


async def _run_maintenance(request: Request, admin: Principal, action: str) -> MaintenanceResult:
    """Dispatch one fixed maintenance action, then audit it (Req 18.2/18.3/18.6).

    Invokes the single-flighted job via the frozen dispatcher, records an
    ``admin.maintenance_action`` audit entry (strict: a failed audit is a hard
    error, so we do NOT report success), and returns the small secret-free
    :class:`MaintenanceResult`.
    """
    result = await get_maintenance_service().run(action)
    status = result["status"]

    # Audit the invocation (Req 18.2). Strict per Req 18.6: if recording fails,
    # surface an error and do not report the action as successful.
    try:
        await get_audit_service().record(
            AuditEvent.ADMIN_MAINTENANCE_ACTION,
            actor_user_id=admin.user_id,
            request_id=getattr(request.state, "request_id", None),
            ip_hash=_ip_hash(request),
            meta={"action": action, "status": status},
            raise_on_error=True,
        )
    except Exception as exc:
        logger.error("Maintenance action %s could not be audited: %s", action, exc)
        raise ApiError(
            500,
            "audit_failed",
            "The maintenance action could not be recorded.",
        )

    _record_action(f"maintenance_{action}", status)
    return MaintenanceResult(action=action, status=status)


@router.post("/maintenance/refresh-metrics", response_model=MaintenanceResult)
async def maintenance_refresh_metrics(
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MaintenanceResult:
    """Re-invoke the cached-metrics (totals snapshot) refresh (Req 18)."""
    return await _run_maintenance(request, admin, MaintenanceAction.REFRESH_METRICS)


@router.post("/maintenance/run-rollup", response_model=MaintenanceResult)
async def maintenance_run_rollup(
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MaintenanceResult:
    """Re-invoke the full rollup job (Req 18)."""
    return await _run_maintenance(request, admin, MaintenanceAction.RUN_ROLLUP)


@router.post("/maintenance/run-cleanup", response_model=MaintenanceResult)
async def maintenance_run_cleanup(
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MaintenanceResult:
    """Re-invoke the purge/cleanup job (Req 18); ``disabled`` when gated off."""
    return await _run_maintenance(request, admin, MaintenanceAction.RUN_CLEANUP)


@router.post("/maintenance/run-retention", response_model=MaintenanceResult)
async def maintenance_run_retention(
    request: Request,
    admin: Principal = Depends(require_admin_manage),
) -> MaintenanceResult:
    """Re-invoke the audit-retention job (Req 18)."""
    return await _run_maintenance(request, admin, MaintenanceAction.RUN_RETENTION)
