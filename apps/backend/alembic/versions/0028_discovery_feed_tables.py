"""discovery feed tables (runs + results)

Revision ID: 0028
Revises: 0027
Create Date: 2026-08-11 10:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, Sequence[str], None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create discovery_runs and discovery_results tables."""
    op.create_table(
        "discovery_runs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("resume_id", sa.String(36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("interval_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("last_run_at", sa.String(40), nullable=True),
        sa.Column("next_run_at", sa.String(40), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("results_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "resume_id", name="uq_discovery_runs_user_resume"),
    )
    op.create_index("ix_discovery_runs_next", "discovery_runs", ["enabled", "next_run_at"])

    op.create_table(
        "discovery_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("company", sa.String(255), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("is_remote", sa.Boolean(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("salary", sa.String(100), nullable=True),
        sa.Column("posted_at", sa.String(40), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("matched_keywords", sa.JSON(), nullable=True),
        sa.Column("missing_keywords", sa.JSON(), nullable=True),
        sa.Column("partial", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'new'"),
        sa.Column("seen", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_discovery_results_user_fp"),
    )
    op.create_index("ix_discovery_results_user_status", "discovery_results", ["user_id", "status"])
    op.create_index("ix_discovery_results_user_created", "discovery_results", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_discovery_results_user_created", table_name="discovery_results")
    op.drop_index("ix_discovery_results_user_status", table_name="discovery_results")
    op.drop_table("discovery_results")
    op.drop_index("ix_discovery_runs_next", table_name="discovery_runs")
    op.drop_table("discovery_runs")
