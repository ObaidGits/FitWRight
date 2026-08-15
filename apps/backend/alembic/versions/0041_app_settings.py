"""Database-backed application settings, editable from the admin panel.

Revision ID: 0041
Revises: 0040

Every setting in this app has been an environment variable, which means every change -
a business address on a receipt, an SMTP host, which sender a welcome email uses -
required a redeploy. That is the wrong shape for operational configuration, and it is
why "let the admin configure mail" could not be built as a small feature: there was
nowhere to put the answer.

ONE TABLE, NAMESPACED KEYS, JSON VALUES. Not a column per setting: a schema migration
per new setting is exactly the friction this removes. Keys are namespaced strings
(``billing.seller``, ``mail.transport``, ``mail.events``) and the value is a JSON object
validated by the module that owns it, so the shape lives in code where it can be
reviewed while the values live in the database where they can be changed.

SECRETS ARE ENCRYPTED, NOT STORED AS JSON. An SMTP password inside the value blob would
be readable by anything that can read the row, and would land in any dump or log that
echoed settings. ``secret_ciphertext`` holds the one encrypted field a setting may need,
using the same envelope as stored provider API keys, and the JSON value carries only
non-secret fields. A setting with a secret therefore cannot be logged whole by accident.

ENV VARS STILL WIN WHERE THEY ALREADY EXIST. A row here is a FALLBACK for settings that
also have an env var, never an override: an operator who set SMTP in the environment and
then sees the panel silently ignore it has been lied to by the UI. New settings that
never had an env var (seller details) live only here.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: Union[str, Sequence[str], None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        # Namespaced key: "billing.seller", "mail.transport", "mail.events".
        sa.Column("key", sa.String(length=100), primary_key=True),
        # Non-secret fields, validated by the module that owns this key.
        sa.Column("value", sa.JSON(), nullable=False),
        # The single encrypted field this setting may need (e.g. an SMTP password).
        # Kept OUT of `value` so a setting cannot be logged or dumped whole with its
        # secret inside it.
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        # Who last changed it. Configuration changes are the first thing looked at
        # after "it worked yesterday", so the trail matters as much as the value.
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
