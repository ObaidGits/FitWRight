"""KVStore port contract test (ARCHITECTURE §17, §19; IMPLEMENTATION_PLAN Phase 4).

ONE behavioral suite run against EVERY implementation of the ``KVStore`` port.
This is what actually prevents adapter drift: any implementation that diverges
from the contract in ``app/auth/kvstore/base.py`` fails here. Adding a new
adapter (e.g. Redis, exercised against a real/fake server) is a single new entry
in ``kvstore_impl``.

Governance (ARCHITECTURE §19): a port implementation without a contract test
must fail CI - this file is that test for KVStore's Local and DB adapters.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.kvstore import DBKVStore, KVStore, LocalKVStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=["local", "db"])
async def kvstore_impl(request, tmp_path: Path):
    """Yield each KVStore implementation under a fresh, isolated backing store."""
    if request.param == "local":
        store: KVStore = LocalKVStore()
        yield store
        await store.close()
    else:
        # File-backed SQLite so multiple async connections share one database.
        db_file = tmp_path / f"kv_{uuid.uuid4().hex}.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
        store = DBKVStore(engine)
        yield store
        await store.close()


class TestGetSetDelete:
    async def test_missing_key_returns_none(self, kvstore_impl: KVStore):
        assert await kvstore_impl.get("absent") is None

    async def test_set_then_get_roundtrip(self, kvstore_impl: KVStore):
        await kvstore_impl.set("k", "v")
        assert await kvstore_impl.get("k") == "v"

    async def test_set_overwrites(self, kvstore_impl: KVStore):
        await kvstore_impl.set("k", "v1")
        await kvstore_impl.set("k", "v2")
        assert await kvstore_impl.get("k") == "v2"

    async def test_delete_is_idempotent(self, kvstore_impl: KVStore):
        await kvstore_impl.set("k", "v")
        await kvstore_impl.delete("k")
        await kvstore_impl.delete("k")  # no error on second delete
        assert await kvstore_impl.get("k") is None


class TestTtl:
    async def test_no_ttl_persists(self, kvstore_impl: KVStore):
        await kvstore_impl.set("k", "v", ttl_seconds=None)
        assert await kvstore_impl.get("k") == "v"

    async def test_expired_entry_reads_as_absent(self, kvstore_impl: KVStore):
        await kvstore_impl.set("k", "v", ttl_seconds=0.05)
        await asyncio.sleep(0.12)
        assert await kvstore_impl.get("k") is None


class TestIncr:
    async def test_first_incr_starts_from_amount(self, kvstore_impl: KVStore):
        assert await kvstore_impl.incr("c") == 1

    async def test_incr_accumulates(self, kvstore_impl: KVStore):
        await kvstore_impl.incr("c")
        assert await kvstore_impl.incr("c") == 2
        assert await kvstore_impl.incr("c", amount=5) == 7

    async def test_incr_ttl_applies_on_creation_then_window_counts_down(
        self, kvstore_impl: KVStore
    ):
        # Window set once on creation; later increments keep the same deadline.
        assert await kvstore_impl.incr("win", ttl_seconds=0.1) == 1
        assert await kvstore_impl.incr("win") == 2
        await asyncio.sleep(0.16)
        # Window expired -> counter resets to the amount on next incr.
        assert await kvstore_impl.incr("win") == 1


class TestLock:
    async def test_acquire_and_release(self, kvstore_impl: KVStore):
        lock = kvstore_impl.lock("res", ttl_seconds=5)
        assert await lock.acquire() is True
        await lock.release()

    async def test_second_holder_blocked_while_held(self, kvstore_impl: KVStore):
        first = kvstore_impl.lock("res", ttl_seconds=5)
        assert await first.acquire() is True
        try:
            second = kvstore_impl.lock("res", ttl_seconds=5, blocking=False)
            assert await second.acquire() is False
        finally:
            await first.release()

    async def test_lock_reacquirable_after_release(self, kvstore_impl: KVStore):
        first = kvstore_impl.lock("res", ttl_seconds=5)
        assert await first.acquire() is True
        await first.release()
        second = kvstore_impl.lock("res", ttl_seconds=5, blocking=False)
        assert await second.acquire() is True
        await second.release()

    async def test_context_manager_yields_acquisition(self, kvstore_impl: KVStore):
        async with kvstore_impl.lock("res", ttl_seconds=5) as acquired:
            assert acquired is True
