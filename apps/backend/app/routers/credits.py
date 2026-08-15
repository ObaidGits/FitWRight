"""What the user themselves can see about their AI allowance.

The whole surface is shaped around ONE question - "can I still do the thing I came
here to do?" - which is why the response leads with actions remaining rather than a
credit count. A number like "37 credits" is not information a user can act on; "about
4 more tailored resumes" is.

It also always names the free alternative. Somebody who has run out is not in a dead
end: their own provider key works forever and costs the operator nothing, so the
out-of-credits state is a fork in the road, not a wall. Presenting it as a wall would
simply lose the user.

Deliberately NOT here: prices, purchase links, or a top-up button. Metering runs
first so the pricing is set from observed usage rather than guessed - see the spec's
Phase 4.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai_credits import (
    FEATURE_FALLBACK_TOKENS,
    credits_for_tokens,
    describe_balance,
    resolve_allowance,
)
from app.ai_metered import user_has_own_key
from app.auth.principal import get_effective_user_id
from app.config import settings

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
#: metered feature: nobody plans their month around "match score".
_HEADLINE_FEATURES = [
    ("resume_tailor", "Tailored resumes"),
    ("cover_letter", "Cover letters"),
    ("interview_prep", "Interview prep"),
]


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

    account = await _db().get_or_create_credit_account(user_id)
    # Same lazy grant the spend path uses, so the balance shown is the balance that
    # will actually be honoured a moment later.
    from app.ai_allowance import ensure_allowance

    account = await ensure_allowance(user_id, account=account) or account
    available = int(account.get("available_credits") or 0)

    if account.get("ai_disabled") or account.get("state") != "ok":
        return {
            "mode": "disabled",
            "unlimited": False,
            "available_credits": available,
            "summary": "AI features are turned off for this account.",
            "actions": [],
            "credits_enabled": True,
        }

    monthly = resolve_allowance(
        account, global_default=settings.ai_monthly_allowance_credits
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
        "summary": describe_balance(available),
        "actions": _actions_remaining(available),
        # Drives the gentle warning in the UI. A threshold rather than a raw count so
        # the copy stays in one place.
        "low": _is_low(available),
        "own_key_is_free": True,
        "credits_enabled": True,
    }


def _actions_remaining(available: int) -> list[dict]:
    """How many of each headline action the user can still do.

    Uses the same estimate the reserve uses, so the number shown here cannot promise
    more than the spend guard will actually allow.
    """
    out = []
    for feature, label in _HEADLINE_FEATURES:
        per_action = credits_for_tokens(FEATURE_FALLBACK_TOKENS.get(feature, 8000))
        out.append(
            {
                "feature": feature,
                "label": label,
                "remaining": (available // per_action) if per_action > 0 else 0,
            }
        )
    return out


def _is_low(available: int) -> bool:
    """Low = not enough left for a tailored resume, the product's core action.

    Defined against what the user can still DO rather than a percentage, because a
    percentage of a number they never see is meaningless.
    """
    per_tailor = credits_for_tokens(FEATURE_FALLBACK_TOKENS["resume_tailor"])
    return per_tailor > 0 and available < per_tailor * 2


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
