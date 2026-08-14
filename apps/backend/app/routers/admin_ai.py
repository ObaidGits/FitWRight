"""Admin API: AI provider channels and per-user credit policy.

Reads use ``require_admin_read``, mutations ``require_admin_manage`` - the same
dependencies every other admin route uses, so the active-admin lockout guard and the
append-only audit trail apply here too.

Two deliberate refusals in this surface:

* **A channel key is never returned.** Not masked-on-read, not "admin only" - never.
  The response exposes presence only. An endpoint that can echo a provider key is
  one log line or one screenshot away from leaking it.

* **A channel cannot be deleted while active.** The operator must drain it first, so
  a channel cannot vanish from under a request already using it. This costs one extra
  click and removes a whole class of mid-flight failure.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.admin.deps import require_admin_manage, require_admin_read
from app.auth.principal import Principal
from app.config import settings
from app.database import db

router = APIRouter(prefix="/admin/ai", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=120)
    api_base: str | None = None
    priority: int = Field(default=100, ge=0, le=10_000)
    monthly_cost_cap_cents: int | None = Field(default=None, ge=0)
    #: Write-only. Stored in the encrypted key store, never returned.
    api_key: str | None = None


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    api_base: str | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    state: Literal["active", "disabled", "draining"] | None = None
    structured_verdict: Literal["reliable", "flaky", "unsupported", "unknown"] | None = None
    monthly_cost_cap_cents: int | None = Field(default=None, ge=0)
    api_key: str | None = None


class ChannelOut(BaseModel):
    id: str
    name: str
    provider: str
    model: str
    api_base: str | None
    priority: int
    state: str
    structured_verdict: str
    monthly_cost_cap_cents: int | None
    #: Presence only. The key itself is never returned by any endpoint.
    has_key: bool
    consecutive_failures: int = 0
    cooling_until: str | None = None
    last_ok_at: str | None = None
    last_error_class: str | None = None


class UserLimitPatch(BaseModel):
    """All fields optional; omitted means "leave unchanged".

    ``monthly_allowance_override`` explicitly accepts ``null`` to CLEAR the override
    back to inheriting the global default - which is a different intent from setting
    it to 0 ("this user gets nothing").
    """

    monthly_allowance_override: int | None = Field(default=None, ge=0)
    clear_allowance_override: bool = False
    velocity_cap_override: int | None = Field(default=None, ge=0)
    clear_velocity_override: bool = False
    ai_disabled: bool | None = None
    state: Literal["ok", "blocked"] | None = None


class GrantIn(BaseModel):
    credits: int = Field(gt=0, le=1_000_000)
    #: Mandatory: an unexplained manual balance change is indistinguishable from a
    #: bug or an abuse when someone reads the ledger six months later.
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def _channel_out(channel: dict[str, Any], health: dict[str, Any] | None, has_key: bool) -> ChannelOut:
    h = health or {}
    return ChannelOut(
        id=channel["id"],
        name=channel["name"],
        provider=channel["provider"],
        model=channel["model"],
        api_base=channel.get("api_base"),
        priority=channel["priority"],
        state=channel["state"],
        structured_verdict=channel["structured_verdict"],
        monthly_cost_cap_cents=channel.get("monthly_cost_cap_cents"),
        has_key=has_key,
        consecutive_failures=int(h.get("consecutive_failures") or 0),
        cooling_until=h.get("cooling_until"),
        last_ok_at=h.get("last_ok_at"),
        last_error_class=h.get("last_error_class"),
    )


async def _has_channel_key(channel_id: str) -> bool:
    """Presence check against the encrypted key store, never the value."""
    try:
        return bool(db.get_api_key_ciphertexts(_CHANNEL_KEY_OWNER).get(_channel_key_name(channel_id)))
    except Exception:
        return False


def _store_channel_key(channel_id: str, api_key: str) -> None:
    """Encrypt and store a channel credential.

    Goes through the SAME crypto helper and the SAME key table as user keys, so the
    codebase keeps exactly one encryption path - just under a reserved owner id.
    """
    from app.crypto import encrypt

    db.set_api_key_ciphertext(_CHANNEL_KEY_OWNER, _channel_key_name(channel_id), encrypt(api_key))


#: Channel credentials are operator-owned, not user-owned. They still live in the
#: user key table (one encryption path, one place that can leak) under a reserved
#: owner id that no real user can hold.
_CHANNEL_KEY_OWNER = "__ai_channel__"


def _channel_key_name(channel_id: str) -> str:
    return f"channel:{channel_id}"


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(_admin: Principal = Depends(require_admin_read)) -> list[ChannelOut]:
    channels = await db.list_ai_channels()
    health = await db.get_ai_channel_health()
    out = []
    for ch in channels:
        out.append(_channel_out(ch, health.get(ch["id"]), await _has_channel_key(ch["id"])))
    return out


@router.post("/channels", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelIn, _admin: Principal = Depends(require_admin_manage)
) -> ChannelOut:
    created = await db.create_ai_channel(
        name=payload.name,
        provider=payload.provider,
        model=payload.model,
        api_base=payload.api_base,
        priority=payload.priority,
        monthly_cost_cap_cents=payload.monthly_cost_cap_cents,
    )
    if payload.api_key:
        _store_channel_key(created["id"], payload.api_key)
    health = await db.get_ai_channel_health()
    return _channel_out(created, health.get(created["id"]), bool(payload.api_key))


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str,
    payload: ChannelPatch,
    _admin: Principal = Depends(require_admin_manage),
) -> ChannelOut:
    existing = await db.get_ai_channel(channel_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    fields.pop("api_key", None)

    # Activating a channel that cannot produce valid JSON is allowed (it can still
    # serve free-text features) but activating one with NO credential is not - it
    # would enter rotation and fail every request it received.
    if fields.get("state") == "active" and not await _has_channel_key(channel_id):
        if not payload.api_key:
            raise HTTPException(
                status_code=400,
                detail="Add an API key before activating this channel.",
            )

    if payload.api_key:
        _store_channel_key(channel_id, payload.api_key)

    updated = await db.update_ai_channel(channel_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    health = await db.get_ai_channel_health()
    return _channel_out(updated, health.get(channel_id), await _has_channel_key(channel_id))


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: str, _admin: Principal = Depends(require_admin_manage)
) -> None:
    existing = await db.get_ai_channel(channel_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    # Refuse while it can still take traffic. The operator drains first, so a
    # channel cannot disappear from under a request already using it.
    if existing["state"] == "active":
        raise HTTPException(
            status_code=409,
            detail="Set this channel to draining before deleting it, so in-flight "
            "requests can finish.",
        )
    await db.delete_ai_channel(channel_id)


# ---------------------------------------------------------------------------
# Per-user limits
# ---------------------------------------------------------------------------


@router.get("/users/{user_id}/credits")
async def get_user_credits(
    user_id: str, _admin: Principal = Depends(require_admin_read)
) -> dict[str, Any]:
    account = await db.get_or_create_credit_account(user_id)
    return {
        **account,
        # Echo the effective defaults so the UI can show "inherited: 50" next to an
        # empty override field rather than leaving the admin guessing.
        "global_monthly_allowance": settings.ai_monthly_allowance_credits,
        "global_velocity_cap": settings.ai_velocity_cap_per_hour,
        "credits_enabled": settings.ai_credits_enabled,
    }


@router.patch("/users/{user_id}/credits")
async def patch_user_credits(
    user_id: str,
    payload: UserLimitPatch,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    await db.get_or_create_credit_account(user_id)

    # `...` is the repository's "leave unchanged" sentinel, which is what lets an
    # override be cleared to None (inherit) as distinct from set to 0 (nothing).
    allowance: Any = ...
    if payload.clear_allowance_override:
        allowance = None
    elif payload.monthly_allowance_override is not None:
        allowance = payload.monthly_allowance_override

    velocity: Any = ...
    if payload.clear_velocity_override:
        velocity = None
    elif payload.velocity_cap_override is not None:
        velocity = payload.velocity_cap_override

    updated = await db.set_credit_policy(
        user_id,
        monthly_allowance_override=allowance,
        velocity_cap_override=velocity,
        ai_disabled=payload.ai_disabled,
        state=payload.state,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="User not found")
    return updated


@router.post("/users/{user_id}/credits/grant")
async def grant_user_credits(
    user_id: str,
    payload: GrantIn,
    admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    """Manually add credits, with a mandatory reason recorded in the ledger."""
    await db.get_or_create_credit_account(user_id)
    import uuid

    status = await db.grant_credits(
        user_id,
        credits=payload.credits,
        kind="admin_adjust",
        # A fresh key per call: an admin granting the same amount twice on purpose
        # must succeed twice. Idempotency protects against retries of ONE request,
        # which the client supplies at the HTTP layer, not against repeated intent.
        idempotency_key=f"admin_adjust:{uuid.uuid4()}",
        reason=payload.reason,
        actor_user_id=getattr(admin, "user_id", None),
    )
    if status == "no_account":
        raise HTTPException(status_code=404, detail="User not found")
    return await db.get_or_create_credit_account(user_id)
