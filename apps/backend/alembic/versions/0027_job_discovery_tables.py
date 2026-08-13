"""job discovery tables (discovery_cache + site_recipes)

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-11 00:00:01.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create discovery_cache and site_recipes tables."""
    # Content-addressed search-result cache (design §6, Req 6.3)
    op.create_table(
        "discovery_cache",
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("expires_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("cache_key"),
    )

    # Persisted custom-site scraping recipes (design §4, Req 4.1)
    op.create_table(
        "site_recipes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("search_url_template", sa.Text(), nullable=False),
        sa.Column("schema", sa.JSON(), nullable=False),
        sa.Column(
            "fetch_mode",
            sa.String(length=16),
            nullable=False,
            server_default="http",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "slug", name="uq_site_recipes_user_slug"),
    )
    op.create_index(
        "ix_site_recipes_user_id",
        "site_recipes",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop discovery tables."""
    op.drop_index("ix_site_recipes_user_id", table_name="site_recipes")
    op.drop_table("site_recipes")
    op.drop_table("discovery_cache")
