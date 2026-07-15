"""P3 Productivity: resume_versions (version history — R1–R3)

Adds the ``resume_versions`` table (design §A / "Data Models"): immutable,
gzip-compressed, content-hash-deduped snapshots of a resume's ``processed_data``
scoped to ``(user_id, resume_id)``. The gzipped JSON payload lives in the
``data_gz`` BLOB (``LargeBinary`` — ``BYTEA`` on Postgres); the metadata-only
list never loads it.

Index ``ix_resume_versions_scope_created (user_id, resume_id, created_at, id)``
serves the newest-first keyset list, the "latest snapshot" dedupe/undo lookup,
and the prune scan.

On Postgres the index is created ``CONCURRENTLY`` (no long write lock — design
"Deployment"); on SQLite (local/test) inline. Locally the same schema is
produced by ``create_all`` from ``app.models.ResumeVersion``; hosted uses this
migration. Reversible.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-09 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_resume_versions_scope_created"
_TABLE = "resume_versions"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("resume_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("data_gz", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Owning-user index (mirrors the model's index=True on user_id).
    op.create_index("ix_resume_versions_user_id", _TABLE, ["user_id"], unique=False)

    cols = ["user_id", "resume_id", "created_at", "id"]
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.create_index(
                _INDEX, _TABLE, cols, unique=False,
                postgresql_concurrently=True, if_not_exists=True,
            )
    else:
        op.create_index(_INDEX, _TABLE, cols, unique=False)


def downgrade() -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(
                _INDEX, table_name=_TABLE, postgresql_concurrently=True, if_exists=True
            )
    else:
        op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_index("ix_resume_versions_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
