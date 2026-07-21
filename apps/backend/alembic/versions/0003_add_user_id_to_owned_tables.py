"""add nullable user_id to owned tables + indexes

Adds a nullable ``user_id`` column (FK -> users.id, ON DELETE CASCADE) and a
``ix_<table>_user_id`` index to every owned table: resumes, jobs, improvements,
applications, and api_keys. Nullable now so the online add is non-blocking and
existing rows stay valid; migration 0004 backfills the bootstrap owner and 0005
enforces NOT NULL / the per-user constraints (ADR-4, ADR-9).

Portable across SQLite (3.35+ supports online ADD/DROP COLUMN; a nullable
REFERENCES column is permitted) and PostgreSQL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-09 00:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Owned tables gaining ``user_id`` and its index.
_OWNED_TABLES: tuple[str, ...] = (
    "resumes",
    "jobs",
    "improvements",
    "applications",
    "api_keys",
)


def upgrade() -> None:
    """Add nullable ``user_id`` (+ index) to each owned table.

    Uses ``batch_alter_table`` so the FK is added portably: SQLite recreates the
    table via copy-and-move (preserving existing rows, indexes, and the resumes
    partial-unique / applications unique constraints), while PostgreSQL issues
    an in-place ``ALTER TABLE``.
    """
    for table in _OWNED_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "user_id",
                    sa.String(),
                    sa.ForeignKey(
                        "users.id",
                        ondelete="CASCADE",
                        name=f"fk_{table}_user_id_users",
                    ),
                    nullable=True,
                )
            )
            batch_op.create_index(f"ix_{table}_user_id", ["user_id"], unique=False)


def downgrade() -> None:
    """Drop the ``user_id`` column (+ index) from each owned table."""
    for table in reversed(_OWNED_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(f"ix_{table}_user_id")
            batch_op.drop_column("user_id")
