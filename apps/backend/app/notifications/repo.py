"""Centralized, user-scoped notification data access (design §B).

The single place notification / preference / unread-counter rows are queried or
mutated. Allow-listed in the scoping guard (same trust model as
``app/admin/repo.py``); every method is explicitly scoped by ``user_id`` so no
notification can cross users. The denormalized ``user_unread_counts`` row is
maintained here (incremented on create, decremented on read/dismiss, clamped at
zero) so the unread badge is O(1), never a COUNT scan (R4.2).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import Notification, NotificationPref, UserUnreadCount

logger = logging.getLogger(__name__)

__all__ = ["NotificationRepo", "get_notification_repo"]

# Built-in per-category delivery defaults (applied when no pref row exists), so a
# new category needs no backfill. security is email-on by default (important).
_DEFAULT_EMAIL = {"security": True}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationRepo:
    """User-scoped repository for notifications, prefs, and the unread counter."""

    def _sf(self):
        from app import database

        return database.db.session_factory

    @staticmethod
    def _to_dict(row: Notification) -> dict[str, Any]:
        return {
            "id": row.id,
            "type": row.type,
            "category": row.category,
            "priority": row.priority,
            "title": row.title,
            "body": row.body,
            "node_type": row.node_type,
            "node_id": row.node_id,
            "group_key": row.group_key,
            "read": row.read,
            "dismissed": row.dismissed,
            "created_at": row.created_at,
        }

    # -- create + unread counter -------------------------------------------

    async def create(
        self,
        user_id: str,
        *,
        type: str,
        category: str,
        priority: str,
        title: str,
        body: str | None,
        node_type: str | None,
        node_id: str | None,
        group_key: str | None,
        dedupe_key: str | None,
        surfaced: bool = True,
    ) -> dict[str, Any] | None:
        """Insert a notification + bump the unread counter, idempotent by dedupe_key.

        Returns the created row, or ``None`` when a row with the same
        ``(user_id, dedupe_key)`` already exists (duplicate delivery suppressed).
        ``surfaced=False`` (the user turned in-app off for this category but kept
        email on) persists the row for the email worker but marks it dismissed so
        it never shows in the center and never bumps the unread badge.
        """
        async with self._sf()() as session:
            row = Notification(
                user_id=user_id,
                type=type,
                category=category,
                priority=priority,
                title=title,
                body=body,
                node_type=node_type,
                node_id=node_id,
                group_key=group_key,
                dedupe_key=dedupe_key,
                dismissed=not surfaced,
                created_at=_now(),
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                # dedupe_key collision -> idempotent no-op (duplicate delivery
                # *prevented* - the exactly-once signal, counterpart of
                # double_fire=0).
                from app.productivity.metrics import get_productivity_metrics

                get_productivity_metrics().notification_deduped()
                return None
            if surfaced:
                await self._bump_unread(session, user_id, +1)
            await session.commit()
            from app.productivity.metrics import get_productivity_metrics

            get_productivity_metrics().notification_created()
            return self._to_dict(row)

    async def _bump_unread(self, session, user_id: str, delta: int) -> None:
        """Adjust the denormalized counter within ``session`` (clamped at 0)."""
        counter = await session.get(UserUnreadCount, user_id)
        if counter is None:
            counter = UserUnreadCount(user_id=user_id, unread=max(0, delta), updated_at=_now())
            session.add(counter)
            return
        counter.unread = max(0, (counter.unread or 0) + delta)
        counter.updated_at = _now()

    # -- reads --------------------------------------------------------------

    async def list(
        self,
        user_id: str,
        *,
        limit: int,
        cursor: str | None = None,
        unread_only: bool = False,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Keyset page (newest first) of non-dismissed notifications."""
        stmt = select(Notification).where(
            Notification.user_id == user_id, Notification.dismissed.is_(False)
        )
        if unread_only:
            stmt = stmt.where(Notification.read.is_(False))
        if category:
            stmt = stmt.where(Notification.category == category)
        if cursor:
            created_at, _, cid = cursor.partition("|")
            stmt = stmt.where(
                (Notification.created_at < created_at)
                | ((Notification.created_at == created_at) & (Notification.id < cid))
            )
        stmt = stmt.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit)
        async with self._sf()() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(r) for r in rows]

    async def unread_count(self, user_id: str) -> int:
        async with self._sf()() as session:
            counter = await session.get(UserUnreadCount, user_id)
            return int(counter.unread) if counter else 0

    # -- mutations ----------------------------------------------------------

    async def mark_read(self, user_id: str, notif_id: str) -> bool:
        async with self._sf()() as session:
            row = await session.get(Notification, notif_id)
            if row is None or row.user_id != user_id:
                return False
            if not row.read:
                row.read = True
                if not row.dismissed:
                    await self._bump_unread(session, user_id, -1)
            await session.commit()
            return True

    async def mark_all_read(self, user_id: str) -> int:
        async with self._sf()() as session:
            result = await session.execute(
                update(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.read.is_(False),
                    Notification.dismissed.is_(False),
                )
                .values(read=True)
            )
            counter = await session.get(UserUnreadCount, user_id)
            if counter is not None:
                counter.unread = 0
                counter.updated_at = _now()
            await session.commit()
            return int(result.rowcount or 0)

    async def dismiss(self, user_id: str, notif_id: str) -> bool:
        async with self._sf()() as session:
            row = await session.get(Notification, notif_id)
            if row is None or row.user_id != user_id:
                return False
            if not row.dismissed:
                row.dismissed = True
                if not row.read:
                    await self._bump_unread(session, user_id, -1)
            await session.commit()
            return True

    async def dismiss_group(self, user_id: str, group_key: str) -> int:
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Notification).where(
                        Notification.user_id == user_id,
                        Notification.group_key == group_key,
                        Notification.dismissed.is_(False),
                    )
                )
            ).scalars().all()
            unread_cleared = 0
            for row in rows:
                row.dismissed = True
                if not row.read:
                    unread_cleared += 1
            if unread_cleared:
                await self._bump_unread(session, user_id, -unread_cleared)
            await session.commit()
            return len(rows)

    # -- preferences --------------------------------------------------------

    async def get_prefs(self, user_id: str) -> dict[str, Any]:
        """Return ``{category: {in_app, email}}`` (defaults applied) + digest."""
        from app.notifications.service import CATEGORIES

        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(NotificationPref).where(NotificationPref.user_id == user_id)
                )
            ).scalars().all()
            stored = {r.category: {"in_app": r.in_app, "email": r.email} for r in rows}
            counter = await session.get(UserUnreadCount, user_id)
            digest = counter.digest if counter else "off"
        prefs = {
            cat: stored.get(cat, {"in_app": True, "email": _DEFAULT_EMAIL.get(cat, False)})
            for cat in CATEGORIES
        }
        return {"categories": prefs, "digest": digest}

    async def resolve_delivery(self, user_id: str, category: str) -> dict[str, bool]:
        """Resolve effective (in_app, email) for a category (defaults applied)."""
        async with self._sf()() as session:
            row = await session.get(NotificationPref, (user_id, category))
            if row is not None:
                return {"in_app": row.in_app, "email": row.email}
        return {"in_app": True, "email": _DEFAULT_EMAIL.get(category, False)}

    async def set_pref(self, user_id: str, category: str, *, in_app: bool, email: bool) -> None:
        async with self._sf()() as session:
            row = await session.get(NotificationPref, (user_id, category))
            if row is None:
                session.add(
                    NotificationPref(
                        user_id=user_id, category=category, in_app=in_app, email=email,
                        updated_at=_now(),
                    )
                )
            else:
                row.in_app = in_app
                row.email = email
                row.updated_at = _now()
            await session.commit()

    async def set_digest(self, user_id: str, digest: str) -> None:
        async with self._sf()() as session:
            counter = await session.get(UserUnreadCount, user_id)
            if counter is None:
                session.add(UserUnreadCount(user_id=user_id, unread=0, digest=digest, updated_at=_now()))
            else:
                counter.digest = digest
                counter.updated_at = _now()
            await session.commit()

    # -- retention + reconcile ---------------------------------------------

    async def prune_read_before(self, cutoff_iso: str) -> int:
        """Delete read *or* dismissed notifications older than ``cutoff_iso`` (R17.4)."""
        async with self._sf()() as session:
            result = await session.execute(
                delete(Notification).where(
                    Notification.created_at < cutoff_iso,
                    (Notification.read.is_(True)) | (Notification.dismissed.is_(True)),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def reconcile_unread(self, user_id: str) -> int:
        """Recompute the counter from the table (drift recovery). Returns the value."""
        async with self._sf()() as session:
            value = int(
                (
                    await session.execute(
                        select(func.count()).select_from(Notification).where(
                            Notification.user_id == user_id,
                            Notification.read.is_(False),
                            Notification.dismissed.is_(False),
                        )
                    )
                ).scalar() or 0
            )
            counter = await session.get(UserUnreadCount, user_id)
            if counter is None:
                session.add(UserUnreadCount(user_id=user_id, unread=value, updated_at=_now()))
            else:
                counter.unread = value
                counter.updated_at = _now()
            await session.commit()
            return value

    async def mark_emailed(self, notif_id: str) -> None:
        """Stamp ``emailed_at`` so the email scan never re-picks the row."""
        async with self._sf()() as session:
            await session.execute(
                update(Notification).where(Notification.id == notif_id).values(emailed_at=_now())
            )
            await session.commit()

    async def mark_emailed_bulk(self, notif_ids: list[str]) -> None:
        if not notif_ids:
            return
        async with self._sf()() as session:
            await session.execute(
                update(Notification).where(Notification.id.in_(notif_ids)).values(emailed_at=_now())
            )
            await session.commit()

    async def digest_batches(self, *, limit_users: int = 500) -> dict[str, list[dict[str, Any]]]:
        """Group un-emailed low/normal notifications by user for digest sending.

        Only users whose ``digest`` is not ``off`` are included; email-pref
        filtering happens in the service (which knows per-category prefs).
        """
        async with self._sf()() as session:
            digest_users = set(
                (
                    await session.execute(
                        select(UserUnreadCount.user_id).where(UserUnreadCount.digest != "off")
                    )
                ).scalars().all()
            )
            if not digest_users:
                return {}
            rows = (
                await session.execute(
                    select(Notification)
                    .where(
                        Notification.emailed_at.is_(None),
                        Notification.user_id.in_(digest_users),
                        Notification.priority.in_(("low", "normal")),
                    )
                    .order_by(Notification.user_id, Notification.created_at)
                )
            ).scalars().all()
        batches: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            batches.setdefault(r.user_id, []).append({**self._to_dict(r), "user_id": r.user_id})
        return dict(list(batches.items())[:limit_users])

    async def emails_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        """Notifications that should be emailed but haven't been yet (digest=off path)."""
        async with self._sf()() as session:
            rows = (
                await session.execute(
                    select(Notification)
                    .where(Notification.emailed_at.is_(None))
                    .order_by(Notification.created_at)
                    .limit(limit)
                )
            ).scalars().all()
            return [{**self._to_dict(r), "user_id": r.user_id} for r in rows]


_repo: NotificationRepo | None = None


def get_notification_repo() -> NotificationRepo:
    """Process-wide notification repository singleton."""
    global _repo
    if _repo is None:
        _repo = NotificationRepo()
    return _repo
