"""Credit packs, editable from the admin panel.

Revision ID: 0039
Revises: 0038

Prices were previously an environment variable (`AI_CREDIT_PACKS`), which meant
changing a price - or running a weekend offer - required a redeploy. That is the wrong
shape for something an operator wants to adjust on a Tuesday afternoon, so packs move
into the database and the env var goes away entirely. One source of truth, not two.

MONEY IS STORED AS INTEGERS IN THE SMALLEST CURRENCY UNIT, twice:

* ``amount_minor`` - the regular price.
* ``sale_amount_minor`` - the discounted price, valid only inside its window.

Both are explicit integers rather than a percentage the system multiplies out. The
operator still THINKS in percentages - the admin form takes "20% off" and computes the
figure - but what is stored, displayed, charged and later re-checked against the
provider's webhook is one exact integer. A stored percentage would be re-multiplied in
several places and could round differently in any of them, and a one-paisa disagreement
between the page and the webhook check is a failed purchase for a customer who did
nothing wrong.

THE SALE WINDOW EXPIRES BY ITSELF. Effective price is the sale price only while
``sale_starts_at <= now <= sale_ends_at``; outside that window the regular price applies
with no cron, no cleanup job, and nothing to forget. An offer that keeps selling at the
discount because a job did not run is a slow leak nobody notices.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: Union[str, Sequence[str], None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_packs",
        # Operator-chosen slug ("starter"). Stable, because credit_purchases records it
        # and those rows outlive any edit to the pack.
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        # Regular price, smallest currency unit, tax-inclusive.
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        # Optional discount. NULL means no offer.
        sa.Column("sale_amount_minor", sa.Integer(), nullable=True),
        sa.Column("sale_label", sa.String(), nullable=True),
        sa.Column("sale_starts_at", sa.String(), nullable=True),
        sa.Column("sale_ends_at", sa.String(), nullable=True),
        # Inactive packs stay in the table so their purchase history keeps making
        # sense; they are simply not offered.
        # `sa.false()` rather than text("0"): Postgres rejects an integer default on a
        # boolean column, and this app runs SQLite locally and Postgres hosted. The
        # portability test suite caught exactly this.
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index("ix_credit_packs_active_sort", "credit_packs", ["active", "sort_order"])


def downgrade() -> None:
    op.drop_index("ix_credit_packs_active_sort", table_name="credit_packs")
    op.drop_table("credit_packs")
