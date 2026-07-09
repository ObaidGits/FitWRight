"""Unit tests for the pluggable KVStore adapters (ADR-6).

Covers the in-process (:class:`LocalKVStore`) and DB-backed (:class:`DBKVStore`)
adapters against the shared :class:`KVStore` contract — get/set/delete + TTL,
atomic ``incr`` (including concurrency and window semantics), and the
single-flight ``lock`` primitive — plus the ``kvstore_from_url`` adapter
selector. The Redis adapter needs a live server and is exercised in integration
env; here we only assert the factory routes to it without connecting.
"""

import asyncio

import pytest

from app.auth.kvstore import (
    DBKVStore,
    LocalKVStore,
    RedisKVStore,
    kvstore_from_url,
)
from app.db_engine import make_async_engine

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures: one parametrized fixture yielding each stateful adapter
# ---------------------------------------------------------------------------


@pytest.fixture
async def local_store():
    store = LocalKVStore()
    yield store
    await store.close()


@pytest.fixture
async def db_store(tmp_path):
    engine = make_async_engine(tmp_path / "kv.db")
    store = DBKVStore(engine)
    yield store
    await store.close()


@pytest.fixture(params=["local", "db"])
async def store(request, tmp_path):
    """Yield each concrete stateful KVStore adapter for contract tests."""
    if request.param == "local":
        adapter = LocalKVStore()
    else:
        adapter = DBKVStore(make_async_engine(tmp_path / "kv.db"))
    yield adapter
    await adapter.close()


# ---------------------------------------------------------------------------
# get / set / delete
# ---------------------------------------------------------------------------


class TestGetSetDelete:
    async def test_missing_key_returns_none(self, store):
        assert await store.get("nope") is None

    async def test_set_then_get(self, store):
        await store.set("k", "v")
        assert await store.get("k") == "v"

    async def test_overwrite(self, store):
        await store.set("k", "one")
        await store.set("k", "two")
        assert await store.get("k") == "two"

    async def test_delete(self, store):
        await store.set("k", "v")
        await store.delete("k")
        assert await store.get("k") is None

    async def test_delete_missing_is_noop(self, store):
        await store.delete("ghost")  # must not raise

    async def test_empty_string_value_roundtrips(self, store):
        await store.set("k", "")
        assert await store.get("k") == ""


# ---------------------------------------------------------------------------
# TTL
# ---------------------------------------------------------------------------


class TestTTL:
    async def test_value_expires(self, store):
        await store.set("k", "v", ttl_seconds=0.1)
        assert await store.get("k") == "v"
        await asyncio.sleep(0.15)
        assert await store.get("k") is None

    async def test_no_ttl_persists(self, store):
        await store.set("k", "v")
        await asyncio.sleep(0.12)
        assert await store.get("k") == "v"

    async def test_reset_clears_ttl(self, store):
        await store.set("k", "v", ttl_seconds=0.1)
        await store.set("k", "v2")  # no TTL — should persist
        await asyncio.sleep(0.15)
        assert await store.get("k") == "v2"


# ---------------------------------------------------------------------------
# incr — atomic counter with window (rate-limit) semantics
# ---------------------------------------------------------------------------


class TestIncr:
    async def test_incr_from_missing_starts_at_amount(self, store):
        assert await store.incr("c") == 1
        assert await store.incr("c") == 2

    async def test_incr_custom_amount(self, store):
        assert await store.incr("c", amount=5) == 5
        assert await store.incr("c", amount=3) == 8

    async def test_incr_value_is_readable_as_string(self, store):
        await store.incr("c", amount=7)
        assert await store.get("c") == "7"

    async def test_ttl_applied_on_creation_only(self, store):
        # First incr sets the window; subsequent incrs must NOT extend it.
        assert await store.incr("win", ttl_seconds=0.2) == 1
        await asyncio.sleep(0.1)
        assert await store.incr("win", ttl_seconds=0.2) == 2  # window still open
        await asyncio.sleep(0.15)  # now past the original 0.2s window
        # Window expired → counter resets to 1 (fresh window).
        assert await store.incr("win", ttl_seconds=0.2) == 1

    async def test_concurrent_incr_is_atomic(self, store):
        # 50 concurrent increments must total exactly 50 with no lost updates.
        await asyncio.gather(*(store.incr("race") for _ in range(50)))
        assert await store.get("race") == "50"


# ---------------------------------------------------------------------------
# lock — TTL-bound single-flight
# ---------------------------------------------------------------------------


class TestLock:
    async def test_acquire_and_release(self, store):
        async with store.lock("job", ttl_seconds=5) as acquired:
            assert acquired is True
        # Released — can be re-acquired immediately.
        async with store.lock("job", ttl_seconds=5) as again:
            assert again is True

    async def test_second_holder_blocked_nonblocking(self, store):
        first = store.lock("job", ttl_seconds=5)
        assert await first.acquire() is True
        try:
            second = store.lock("job", ttl_seconds=5, blocking=False)
            assert await second.acquire() is False
        finally:
            await first.release()

    async def test_expired_lock_can_be_reclaimed(self, store):
        first = store.lock("job", ttl_seconds=0.1)
        assert await first.acquire() is True
        await asyncio.sleep(0.15)  # let it expire without releasing
        second = store.lock("job", ttl_seconds=5, blocking=False)
        assert await second.acquire() is True
        await second.release()

    async def test_blocking_acquire_times_out(self, store):
        first = store.lock("job", ttl_seconds=5)
        assert await first.acquire() is True
        try:
            waiter = store.lock("job", ttl_seconds=5, blocking=True, timeout=0.1)
            assert await waiter.acquire() is False
        finally:
            await first.release()

    async def test_single_flight_under_contention(self, store):
        # Only one of many concurrent acquirers gets the lock at a time; the
        # critical section must never run concurrently.
        in_section = 0
        max_concurrent = 0
        ran = 0

        async def worker():
            nonlocal in_section, max_concurrent, ran
            async with store.lock("crit", ttl_seconds=5, timeout=5) as acquired:
                if not acquired:
                    return
                in_section += 1
                max_concurrent = max(max_concurrent, in_section)
                await asyncio.sleep(0.01)
                in_section -= 1
                ran += 1

        await asyncio.gather(*(worker() for _ in range(10)))
        assert max_concurrent == 1
        assert ran == 10

    async def test_release_only_affects_own_lock(self, store):
        # A late release from an expired holder must not free a lock a new
        # holder has since acquired.
        first = store.lock("job", ttl_seconds=0.1)
        assert await first.acquire() is True
        await asyncio.sleep(0.15)  # first expires
        second = store.lock("job", ttl_seconds=5)
        assert await second.acquire() is True
        await first.release()  # stale release — must be a no-op for `second`
        # `second` still holds it: a non-blocking third acquire fails.
        third = store.lock("job", ttl_seconds=5, blocking=False)
        assert await third.acquire() is False
        await second.release()


# ---------------------------------------------------------------------------
# Factory routing
# ---------------------------------------------------------------------------


class TestFactory:
    @pytest.mark.parametrize("url", ["", "local", "memory", "inproc", None])
    def test_local_selection(self, url):
        assert isinstance(kvstore_from_url(url), LocalKVStore)

    @pytest.mark.parametrize("url", ["redis://localhost:6379/0", "rediss://x.upstash.io"])
    def test_redis_selection_lazy(self, url):
        # from_url is lazy — this must not require a running Redis.
        assert isinstance(kvstore_from_url(url), RedisKVStore)

    def test_db_selection_requires_engine(self, tmp_path):
        engine = make_async_engine(tmp_path / "kv.db")
        assert isinstance(kvstore_from_url("db", db_engine=engine), DBKVStore)

    def test_db_without_engine_raises(self):
        with pytest.raises(ValueError, match="no database engine"):
            kvstore_from_url("database")

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError, match="Unrecognized"):
            kvstore_from_url("memcached://host")
