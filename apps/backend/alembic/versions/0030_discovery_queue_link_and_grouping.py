"""Link discovered jobs to the apply queue, and group cross-board duplicates.

Two columns on ``discovery_results``, in one revision because both exist to make
the feed behave like one coherent list rather than a pile of rows:

* ``job_id`` - the job-description row created when the user saves a job, so the
  feed remembers which queue entry belongs to it. Without it, un-saving a job
  could only find its application by fuzzy company/role matching, which is a
  bad way to decide what to delete.
* ``group_fingerprint`` - a URL-free identity (company + title + location) so the
  same posting harvested from LinkedIn, Indeed and Glassdoor collapses into one
  row. The existing ``fingerprint`` includes the URL by design, which is right
  for "is this the same listing" and wrong for "is this the same job".

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, Sequence[str], None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discovery_results", sa.Column("job_id", sa.String(), nullable=True))
    op.add_column(
        "discovery_results", sa.Column("group_fingerprint", sa.String(), nullable=True)
    )
    # Indexed because the feed groups on it on every read.
    op.create_index(
        "ix_discovery_results_group_fingerprint",
        "discovery_results",
        ["group_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_results_group_fingerprint", table_name="discovery_results")
    op.drop_column("discovery_results", "group_fingerprint")
    op.drop_column("discovery_results", "job_id")
