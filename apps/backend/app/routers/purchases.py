"""Buying credits: start a payment, confirm it, and receive Razorpay's webhook.

THE RULE THIS FILE EXISTS TO ENFORCE: credits are granted only after a signature this
server computed itself has matched. Neither endpoint trusts an amount, a status, or a
"payment succeeded" flag sent by the browser - only a signature made with a secret the
browser does not have.

Two confirmation routes, converging on one idempotent grant:

* ``/confirm`` - the browser relays what the checkout modal returned. Fast, so the
  customer gets an immediate answer.
* ``/webhook`` - Razorpay's servers tell us directly. Authoritative, and it still arrives
  when the customer closes the tab, loses signal, or their phone dies mid-redirect.

Keeping only the first would lose a paying customer's credits every time a mobile network
drops at the wrong moment. Keeping only the second would leave the customer staring at a
spinner. Both grant through the same idempotent path keyed by the payment id, so whichever
lands first wins and the other changes nothing.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.principal import get_effective_user_id
from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/credits/purchase", tags=["credits"])


class StartIn(BaseModel):
    pack_id: str = Field(min_length=1, max_length=64)


class ConfirmIn(BaseModel):
    """Exactly what Razorpay's checkout handler hands back."""

    razorpay_order_id: str = Field(min_length=1, max_length=128)
    razorpay_payment_id: str = Field(min_length=1, max_length=128)
    razorpay_signature: str = Field(min_length=1, max_length=256)


@router.post("")
async def start(
    payload: StartIn, user_id: str = Depends(get_effective_user_id)
) -> dict:
    """Open a payment for one pack and return what the browser needs.

    Grants nothing. The order exists at the provider and a purchase row exists here in
    ``created`` state; no balance moves until a signature verifies.
    """
    from app.ai_purchases import start_purchase

    result = await start_purchase(user_id, payload.pack_id)
    order = result["order"]
    return {
        "purchase_id": result["purchase_id"],
        "order_id": order.get("order_id"),
        "amount_minor": order.get("amount_minor"),
        "currency": order.get("currency"),
        # Publishable key, fetched at runtime rather than baked into the frontend build.
        # This app ships as one Docker image, so a build-time key would have to be
        # rebuilt to rotate; and it means the browser is never handed anything secret.
        "key_id": order.get("key_id"),
    }


@router.post("/confirm")
async def confirm(
    payload: ConfirmIn, user_id: str = Depends(get_effective_user_id)
) -> dict:
    """Verify the checkout callback and grant if it is genuine.

    The signature is ``HMAC_SHA256(order_id|payment_id, KEY_SECRET)``, computed here. A
    browser cannot forge it without the secret, which is what makes this callback
    trustworthy - not the fact that the browser said the payment worked.

    A mismatch returns 400 and marks NOTHING as paid.
    """
    if (getattr(settings, "ai_payment_provider", "fake") or "fake").lower() != "razorpay":
        raise ApiError(400, "wrong_provider", "This confirmation route is for Razorpay.")

    from app.ai_razorpay import verify_checkout_signature
    from app.database import db

    if not verify_checkout_signature(
        order_id=payload.razorpay_order_id,
        payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    ):
        # Deliberately terse: a caller probing signatures learns nothing about why.
        logger.warning("A checkout confirmation failed signature verification")
        raise ApiError(
            400, "invalid_signature", "This payment could not be verified."
        )

    purchase = await db.get_purchase_by_order(payload.razorpay_order_id)
    if purchase is None:
        raise ApiError(404, "purchase_not_found", "That payment does not match an order.")
    if purchase["user_id"] != user_id:
        # A verified signature proves Razorpay signed this pair; it does not prove the
        # person relaying it is the buyer. Without this check, one user could confirm
        # another's order and the credits would land on the wrong account.
        logger.warning("A user tried to confirm a purchase belonging to someone else")
        raise ApiError(404, "purchase_not_found", "That payment does not match an order.")

    # Idempotent by the payment id, so a double-submit or a refresh cannot grant twice -
    # and it converges with the webhook path on the same row.
    status = await db.grant_purchase(
        purchase["id"], event_id=f"checkout:{payload.razorpay_payment_id}"
    )
    account = await db.get_or_create_credit_account(user_id)
    return {
        "status": status,
        "credits_added": purchase["credits"] if status == "granted" else 0,
        "available_credits": account["available_credits"],
    }


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request) -> dict:
    """Receive Razorpay's server-to-server notification.

    Unauthenticated by necessity - Razorpay cannot hold a session - so the SIGNATURE is
    the authentication. The raw body is verified byte for byte: re-serialising the parsed
    JSON changes whitespace and key order, and the HMAC would no longer match.

    Always returns 200 for events we have processed or deliberately ignored. A provider
    that receives an error retries, and retrying on an event we already handled adds load
    for no benefit; the signature check still rejects forgeries with a 400.
    """
    from app.ai_purchases import handle_webhook

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    # Razorpay's per-delivery id, used as the idempotency key so a redelivery of the SAME
    # event is a no-op even though a retry legitimately repeats the payload.
    event_id = headers.get("x-razorpay-event-id")

    try:
        result = await handle_webhook(body=body, headers=headers, event_id=event_id)
    except ApiError:
        # Signature failures and configuration problems propagate: a 4xx/5xx tells
        # Razorpay to retry (or tells us, loudly, that the secret is wrong).
        raise
    except Exception:
        logger.exception("A Razorpay webhook could not be processed")
        raise ApiError(500, "webhook_failed", "The webhook could not be processed.") from None

    return result
