"""Channel credentials live on the channel row.

Revision ID: 0036
Revises: 0035

WHY THIS EXISTS - a design decision that was wrong.

Channel credentials were originally put in the existing encrypted ``api_keys`` table
under a reserved owner id (``__ai_channel__``), on the reasoning that one encryption
path is safer than two. The reasoning was sound; the design was impossible.
``api_keys.user_id`` is a FOREIGN KEY to ``users.id``, so a reserved owner that is not
a real user violates the constraint and every insert fails.

The consequence was not subtle: storing a channel credential NEVER WORKED. Creating a
channel with a key through the admin API raised a foreign-key error, which means the
channels feature could not have served a single request. It surfaced only when the
env-key import exercised the same path against a database with foreign keys enforced.

The alternative to this migration was inserting a fake user row to satisfy the
constraint. That was rejected: a non-user in the users table would leak into user
counts, admin lists, retention sweeps and anything else that trusts that table, to
save one column here.

A credential belongs to its channel anyway - same lifecycle (deleting the channel
deletes the key, with no orphan to clean up), same authorisation boundary. The
ciphertext continues to use ``app.crypto``, so there is still exactly one encryption
implementation; only the storage location changed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: self-hosted channels (Ollama, openai_compatible) legitimately have no
    # credential, and a NOT NULL column would force a meaningless empty string.
    op.add_column(
        "ai_channels",
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_channels", "api_key_ciphertext")
