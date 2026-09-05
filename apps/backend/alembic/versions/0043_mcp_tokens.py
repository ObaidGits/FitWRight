"""MCP integration: the mcp_tokens bearer-token table.

Revision ID: 0043
Revises: 0042

Task 2 of the MCP integration spec (.superpowers/sdd/2026-09-06-mcp-integration/):
a per-user, revocable bearer token for the FastMCP mount. The trust model copies
``sessions`` (Task 2 plan): only ``sha256(raw)`` is stored in ``token_hash``
(the raw ``fw_``-prefixed token exists only in the client's config), a non-null
``revoked_at`` kills the token, and ``expires_at`` is optional because the
documented default TTL is 0 = never expires. ``last_used_at`` is a best-effort,
throttled "recently used" stamp - not authoritative, hence no index on it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: Union[str, Sequence[str], None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcp_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.String(), nullable=True),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.Column("revoked_at", sa.String(), nullable=True),
    )
    op.create_index("ux_mcp_tokens_token_hash", "mcp_tokens", ["token_hash"], unique=True)
    op.create_index("ix_mcp_tokens_user_id", "mcp_tokens", ["user_id"])
    op.create_index("ix_mcp_tokens_user_revoked", "mcp_tokens", ["user_id", "revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_mcp_tokens_user_revoked", table_name="mcp_tokens")
    op.drop_index("ix_mcp_tokens_user_id", table_name="mcp_tokens")
    op.drop_index("ux_mcp_tokens_token_hash", table_name="mcp_tokens")
    op.drop_table("mcp_tokens")
