"""Live-Redis validation for the production ``RedisKVStore`` adapter (ADR-6).

The audit flagged that the Redis adapter - the hosted free-tier (Upstash) and
premium (managed Redis) shared-state path - was never integration-tested: the
unit suite only proves the factory *routes* to it lazily, and the contract
tests run against the local/DB adapters. This suite closes that gap by driving
``RedisKVStore`` against a **real** Redis server for exactly the primitives auth
depends on at runtime:

- ``set``/``get``/``delete`` incl. TTL expiry (session cache, transient OAuth
  ``state``/``nonce``/``code_verifier`` blobs);
- atomic ``incr`` with rate-limit *window* semantics + concurrent increments
  (rate-limit / lockout counters);
- the ``lock`` single-flight primitive - holder token, a second acquire fails
  while held, correct release, and TTL auto-expiry (session reaper, per-user
  master-resume promotion).

It is **best-effort and gated**, mirroring ``test_postgres_backend.py``: it uses
``TEST_REDIS_URL`` if set, otherwise spins up a disposable ``redis:7-alpine`` via
Docker, and **skips with a clear reason** if neither is available (no Docker, no
image pull, unreachable server).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid

import pytest

from app.auth.kvstore import RedisKVStore, kvstore_from_url

pytestmark = pytest.mark.integration

_REDIS_IMAGE = "redis:7-alpine"
_READY_TIMEOUT_S = 30


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


async def _wait_until_ready(url: str) -> bool:
    """Poll a client until the Redis server answers ``PING``."""
    import redis.asyncio as redis

    deadline = time.time() + _READY_TIMEOUT_S
    last_err: Exception | None = None
    while time.time() < deadline:
        client = redis.from_url(url, decode_responses=True)
        try:
            if await client.ping():
                return True
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last_err = exc
            await asyncio.sleep(0.5)
        finally:
            await client.aclose()
    if last_err is not None:
        print(f"Redis never became ready: {last_err}")
    return False


@pytest.fixture(scope="module")
def redis_url() -> str:
    """A reachable Redis URL, or skip with a clear reason.

    Precedence: an explicit ``TEST_REDIS_URL`` (CI/dev supplies a server) -> a
    disposable Docker container -> skip.
    """
    explicit = os.environ.get("TEST_REDIS_URL")
    if explicit:
        if not asyncio.run(_wait_until_ready(explicit)):
            pytest.skip(f"TEST_REDIS_URL set but server not reachable: {explicit}")
        yield explicit
        return

    if not _docker_available():
        pytest.skip(
            "No TEST_REDIS_URL and Docker is unavailable; skipping live-Redis "
            "validation (set TEST_REDIS_URL to run against an existing server)."
        )

    container = f"fitwright-redis-{uuid.uuid4().hex[:12]}"
    run = subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", container,
            "-P",
            _REDIS_IMAGE,
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        pytest.skip(f"Could not start Redis container (docker run failed): {run.stderr.strip()}")

    try:
        port_proc = subprocess.run(
            ["docker", "port", container, "6379/tcp"],
            capture_output=True,
            text=True,
        )
        if port_proc.returncode != 0 or not port_proc.stdout.strip():
            pytest.skip(f"Could not resolve mapped Redis port: {port_proc.stderr.strip()}")
        # e.g. "0.0.0.0:49153" (may be multiple lines for v4/v6) -> take the port.
        host_port = port_proc.stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        url = f"redis://127.0.0.1:{host_port}/0"

        if not asyncio.run(_wait_until_ready(url)):
            pytest.skip("Redis container started but never became ready in time.")
        yield url
    finally:
        subprocess.run(["docker", "stop", container], capture_output=True)


@pytest.fixture
async def store(redis_url):
    """A live ``RedisKVStore`` on a per-test key namespace (flushed after)."""
    adapter = kvstore_from_url(redis_url)
    # The factory must select the real Redis adapter for a redis:// URL.
    assert isinstance(adapter, RedisKVStore)
    try:
        yield adapter
    finally:
        # Clear all keys so tests never leak state into one another.
        await adapter._client.flushdb()
        await adapter.close()


def _k(name: str) -> str:
    """Unique-ish key per invocation to avoid cross-test collisions."""
    return f"{name}:{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# get / set / delete + TTL - session cache & transient OAuth blobs
# ---------------------------------------------------------------------------


class TestGetSetDeleteTTL:
    async def test_missing_key_returns_none(self, store):
        assert await store.get(_k("nope")) is None

    async def test_set_get_overwrite_delete(self, store):
        key = _k("k")
        await store.set(key, "v")
        assert await store.get(key) == "v"
        await store.set(key, "v2")
        assert await store.get(key) == "v2"
        await store.delete(key)
        assert await store.get(key) is None

    async def test_delete_missing_is_noop(self, store):
        await store.delete(_k("ghost"))  # must not raise

    async def test_empty_string_value_roundtrips(self, store):
        key = _k("empty")
        await store.set(key, "")
        assert await store.get(key) == ""

    async def test_ttl_expiry(self, store):
        key = _k("ttl")
        await store.set(key, "v", ttl_seconds=1)
        assert await store.get(key) == "v"
        await asyncio.sleep(1.4)
        assert await store.get(key) is None

    async def test_no_ttl_persists(self, store):
        key = _k("persist")
        await store.set(key, "v")
        await asyncio.sleep(0.3)
        assert await store.get(key) == "v"

    async def test_reset_clears_ttl(self, store):
        key = _k("reset-ttl")
        await store.set(key, "v", ttl_seconds=1)
        await store.set(key, "v2")  # no TTL -> should persist past the window
        await asyncio.sleep(1.4)
        assert await store.get(key) == "v2"


# ---------------------------------------------------------------------------
# incr - atomic counter with rate-limit window semantics
# ---------------------------------------------------------------------------


class TestIncr:
    async def test_incr_from_missing_starts_at_amount(self, store):
        key = _k("c")
        assert await store.incr(key) == 1
        assert await store.incr(key) == 2

    async def test_incr_custom_amount(self, store):
        key = _k("c")
        assert await store.incr(key, amount=5) == 5
        assert await store.incr(key, amount=3) == 8

    async def test_incr_value_readable_as_string(self, store):
        key = _k("c")
        await store.incr(key, amount=7)
        assert await store.get(key) == "7"

    async def test_ttl_applied_on_creation_only(self, store):
        # First incr sets the window; later incrs must NOT extend it (Redis
        # INCR+EXPIRE-once semantics - the rate-limit window counts down).
        key = _k("win")
        assert await store.incr(key, ttl_seconds=1) == 1
        await asyncio.sleep(0.4)
        assert await store.incr(key, ttl_seconds=1) == 2  # window still open
        await asyncio.sleep(0.9)  # now past the original 1s window
        # Window expired -> counter resets to 1 (fresh window).
        assert await store.incr(key, ttl_seconds=1) == 1

    async def test_concurrent_incr_is_atomic(self, store):
        # 50 concurrent increments must total exactly 50 with no lost updates.
        key = _k("race")
        await asyncio.gather(*(store.incr(key) for _ in range(50)))
        assert await store.get(key) == "50"


# ---------------------------------------------------------------------------
# lock - TTL-bound single-flight primitive
# ---------------------------------------------------------------------------


class TestLock:
    async def test_acquire_and_release(self, store):
        key = _k("job")
        async with store.lock(key, ttl_seconds=5) as acquired:
            assert acquired is True
        # Released - can be re-acquired immediately.
        async with store.lock(key, ttl_seconds=5) as again:
            assert again is True

    async def test_second_holder_blocked_while_held(self, store):
        key = _k("job")
        first = store.lock(key, ttl_seconds=5)
        assert await first.acquire() is True
        try:
            second = store.lock(key, ttl_seconds=5, blocking=False)
            assert await second.acquire() is False
        finally:
            await first.release()
        # After release a fresh acquire succeeds.
        third = store.lock(key, ttl_seconds=5, blocking=False)
        assert await third.acquire() is True
        await third.release()

    async def test_blocking_acquire_times_out(self, store):
        key = _k("job")
        first = store.lock(key, ttl_seconds=5)
        assert await first.acquire() is True
        try:
            waiter = store.lock(key, ttl_seconds=5, blocking=True, timeout=0.3)
            assert await waiter.acquire() is False
        finally:
            await first.release()

    async def test_ttl_auto_expiry_lets_lock_be_reclaimed(self, store):
        key = _k("job")
        first = store.lock(key, ttl_seconds=1)
        assert await first.acquire() is True
        await asyncio.sleep(1.4)  # let it expire without releasing
        second = store.lock(key, ttl_seconds=5, blocking=False)
        assert await second.acquire() is True
        await second.release()

    async def test_release_only_affects_own_lock(self, store):
        # A stale release from an expired holder must not free a lock a new
        # holder has since acquired (compare-and-delete on the holder token).
        key = _k("job")
        first = store.lock(key, ttl_seconds=1)
        assert await first.acquire() is True
        await asyncio.sleep(1.4)  # first expires
        second = store.lock(key, ttl_seconds=5)
        assert await second.acquire() is True
        await first.release()  # stale release - must be a no-op for `second`
        third = store.lock(key, ttl_seconds=5, blocking=False)
        assert await third.acquire() is False  # `second` still holds it
        await second.release()

    async def test_single_flight_under_contention(self, store):
        # Only one of many concurrent acquirers is in the critical section at a
        # time; the section must never run concurrently.
        key = _k("crit")
        in_section = 0
        max_concurrent = 0
        ran = 0

        async def worker():
            nonlocal in_section, max_concurrent, ran
            async with store.lock(key, ttl_seconds=5, timeout=5) as acquired:
                if not acquired:
                    return
                in_section += 1
                max_concurrent = max(max_concurrent, in_section)
                await asyncio.sleep(0.02)
                in_section -= 1
                ran += 1

        await asyncio.gather(*(worker() for _ in range(10)))
        assert max_concurrent == 1
        assert ran == 10
