"""admin invite lifecycle provenance and history index

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-25 00:00:01.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add distinct revocation provenance without rewriting redemption data."""
    op.add_column("admin_invites", sa.Column("revoked_at", sa.String(), nullable=True))
    op.add_column("admin_invites", sa.Column("revoked_by", sa.String(), nullable=True))
    op.add_column("admin_invites", sa.Column("revoke_reason", sa.String(), nullable=True))
    op.create_index(
        "ix_admin_invites_created_at", "admin_invites", ["created_at"], unique=False
    )


def downgrade() -> None:
    """Remove lifecycle provenance fields and their history-order index."""
    op.drop_index("ix_admin_invites_created_at", table_name="admin_invites")
    op.drop_column("admin_invites", "revoke_reason")
    op.drop_column("admin_invites", "revoked_by")
    op.drop_column("admin_invites", "revoked_at")
