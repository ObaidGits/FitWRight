"""Safe OAuth link/create decision (Property 5 / R4.4) — Task 7.2.

Once the callback has *fully verified* a provider id_token and confirmed
``email_verified``, this module decides which local user the sign-in resolves to,
under the anti-hijack rules of R4.4 / the design `§Security` "Account linking
hijack" row. The whole point is that a Google sign-in must never silently take
over — or duplicate — an existing password account.

Decision matrix (evaluated in order):

1. **Identity already linked** — an ``oauth_identities`` row exists for
   ``(provider, subject)`` → log that user in (``LOGIN_EXISTING``). This is a
   returning OAuth user; ``sub`` is the stable, provider-verified key.
2. **No identity, no account with that email** → create a new, already-verified
   user and link the identity (``CREATED``, R4.3).
3. **No identity, an account with that email exists** → link **only if** the
   provider email is verified AND (that account has *no password* OR its email
   is *already verified* OR *the request is already authenticated* and linking
   from Settings). Then attach the identity to that account (``LINKED``, R4.4).
4. **Otherwise** (a password account whose email is unverified, request not
   authenticated) → **refuse** (``LINK_REQUIRED``): no session, no new row —
   the user must log in first to link, so an unverified account cannot be
   hijacked and no duplicate is created (R4.4).

The provider email is always verified by the time linking runs (the callback
rejects ``email_verified == false`` first, R4.5), so in practice step 3's guard
reduces to *no-password OR verified OR authenticated*; the explicit
``provider_email_verified`` argument keeps the rule self-contained and total.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.accounts import AccountRecord, create_user, get_by_email, get_password_hash
from app.auth.owner import normalize_email
from app.models import OAuthIdentity, User

logger = logging.getLogger(__name__)

__all__ = ["LinkAction", "LinkResult", "link_or_create_user"]


class LinkAction(str, Enum):
    """What the linking decision did (drives auditing + metrics)."""

    LOGIN_EXISTING = "login_existing"
    CREATED = "created"
    LINKED = "linked"
    LINK_REQUIRED = "link_required"


@dataclass(frozen=True, slots=True)
class LinkResult:
    """Outcome of the link/create decision.

    ``user_id`` is set for every success (``LOGIN_EXISTING``/``CREATED``/
    ``LINKED``) and ``None`` for the refusal (``LINK_REQUIRED``), which the
    callback maps to ``oauth_failed`` (no session).
    """

    action: LinkAction
    user_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.user_id is not None


def _resolve_db(db):
    if db is not None:
        return db
    from app.database import db as default_db

    return default_db


async def _get_identity_user_id(db, provider: str, subject: str) -> str | None:
    async with db.session_factory() as session:
        return (
            await session.execute(
                select(OAuthIdentity.user_id).where(
                    OAuthIdentity.provider == provider,
                    OAuthIdentity.subject == subject,
                )
            )
        ).scalar_one_or_none()


async def _create_identity(
    db, *, provider: str, subject: str, user_id: str, email_at_link: str
) -> bool:
    """Insert an ``oauth_identities`` row. Returns ``False`` on a unique-race."""
    from datetime import datetime, timezone

    async with db.session_factory() as session:
        session.add(
            OAuthIdentity(
                provider=provider,
                subject=subject,
                user_id=user_id,
                email_at_link=email_at_link,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
    return True


async def link_or_create_user(
    *,
    provider: str,
    subject: str,
    email: str,
    provider_email_verified: bool,
    authenticated_user_id: str | None = None,
    name: str | None = None,
    db=None,
) -> LinkResult:
    """Resolve a verified provider identity to a local user (R4.4, Property 5)."""
    db = _resolve_db(db)
    normalized = normalize_email(email)

    # 1) Already-linked identity → returning OAuth user.
    linked_user_id = await _get_identity_user_id(db, provider, subject)
    if linked_user_id is not None:
        return LinkResult(LinkAction.LOGIN_EXISTING, user_id=linked_user_id)

    existing = await get_by_email(normalized, db=db)

    # 2) No account with this email → create a fresh, already-verified user.
    if existing is None:
        from datetime import datetime, timezone

        record: AccountRecord = await create_user(
            email=normalized,
            name=name or normalized.split("@", 1)[0],
            password_hash=None,  # OAuth-only until a password is set (R6.3)
            status="active",
            email_verified_at=datetime.now(timezone.utc).isoformat(),
            db=db,
        )
        created = await _create_identity(
            db, provider=provider, subject=subject, user_id=record.id, email_at_link=normalized
        )
        if not created:
            # Extremely rare: identity created concurrently. Fall back to it.
            linked = await _get_identity_user_id(db, provider, subject)
            return LinkResult(LinkAction.LOGIN_EXISTING, user_id=linked or record.id)
        return LinkResult(LinkAction.CREATED, user_id=record.id)

    # 3) An account with this email exists → link ONLY under the safe rules.
    has_password = (await get_password_hash(existing.id, db=db)) is not None
    is_authenticated_self = (
        authenticated_user_id is not None and authenticated_user_id == existing.id
    )
    may_link = provider_email_verified and (
        not has_password or existing.email_verified or is_authenticated_self
    )
    if not may_link:
        # Refuse: a password account with an unverified email, request not
        # authenticated → require login-first linking (never hijack, R4.4).
        return LinkResult(LinkAction.LINK_REQUIRED, user_id=None)

    linked = await _create_identity(
        db, provider=provider, subject=subject, user_id=existing.id, email_at_link=normalized
    )
    if not linked:
        # A concurrent request linked the same identity first — resolve to it.
        resolved = await _get_identity_user_id(db, provider, subject)
        return LinkResult(LinkAction.LINKED, user_id=resolved or existing.id)
    return LinkResult(LinkAction.LINKED, user_id=existing.id)
