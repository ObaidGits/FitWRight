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

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.admin.deps import require_admin_manage, require_admin_read
from app.auth.audit import AuditEvent
from app.auth.principal import Principal
from app.config import settings
from app.database import db

logger = logging.getLogger(__name__)

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
    """Presence check only. The value is never read here, and never returned."""
    try:
        return bool(await db.get_ai_channel_key(channel_id))
    except Exception:
        return False


async def _store_channel_key(channel_id: str, api_key: str) -> None:
    """Encrypt and store a channel credential on the channel row (migration 0036).

    Uses the same ``app.crypto`` helper as user keys, so there is still exactly one
    encryption implementation. It does NOT use the ``api_keys`` table: that table's
    user_id is a foreign key to users, so a channel - which has no user - could never
    be stored there. See the 0036 migration for the full account.
    """
    from app.crypto import encrypt

    await db.set_ai_channel_key(channel_id, encrypt(api_key))


async def _audit(
    event: str,
    admin: Principal,
    *,
    target_user_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Record an admin AI action in the SHARED audit trail.

    Deliberately fails soft (the default): an audit write must not break the action it
    observes. The credit ledger is the financial record and is written
    transactionally; this is the accountability record - who did it - and losing one
    row of it is preferable to refusing a legitimate operator action.
    """
    from app.auth.audit import get_audit_service

    try:
        await get_audit_service().record(
            event,
            actor_user_id=admin.user_id,
            target_user_id=target_user_id,
            meta=meta,
        )
    except Exception:  # pragma: no cover - fail-soft by design
        logger.warning("Admin AI action could not be audited: %s", event)


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
        await _store_channel_key(created["id"], payload.api_key)
    await _audit(
        AuditEvent.ADMIN_AI_CHANNEL_CREATED,
        _admin,
        meta={
            "channel_id": created["id"],
            "name": payload.name,
            "provider": payload.provider,
            "model": payload.model,
            # Never the key itself - only whether one was supplied.
            "had_key": bool(payload.api_key),
        },
    )
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
        await _store_channel_key(channel_id, payload.api_key)

    updated = await db.update_ai_channel(channel_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    await _audit(
        AuditEvent.ADMIN_AI_CHANNEL_UPDATED,
        _admin,
        meta={
            "channel_id": channel_id,
            "changed": sorted(fields.keys()),
            "key_replaced": bool(payload.api_key),
        },
    )
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
    await _audit(
        AuditEvent.ADMIN_AI_CHANNEL_DELETED,
        _admin,
        meta={"channel_id": channel_id, "name": existing.get("name")},
    )


# ---------------------------------------------------------------------------
# Per-user limits
# ---------------------------------------------------------------------------


class PackIn(BaseModel):
    """Create or update a pack.

    ``discount_percent`` is a CONVENIENCE, not storage: the operator thinks in
    percentages, and the exact sale price is computed once here and stored as an integer.
    A stored percentage would be re-multiplied on every render and every check, and a
    one-paisa disagreement between the buy screen and the webhook amount check fails a
    purchase for a customer who did nothing wrong.

    If both ``discount_percent`` and ``sale_amount_minor`` are given, the explicit amount
    wins - an operator who typed an exact figure meant it.
    """

    label: str = Field(min_length=1, max_length=80)
    credits: int = Field(gt=0, le=1_000_000)
    amount_minor: int = Field(ge=0, le=100_000_000)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=200)
    active: bool = False
    sort_order: int = 100
    sale_amount_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    sale_label: str | None = Field(default=None, max_length=60)
    sale_starts_at: str | None = None
    sale_ends_at: str | None = None


class PackPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    credits: int | None = Field(default=None, gt=0, le=1_000_000)
    amount_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    description: str | None = Field(default=None, max_length=200)
    active: bool | None = None
    sort_order: int | None = None
    sale_amount_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    sale_label: str | None = Field(default=None, max_length=60)
    sale_starts_at: str | None = None
    sale_ends_at: str | None = None
    #: Explicit, because `None` on the fields above means "leave alone" - there has to be
    #: a way to say "end the offer" that is not ambiguous with "do not touch it".
    clear_sale: bool = False


def _pack_out(pack: dict) -> dict[str, Any]:
    """A pack plus what a customer would be charged for it right now.

    The admin sees the resolved price from the SAME function the buy screen uses, so the
    preview cannot drift from reality - which is the only way to be sure a discount is
    actually live when the panel says it is.
    """
    from app.ai_pack_pricing import effective_offer, percent_off

    offer = effective_offer(pack)
    return {
        **pack,
        "effective_amount_minor": offer.amount_minor,
        "on_sale": offer.on_sale,
        "percent_off": percent_off(pack["amount_minor"], offer.amount_minor),
    }


def _resolve_sale_fields(payload: Any, *, base_amount: int | None) -> dict[str, Any]:
    """Turn a percentage into the stored integer, if that is what was given."""
    from app.ai_pack_pricing import discounted_amount

    fields: dict[str, Any] = {}
    if payload.sale_amount_minor is not None:
        fields["sale_amount_minor"] = payload.sale_amount_minor
    elif payload.discount_percent is not None and base_amount is not None:
        fields["sale_amount_minor"] = discounted_amount(base_amount, payload.discount_percent)
    for key in ("sale_label", "sale_starts_at", "sale_ends_at"):
        value = getattr(payload, key, None)
        if value is not None:
            fields[key] = value
    return fields


@router.get("/packs")
async def list_packs(_admin: Principal = Depends(require_admin_read)) -> list[dict[str, Any]]:
    """Every pack, active or not, each with its live effective price."""
    return [_pack_out(p) for p in await db.list_credit_packs()]


@router.post("/packs/{pack_id}", status_code=201)
async def create_pack(
    pack_id: str,
    payload: PackIn,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    """Create a pack. It starts INACTIVE unless explicitly activated.

    The slug is chosen by the operator and is permanent, because purchase history records
    it and those rows must keep making sense after the pack is edited or withdrawn.
    """
    if await db.get_credit_pack(pack_id) is not None:
        raise HTTPException(status_code=409, detail="A pack with that id already exists.")

    fields: dict[str, Any] = {
        "label": payload.label,
        "credits": payload.credits,
        "amount_minor": payload.amount_minor,
        "currency": payload.currency.upper(),
        "description": payload.description,
        "active": payload.active,
        "sort_order": payload.sort_order,
        **_resolve_sale_fields(payload, base_amount=payload.amount_minor),
    }
    try:
        created = await db.upsert_credit_pack(pack_id, **fields)
    except ValueError as exc:
        # Money validation - a "discount" above the regular price, or a pack with no
        # credits. The operator's own words back, not a stack trace.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        AuditEvent.ADMIN_AI_PACK_CHANGED,
        _admin,
        meta={"pack_id": pack_id, "action": "created", "amount_minor": payload.amount_minor,
              "credits": payload.credits, "active": payload.active},
    )
    return _pack_out(created)


@router.patch("/packs/{pack_id}")
async def update_pack(
    pack_id: str,
    payload: PackPatch,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    existing = await db.get_credit_pack(pack_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Pack not found")

    fields = payload.model_dump(
        exclude_unset=True,
        exclude={"discount_percent", "clear_sale", "sale_amount_minor",
                 "sale_label", "sale_starts_at", "sale_ends_at"},
    )
    fields = {k: v for k, v in fields.items() if v is not None}
    if "currency" in fields:
        fields["currency"] = str(fields["currency"]).upper()

    if payload.clear_sale:
        # Ending an offer clears the whole thing. Leaving a stale label or window behind
        # is how a finished promotion reappears months later.
        fields.update(
            sale_amount_minor=None, sale_label=None, sale_starts_at=None, sale_ends_at=None
        )
    else:
        base = fields.get("amount_minor", existing["amount_minor"])
        fields.update(_resolve_sale_fields(payload, base_amount=base))

    try:
        updated = await db.upsert_credit_pack(pack_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        AuditEvent.ADMIN_AI_PACK_CHANGED,
        _admin,
        meta={"pack_id": pack_id, "action": "updated", "changed": sorted(fields.keys())},
    )
    return _pack_out(updated)


@router.delete("/packs/{pack_id}", status_code=204)
async def delete_pack(
    pack_id: str, _admin: Principal = Depends(require_admin_manage)
) -> None:
    """Delete a pack. Past purchases keep their own recorded price and credits.

    Deactivating is usually the better move and the UI says so - a deleted pack no longer
    appears anywhere, while an inactive one still explains the purchases that reference it.
    """
    if not await db.delete_credit_pack(pack_id):
        raise HTTPException(status_code=404, detail="Pack not found")
    await _audit(
        AuditEvent.ADMIN_AI_PACK_CHANGED, _admin, meta={"pack_id": pack_id, "action": "deleted"}
    )


# ======================================================================
# Feature prices - what each AI action costs the user
# ======================================================================


class FeaturePriceIn(BaseModel):
    """Create or update one feature's price.

    ``is_charged`` is separate from ``credits`` on purpose. Making something free by
    setting its price to zero loses the price you had, and makes "free on purpose"
    indistinguishable from "not filled in yet" - so free is its own switch and the
    number it would otherwise cost is preserved underneath it.
    """

    label: str = Field(min_length=1, max_length=80)
    credits: int = Field(ge=0, le=1_000_000)
    is_charged: bool = True
    active: bool = True
    sort_order: int = 100
    description: str | None = Field(default=None, max_length=200)


class FeaturePricePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    credits: int | None = Field(default=None, ge=0, le=1_000_000)
    is_charged: bool | None = None
    active: bool | None = None
    sort_order: int | None = None
    description: str | None = Field(default=None, max_length=200)


@router.get("/feature-prices")
async def list_feature_prices(
    _admin: Principal = Depends(require_admin_read),
) -> dict[str, Any]:
    """Every priced action, plus the features the code spends against.

    ``unpriced`` matters: a feature the code charges for but which has no row runs on
    the built-in fallback, so it is neither visible nor editable here. Surfacing the
    gap is the difference between an operator who knows their price list is incomplete
    and one who finds out from a margin report.
    """
    from app.ai_feature_prices import DEFAULT_FEATURE_PRICES

    rows = await db.list_feature_prices()
    known = {r["feature"] for r in rows}
    return {
        "prices": rows,
        "unpriced": sorted(f for f in DEFAULT_FEATURE_PRICES if f not in known),
    }


@router.post("/feature-prices/{feature}", status_code=201)
async def create_feature_price(
    feature: str,
    payload: FeaturePriceIn,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    if await db.get_feature_price(feature) is not None:
        raise HTTPException(
            status_code=409, detail="A price for that feature already exists."
        )
    try:
        created = await db.upsert_feature_price(feature, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.ai_feature_prices import invalidate_price_cache

    invalidate_price_cache()
    await _audit(
        AuditEvent.ADMIN_AI_FEATURE_PRICE_CHANGED,
        _admin,
        meta={
            "feature": feature,
            "action": "created",
            "credits": payload.credits,
            "is_charged": payload.is_charged,
        },
    )
    return created


@router.patch("/feature-prices/{feature}")
async def update_feature_price(
    feature: str,
    payload: FeaturePricePatch,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    if await db.get_feature_price(feature) is None:
        raise HTTPException(status_code=404, detail="Feature price not found")

    fields = payload.model_dump(exclude_unset=True)
    fields = {k: v for k, v in fields.items() if v is not None}
    try:
        updated = await db.upsert_feature_price(feature, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from app.ai_feature_prices import invalidate_price_cache

    # Dropped immediately so the operator's next page load reflects the edit. Other
    # workers catch up within the cache TTL - see app/ai_feature_prices.
    invalidate_price_cache()
    await _audit(
        AuditEvent.ADMIN_AI_FEATURE_PRICE_CHANGED,
        _admin,
        meta={"feature": feature, "action": "updated", "changed": sorted(fields.keys())},
    )
    return updated


# ======================================================================
# Subscription plans - the monthly tiers
# ======================================================================


class PlanIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    price_minor: int = Field(default=0, ge=0, le=100_000_000)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    monthly_credits: int = Field(default=0, ge=0, le=10_000_000)
    #: ``None`` = uncapped searches. Deliberately expressible, deliberately not default.
    search_daily_limit: int | None = Field(default=None, ge=0, le=100_000)
    is_default: bool = False
    active: bool = False
    sort_order: int = 100
    description: str | None = Field(default=None, max_length=200)


class PlanPatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    price_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    monthly_credits: int | None = Field(default=None, ge=0, le=10_000_000)
    search_daily_limit: int | None = Field(default=None, ge=0, le=100_000)
    is_default: bool | None = None
    active: bool | None = None
    sort_order: int | None = None
    description: str | None = Field(default=None, max_length=200)
    #: Explicit, because `None` on ``search_daily_limit`` means "leave alone" - there has
    #: to be an unambiguous way to say "remove the cap".
    clear_search_limit: bool = False


@router.get("/plans")
async def list_plans(_admin: Principal = Depends(require_admin_read)) -> list[dict[str, Any]]:
    """Every plan, active or not."""
    return await db.list_subscription_plans()


@router.post("/plans/{plan_id}", status_code=201)
async def create_plan(
    plan_id: str,
    payload: PlanIn,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    """Create a plan. It starts INACTIVE unless explicitly activated.

    The slug is permanent: accounts record it, and those rows must keep resolving after
    the plan is renamed, repriced or withdrawn.
    """
    if await db.get_subscription_plan(plan_id) is not None:
        raise HTTPException(status_code=409, detail="A plan with that id already exists.")

    fields = payload.model_dump()
    fields["currency"] = str(fields["currency"]).upper()
    try:
        created = await db.upsert_subscription_plan(plan_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        AuditEvent.ADMIN_AI_PLAN_CHANGED,
        _admin,
        meta={
            "plan_id": plan_id,
            "action": "created",
            "price_minor": payload.price_minor,
            "monthly_credits": payload.monthly_credits,
            "active": payload.active,
        },
    )
    return created


@router.patch("/plans/{plan_id}")
async def update_plan(
    plan_id: str,
    payload: PlanPatch,
    _admin: Principal = Depends(require_admin_manage),
) -> dict[str, Any]:
    if await db.get_subscription_plan(plan_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    fields = payload.model_dump(exclude_unset=True, exclude={"clear_search_limit"})
    fields = {k: v for k, v in fields.items() if v is not None}
    if "currency" in fields:
        fields["currency"] = str(fields["currency"]).upper()
    if payload.clear_search_limit:
        fields["search_daily_limit"] = None

    try:
        updated = await db.upsert_subscription_plan(plan_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _audit(
        AuditEvent.ADMIN_AI_PLAN_CHANGED,
        _admin,
        meta={"plan_id": plan_id, "action": "updated", "changed": sorted(fields.keys())},
    )
    return updated


@router.delete("/plans/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: str, _admin: Principal = Depends(require_admin_manage)
) -> None:
    """Delete a plan. Accounts on it fall back to the default at read time.

    Deactivating is usually better: a withdrawn-but-present plan still explains the
    accounts that reference it, whereas a deleted one leaves them silently on the
    default tier.
    """
    if not await db.delete_subscription_plan(plan_id):
        raise HTTPException(status_code=404, detail="Plan not found")
    await _audit(
        AuditEvent.ADMIN_AI_PLAN_CHANGED,
        _admin,
        meta={"plan_id": plan_id, "action": "deleted"},
    )


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: str, _admin: Principal = Depends(require_admin_manage)
) -> dict[str, Any]:
    """Send one tiny probe through this channel and report what happened.

    Exists so a wrong credential is discovered HERE rather than by real users. Without
    it the only way to validate a channel is to activate it and watch generations
    fail, which spends the operator's credibility to learn something a 200-token probe
    can tell them.

    It also records the structured-output verdict, because "can this model return
    JSON?" cannot be answered from configuration - some models advertise the
    capability and then emit prose. The verdict gates which features may use the
    channel, so measuring it is the difference between a safe route and one that
    breaks resume parsing in a way that looks like our bug.

    ``require_admin_manage`` rather than read: it spends real provider money, however
    little, and writes the verdict.
    """
    channel = await db.get_ai_channel(channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")

    from app.ai_channel_test import probe_channel

    result = await probe_channel(channel)

    # Persist the verdict so routing can use it, and so the operator sees it on the
    # list without re-testing.
    await db.update_ai_channel(
        channel_id, structured_verdict=result["structured_verdict"]
    )
    await _audit(
        AuditEvent.ADMIN_AI_CHANNEL_TESTED,
        _admin,
        meta={
            "channel_id": channel_id,
            "ok": result["ok"],
            "error_class": result.get("error_class"),
            "structured_verdict": result["structured_verdict"],
        },
    )
    return result


@router.get("/performance")
async def get_channel_performance(
    days: int = 7,
    _actor: str = Depends(require_admin_read),
) -> list[dict[str, Any]]:
    """Success rate and p95 latency per channel (task 5.1).

    Sorted worst-first: the channel you need to know about should not be the one you
    have to scroll to.
    """
    return await db.channel_performance(days=days)


@router.get("/alerts")
async def get_ai_alerts(
    days: int = 7,
    _actor: str = Depends(require_admin_read),
) -> list[dict[str, Any]]:
    """Current AI alert findings (task 5.3). Read-only; nothing is remediated."""
    from app.ai_alerts import evaluate_ai_alerts

    return await evaluate_ai_alerts(days=days)


@router.get("/reconciliation")
async def get_reconciliation(
    _actor: str = Depends(require_admin_read),
) -> dict[str, Any]:
    """Counts that should all be zero (task 5.4).

    Surfaced here rather than only in logs, because an invariant nobody looks at is
    an invariant nobody enforces.
    """
    from app.ai_retention import reconcile_credits

    return await reconcile_credits()


@router.get("/abuse-review")
async def get_abuse_review(
    days: int = 7,
    _actor: str = Depends(require_admin_read),
) -> list[dict[str, Any]]:
    """Accounts worth a HUMAN look (task 6.2).

    Never an automatic block. Every signal here is circumstantial and each entry says
    so, including the innocent explanation - which is usually the true one.
    """
    from app.ai_abuse_signals import abuse_review_candidates

    return await abuse_review_candidates(days=days)


@router.get("/spend")
async def get_spend_summary(
    days: int = 30,
    _actor: str = Depends(require_admin_read),
) -> dict:
    """Operator spend: credits charged against real provider cost.

    Read-only and aggregate. It reports ``unpriced_calls`` prominently because the
    margin figure is only as trustworthy as the rate table behind it, and a partial
    rate table produces a number that looks complete.
    """
    return await db.ai_spend_summary(days=days)


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
    await _audit(
        AuditEvent.ADMIN_AI_CREDIT_POLICY_CHANGED,
        _admin,
        target_user_id=user_id,
        meta={
            # Recording the RESOLVED intent, not the raw payload: "cleared the
            # override" and "set it to 0" look similar in a request body and mean
            # opposite things to whoever reads this later.
            "allowance_override": "unchanged" if allowance is ... else allowance,
            "velocity_override": "unchanged" if velocity is ... else velocity,
            "ai_disabled": payload.ai_disabled,
            "state": payload.state,
        },
    )
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
    # The ledger already holds the reason; this is the accountability half - WHO.
    # A balance change that cannot be traced to an administrator is the shape of a
    # dispute nobody can settle.
    await _audit(
        AuditEvent.ADMIN_AI_CREDITS_GRANTED,
        admin,
        target_user_id=user_id,
        meta={"credits": payload.credits, "reason": payload.reason, "status": status},
    )
    return await db.get_or_create_credit_account(user_id)
