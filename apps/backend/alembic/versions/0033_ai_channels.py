"""Provider channels, so one dead provider stops being one dead app.

FitWright routes every AI call through a LiteLLM Router that has exactly one
deployment in its ``model_list``, plus a single deployment-level ``LLM_API_KEY``
environment variable as the only shared fallback. That variable is invisible,
unmeterable and already caused a production bug this month (a stale value made the
app report "AI ready" when it was not). And with one deployment there is nowhere to
fail over TO, which is why ``_build_router`` currently carries the comment:

    Cooldowns disabled: with a single deployment and no fallback, cooldowns would
    blackout the backend on transient failures. Re-enable when a fallback
    deployment is added.

This adds the fallback deployments.

Two tables, deliberately split:

* ``ai_channels`` is CONFIGURATION - what the operator set up. Changes rarely,
  by hand, and is audited.
* ``ai_channel_health`` is RUNTIME STATE - failure counts and cooldowns. Changes
  constantly, automatically, and is disposable.

Keeping them apart means a transient provider blip can never rewrite the operator's
configuration, and wiping health to recover from a bad cooldown cannot lose a key.

Credentials are deliberately NOT here: they go to the existing encrypted per-provider
key store, keyed by channel id, so the codebase keeps exactly one encryption path and
one place that can leak.

Additive and reversible: no existing column changes, no data rewritten.

Revision ID: 0033
Revises: 0032
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: Union[str, Sequence[str], None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_channels",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("api_base", sa.String(), nullable=True),
        # Lower number = preferred. Not unique: two channels may deliberately
        # share a priority, in which case ordering falls back to created_at so it
        # is at least deterministic.
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        # active   - eligible for new traffic
        # disabled - ineligible, config retained
        # draining - serve in-flight only; the required step before deletion, so a
        #            channel can never vanish from under a request already using it
        sa.Column("state", sa.String(), nullable=False, server_default="disabled"),
        # reliable | flaky | unsupported | unknown. An `unsupported` channel is
        # barred from features that need valid JSON (resume parse/tailor): a
        # fallback that keeps the app "up" while returning unusable output is worse
        # than an honest error, because the user only finds out after reading it.
        sa.Column("structured_verdict", sa.String(), nullable=False, server_default="unknown"),
        # Operator's own spend ceiling for this channel, in integer minor units.
        # NULL = uncapped.
        sa.Column("monthly_cost_cap_cents", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("name", name="uq_ai_channels_name"),
    )
    # The routing query is "active channels, best priority first" - index it.
    op.create_index("ix_ai_channels_state_priority", "ai_channels", ["state", "priority"])

    op.create_table(
        "ai_channel_health",
        sa.Column("channel_id", sa.String(), primary_key=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        # Set while a channel is benched. A single probe request is allowed through
        # when this passes, rather than the full traffic, so recovery cannot
        # instantly re-break a provider that is still struggling.
        sa.Column("cooling_until", sa.String(), nullable=True),
        sa.Column("last_ok_at", sa.String(), nullable=True),
        sa.Column("last_error_at", sa.String(), nullable=True),
        # Error CLASS only (timeout / rate_limit / server / auth / ...), never the
        # provider's message - those can carry prompt fragments.
        sa.Column("last_error_class", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_channel_health")
    op.drop_index("ix_ai_channels_state_priority", table_name="ai_channels")
    op.drop_table("ai_channels")
