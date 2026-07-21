"""Professional Profile System: profiles + profile_versions

Adds the document-oriented professional profile
(docs/architecture/PROFILE_SYSTEM_PLAN.md):

- ``profiles`` - one canonical ``ProfileData`` JSON document per user
  (``data``), with a cached ``completeness`` score and a ``version``
  optimistic-concurrency token. ``UNIQUE(user_id)`` enforces one profile per
  user (single-source-of-truth invariant).
- ``profile_versions`` - immutable, gzip-compressed, content-hash-deduped
  snapshots of ``profiles.data`` scoped to ``(user_id, profile_id)`` - mirrors
  ``resume_versions``.

Purely **additive** and **reversible**: no existing table is touched, no
backfill runs in the migration (profiles are created lazily on first
``/profile`` load from the user's master resume). Locally the same schema is
produced by ``create_all`` from ``app.models``; hosted uses this migration. On
Postgres the keyset index is created ``CONCURRENTLY`` (no long write lock).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-13 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILES = "profiles"
_VERSIONS = "profile_versions"
_VERSIONS_INDEX = "ix_profile_versions_scope_created"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        _PROFILES,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("completeness", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # One profile per user.
    op.create_index("ux_profiles_user_id", _PROFILES, ["user_id"], unique=True)

    op.create_table(
        _VERSIONS,
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("data_gz", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_profile_versions_user_id", _VERSIONS, ["user_id"], unique=False
    )

    cols = ["user_id", "profile_id", "created_at", "id"]
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.create_index(
                _VERSIONS_INDEX, _VERSIONS, cols, unique=False,
                postgresql_concurrently=True, if_not_exists=True,
            )
    else:
        op.create_index(_VERSIONS_INDEX, _VERSIONS, cols, unique=False)


def downgrade() -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(
                _VERSIONS_INDEX, table_name=_VERSIONS,
                postgresql_concurrently=True, if_exists=True,
            )
    else:
        op.drop_index(_VERSIONS_INDEX, table_name=_VERSIONS)
    op.drop_index("ix_profile_versions_user_id", table_name=_VERSIONS)
    op.drop_table(_VERSIONS)
    op.drop_index("ux_profiles_user_id", table_name=_PROFILES)
    op.drop_table(_PROFILES)
