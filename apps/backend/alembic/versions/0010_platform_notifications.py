"""P3 Productivity: outbox + notifications platform (R4-R6, R16)

Adds the shared event platform + notification tables (design §Platform/§B):

- ``outbox`` - transactional domain-event log consumed at-least-once + idempotent
  by id (notifier + search indexer). Index ``(processed_at, created_at, id)``
  is the consumer cursor; ``dead_at`` parks poison rows (DLQ).
- ``notifications`` - user-scoped, content-safe items with category/priority/
  group_key + a unique ``(user_id, dedupe_key)`` for scheduled-notification
  idempotency.
- ``notification_prefs`` - per-user, per-category in_app/email toggles.
- ``user_unread_counts`` - denormalized O(1) unread badge + digest setting.

Postgres indexes are created ``CONCURRENTLY``; SQLite inline. Locally the same
schema is produced by ``create_all``; hosted uses this migration. Reversible.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-09 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _create_index(name: str, table: str, cols: list[str], *, unique: bool = False, where: str | None = None) -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.create_index(
                name, table, cols, unique=unique,
                postgresql_concurrently=True, if_not_exists=True,
                postgresql_where=sa.text(where) if where else None,
            )
    else:
        op.create_index(
            name, table, cols, unique=unique,
            sqlite_where=sa.text(where) if where else None,
        )


def _drop_index(name: str, table: str) -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
    else:
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("processed_at", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dead_at", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="system"),
        sa.Column("priority", sa.String(), nullable=False, server_default="normal"),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("node_type", sa.String(), nullable=True),
        sa.Column("node_id", sa.String(), nullable=True),
        sa.Column("group_key", sa.String(), nullable=True),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("dismissed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("emailed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "notification_prefs",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("in_app", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "category"),
    )
    op.create_table(
        "user_unread_counts",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("unread", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("digest", sa.String(), nullable=False, server_default="off"),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    _create_index("ix_outbox_processed_created", "outbox", ["processed_at", "created_at", "id"])
    _create_index("ix_outbox_dead_at", "outbox", ["dead_at"])
    _create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at", "id"])
    _create_index("ix_notifications_user_read", "notifications", ["user_id", "read", "dismissed"])
    _create_index(
        "ux_notifications_user_dedupe", "notifications", ["user_id", "dedupe_key"],
        unique=True, where="dedupe_key IS NOT NULL",
    )


def downgrade() -> None:
    _drop_index("ux_notifications_user_dedupe", "notifications")
    _drop_index("ix_notifications_user_read", "notifications")
    _drop_index("ix_notifications_user_created", "notifications")
    _drop_index("ix_outbox_dead_at", "outbox")
    _drop_index("ix_outbox_processed_created", "outbox")
    op.drop_table("user_unread_counts")
    op.drop_table("notification_prefs")
    op.drop_table("notifications")
    op.drop_table("outbox")
