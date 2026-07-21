"""User lifecycle: enable/disable, role change, soft-delete, restore (Tasks 4-6).

All mutations funnel through :class:`LifecycleService` so authorization,
idempotency (no-op -> ``changed:false``), the **atomic active-admin guard**,
session revocation (+ P1 cache eviction), and audit are applied uniformly and in
one place. The service never touches owned tables - it operates on the
(non-owned) ``users`` + ``sessions`` rows - so it stays outside the scoping guard.

The active-admin invariant (Property 3, R6.3/7.2/8.1/10.2) is enforced with a
**single conditional UPDATE**::

    UPDATE users SET ... WHERE id = :target
      AND (SELECT count(*) FROM users
             WHERE role='admin' AND status='active'
               AND deleted_at IS NULL AND id <> :target) >= 1

which is atomic on both SQLite and Postgres. Two concurrent demotions/disables/
deletes of the last two admins therefore serialize: the statement that runs
second sees zero *other* active admins and affects **0 rows** -> the service
raises ``last_active_admin`` (409). There is no check-then-act window.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.admin.repo import AdminUserRowData, build_user_row_data
from app.auth.audit import AuditEvent
from app.auth.owner import normalize_email
from app.models import User

logger = logging.getLogger(__name__)

__all__ = [
    "LifecycleService",
    "LifecycleError",
    "UserNotFoundError",
    "LastActiveAdminError",
    "SelfActionError",
    "InvalidValueError",
    "ConfirmMismatchError",
    "DestructiveDisabledError",
    "LifecycleOutcome",
    "get_lifecycle_service",
    "reset_lifecycle_service",
]

_VALID_STATUS = frozenset({"active", "disabled"})
_VALID_ROLE = frozenset({"user", "admin"})


class LifecycleError(Exception):
    """Base class for lifecycle errors (each maps to an ApiError code)."""


class UserNotFoundError(LifecycleError):
    """Target user id is unknown or purged (-> 404)."""


class LastActiveAdminError(LifecycleError):
    """The action would leave zero active admins (-> 409 last_active_admin)."""


class SelfActionError(LifecycleError):
    """The actor targeted themselves for a forbidden self-action (-> 409 self_action)."""


class InvalidValueError(LifecycleError):
    """An unknown status/role value (-> 400 invalid_value)."""


class ConfirmMismatchError(LifecycleError):
    """Typed delete confirmation did not match the target email (-> 400)."""


class DestructiveDisabledError(LifecycleError):
    """Destructive actions are disabled by the kill-switch (-> 403)."""


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """Result of a lifecycle mutation: whether state changed + the fresh row."""

    changed: bool
    row: AdminUserRowData | None
    event: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LifecycleService:
    """Enable/disable/role/delete/restore with the atomic active-admin guard."""

    def __init__(self, session_factory: async_sessionmaker, *, session_service=None, audit_service=None) -> None:
        self._session_factory = session_factory
        self._session_service = session_service
        self._audit_service = audit_service

    # -- collaborators (lazily resolved so tests can inject) ----------------

    def _sessions(self):
        if self._session_service is not None:
            return self._session_service
        from app.auth.sessions import get_session_service

        return get_session_service()

    def _audit(self):
        if self._audit_service is not None:
            return self._audit_service
        from app.auth.audit import get_audit_service

        return get_audit_service()

    # -- atomic guard --------------------------------------------------------

    @staticmethod
    def _other_active_admin_guard(target_id: str):
        """Scalar subquery: count of active admins **other than** ``target_id``."""
        return (
            select(func.count())
            .select_from(User)
            .where(
                User.role == "admin",
                User.status == "active",
                User.deleted_at.is_(None),
                User.id != target_id,
            )
            .scalar_subquery()
        )

    async def _apply(self, target_id: str, values: dict, *, needs_guard: bool) -> int:
        """Apply ``values`` to the target, guarded when it removes an active admin.

        Returns affected rowcount (0 => guard failed for a guarded update).
        """
        values = {**values, "updated_at": _now_iso()}
        async with self._session_factory() as session:
            stmt = update(User).where(User.id == target_id)
            if needs_guard:
                stmt = stmt.where(self._other_active_admin_guard(target_id) >= 1)
            stmt = stmt.values(**values)
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or 0)

    async def _load(self, target_id: str) -> User | None:
        async with self._session_factory() as session:
            return await session.get(User, target_id)

    async def _load_row(self, target_id: str) -> AdminUserRowData | None:
        row = await self._load(target_id)
        return build_user_row_data(row) if row is not None else None

    @staticmethod
    def _is_active_admin(row: User) -> bool:
        return row.role == "admin" and row.status == "active" and row.deleted_at is None

    # -- status (enable / disable) ------------------------------------------

    async def set_status(
        self,
        *,
        actor_id: str | None,
        target_id: str,
        new_status: str,
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> LifecycleOutcome:
        """Enable or disable a user (idempotent; atomic guard on disable)."""
        if new_status not in _VALID_STATUS:
            raise InvalidValueError(new_status)
        row = await self._load(target_id)
        if row is None or row.deleted_at is not None:
            raise UserNotFoundError(target_id)

        if row.status == new_status:
            return LifecycleOutcome(changed=False, row=build_user_row_data(row))

        needs_guard = new_status == "disabled" and self._is_active_admin(row)
        affected = await self._apply(target_id, {"status": new_status}, needs_guard=needs_guard)
        if needs_guard and affected == 0:
            raise LastActiveAdminError(target_id)

        if new_status == "disabled":
            # Revoke every session + evict caches so the disabled user is out
            # within one request cycle (R6.1).
            await self._sessions().revoke_all_for_user(target_id)

        event = AuditEvent.ADMIN_USER_DISABLED if new_status == "disabled" else AuditEvent.ADMIN_USER_ENABLED
        await self._audit().record(
            event,
            actor_user_id=actor_id,
            target_user_id=target_id,
            request_id=request_id,
            ip_hash=ip_hash,
            meta={"from": row.status, "to": new_status},
        )
        return LifecycleOutcome(changed=True, row=await self._load_row(target_id), event=event)

    # -- role ----------------------------------------------------------------

    async def set_role(
        self,
        *,
        actor_id: str | None,
        target_id: str,
        new_role: str,
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> LifecycleOutcome:
        """Change a user's role (idempotent; atomic guard on demotion)."""
        if new_role not in _VALID_ROLE:
            raise InvalidValueError(new_role)
        # A user may never change their own role via any endpoint (R7.3).
        if actor_id is not None and actor_id == target_id:
            raise SelfActionError(target_id)
        row = await self._load(target_id)
        if row is None or row.deleted_at is not None:
            raise UserNotFoundError(target_id)

        if row.role == new_role:
            return LifecycleOutcome(changed=False, row=build_user_row_data(row))

        # Demotion of an active admin removes an admin -> guarded.
        needs_guard = new_role != "admin" and self._is_active_admin(row)
        affected = await self._apply(target_id, {"role": new_role}, needs_guard=needs_guard)
        if needs_guard and affected == 0:
            raise LastActiveAdminError(target_id)

        # Revoke sessions to force fresh authz on the next request (R7.1).
        await self._sessions().revoke_all_for_user(target_id)
        await self._audit().record(
            AuditEvent.ROLE_CHANGED,
            actor_user_id=actor_id,
            target_user_id=target_id,
            request_id=request_id,
            ip_hash=ip_hash,
            meta={"from": row.role, "to": new_role},
        )
        return LifecycleOutcome(changed=True, row=await self._load_row(target_id), event=AuditEvent.ROLE_CHANGED)

    # -- combined role + status (atomic PATCH) ------------------------------

    async def set_role_and_status(
        self,
        *,
        actor_id: str | None,
        target_id: str,
        new_role: str,
        new_status: str,
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> LifecycleOutcome:
        """Apply role **and** status in one atomic transaction (M2 fix).

        A combined ``PATCH`` must never partially apply (e.g. role committed +
        sessions revoked, then status fails). Both fields are validated up front,
        applied in a **single guarded UPDATE**, and only then are sessions revoked
        + distinct audit events emitted - so either both changes land or neither
        does (the guard is evaluated against the *combined* final state).
        """
        if new_role not in _VALID_ROLE:
            raise InvalidValueError(new_role)
        if new_status not in _VALID_STATUS:
            raise InvalidValueError(new_status)
        row = await self._load(target_id)
        if row is None or row.deleted_at is not None:
            raise UserNotFoundError(target_id)

        role_changed = row.role != new_role
        status_changed = row.status != new_status
        # Self role-change is forbidden (R7.3); a self status change is allowed.
        if actor_id is not None and actor_id == target_id and role_changed:
            raise SelfActionError(target_id)

        if not role_changed and not status_changed:
            return LifecycleOutcome(changed=False, row=build_user_row_data(row))

        # Guard when the combined final state stops being an active admin.
        final_is_active_admin = (
            new_role == "admin" and new_status == "active" and row.deleted_at is None
        )
        needs_guard = self._is_active_admin(row) and not final_is_active_admin

        values: dict = {}
        if role_changed:
            values["role"] = new_role
        if status_changed:
            values["status"] = new_status
        affected = await self._apply(target_id, values, needs_guard=needs_guard)
        if needs_guard and affected == 0:
            raise LastActiveAdminError(target_id)

        # Revoke sessions if role changed (fresh authz) or the user was disabled.
        if role_changed or new_status == "disabled":
            await self._sessions().revoke_all_for_user(target_id)

        if role_changed:
            await self._audit().record(
                AuditEvent.ROLE_CHANGED,
                actor_user_id=actor_id,
                target_user_id=target_id,
                request_id=request_id,
                ip_hash=ip_hash,
                meta={"from": row.role, "to": new_role},
            )
        if status_changed:
            event = (
                AuditEvent.ADMIN_USER_DISABLED
                if new_status == "disabled"
                else AuditEvent.ADMIN_USER_ENABLED
            )
            await self._audit().record(
                event,
                actor_user_id=actor_id,
                target_user_id=target_id,
                request_id=request_id,
                ip_hash=ip_hash,
                meta={"from": row.status, "to": new_status},
            )
        return LifecycleOutcome(changed=True, row=await self._load_row(target_id))

    # -- soft-delete / restore ----------------------------------------------

    async def soft_delete(
        self,
        *,
        actor_id: str | None,
        target_id: str,
        email_confirm: str,
        destructive_enabled: bool,
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> LifecycleOutcome:
        """Soft-delete a user (typed email confirm + atomic guard, R8.1/14.1)."""
        if not destructive_enabled:
            raise DestructiveDisabledError()
        if actor_id is not None and actor_id == target_id:
            raise SelfActionError(target_id)
        row = await self._load(target_id)
        if row is None:
            raise UserNotFoundError(target_id)

        # Already soft-deleted => idempotent no-op (re-delete is a 200 no-op, R10.1).
        if row.deleted_at is not None:
            return LifecycleOutcome(changed=False, row=build_user_row_data(row))

        # Typed confirmation must match the target's (normalized) email (R8.1).
        if normalize_email(email_confirm) != normalize_email(row.email):
            raise ConfirmMismatchError(target_id)

        needs_guard = self._is_active_admin(row)
        affected = await self._apply(
            target_id,
            {"deleted_at": _now_iso(), "status": "disabled"},
            needs_guard=needs_guard,
        )
        if needs_guard and affected == 0:
            raise LastActiveAdminError(target_id)

        await self._sessions().revoke_all_for_user(target_id)
        # R8.4 erasure: the audit row is retained through purge, so it MUST NOT
        # carry PII (email/name). The target is identified by ``target_user_id``
        # (an opaque id) only; the confirmation happened at the API boundary.
        await self._audit().record(
            AuditEvent.ADMIN_USER_SOFT_DELETED,
            actor_user_id=actor_id,
            target_user_id=target_id,
            request_id=request_id,
            ip_hash=ip_hash,
        )
        return LifecycleOutcome(
            changed=True, row=await self._load_row(target_id), event=AuditEvent.ADMIN_USER_SOFT_DELETED
        )

    async def restore(
        self,
        *,
        actor_id: str | None,
        target_id: str,
        destructive_enabled: bool,
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> LifecycleOutcome:
        """Restore a soft-deleted user within the grace period (R8.2).

        Clears ``deleted_at``; status stays ``disabled`` until an admin explicitly
        enables the account (so a restore never silently re-grants access).
        """
        if not destructive_enabled:
            raise DestructiveDisabledError()
        row = await self._load(target_id)
        if row is None:
            raise UserNotFoundError(target_id)
        if row.deleted_at is None:
            # Not deleted => nothing to restore (idempotent no-op).
            return LifecycleOutcome(changed=False, row=build_user_row_data(row))

        await self._apply(target_id, {"deleted_at": None}, needs_guard=False)
        await self._audit().record(
            AuditEvent.ADMIN_USER_RESTORED,
            actor_user_id=actor_id,
            target_user_id=target_id,
            request_id=request_id,
            ip_hash=ip_hash,
        )
        return LifecycleOutcome(
            changed=True, row=await self._load_row(target_id), event=AuditEvent.ADMIN_USER_RESTORED
        )

    # -- bulk disable --------------------------------------------------------

    async def bulk_disable(
        self,
        *,
        actor_id: str | None,
        target_ids: list[str],
        request_id: str | None = None,
        ip_hash: str | None = None,
    ) -> list[dict]:
        """Disable a bounded batch, applying the atomic invariant per target (R6.4).

        Each target is disabled through :meth:`set_status`, so the active-admin
        guard, idempotency, session revocation, and per-target audit all apply.
        Returns a per-target result list (``{id, result}``) - never aborts the
        whole batch on a single failure.
        """
        results: list[dict] = []
        # De-dupe while preserving order so a repeated id is processed once.
        seen: set[str] = set()
        for target_id in target_ids:
            if target_id in seen:
                continue
            seen.add(target_id)
            if actor_id is not None and target_id == actor_id:
                results.append({"id": target_id, "result": "self_action"})
                continue
            try:
                outcome = await self.set_status(
                    actor_id=actor_id,
                    target_id=target_id,
                    new_status="disabled",
                    request_id=request_id,
                    ip_hash=ip_hash,
                )
                results.append(
                    {"id": target_id, "result": "disabled" if outcome.changed else "no_op"}
                )
            except LastActiveAdminError:
                results.append({"id": target_id, "result": "last_active_admin"})
            except UserNotFoundError:
                results.append({"id": target_id, "result": "not_found"})
        return results


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_service: LifecycleService | None = None


def get_lifecycle_service() -> LifecycleService:
    """Return the process-wide :class:`LifecycleService` (bound to the app DB)."""
    global _service
    if _service is None:
        from app.database import db

        _service = LifecycleService(db.session_factory)
    return _service


def reset_lifecycle_service() -> None:
    """Drop the cached instance (test helper)."""
    global _service
    _service = None
