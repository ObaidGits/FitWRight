"""DB-backed ``KVStore`` adapter - the "no Redis at all" fallback (ADR-6).

On the strictest free tier there may be no Redis (not even Upstash). This
adapter keeps the app fully functional by persisting KV entries in a single
``kv`` table in the primary database (SQLite locally, Postgres hosted). It is
the documented trade-off from ADR-6: coarser rate-limit granularity and a
DB-hit (rather than in-memory) session cache, in exchange for zero extra infra.

Portability:
- The table is defined with SQLAlchemy Core (its own ``MetaData``, independent
  of the ORM ``Base``) and created on demand, so the adapter works whether or
  not the Alembic migrations have run yet.
- ``expires_at`` is stored as epoch seconds (a float column), compared in SQL so
  expiry is consistent regardless of engine.

Atomicity: ``incr`` and ``lock`` run inside a transaction and retry on the
transient "database is locked" error SQLite raises under write contention
(``busy_timeout`` handles most of it; the retry covers the rest). On Postgres
the same transactions serialize via row locks.
"""

from __future__ import annotations

import asyncio
import secrets
import time

from sqlalchemy import (
    Column,
    Float,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.kvstore.base import KVLock, KVStore

__all__ = ["DBKVStore"]

# Internal namespace so lock rows never collide with caller keys.
_LOCK_PREFIX = "\x00lock\x00"

_metadata = MetaData()

# Shared table definition for both value entries and lock rows. ``value`` holds
# the string payload (or the lock holder's token); ``expires_at`` is epoch
# seconds or NULL for "never expires".
kv_table = Table(
    "kv",
    _metadata,
    Column("key", String(512), primary_key=True),
    Column("value", Text, nullable=True),
    Column("expires_at", Float, nullable=True),
)

# How many times to retry a write transaction that hit SQLite's transient lock.
_MAX_RETRIES = 8
_RETRY_DELAY = 0.02


def _is_locked_error(exc: OperationalError) -> bool:
    message = str(exc.orig or exc).lower()
    return "locked" in message or "busy" in message


class _DBLock(KVLock):
    """TTL-bound single-flight lock stored as a row in the ``kv`` table."""

    def __init__(
        self,
        store: "DBKVStore",
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool,
        timeout: float | None,
        poll_interval: float,
    ) -> None:
        self._store = store
        self._key = _LOCK_PREFIX + key
        self._ttl = ttl_seconds
        self._blocking = blocking
        self._timeout = timeout
        self._poll = max(poll_interval, 0.001)
        self._token = secrets.token_hex(16)
        self._held = False

    async def acquire(self) -> bool:
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            if await self._store._try_take_lock(self._key, self._token, self._ttl):
                self._held = True
                return True
            if not self._blocking:
                return False
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self._poll)

    async def release(self) -> None:
        if self._held:
            await self._store._release_lock(self._key, self._token)
            self._held = False


class DBKVStore(KVStore):
    """KVStore persisted in a ``kv`` table of the primary database."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        self._table_ready = False
        self._ready_lock = asyncio.Lock()
        # Serializes read-modify-write ops (incr, lock take/release) within this
        # worker. SQLite ignores ``FOR UPDATE`` so this guard is what makes the
        # increment atomic across concurrent tasks in one process (the SQLite
        # dev/free-tier case); on Postgres ``with_for_update`` additionally
        # serializes across connections/workers via row locks.
        self._write_guard = asyncio.Lock()

    async def _ensure_table(self) -> None:
        if self._table_ready:
            return
        async with self._ready_lock:
            if self._table_ready:
                return
            async with self._engine.begin() as conn:
                await conn.run_sync(_metadata.create_all)
            self._table_ready = True

    @staticmethod
    def _now() -> float:
        return time.time()

    async def _run_with_retry(self, coro_factory):
        """Run a write transaction, retrying on SQLite's transient lock error."""
        last_exc: OperationalError | None = None
        for _ in range(_MAX_RETRIES):
            try:
                return await coro_factory()
            except OperationalError as exc:
                if not _is_locked_error(exc):
                    raise
                last_exc = exc
                await asyncio.sleep(_RETRY_DELAY)
        assert last_exc is not None
        raise last_exc

    async def get(self, key: str) -> str | None:
        await self._ensure_table()
        now = self._now()
        async with self._session_factory() as session:
            row = (
                await session.execute(select(kv_table.c.value, kv_table.c.expires_at).where(kv_table.c.key == key))
            ).first()
            if row is None:
                return None
            value, expires_at = row
            if expires_at is not None and now >= expires_at:
                return None
            return value

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        await self._ensure_table()
        expires_at = self._now() + ttl_seconds if ttl_seconds and ttl_seconds > 0 else None

        async def _op() -> None:
            async with self._session_factory() as session:
                async with session.begin():
                    # Upsert via delete+insert keeps portability simple and
                    # avoids dialect-specific ON CONFLICT syntax.
                    await session.execute(delete(kv_table).where(kv_table.c.key == key))
                    await session.execute(
                        kv_table.insert().values(key=key, value=value, expires_at=expires_at)
                    )

        await self._run_with_retry(_op)

    async def delete(self, key: str) -> None:
        await self._ensure_table()

        async def _op() -> None:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(delete(kv_table).where(kv_table.c.key == key))

        await self._run_with_retry(_op)

    async def incr(
        self, key: str, *, amount: int = 1, ttl_seconds: float | None = None
    ) -> int:
        await self._ensure_table()

        async def _op() -> int:
            now = self._now()
            async with self._session_factory() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(kv_table.c.value, kv_table.c.expires_at)
                            .where(kv_table.c.key == key)
                            .with_for_update()
                        )
                    ).first()
                    if row is None or (row[1] is not None and now >= row[1]):
                        new_value = amount
                        expires_at = (
                            now + ttl_seconds if ttl_seconds and ttl_seconds > 0 else None
                        )
                        await session.execute(delete(kv_table).where(kv_table.c.key == key))
                        await session.execute(
                            kv_table.insert().values(
                                key=key, value=str(new_value), expires_at=expires_at
                            )
                        )
                        return new_value
                    new_value = int(row[0]) + amount
                    await session.execute(
                        kv_table.update()
                        .where(kv_table.c.key == key)
                        .values(value=str(new_value))
                    )
                    return new_value

        async with self._write_guard:
            return await self._run_with_retry(_op)

    def lock(
        self,
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> KVLock:
        return _DBLock(
            self,
            key,
            ttl_seconds=ttl_seconds,
            blocking=blocking,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def close(self) -> None:
        await self._engine.dispose()

    # -- lock helpers (called by _DBLock) -----------------------------------

    async def _try_take_lock(self, lock_key: str, token: str, ttl_seconds: float) -> bool:
        await self._ensure_table()

        async def _op() -> bool:
            now = self._now()
            expires_at = now + max(ttl_seconds, 0.0)
            async with self._session_factory() as session:
                async with session.begin():
                    row = (
                        await session.execute(
                            select(kv_table.c.expires_at)
                            .where(kv_table.c.key == lock_key)
                            .with_for_update()
                        )
                    ).first()
                    if row is not None:
                        held_until = row[0]
                        if held_until is not None and now < held_until:
                            return False
                        # Stale/expired lock - reclaim it.
                        await session.execute(
                            kv_table.update()
                            .where(kv_table.c.key == lock_key)
                            .values(value=token, expires_at=expires_at)
                        )
                        return True
                    try:
                        await session.execute(
                            kv_table.insert().values(
                                key=lock_key, value=token, expires_at=expires_at
                            )
                        )
                    except IntegrityError:
                        # A concurrent acquirer inserted first.
                        return False
                    return True

        async with self._write_guard:
            return await self._run_with_retry(_op)

    async def _release_lock(self, lock_key: str, token: str) -> None:
        async def _op() -> None:
            async with self._session_factory() as session:
                async with session.begin():
                    # Compare-and-delete: only release the lock we still own.
                    await session.execute(
                        delete(kv_table).where(
                            kv_table.c.key == lock_key, kv_table.c.value == token
                        )
                    )

        async with self._write_guard:
            await self._run_with_retry(_op)
