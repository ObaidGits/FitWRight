"""Credit purchases.

Revision ID: 0038
Revises: 0037

Phase 4 of the ai-provider-admin spec. The table exists and the machinery around it is
complete and tested against a fake provider; NO live payment provider is wired, and
`AI_PURCHASES_ENABLED` defaults to false. See app/ai_purchases.py for why that boundary
is where it is.

Design notes that are load-bearing:

* ``provider_event_id`` is UNIQUE. Payment providers redeliver webhooks - that is normal,
  documented behaviour, not an error - so the uniqueness constraint is what makes a
  redelivery a no-op instead of a second grant of credits.
* ``state`` is forward-only: created -> paid -> granted, with failed/refunded as
  terminal branches. A backwards transition is refused in code, because a late-arriving
  earlier webhook must not undo a completed purchase.
* Money is stored in the smallest currency unit as an INTEGER with an explicit currency
  code. No floats: 0.1 + 0.2 is not 0.3, and that error compounds across a ledger.
* ``credits`` is recorded on the row rather than recomputed from the amount at grant
  time. Pack pricing changes; what this buyer was promised must not.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: Union[str, Sequence[str], None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_purchases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("pack_id", sa.String(), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        # Smallest currency unit (paise, cents). Integer, never float.
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("tax_minor", sa.Integer(), nullable=False, server_default="0"),
        # created | paid | granted | failed | refunded. Forward-only.
        sa.Column("state", sa.String(), nullable=False, server_default="created"),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_order_id", sa.String(), nullable=True),
        sa.Column("provider_payment_id", sa.String(), nullable=True),
        # UNIQUE: the whole idempotency story for webhook redelivery.
        sa.Column("provider_event_id", sa.String(), nullable=True),
        sa.Column("invoice_number", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("granted_at", sa.String(), nullable=True),
        sa.Column("refunded_at", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_credit_purchases_user_created", "credit_purchases", ["user_id", "created_at"]
    )
    op.create_index(
        "ux_credit_purchases_event",
        "credit_purchases",
        ["provider_event_id"],
        unique=True,
    )
    op.create_index(
        "ux_credit_purchases_invoice",
        "credit_purchases",
        ["invoice_number"],
        unique=True,
    )
    op.create_index("ix_credit_purchases_state", "credit_purchases", ["state"])


def downgrade() -> None:
    op.drop_index("ix_credit_purchases_state", table_name="credit_purchases")
    op.drop_index("ux_credit_purchases_invoice", table_name="credit_purchases")
    op.drop_index("ux_credit_purchases_event", table_name="credit_purchases")
    op.drop_index("ix_credit_purchases_user_created", table_name="credit_purchases")
    op.drop_table("credit_purchases")
