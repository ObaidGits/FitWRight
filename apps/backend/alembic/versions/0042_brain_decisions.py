"""Auto-apply brain, Phase 0: the decision audit trail.

Revision ID: 0042
Revises: 0041

Phase 0 of the auto-apply-brain spec (.kiro/specs/auto-apply-brain/) ships before
any LLM call does. Its job is to make every autofill decision explainable: for each
field on a form, where the value came from and how confident that source is.
Grading (green/yellow/red) and the eventual confidence-gated auto-submit are both
computed FROM this table - never estimated after the fact.

``value_source`` and ``grade_contribution`` are plain strings, not a DB enum: a new
source (a future brain capability) is then a code change, validated at the API
boundary, not a migration.

No other table yet. The classification cache (Phase 2), site policy (Phase 3) and
per-user auto-submit settings (Phase 3) are added by the phases that use them, so a
team that stops after Phase 0 has exactly the schema Phase 0 needs and nothing else.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: Union[str, Sequence[str], None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brain_decisions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Nullable: a decision can be recorded mid-fill, before an Application
        # row exists (autofill runs before the user ever presses submit). Not a
        # foreign key for the same reason - the id is provisional at fill time,
        # and ApplicationField follows the same pattern.
        sa.Column("application_id", sa.String(), nullable=True),
        sa.Column("site_host", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("label_normalized", sa.String(), nullable=False),
        sa.Column("resolved_target", sa.String(), nullable=True),
        sa.Column("value_source", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("is_knockout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("filled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("readback_ok", sa.Boolean(), nullable=True),
        sa.Column("grade_contribution", sa.String(), nullable=False, server_default="green"),
        sa.Column("brain_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
    )
    op.create_index("ix_brain_decisions_user_id", "brain_decisions", ["user_id"])
    op.create_index(
        "ix_brain_decisions_application_id", "brain_decisions", ["application_id"]
    )
    op.create_index("ix_brain_decisions_site_host", "brain_decisions", ["site_host"])


def downgrade() -> None:
    op.drop_index("ix_brain_decisions_site_host", table_name="brain_decisions")
    op.drop_index("ix_brain_decisions_application_id", table_name="brain_decisions")
    op.drop_index("ix_brain_decisions_user_id", table_name="brain_decisions")
    op.drop_table("brain_decisions")
