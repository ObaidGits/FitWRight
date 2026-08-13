"""application field registry + submission audit trail

Adds the two pieces of schema Phases 2 and 5 share, in one revision so the
phases never contend for a migration number:

* ``application_fields`` - the learning loop's store. One row per question an
  application form asked, holding either an answer or a pointer at the Profile
  field that already answers it.
* three columns on ``applications`` - what was actually submitted, so a past
  application can be reviewed and callback rates compared per resume version.

Revision ID: 0029
Revises: 0028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, Sequence[str], None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "application_fields",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("label_normalized", sa.String(), nullable=False),
        sa.Column("synonyms", sa.JSON(), nullable=True),
        sa.Column("field_type", sa.String(), nullable=False, server_default="text"),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("profile_path", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False, server_default="global"),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="needs_answer"),
        sa.Column("source", sa.String(), nullable=False, server_default="learned"),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.String(), nullable=True),
        sa.Column("last_seen_url", sa.String(), nullable=True),
        sa.Column("last_seen_ats", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id",
            "label_normalized",
            "scope",
            "company",
            name="uq_appfield_user_label_scope",
        ),
    )
    op.create_index("ix_application_fields_user_id", "application_fields", ["user_id"])
    op.create_index(
        "ix_application_fields_label_normalized", "application_fields", ["label_normalized"]
    )
    op.create_index("ix_application_fields_status", "application_fields", ["status"])
    op.create_index("ix_application_fields_company", "application_fields", ["company"])
    op.create_index("ix_appfield_user_status", "application_fields", ["user_id", "status"])

    # Nullable with no default: an application submitted before this migration
    # genuinely has no record of what was sent, and inventing one would be worse
    # than admitting the gap.
    op.add_column("applications", sa.Column("submitted_answers", sa.JSON(), nullable=True))
    op.add_column(
        "applications", sa.Column("submitted_resume_version_id", sa.String(), nullable=True)
    )
    op.add_column("applications", sa.Column("submitted_via", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "submitted_via")
    op.drop_column("applications", "submitted_resume_version_id")
    op.drop_column("applications", "submitted_answers")

    op.drop_index("ix_appfield_user_status", table_name="application_fields")
    op.drop_index("ix_application_fields_company", table_name="application_fields")
    op.drop_index("ix_application_fields_status", table_name="application_fields")
    op.drop_index("ix_application_fields_label_normalized", table_name="application_fields")
    op.drop_index("ix_application_fields_user_id", table_name="application_fields")
    op.drop_table("application_fields")
