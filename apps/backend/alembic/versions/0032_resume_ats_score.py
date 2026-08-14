"""Keep the match score, so the library can show which version scored best.

The ATS score was computed during tailoring, shown once on the review screen, and
then discarded. Nothing persisted it and the list endpoint could not return it, so
a user with a dozen tailored variants had no way to see which one actually matched
its job best - the one number most worth comparing was the one number the app
forgot.

Deliberately a single nullable float, not a table:

* Nullable, because every resume that already exists has no score and inventing
  one would be a lie. A master resume has no job to be scored against at all, so
  for those it stays NULL permanently - "not applicable", not "zero".
* One number, not the full breakdown. Sub-scores, missing keywords and
  recommendations stay a per-request computation; storing them would duplicate a
  derived artifact that changes whenever the scoring rules do. The overall score
  is kept only because it is a fact about a decision the user already made (they
  accepted THIS resume for THAT job) and cannot be recomputed later without the
  job it was tailored against.

Additive and reversible: no existing column changes and no data is rewritten.

Revision ID: 0032
Revises: 0031
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, Sequence[str], None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("resumes", sa.Column("ats_score", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("resumes", "ats_score")
