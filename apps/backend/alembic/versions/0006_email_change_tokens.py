"""email-change tokens (verify-before-switch email change)

Adds the ``email_change_tokens`` table backing the verify-before-switch email
change flow (R7.4, Task 6.2). It mirrors ``email_verification_tokens`` (hashed
single-use TTL token stored as ``sha256`` PK) but carries the pending
``new_email`` so the account's primary email only switches once the new address
is confirmed via the emailed link.

Locally the same table is created by ``create_all`` from
``app.models.EmailChangeToken`` (zero-config boot); hosted uses this migration.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-09 01:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the ``email_change_tokens`` table."""
    op.create_table(
        "email_change_tokens",
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("new_email", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=False),
        sa.Column("used_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index(
        "ix_email_change_tokens_user_id",
        "email_change_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_email_change_tokens_expires_at",
        "email_change_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the ``email_change_tokens`` table (reverse creation)."""
    op.drop_index(
        "ix_email_change_tokens_expires_at", table_name="email_change_tokens"
    )
    op.drop_index(
        "ix_email_change_tokens_user_id", table_name="email_change_tokens"
    )
    op.drop_table("email_change_tokens")
