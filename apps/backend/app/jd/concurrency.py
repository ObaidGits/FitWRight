"""Distributed SingleFlight for JD extraction (§23 of enhancement plan).

Ensures only one extraction runs per canonical URL, even across multiple
workers. Followers wait for the leader's result via polling.

Degradation: When KVStore is not Redis (local/DB), falls back to per-process
asyncio.Lock (v1.0 behavior — no distributed dedup but no crashes either).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

__all__ = ["single_flight"]

# Per-process locks for local fallback (no Redis)
_local_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_local_results: dict[str, str] = {}

_LOCK_TTL = 10  # seconds
_POLL_INTERVAL = 0.5  # seconds
_FOLLOWER_TIMEOUT = 25  # seconds


def _flight_key(canonical_url: str) -> str:
    h = hashlib.sha256(canonical_url.encode()).hexdigest()[:24]
    return f"jd:flight:{h}"


def _result_key(canonical_url: str) -> str:
    h = hashlib.sha256(canonical_url.encode()).hexdigest()[:24]
    return f"jd:flight:result:{h}"


async def single_flight(canonical_url: str, pipeline_fn) -> dict:
    """Execute pipeline_fn with SingleFlight deduplication.

    If another request for the same canonical_url is already in-flight,
    wait for its result instead of running the pipeline again.

    Returns: the pipeline result (dict).
    """
    from app.auth.runtime import get_kvstore
    from app.auth.kvstore.redis_store import RedisKVStore

    kv = get_kvstore()

    if isinstance(kv, RedisKVStore):
        return await _distributed_flight(kv, canonical_url, pipeline_fn)
    else:
        return await _local_flight(canonical_url, pipeline_fn)


async def _distributed_flight(kv, canonical_url: str, pipeline_fn) -> dict:
    """Distributed SingleFlight using Redis SET NX + polling."""
    lock_key = _flight_key(canonical_url)
    result_key = _result_key(canonical_url)
    leader_id = f"{id(asyncio.current_task())}:{time.time()}"

    # Try to become leader
    existing = await kv.get(lock_key)
    if not existing:
        # Attempt to acquire (SET NX equivalent via set-if-absent)
        await kv.set(lock_key, leader_id, ttl_seconds=_LOCK_TTL)
        check = await kv.get(lock_key)
        if check == leader_id:
            # We are the leader — run the pipeline
            try:
                result = await _run_with_heartbeat(kv, lock_key, leader_id, pipeline_fn)
                # Publish result for followers
                await kv.set(result_key, json.dumps(result), ttl_seconds=30)
                return result
            finally:
                await kv.delete(lock_key)

    # We are a follower — poll for leader's result
    return await _poll_for_result(kv, result_key, canonical_url, pipeline_fn)


async def _run_with_heartbeat(kv, lock_key: str, leader_id: str, pipeline_fn) -> dict:
    """Run pipeline while refreshing lock TTL every 5s (heartbeat)."""
    heartbeat_task = asyncio.create_task(_heartbeat_loop(kv, lock_key, leader_id))
    try:
        return await pipeline_fn()
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _heartbeat_loop(kv, lock_key: str, leader_id: str):
    """Refresh lock TTL every 5s while leader is working."""
    while True:
        await asyncio.sleep(5)
        try:
            await kv.set(lock_key, leader_id, ttl_seconds=_LOCK_TTL)
        except Exception:
            break


async def _poll_for_result(kv, result_key: str, canonical_url: str, pipeline_fn) -> dict:
    """Poll for leader's result. If leader dies (timeout), promote self."""
    deadline = time.time() + _FOLLOWER_TIMEOUT
    while time.time() < deadline:
        raw = await kv.get(result_key)
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                break
        await asyncio.sleep(_POLL_INTERVAL)

    # Leader died or timed out — promote self (run pipeline)
    logger.info("JD SingleFlight: follower promoting to leader for %s", canonical_url)
    return await pipeline_fn()


async def _local_flight(canonical_url: str, pipeline_fn) -> dict:
    """Per-process fallback when Redis is not available."""
    lock = _local_locks[canonical_url]

    # Check if result already exists from a concurrent request
    if canonical_url in _local_results:
        try:
            return json.loads(_local_results[canonical_url])
        except (json.JSONDecodeError, TypeError):
            pass

    async with lock:
        # Double-check after acquiring lock
        if canonical_url in _local_results:
            try:
                return json.loads(_local_results[canonical_url])
            except (json.JSONDecodeError, TypeError):
                pass

        # We are the leader — run pipeline
        result = await pipeline_fn()

        # Cache for other waiters (short-lived, cleared after 60s)
        _local_results[canonical_url] = json.dumps(result)
        asyncio.get_event_loop().call_later(60, lambda: _local_results.pop(canonical_url, None))

        return result
