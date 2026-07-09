"""KVStore abstraction — the pluggable shared-state contract (ADR-6).

Phase 2 runs behind ≥2 workers, so all shared mutable auth state (session cache,
rate-limit counters, transient OAuth state, single-flight locks) moves out of
process memory into a ``KVStore``. The interface is intentionally small but
covers everything the later auth waves need:

- ``get`` / ``set`` / ``delete`` with an optional TTL (session cache, transient
  OAuth ``state``/``nonce``/``code_verifier`` blobs);
- ``incr`` — atomic increment with a TTL applied on key creation (rate-limit
  windows, lockout counters);
- ``lock`` — a TTL-bound single-flight primitive (session reaper, per-user
  master-resume promotion, "run this batch once").

Concrete adapters (``LocalKVStore``, ``RedisKVStore``, ``DBKVStore``) are chosen
by ``KVSTORE_URL`` via :func:`app.auth.kvstore.kvstore_from_url`. Swapping
between them is a single env-var change, never a code change (ADR-14).

Values are ``str`` (callers serialize JSON themselves); this keeps the contract
identical across a text-oriented Redis and a relational fallback.
"""

from __future__ import annotations

import abc
from types import TracebackType
from typing import Optional, Type

__all__ = ["KVStore", "KVLock"]


class KVLock(abc.ABC):
    """A TTL-bound single-flight lock, usable as an async context manager.

    ``async with store.lock("reaper", ttl_seconds=30) as acquired:`` yields
    ``True`` when the caller holds the lock and ``False`` when it could not be
    acquired (non-blocking mode or timeout). The TTL guarantees a crashed holder
    cannot wedge the lock forever — it auto-expires.
    """

    @abc.abstractmethod
    async def acquire(self) -> bool:
        """Attempt to acquire the lock; return whether it is now held."""

    @abc.abstractmethod
    async def release(self) -> None:
        """Release the lock if (and only if) this instance still owns it."""

    async def __aenter__(self) -> bool:
        return await self.acquire()

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.release()


class KVStore(abc.ABC):
    """Pluggable key/value store contract shared by all auth infrastructure."""

    @abc.abstractmethod
    async def get(self, key: str) -> str | None:
        """Return the value for ``key`` or ``None`` if missing/expired."""

    @abc.abstractmethod
    async def set(self, key: str, value: str, *, ttl_seconds: float | None = None) -> None:
        """Set ``key`` to ``value``.

        ``ttl_seconds`` (when > 0) makes the entry expire after that many
        seconds; ``None`` stores it without expiry.
        """

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete ``key`` (a no-op if it does not exist)."""

    @abc.abstractmethod
    async def incr(
        self, key: str, *, amount: int = 1, ttl_seconds: float | None = None
    ) -> int:
        """Atomically add ``amount`` to the integer at ``key`` and return it.

        A missing/expired key starts at 0 before the increment. ``ttl_seconds``
        is applied **only when the key is (re)created**, so a rate-limit window
        is set once and then counts down independently of later increments —
        matching Redis ``INCR``+``EXPIRE`` semantics.
        """

    @abc.abstractmethod
    def lock(
        self,
        key: str,
        *,
        ttl_seconds: float,
        blocking: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.05,
    ) -> KVLock:
        """Return a :class:`KVLock` for ``key``.

        ``ttl_seconds`` bounds how long the lock is held before auto-expiring.
        When ``blocking`` is true the lock waits up to ``timeout`` seconds
        (``None`` = wait indefinitely), polling every ``poll_interval`` seconds;
        when false it returns immediately with the acquisition result.
        """

    async def close(self) -> None:
        """Release any adapter resources (connections, engines). Default no-op."""
        return None
