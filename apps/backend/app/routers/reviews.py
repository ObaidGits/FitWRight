"""Public product review endpoint (Connect page).

Unauthenticated, sharing the same production defenses as the contact endpoint
via :mod:`app.services.intake` (per-IP rate limiting, honeypot + submit-timing,
de-duplication, durable persistence). Reviews are stored with a ``pending``
status - they are moderated before ever appearing publicly, so nothing
user-submitted is rendered without review (XSS / abuse safe by construction).
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, Request

from app.auth import get_email_sender
from app.auth.email import build_review_notification_email, send_email_safe
from app.config import settings
from app.routers._auth_deps import client_ip
from app.schemas.reviews import ReviewRequest, ReviewResponse
from app.services.intake import (
    check_and_reserve_dedup,
    enforce_intake_rate_limit,
    hash_ip,
    looks_like_bot,
    persist_record,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reviews", tags=["Reviews"])


def _fingerprint(payload: ReviewRequest) -> str:
    raw = f"{payload.title.lower()}|{payload.body}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/", response_model=ReviewResponse)
async def submit_review(request: Request, payload: ReviewRequest) -> ReviewResponse:
    """Accept a public product review (validated, rate-limited, spam-guarded)."""
    ip = client_ip(request) or "unknown"
    reference = uuid.uuid4().hex[:12]

    await enforce_intake_rate_limit("reviews", ip)

    if looks_like_bot(payload.company_website, payload.elapsed_ms):
        logger.info("Review dropped by spam heuristics (ref=%s)", reference)
        return ReviewResponse(message="Thanks for the review!", reference=reference)

    existing = await check_and_reserve_dedup("reviews", _fingerprint(payload), reference)
    if existing:
        return ReviewResponse(message="Thanks - we already have this review.", reference=existing)

    await persist_record(
        "reviews",
        reference,
        {
            "reference": reference,
            "status": "pending",  # moderated before public display
            "ip_hash": hash_ip(ip),
            "rating": payload.rating,
            "title": payload.title,
            "body": payload.body,
            "name": payload.name,
            "email": payload.email,
        },
    )
    logger.info("Review received ref=%s rating=%d title=%r", reference, payload.rating, payload.title)

    recipient = (settings.contact_recipient_email or settings.email_from or "").strip()
    if recipient:
        await send_email_safe(
            get_email_sender(),
            build_review_notification_email(
                to=recipient,
                reference=reference,
                rating=payload.rating,
                title=payload.title,
                body=payload.body,
                name=payload.name,
                email=payload.email,
            ),
        )
    else:
        logger.warning("No review recipient configured; review ref=%s persisted + logged only.", reference)

    return ReviewResponse(
        message="Thank you for the review - it means a lot and helps shape what comes next.",
        reference=reference,
    )
