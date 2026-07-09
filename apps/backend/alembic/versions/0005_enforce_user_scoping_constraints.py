"""enforce user-scoping constraints

Locks in the user-scoping guarantees once every owned row has an owner
(migration 0004 backfilled them), R10.4 / R14.2:

- ``user_id`` becomes NOT NULL on resumes, jobs, improvements, applications.
- The global single-master index becomes the **per-user** partial-unique index
  ``ux_resumes_single_master (user_id, is_master) WHERE is_master = 1``
  (Property 2).
- Applications dedupe on ``(user_id, job_id, resume_id)`` and the unique
  constraint moves from ``(job_id, resume_id)`` to ``(user_id, job_id,
  resume_id)``.
- ``api_keys`` primary key moves from ``(provider)`` to ``(user_id, provider)``
  so keys are per-user (R10.1/10.6).

Every step is reversible (downgrade restores the pre-0005 shape). Constraint and
primary-key changes use ``batch_alter_table`` (SQLite copy-and-move) or an
explicit table rebuild (the ``api_keys`` PK change), so the chain runs on both
SQLite (local) and PostgreSQL (hosted).

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-09 00:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Partial-index predicate for the single-master invariant. SQLite stores
# booleans as integers (``is_master = 1``), but Postgres is strictly typed and
# rejects ``boolean = integer`` — it needs ``is_master = true``. Using a single
# ``is_master = 1`` for both dialects fails on Postgres with
# "operator does not exist: boolean = integer", so the predicate is
# dialect-specific (mirrors ``Resume.__table_args__`` in ``app/models.py``).
_MASTER_WHERE_SQLITE = "is_master = 1"
_MASTER_WHERE_PG = "is_master = true"


def upgrade() -> None:
    """Enforce NOT NULL + per-user uniqueness and the per-user api_keys PK."""
    # -- resumes: NOT NULL + per-user single-master partial-unique index ------
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.drop_index("ux_resumes_single_master")
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch_op.create_index(
            "ux_resumes_single_master",
            ["user_id", "is_master"],
            unique=True,
            sqlite_where=sa.text(_MASTER_WHERE_SQLITE),
            postgresql_where=sa.text(_MASTER_WHERE_PG),
        )

    # -- jobs / improvements: NOT NULL ----------------------------------------
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
    with op.batch_alter_table("improvements", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)

    # -- applications: dedupe, NOT NULL, move the unique constraint -----------
    # Collapse any exact (user_id, job_id, resume_id) duplicates first so the
    # new unique constraint can be created. Keeps the lexically-smallest id.
    op.execute(
        "DELETE FROM applications WHERE application_id NOT IN ("
        " SELECT keep FROM ("
        "  SELECT MIN(application_id) AS keep FROM applications"
        "  GROUP BY user_id, job_id, resume_id"
        " ) AS keepers"
        ")"
    )
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint("uq_application_job_resume", type_="unique")
        batch_op.create_unique_constraint(
            "uq_application_user_job_resume", ["user_id", "job_id", "resume_id"]
        )

    # -- api_keys: primary key (provider) -> (user_id, provider) --------------
    op.create_table(
        "api_keys_tmp",
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_api_keys_user_id_users",
        ),
        sa.PrimaryKeyConstraint("user_id", "provider"),
    )
    op.execute(
        "INSERT INTO api_keys_tmp (user_id, provider, ciphertext, updated_at) "
        "SELECT user_id, provider, ciphertext, updated_at FROM api_keys"
    )
    op.drop_table("api_keys")
    op.rename_table("api_keys_tmp", "api_keys")
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], unique=False)


def downgrade() -> None:
    """Reverse enforcement: restore nullable ``user_id`` and the prior keys."""
    # -- api_keys: (user_id, provider) -> (provider) --------------------------
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.create_table(
        "api_keys_tmp",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE",
            name="fk_api_keys_user_id_users",
        ),
        sa.PrimaryKeyConstraint("provider"),
    )
    op.execute(
        "INSERT INTO api_keys_tmp (provider, ciphertext, updated_at, user_id) "
        "SELECT provider, ciphertext, updated_at, user_id FROM api_keys"
    )
    op.drop_table("api_keys")
    op.rename_table("api_keys_tmp", "api_keys")
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"], unique=False)

    # -- applications: restore (job_id, resume_id) unique + nullable ----------
    with op.batch_alter_table("applications", schema=None) as batch_op:
        batch_op.drop_constraint("uq_application_user_job_resume", type_="unique")
        batch_op.create_unique_constraint(
            "uq_application_job_resume", ["job_id", "resume_id"]
        )
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)

    # -- jobs / improvements: nullable ----------------------------------------
    with op.batch_alter_table("improvements", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)

    # -- resumes: restore global single-master index + nullable ---------------
    with op.batch_alter_table("resumes", schema=None) as batch_op:
        batch_op.drop_index("ux_resumes_single_master")
        batch_op.alter_column("user_id", existing_type=sa.String(), nullable=True)
        batch_op.create_index(
            "ux_resumes_single_master",
            ["is_master"],
            unique=True,
            sqlite_where=sa.text(_MASTER_WHERE_SQLITE),
            postgresql_where=sa.text(_MASTER_WHERE_PG),
        )
