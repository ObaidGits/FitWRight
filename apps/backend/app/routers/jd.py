"""JD-from-URL endpoint (P3 §D, Requirement 9).

``POST /jobs/fetch-url`` fetches + extracts a job description server-side with the
full SSRF guard. User-scoped, auth-guarded, behind the ``JD_FROM_URL``
kill-switch. Failures surface a single opaque ``fetch_failed`` (no internal
detail); rate limits return 429.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_effective_user_id
from app.config import settings
from app.jd.service import JdFetchError, JdRateLimited, fetch_jd_from_url
from app.schemas.jd import (
    ExtractRenderedRequest,
    FetchUrlRequest,
    FetchUrlResponse,
    WebhookJobPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["JD from URL"])


def _require_enabled() -> None:
    if not settings.jd_from_url_enabled:
        raise HTTPException(status_code=404, detail="jd_from_url_disabled")


def _result_to_response(result) -> FetchUrlResponse:
    """Map a v2 ExtractionResult -> the (backward-compatible) response schema."""
    return FetchUrlResponse(
        content=result.content,
        low_confidence=result.low_confidence,
        source_url=result.canonical_url or result.submitted_url,
        schema_version=result.schema_version,
        confidence_level=result.confidence.level,
        confidence_score=result.confidence.score,
        source=result.source,
        partial=result.partial,
        error_code=result.error_code,
        language=result.language or None,
        suggestions=result.explanation.suggestions or None,
        warnings=result.explanation.warnings or None,
    )


@router.post("/fetch-url", response_model=FetchUrlResponse)
async def fetch_url(
    request: FetchUrlRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> FetchUrlResponse:
    """Fetch + extract a JD from a URL (SSRF-hardened, cached, rate-limited)."""
    if settings.jd_v2_enabled:
        result = await _fetch_v2(request, user_id)
    else:
        result = await _fetch_v1(request, user_id)
    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_JD_PARSE
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_JD_PARSE, 1)
    except Exception:
        pass  # metrics never break user operations
    return result


async def _fetch_v1(request: FetchUrlRequest, user_id: str) -> FetchUrlResponse:
    """Legacy v1 pipeline (BeautifulSoup only)."""
    try:
        result = await fetch_jd_from_url(user_id, request.url, use_ai=request.use_ai)
    except JdRateLimited:
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")
    except JdFetchError:
        raise HTTPException(status_code=422, detail="fetch_failed")
    return FetchUrlResponse(**result)


async def _fetch_v2(request: FetchUrlRequest, user_id: str) -> FetchUrlResponse:
    """v2 cascade pipeline (API -> JSON-LD -> DOM)."""
    from app.jd.orchestrator import orchestrate_v2

    try:
        # Rate limit (reuse existing v1 rate limiter)
        from app.jd.service import _enforce_rate_limits, JdRateLimited as RL
        await _enforce_rate_limits(user_id)
    except Exception:
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    result = await orchestrate_v2(
        user_id, request.url,
        use_ai=request.use_ai,
        timeout=float(settings.jd_pipeline_timeout),
    )

    # If AI cleanup is requested and we got content, apply it
    if request.use_ai and result.content and result.confidence.level != "LOW":
        from app.jd.service import _maybe_clean
        cleaned = await _maybe_clean(user_id, result.to_legacy_dict())
        if cleaned.get("content"):
            result.content = cleaned["content"]

    # Return v1-compatible shape + optional v2 metadata. The frontend detects v2
    # by the presence of `schema_version` and can surface confidence/errors.
    return _result_to_response(result)


@router.post("/extract-rendered", response_model=FetchUrlResponse)
async def extract_rendered(
    request: ExtractRenderedRequest,
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> FetchUrlResponse:
    """Browser-extension fallback: extract from the user's already-rendered DOM.

    For pages we cannot fetch server-side (auth walls, anti-bot, client-only
    SPAs), a browser extension captures the DOM the user is viewing and posts it
    here. We run the static extractors on it - no network, no scraping.
    """
    if not settings.jd_extension_fallback_enabled:
        raise HTTPException(status_code=404, detail="extension_fallback_disabled")

    # Reuse the shared rate limiter (same abuse surface as fetch-url).
    try:
        from app.jd.service import _enforce_rate_limits
        await _enforce_rate_limits(user_id)
    except Exception:
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")

    from app.jd.orchestrator import _finalize
    from app.jd.rendered import extract_from_rendered

    result = _finalize(extract_from_rendered(request.url, request.html))

    if request.use_ai and result.content and result.confidence.level != "LOW":
        from app.jd.service import _maybe_clean
        cleaned = await _maybe_clean(user_id, result.to_legacy_dict())
        if cleaned.get("content"):
            result.content = cleaned["content"]

    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_JD_PARSE
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_JD_PARSE, 1)
    except Exception:
        pass  # metrics never break user operations

    return _result_to_response(result)


@router.get("/jd/adapter-health")
async def adapter_health(
    user_id: str = Depends(get_effective_user_id),
    _: None = Depends(_require_enabled),
) -> dict:
    """Per-adapter circuit-breaker health snapshot (self-healing status)."""
    from app.jd.health import adapter_health_snapshot
    return await adapter_health_snapshot()


@router.post("/webhook")
async def employer_webhook(request: Request) -> dict:
    """Employer/ATS authoritative job push (zero-scrape, HMAC-authenticated).

    Not user-authenticated - authenticated by an HMAC-SHA256 signature over the
    raw body (header ``X-JD-Signature``) using the shared ``jd_webhook_secret``.
    Stores the posting in the extraction cache as a HIGH-confidence result.
    """
    if not settings.jd_webhook_enabled or not settings.jd_webhook_secret:
        raise HTTPException(status_code=404, detail="webhook_disabled")

    raw = await request.body()
    if len(raw) > 512 * 1024:
        raise HTTPException(status_code=413, detail="payload_too_large")

    from app.jd.webhook import verify_signature, ingest_webhook

    provided = request.headers.get("x-jd-signature") or request.headers.get("X-JD-Signature")
    if not verify_signature(settings.jd_webhook_secret, raw, provided):
        raise HTTPException(status_code=401, detail="invalid_signature")

    import json as _json
    try:
        payload = _json.loads(raw)
        # Validate/normalize via the pydantic schema (length caps, types).
        model = WebhookJobPayload(**payload)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid_payload")

    try:
        return await ingest_webhook(model.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
