"""P2 Admin: soft-delete + usage counters + last_active + metrics_daily + indexes

Adds the schema the P2 Admin subsystem needs (design "Data Models"):

- ``users.deleted_at`` (soft-delete grace-period marker), ``users.resume_count``
  / ``users.application_count`` (denormalized usage counters - R11.3), and
  ``users.last_active_at`` (session-activity watermark for the active-user calc).
- ``metrics_daily(day_utc, metric, value, computed_at)`` - the closed-day rollup
  target UPSERTed by the ``RollupJob`` (R3.2, R10.3).
- The admin composite indexes: ``users(role,status)`` (active-admin count),
  ``users(created_at,id)`` (list keyset), ``users(deleted_at)`` (purge scan +
  filter), ``users(last_active_at)``, ``sessions(user_id,revoked_at,last_seen_at)``
  (active-user calc), and ``audit_log(target_user_id, ts)`` (audit filter +
  per-user detail).

On Postgres the indexes are created ``CONCURRENTLY`` (inside an autocommit block,
so a large hosted table is indexed without a long write lock - design
"Deployment"); on SQLite (local/test) they are created inline. Reversible down
verified on a copy by the migration test harness.

Locally the same schema is produced by ``create_all`` from the updated
``app.models`` (zero-config boot); hosted uses this migration.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-09 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (index_name, table, [columns], unique)
_INDEXES: tuple[tuple[str, str, list[str], bool], ...] = (
    ("ix_users_role_status", "users", ["role", "status"], False),
    ("ix_users_created_at_id", "users", ["created_at", "id"], False),
    ("ix_users_deleted_at", "users", ["deleted_at"], False),
    ("ix_users_last_active_at", "users", ["last_active_at"], False),
    (
        "ix_sessions_user_revoked_seen",
        "sessions",
        ["user_id", "revoked_at", "last_seen_at"],
        False,
    ),
    ("ix_audit_log_target_ts", "audit_log", ["target_user_id", "ts"], False),
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_index(name: str, table: str, cols: list[str], unique: bool) -> None:
    """Create an index, CONCURRENTLY on Postgres (no long write lock)."""
    if _is_postgres():
        # CONCURRENTLY cannot run inside a transaction; autocommit_block exits
        # the migration's transaction for the duration of the statement.
        with op.get_context().autocommit_block():
            op.create_index(
                name,
                table,
                cols,
                unique=unique,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        op.create_index(name, table, cols, unique=unique)


def _drop_index(name: str, table: str) -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(
                name, table_name=table, postgresql_concurrently=True, if_exists=True
            )
    else:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    """Add admin columns + metrics_daily + admin indexes."""
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("deleted_at", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "resume_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "application_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("last_active_at", sa.String(), nullable=True))

    op.create_table(
        "metrics_daily",
        sa.Column("day_utc", sa.String(), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("day_utc", "metric"),
    )
    op.create_index(
        "ix_metrics_daily_metric_day", "metrics_daily", ["metric", "day_utc"], unique=False
    )

    for name, table, cols, unique in _INDEXES:
        _create_index(name, table, cols, unique)


def downgrade() -> None:
    """Reverse the admin schema additions."""
    for name, table, _cols, _unique in reversed(_INDEXES):
        _drop_index(name, table)

    op.drop_index("ix_metrics_daily_metric_day", table_name="metrics_daily")
    op.drop_table("metrics_daily")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("last_active_at")
        batch_op.drop_column("application_count")
        batch_op.drop_column("resume_count")
        batch_op.drop_column("deleted_at")
