"""backfill bootstrap owner and assign existing owned rows

Creates the bootstrap owner user (role=admin, status=active, email verified;
email from ``OWNER_EMAIL``, password hashed with Argon2id only if
``OWNER_PASSWORD`` is set - otherwise NULL, i.e. OAuth-only) and assigns every
pre-existing owned row (resumes, jobs, improvements, applications, api_keys)
that has no owner yet to that user (ADR-4/9, R10.5, R14.1).

The migration is:
- **Idempotent** - the owner is created only if an account with that normalized
  email does not already exist, and rows are assigned only ``WHERE user_id IS
  NULL``. Re-running is a no-op.
- **Chunked** - assignment updates run in bounded batches so a very large table
  never builds one enormous transaction.
- **Data-preserving** - it only sets ``user_id``; no owned row is created,
  deleted, or otherwise mutated.

Reverse (downgrade) un-assigns the rows it claimed (``user_id`` -> NULL) and
removes the bootstrap owner, restoring the exact pre-0004 state.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-09 00:20:00.000000
"""

import unicodedata
from datetime import datetime, timezone
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Owned tables assigned to the bootstrap owner.
_OWNED_TABLES: tuple[str, ...] = (
    "resumes",
    "jobs",
    "improvements",
    "applications",
    "api_keys",
)

# Batch size for the chunked backfill updates.
_CHUNK = 500

# Per-table primary key column used to page through the backfill.
_PK: dict[str, str] = {
    "resumes": "resume_id",
    "jobs": "job_id",
    "improvements": "request_id",
    "applications": "application_id",
    "api_keys": "provider",
}


def _normalize_email(email: str) -> str:
    """Normalize an email the same way the app does: NFKC + trim + lowercase."""
    return unicodedata.normalize("NFKC", email or "").strip().lower()


def _hash_owner_password(password: str) -> str:
    """Hash ``OWNER_PASSWORD`` with Argon2id using the configured parameters."""
    from argon2 import PasswordHasher

    from app.config import settings

    hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
    )
    return hasher.hash(password)


def _assign_owner(conn: sa.engine.Connection, table: str, owner_id: str) -> None:
    """Assign ``owner_id`` to every row of ``table`` with a NULL ``user_id``.

    Chunked and idempotent: pages through unassigned rows by primary key in
    batches of ``_CHUNK`` and updates only those still NULL.
    """
    pk = _PK[table]
    while True:
        ids = conn.execute(
            sa.text(
                f"SELECT {pk} FROM {table} WHERE user_id IS NULL LIMIT :limit"
            ),
            {"limit": _CHUNK},
        ).scalars().all()
        if not ids:
            break
        conn.execute(
            sa.text(
                f"UPDATE {table} SET user_id = :owner "
                f"WHERE {pk} IN :ids AND user_id IS NULL"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"owner": owner_id, "ids": list(ids)},
        )


def upgrade() -> None:
    """Create the bootstrap owner (if absent) and assign all owned rows to it."""
    from app.config import settings

    conn = op.get_bind()
    email = _normalize_email(settings.owner_email)
    now = datetime.now(timezone.utc).isoformat()

    owner_id = conn.execute(
        sa.text("SELECT id FROM users WHERE email = :email"), {"email": email}
    ).scalar()

    if owner_id is None:
        owner_id = str(uuid4())
        password_hash = (
            _hash_owner_password(settings.owner_password)
            if settings.owner_password
            else None
        )
        conn.execute(
            sa.text(
                "INSERT INTO users "
                "(id, email, name, password_hash, role, status, avatar_url, "
                " email_verified_at, mfa_enrolled, created_at, updated_at) "
                "VALUES "
                "(:id, :email, :name, :password_hash, 'admin', 'active', NULL, "
                " :verified_at, :mfa, :created_at, :updated_at)"
            ),
            {
                "id": owner_id,
                "email": email,
                "name": "Owner",
                "password_hash": password_hash,
                "verified_at": now,
                "mfa": False,
                "created_at": now,
                "updated_at": now,
            },
        )

    for table in _OWNED_TABLES:
        _assign_owner(conn, table, owner_id)


def downgrade() -> None:
    """Un-assign rows claimed by the owner and remove the bootstrap owner.

    Restores the pre-0004 state: every owned row this migration assigned goes
    back to ``user_id = NULL`` and the bootstrap owner row is deleted (its
    dependent sessions/tokens/identities, if any, cascade). Owned rows
    themselves are preserved.
    """
    from app.config import settings

    conn = op.get_bind()
    email = _normalize_email(settings.owner_email)

    owner_id = conn.execute(
        sa.text("SELECT id FROM users WHERE email = :email"), {"email": email}
    ).scalar()
    if owner_id is None:
        return

    for table in _OWNED_TABLES:
        conn.execute(
            sa.text(f"UPDATE {table} SET user_id = NULL WHERE user_id = :owner"),
            {"owner": owner_id},
        )
    conn.execute(sa.text("DELETE FROM users WHERE id = :owner"), {"owner": owner_id})
