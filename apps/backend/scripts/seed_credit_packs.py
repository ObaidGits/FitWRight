#!/usr/bin/env python3
"""Create the starting credit packs. Idempotent - safe to run again.

WHY THESE NUMBERS

The unit economics are lopsided, and knowing that changes how to price. One credit is
1,000 tokens; a tailored resume costs 20 credits. At the configured provider rates that
is roughly ₹0.012 of provider cost per credit, so 200 credits costs about ₹2.40 to
serve. Razorpay's processing fee on a ₹149 sale is larger than the AI bill behind it.

So price is NOT cost-driven here - it is anchored on what the pack lets someone DO:

    20 credits  = 1 tailored resume
    4  credits  = 1 cover letter
    12 credits  = 1 interview prep pack
    8  credits  = 1 resume upload/parse

A job seeker applying seriously does 20-40 applications, which is 500-1,000 credits with
cover letters. The free monthly allowance of 50 credits is about two tailored resumes -
enough to see that it works, not enough to finish a job hunt.

Per-credit price falls as the pack grows (0.75 -> 0.58 -> 0.47 paise), because a volume
discount is what makes the larger pack the obvious choice rather than a worse deal.

These are a STARTING POINT, not a recommendation to keep forever. Edit them in
Admin > Credit packs - that is the whole reason they live in the database. Once a few
weeks of real metering exist, check Admin > AI spend: if actual cost per credit is far
from the estimate above, revisit.

Usage:
    cd apps/backend && uv run python scripts/seed_credit_packs.py          # create, inactive
    cd apps/backend && uv run python scripts/seed_credit_packs.py --activate

Packs are created INACTIVE unless --activate is passed, so nothing goes on sale by
accident on a production database.
"""

from __future__ import annotations

import asyncio
import sys

#: id, label, credits, price in paise, description
PACKS = [
    (
        "starter",
        "Starter",
        200,
        14900,  # ₹149
        "About 10 tailored resumes",
    ),
    (
        "popular",
        "Job hunt",
        600,
        34900,  # ₹349
        "About 30 tailored resumes with cover letters",
    ),
    (
        "pro",
        "Serious search",
        1500,
        69900,  # ₹699
        "About 75 tailored resumes - best value per credit",
    ),
]


async def main(activate: bool) -> None:
    from app.database import db

    for index, (pack_id, label, credits, amount_minor, description) in enumerate(PACKS):
        existing = await db.get_credit_pack(pack_id)
        if existing is not None:
            # Never silently overwrite a price an operator has since changed. Re-running
            # this script must not undo a deliberate edit.
            print(
                f"  {pack_id}: already exists at "
                f"₹{existing['amount_minor'] / 100:.2f} - left alone"
            )
            continue

        await db.upsert_credit_pack(
            pack_id,
            label=label,
            credits=credits,
            amount_minor=amount_minor,
            currency="INR",
            description=description,
            sort_order=(index + 1) * 10,
            active=activate,
        )
        per_credit = amount_minor / credits
        print(
            f"  {pack_id}: {credits} credits for ₹{amount_minor / 100:.0f} "
            f"({per_credit:.2f} paise/credit) "
            f"{'ON SALE' if activate else 'inactive'}"
        )

    print("\nEdit prices and offers in Admin > Credit packs.")


if __name__ == "__main__":
    activate = "--activate" in sys.argv
    print("Seeding credit packs" + (" (activating)" if activate else " (inactive)"))
    asyncio.run(main(activate))
