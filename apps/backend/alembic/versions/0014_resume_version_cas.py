"""P4 Resilience: add ``resumes.version`` optimistic-concurrency token (R3.1)

Adds an integer ``version`` column to ``resumes`` (NOT NULL, default 1) used for
optimistic-concurrency (version CAS): every write carries the ``base_version`` it
read and the server applies it only when the stored version still matches,
bumping it atomically. Existing rows are backfilled to 1. Reversible.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-09 21:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add with a server_default so the NOT NULL backfill is atomic on both
    # SQLite (batch/table-rebuild) and Postgres (single ALTER). We keep the
    # server_default in place: it is harmless if the column is ever unused
    # (rollback safety - design §Deployment), and it means INSERTs that omit
    # ``version`` (legacy code paths) still get a valid 1.
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
    # Defensive explicit backfill (batch add_column already applies the
    # server_default to existing rows, but this makes intent explicit and is a
    # no-op if already set).
    op.execute("UPDATE resumes SET version = 1 WHERE version IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.drop_column("version")
