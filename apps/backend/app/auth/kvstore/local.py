"""In-process ``KVStore`` adapter — local dev / single-worker (ADR-6).

Backed by a plain dict guarded by an ``asyncio.Lock``. Correct and fast for a
single worker (the default local-dev topology); it does **not** share state
across processes, so it must not back a multi-worker hosted deployment — that is
what the Redis and DB-backed adapters are for.

Expiry is lazy: entries carry an absolute deadline and are treated as absent
once past it (and cleaned up opportunistically), so no background sweeper is
needed.
"""

from __future__ import annotations

import asyncio
import secrets
import time

from app.auth.kvstore.base import KVLock, KVStore

__all__ = ["LocalKVStore"]


class _LocalLock(KVLock):
    """TTL-bound single-flight lock over the owning store's ``_lock_until`` map."""

    def __init__(
        self,
        store: "LocalKVStore",
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool,
        timeout: float | None,
        poll_interval: float,
    ) -> None:
        self._store = store
        self._key = key
        self._ttl = ttl_seconds
        self._blocking = blocking
        self._timeout = timeout
        self._poll = max(poll_interval, 0.001)
        # A per-acquisition token so release only ever frees the lock this
        # instance still owns (a stale holder whose TTL lapsed must not free a
        # lock a newer holder has since taken).
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


class LocalKVStore(KVStore):
    """Dict-backed in-process KVStore for single-worker/local use."""

    def __init__(self) -> None:
        # key -> (value, expiry_monotonic | None)
        self._data: dict[str, tuple[str, float | None]] = {}
        # lock key -> (expiry_monotonic, holder_token)
        self._lock_until: dict[str, tuple[float, str]] = {}
        self._guard = asyncio.Lock()

    @staticmethod
    def _expired(expiry: float | None, now: float) -> bool:
        return expiry is not None and now >= expiry

    async def get(self, key: str) -> str | None:
        now = time.monotonic()
        async with self._guard:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if self._expired(expiry, now):
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        expiry = time.monotonic() + ttl_seconds if ttl_seconds and ttl_seconds > 0 else None
        async with self._guard:
            self._data[key] = (value, expiry)

    async def delete(self, key: str) -> None:
        async with self._guard:
            self._data.pop(key, None)

    async def incr(
        self, key: str, *, amount: int = 1, ttl_seconds: float | None = None
    ) -> int:
        now = time.monotonic()
        async with self._guard:
            entry = self._data.get(key)
            if entry is None or self._expired(entry[1], now):
                # (Re)create the counter and apply the TTL once, on creation.
                expiry = now + ttl_seconds if ttl_seconds and ttl_seconds > 0 else None
                new_value = amount
                self._data[key] = (str(new_value), expiry)
                return new_value
            current = int(entry[0])
            new_value = current + amount
            # Preserve the existing window deadline across increments.
            self._data[key] = (str(new_value), entry[1])
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
        return _LocalLock(
            self,
            key,
            ttl_seconds=ttl_seconds,
            blocking=blocking,
            timeout=timeout,
            poll_interval=poll_interval,
        )

    # -- lock helpers (called by _LocalLock) --------------------------------

    async def _try_take_lock(self, key: str, token: str, ttl_seconds: float) -> bool:
        now = time.monotonic()
        async with self._guard:
            held = self._lock_until.get(key)
            if held is not None and now < held[0]:
                return False
            self._lock_until[key] = (now + max(ttl_seconds, 0.0), token)
            return True

    async def _release_lock(self, key: str, token: str) -> None:
        async with self._guard:
            held = self._lock_until.get(key)
            # Compare-and-delete: only release the lock we still own.
            if held is not None and held[1] == token:
                self._lock_until.pop(key, None)
