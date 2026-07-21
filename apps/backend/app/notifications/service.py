"""NotificationService - the single notification writer (design §B, R4-R6).

Every notification (in-app + optional email) is created here so dedupe,
preferences, priority, grouping, and the unread counter are enforced in exactly
one place (R16.3). Content is **content-safe**: titles/bodies are stripped of
control characters (prevents CRLF/header injection when a title becomes an email
subject) and length-bounded; emails carry only a title + a deep link, never
resume/JD content or secrets (R6.2).

Email delivery is decoupled: :meth:`notify` persists the row; a worker
(:meth:`process_pending_emails` / :meth:`process_digests`) sends via the pluggable
``EmailSender`` honoring per-category email prefs + the digest setting, with a
best-effort send that never blocks the creator.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.notifications.repo import get_notification_repo

logger = logging.getLogger(__name__)

__all__ = ["CATEGORIES", "PRIORITIES", "NotificationService", "get_notification_service"]

CATEGORIES: tuple[str, ...] = ("system", "reminder", "interview", "ai", "security")
PRIORITIES: tuple[str, ...] = ("low", "normal", "high")

_TITLE_MAX = 200
_BODY_MAX = 500

# node_type -> frontend path for the deep link (content-carrying, never content).
# Paths match the real app routes (see the sidebar/command-palette NAV_HREF).
_NODE_PATHS = {
    "resume": "/resumes/{id}",
    "application": "/applications/{id}",
    "job": "/applications",
    "interview": "/agenda?interview={id}",
    "reminder": "/agenda?reminder={id}",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize(text: str | None, limit: int) -> str | None:
    """Strip control chars (incl. CR/LF) + collapse whitespace + length-bound.

    Notification text is plain (never HTML - React escapes on render) and a title
    can become an email subject, so control characters must never survive
    (header-injection / log-injection safety).
    """
    if text is None:
        return None
    cleaned = "".join(ch for ch in text if ch == " " or (ch.isprintable() and ch not in "\r\n"))
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 3].rstrip() + "..."
    return cleaned


class NotificationService:
    """The sole writer of notifications."""

    def __init__(self, repo=None) -> None:
        self._repo = repo or get_notification_repo()

    async def notify(
        self,
        user_id: str,
        *,
        type: str,
        title: str,
        category: str = "system",
        priority: str = "normal",
        body: str | None = None,
        node_type: str | None = None,
        node_id: str | None = None,
        group_key: str | None = None,
        dedupe_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a notification honoring prefs + dedupe; returns the row or None.

        ``None`` when the user has fully opted out of the category (no in-app and
        no email) or when a same-``dedupe_key`` notification already exists
        (idempotent - duplicate delivery impossible, R5.2).
        """
        if category not in CATEGORIES:
            category = "system"
        if priority not in PRIORITIES:
            priority = "normal"
        safe_title = _sanitize(title, _TITLE_MAX) or "(notification)"
        safe_body = _sanitize(body, _BODY_MAX)

        delivery = await self._repo.resolve_delivery(user_id, category)
        if not delivery["in_app"] and not delivery["email"]:
            return None

        return await self._repo.create(
            user_id,
            type=type,
            category=category,
            priority=priority,
            title=safe_title,
            body=safe_body,
            node_type=node_type,
            node_id=node_id,
            group_key=group_key,
            dedupe_key=dedupe_key,
            surfaced=delivery["in_app"],
        )

    # -- email workers ------------------------------------------------------

    def _deep_link(self, node_type: str | None, node_id: str | None) -> str | None:
        if not node_type or not node_id:
            return None
        from app.config import settings

        path = _NODE_PATHS.get(node_type)
        if not path:
            return None
        return f"{settings.frontend_base_url.rstrip('/')}{path.format(id=node_id)}"

    async def _user_email(self, user_id: str) -> str | None:
        from app import database
        from app.models import User

        async with database.db.session_factory() as session:
            user = await session.get(User, user_id)
            return user.email if user and user.email else None

    def _build_message(self, to: str, items: list[dict[str, Any]]):
        from app.auth.email import EmailMessage

        if len(items) == 1:
            n = items[0]
            subject = _sanitize(n["title"], _TITLE_MAX) or "FitWright notification"
            lines = [n["title"]]
            if n.get("body"):
                lines.append("")
                lines.append(n["body"])
            link = self._deep_link(n.get("node_type"), n.get("node_id"))
            if link:
                lines += ["", link]
            return EmailMessage(to=to, subject=subject, text_body="\n".join(lines))
        # Digest: one email batching several items (content-safe titles + links).
        subject = f"FitWright: {len(items)} updates"
        lines = ["Here's what's new on FitWright:", ""]
        for n in items:
            link = self._deep_link(n.get("node_type"), n.get("node_id"))
            lines.append(f"- {n['title']}" + (f" - {link}" if link else ""))
        return EmailMessage(to=to, subject=subject, text_body="\n".join(lines))

    async def process_pending_emails(self, *, limit: int = 100) -> dict[str, int]:
        """Send immediate (non-digest) emails for email-on categories (R5.3/6.2).

        Digest-mode users' low/normal items are left for :meth:`process_digests`.
        Every processed row gets ``emailed_at`` set (even skips) so the scan is
        bounded and never re-picks a row. Best-effort send: a provider outage
        marks the row for retry on the next pass (leaves emailed_at unset).
        """
        from app.auth.email import send_email_safe
        from app.auth.runtime import get_email_sender
        from app.config import settings

        # NOTIFICATIONS_EMAIL kill-switch: in-app always works; email is paused.
        # Rows are left unmarked so they flush once email is re-enabled.
        if not settings.notifications_email_enabled:
            return {"sent": 0, "skipped": 0, "deferred": 0, "disabled": 1}

        pending = await self._repo.emails_pending(limit=limit)
        sender = get_email_sender()
        sent = skipped = deferred = 0
        for n in pending:
            user_id = n["user_id"]
            delivery = await self._repo.resolve_delivery(user_id, n["category"])
            prefs = await self._repo.get_prefs(user_id)
            digest = prefs.get("digest", "off")
            if not delivery["email"]:
                await self._repo.mark_emailed(n["id"])
                skipped += 1
                continue
            if digest != "off" and n["priority"] in ("low", "normal"):
                deferred += 1  # digest job will handle it
                continue
            email = await self._user_email(user_id)
            if not email:
                await self._repo.mark_emailed(n["id"])
                skipped += 1
                continue
            ok = await send_email_safe(sender, self._build_message(email, [n]))
            if ok:
                await self._repo.mark_emailed(n["id"])
                from app.productivity.metrics import get_productivity_metrics

                get_productivity_metrics().notification_emailed()
                sent += 1
            # On failure leave emailed_at unset -> retried next pass (DLQ-like).
        return {"sent": sent, "skipped": skipped, "deferred": deferred}

    async def process_digests(self, *, limit_users: int = 500) -> dict[str, int]:
        """Batch each digest-mode user's pending low/normal email-on items (R6.1)."""
        from app.auth.email import send_email_safe
        from app.auth.runtime import get_email_sender

        from app.config import settings

        if not settings.notifications_email_enabled:
            return {"digests_sent": 0, "disabled": 1}

        batches = await self._repo.digest_batches(limit_users=limit_users)
        sender = get_email_sender()
        sent = 0
        for user_id, items in batches.items():
            # Re-check per-category email prefs - only email-on categories are
            # digested (digest_batches filters priority, not per-category prefs).
            emailable = []
            for n in items:
                delivery = await self._repo.resolve_delivery(user_id, n["category"])
                if delivery["email"]:
                    emailable.append(n)
                else:
                    await self._repo.mark_emailed(n["id"])  # skip -> don't rescan
            if not emailable:
                continue
            email = await self._user_email(user_id)
            if email:
                ok = await send_email_safe(sender, self._build_message(email, emailable))
                if ok:
                    await self._repo.mark_emailed_bulk([i["id"] for i in emailable])
                    sent += 1
        return {"digests_sent": sent}


    # -- maintenance (module-owned; retention calls this instead of the repo
    #    directly so notifications remains the sole writer - Amendment E) ------

    async def prune_read_before(self, cutoff_iso: str) -> int:
        """Retention: prune read/dismissed notifications older than ``cutoff_iso``."""
        return await self._repo.prune_read_before(cutoff_iso)


_service: NotificationService | None = None


def get_notification_service() -> NotificationService:
    """Process-wide NotificationService singleton."""
    global _service
    if _service is None:
        _service = NotificationService()
    return _service


def reset_notification_service() -> None:
    """Test hook: drop the singleton so it rebinds to an isolated DB."""
    global _service
    _service = None
