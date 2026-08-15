"""Per-user AI usage ledger - the billing record, deliberately separate from metrics.

FitWright already records AI usage in ``app/admin/ai_metrics.py``. That module CANNOT
be reused for this, and the reason is in its own docstrings: raw provider input "is
never retained or exposed", and the prompt/completion token breakdown "is a rejected
field". It is intentionally anonymous, aggregate, and has no user dimension.

Billing needs the exact opposite properties - per user, per call, itemised, retained,
reconcilable against a provider invoice. Those two privacy contracts cannot live in
one table without breaking the anonymous one. So this is a second system, on purpose,
and the two must not be merged later.

Append-only. A correction is a new compensating row, never an edit - otherwise "why
is my balance this?" becomes unanswerable the moment anything goes wrong.

Costs are integer MICROS of currency and tokens are integers. No floats anywhere in a
money path: binary floating point cannot represent decimal currency exactly, and the
error compounds across thousands of rows.

Additive and reversible.

Revision ID: 0034
Revises: 0033
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_usage_ledger",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        # Which product action spent this (resume_parse, tailor, cover_letter, ...).
        # Drives both the per-feature cost estimates and the user's own history.
        sa.Column("feature", sa.String(), nullable=False),
        # Nullable: a call can fail before any channel is chosen (e.g. every channel
        # cooling down), and that attempt still deserves a row.
        sa.Column("channel_id", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        # TRUE when the provider returned no usage block and these numbers are our
        # own estimate. An estimate must never be silently indistinguishable from a
        # measurement, or reconciliation against the provider's bill is impossible.
        sa.Column("tokens_estimated", sa.Boolean(), nullable=False, server_default=sa.false()),
        # What the OPERATOR paid, in micros. Distinct from credits_charged: the user
        # is charged the primary channel's rate even when an expensive fallback
        # served them, because failover is the operator's problem, not theirs.
        sa.Column("provider_cost_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits_charged", sa.Integer(), nullable=False, server_default="0"),
        # Links a charge back to the hold it settled (Phase 3). Nullable so Phase 2
        # can ship and meter for a week before any charging exists.
        sa.Column("reservation_id", sa.String(), nullable=True),
        sa.Column("request_id", sa.String(), nullable=True),
        # ok | failed | cancelled. `failed` rows exist precisely so a zero charge is
        # provable rather than merely absent.
        sa.Column("outcome", sa.String(), nullable=False, server_default="ok"),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    # "this user's history, newest first" and "spend for a period".
    op.create_index("ix_ai_usage_user_created", "ai_usage_ledger", ["user_id", "created_at"])
    # Admin cost dashboard: per channel over time.
    op.create_index(
        "ix_ai_usage_channel_created", "ai_usage_ledger", ["channel_id", "created_at"]
    )
    # Per-feature rolling averages, which is what makes a pre-flight cost estimate
    # honest instead of a hardcoded guess.
    op.create_index("ix_ai_usage_feature_created", "ai_usage_ledger", ["feature", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_feature_created", table_name="ai_usage_ledger")
    op.drop_index("ix_ai_usage_channel_created", table_name="ai_usage_ledger")
    op.drop_index("ix_ai_usage_user_created", table_name="ai_usage_ledger")
    op.drop_table("ai_usage_ledger")
