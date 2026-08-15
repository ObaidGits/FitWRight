"""Admin-editable feature prices, subscription plans, and daily usage caps.

Revision ID: 0040
Revises: 0039

Three things move out of code and environment variables into the database, because all
three are things an operator changes on a Tuesday afternoon and none of them should
need a redeploy:

1. ``feature_prices`` - what each AI action costs in credits.

   Previously this was ``FEATURE_FALLBACK_TOKENS`` in code, feeding a p95-of-observed-
   usage estimate. That produced a VARIABLE charge, which cannot be shown honestly in a
   UI: "this will cost somewhere between 14 and 26 credits" is not a price a user can
   reason about, and a charge that differs from the number they were shown reads as
   being cheated. So a feature now has ONE published integer price. Token metering does
   not go away - it still records what the call really cost the operator, which is what
   the admin spend view and margin figures are built from - it simply stops deciding
   what the USER pays.

   ``is_charged`` exists separately from the price so an operator can make an action
   free without losing the price they had set, and without a magic 0 meaning two
   different things ("free" vs "not configured yet").

2. ``subscription_plans`` - the monthly tiers, their price, their monthly credit
   allowance, and their fair-use search ceiling.

   The monthly grant was ``AI_MONTHLY_ALLOWANCE_CREDITS``, a single global number, so
   every user necessarily got the same allowance and there was no notion of a paid
   tier at all. A plan row is what makes "which package am I on?" answerable, which in
   turn is what a badge, an upgrade screen and admin plan management all need.

3. ``daily_usage_counters`` - non-billed actions that still need a ceiling.

   Job search is deliberately NOT charged in credits: metering exploration teaches
   users to stop exploring, and exploring is what produces the applications that ARE
   charged. But an uncapped search is an invitation to hammer job boards from a
   residential IP, so it gets a per-day counter per plan instead of a price. A rate
   limit, not a charge.

MONEY AND CREDITS ARE INTEGERS. Plan price is the smallest currency unit
(``price_minor``), matching ``credit_packs``. No floats anywhere near a money path.

PLAN IS RECORDED ON THE ACCOUNT, NOT DERIVED. ``credit_accounts.plan_id`` is nullable
and unconstrained by a foreign key on purpose: a plan row may later be retired while
accounts that were on it still need to render, exactly as ``credit_purchases.pack_id``
already outlives edits to a pack.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: Union[str, Sequence[str], None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_prices",
        # The feature key the code already spends against ("resume_tailor"). Stable:
        # the usage ledger records it and those rows outlive any price edit.
        sa.Column("feature", sa.String(), primary_key=True),
        # What the user sees. "Tailored resume", not "resume_tailor".
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        # False = this action runs without spending credits. Kept separate from a
        # price of 0 so "free on purpose" and "not priced yet" stay distinguishable.
        sa.Column("is_charged", sa.Boolean(), nullable=False, server_default=sa.true()),
        # Inactive rows stay so historical ledger entries keep resolving to a label.
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        # Shown under the label on the pricing screen, e.g. "per open-ended question".
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_feature_prices_active_sort", "feature_prices", ["active", "sort_order"]
    )

    op.create_table(
        "subscription_plans",
        # Operator-chosen slug ("free", "job_hunt", "serious"). Recorded on accounts.
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        # 0 for the free tier. Smallest currency unit, tax-inclusive, as credit_packs.
        sa.Column("price_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        # Credits granted on signup and re-granted at each monthly period boundary.
        sa.Column("monthly_credits", sa.Integer(), nullable=False, server_default="0"),
        # Fair-use ceiling for non-billed job searches. 0 = no searches allowed;
        # NULL = uncapped (deliberately expressible, deliberately not the default).
        sa.Column("search_daily_limit", sa.Integer(), nullable=True),
        # Exactly one plan should carry this: the tier a new account lands on.
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_subscription_plans_active_sort",
        "subscription_plans",
        ["active", "sort_order"],
    )

    # Which plan this account is on. Nullable = "has not been placed on a plan yet",
    # which resolves to the default plan at read time rather than being backfilled
    # here: a backfill would miss every account created after it ran.
    op.add_column(
        "credit_accounts", sa.Column("plan_id", sa.String(), nullable=True)
    )

    op.create_table(
        "daily_usage_counters",
        sa.Column("user_id", sa.String(), nullable=False),
        # UTC calendar day, "YYYY-MM-DD". A date string rather than a timestamp so the
        # primary key itself enforces one row per user per kind per day.
        sa.Column("day", sa.String(length=10), nullable=False),
        # "job_search" today; the table is deliberately generic so the next
        # rate-limited-but-free action does not need another migration.
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "day", "kind", name="pk_daily_usage_counters"),
    )


def downgrade() -> None:
    op.drop_table("daily_usage_counters")
    op.drop_column("credit_accounts", "plan_id")
    op.drop_index(
        "ix_subscription_plans_active_sort", table_name="subscription_plans"
    )
    op.drop_table("subscription_plans")
    op.drop_index("ix_feature_prices_active_sort", table_name="feature_prices")
    op.drop_table("feature_prices")
