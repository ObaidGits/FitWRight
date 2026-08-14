"""Credit accounts, reservations and transactions - reserve-then-settle accounting.

The naive alternatives both break in production:

* Charge AFTER the call: a user with 1 credit left fires 50 concurrent requests and
  every one passes the balance check before any of them settles. Classic overdraft.
* Charge BEFORE the call: overcharges every time, because the real cost is unknown
  until the response arrives.

So: hold an estimate before the call, settle the actual after, release the difference,
and sweep abandoned holds. This is what payment systems and cloud providers do, and it
is the only option that is both safe under concurrency and fair on price.

Three tables:

* ``credit_accounts`` is the balance AUTHORITY. Balance lives in the database, not in
  memory - note that this app's existing per-minute rate limiter degrades to
  per-process when ``KVSTORE_URL`` is unset (it is unset in production today). That is
  survivable for a rate limit. It is not survivable for money.
* ``credit_reservations`` are the short-lived holds.
* ``credit_transactions`` is every balance movement, append-only, so a balance can
  always be explained rather than merely asserted.

Available balance is deliberately NOT a stored column. It is derived:

    available = allowance_credits + wallet_credits - reserved_credits

A separately-maintained "available" column is guaranteed to disagree with its
components eventually. Deriving it makes that impossible.

Credits are integers. Money is integer minor units. No floats.

Additive and reversible.

Revision ID: 0035
Revises: 0034
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credit_accounts",
        sa.Column("user_id", sa.String(), primary_key=True),
        # The recurring free grant. Use-it-or-lose-it, reset each period.
        sa.Column("allowance_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("allowance_period_start", sa.String(), nullable=True),
        # Purchased credits. Deliberately never expire - expiring paid credits is the
        # single most resented pattern in prepaid products.
        sa.Column("wallet_credits", sa.Integer(), nullable=False, server_default="0"),
        # Sum of live holds. Subtracted from the total to get available.
        sa.Column("reserved_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lifetime_granted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lifetime_spent", sa.Integer(), nullable=False, server_default="0"),
        # Spend velocity, independent of balance: credits alone do not stop a stolen
        # session from draining a funded wallet in one minute.
        sa.Column("velocity_window_start", sa.String(), nullable=True),
        sa.Column("velocity_spent", sa.Integer(), nullable=False, server_default="0"),
        # ok | blocked. `blocked` after a refund/chargeback clawed back credits that
        # were already spent, which is the one case a balance may go negative.
        sa.Column("state", sa.String(), nullable=False, server_default="ok"),
        # Per-user policy override. NULL = inherit the global default. An override is
        # ABSOLUTE: raising the global default must never implicitly widen it.
        sa.Column("monthly_allowance_override", sa.Integer(), nullable=True),
        sa.Column("velocity_cap_override", sa.Integer(), nullable=True),
        # Per-user kill switch, effective without a deploy.
        sa.Column("ai_disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        # A negative balance is legitimate ONLY via claw-back, so it is not
        # constrained away here - but allowance and wallet are each independently
        # non-negative in normal operation, and reserved can never be negative.
        sa.CheckConstraint("reserved_credits >= 0", name="ck_credit_accounts_reserved_nonneg"),
    )

    op.create_table(
        "credit_reservations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("credits_reserved", sa.Integer(), nullable=False),
        # held | settled | released | expired. Forward-only.
        sa.Column("state", sa.String(), nullable=False, server_default="held"),
        # Makes a retried HTTP request reuse its hold instead of taking a second one.
        # UNIQUE is what actually enforces that, not application checks.
        sa.Column("idempotency_key", sa.String(), nullable=False),
        # A crashed worker must not freeze a balance forever; a sweep releases holds
        # past this instant.
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("settled_at", sa.String(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_reservations_idem"),
    )
    # The sweep query: everything still held past expiry.
    op.create_index(
        "ix_credit_reservations_state_expires", "credit_reservations", ["state", "expires_at"]
    )
    op.create_index("ix_credit_reservations_user", "credit_reservations", ["user_id"])

    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        # signup_grant | monthly_refill | purchase | spend | refund | admin_adjust |
        # chargeback
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("credits_delta", sa.Integer(), nullable=False),
        # Balance after this movement, so history can be replayed and audited without
        # recomputing from the beginning of time.
        sa.Column("balance_after", sa.Integer(), nullable=False),
        # Mandatory for admin_adjust - an unexplained manual balance change is
        # indistinguishable from a bug or an abuse.
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("actor_user_id", sa.String(), nullable=True),
        # Payment/event id, or the ledger row this settles.
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        # The single constraint that makes double-charging impossible, rather than
        # merely unlikely.
        sa.UniqueConstraint("idempotency_key", name="uq_credit_transactions_idem"),
    )
    op.create_index(
        "ix_credit_transactions_user_created", "credit_transactions", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_credit_transactions_user_created", table_name="credit_transactions")
    op.drop_table("credit_transactions")
    op.drop_index("ix_credit_reservations_user", table_name="credit_reservations")
    op.drop_index("ix_credit_reservations_state_expires", table_name="credit_reservations")
    op.drop_table("credit_reservations")
    op.drop_table("credit_accounts")
