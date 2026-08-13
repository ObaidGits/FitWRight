"""Content-addressed search-result cache for Job Discovery (task 0.9).

A thin, deterministic cache layer sitting on top of the ``discovery_cache``
table accessors in :mod:`app.database`. Recommendation responses are keyed by a
SHA-256 of ``(resume_version, query, filters)`` so the same resume + same search
intent + same filters always resolves to the same row, and any change to any of
those three inputs produces a distinct key (Req 6.2).

TTL comes from ``JOB_DISCOVERY_CACHE_TTL_SECONDS`` (config, Req 6.4) unless an
explicit override is supplied. Expiry is enforced by the underlying accessor:
an expired row reads back as a miss.

Design reference: ``.kiro/specs/job-discovery/design.md`` §6 (caching).
Requirements: 6.2, 6.4.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, fields as dataclass_fields, is_dataclass
from typing import Any

from app.config import settings
from app.database import Database
from app.job_discovery.models import SearchFilters, SearchQuery

# Fields of ``SearchQuery`` that define the *search intent*. ``degraded`` is a
# runtime status flag and ``resume_version`` is passed to the key separately, so
# neither participates in the query component of the key -- otherwise an LLM vs
# fallback run of an identical search would miss the cache spuriously.
_QUERY_KEY_FIELDS = (
    "titles",
    "search_string",
    "seniority",
    "location",
    "country_indeed",
)


def _normalize(value: Any) -> Any:
    """Coerce an input into a canonical, JSON-serializable structure.

    Dataclasses/dicts become key-sorted dicts, sequences are normalized
    element-wise, and scalars pass through. ``json.dumps(sort_keys=True)`` on
    the result is stable across process runs and insertion order.
    """
    if value is None:
        return None
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if hasattr(value, "model_dump"):  # pydantic BaseModel
        value = value.model_dump()
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def _query_component(query: SearchQuery | dict | str | None) -> Any:
    """Reduce a query to just its intent-defining fields for keying."""
    if query is None:
        return None
    if isinstance(query, str):
        return query
    data = _normalize(query)
    if isinstance(data, dict):
        return {f: data.get(f) for f in _QUERY_KEY_FIELDS}
    return data


def _filters_component(filters: SearchFilters | dict | None) -> Any:
    """Reduce filters to a canonical shape, whichever form they arrived in.

    A dict and the equivalent dataclass must produce the same key, or the same
    search cached by one call style is a guaranteed miss for the other. They did
    not: `asdict()` on the dataclass emits every field, including the ones a
    caller's dict simply omits (`distance`, `job_type`), so two descriptions of one
    search hashed differently. Coercing the dict through `SearchFilters` first
    gives both paths the same field set - and means adding a field to the dataclass
    can never silently split the keyspace again.

    Unknown keys are dropped rather than rejected: a caller passing an extra field
    is describing the same search, and refusing it would turn a cache-key helper
    into a validator.
    """
    if filters is None:
        return None
    if isinstance(filters, dict):
        known = {f.name for f in dataclass_fields(SearchFilters)}
        try:
            filters = SearchFilters(**{k: v for k, v in filters.items() if k in known})
        except TypeError:
            # Not coercible (missing a required field) - fall back to the raw dict
            # rather than failing a cache lookup over it.
            return _normalize(filters)
    return _normalize(filters)


def make_cache_key(
    resume_version: str | None,
    query: SearchQuery | dict | str | None,
    filters: SearchFilters | dict | None,
) -> str:
    """Derive the deterministic SHA-256 cache key for a search (Req 6.2).

    The key is ``sha256`` over a canonical JSON encoding of
    ``{resume_version, query, filters}`` and is 64 hex chars -- exactly the
    width of the ``discovery_cache.cache_key`` column.
    """
    material = {
        "resume_version": resume_version,
        "query": _query_component(query),
        "filters": _filters_component(filters),
    }
    encoded = json.dumps(
        material, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SearchCache:
    """Content-addressed cache for recommendation responses (design §6)."""

    def __init__(self, db: Database, ttl_seconds: int | None = None) -> None:
        self._db = db
        self._ttl = (
            int(ttl_seconds)
            if ttl_seconds is not None
            else int(settings.JOB_DISCOVERY_CACHE_TTL_SECONDS)
        )

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @staticmethod
    def make_key(
        resume_version: str | None,
        query: SearchQuery | dict | str | None,
        filters: SearchFilters | dict | None,
    ) -> str:
        """Expose :func:`make_cache_key` as a method for convenience/testing."""
        return make_cache_key(resume_version, query, filters)

    async def get(
        self,
        resume_version: str | None,
        query: SearchQuery | dict | str | None,
        filters: SearchFilters | dict | None,
    ) -> Any | None:
        """Return the cached payload for this search, or ``None`` on miss.

        A miss is either an absent row or an expired one -- the underlying
        accessor treats an expired row as absent (Req 6.4).
        """
        key = make_cache_key(resume_version, query, filters)
        return await self._db.get_discovery_cache(key)

    async def store(
        self,
        resume_version: str | None,
        query: SearchQuery | dict | str | None,
        filters: SearchFilters | dict | None,
        payload: Any,
    ) -> str:
        """Persist ``payload`` under this search's key with the configured TTL.

        Returns the cache key so callers can log/trace the write.
        """
        key = make_cache_key(resume_version, query, filters)
        await self._db.put_discovery_cache(key, payload, self._ttl)
        return key


__all__ = ["SearchCache", "make_cache_key"]
