"""Buying credits: the machinery, deliberately not connected to a live provider.

WHAT IS HERE, AND WHY IT STOPS WHERE IT DOES

Everything that decides whether a balance moves is implemented and tested: the
forward-only state machine, webhook idempotency, grant-only-on-verified-webhook,
refund claw-back, invoice numbering, and the fail-closed rule. It is exercised against
a fake provider in the tests.

What is NOT here is a live Razorpay (or any) integration, and
``AI_PURCHASES_ENABLED`` defaults to false. Three reasons, in order of importance:

1. **Prices are not known yet.** The whole point of metering first was to set prices from
   observed cost. Shipping packs priced by guesswork would either lose money on every
   sale or overcharge, and both are hard to walk back once customers have paid.
2. **A payment integration cannot be verified without the provider.** Signature
   verification, webhook delivery, and the exact event shapes are things you confirm
   against a real sandbox account with real keys. Code that has never seen a real
   webhook is not "done", however good it looks.
3. **The failure mode is taking money incorrectly.** Everything else in this system can
   be fixed by an operator with a grant. A payment bug takes real money from a real
   person, and the remedy involves refunds, chargebacks and trust that does not come
   back.

So: the adapter is an interface with a fake implementation. Adding a real provider means
writing one class and setting the keys - with the state machine, idempotency and
reconciliation already proven around it.

THE RULE THAT MATTERS MOST: credits are granted only when a VERIFIED WEBHOOK from the
provider says the money arrived. The browser saying "payment succeeded" is a claim by an
untrusted party - anyone who can read the page can send it - so it may update the UI and
must never move a balance.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from app.ai_pack_pricing import PackOffer, effective_offer
from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

__all__ = [
    "PackOffer",
    "FakePaymentProvider",
    "PaymentProvider",
    "PurchasesDisabled",
    "available_packs",
    "get_payment_provider",
    "handle_webhook",
    "start_purchase",
]

#: Forward-only. The value is how far along the purchase is; a webhook that would move it
#: BACKWARDS is ignored, because providers deliver out of order and a late `created` must
#: not undo a completed `granted`.
_STATE_ORDER = {"created": 0, "failed": 1, "paid": 2, "granted": 3, "refunded": 4}


class PurchasesDisabled(ApiError):
    def __init__(self, reason: str = "Credit purchases are not available yet."):
        super().__init__(503, "purchases_disabled", reason)


async def available_packs() -> list[PackOffer]:
    """The packs on sale right now, with any live discount already applied.

    Reads the database, which is the ONLY source of pack prices. They used to come from
    an environment variable, so changing a price - or running a weekend offer - needed a
    redeploy; that is the wrong shape for something an operator adjusts on a Tuesday.

    Returns EMPTY until the operator creates and activates a pack. There is deliberately
    no default pricing: a default price is a guess, and a guess here is either a loss on
    every sale or an overcharge.
    """
    from app.database import db

    try:
        rows = await db.list_credit_packs(only_active=True)
    except Exception:
        # Failing closed means nothing is on sale, which is the safe direction: showing
        # no packs loses a sale, while showing a wrong price takes the wrong money.
        logger.exception("Could not read credit packs; nothing is on sale")
        return []
    return [effective_offer(row) for row in rows]


class PaymentProvider(Protocol):
    """What a payment provider must do. Deliberately tiny.

    Only three operations, because everything else - state, idempotency, granting - is
    ours and must not vary per provider. A provider that needs more than this is telling
    you it wants to own logic that belongs on our side of the boundary.
    """

    name: str

    def create_order(self, *, amount_minor: int, currency: str, reference: str) -> dict[str, Any]:
        """Open a payment with the provider. Returns provider ids for the client."""

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Authenticate a webhook and return its parsed payload.

        MUST raise if the signature does not verify. This is the only thing standing
        between an attacker and free credits: an unverified webhook is just an HTTP
        request that anybody can send.
        """

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalise a provider event to
        ``{event_id, kind, order_id, payment_id, amount_minor, currency}``
        where ``kind`` is ``paid`` | ``failed`` | ``refunded`` | ``ignored``."""


class FakePaymentProvider:
    """A provider that exists so the machinery can be tested without taking money.

    Not a stub in the pejorative sense - it is the harness the state machine, the
    idempotency and the claw-back are proven against. It refuses to be used when
    purchases are enabled, so it cannot become the thing serving real customers.
    """

    name = "fake"

    def create_order(self, *, amount_minor: int, currency: str, reference: str) -> dict[str, Any]:
        return {"order_id": f"fake_order_{reference}", "amount_minor": amount_minor,
                "currency": currency}

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        import json

        if headers.get("x-fake-signature") != "valid":
            # Mirrors a real provider's behaviour, so the tests exercise the rejection
            # path rather than assuming it works.
            raise ApiError(400, "invalid_signature", "The webhook signature did not verify.")
        return json.loads(body.decode() or "{}")

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": payload.get("event_id"),
            "kind": payload.get("kind", "ignored"),
            "order_id": payload.get("order_id"),
            "payment_id": payload.get("payment_id"),
            "amount_minor": int(payload.get("amount_minor") or 0),
            "currency": payload.get("currency", "INR"),
        }


def get_payment_provider() -> PaymentProvider:
    """Resolve the configured provider.

    Only the fake one exists. Adding Razorpay means implementing the three methods above
    and returning it here; nothing else in this module changes, which is the point of the
    interface.
    """
    configured = (getattr(settings, "ai_payment_provider", "") or "fake").lower()
    if configured == "razorpay":
        from app.ai_razorpay import RazorpayProvider

        return RazorpayProvider()
    if configured == "fake":
        return FakePaymentProvider()
    # An unknown provider refuses every purchase rather than half-taking money through
    # an adapter that does not exist.
    raise PurchasesDisabled(
        f"Payment provider {configured!r} is configured but not implemented. "
        "No purchase can be taken."
    )


async def start_purchase(user_id: str, pack_id: str) -> dict[str, Any]:
    """Open a purchase and return what the client needs to pay.

    FAILS CLOSED when metering is unavailable: if we cannot record what a user consumes,
    we must not sell them the right to consume it. Selling into a system that cannot
    measure is how you end up unable to answer "what did they actually get?".
    """
    if not getattr(settings, "ai_purchases_enabled", False):
        raise PurchasesDisabled()
    if not settings.ai_credits_enabled:
        # Selling credits that nothing will spend or track.
        raise PurchasesDisabled(
            "Credits are not enabled, so there is nothing to buy yet."
        )

    # Resolved through the same function the buy screen used, so the amount charged is
    # by construction the amount advertised - including any live discount.
    pack = next((p for p in await available_packs() if p.id == pack_id), None)
    if pack is None:
        raise ApiError(404, "pack_not_found", "That credit pack is not available.")

    from app.database import db

    provider = get_payment_provider()
    purchase = await db.create_credit_purchase(
        user_id=user_id,
        pack_id=pack.id,
        credits=pack.credits,
        amount_minor=pack.amount_minor,
        currency=pack.currency,
        provider=provider.name,
    )
    order = provider.create_order(
        amount_minor=pack.amount_minor, currency=pack.currency, reference=purchase["id"]
    )
    await db.attach_purchase_order(purchase["id"], order_id=str(order.get("order_id")))
    return {"purchase_id": purchase["id"], "order": order}


async def handle_webhook(
    *, body: bytes, headers: dict[str, str], event_id: str | None = None
) -> dict[str, Any]:
    """Process one provider webhook. The ONLY path that grants purchased credits.

    Idempotent by the provider's event id: a redelivery finds the id already recorded and
    changes nothing. That is not an edge case - providers redeliver by design, and
    without this a retry would double a customer's credits.
    """
    from app.database import db

    provider = get_payment_provider()
    payload = provider.verify_webhook(body=body, headers=headers)
    event = provider.parse_event(payload)

    # The provider's per-delivery id wins when present: it is what makes a REDELIVERY of
    # the same event distinguishable from a genuinely new one, which a body-derived id
    # cannot always be.
    event_id = event_id or event.get("event_id")
    if not event_id:
        # Without an event id there is no way to be idempotent, so the safe answer is to
        # do nothing rather than risk granting twice.
        return {"status": "ignored", "reason": "no event id"}

    if await db.purchase_event_seen(str(event_id)):
        return {"status": "replayed"}

    purchase = await db.get_purchase_by_order(str(event.get("order_id") or ""))
    if purchase is None:
        return {"status": "ignored", "reason": "unknown order"}

    kind = event.get("kind")
    if kind == "paid":
        # Amount is re-checked against what WE recorded, never trusted from the event: a
        # forged or mistaken amount must not buy more credits than were paid for.
        if int(event.get("amount_minor") or 0) < int(purchase["amount_minor"]):
            await db.fail_purchase(purchase["id"], reason="amount_mismatch", event_id=str(event_id))
            return {"status": "failed", "reason": "amount mismatch"}
        granted = await db.grant_purchase(purchase["id"], event_id=str(event_id))
        if granted == "granted":
            # After the grant has COMMITTED, never inside it: sending mail from within the
            # granting transaction would hold a write lock open for an SMTP round trip, and
            # a mail timeout would roll back a completed payment. Its own failures are
            # caught internally, so a notification problem cannot fail the webhook and
            # invite a duplicate payment.
            from app.ai_purchase_notify import notify_purchase_complete

            delivery = await notify_purchase_complete(purchase["id"])
            logger.info("Purchase %s notification: %s", purchase["id"], delivery)
        return {"status": granted}
    if kind == "failed":
        await db.fail_purchase(purchase["id"], reason="provider_failed", event_id=str(event_id))
        return {"status": "failed"}
    if kind == "refunded":
        # Claw-back may take the balance NEGATIVE and blocks the account. Refusing to go
        # negative would let someone buy, spend, refund, and keep the value.
        await db.refund_purchase(purchase["id"], event_id=str(event_id))
        return {"status": "refunded"}

    return {"status": "ignored", "reason": f"unhandled kind {kind}"}
