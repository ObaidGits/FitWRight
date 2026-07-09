"""Redis/Upstash ``KVStore`` adapter — hosted free-tier & premium (ADR-6).

Upstash Redis (free serverless, HTTP/TLS) is the free-tier hosted default; a
full managed Redis is the premium value. Both speak the Redis protocol, so this
single adapter serves both — selected purely by the ``KVSTORE_URL`` scheme
(``redis://`` / ``rediss://``), no code change (ADR-14).

Semantics map directly onto native Redis primitives:
- ``get``/``set`` (with ``EX``)/``delete`` → ``GET``/``SET``/``DEL``;
- ``incr`` → ``INCRBY`` plus an ``EXPIRE`` applied only when the counter is first
  created (so the rate-limit window is set once);
- ``lock`` → ``SET key token NX PX ttl`` with a compare-and-delete release
  (Lua) so a caller only ever releases the lock it still owns.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any

from app.auth.kvstore.base import KVLock, KVStore

__all__ = ["RedisKVStore"]

# Release only if we still own the lock (atomic compare-and-delete).
_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class _RedisLock(KVLock):
    """TTL-bound single-flight lock via ``SET NX PX`` + compare-and-delete."""

    def __init__(
        self,
        store: "RedisKVStore",
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool,
        timeout: float | None,
        poll_interval: float,
    ) -> None:
        self._store = store
        self._key = f"lock:{key}"
        self._ttl_ms = max(int(ttl_seconds * 1000), 1)
        self._blocking = blocking
        self._timeout = timeout
        self._poll = max(poll_interval, 0.001)
        self._token = secrets.token_hex(16)
        self._held = False

    async def acquire(self) -> bool:
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        client = self._store._client
        while True:
            acquired = await client.set(self._key, self._token, nx=True, px=self._ttl_ms)
            if acquired:
                self._held = True
                return True
            if not self._blocking:
                return False
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self._poll)

    async def release(self) -> None:
        if self._held:
            await self._store._client.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
            self._held = False


class RedisKVStore(KVStore):
    """KVStore backed by a Redis-protocol server (Redis or Upstash)."""

    def __init__(self, client: Any) -> None:
        # ``client`` is a ``redis.asyncio.Redis`` decoding responses to ``str``.
        self._client = client

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "RedisKVStore":
        """Build from a ``redis://`` / ``rediss://`` URL (lazy — no connect)."""
        import redis.asyncio as redis  # local import: redis is an optional infra dep

        client = redis.from_url(url, decode_responses=True, **kwargs)
        return cls(client)

    async def get(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        if ttl_seconds and ttl_seconds > 0:
            # Millisecond precision preserves sub-second TTLs (transient OAuth).
            await self._client.set(key, value, px=max(int(ttl_seconds * 1000), 1))
        else:
            await self._client.set(key, value)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def incr(
        self, key: str, *, amount: int = 1, ttl_seconds: float | None = None
    ) -> int:
        new_value = int(await self._client.incrby(key, amount))
        if ttl_seconds and ttl_seconds > 0 and new_value == amount:
            # First increment created the key — set the window once.
            await self._client.pexpire(key, max(int(ttl_seconds * 1000), 1))
        return new_value

    def lock(
        self,
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> KVLock:
        return _RedisLock(
            self,
            key,
            ttl_seconds=ttl_seconds,
            blocking=blocking,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    async def close(self) -> None:
        await self._client.aclose()
