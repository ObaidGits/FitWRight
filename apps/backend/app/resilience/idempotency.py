"""Idempotency-key dedupe for retriable mutations (autosave) — R4.2, §4.2.

Every retriable autosave/offline-replay write carries a client-random
``Idempotency-Key``. A network retry (the client couldn't tell whether the first
attempt landed) re-sends the *same* key; the server, seeing a cached result for
``idem:{user_id}:{key}``, returns that result instead of applying the write a
second time. Combined with the version CAS this makes replays safe: an op
applied once is never applied twice (Property 4).

Keys are namespaced per user (a key from one account can never dedupe another's
write), short-TTL'd (they only need to outlive a client's retry window), and only
ever dedupe an *identical* operation — the stored fingerprint pins the request so
a key reused for different content is treated as a new write, not a false hit.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.auth.kvstore import KVStore

logger = logging.getLogger(__name__)

__all__ = ["IdempotencyCache", "IdempotencyRecord", "get_idempotency_cache"]

_PREFIX = "idem"
# Idempotency keys only need to survive a client's bounded retry window; a short
# TTL keeps the KVStore small and prevents stale replays much later.
_DEFAULT_TTL = 600.0
# Guard against a client sending an absurd key that could blow up the KV key
# space; real keys are UUID-length.
_MAX_KEY_LEN = 200


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A cached result for a previously-applied idempotent mutation."""

    fingerprint: str
    result: dict


def _valid_key(key: str | None) -> bool:
    return bool(key) and isinstance(key, str) and 0 < len(key) <= _MAX_KEY_LEN


class IdempotencyCache:
    """Per-user idempotency-key result cache over a :class:`KVStore`."""

    def __init__(self, kv: KVStore, *, ttl_seconds: float = _DEFAULT_TTL) -> None:
        self._kv = kv
        self._ttl = ttl_seconds

    @staticmethod
    def _key(user_id: str, idem_key: str) -> str:
        return f"{_PREFIX}:{user_id}:{idem_key}"

    async def get(self, user_id: str, idem_key: str | None) -> IdempotencyRecord | None:
        """Return the cached record for ``idem_key`` if present, else ``None``."""
        if not _valid_key(idem_key):
            return None
        try:
            raw = await self._kv.get(self._key(user_id, idem_key))  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - KV outage
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return IdempotencyRecord(
                fingerprint=str(data["fingerprint"]), result=dict(data["result"])
            )
        except (ValueError, TypeError, KeyError):
            return None

    async def store(
        self,
        user_id: str,
        idem_key: str | None,
        *,
        fingerprint: str,
        result: dict,
    ) -> None:
        """Cache ``result`` for ``idem_key`` under a short TTL (best-effort)."""
        if not _valid_key(idem_key):
            return
        try:
            await self._kv.set(
                self._key(user_id, idem_key),  # type: ignore[arg-type]
                json.dumps({"fingerprint": fingerprint, "result": result}),
                ttl_seconds=self._ttl,
            )
        except Exception:  # pragma: no cover
            logger.debug("idempotency store failed for %s", user_id, exc_info=True)


_cache: IdempotencyCache | None = None


def get_idempotency_cache() -> IdempotencyCache:
    """Return the process-wide :class:`IdempotencyCache` (built on first use)."""
    global _cache
    if _cache is None:
        from app.auth.runtime import get_kvstore

        _cache = IdempotencyCache(get_kvstore())
    return _cache
