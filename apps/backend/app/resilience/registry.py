"""Cross-worker streaming-task registry + per-user concurrency cap (R1.5).

Phase 2 runs behind ≥2 workers, so the "how many streams does this user have
open, and should we start another?" decision cannot live in one process's
memory - it moves into the :class:`~app.auth.kvstore.KVStore`.

Design (design §Streaming, "Task registry & cancellation"):

- Each active stream registers a **slot** ``{request_id, expires_at}`` in a
  per-user JSON list at ``stream:active:{user_id}``, guarded by a KV lock so two
  workers can't interleave a read-modify-write into a corrupt list.
- The list is **self-healing**: every register/heartbeat/count prunes slots
  whose ``expires_at`` has passed, so a stream leaked by a crashed worker frees
  its slot within the heartbeat TTL rather than wedging the user's cap forever.
- Cancellation is **cross-worker**: the cancel endpoint sets a short-lived flag
  at ``stream:cancel:{user_id}:{request_id}``; the streaming loop (which may run
  on a different worker) polls :meth:`is_cancelled` between chunks and aborts the
  provider call. The owning worker also holds the real ``asyncio.Task`` for a
  direct, immediate cancel when the request lands on the same worker.

The actual ``asyncio.Task`` handle is intentionally *not* stored here (it isn't
serialisable and is process-local); this registry only tracks the cross-worker
metadata needed for the cap, liveness, and cancel signalling.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from app.auth.kvstore import KVStore

logger = logging.getLogger(__name__)

__all__ = ["StreamRegistry", "StreamSlot", "get_stream_registry"]

_ACTIVE_PREFIX = "stream:active"
_CANCEL_PREFIX = "stream:cancel"
_LOCK_PREFIX = "stream:lock"


@dataclass(frozen=True, slots=True)
class StreamSlot:
    """A registered stream slot: its request id and wall-clock expiry (epoch s)."""

    request_id: str
    expires_at: float


class StreamRegistry:
    """Per-user active-stream tracking over a :class:`KVStore`."""

    def __init__(
        self,
        kv: KVStore,
        *,
        clock=time.time,
        lock_ttl: float = 5.0,
        lock_timeout: float = 3.0,
    ) -> None:
        self._kv = kv
        self._clock = clock
        self._lock_ttl = lock_ttl
        self._lock_timeout = lock_timeout

    # -- key helpers --------------------------------------------------------

    @staticmethod
    def _active_key(user_id: str) -> str:
        return f"{_ACTIVE_PREFIX}:{user_id}"

    @staticmethod
    def _cancel_key(user_id: str, request_id: str) -> str:
        return f"{_CANCEL_PREFIX}:{user_id}:{request_id}"

    @staticmethod
    def _lock_key(user_id: str) -> str:
        return f"{_LOCK_PREFIX}:{user_id}"

    # -- (de)serialisation --------------------------------------------------

    def _load_slots(self, raw: str | None) -> list[StreamSlot]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return []
        slots: list[StreamSlot] = []
        if isinstance(data, list):
            for item in data:
                try:
                    slots.append(
                        StreamSlot(str(item["request_id"]), float(item["expires_at"]))
                    )
                except (KeyError, TypeError, ValueError):
                    continue
        return slots

    def _prune(self, slots: list[StreamSlot], now: float) -> list[StreamSlot]:
        return [s for s in slots if s.expires_at > now]

    def _dump(self, slots: list[StreamSlot]) -> str:
        return json.dumps(
            [{"request_id": s.request_id, "expires_at": s.expires_at} for s in slots]
        )

    # -- public API ---------------------------------------------------------

    async def try_register(
        self,
        user_id: str,
        request_id: str,
        *,
        max_concurrent: int,
        heartbeat_ttl: float,
    ) -> bool:
        """Atomically register a stream slot if under the per-user cap.

        Returns ``True`` when the slot was added (the caller may start the
        stream) and ``False`` when the user already holds ``max_concurrent`` live
        streams (the caller returns a retryable 429). Prunes expired slots first
        so a leaked stream never permanently consumes the cap. On a KV outage it
        fails **open** for a single stream (availability over a hard cap) but logs
        it - the in-process max-lifetime reaper still bounds any leak.
        """
        now = self._clock()
        lock = self._kv.lock(
            self._lock_key(user_id),
            ttl_seconds=self._lock_ttl,
            blocking=True,
            timeout=self._lock_timeout,
        )
        try:
            async with lock as acquired:
                if not acquired:
                    logger.warning("stream registry lock contended for %s", user_id)
                    return True  # fail-open; bounded by in-process max lifetime
                key = self._active_key(user_id)
                slots = self._prune(self._load_slots(await self._kv.get(key)), now)
                # Re-registering an existing request id is a no-op refresh.
                slots = [s for s in slots if s.request_id != request_id]
                if len(slots) >= max_concurrent:
                    await self._kv.set(key, self._dump(slots), ttl_seconds=heartbeat_ttl * 4)
                    return False
                slots.append(StreamSlot(request_id, now + heartbeat_ttl))
                await self._kv.set(key, self._dump(slots), ttl_seconds=heartbeat_ttl * 4)
                return True
        except Exception:  # pragma: no cover - KV outage path
            logger.warning("stream registry unavailable; failing open", exc_info=True)
            return True

    async def heartbeat(self, user_id: str, request_id: str, *, heartbeat_ttl: float) -> None:
        """Extend a slot's expiry (called on each SSE heartbeat/token flush)."""
        now = self._clock()
        lock = self._kv.lock(
            self._lock_key(user_id),
            ttl_seconds=self._lock_ttl,
            blocking=True,
            timeout=self._lock_timeout,
        )
        try:
            async with lock as acquired:
                if not acquired:
                    return
                key = self._active_key(user_id)
                slots = self._prune(self._load_slots(await self._kv.get(key)), now)
                slots = [s for s in slots if s.request_id != request_id]
                slots.append(StreamSlot(request_id, now + heartbeat_ttl))
                await self._kv.set(key, self._dump(slots), ttl_seconds=heartbeat_ttl * 4)
        except Exception:  # pragma: no cover
            logger.debug("stream heartbeat failed for %s/%s", user_id, request_id, exc_info=True)

    async def unregister(self, user_id: str, request_id: str) -> None:
        """Remove a slot (stream ended, cancelled, or reaped) and clear its flag."""
        now = self._clock()
        lock = self._kv.lock(
            self._lock_key(user_id),
            ttl_seconds=self._lock_ttl,
            blocking=True,
            timeout=self._lock_timeout,
        )
        try:
            async with lock as acquired:
                if acquired:
                    key = self._active_key(user_id)
                    slots = self._prune(self._load_slots(await self._kv.get(key)), now)
                    slots = [s for s in slots if s.request_id != request_id]
                    if slots:
                        await self._kv.set(key, self._dump(slots))
                    else:
                        await self._kv.delete(key)
        except Exception:  # pragma: no cover
            logger.debug("stream unregister failed for %s/%s", user_id, request_id, exc_info=True)
        finally:
            try:
                await self._kv.delete(self._cancel_key(user_id, request_id))
            except Exception:  # pragma: no cover
                pass

    async def active_count(self, user_id: str) -> int:
        """Number of non-expired stream slots for ``user_id``."""
        now = self._clock()
        try:
            slots = self._prune(self._load_slots(await self._kv.get(self._active_key(user_id))), now)
            return len(slots)
        except Exception:  # pragma: no cover
            return 0

    async def request_cancel(self, user_id: str, request_id: str, *, ttl: float = 60.0) -> None:
        """Signal a cancel for ``request_id`` (polled by the streaming loop)."""
        try:
            await self._kv.set(self._cancel_key(user_id, request_id), "1", ttl_seconds=ttl)
        except Exception:  # pragma: no cover
            logger.warning("failed to set cancel flag for %s/%s", user_id, request_id)

    async def is_cancelled(self, user_id: str, request_id: str) -> bool:
        """Whether a cross-worker cancel was requested for ``request_id``."""
        try:
            return (await self._kv.get(self._cancel_key(user_id, request_id))) is not None
        except Exception:  # pragma: no cover
            return False


_registry: StreamRegistry | None = None


def get_stream_registry() -> StreamRegistry:
    """Return the process-wide :class:`StreamRegistry` (built on first use)."""
    global _registry
    if _registry is None:
        from app.auth.runtime import get_kvstore

        _registry = StreamRegistry(get_kvstore())
    return _registry
