"""Persist per-resume appearance: resumes.template_settings

Adds a nullable ``template_settings`` JSON column to ``resumes`` so a resume
remembers the template + customization it was created/edited with (engine, page
size, margins, spacing, fonts, accent, photo behavior — the frontend
``TemplateSettings`` shape). Previously the selection lived only in the
browser's localStorage, so it did not survive across devices, duplication, or
server-side PDF export.

Purely **additive** and **reversible**: no existing row is touched; a resume
created before the template system reads back ``NULL`` and the app falls back to
its default template. Locally the same schema is produced by ``create_all`` from
``app.models``; hosted uses this migration.

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-14 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: Union[str, Sequence[str], None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.add_column(sa.Column("template_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.drop_column("template_settings")
