"""MCP access-token management (browser-authenticated).

Creating a token is the act of granting a non-browser client full access to
the user's FitWright data, so these routes require a verified session (and,
for creation, a recent step-up re-auth), and sit behind the same MCP_ENABLED
kill-switch as the mount itself. The raw token is returned exactly once at
creation - the DB keeps only sha256.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.auth import Principal, get_optional_principal, require_verified_user_id
from app.auth.audit import AuditEvent, get_audit_service
from app.auth.mcp_tokens import (
    McpTokenLimitError,
    get_mcp_token_service,
)
from app.config import Settings, settings
from app.errors import ApiError
from app.routers._auth_deps import require_stepped_up_session


class TokenCreateRequest(BaseModel):
    """Token-creation payload. ``label`` is the client's display name."""

    label: str = Field(min_length=1, max_length=100,
                       description="Client name, e.g. 'Claude Desktop'")
    # None = fall back to MCP_TOKEN_TTL_DAYS (0 = no expiry, the documented
    # default). Bounded to 10 years so a typo cannot mint an immortal token
    # when the deployment default was meant to apply.
    ttl_days: int | None = Field(default=None, ge=1, le=3650)


def _require_mcp_enabled(config: Settings = Depends(lambda: settings)) -> None:
    """Kill-switch gate for the whole router (pattern: require_extension_enabled).

    Runs as a router-level dependency, so a disabled deployment 404s the whole
    surface rather than leaking its shape.
    """
    if not config.mcp_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


def _step_up_for_token_mint(request: Request) -> Principal | None:
    """F2: minting requires a recent step-up - with the local carve-out.

    Hosted: :func:`require_stepped_up_session` verbatim (401
    ``step_up_required`` without a recent re-auth; 401 ``unauthorized`` for an
    anonymous caller).

    Single-user local mode: there are no sessions at all (implicit owner,
    zero-config), so there is no hijackable session to re-auth against - the
    same carve-out ``require_verified_user_id`` applies. Without it, local
    deployments could never mint the token the docs tell them to configure.
    """
    principal = get_optional_principal(request)
    if principal is None:
        if settings.single_user_mode:
            return None
        raise ApiError(401, "unauthorized", "Authentication required")
    return require_stepped_up_session(principal=principal)


router = APIRouter(
    prefix="/mcp/tokens",
    tags=["MCP"],
    dependencies=[Depends(_require_mcp_enabled)],
)


@router.post("", status_code=201)
async def create_token(
    body: TokenCreateRequest,
    stepped_up: Principal | None = Depends(_step_up_for_token_mint),
    user_id: str = Depends(require_verified_user_id),
) -> dict:
    """Mint a token. The raw value appears in this response and nowhere else.

    Requires a recent step-up (sudo window, R9.1): minting a long-lived
    credential that bypasses browser auth is at least as sensitive as a
    password change, so a merely-hijacked session cannot do it (401
    ``step_up_required`` without a recent re-auth).
    """
    label = body.label.strip()
    if not label:
        raise ApiError(
            400, "invalid_label", "Label must contain at least one non-whitespace character."
        )
    svc = get_mcp_token_service()
    ttl = body.ttl_days if body.ttl_days is not None else settings.mcp_token_ttl_days
    try:
        rec, raw = await svc.issue(user_id, label, ttl_days=ttl)
    except McpTokenLimitError as exc:
        raise ApiError(409, "token_limit_reached", str(exc)) from None
    # Meta keys are deliberately ``id``/``label``: sanitize_meta drops any key
    # containing "token" wholesale, and no fragment of the raw secret belongs
    # in the audit trail anyway (only sha256 is ever persisted elsewhere).
    await get_audit_service().record(
        AuditEvent.MCP_TOKEN_CREATED, actor_user_id=user_id,
        meta={"id": rec["id"], "label": label},
    )
    return {"token": raw, **rec}


@router.get("")
async def list_tokens(user_id: str = Depends(require_verified_user_id)) -> dict:
    """The caller's tokens, masked (token_hash is never included)."""
    return {"items": await get_mcp_token_service().list_for_user(user_id)}


@router.delete("/{token_id}")
async def revoke_token(token_id: str, user_id: str = Depends(require_verified_user_id)) -> dict:
    """Owner-scoped revoke. Another user's (or already-revoked) id -> 404."""
    revoked = await get_mcp_token_service().revoke(user_id, token_id)
    if not revoked:
        # Indistinguishable from a nonexistent id: a foreign token_id must not
        # confirm or deny existence to another user.
        raise ApiError(404, "not_found", "No such active token.")
    await get_audit_service().record(
        AuditEvent.MCP_TOKEN_REVOKED, actor_user_id=user_id, meta={"id": token_id}
    )
    return {"revoked": True}
