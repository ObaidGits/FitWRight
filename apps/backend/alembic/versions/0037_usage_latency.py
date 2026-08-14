"""Per-request AI latency on the usage ledger.

Revision ID: 0037
Revises: 0036

Needed for per-channel p95 latency (task 5.1). The existing anonymous metrics keep only
a SUM of latency, which yields an average - and an average hides exactly the problem an
operator needs to see, because a channel that is usually fast and occasionally awful
looks fine on the mean.

The value is REQUEST-level: total time spent talking to providers while serving one
request, attributed to the channel that served it. For the common single-call request
that is the call's latency; for a multi-call feature it is the sum, which is the honest
answer to "how slow does this channel make my product feel".

Nullable, because rows written before this column existed have no value and inventing
one would corrupt the percentile it exists to compute.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_usage_ledger",
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_usage_ledger", "latency_ms")
