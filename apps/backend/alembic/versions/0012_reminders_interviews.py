"""P3 Productivity: reminders + interviews (R10, R11)

Adds the two scheduled-entity tables (design §E/§F):

- ``reminders`` - follow-up reminders on an application (UTC ``due_at`` + IANA
  ``tz`` + bounded ``recurrence``), driven by the claim-based scheduler
  (``status`` pending->firing->fired, ``claimed_at`` lease). Index
  ``(status, due_at)`` is the scanner cursor.
- ``interviews`` - scheduled interviews (UTC ``starts_at`` + IANA ``tz`` +
  ``lead_times``/``fired_leads`` JSON), scanned for lead-time notifications.
  Index ``(status, starts_at)`` is the scanner cursor.

Both carry user + application scoping indexes for the workspace/agenda reads.
Postgres indexes created ``CONCURRENTLY``; SQLite inline. Reversible.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-09 19:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEXES: tuple[tuple[str, str, list[str]], ...] = (
    ("ix_reminders_status_due", "reminders", ["status", "due_at"]),
    ("ix_reminders_user_app", "reminders", ["user_id", "application_id"]),
    ("ix_reminders_user_due", "reminders", ["user_id", "due_at"]),
    ("ix_interviews_status_starts", "interviews", ["status", "starts_at"]),
    ("ix_interviews_user_app", "interviews", ["user_id", "application_id"]),
    ("ix_interviews_user_starts", "interviews", ["user_id", "starts_at"]),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_index(name: str, table: str, cols: list[str]) -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.create_index(name, table, cols, postgresql_concurrently=True, if_not_exists=True)
    else:
        op.create_index(name, table, cols)


def _drop_index(name: str, table: str) -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
    else:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("due_at", sa.String(), nullable=False),
        sa.Column("tz", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("recurrence", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("claimed_at", sa.String(), nullable=True),
        sa.Column("fired_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "interviews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("starts_at", sa.String(), nullable=False),
        sa.Column("tz", sa.String(), nullable=False, server_default="UTC"),
        sa.Column("duration_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("kind", sa.String(), nullable=False, server_default="screen"),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("lead_times", sa.JSON(), nullable=True),
        sa.Column("fired_leads", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="scheduled"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, table, cols in _INDEXES:
        _create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _cols in reversed(_INDEXES):
        _drop_index(name, table)
    op.drop_table("interviews")
    op.drop_table("reminders")
