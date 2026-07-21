"""Canonical domain-event type registry (design §Platform).

Event types are stable string constants (stored in ``outbox.event_type``).
Keeping them in one enum prevents typos between producers and consumers and
documents the full event surface in a single place. New consumers subscribe to
these without the producer knowing (decoupling - R16.1).
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    """Every domain event emitted to the outbox."""

    # Resume lifecycle (notification + search).
    RESUME_PARSED = "resume.parsed"
    RESUME_PARSE_FAILED = "resume.parse_failed"
    RESUME_UPSERTED = "resume.upserted"
    RESUME_DELETED = "resume.deleted"

    # Tailoring / AI (notification).
    AI_GENERATION_DONE = "ai.generation_done"

    # Professional profile lifecycle (analytics + future sync/notification).
    PROFILE_CREATED = "profile.created"
    PROFILE_UPDATED = "profile.updated"
    PROFILE_COMPLETED = "profile.completed"
    PROFILE_RESUME_GENERATED = "profile.resume_generated"
    PROFILE_VERSION_CREATED = "profile.version_created"
    PROFILE_IMPORTED = "profile.imported"
    MERGE_COMPLETED = "merge.completed"
    RESUME_SYNCED = "resume.synced"
    PUBLIC_SHARED = "public.shared"
    # Profile analytics/observability (consumed by the analytics service).
    PROFILE_AI_USED = "profile.ai_used"
    PROFILE_EXPORTED = "profile.exported"
    PROFILE_SEARCHED = "profile.searched"
    PUBLIC_VIEWED = "public.viewed"
    PORTFOLIO_VIEWED = "portfolio.viewed"
    PORTFOLIO_GENERATED = "portfolio.generated"
    THEME_CHANGED = "profile.theme_changed"

    # Job / application (search).
    JOB_UPSERTED = "job.upserted"
    JOB_DELETED = "job.deleted"
    APPLICATION_UPSERTED = "application.upserted"
    APPLICATION_DELETED = "application.deleted"

    # Scheduler-derived (notification).
    REMINDER_DUE = "reminder.due"
    INTERVIEW_UPCOMING = "interview.upcoming"
