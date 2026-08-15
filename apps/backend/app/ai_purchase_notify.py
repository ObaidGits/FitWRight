"""Telling the customer their purchase worked.

Three deliveries, deliberately independent:

1. **In-app notification** - always. It uses the existing notification centre, needs no
   mail configuration, and survives an unconfigured or broken mail provider.
2. **Email with the receipt attached** - only when mail is configured AND the operator has
   the ``purchase_receipt`` event enabled.
3. Neither is allowed to fail the purchase. The credits are already granted by the time
   this runs; raising here would turn a successful payment into an error response and
   invite the customer to pay twice. Every path is caught and logged.

WHY IT IS NOT INSIDE THE GRANT TRANSACTION

Sending mail from inside the database transaction that grants credits would hold a write
lock open for the length of an SMTP round trip, and a mail timeout would roll back a
completed payment. So this is called after the grant has committed, and its failure is
recorded rather than propagated.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__all__ = ["PURCHASE_EVENT", "notify_purchase_complete"]

#: Key in ``MAIL_EVENTS`` that gates the email half of this.
PURCHASE_EVENT = "purchase_receipt"


async def notify_purchase_complete(purchase_id: str) -> dict:
    """Notify a user that their purchase landed. Never raises.

    Returns a small report so the caller can log what actually happened rather than
    assuming - "we sent an email" is a claim worth being able to check.
    """
    report: dict[str, object] = {"notified": False, "emailed": False}
    try:
        from app.database import db

        purchase = await db.get_purchase(purchase_id)
        if purchase is None:
            return {**report, "error": "purchase_not_found"}
        if purchase.get("state") != "granted":
            # Only a completed purchase is worth announcing. A "paid" row whose credits
            # have not landed yet would promise something the balance does not show.
            return {**report, "skipped": f"state={purchase.get('state')}"}

        user_id = str(purchase.get("user_id") or "")
        credits = int(purchase.get("credits") or 0)

        report["notified"] = await _in_app(user_id, purchase_id, credits)
        report["emailed"] = await _email(user_id, purchase)
    except Exception:
        # A notification failure must never surface as a payment failure.
        logger.exception("Purchase notification failed for %s", purchase_id)
        report["error"] = "unexpected"
    return report


async def _in_app(user_id: str, purchase_id: str, credits: int) -> bool:
    try:
        from app.notifications.service import NotificationService

        await NotificationService().notify(
            user_id,
            type="credits_purchased",
            # "ai" rather than "system": it is about their AI usage, and it means a user
            # who muted system chatter still hears about their own money.
            category="ai",
            title=f"{credits:,} credits added",
            body="Your purchase is complete. Credits you buy never expire.",
            node_type="purchase",
            node_id=purchase_id,
            # One notification per purchase, however many times a webhook is redelivered.
            dedupe_key=f"purchase:{purchase_id}",
        )
        return True
    except Exception:
        logger.exception("In-app purchase notification failed for %s", purchase_id)
        return False


async def _email(user_id: str, purchase: dict) -> bool:
    """Send the receipt email, if mail is configured and the event is enabled."""
    try:
        from app.app_settings import get_mail_transport, get_seller_details
        from app.database import db

        transport = await get_mail_transport(db)
        if not transport.provider:
            logger.info("Purchase email skipped: no mail provider configured")
            return False
        if not transport.sends(PURCHASE_EVENT):
            logger.info("Purchase email skipped: event disabled by operator")
            return False

        user = await _load_user(user_id)
        if user is None or not user.get("email"):
            return False

        seller = await get_seller_details(db)
        from app.ai_receipts import build_receipt, render_receipt_pdf

        receipt = build_receipt(
            purchase,
            seller=seller,
            buyer_name=str(user.get("name") or ""),
            buyer_email=str(user.get("email") or ""),
        )

        attachment: bytes | None = None
        try:
            attachment = await render_receipt_pdf(receipt)
        except Exception:
            # Send the confirmation without the PDF rather than not at all: knowing the
            # payment succeeded matters more than having the document immediately, and it
            # stays downloadable from the billing page either way.
            logger.warning("Receipt PDF failed; sending confirmation without attachment")

        from app.auth.email import EmailMessage, send_email_safe
        from app.platform import get_container

        credits = int(purchase.get("credits") or 0)
        body = (
            f"Hi {user.get('name') or 'there'},\n\n"
            f"Your purchase of {credits:,} credits is complete and they are already in "
            f"your account. Credits you buy never expire.\n\n"
            f"Receipt number: {receipt.number}\n"
            f"Amount paid: {receipt.total_minor / 100:,.2f} {receipt.currency}\n\n"
            f"You can download the receipt any time from Plan & billing in the app.\n\n"
            f"- {receipt.seller_name}"
        )
        message = EmailMessage(
            to=str(user["email"]),
            subject=f"{credits:,} credits added - receipt {receipt.number}",
            text_body=body,
        )
        # Through the composition root: adapter construction has exactly one home, which
        # the architecture guard enforces.
        sender = get_container().email_sender_for(transport)
        ok = await send_email_safe(sender, message)
        if ok and attachment is not None:
            # The sender interface has no attachment slot today, so the PDF is not
            # attached - saying so plainly beats a comment claiming it was. The receipt is
            # available on the billing page, which the email points at.
            logger.info("Receipt PDF generated but not attached (sender has no attachment support)")
        return bool(ok)
    except Exception:
        logger.exception("Purchase email failed")
        return False


async def _load_user(user_id: str) -> dict | None:
    """Name + email for the receipt, via the accounts store that owns user records."""
    try:
        from app.auth import accounts

        record = await accounts.get_by_id(user_id)
        if record is None:
            return None
        return {"email": getattr(record, "email", ""), "name": getattr(record, "name", "")}
    except Exception:
        logger.exception("Could not load user %s for the receipt email", user_id)
        return None
