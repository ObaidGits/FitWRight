"""Unit tests for the P4 cross-worker stream registry + idempotency cache."""

import pytest

from app.auth.kvstore.local import LocalKVStore
from app.resilience.idempotency import IdempotencyCache
from app.resilience.registry import StreamRegistry


@pytest.fixture
def kv():
    return LocalKVStore()


class TestStreamRegistry:
    async def test_registers_up_to_cap_then_rejects(self, kv):
        reg = StreamRegistry(kv)
        assert await reg.try_register("u1", "r1", max_concurrent=2, heartbeat_ttl=30)
        assert await reg.try_register("u1", "r2", max_concurrent=2, heartbeat_ttl=30)
        # Third exceeds the cap.
        assert not await reg.try_register("u1", "r3", max_concurrent=2, heartbeat_ttl=30)
        assert await reg.active_count("u1") == 2

    async def test_unregister_frees_a_slot(self, kv):
        reg = StreamRegistry(kv)
        await reg.try_register("u1", "r1", max_concurrent=1, heartbeat_ttl=30)
        assert not await reg.try_register("u1", "r2", max_concurrent=1, heartbeat_ttl=30)
        await reg.unregister("u1", "r1")
        assert await reg.active_count("u1") == 0
        assert await reg.try_register("u1", "r2", max_concurrent=1, heartbeat_ttl=30)

    async def test_expired_slots_are_pruned_and_self_heal_cap(self):
        # Drive the clock so a leaked (un-heartbeated) slot expires and frees the
        # cap without an explicit unregister (crashed-worker recovery).
        now = {"t": 1000.0}
        kv = LocalKVStore()
        reg = StreamRegistry(kv, clock=lambda: now["t"])
        await reg.try_register("u1", "r1", max_concurrent=1, heartbeat_ttl=30)
        assert not await reg.try_register("u1", "r2", max_concurrent=1, heartbeat_ttl=30)
        now["t"] += 31  # r1's slot has now expired
        assert await reg.try_register("u1", "r2", max_concurrent=1, heartbeat_ttl=30)

    async def test_heartbeat_extends_slot(self):
        now = {"t": 1000.0}
        reg = StreamRegistry(LocalKVStore(), clock=lambda: now["t"])
        await reg.try_register("u1", "r1", max_concurrent=1, heartbeat_ttl=30)
        now["t"] += 20
        await reg.heartbeat("u1", "r1", heartbeat_ttl=30)
        now["t"] += 20  # 40s since register, but only 20s since heartbeat
        assert await reg.active_count("u1") == 1

    async def test_users_are_isolated(self, kv):
        reg = StreamRegistry(kv)
        await reg.try_register("u1", "r1", max_concurrent=1, heartbeat_ttl=30)
        # A different user has their own cap.
        assert await reg.try_register("u2", "r1", max_concurrent=1, heartbeat_ttl=30)

    async def test_cancel_signal_roundtrip(self, kv):
        reg = StreamRegistry(kv)
        assert not await reg.is_cancelled("u1", "r1")
        await reg.request_cancel("u1", "r1")
        assert await reg.is_cancelled("u1", "r1")
        await reg.unregister("u1", "r1")
        assert not await reg.is_cancelled("u1", "r1")

    async def test_cancel_is_isolated_across_users(self, kv):
        """A cancel for one user's stream can never abort another user's stream
        with the same request id (cross-user isolation - Property 5)."""
        reg = StreamRegistry(kv)
        await reg.request_cancel("attacker", "shared-req-id")
        # The victim's identically-named stream is unaffected.
        assert not await reg.is_cancelled("victim", "shared-req-id")


class TestIdempotencyCache:
    async def test_store_and_get_roundtrip(self, kv):
        cache = IdempotencyCache(kv)
        await cache.store("u1", "k1", fingerprint="fp", result={"version": 2})
        rec = await cache.get("u1", "k1")
        assert rec is not None
        assert rec.fingerprint == "fp"
        assert rec.result == {"version": 2}

    async def test_missing_key_returns_none(self, kv):
        cache = IdempotencyCache(kv)
        assert await cache.get("u1", "nope") is None
        assert await cache.get("u1", None) is None

    async def test_namespaced_per_user(self, kv):
        cache = IdempotencyCache(kv)
        await cache.store("u1", "k1", fingerprint="fp", result={"v": 1})
        # A different user cannot see u1's cached result under the same key.
        assert await cache.get("u2", "k1") is None
