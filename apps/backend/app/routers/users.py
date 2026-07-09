"""Profile + device-management endpoints (Task 4.2).

- ``GET /users/me`` — the caller's ``SafeUser``.
- ``PATCH /users/me`` — update the display name only; ``role``/``status`` are
  ignored (R7.2, R8.4). Optional optimistic-concurrency via ``updated_at`` → 409.
- ``GET /users/me/sessions`` — active sessions for device management, never the
  raw token (R3.5).
- ``DELETE /users/me/sessions/{id}`` — revoke one of the caller's sessions;
  CSRF-protected; a session id that isn't the caller's returns 404 (no
  cross-user disclosure, R10.3).

Only :class:`~app.schemas.auth.SafeUser` is ever returned for a user (R7.5).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.auth import (
    Principal,
    get_email_sender,
    get_session_service,
    get_token_service,
)
from app.auth.accounts import (
    EmailInUseError,
    email_exists,
    get_by_id,
    normalize_email,
    set_email,
    update_name,
)
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.email import build_email_change_email
from app.config import settings
from app.errors import ApiError
from app.routers._auth_deps import (
    client_ip,
    require_session,
    require_stepped_up_session,
)
from app.schemas.auth import (
    ChangeEmailRequest,
    EmailChangeConfirmRequest,
    MessageResponse,
    SafeUser,
    SessionListResponse,
    SessionSummary,
    UpdateProfileRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


def _safe_user(record, *, aal: str) -> SafeUser:
    return SafeUser.build(
        id=record.id,
        name=record.name,
        email=record.email,
        role=record.role,
        status=record.status,
        email_verified=record.email_verified,
        aal=aal,
        avatar_url=record.avatar_url,
    )


@router.get("/me", response_model=SafeUser)
async def get_me(principal: Principal = Depends(require_session)) -> SafeUser:
    """Return the authenticated user's ``SafeUser``."""
    record = await get_by_id(principal.user_id)
    if record is None:  # pragma: no cover - a live session implies a live user
        raise ApiError(401, "unauthorized", "Account not found.")
    return _safe_user(record, aal=principal.aal)


@router.patch("/me", response_model=SafeUser)
async def update_me(
    payload: UpdateProfileRequest,
    principal: Principal = Depends(require_session),
) -> SafeUser:
    """Update the display name only (role/status are never changed here)."""
    outcome, record = await update_name(
        principal.user_id, payload.name, expected_updated_at=payload.updated_at
    )
    if outcome == "not_found":  # pragma: no cover - live session implies live user
        raise ApiError(401, "unauthorized", "Account not found.")
    if outcome == "conflict":
        raise ApiError(
            409,
            "conflict",
            "The profile was modified elsewhere. Reload and try again.",
        )
    assert record is not None
    return _safe_user(record, aal=principal.aal)


@router.get("/me/sessions", response_model=SessionListResponse)
async def list_sessions(
    principal: Principal = Depends(require_session),
) -> SessionListResponse:
    """List the caller's active sessions for device management (R3.5)."""
    sessions = await get_session_service().list_active_sessions(principal.user_id)
    return SessionListResponse(
        sessions=[
            SessionSummary(
                id=s.id,
                deviceLabel=s.device_label,
                ipHash=s.ip_hash,
                createdAt=s.created_at,
                lastSeenAt=s.last_seen_at,
                current=s.id == principal.session_id,
            )
            for s in sessions
        ]
    )


@router.delete("/me/sessions/{session_id}", response_model=None, status_code=204)
async def revoke_session(
    session_id: str,
    principal: Principal = Depends(require_session),
) -> None:
    """Revoke one of the caller's sessions; a foreign id returns 404 (R10.3)."""
    session_service = get_session_service()
    # Ownership check first: only the caller's own sessions are visible, so a
    # session belonging to someone else is indistinguishable from a missing one.
    owned = {s.id for s in await session_service.list_active_sessions(principal.user_id)}
    if session_id not in owned:
        raise ApiError(404, "not_found", "Session not found.")
    await session_service.revoke_session(session_id)
    await get_audit_service().record(
        AuditEvent.SESSION_REVOKED,
        actor_user_id=principal.user_id,
        # Key avoids the audit sanitizer's "session" secret-marker so the revoked
        # session id (an opaque row id, not a token) is retained for the trail.
        meta={"sid": session_id},
    )


# ---------------------------------------------------------------------------
# POST /users/me/email  (begin a verify-before-switch email change)
# ---------------------------------------------------------------------------


@router.post("/me/email", response_model=MessageResponse)
async def begin_email_change(
    request: Request,
    payload: ChangeEmailRequest,
    principal: Principal = Depends(require_stepped_up_session),
) -> MessageResponse:
    """Begin an email change: verify the *new* address before switching (R7.4).

    Requires a recent step-up (``require_stepped_up_session`` → 401
    ``step_up_required`` otherwise, Property 6). Enforces uniqueness up front (a
    new address already registered to any account → 409 ``email_unavailable``),
    then issues a hashed single-use token to the **new** address and emails a
    confirmation link. The account's primary ``email`` is **not** touched here —
    it only switches once the link is confirmed (``POST /users/me/email/confirm``),
    so the account never moves to an unverified address.
    """
    new_email = normalize_email(payload.email)

    record = await get_by_id(principal.user_id)
    if record is None:  # pragma: no cover - a live session implies a live user
        raise ApiError(401, "unauthorized", "Account not found.")

    # No-op change to the current address is pointless; treat as unavailable so
    # the caller gets a clear signal without a spurious confirmation email.
    if new_email == normalize_email(record.email):
        raise ApiError(409, "email_unavailable", "That email is unavailable.")

    # Uniqueness pre-check (the authoritative guard is the unique index enforced
    # at confirm time). This endpoint requires auth + step-up, so it is not an
    # anonymous enumeration vector.
    if await email_exists(new_email):
        raise ApiError(409, "email_unavailable", "That email is unavailable.")

    raw_token = await get_token_service().issue_email_change(principal.user_id, new_email)
    await get_email_sender().send(
        build_email_change_email(
            to=new_email,
            raw_token=raw_token,
            base_url=settings.frontend_base_url,
        )
    )
    return MessageResponse(message="Check your new email address to confirm the change.")


# ---------------------------------------------------------------------------
# POST /users/me/email/confirm  (redeem the email-change token → switch)
# ---------------------------------------------------------------------------


@router.post("/me/email/confirm", response_model=SafeUser)
async def confirm_email_change(
    request: Request,
    payload: EmailChangeConfirmRequest,
) -> SafeUser:
    """Confirm a pending email change, switching the primary email (R7.4).

    The token was delivered to the new address, so redeeming it proves ownership
    of that address (verify-before-switch). It is token-only (no session needed —
    the link may be opened on the device holding the new mailbox) and single-use:
    a missing/used/expired token collapses to one generic ``invalid_token``. On
    success the account's primary ``email`` switches to the confirmed address
    (uniqueness re-checked at the DB layer — a lost race → 409
    ``email_unavailable``) and the change is audited as ``email_changed``.
    """
    result = await get_token_service().consume_email_change(payload.token)
    if not result.ok or result.user_id is None or result.new_email is None:
        raise ApiError(400, "invalid_token", "This confirmation link is invalid or has expired.")

    try:
        record = await set_email(result.user_id, result.new_email)
    except EmailInUseError:
        # Another account claimed the address between request and confirm.
        raise ApiError(409, "email_unavailable", "That email is unavailable.")

    if record is None:  # pragma: no cover - token FK implies a live user
        raise ApiError(400, "invalid_token", "This confirmation link is invalid or has expired.")

    await get_audit_service().record(
        AuditEvent.EMAIL_CHANGED,
        actor_user_id=record.id,
        ip_hash=get_session_service().hash_ip(client_ip(request)),
    )
    return _safe_user(record, aal="aal1")
