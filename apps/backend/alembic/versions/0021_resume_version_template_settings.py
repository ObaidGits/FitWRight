"""Template-aware version history: resume_versions.template_settings

Adds a nullable ``template_settings`` JSON column to ``resume_versions`` so each
snapshot also records the resume's appearance at capture time. Restoring a
version then restores BOTH content and template (audit Bug #3), and the pre-
restore "reversible" snapshot preserves the current template too.

Purely **additive** and **reversible**: existing snapshots read back ``NULL``
(restore leaves the current template unchanged for those, preserving prior
behavior). Locally the same schema is produced by ``create_all``; hosted uses
this migration.

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-14 13:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: Union[str, Sequence[str], None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("resume_versions", schema=None) as batch_op:
        batch_op.add_column(sa.Column("template_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("resume_versions", schema=None) as batch_op:
        batch_op.drop_column("template_settings")
