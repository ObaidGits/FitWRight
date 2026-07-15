"""Outbox → notification consumers (design §Platform + §B, R5.1).

Registers idempotent handlers that translate domain events into notifications
via the single-writer :class:`NotificationService`. Every handler sets a
``dedupe_key`` so the at-least-once outbox (retries, multi-worker) can never
deliver a duplicate notification for the same event (R5.2 / Property 3).

Bodies are content-safe — a title + a deep link only, never resume/JD content.
"""

from __future__ import annotations

import logging

from app.events import EventType, OutboxEvent, register_handler
from app.notifications.service import get_notification_service

logger = logging.getLogger(__name__)

__all__ = ["ensure_registered"]

_registered = False


async def _on_resume_parsed(event: OutboxEvent) -> None:
    rid = event.payload.get("resume_id")
    if not event.user_id or not rid:
        return
    await get_notification_service().notify(
        event.user_id,
        type=EventType.RESUME_PARSED.value,
        category="system",
        priority="normal",
        title="Your resume is ready",
        body="We finished processing your uploaded resume.",
        node_type="resume",
        node_id=rid,
        dedupe_key=f"resume_parsed:{rid}",
    )


async def _on_resume_parse_failed(event: OutboxEvent) -> None:
    rid = event.payload.get("resume_id")
    if not event.user_id or not rid:
        return
    await get_notification_service().notify(
        event.user_id,
        type=EventType.RESUME_PARSE_FAILED.value,
        category="system",
        priority="high",
        title="Resume processing failed",
        body="We couldn't process your resume. Open it to retry.",
        node_type="resume",
        node_id=rid,
        dedupe_key=f"resume_parse_failed:{rid}",
    )


async def _on_ai_generation_done(event: OutboxEvent) -> None:
    rid = event.payload.get("resume_id")
    if not event.user_id or not rid:
        return
    await get_notification_service().notify(
        event.user_id,
        type=EventType.AI_GENERATION_DONE.value,
        category="ai",
        priority="normal",
        title="Your tailored resume is ready",
        body="AI tailoring finished. Review the changes when you're ready.",
        node_type="resume",
        node_id=rid,
        dedupe_key=f"ai_done:{rid}",
    )


async def _on_reminder_due(event: OutboxEvent) -> None:
    rid = event.payload.get("reminder_id")
    app_id = event.payload.get("application_id")
    bucket = event.payload.get("bucket", "")
    if not event.user_id or not rid:
        return
    await get_notification_service().notify(
        event.user_id,
        type=EventType.REMINDER_DUE.value,
        category="reminder",
        priority="normal",
        title=event.payload.get("title") or "Follow-up reminder",
        body="A follow-up you scheduled is due.",
        node_type="application",
        node_id=app_id,
        group_key=f"reminder:{app_id}" if app_id else None,
        dedupe_key=f"reminder_due:{rid}:{bucket}",
    )


async def _on_interview_upcoming(event: OutboxEvent) -> None:
    iid = event.payload.get("interview_id")
    app_id = event.payload.get("application_id")
    lead = event.payload.get("lead_minutes", "")
    if not event.user_id or not iid:
        return
    await get_notification_service().notify(
        event.user_id,
        type=EventType.INTERVIEW_UPCOMING.value,
        category="interview",
        priority="high",
        title=event.payload.get("title") or "Upcoming interview",
        body="You have an interview coming up. Open it to prepare.",
        node_type="interview",
        node_id=iid,
        group_key=f"interview:{app_id}" if app_id else None,
        dedupe_key=f"interview_upcoming:{iid}:{lead}",
    )


def ensure_registered() -> None:
    """Register the notification handlers once (idempotent, import-safe)."""
    global _registered
    if _registered:
        return
    register_handler(EventType.RESUME_PARSED, _on_resume_parsed)
    register_handler(EventType.RESUME_PARSE_FAILED, _on_resume_parse_failed)
    register_handler(EventType.AI_GENERATION_DONE, _on_ai_generation_done)
    register_handler(EventType.REMINDER_DUE, _on_reminder_due)
    register_handler(EventType.INTERVIEW_UPCOMING, _on_interview_upcoming)
    _registered = True
