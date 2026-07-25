"""In-process cache for rendered PDF bytes.

Rendering a resume PDF spins up headless Chromium and navigates to the print
route - several seconds each time. The *output* is a pure function of the
resume content + resolved appearance settings + page size + locale, so once
rendered it can be reused until any of those change. This cache makes repeat
downloads (same resume, re-download, resume + cover letter, tweak-and-retry)
effectively instant.

Design:
- Keyed by a content+settings fingerprint (never the volatile print token), so
  a stale entry can never serve the wrong content: any edit changes the key.
- Bounded, TTL'd LRU held per process (no new infra). A cache miss simply
  renders as before, so correctness never depends on the cache being warm.
- asyncio.Lock-guarded so concurrent requests don't corrupt the ordering map.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from typing import Optional

__all__ = ["get_pdf_cache", "PdfRenderCache", "make_pdf_cache_key"]


def make_pdf_cache_key(*, kind: str, resume_id: str, params: str, content: object) -> str:
    """Build a stable fingerprint for a render.

    ``params`` is the resolved appearance/query string WITHOUT the print token
    (which is minted fresh per request and must not affect caching). ``content``
    is any JSON-serialisable projection of the resume whose change should bust
    the cache (the full resume dict is fine - datetimes are coerced via str).
    """
    try:
        content_blob = json.dumps(content, sort_keys=True, default=str)
    except (TypeError, ValueError):
        content_blob = repr(content)
    digest = hashlib.sha256(
        f"{kind}\0{resume_id}\0{params}\0{content_blob}".encode("utf-8")
    ).hexdigest()
    return f"{kind}:{resume_id}:{digest[:32]}"


class PdfRenderCache:
    """Bounded, TTL'd LRU of rendered PDF bytes."""

    def __init__(self, *, max_entries: int, ttl_seconds: int) -> None:
        self._max = max(1, int(max_entries))
        self._ttl = max(1, int(ttl_seconds))
        self._store: "OrderedDict[str, tuple[float, bytes]]" = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[bytes]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                # Expired: drop it and miss.
                self._store.pop(key, None)
                return None
            # Mark as most-recently-used.
            self._store.move_to_end(key)
            return value

    async def set(self, key: str, value: bytes) -> None:
        if not value:
            return
        async with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)  # evict least-recently-used

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


_cache: Optional[PdfRenderCache] = None
_cache_signature: tuple[int, int] | None = None


def get_pdf_cache() -> Optional[PdfRenderCache]:
    """Return the process-wide cache, or ``None`` when disabled.

    Rebuilt if the configured size/TTL changed (tests/hot-reload).
    """
    global _cache, _cache_signature
    from app.config import settings

    if not getattr(settings, "pdf_cache_enabled", True):
        return None

    signature = (
        int(getattr(settings, "pdf_cache_max_entries", 64)),
        int(getattr(settings, "pdf_cache_ttl_seconds", 900)),
    )
    if _cache is None or _cache_signature != signature:
        _cache = PdfRenderCache(max_entries=signature[0], ttl_seconds=signature[1])
        _cache_signature = signature
    return _cache
