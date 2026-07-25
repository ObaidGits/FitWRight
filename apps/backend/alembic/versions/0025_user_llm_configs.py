"""durable per-user LLM configuration

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-25 00:00:02.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Persist provider/model selection beside each user's encrypted keys."""
    op.create_table(
        "user_llm_configs",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("api_base", sa.Text(), nullable=True),
        sa.Column(
            "reasoning_effort", sa.String(), server_default="", nullable=False
        ),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    """Remove durable per-user LLM configuration."""
    op.drop_table("user_llm_configs")
