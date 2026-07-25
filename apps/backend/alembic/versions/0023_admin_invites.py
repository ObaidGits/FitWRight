"""admin invite tokens (secure admin signup - Option B)

Adds the ``admin_invites`` table backing the invite-based admin-signup flow. An
existing admin issues a single-use, TTL-bound, email-bound invitation; only the
``sha256`` of the random token is stored (never the raw token), mirroring
``email_verification_tokens`` / ``email_change_tokens``. Redeeming the invite at
``/auth/signup`` creates an account with the invite's ``role`` (proving inbox
control), and single-use is enforced atomically via ``used_at``.

Locally the same table is created by ``create_all`` from
``app.models.AdminInvite`` (zero-config boot); hosted uses this migration.

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-25 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: Union[str, Sequence[str], None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``admin_invites`` table."""
    op.create_table(
        "admin_invites",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.Column("used_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_admin_invites_token_hash", "admin_invites", ["token_hash"], unique=True
    )
    op.create_index("ix_admin_invites_email", "admin_invites", ["email"], unique=False)
    op.create_index(
        "ix_admin_invites_expires_at", "admin_invites", ["expires_at"], unique=False
    )


def downgrade() -> None:
    """Drop the ``admin_invites`` table (reverse creation)."""
    op.drop_index("ix_admin_invites_expires_at", table_name="admin_invites")
    op.drop_index("ix_admin_invites_email", table_name="admin_invites")
    op.drop_index("ix_admin_invites_token_hash", table_name="admin_invites")
    op.drop_table("admin_invites")
