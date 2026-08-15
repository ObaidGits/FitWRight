"""What the user themselves can see about their AI allowance.

The whole surface is shaped around ONE question - "can I still do the thing I came
here to do?" - which is why the response leads with actions remaining rather than a
credit count. A number like "37 credits" is not information a user can act on; "about
4 more tailored resumes" is.

It also always names the free alternative. Somebody who has run out is not in a dead
end: their own provider key works forever and costs the operator nothing, so the
out-of-credits state is a fork in the road, not a wall. Presenting it as a wall would
simply lose the user.

Prices now live here too (``/pricing``), which they deliberately did not before -
metering ran first so the price list could be set from observed cost rather than
guessed. Every number a user sees comes from the same admin-editable rows the charge
comes from, and the "one application" figure is computed from those rows rather than
written down separately, because a headline that disagrees with the price list beside
it is worse than no headline.

Searches are reported separately from credits on purpose. They are capped, not charged,
so running out of them means "come back tomorrow" - telling that user to buy credits
would not help them at all.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.ai_credits import describe_balance, resolve_allowance
from app.ai_metered import user_has_own_key
from app.auth.principal import get_effective_user_id
from app.config import settings
from app.errors import ApiError

router = APIRouter(prefix="/credits", tags=["credits"])


def _db():
    """Resolve the database at CALL time, not import time.

    A module-level ``from app.database import db`` binds whichever instance existed
    when this module was first imported. Tests swap that singleton for an isolated
    one, and a module that captured the original keeps reading the real database -
    which is how this file's first version quietly passed its assertions against the
    developer's own data instead of the fixture's.
    """
    from app.database import db

    return db


#: The features worth showing a user, in the order they matter to them. Not every
#: priced feature: nobody plans their month around "match score". Labels come from the
#: admin-editable price rows, so this is only a selection, never a second copy of the
#: names or the numbers.
_HEADLINE_FEATURES = ("resume_tailor", "cover_letter", "interview_prep")


@router.get("")
async def get_my_credits(user_id: str = Depends(get_effective_user_id)) -> dict:
    """This user's AI allowance, phrased in things they can do.

    Safe to call on every page load: it reads the account and never reserves, so
    rendering a balance can never consume one.
    """
    # A user on their own key has no limit worth showing. Telling them they have "0
    # credits" would be alarming and false - they are not spending the operator's
    # money at all.
    if user_has_own_key(user_id):
        return {
            "mode": "own_key",
            "unlimited": True,
            "summary": "You're using your own AI provider key, so FitWright isn't limiting you.",
            "actions": [],
            "credits_enabled": settings.ai_credits_enabled,
        }

    if not settings.ai_credits_enabled:
        # Shipping dark: metering is on, charging is not. Inventing a balance here
        # would train users to worry about a limit that does not exist yet.
        return {
            "mode": "unlimited",
            "unlimited": True,
            "summary": "AI features are included with your account.",
            "actions": [],
            "credits_enabled": False,
        }

    db = _db()
    account = await db.get_or_create_credit_account(user_id)
    # Same lazy grant the spend path uses, so the balance shown is the balance that
    # will actually be honoured a moment later.
    from app.ai_allowance import ensure_allowance

    account = await ensure_allowance(user_id, account=account) or account
    available = int(account.get("available_credits") or 0)

    from app.ai_plans import check_search_allowance, resolve_account_plan

    plan = await resolve_account_plan(db, account)

    if account.get("ai_disabled") or account.get("state") != "ok":
        return {
            "mode": "disabled",
            "unlimited": False,
            "available_credits": available,
            "summary": "AI features are turned off for this account.",
            "actions": [],
            "credits_enabled": True,
            "plan": _plan_payload(plan),
        }

    monthly = resolve_allowance(account, global_default=plan.monthly_credits)

    from app.ai_feature_prices import application_bundle_credits, resolve_feature_cost

    per_application = await application_bundle_credits(db)
    search = await check_search_allowance(db, user_id, plan)

    actions = []
    for feature in _HEADLINE_FEATURES:
        cost = await resolve_feature_cost(db, feature)
        per_action = cost.effective_credits
        actions.append(
            {
                "feature": feature,
                "label": cost.label,
                "credits_each": per_action,
                # Free actions are unlimited by definition; -1 would be a magic number,
                # so the flag says so explicitly and the UI renders "included".
                "is_free": per_action <= 0,
                "remaining": (available // per_action) if per_action > 0 else None,
            }
        )

    return {
        "mode": "credits",
        "unlimited": False,
        "available_credits": available,
        "allowance_credits": int(account.get("allowance_credits") or 0),
        "wallet_credits": int(account.get("wallet_credits") or 0),
        "monthly_allowance": monthly,
        # When the free allowance renews. Purchased credits never expire, so this
        # date applies to the free portion only - conflating them would imply a
        # user's paid balance is about to disappear.
        "allowance_period_start": account.get("allowance_period_start"),
        "summary": describe_balance(available, per_action_credits=per_application),
        "credits_per_application": per_application,
        "actions": actions,
        # Drives the gentle warning in the UI. A threshold rather than a raw count so
        # the copy stays in one place.
        "low": per_application > 0 and available < per_application * 2,
        "own_key_is_free": True,
        "credits_enabled": True,
        "plan": _plan_payload(plan),
        # Searches are capped but never charged, so they are reported as their own
        # thing. Folding them into the credit balance would tell a user who has run out
        # of searches to buy credits, which would not help them at all.
        "search": {
            "used_today": search.used,
            "daily_limit": search.limit,
            "remaining": search.remaining,
            "exhausted": not search.allowed,
        },
    }


def _plan_payload(plan) -> dict:
    return {
        "id": plan.id,
        "label": plan.label,
        "price_minor": plan.price_minor,
        "currency": plan.currency,
        "monthly_credits": plan.monthly_credits,
        "search_daily_limit": plan.search_daily_limit,
        "is_free": plan.is_free,
        "description": plan.description,
    }


@router.get("/pricing")
async def get_pricing(user_id: str = Depends(get_effective_user_id)) -> dict:
    """Everything the user needs to understand what things cost.

    One endpoint for the whole pricing screen - the per-action price list, the plans,
    and the headline "one application" figure - because these three numbers must agree
    and the surest way to make them agree is to derive them together, from the same
    rows, in one response.
    """
    db = _db()
    from app.ai_feature_prices import application_bundle_credits, resolve_all_feature_costs
    from app.ai_plans import resolve_account_plan

    costs = await resolve_all_feature_costs(db, only_active=True)
    plans = await db.list_subscription_plans(only_active=True)
    account = await db.get_or_create_credit_account(user_id)
    current = await resolve_account_plan(db, account)

    return {
        "credits_enabled": settings.ai_credits_enabled,
        "credits_per_application": await application_bundle_credits(db),
        "current_plan_id": current.id,
        "features": [
            {
                "feature": c.feature,
                "label": c.label,
                "credits": c.effective_credits,
                "is_free": not c.is_charged or c.credits <= 0,
                "description": c.description,
            }
            for c in costs
        ],
        "plans": [
            {
                "id": p["id"],
                "label": p["label"],
                "price_minor": p["price_minor"],
                "currency": p["currency"],
                "monthly_credits": p["monthly_credits"],
                "search_daily_limit": p["search_daily_limit"],
                "is_free": int(p["price_minor"]) <= 0,
                "is_current": p["id"] == current.id,
                "description": p["description"],
            }
            for p in plans
        ],
    }


@router.get("/purchases/{purchase_id}/receipt")
async def download_receipt(
    purchase_id: str, user_id: str = Depends(get_effective_user_id)
) -> Response:
    """The receipt for one of THIS user's purchases, as a PDF.

    Scoped to the caller: a purchase id is a guessable-ish opaque string, and a receipt
    carries a name, an email and an amount, so ownership is checked rather than assumed
    from possession of the id.

    Only issued once the purchase is complete. A receipt for money that has not finished
    moving would be a document asserting something untrue.
    """
    db = _db()
    purchase = await db.get_purchase(purchase_id)
    if purchase is None or purchase.get("user_id") != user_id:
        # Same answer for "not yours" as for "does not exist", so the endpoint cannot be
        # used to discover whether an id belongs to somebody else.
        raise ApiError(404, "not_found", "Receipt not found.")
    if purchase.get("state") not in ("granted", "refunded"):
        raise ApiError(
            409,
            "receipt_not_ready",
            "This payment hasn't completed yet, so there's no receipt for it.",
        )

    from app.ai_receipts import build_receipt, render_receipt_pdf
    from app.app_settings import get_seller_details
    from app.auth import accounts

    seller = await get_seller_details(db)
    record = await accounts.get_by_id(user_id)
    receipt = build_receipt(
        purchase,
        seller=seller,
        buyer_name=getattr(record, "name", "") or "",
        buyer_email=getattr(record, "email", "") or "",
    )
    pdf = await render_receipt_pdf(receipt)
    filename = f"receipt-{receipt.number}.pdf".replace("/", "-")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class CustomPlanRequest(BaseModel):
    """A request for a plan none of the published tiers covers."""

    #: Roughly how many applications a month they need. The single most useful number for
    #: quoting, and the one thing a slider on the pricing page already made them think about.
    applications_per_month: int = Field(ge=1, le=100_000)
    message: str = Field(default="", max_length=2000)
    company: str = Field(default="", max_length=200)


@router.post("/custom-plan-request")
async def request_custom_plan(
    payload: CustomPlanRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Ask the operator for a custom plan.

    Routed through the EXISTING contact pipeline rather than a new inbox: that path already
    persists the message durably BEFORE attempting delivery, notifies the operator, and
    survives an unconfigured mail provider. A second half-built channel is how requests get
    silently lost.

    Deliberately does not create a pack or quote a price. Only the operator can decide what
    a bespoke plan costs, and a system that invented one would be negotiating for them.
    """
    from uuid import uuid4

    from app.auth import accounts
    from app.auth.email import (
        build_contact_notification_email,
        get_email_sender,
        send_email_safe,
    )
    from app.services.intake import persist_record

    record = await accounts.get_by_id(user_id)
    email = (getattr(record, "email", "") or "").strip()
    name = (getattr(record, "name", "") or "").strip()
    reference = f"plan-{uuid4().hex[:12]}"

    message = (
        f"Needs about {payload.applications_per_month} applications a month.\n\n"
        f"{payload.message or '(no message)'}"
    )

    # Persisted first, exactly as the contact form does, so a mail outage cannot lose a
    # sales lead.
    await persist_record(
        "contact",
        reference,
        {
            "reference": reference,
            "name": name,
            "email": email,
            "subject": "Custom plan request",
            "message": message,
            "purpose": "custom_plan",
            "company": payload.company,
            "user_id": user_id,
            "applications_per_month": payload.applications_per_month,
        },
    )

    recipient = (settings.contact_recipient_email or settings.email_from or "").strip()
    if recipient:
        await send_email_safe(
            get_email_sender(),
            build_contact_notification_email(
                to=recipient,
                reference=reference,
                name=name,
                email=email,
                subject="Custom plan request",
                message=message,
                purpose="custom_plan",
                company=payload.company,
            ),
        )
    else:
        # Said out loud rather than silently dropped: the request IS saved, but nobody is
        # being told about it until a recipient is configured.
        import logging

        logging.getLogger(__name__).warning(
            "Custom plan request %s persisted but no contact recipient is configured",
            reference,
        )

    return {"status": "received", "reference": reference}


@router.get("/purchases")

async def get_my_purchases(
    limit: int = 20, user_id: str = Depends(get_effective_user_id)
) -> dict:
    """This user's payment history.

    The repository could already answer this; nothing exposed it, so a customer had no
    way to see what they had paid for and no invoice reference to quote in a support
    message. Only completed and in-flight purchases carry meaning to a user, but
    failures are included too - a failed attempt they can SEE is one they will not
    report as a missing payment.
    """
    limit = max(1, min(int(limit or 20), 100))
    rows = await _db().list_purchases(user_id, limit=limit)
    return {
        "items": [
            {
                "id": r.get("id"),
                "pack_id": r.get("pack_id"),
                "credits": r.get("credits"),
                "amount_minor": r.get("amount_minor"),
                "currency": r.get("currency"),
                "state": r.get("state"),
                "invoice_number": r.get("invoice_number"),
                "failure_reason": r.get("failure_reason"),
                "created_at": r.get("created_at"),
                "granted_at": r.get("granted_at"),
                "refunded_at": r.get("refunded_at"),
            }
            for r in rows
        ]
    }


@router.get("/packs")
async def get_packs(user_id: str = Depends(get_effective_user_id)) -> dict:
    """The packs this user can buy, with any live discount already applied.

    Prices come from the same resolver the charge uses, so what is shown here is by
    construction what gets charged - a buy screen that advertises one price while the
    order is created for another is a chargeback, not a bug report.

    Returns an empty list when nothing is on sale (no packs configured, or purchases
    switched off). The UI treats that as "not available yet" rather than an error.
    """
    from app.ai_purchases import available_packs

    if not getattr(settings, "ai_purchases_enabled", False):
        return {"enabled": False, "packs": []}

    offers = await available_packs()
    return {
        "enabled": True,
        "packs": [
            {
                "id": o.id,
                "label": o.label,
                "credits": o.credits,
                "currency": o.currency,
                "amount_minor": o.amount_minor,
                "compare_at_minor": o.compare_at_minor,
                "on_sale": o.on_sale,
                "percent_off": o.percent_off,
                "sale_label": o.sale_label,
                "sale_ends_at": o.sale_ends_at,
                "description": o.description,
            }
            for o in offers
        ],
    }


@router.get("/usage")
async def get_my_usage(
    limit: int = 20, user_id: str = Depends(get_effective_user_id)
) -> dict:
    """This user's own recent AI activity.

    Exists so a user can answer "where did it go?" themselves. A balance that drops
    with no visible history is indistinguishable from a bug, and support tickets are
    the expensive way to learn that.
    """
    limit = max(1, min(int(limit or 20), 100))
    rows = await _db().list_usage(user_id, limit=limit)
    return {
        "items": [
            {
                "feature": r.get("feature"),
                "credits_charged": r.get("credits_charged"),
                "outcome": r.get("outcome"),
                "created_at": r.get("created_at"),
            }
            for r in rows
        ]
    }
