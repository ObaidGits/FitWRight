"""Create the starting feature prices and subscription plans. Idempotent.

WHY THESE NUMBERS

The economics are lopsided, and knowing that changes how to price. One credit is 1,000
tokens; at the configured provider rates that is roughly ₹0.012 of provider cost. So one
complete application - a tailored resume (20) plus a cover letter (4) plus a drafted
answer (2) - costs about ₹0.31 to serve.

That means provider spend is NOT the constraint. Razorpay's fee on a ₹299 sale is larger
than the AI bill behind a whole month of heavy use. The real costs are the scraping and
headless-browser infrastructure, and support. Prices are therefore anchored on what the
plan lets someone DO, not on cost-plus.

WHAT IS CHARGED AND WHAT IS NOT

Charged: the AI actions, priced per action, because those are the moments of value and
they are the only things with a real marginal cost.

Not charged: job search. Metering exploration teaches people to stop exploring, and
exploring is exactly what produces the applications that are charged. Search gets a
per-day fair-use ceiling per plan instead - a rate limit, not a price. That distinction
carries into the UI: running out of searches means "come back tomorrow", which is a very
different message from "top up".

PLAN SIZING

A serious job seeker sends 10-30 applications a week. At ~26 credits per application:

    Free       300 credits  ~10 applications   enough to prove it works, not to finish
    Job Hunt  2000 credits  ~65 applications   a normal active search
    Serious   6000 credits ~200 applications   an aggressive search

STARTING POINT, NOT PERMANENT. Everything here is editable in Admin > Feature prices and
Admin > Plans, which is the entire reason these live in the database. After ~50 paying
users, check Admin > AI spend: if real cost per application is far from ₹0.31, revisit.

Usage:
    cd apps/backend && uv run python scripts/seed_pricing.py            # create, inactive plans
    cd apps/backend && uv run python scripts/seed_pricing.py --activate

Plans are created INACTIVE unless --activate is passed, so a production database does not
start charging by accident. Feature PRICES are always created active: a price row that
exists but is inactive would silently fall back to the built-in default, which is more
confusing than either extreme.
"""

from __future__ import annotations

import asyncio
import sys


async def main(activate: bool) -> None:
    from app.ai_feature_prices import (
        APPLICATION_BUNDLE,
        DEFAULT_FEATURE_PRICES,
        invalidate_price_cache,
    )
    from app.ai_plans import DEFAULT_PLANS
    from app.database import db

    print("Feature prices")
    for index, (feature, (label, credits, description)) in enumerate(
        DEFAULT_FEATURE_PRICES.items()
    ):
        existing = await db.get_feature_price(feature)
        if existing is not None:
            # Never silently overwrite a price an operator has since changed - rerunning
            # this script must not undo a deliberate edit.
            print(
                f"  {feature}: already set to {existing['credits']} credits - left alone"
            )
            continue
        await db.upsert_feature_price(
            feature,
            label=label,
            credits=credits,
            is_charged=True,
            active=True,
            sort_order=(index + 1) * 10,
            description=description,
        )
        print(f"  {feature}: {credits} credits ({label})")

    invalidate_price_cache()

    print("\nPlans")
    for index, (
        plan_id,
        label,
        price_minor,
        monthly_credits,
        search_limit,
        is_default,
        description,
    ) in enumerate(DEFAULT_PLANS):
        existing = await db.get_subscription_plan(plan_id)
        if existing is not None:
            print(
                f"  {plan_id}: already exists at ₹{existing['price_minor'] / 100:.0f}"
                f" - left alone"
            )
            continue
        await db.upsert_subscription_plan(
            plan_id,
            label=label,
            price_minor=price_minor,
            currency="INR",
            monthly_credits=monthly_credits,
            search_daily_limit=search_limit,
            is_default=is_default,
            # The free tier must be live regardless: it is what every new account
            # resolves to, and an inactive default plan would leave new users with no
            # allowance at all.
            active=activate or price_minor == 0,
            sort_order=(index + 1) * 10,
            description=description,
        )
        per_app = 26
        print(
            f"  {plan_id}: ₹{price_minor / 100:.0f}/mo, {monthly_credits} credits "
            f"(~{monthly_credits // per_app} applications), "
            f"{search_limit if search_limit is not None else 'unlimited'} searches/day"
            f" {'ACTIVE' if (activate or price_minor == 0) else 'inactive'}"
        )

    bundle = " + ".join(APPLICATION_BUNDLE)
    print(f"\nOne application = {bundle}")
    print("Edit everything in Admin > Feature prices and Admin > Plans.")


if __name__ == "__main__":
    activate = "--activate" in sys.argv
    print("Seeding pricing" + (" (activating paid plans)" if activate else ""))
    asyncio.run(main(activate))
