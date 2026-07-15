"""Multi-layer cache for JD extraction v2 (§24 of enhancement plan).

Implements independent cache layers with per-layer TTL and version-keyed
invalidation. Uses the existing pluggable KVStore (Redis/Local/DB).

Layers:
  L0: Raw HTTP response (html + headers) — 60 min
  L2: Structured extraction result — 60 min (version-keyed)
  L5: Error state — 5 min (short, allows retry)

Cache lookup order: L2 → L5 → full pipeline
On extractor upgrade: L2 invalidates (key includes version); L0 remains (re-extract from cached HTML).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from app.jd.models import ConfidenceResult, ExtractionExplanation, ExtractionResult

logger = logging.getLogger(__name__)

__all__ = ["JdCache"]

_VERSION = "2.1.0"  # Bump on extractor logic changes to invalidate L2

# TTL classes (seconds)
TTL_L0_HTML = 3600       # 60 min — raw HTML
TTL_L2_HIGH = 3600       # 60 min — HIGH confidence result
TTL_L2_MEDIUM = 1800     # 30 min — MEDIUM confidence result
TTL_L2_LOW = 600         # 10 min — LOW confidence (re-try soon)
TTL_L5_ERROR = 300       # 5 min — error state (allow quick retry)


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:32]


class JdCache:
    """Multi-layer JD extraction cache backed by KVStore."""

    def __init__(self, kv):
        """Initialize with a KVStore instance (from get_kvstore())."""
        self._kv = kv

    # ------------------------------------------------------------------
    # L2: Structured extraction result (version-keyed)
    # ------------------------------------------------------------------

    def _l2_key(self, canonical_url: str) -> str:
        return f"jd:l2:{_VERSION}:{_hash(canonical_url)}"

    async def get_result(self, canonical_url: str) -> ExtractionResult | None:
        """Look up L2 cached result. Returns None on miss."""
        key = self._l2_key(canonical_url)
        raw = await self._kv.get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return _deserialize_result(data)
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    async def set_result(self, canonical_url: str, result: ExtractionResult) -> None:
        """Store extraction result in L2 cache with confidence-based TTL."""
        key = self._l2_key(canonical_url)
        ttl = (
            TTL_L2_HIGH if result.confidence.level == "HIGH"
            else TTL_L2_MEDIUM if result.confidence.level == "MEDIUM"
            else TTL_L2_LOW
        )
        data = _serialize_result(result)
        try:
            await self._kv.set(key, json.dumps(data), ttl_seconds=ttl)
        except Exception:
            logger.debug("JD cache L2 write failed", exc_info=True)

    # ------------------------------------------------------------------
    # L5: Error state cache (short TTL)
    # ------------------------------------------------------------------

    def _l5_key(self, canonical_url: str) -> str:
        return f"jd:l5:{_hash(canonical_url)}"

    async def get_error(self, canonical_url: str) -> dict | None:
        """Check if URL recently failed. Returns error dict or None."""
        key = self._l5_key(canonical_url)
        raw = await self._kv.get(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    async def set_error(self, canonical_url: str, reason: str) -> None:
        """Cache a failure for 5 min (prevents hammering broken URLs)."""
        key = self._l5_key(canonical_url)
        try:
            await self._kv.set(key, json.dumps({
                "reason": reason, "cached_at": time.time()
            }), ttl_seconds=TTL_L5_ERROR)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # L6: Content-fingerprint index (§22 near-duplicate linking)
    # ------------------------------------------------------------------

    def _fp_key(self, fingerprint: str) -> str:
        return f"jd:fp:{fingerprint[:32]}"

    async def get_url_by_fingerprint(self, fingerprint: str) -> str | None:
        """Return the canonical URL previously seen for this content fingerprint."""
        if not fingerprint:
            return None
        try:
            return await self._kv.get(self._fp_key(fingerprint))
        except Exception:
            return None

    async def register_fingerprint(self, fingerprint: str, canonical_url: str) -> None:
        """Map a content fingerprint → canonical URL (24h TTL) for near-dup linking."""
        if not fingerprint or not canonical_url:
            return
        try:
            await self._kv.set(self._fp_key(fingerprint), canonical_url, ttl_seconds=86400)
        except Exception:
            logger.debug("JD fingerprint index write failed", exc_info=True)

    # ------------------------------------------------------------------
    # L0: Raw HTML cache (for re-extraction without re-fetch)
    # ------------------------------------------------------------------

    def _l0_key(self, canonical_url: str) -> str:
        return f"jd:l0:{_hash(canonical_url)}"

    async def get_html(self, canonical_url: str) -> str | None:
        """Get cached raw HTML (L0). Returns None on miss."""
        return await self._kv.get(self._l0_key(canonical_url))

    async def set_html(self, canonical_url: str, html: str) -> None:
        """Cache raw HTML for re-extraction on extractor upgrade."""
        key = self._l0_key(canonical_url)
        # Cap HTML at 500KB to avoid KV bloat
        if len(html) > 500_000:
            return
        try:
            await self._kv.set(key, html, ttl_seconds=TTL_L0_HTML)
        except Exception:
            logger.debug("JD cache L0 write failed", exc_info=True)


def _serialize_result(r: ExtractionResult) -> dict:
    """Serialize ExtractionResult for JSON storage."""
    return {
        "content": r.content,
        "source": r.source,
        "confidence_level": r.confidence.level,
        "confidence_score": r.confidence.score,
        "confidence_reasons": r.confidence.reasons,
        "canonical_url": r.canonical_url,
        "submitted_url": r.submitted_url,
        "partial": r.partial,
        "title": r.title.value if r.title else None,
        "company": r.company.value if r.company else None,
        "location": r.location.value if r.location else None,
        "language": r.language or None,
        "fingerprint": r.fingerprint or None,
        "error_code": r.error_code,
        "cached_at": time.time(),
    }


def _deserialize_result(data: dict) -> ExtractionResult:
    """Deserialize ExtractionResult from JSON storage."""
    from app.jd.models import FieldProvenance

    result = ExtractionResult(
        content=data["content"],
        source=data.get("source", "dom_semantic"),
        confidence=ConfidenceResult(
            level=data["confidence_level"],
            score=data["confidence_score"],
            reasons=data.get("confidence_reasons", []) + ["(from cache)"],
        ),
        canonical_url=data.get("canonical_url", ""),
        submitted_url=data.get("submitted_url", ""),
        partial=data.get("partial", False),
        language=data.get("language") or "",
        fingerprint=data.get("fingerprint") or "",
        error_code=data.get("error_code"),
        explanation=ExtractionExplanation(summary="Served from cache."),
    )
    if data.get("title"):
        result.title = FieldProvenance(value=data["title"], source=result.source, confidence=90, extractor_version=_VERSION)
    if data.get("company"):
        result.company = FieldProvenance(value=data["company"], source=result.source, confidence=85, extractor_version=_VERSION)
    if data.get("location"):
        result.location = FieldProvenance(value=data["location"], source=result.source, confidence=80, extractor_version=_VERSION)
    return result
