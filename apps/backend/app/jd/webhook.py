"""Employer webhook ingestion - zero-scrape authoritative job data (Phase 4).

Employers / ATS vendors can PUSH job descriptions directly instead of us pulling
them. A pushed posting is the MOST authoritative source possible (the employer is
the source of truth), so it is stored in the extraction cache as a HIGH-confidence
result keyed by the posting's canonical URL. A later ``/jobs/fetch-url`` for that
URL then serves the pushed data with zero network I/O and zero scraping.

Security:
- Feature-flagged (``jd_webhook_enabled``); the endpoint 404s when off.
- Authenticated by an HMAC-SHA256 signature over the raw body using a shared
  secret (``jd_webhook_secret``), compared in constant time. No secret -> refuse.
- Payload is validated + length-capped; the content is treated as data, never
  instructions (same prompt-injection posture as scraped content).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re

from app.jd.canonicalize import canonicalize_url
from app.jd.fingerprint import content_fingerprint
from app.jd.i18n import detect_language
from app.jd.models import (
    ConfidenceResult,
    ExtractionExplanation,
    ExtractionResult,
    FieldProvenance,
)

logger = logging.getLogger(__name__)

__all__ = ["verify_signature", "build_result_from_payload", "ingest_webhook", "MAX_WEBHOOK_BYTES"]

MAX_WEBHOOK_BYTES = 512 * 1024
_VERSION = "webhook-1.0.0"


def verify_signature(secret: str, body: bytes, provided_sig: str | None) -> bool:
    """Constant-time HMAC-SHA256 verification of the raw request body.

    Accepts an optional ``sha256=`` prefix (GitHub-style). Returns False on any
    missing/misformatted input - never raises.
    """
    if not secret or not provided_sig:
        return False
    sig = provided_sig.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256="):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


def _strip_html(s: str) -> str:
    if not s:
        return ""
    text = re.sub(r"<[^>]+>", "\n", s)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_result_from_payload(payload: dict) -> ExtractionResult:
    """Construct an authoritative ExtractionResult from a webhook payload.

    Required: ``url`` and (``description`` or ``description_html``).
    Optional: ``title``, ``company``, ``location``, ``employment_type``, ``salary``.
    """
    url = (payload.get("url") or "").strip()
    if not url:
        raise ValueError("missing_url")

    title = (payload.get("title") or "").strip()
    company = (payload.get("company") or "").strip()
    location = (payload.get("location") or "").strip()
    employment_type = (payload.get("employment_type") or "").strip()
    salary = (payload.get("salary") or "").strip()

    description = payload.get("description") or ""
    if not description and payload.get("description_html"):
        description = _strip_html(payload["description_html"])
    description = (description or "").strip()
    if not description:
        raise ValueError("missing_description")

    canonical = canonicalize_url(url)
    full_content = f"{title}\n\n{description}" if title and description else (description or title)

    def prov(value: str, conf: int, loc: str):
        return FieldProvenance(
            value=value, source="platform_api", confidence=conf,
            extractor_version=_VERSION, raw_location=loc,
        ) if value else None

    result = ExtractionResult(
        content=full_content,
        title=prov(title, 99, "webhook:title"),
        company=prov(company, 99, "webhook:company"),
        location=prov(location, 95, "webhook:location"),
        employment_type=prov(employment_type, 95, "webhook:employment_type"),
        salary=prov(salary, 95, "webhook:salary"),
        confidence=ConfidenceResult(
            level="HIGH", score=99,
            reasons=["Authoritative employer webhook (zero-scrape)"],
        ),
        explanation=ExtractionExplanation(
            summary="Provided directly by the employer via webhook (most authoritative source).",
        ),
        source="platform_api",
        canonical_url=canonical,
        submitted_url=url,
    )
    result.language = detect_language(text=full_content) or ""
    result.fingerprint = content_fingerprint(title, company, location, full_content)
    return result


async def ingest_webhook(payload: dict) -> dict:
    """Validate + store a pushed job posting in the extraction cache.

    Returns a small ack dict. Raises ValueError on invalid payload.
    """
    result = build_result_from_payload(payload)

    from app.jd.orchestrator import _get_cache
    cache = _get_cache()
    await cache.set_result(result.canonical_url, result)
    await cache.register_fingerprint(result.fingerprint, result.canonical_url)

    try:
        from app.productivity.metrics import get_productivity_metrics
        get_productivity_metrics().incr("jd_webhook_ingested_total")
    except Exception:
        pass

    logger.info("JD webhook: ingested authoritative posting (%d chars)", len(result.content))
    return {
        "status": "ok",
        "canonical_url": result.canonical_url,
        "fingerprint": result.fingerprint,
        "content_length": len(result.content),
    }
