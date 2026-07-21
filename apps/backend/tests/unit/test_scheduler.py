"""Unit tests for the internal reaper loop (ADR-15, Package C).

Verifies the ``internal``-mode background runner (``app.scheduler``):

- the loop invokes ``SessionService.reap`` on each interval;
- it cancels cleanly on shutdown (no task leak, no swallowed error);
- a failing batch is logged and does not tear the loop down.

Uses an injected fake ``sleep`` + an ``asyncio.Event`` so the test never sleeps
for real and is fully deterministic.
"""

from __future__ import annotations

import asyncio

import pytest

from app.scheduler import reaper_loop, start_reaper, stop_reaper

pytestmark = pytest.mark.asyncio


class _FakeService:
    """A stand-in SessionService whose ``reap`` counts invocations."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self._fail = fail

    async def reap(self) -> dict[str, int]:
        self.calls += 1
        if self._fail:
            raise RuntimeError("boom")
        return {"sessions": 0, "email_tokens": 0, "reset_tokens": 0, "email_change_tokens": 0}


async def test_loop_invokes_reap_each_interval():
    service = _FakeService()
    ticked = asyncio.Event()

    async def fake_sleep(_seconds: float) -> None:
        # Signal that one reap+sleep cycle finished, then park until cancelled so
        # the loop does not spin (no real sleeping).
        ticked.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        reaper_loop(3600, sleep=fake_sleep, get_service=lambda: service)
    )
    try:
        await asyncio.wait_for(ticked.wait(), timeout=1.0)
    finally:
        await stop_reaper(task)

    assert service.calls == 1
    # The task is fully finished and cancellation was absorbed cleanly.
    assert task.done()
    assert task.cancelled() or task.exception() is None


async def test_loop_runs_multiple_intervals():
    service = _FakeService()
    reached_three = asyncio.Event()

    async def fake_sleep(_seconds: float) -> None:
        if service.calls >= 3:
            reached_three.set()
            await asyncio.Event().wait()
        # Otherwise return immediately to drive the next iteration.

    task = asyncio.create_task(
        reaper_loop(0.0, sleep=fake_sleep, get_service=lambda: service)
    )
    try:
        await asyncio.wait_for(reached_three.wait(), timeout=1.0)
    finally:
        await stop_reaper(task)

    assert service.calls >= 3


async def test_failing_reap_does_not_kill_loop():
    service = _FakeService(fail=True)
    ticked = asyncio.Event()

    async def fake_sleep(_seconds: float) -> None:
        # First failure still reaches the sleep (error was swallowed); park.
        ticked.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        reaper_loop(3600, sleep=fake_sleep, get_service=lambda: service)
    )
    try:
        await asyncio.wait_for(ticked.wait(), timeout=1.0)
        # The loop is still alive despite the reap raising.
        assert not task.done()
        assert service.calls == 1
    finally:
        await stop_reaper(task)
    assert task.done()


async def test_stop_reaper_is_safe_with_none():
    # Nothing started (external_cron mode): stop is a no-op.
    await stop_reaper(None)


async def test_stop_reaper_idempotent_on_finished_task():
    async def _noop() -> None:
        return None

    task = asyncio.create_task(_noop())
    await task
    # Already finished -> stop_reaper must not raise.
    await stop_reaper(task)


async def test_start_reaper_returns_named_task():
    # start_reaper wires the real loop; cancel immediately so it never actually
    # touches the DB/KVStore in this unit test.
    task = start_reaper(3600)
    assert task.get_name() == "session-reaper"
    await stop_reaper(task)
