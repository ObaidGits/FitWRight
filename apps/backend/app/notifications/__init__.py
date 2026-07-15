"""P3 Productivity — Notifications (design §B, Requirements 4–6).

A single-writer :class:`~app.notifications.service.NotificationService` (dedupe,
per-category preferences, priority, grouping, and an O(1) unread counter) fed by
the event outbox and by direct feature calls (reminders/interviews). DB access
is centralized + user-scoped in :mod:`app.notifications.repo`.
"""

from app.notifications.service import (
    CATEGORIES,
    PRIORITIES,
    NotificationService,
    get_notification_service,
)

__all__ = [
    "CATEGORIES",
    "PRIORITIES",
    "NotificationService",
    "get_notification_service",
]
