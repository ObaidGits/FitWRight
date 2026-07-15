"""Profile public sharing: public_slug + visibility on profiles (P7).

Adds the two columns that back the Public Profile Platform
(docs/architecture/PROFILE_SYSTEM_PLAN.md §P7):

- ``profiles.public_slug`` — the globally-unique URL segment for a shared
  profile (nullable until first publish); a partial-safe UNIQUE index enforces
  uniqueness and powers the fast, JSON-free anonymous lookup by slug.
- ``profiles.visibility`` — ``private`` (default) | ``unlisted`` | ``public``;
  the authoritative publish gate for the anonymous ``/public/profiles/{slug}``
  endpoint.

Purely **additive** and **reversible**: no existing row is touched, both columns
default to the safe ``private``/``NULL`` state. Locally the same schema is
produced by ``create_all`` from ``app.models``; hosted uses this migration. On
Postgres the unique slug index is created ``CONCURRENTLY`` (no long write lock).

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-13 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: Union[str, Sequence[str], None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILES = "profiles"
_SLUG_INDEX = "ux_profiles_public_slug"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column(_PROFILES, sa.Column("public_slug", sa.String(), nullable=True))
    op.add_column(
        _PROFILES,
        sa.Column(
            "visibility", sa.String(), nullable=False, server_default="private"
        ),
    )
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.create_index(
                _SLUG_INDEX,
                _PROFILES,
                ["public_slug"],
                unique=True,
                postgresql_concurrently=True,
                if_not_exists=True,
            )
    else:
        op.create_index(_SLUG_INDEX, _PROFILES, ["public_slug"], unique=True)


def downgrade() -> None:
    if _is_postgres():
        with op.get_context().autocommit_block():
            op.drop_index(
                _SLUG_INDEX,
                table_name=_PROFILES,
                postgresql_concurrently=True,
                if_exists=True,
            )
    else:
        op.drop_index(_SLUG_INDEX, table_name=_PROFILES)
    op.drop_column(_PROFILES, "visibility")
    op.drop_column(_PROFILES, "public_slug")
