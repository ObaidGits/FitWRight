"""Durable single-use tailoring previews.

Revision ID: 0022
Revises: 0021
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, Sequence[str], None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tailor_previews",
        sa.Column("preview_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("resume_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("consumed_at", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.resume_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("preview_id"),
        sa.UniqueConstraint("user_id", "request_id", name="uq_tailor_preview_user_request"),
    )
    op.create_index(
        "ix_tailor_preview_consume",
        "tailor_previews",
        [
            "preview_id",
            "user_id",
            "resume_id",
            "job_id",
            "payload_hash",
            "consumed_at",
            "expires_at",
        ],
    )
    op.create_index(
        "ix_tailor_preview_scope_created",
        "tailor_previews",
        ["user_id", "resume_id", "job_id", "prompt_id", "created_at"],
    )
    op.create_index(
        "ix_tailor_preview_expires_at", "tailor_previews", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tailor_preview_expires_at", table_name="tailor_previews")
    op.drop_index("ix_tailor_preview_scope_created", table_name="tailor_previews")
    op.drop_index("ix_tailor_preview_consume", table_name="tailor_previews")
    op.drop_table("tailor_previews")
