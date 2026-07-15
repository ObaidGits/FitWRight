"""P3 Productivity: search_documents + FTS (global search — R7, R8)

Adds the user-scoped ``search_documents`` store (design §C) plus the dialect-
specific full-text acceleration:

- **Postgres** (hosted): a GIN expression index on
  ``to_tsvector('english', title || ' ' || body)`` so ``@@ plainto_tsquery`` is
  index-served, created ``CONCURRENTLY``.
- **SQLite** (local/test): an ``search_fts`` external-content FTS5 virtual table
  + INSERT/UPDATE/DELETE triggers that keep it in lock-step with
  ``search_documents`` (matches the DDL hook used by ``create_all`` locally).

The indexer only ever writes ``search_documents``; the FTS/tsvector layer is
maintained by triggers (SQLite) or computed at query time over the GIN index
(Postgres). Reversible.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-09 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SQLITE_FTS_DDL = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
    "title, body, content='search_documents', content_rowid='rowid', tokenize='unicode61')",
    "CREATE TRIGGER IF NOT EXISTS search_documents_ai AFTER INSERT ON search_documents BEGIN "
    "INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body); END",
    "CREATE TRIGGER IF NOT EXISTS search_documents_ad AFTER DELETE ON search_documents BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body); END",
    "CREATE TRIGGER IF NOT EXISTS search_documents_au AFTER UPDATE ON search_documents BEGIN "
    "INSERT INTO search_fts(search_fts, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body); "
    "INSERT INTO search_fts(rowid, title, body) VALUES (new.rowid, new.title, new.body); END",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "search_documents",
        sa.Column("node_type", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("node_type", "node_id"),
    )
    op.create_index("ix_search_documents_user", "search_documents", ["user_id"], unique=False)
    op.create_index(
        "ix_search_documents_user_updated", "search_documents", ["user_id", "updated_at"], unique=False
    )

    if _is_postgres():
        with op.get_context().autocommit_block():
            op.execute(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_search_documents_fts "
                "ON search_documents USING gin "
                "(to_tsvector('english', title || ' ' || body))"
            )
    else:
        for stmt in _SQLITE_FTS_DDL:
            op.execute(stmt)


def downgrade() -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_search_documents_fts")
    else:
        op.execute("DROP TRIGGER IF EXISTS search_documents_au")
        op.execute("DROP TRIGGER IF EXISTS search_documents_ad")
        op.execute("DROP TRIGGER IF EXISTS search_documents_ai")
        op.execute("DROP TABLE IF EXISTS search_fts")
    op.drop_index("ix_search_documents_user_updated", table_name="search_documents")
    op.drop_index("ix_search_documents_user", table_name="search_documents")
    op.drop_table("search_documents")
