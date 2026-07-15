"""Photo System: canonical profile-image metadata on users

Adds metadata-only columns for the canonical profile-image master (Photo
System). Only metadata is stored — never binary; the master itself lives in the
object store (Cloudinary/local) keyed by ``avatar_key`` and delivered via
``avatar_url``. Responsive variants are CDN URL transforms of that one master
(no extra rows, no re-uploads).

- ``avatar_checksum`` — SHA-256 of the *original* upload bytes; content-addressed
  dedup so re-uploading the same file is a no-op (no wasted CDN write).
- ``avatar_width`` / ``avatar_height`` / ``avatar_format`` / ``avatar_bytes`` —
  master dimensions/format for CLS-free layout + responsive sizing.
- ``avatar_dominant_color`` — for skeletons / theme accents.
- ``avatar_updated_at`` — cache-busting / provenance.

Purely **additive** and **reversible**: no existing row is touched, all columns
are nullable with no backfill (a pre-Photo-System avatar keeps NULLs until its
next upload). Locally the same schema is produced by ``create_all`` from
``app.models``; hosted uses this migration.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-13 16:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: Union[str, Sequence[str], None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    ("avatar_width", sa.Integer()),
    ("avatar_height", sa.Integer()),
    ("avatar_checksum", sa.String()),
    ("avatar_format", sa.String()),
    ("avatar_bytes", sa.Integer()),
    ("avatar_dominant_color", sa.String()),
    ("avatar_updated_at", sa.String()),
)


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        for name, type_ in _COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        for name, _ in reversed(_COLUMNS):
            batch_op.drop_column(name)
