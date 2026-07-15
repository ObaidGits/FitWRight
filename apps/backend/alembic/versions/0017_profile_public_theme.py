"""Profile public theme: profiles.public_theme (P-final).

Adds the selectable theme for a shared public profile page
(minimal | modern | developer). Purely additive and reversible; defaults to
``minimal`` so existing published profiles are unaffected. Locally the same
schema is produced by ``create_all``; hosted uses this migration.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-13 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PROFILES = "profiles"


def upgrade() -> None:
    op.add_column(
        _PROFILES,
        sa.Column("public_theme", sa.String(), nullable=False, server_default="minimal"),
    )


def downgrade() -> None:
    op.drop_column(_PROFILES, "public_theme")
