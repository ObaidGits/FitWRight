"""Transactional outbox + at-least-once idempotent consumer (design §Platform).

**Producer side.** :func:`emit` appends an ``outbox`` row. Passed a live
``AsyncSession`` it enlists in the caller's transaction (true transactional
outbox — the event and the change commit or roll back together); with no session
it opens and commits its own (best-effort at-least-once, for producers that have
already committed their change, e.g. a background scheduler claim).

**Consumer side.** Handlers register per :class:`~app.events.types.EventType`.
:func:`process_outbox_batch` is single-flighted via the KVStore lock (safe to run
under external-cron *and* the in-process scheduler simultaneously — ADR-15),
scans unprocessed rows oldest-first in a bounded batch, and runs every handler
for each event. Handlers MUST be idempotent (the batch is at-least-once): a
partial failure re-runs all handlers for that event on the next pass. On repeated
failure past ``max_attempts`` the event is parked in the DLQ (``dead_at`` set) so
one poison row never blocks the queue; :func:`replay_dead_letters` re-arms them
(operator runbook).

Backlog + DLQ depth are exposed for metrics/alerts via :func:`outbox_stats`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.types import EventType

logger = logging.getLogger(__name__)

__all__ = [
    "OutboxEvent",
    "emit",
    "register_handler",
    "process_outbox_batch",
    "replay_dead_letters",
    "outbox_stats",
    "OUTBOX_LOCK_KEY",
]

OUTBOX_LOCK_KEY = "events:outbox"
_DEFAULT_BATCH = 200
_DEFAULT_MAX_ATTEMPTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class OutboxEvent:
    """The immutable view of an outbox row handed to a consumer handler."""

    id: str
    user_id: str | None
    event_type: str
    payload: dict[str, Any]
    created_at: str
    attempts: int


# Registry: event_type value -> ordered list of idempotent async handlers.
_HANDLERS: dict[str, list[Callable[[OutboxEvent], Awaitable[None]]]] = {}


def register_handler(
    event_type: EventType | str,
    handler: Callable[[OutboxEvent], Awaitable[None]],
) -> None:
    """Register an idempotent async ``handler`` for ``event_type`` (import-time)."""
    key = event_type.value if isinstance(event_type, EventType) else event_type
    _HANDLERS.setdefault(key, []).append(handler)


def _db():
    from app import database

    return database.db


async def emit(
    event_type: EventType | str,
    payload: dict[str, Any],
    *,
    user_id: str | None = None,
    session: AsyncSession | None = None,
) -> str:
    """Append an event to the outbox; return its id.

    With ``session`` the row enlists in the caller's transaction (the caller
    commits); without it, a dedicated transaction is opened and committed here.
    """
    from app.models import Outbox

    key = event_type.value if isinstance(event_type, EventType) else event_type
    row = Outbox(user_id=user_id, event_type=key, payload=payload or {}, created_at=_now())
    if session is not None:
        session.add(row)
        await session.flush()
        return row.id
    async with _db().session_factory() as own:
        own.add(row)
        await own.commit()
        return row.id


async def process_outbox_batch(
    *,
    kvstore=None,
    limit: int = _DEFAULT_BATCH,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
) -> dict[str, int]:
    """Process one bounded, single-flighted batch of unprocessed events.

    Returns ``{processed, failed, dead, locked}`` counts. Never raises on a
    single event's handler failure — that event is retried (attempts++) or
    dead-lettered, and the batch continues.
    """
    from app.models import Outbox

    if kvstore is None:
        from app.auth.runtime import get_kvstore

        kvstore = get_kvstore()

    processed = failed = dead = 0
    lock = kvstore.lock(OUTBOX_LOCK_KEY, ttl_seconds=120, blocking=False)
    async with lock as acquired:
        if not acquired:
            return {"processed": 0, "failed": 0, "dead": 0, "locked": 1}

        db = _db()
        async with db.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(Outbox)
                        .where(Outbox.processed_at.is_(None), Outbox.dead_at.is_(None))
                        .order_by(Outbox.created_at, Outbox.id)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        for row in rows:
            event = OutboxEvent(
                id=row.id,
                user_id=row.user_id,
                event_type=row.event_type,
                payload=row.payload or {},
                created_at=row.created_at,
                attempts=row.attempts,
            )
            handlers = _HANDLERS.get(row.event_type, [])
            try:
                for handler in handlers:
                    await handler(event)
                # Mark processed (unknown event types are processed as a no-op so
                # they don't clog the queue; a new consumer can rebuild instead).
                async with db.session_factory() as s:
                    await s.execute(
                        update(Outbox).where(Outbox.id == row.id).values(processed_at=_now())
                    )
                    await s.commit()
                processed += 1
            except Exception as exc:  # noqa: BLE001 - one poison event must not stop the batch
                attempts = row.attempts + 1
                is_dead = attempts >= max_attempts
                logger.warning(
                    "Outbox event %s (%s) failed (attempt %d/%d)%s: %s",
                    row.id, row.event_type, attempts, max_attempts,
                    " → DLQ" if is_dead else "", exc,
                )
                async with db.session_factory() as s:
                    await s.execute(
                        update(Outbox).where(Outbox.id == row.id).values(
                            attempts=attempts,
                            last_error=str(exc)[:500],
                            dead_at=_now() if is_dead else None,
                        )
                    )
                    await s.commit()
                if is_dead:
                    dead += 1
                else:
                    failed += 1

    return {"processed": processed, "failed": failed, "dead": dead, "locked": 0}


async def replay_dead_letters(*, limit: int = 100) -> int:
    """Re-arm dead-lettered events for another pass (operator runbook). Returns count."""
    from app.models import Outbox

    db = _db()
    async with db.session_factory() as session:
        ids = list(
            (
                await session.execute(
                    select(Outbox.id).where(Outbox.dead_at.is_not(None)).limit(limit)
                )
            ).scalars().all()
        )
        if not ids:
            return 0
        await session.execute(
            update(Outbox).where(Outbox.id.in_(ids)).values(dead_at=None, attempts=0, last_error=None)
        )
        await session.commit()
        return len(ids)


async def outbox_stats() -> dict[str, int]:
    """Return ``{backlog, dead}`` for metrics/alerting (indexer/notifier lag)."""
    from app.models import Outbox

    db = _db()
    async with db.session_factory() as session:
        backlog = int(
            (
                await session.execute(
                    select(func.count()).select_from(Outbox).where(
                        Outbox.processed_at.is_(None), Outbox.dead_at.is_(None)
                    )
                )
            ).scalar() or 0
        )
        dead = int(
            (
                await session.execute(
                    select(func.count()).select_from(Outbox).where(Outbox.dead_at.is_not(None))
                )
            ).scalar() or 0
        )
    return {"backlog": backlog, "dead": dead}
