"""Per-board health, so a broken adapter stops being invisible.

Job boards redesign constantly. When a selector dies the board simply returns
nothing, and the only trace was a toast in whichever run happened to be on screen
at the time - so an adapter could be dead for three weeks while the user assumed
their search terms were too narrow.

This records the outcome of every harvest per board and answers one question the
user can act on: *is this board actually working for me?* Three consecutive empty
or failed runs is the signal - one empty run is a normal search, three in a row
against a board that used to work is a broken adapter or an expired session.

Kept deliberately small: a rolling counter and the last error, not a log. The aim
is a status the UI can show, not an audit trail nobody reads.

Revision ID: 0031
Revises: 0030
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "board_health",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("board", sa.String(), nullable=False),
        # ok | empty | signed_out | capped | error
        sa.Column("last_status", sa.String(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_run_at", sa.String(), nullable=False),
        # Last run that actually returned rows - the "it used to work" evidence.
        sa.Column("last_success_at", sa.String(), nullable=True),
        sa.Column("last_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_runs", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("user_id", "board", name="uq_board_health_user_board"),
    )
    op.create_index("ix_board_health_user_id", "board_health", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_board_health_user_id", table_name="board_health")
    op.drop_table("board_health")
