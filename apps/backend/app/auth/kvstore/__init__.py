"""Pluggable KVStore package (ADR-6).

Exposes the :class:`KVStore` contract, the three concrete adapters
(:class:`LocalKVStore`, :class:`RedisKVStore`, :class:`DBKVStore`), the
:class:`KVLock` primitive, and the :func:`kvstore_from_url` adapter selector.
"""

from app.auth.kvstore.base import KVLock, KVStore
from app.auth.kvstore.db import DBKVStore
from app.auth.kvstore.factory import kvstore_from_url, url_needs_db_engine
from app.auth.kvstore.local import LocalKVStore
from app.auth.kvstore.redis_store import RedisKVStore

__all__ = [
    "KVStore",
    "KVLock",
    "LocalKVStore",
    "DBKVStore",
    "RedisKVStore",
    "kvstore_from_url",
    "url_needs_db_engine",
]
