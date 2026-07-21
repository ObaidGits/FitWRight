"""P2 Admin: index-usable search + active-user range index (audit H2/M1 fixes)

Adds the indexes that make the admin user search and active-user calculation
truly index-served at scale (Requirements 4.2, 11.1, 11.2):

- **Postgres** (the hosted target): prefix `LIKE 'x%'` on a non-C collation only
  uses a btree index when it is declared with the ``text_pattern_ops`` operator
  class. So we add ``ix_users_email_pattern (email text_pattern_ops)`` and
  ``ix_users_name_lower_pattern (lower(name) text_pattern_ops)`` - the email is
  matched on the bare (lowercase-normalized) column and the name on
  ``lower(name)``. Plus ``ix_sessions_last_seen_at (last_seen_at)`` so the
  active-user range filter (`last_seen_at >= cutoff`) is an index range scan
  rather than a full table scan. All created ``CONCURRENTLY`` (no long lock).
- **SQLite** (local/test): the ``text_pattern_ops`` opclass does not exist, so we
  create the plain expression/column indexes (``lower(name)`` + ``last_seen_at``);
  email prefix is already served by ``ux_users_email``.

These mirror the model indexes in ``app.models`` (create_all locally); hosted
uses this migration. Reversible; the down path drops them (CONCURRENTLY on PG).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-09 14:00:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        # CONCURRENTLY cannot run inside a transaction.
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_email_pattern "
                "ON users (email text_pattern_ops)"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_users_name_lower_pattern "
                "ON users (lower(name) text_pattern_ops)"
            )
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_sessions_last_seen_at "
                "ON sessions (last_seen_at)"
            )
    else:
        op.execute("CREATE INDEX IF NOT EXISTS ix_users_name_lower ON users (lower(name))")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_sessions_last_seen_at ON sessions (last_seen_at)"
        )


def downgrade() -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_sessions_last_seen_at")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_name_lower_pattern")
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_users_email_pattern")
    else:
        op.execute("DROP INDEX IF EXISTS ix_sessions_last_seen_at")
        op.execute("DROP INDEX IF EXISTS ix_users_name_lower")
