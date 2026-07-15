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
    BulkDisableRequest,
    BulkDisableResult,
    ConfigDiagnostics,
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
    under a per-source 2s timeout — never a new infra probe (R3.1/3.6, R21.3/4/5).
    """
    return await get_health_service().compose_health()


@router.get("/jobs", response_model=JobsPanel)
async def get_admin_jobs(_admin: Principal = Depends(require_admin_read)) -> JobsPanel:
    """Background jobs panel: per-job state + stuck detection + gauges (R8).

    An O(1) read (<500ms, Req 8.4) served from the KV run markers written by the
    job runner + the worker-independent purge-backlog gauge — it never scans
    ``audit_log``/``users`` rows. Each row surfaces last/next run, last-success,
    running-since, current vs expected duration, potentially-stuck (from markers +
    ``admin_job_stuck_*`` config), and best-effort lock state; queue length is
    marked unavailable when no admin gauge exists (Req 8.7).
    """
    return await get_jobs_panel_service().panel()


@router.get("/config", response_model=ConfigDiagnostics)
async def get_admin_config(
    request: Request,
    admin: Principal = Depends(require_admin_read),
) -> ConfigDiagnostics:
    """Read-only configuration diagnostics (R10): env, providers, flags,
    kill-switches, grace period, versions — secrets as presence booleans only.

    This is a Sensitive_Endpoint (config diagnostics), so the access is audited
    ``admin.config_viewed`` before the payload is returned (R15.3). Per R15.9 the
    audit write is strict: if recording the access fails, the endpoint surfaces
    an error and does NOT return the configuration (the access is only legitimate
    when it is traceable). The endpoint performs no mutation (R10.3 / 21.7).
    """
    diagnostics = get_config_service().diagnostics()

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
    """AI analytics: allowlisted call aggregates + provider breakdown + cost (R4).

    An O(1) read (Req 4.9) served from the durable ``AI_*`` ``metrics_daily`` keys
    (via the shared Metric_Store) plus the current in-process accumulator so
    today's not-yet-flushed activity is included. ``window`` is validated to the
    inclusive 1–365 range (default 30, Req 4.3); an out-of-range value is rejected
    with a 422 by the framework — an authz-independent request validation.
    ``require_admin_read`` enforces the kill-switch, authN (401), status recheck +
    capability (403), and the per-admin rate limit (429) before any data is read
    (Req 4.4 / 15.1). Success + failure rates always sum to 1.0 when calls>0 and
    are both 0.0 when calls==0 (Req 4.6/4.7); cost is truncated whole dollars
    (Req 4.5). The response is the allowlisted, secret-free :class:`AiAnalytics`.
    """
    return await get_ai_metrics_service().analytics(window)


@router.get("/errors", response_model=ErrorsSummary)
async def get_admin_errors(
    window: int = Query(30),
    _admin: Principal = Depends(require_admin_read),
) -> ErrorsSummary:
    """Errors summary: grouped 4xx/5xx counts + by-source + trend (R5).

    An O(1) read (Req 5.7) served from durable ``metrics_daily`` keys via the
    shared Metric_Store — grouped buckets only, never a raw log/stack/trace/
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

    An O(1) read (Req 6.6) served entirely from aggregates the backend already
    produces — one in-process ``AdminMetrics`` snapshot (route-class latency +
    cache ratio) plus a fixed handful of KV job-run marker reads — never a row
    scan and never new instrumentation (Req 21.4). No query params.
    ``require_admin_read`` enforces the kill-switch, authN (401), status recheck +
    capability (403), and the per-admin rate limit (429) before any data is read
    (Req 15.1).

    ``response_model_exclude_none=True`` drops every ``None`` field from the
    payload — this is the Req 6.5 omission mechanism for the optional host
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
    """Storage panel: cached DB size + object storage + counts + growth (R7).

    An O(1) read (Req 7.5) served entirely from cached/pre-aggregated values the
    Rollup_Job already produced — the ``DB_SIZE_BYTES`` daily series (a bounded
    30-day read), the ``db_size_last_sample`` freshness marker, and the named
    ``"storage"`` snapshot (counts + object-storage usage). It NEVER issues a
    live storage-size or object-enumeration query on the request path
    (Req 7.4/21.5): the DB-size query and the disk walk both live in the job's
    rollup steps, not here. No query params.

    Stale/unavailable markers surface a last-cached value rather than erroring:
    ``dbSizeStale`` when the last successful sample is missing or too old
    (Req 7.6), ``objectStorageStale`` when the storage snapshot is missing/old or
    the provider was unavailable at sample time (Req 7.7), and the growth figure
    is reported unavailable when fewer than two daily samples exist (Req 7.8).
    ``require_admin_read`` enforces the kill-switch, authN (401), status recheck +
    capability (403), and the per-admin rate limit (429) before any data is read
    (Req 15.1).
    """
    return await get_storage_metrics_service().panel()


@router.get("/security", response_model=SecurityView)
async def get_admin_security(
    _admin: Principal = Depends(require_admin_read),
) -> SecurityView:
    """Security view: trailing-24h security counts from audit aggregates (R9).

    An O(1) read (Req 9.7) served EXCLUSIVELY from the durable ``SEC_*``
    ``metrics_daily`` keys (via the shared Metric_Store) — failed logins, admin
    logins, authz denials, rate-limit hits, and suspicious/blocked requests over
    a trailing-24h window (approximated as the last two UTC days of daily
    aggregates; see :class:`~app.admin.security_metrics.SecurityMetricsService`).
    It NEVER scans ``audit_log`` on the request path (Req 9.6): the day-bounded
    audit scan that produces these aggregates lives only in the Rollup_Job's
    ``SecurityAggregateStep``. Missing aggregates read as ``0`` with no fallback
    to raw rows (Req 9.5). No query params. ``require_admin_read`` enforces the
    kill-switch, authN (401), status recheck + capability (403), and the
    per-admin rate limit (429) before any data is read (Req 9.4 / 15.1).
    """
    return await get_security_metrics_service().view()


@router.get("/kpis", response_model=OverviewKpis)
async def get_admin_kpis(_admin: Principal = Depends(require_admin_read)) -> OverviewKpis:
    """Overview KPI cards: totals + today's signups/AI calls + 24h error rate +
    purge backlog (R13).

    An O(1) read served from the ``_TOTALS_DAY`` snapshot, durable ``AI_CALLS`` /
    ``REQUEST_*`` keys, a single day-bounded live signups count, and the in-process
    purge-backlog gauge — never a full-table scan. Each KPI is computed in
    isolation, so a source that cannot be computed is returned as an explicit
    ``unavailable`` card while the rest still return (Req 13.7). All day/window
    boundaries are UTC (Req 13.3). ``require_admin_read`` enforces the kill-switch,
    authN (401), status recheck + capability (403), and the per-admin rate limit
    (429) (Req 15.1).
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
    tracked feature — aggregate totals only, no user-level data (Req 16.6).

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
    """Resume analytics: source split, top templates, growth series (R14).

    An O(1) read (Req 14.5) served from the pre-computed ``"resume_snapshot"``
    KV blob (source counts + popular templates) plus zero-filled daily growth
    from the four ``RESUMES_*`` durable keys via the shared Metric_Store.

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

    # Audit the sensitive cross-user read (R5.3) — traceable admin access.
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
            # Both fields → single atomic transaction (no partial apply, M2 fix).
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
# Maintenance actions (the ONLY writes here beyond user lifecycle) — Req 18
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
