"""Unit/integration tests for the shared event outbox (design §Platform, R16).

Exercises emit → consume → idempotency → retry → DLQ → replay against a real
isolated DB, using a local KVStore for the single-flight lock.
"""

from __future__ import annotations

import pytest

from app.auth.kvstore.local import LocalKVStore
from app.events import outbox as outbox_mod
from app.events.outbox import (
    OutboxEvent,
    emit,
    outbox_stats,
    process_outbox_batch,
    replay_dead_letters,
)


@pytest.fixture(autouse=True)
def _clear_handlers(monkeypatch):
    """Isolate the handler registry per test."""
    monkeypatch.setattr(outbox_mod, "_HANDLERS", {})
    yield


def _kv():
    return LocalKVStore()


class TestEmitAndProcess:
    async def test_emit_then_process_runs_handler(self, isolated_db):
        seen: list[OutboxEvent] = []

        async def handler(event: OutboxEvent) -> None:
            seen.append(event)

        outbox_mod.register_handler("test.event", handler)
        await emit("test.event", {"k": "v"}, user_id="u1")

        result = await process_outbox_batch(kvstore=_kv())
        assert result["processed"] == 1
        assert len(seen) == 1
        assert seen[0].payload == {"k": "v"}
        assert seen[0].user_id == "u1"

    async def test_processed_events_not_reprocessed(self, isolated_db):
        calls = {"n": 0}

        async def handler(event: OutboxEvent) -> None:
            calls["n"] += 1

        outbox_mod.register_handler("test.event", handler)
        await emit("test.event", {}, user_id="u1")
        await process_outbox_batch(kvstore=_kv())
        second = await process_outbox_batch(kvstore=_kv())
        assert calls["n"] == 1  # processed once
        assert second["processed"] == 0

    async def test_unknown_event_type_is_processed_noop(self, isolated_db):
        await emit("nobody.listens", {}, user_id="u1")
        result = await process_outbox_batch(kvstore=_kv())
        assert result["processed"] == 1  # marked processed, no handler

    async def test_single_flight_lock_blocks_concurrent(self, isolated_db):
        kv = _kv()
        # Hold the lock, then a batch should report locked.
        lock = kv.lock(outbox_mod.OUTBOX_LOCK_KEY, ttl_seconds=30, blocking=False)
        async with lock as acquired:
            assert acquired
            result = await process_outbox_batch(kvstore=kv)
            assert result["locked"] == 1


class TestRetryAndDlq:
    async def test_failing_handler_retries_then_dead_letters(self, isolated_db):
        async def boom(event: OutboxEvent) -> None:
            raise RuntimeError("nope")

        outbox_mod.register_handler("test.event", boom)
        await emit("test.event", {}, user_id="u1")

        # max_attempts=2 → first pass fails (attempts=1), second dead-letters.
        r1 = await process_outbox_batch(kvstore=_kv(), max_attempts=2)
        assert r1["failed"] == 1 and r1["dead"] == 0
        r2 = await process_outbox_batch(kvstore=_kv(), max_attempts=2)
        assert r2["dead"] == 1

        stats = await outbox_stats()
        assert stats["dead"] == 1
        assert stats["backlog"] == 0  # dead rows are not backlog

    async def test_replay_dead_letters_rearms(self, isolated_db):
        async def boom(event: OutboxEvent) -> None:
            raise RuntimeError("nope")

        outbox_mod.register_handler("test.event", boom)
        await emit("test.event", {}, user_id="u1")
        await process_outbox_batch(kvstore=_kv(), max_attempts=1)  # → DLQ immediately
        assert (await outbox_stats())["dead"] == 1

        replayed = await replay_dead_letters()
        assert replayed == 1
        assert (await outbox_stats())["dead"] == 0
        assert (await outbox_stats())["backlog"] == 1  # re-armed for another pass
