"""P3 Productivity: extended profile + avatar_key on users (R13, R14)

Adds ``users.avatar_key`` (server-generated storage key for orphan GC) and the
extended profile fields ``headline`` / ``location`` / ``links`` (JSON) used to
prefill resumes (design §H). All nullable; no backfill. Reversible.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-09 20:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("avatar_key", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("headline", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("location", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("links", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("links")
        batch_op.drop_column("location")
        batch_op.drop_column("headline")
        batch_op.drop_column("avatar_key")
