"""Public pricing - the only unauthenticated view of what FitWright costs.

A visitor deciding whether to sign up cannot be asked to sign up first to see the price.
This is the one endpoint that serves the marketing pricing page, and it is deliberately
narrow: plan and per-action prices only, no user data, nothing account-specific. It takes
no session and resolves no user id, which is what keeps it out of the owned-route authz
inventory legitimately rather than by omission.

WHAT IS INTENTIONALLY EXPOSED: your price list. That is public information the moment you
publish a pricing page, so there is nothing here an operator would not print on a
billboard. What is NOT here: anybody's balance, plan, or purchase history - those all live
behind ``/credits``, which requires a session.

Prices come from the same admin-editable rows the charge uses, so the public page cannot
advertise a number the system will not honour.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/public", tags=["Public pricing"])


def _db():
    """Resolved at call time so tests can swap the singleton (see routers/credits)."""
    from app.database import db

    return db


@router.get("/pricing")
async def public_pricing() -> dict:
    """Plans and per-action prices, for a visitor who is not signed in yet."""
    db = _db()
    from app.ai_feature_prices import application_bundle_credits, resolve_all_feature_costs

    try:
        plans = await db.list_subscription_plans(only_active=True)
        costs = await resolve_all_feature_costs(db, only_active=True)
        per_application = await application_bundle_credits(db)
    except Exception:
        # A pricing page that fails to load costs a signup. An empty-but-valid response
        # lets the page render its static explanation instead of an error.
        return {
            "credits_enabled": bool(getattr(settings, "ai_credits_enabled", False)),
            "credits_per_application": 0,
            "plans": [],
            "features": [],
        }

    return {
        "credits_enabled": bool(getattr(settings, "ai_credits_enabled", False)),
        "credits_per_application": per_application,
        "plans": [
            {
                "id": p["id"],
                "label": p["label"],
                "price_minor": p["price_minor"],
                "currency": p["currency"],
                "monthly_credits": p["monthly_credits"],
                "search_daily_limit": p["search_daily_limit"],
                "is_free": int(p["price_minor"]) <= 0,
                "description": p["description"],
                # So the page can express an allowance as something human without
                # recomputing the bundle itself and drifting from the app.
                "approx_applications": (
                    int(p["monthly_credits"]) // per_application if per_application > 0 else 0
                ),
            }
            for p in plans
        ],
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
    }
