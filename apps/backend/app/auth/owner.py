"""Bootstrap owner resolution for single-user / local zero-config mode (R14.1/14.3).

On hosted deployments the Alembic chain (migration ``0004``) creates the
bootstrap owner and assigns every pre-existing owned row to it. Local dev uses
the ORM ``create_all`` path (no migrations), so this module reproduces the same
outcome at runtime:

- :func:`ensure_owner` - idempotently creates the owner user (``role=admin``,
  ``status=active``, email verified; email normalized from ``OWNER_EMAIL``,
  password hashed only if ``OWNER_PASSWORD`` is set) **and** backfills every
  owned row that still has ``user_id IS NULL`` to the owner. Mirrors ``0004``
  exactly so single-user local behaves identically to the enforced hosted shape.
- :func:`resolve_owner_id_sync` - the synchronous path used by the encrypted
  api-key hot read (``llm.py`` -> ``resolve_api_key``), which runs off the sync
  engine and cannot ``await``.

Both are idempotent and cache the resolved owner id **on the ``Database``
instance** (so isolated per-test databases never see a stale id from another
database). The backfill is guarded by a per-instance flag so it scans owned
tables at most once per process, and updates only ``WHERE user_id IS NULL`` so
it is zero-data-loss and safe to call repeatedly.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, text, update

from app.config import settings
from app.models import ApiKey, Application, Improvement, Job, Resume, User

logger = logging.getLogger(__name__)

__all__ = [
    "normalize_email",
    "ensure_owner",
    "resolve_owner_id_sync",
    "OWNED_BACKFILL_MODELS",
]

# Owned models backfilled to the owner locally (mirrors migration 0004's tables).
OWNED_BACKFILL_MODELS: tuple[type, ...] = (Resume, Job, Improvement, Application, ApiKey)

_OWNER_ID_ATTR = "_owner_user_id"
_BACKFILLED_ATTR = "_owner_backfilled"


def normalize_email(email: str) -> str:
    """Normalize an email the way the app + migration 0004 do: NFKC+trim+lower."""
    return unicodedata.normalize("NFKC", email or "").strip().lower()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_owner_password(password: str) -> str:
    """Argon2id-hash ``OWNER_PASSWORD`` with the configured parameters."""
    from argon2 import PasswordHasher

    hasher = PasswordHasher(
        time_cost=settings.argon2_time_cost,
        memory_cost=settings.argon2_memory_cost,
        parallelism=settings.argon2_parallelism,
    )
    return hasher.hash(password)


def _owner_insert_values(owner_id: str, email: str) -> dict:
    now = _now_iso()
    return {
        "id": owner_id,
        "email": email,
        "name": "Owner",
        "password_hash": (
            _hash_owner_password(settings.owner_password)
            if settings.owner_password
            else None
        ),
        "role": "admin",
        "status": "active",
        "avatar_url": None,
        "email_verified_at": now,
        "mfa_enrolled": False,
        "created_at": now,
        "updated_at": now,
    }


async def ensure_owner(db=None) -> str:
    """Return the bootstrap owner's id, creating it + backfilling owned rows once.

    Idempotent: the owner is created only when the normalized ``OWNER_EMAIL`` is
    absent, and the one-time backfill updates only rows still owned by nobody
    (``user_id IS NULL``). The resolved id is cached on the ``Database`` instance.
    """
    if db is None:
        from app.database import db as default_db

        db = default_db

    cached = getattr(db, _OWNER_ID_ATTR, None)
    if cached is not None:
        return cached

    email = normalize_email(settings.owner_email)
    async with db.session_factory() as session:
        owner_id = (
            await session.execute(select(User.id).where(User.email == email))
        ).scalar_one_or_none()
        if owner_id is None:
            owner_id = str(uuid4())
            session.add(User(**_owner_insert_values(owner_id, email)))
            await session.commit()
            logger.info("Created bootstrap owner for single-user mode (%s)", email)

    setattr(db, _OWNER_ID_ATTR, owner_id)
    await _backfill_owned_rows(db, owner_id)
    return owner_id


async def _backfill_owned_rows(db, owner_id: str) -> None:
    """Assign every owned row with ``user_id IS NULL`` to ``owner_id`` (once).

    Transitional local rows created by ``create_all`` before scoping was threaded
    may have ``user_id IS NULL``; this reclaims them for the owner so they remain
    visible in single-user mode. Idempotent and data-preserving (only NULLs are
    touched); guarded so it runs at most once per process/instance.
    """
    if getattr(db, _BACKFILLED_ATTR, False):
        return
    async with db.session_factory() as session:
        for model in OWNED_BACKFILL_MODELS:
            await session.execute(
                update(model)
                .where(model.user_id.is_(None))
                .values(user_id=owner_id)
            )
        await session.commit()
    setattr(db, _BACKFILLED_ATTR, True)


def resolve_owner_id_sync(db=None) -> str:
    """Synchronously return the owner id (create if absent). Hot-path safe.

    Used by the encrypted api-key store reads (``llm.py`` runs synchronously and
    cannot ``await``). Reuses the cached id when present; otherwise resolves /
    creates the owner off the sync engine. Does not run the owned-row backfill -
    that is handled by the async :func:`ensure_owner` on the request path and at
    startup.
    """
    if db is None:
        from app.database import db as default_db

        db = default_db

    cached = getattr(db, _OWNER_ID_ATTR, None)
    if cached is not None:
        return cached

    email = normalize_email(settings.owner_email)
    # ``db._sync`` is the initialized sync session factory (idempotent init).
    with db._sync() as session:
        owner_id = session.execute(
            select(User.id).where(User.email == email)
        ).scalar_one_or_none()
        if owner_id is None:
            owner_id = str(uuid4())
            session.add(User(**_owner_insert_values(owner_id, email)))
            session.commit()
            logger.info("Created bootstrap owner (sync path) for single-user mode (%s)", email)

    setattr(db, _OWNER_ID_ATTR, owner_id)
    return owner_id
