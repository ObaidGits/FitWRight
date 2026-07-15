"""Public "Contact" form endpoint.

An unauthenticated, production-hardened submission endpoint. The shared
production defenses (per-IP rate limiting, honeypot + submit-timing spam
heuristics, de-duplication, durable KVStore persistence) live in
:mod:`app.services.intake`; this router adds contact-specific validation and
best-effort delivery (owner notification + sender acknowledgement).
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from fastapi import APIRouter, Request

from app.auth import get_email_sender
from app.auth.email import (
    build_contact_acknowledgement_email,
    build_contact_notification_email,
    send_email_safe,
)
from app.config import settings
from app.routers._auth_deps import client_ip
from app.schemas.contact import ContactRequest, ContactResponse
from app.services.intake import (
    check_and_reserve_dedup,
    enforce_intake_rate_limit,
    hash_ip,
    looks_like_bot,
    persist_record,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contact", tags=["Contact"])

_ESTIMATED_RESPONSE = "within 1–2 business days"


def _fingerprint(payload: ContactRequest) -> str:
    raw = f"{payload.email.lower()}|{payload.subject.lower()}|{payload.message}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.post("/", response_model=ContactResponse)
async def submit_contact(request: Request, payload: ContactRequest) -> ContactResponse:
    """Accept a public contact submission (validated, rate-limited, spam-guarded)."""
    ip = client_ip(request) or "unknown"
    reference = uuid.uuid4().hex[:12]

    await enforce_intake_rate_limit("contact", ip)

    # Spam heuristics: accept-but-drop so bots gain no signal.
    if looks_like_bot(payload.company_website, payload.elapsed_ms):
        logger.info("Contact submission dropped by spam heuristics (ref=%s)", reference)
        return ContactResponse(
            message="Thanks — your message has been received.",
            reference=reference,
            estimated_response=_ESTIMATED_RESPONSE,
        )

    # De-duplicate identical submissions within a short window (double-click).
    existing = await check_and_reserve_dedup("contact", _fingerprint(payload), reference)
    if existing:
        logger.info("Duplicate contact submission collapsed (ref=%s)", existing)
        return ContactResponse(
            message="Thanks — your message has already been received.",
            reference=existing,
            estimated_response=_ESTIMATED_RESPONSE,
        )

    # Persist durably BEFORE delivery so a provider outage never loses it.
    await persist_record(
        "contact",
        reference,
        {
            "reference": reference,
            "ip_hash": hash_ip(ip),
            "name": payload.name,
            "email": payload.email,
            "subject": payload.subject,
            "message": payload.message,
            "purpose": payload.purpose,
            "company": payload.company,
            "linkedin": payload.linkedin,
            "project_type": payload.project_type,
            "budget": payload.budget,
        },
    )
    logger.info(
        "Contact received ref=%s purpose=%s from=%s subject=%r",
        reference,
        payload.purpose,
        payload.email,
        payload.subject,
    )

    # Deliver (best-effort). Owner notification → configured recipient (or
    # EMAIL_FROM fallback); acknowledgement → the submitter.
    sender = get_email_sender()
    recipient = (settings.contact_recipient_email or settings.email_from or "").strip()
    if recipient:
        await send_email_safe(
            sender,
            build_contact_notification_email(
                to=recipient,
                reference=reference,
                name=payload.name,
                email=payload.email,
                subject=payload.subject,
                message=payload.message,
                purpose=payload.purpose,
                company=payload.company,
                linkedin=payload.linkedin,
                project_type=payload.project_type,
                budget=payload.budget,
            ),
        )
    else:
        logger.warning(
            "No contact recipient configured (CONTACT_RECIPIENT_EMAIL/EMAIL_FROM); "
            "message ref=%s persisted + logged only.",
            reference,
        )

    await send_email_safe(
        sender,
        build_contact_acknowledgement_email(
            to=payload.email,
            name=payload.name,
            reference=reference,
            subject=payload.subject,
        ),
    )

    return ContactResponse(
        message="Thanks — your message has been received.",
        reference=reference,
        estimated_response=_ESTIMATED_RESPONSE,
    )
