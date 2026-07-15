"""Persistent AI analysis cache: analysis_artifacts (Universal Analysis Object)

Adds the generic, user-scoped ``analysis_artifacts`` cache that lets the app
reuse the result of an expensive AI/analysis operation instead of recomputing
it. It is complementary to ``resume_versions`` / ``profile_versions`` (user-
facing edit history) — this table is an internal, content+algorithm-addressed
cache.

- Reuse key ``(user_id, artifact_type, source_id, checksum, version)`` is UNIQUE
  → an exact lookup is a cache hit, and concurrent producers converge on one row.
- ``checksum`` is the SHA-256 of the canonical input; ``version`` encodes the
  prompt+model+algo, so a prompt/model change simply misses (lazy regeneration).
- ``source_id`` / ``related_id`` support dependency-aware invalidation: deleting
  by a resource id drops artifacts that reference it via either column.

Purely **additive** and **reversible**. Locally the same schema is produced by
``create_all`` from ``app.models``; hosted uses this migration.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-14 11:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: Union[str, Sequence[str], None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("artifact_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("related_id", sa.String(), nullable=True),
        sa.Column("checksum", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="ready"),
        sa.Column("analysis_data", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_analysis_artifacts_user_id", "analysis_artifacts", ["user_id"], unique=False
    )
    op.create_index(
        "ix_analysis_artifacts_related_id", "analysis_artifacts", ["related_id"], unique=False
    )
    op.create_index(
        "ix_analysis_artifacts_source",
        "analysis_artifacts",
        ["user_id", "source_id"],
        unique=False,
    )
    op.create_index(
        "ux_analysis_artifacts_key",
        "analysis_artifacts",
        ["user_id", "artifact_type", "source_id", "checksum", "version"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_analysis_artifacts_key", table_name="analysis_artifacts")
    op.drop_index("ix_analysis_artifacts_source", table_name="analysis_artifacts")
    op.drop_index("ix_analysis_artifacts_related_id", table_name="analysis_artifacts")
    op.drop_index("ix_analysis_artifacts_user_id", table_name="analysis_artifacts")
    op.drop_table("analysis_artifacts")
