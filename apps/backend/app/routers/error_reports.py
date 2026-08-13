"""Authenticated privacy-safe user error-report and admin read endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from app.admin.cursor import CursorError
from app.admin.deps import require_admin_read
from app.admin.error_reports_service import AdminErrorReportData, list_error_reports_page
from app.auth import Principal, get_principal, require_verified_user_id
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.auth.sessions import get_session_service
from app.database import db
from app.error_reports.schemas import (
    AdminErrorReport,
    AdminErrorReportList,
    ErrorReportCreate,
    ErrorReportCreated,
    ErrorReportUser,
)
from app.errors import ApiError
from app.routers._auth_deps import client_ip

logger = logging.getLogger(__name__)
router = APIRouter(tags=["error-reports"])

_BURST_RULE = RateLimitRule(limit=5, window_seconds=60)
_HOURLY_RULE = RateLimitRule(limit=30, window_seconds=3600)


async def _require_verified_session_principal(
    request: Request, principal: Principal = Depends(get_principal)
) -> Principal:
    """Require the standard verified gate while sourcing identity only from session."""
    verified_user_id = await require_verified_user_id(request)
    if verified_user_id != principal.user_id:  # defensive composition invariant
        raise ApiError(403, "forbidden", "This action is not permitted.")
    return principal


async def _enforce_user_rate_limit(user_id: str) -> None:
    """Apply two-tier per-user intake limiting after authentication."""
    limiter = get_rate_limiter()
    for bucket, rule in (
        ("user_error_reports_burst", _BURST_RULE),
        ("user_error_reports_hourly", _HOURLY_RULE),
    ):
        result = await limiter.check(bucket, f"user:{user_id}", rule, fail_closed=False)
        if result.fail_closed:
            logger.warning("Error-report rate limiter degraded for bucket=%s", bucket)
        if not result.allowed:
            raise ApiError(
                429,
                "rate_limited",
                "Too many error reports. Please wait before trying again.",
                headers={"Retry-After": str(result.retry_after or rule.window_seconds)},
            )


def _admin_report(row: AdminErrorReportData) -> AdminErrorReport:
    return AdminErrorReport(
        id=row.id,
        userId=row.user_id,
        clientReportId=row.client_report_id,
        issueType=row.issue_type,
        message=row.message,
        errorCode=row.error_code,
        httpStatus=row.http_status,
        retryable=row.retryable,
        apiMethod=row.api_method,
        apiRoute=row.api_route,
        operationRequestId=row.operation_request_id,
        apiRequestId=row.api_request_id,
        pipelineStage=row.pipeline_stage,
        streamPhase=row.stream_phase,
        fallbackSafe=row.fallback_safe,
        createdAt=row.created_at,
        user=ErrorReportUser(
            id=row.user_id,
            name=row.user_name,
            email=row.user_email,
        ),
    )


@router.post("/error-reports/", response_model=ErrorReportCreated)
async def create_error_report(
    payload: ErrorReportCreate,
    principal: Principal = Depends(_require_verified_session_principal),
) -> ErrorReportCreated:
    """Persist bounded metadata; duplicate client ids return the original report."""
    await _enforce_user_rate_limit(principal.user_id)
    row = await db.create_user_error_report(
        principal.user_id,
        payload.model_dump(),
    )
    return ErrorReportCreated(reportId=row["id"], createdAt=row["created_at"])


@router.get("/admin/error-reports", response_model=AdminErrorReportList)
async def list_error_reports(
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    admin: Principal = Depends(require_admin_read),
) -> AdminErrorReportList:
    """Return a newest-first audited page of reports across users."""
    try:
        rows, next_cursor = await list_error_reports_page(cursor=cursor, limit=limit)
    except CursorError:
        raise ApiError(400, "bad_cursor", "The pagination cursor is invalid.")

    await get_audit_service().record(
        AuditEvent.ADMIN_ERROR_REPORTS_VIEWED,
        actor_user_id=admin.user_id,
        request_id=getattr(request.state, "request_id", None),
        ip_hash=get_session_service().hash_ip(client_ip(request)),
        meta={"path": request.url.path, "item_count": len(rows)},
        raise_on_error=True,
    )
    return AdminErrorReportList(
        items=[_admin_report(row) for row in rows],
        nextCursor=next_cursor,
    )