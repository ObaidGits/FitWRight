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
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Request, UploadFile

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
    set_avatar,
    set_email,
    update_name,
    update_profile,
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
from app.schemas.profile import (
    AvatarResponse,
    ProfileLink,
    ProfileResponse,
    ProfileUpdateRequest,
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


@router.get("/me/profile", response_model=ProfileResponse)
async def get_profile(principal: Principal = Depends(require_session)) -> ProfileResponse:
    """Return the caller's extended profile (R14.1)."""
    record = await get_by_id(principal.user_id)
    if record is None:  # pragma: no cover - live session implies live user
        raise ApiError(401, "unauthorized", "Account not found.")
    return ProfileResponse(
        headline=record.headline,
        location=record.location,
        links=[ProfileLink(**link) for link in (record.links or [])],
        avatar_url=record.avatar_url,
    )


@router.patch("/me/profile", response_model=ProfileResponse)
async def update_my_profile(
    payload: ProfileUpdateRequest,
    principal: Principal = Depends(require_session),
) -> ProfileResponse:
    """Update the caller's reusable profile fields (validated) — R14.1."""
    links = [link.model_dump() for link in payload.links] if payload.links is not None else None
    record = await update_profile(
        principal.user_id,
        headline=(payload.headline or None),
        location=(payload.location or None),
        links=links,
    )
    if record is None:  # pragma: no cover
        raise ApiError(401, "unauthorized", "Account not found.")
    return ProfileResponse(
        headline=record.headline,
        location=record.location,
        links=[ProfileLink(**link) for link in (record.links or [])],
        avatar_url=record.avatar_url,
    )


@router.post("/me/avatar", response_model=AvatarResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    principal: Principal = Depends(require_session),
) -> AvatarResponse:
    """Upload → canonical master → store → set url+metadata (Photo System).

    Pipeline (``app.storage.image``): magic-byte sniff (no SVG/polyglot), byte +
    pixel caps, decompression-bomb guard, EXIF-orientation normalize, EXIF/GPS
    strip, aspect-ratio-preserving downscale, canonical WebP re-encode. The
    original is never mutated; all crops/shapes/responsive variants are derived
    later. Content-addressed **dedup**: if the same bytes were already stored we
    skip the CDN write. The url is set only *after* a successful store; the
    replaced object is garbage-collected. Never trusts the client MIME/extension.
    """
    from app.storage.image import ImageError, process_profile_image
    from app.storage.provider import get_storage_provider
    from app.productivity.metrics import get_productivity_metrics

    metrics = get_productivity_metrics()
    raw = await file.read()
    try:
        processed = process_profile_image(raw)
    except ImageError:
        # Opaque: the precise reason (bad magic, bomb, dimensions) stays server-side.
        metrics.avatar_upload("rejected")
        raise ApiError(
            422,
            "invalid_file",
            "That image couldn't be processed. Use a JPEG, PNG, WebP, AVIF, or HEIC photo.",
        )

    # Content-addressed dedup: identical bytes → reuse the current master, no
    # wasted CDN write (ABSOLUTE RULE: never duplicate image storage).
    current = await get_by_id(principal.user_id)
    if current and current.avatar_checksum == processed.checksum and current.avatar_url:
        metrics.avatar_upload("ok")
        return _avatar_response(current, deduplicated=True)

    key = f"{principal.user_id}/{uuid4().hex}.{processed.ext}"
    try:
        url = await get_storage_provider().put(
            key, processed.data, content_type=processed.content_type
        )
    except Exception as exc:  # storage outage → clean failure, no dangling url
        logger.error("Avatar storage failed: %s", exc)
        raise ApiError(503, "storage_unavailable", "Couldn't save your photo right now. Try again.")

    record, old_key = await set_avatar(
        principal.user_id,
        avatar_url=url,
        avatar_key=key,
        metadata={
            "width": processed.width,
            "height": processed.height,
            "checksum": processed.checksum,
            "format": processed.source_format,
            "byte_size": processed.byte_size,
            "dominant_color": processed.dominant_color,
        },
    )
    if record is None:  # pragma: no cover
        raise ApiError(401, "unauthorized", "Account not found.")
    # Garbage-collect the replaced object (best-effort; retention also sweeps).
    if old_key and old_key != key:
        try:
            await get_storage_provider().delete(old_key)
        except Exception:  # pragma: no cover
            logger.debug("Old avatar GC failed for %s", old_key, exc_info=True)
    metrics.avatar_upload("ok")
    return _avatar_response(record)


@router.delete("/me/avatar", response_model=AvatarResponse)
async def delete_avatar(
    principal: Principal = Depends(require_session),
) -> AvatarResponse:
    """Remove the caller's profile photo and GC the stored master (Photo System).

    Resumes that pinned a snapshot keep rendering (their frozen URL is unaffected);
    resumes tracking the canonical photo fall back to their no-photo layout.
    """
    from app.auth.accounts import clear_avatar
    from app.storage.provider import get_storage_provider

    record, old_key = await clear_avatar(principal.user_id)
    if record is None:  # pragma: no cover - live session implies live user
        raise ApiError(401, "unauthorized", "Account not found.")
    if old_key:
        try:
            await get_storage_provider().delete(old_key)
        except Exception:  # pragma: no cover - best-effort; retention also sweeps
            logger.debug("Avatar GC failed for %s", old_key, exc_info=True)
    return _avatar_response(record)


def _avatar_response(record, *, deduplicated: bool = False) -> AvatarResponse:
    """Build the :class:`AvatarResponse` from an account record + its metadata."""
    aspect = None
    if record.avatar_width and record.avatar_height:
        aspect = round(record.avatar_width / record.avatar_height, 4)
    return AvatarResponse(
        avatar_url=record.avatar_url,
        width=record.avatar_width,
        height=record.avatar_height,
        aspect_ratio=aspect,
        dominant_color=record.avatar_dominant_color,
        format=record.avatar_format,
        byte_size=record.avatar_bytes,
        checksum=record.avatar_checksum,
        deduplicated=deduplicated,
    )


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
