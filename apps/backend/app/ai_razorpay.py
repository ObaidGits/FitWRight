"""Razorpay Standard Checkout, behind the existing ``PaymentProvider`` interface.

It implements the same three methods the fake provider does, so the state machine,
webhook idempotency, refund claw-back and reconciliation that were already proven around
that interface apply unchanged. Nothing about how a purchase is fulfilled is
Razorpay-specific.

TWO SIGNATURES, TWO ALGORITHMS, TWO SECRETS - and confusing them is the classic mistake:

* **Checkout callback** (what the browser hands back after the modal closes):
  ``HMAC_SHA256(order_id + "|" + payment_id, KEY_SECRET)``.
* **Webhook** (what Razorpay's servers POST to us):
  ``HMAC_SHA256(raw_request_body, WEBHOOK_SECRET)`` in the ``X-Razorpay-Signature``
  header. The webhook secret is a DIFFERENT value, set separately in the Razorpay
  dashboard - using the key secret here silently rejects every webhook.

The raw body matters: re-serialising the parsed JSON changes whitespace and key order and
the HMAC no longer matches, so the bytes are verified exactly as received.

WHY BOTH PATHS EXIST, AND WHY THAT IS SAFE

The callback is fast and gives the customer an immediate answer; the webhook is
authoritative and arrives even if the customer closes the tab, loses signal, or the
browser is killed mid-redirect. Relying on the callback alone loses a paid customer's
credits whenever their network drops at the wrong second - which is common on mobile.

Both routes converge on the SAME idempotent grant, keyed by the payment id. Whichever
arrives first grants; the other reports ``replayed`` and changes nothing. That is why
having two paths adds reliability rather than a double-credit bug.

WHY httpx AND stdlib hmac RATHER THAN THE OFFICIAL SDK

The ``razorpay`` Python SDK is synchronous (it wraps ``requests``). Calling it from this
async request path would block the event loop for the duration of a network round trip to
Razorpay, so every other in-flight request stalls behind it. Order creation is one HTTP
POST and signature verification is one HMAC from the standard library, so the SDK buys
nothing here and costs an event-loop stall plus two more pinned dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from app.config import settings
from app.errors import ApiError

logger = logging.getLogger(__name__)

__all__ = ["RazorpayProvider", "verify_checkout_signature"]

_ORDERS_URL = "https://api.razorpay.com/v1/orders"

#: Razorpay's own minimum. Below this the API rejects the order, so it is caught here
#: with a message that says what to do instead of surfacing a provider error.
MIN_AMOUNT_MINOR = 100


def _credentials() -> tuple[str, str]:
    key_id = (getattr(settings, "razorpay_key_id", "") or "").strip()
    key_secret = (getattr(settings, "razorpay_key_secret", "") or "").strip()
    if not key_id or not key_secret:
        raise ApiError(
            503,
            "payments_unconfigured",
            "Payments are not configured. Please try again later.",
        )
    return key_id, key_secret


def verify_checkout_signature(
    *, order_id: str, payment_id: str, signature: str
) -> bool:
    """Verify the signature the BROWSER hands back after the modal closes.

    ``HMAC_SHA256(order_id|payment_id, KEY_SECRET)``. Compared with
    ``hmac.compare_digest`` rather than ``==`` so the comparison time does not depend on
    how many leading characters matched - the standard defence against an attacker
    learning a signature one byte at a time.
    """
    if not (order_id and payment_id and signature):
        return False
    _key_id, key_secret = _credentials()
    expected = hmac.new(
        key_secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class RazorpayProvider:
    """Order creation and webhook verification for Razorpay."""

    name = "razorpay"

    def create_order(
        self, *, amount_minor: int, currency: str, reference: str
    ) -> dict[str, Any]:
        # The interface is sync, so the blocking client is used deliberately and only
        # here. httpx's sync client is used rather than requests because httpx is already
        # in the dependency tree.
        if int(amount_minor) < MIN_AMOUNT_MINOR:
            raise ApiError(
                400,
                "amount_too_small",
                f"The minimum payment is {MIN_AMOUNT_MINOR} paise.",
            )
        key_id, key_secret = _credentials()

        try:
            response = httpx.post(
                _ORDERS_URL,
                auth=(key_id, key_secret),
                json={
                    "amount": int(amount_minor),
                    "currency": currency.upper(),
                    # Our own purchase id, so a payment can always be traced back to the
                    # row that created it even if our response to the client was lost.
                    "receipt": reference,
                    "notes": {"purchase_id": reference},
                },
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            logger.warning("Razorpay order creation failed: %s", exc)
            raise ApiError(
                502,
                "payment_provider_unreachable",
                "We could not reach the payment provider. Please try again.",
            ) from exc

        if response.status_code in (401, 403):
            # Our problem, not the customer's - do not tell them to retry.
            logger.error("Razorpay rejected our credentials (%s)", response.status_code)
            raise ApiError(
                503,
                "payments_unconfigured",
                "Payments are temporarily unavailable. Please try again later.",
            )
        if response.status_code >= 400:
            logger.error(
                "Razorpay order creation returned %s: %s",
                response.status_code,
                response.text[:300],
            )
            raise ApiError(
                502,
                "payment_provider_error",
                "The payment provider could not start this payment. Please try again.",
            )

        body = response.json()
        return {
            "order_id": body.get("id"),
            "amount_minor": int(body.get("amount") or amount_minor),
            "currency": body.get("currency") or currency.upper(),
            # The publishable key the browser needs to open the modal. Safe to send: it
            # identifies the account and cannot authorise anything on its own.
            "key_id": key_id,
        }

    def verify_webhook(self, *, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Authenticate a webhook against the WEBHOOK secret and the RAW body."""
        secret = (getattr(settings, "razorpay_webhook_secret", "") or "").strip()
        if not secret:
            # Refusing is the only safe answer: without the secret we cannot tell a real
            # Razorpay webhook from an HTTP request any stranger can send.
            logger.error("A Razorpay webhook arrived but RAZORPAY_WEBHOOK_SECRET is unset")
            raise ApiError(
                503, "webhook_unconfigured", "Webhook verification is not configured."
            )

        provided = (
            headers.get("x-razorpay-signature")
            or headers.get("X-Razorpay-Signature")
            or ""
        )
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not provided or not hmac.compare_digest(expected, provided):
            raise ApiError(
                400, "invalid_signature", "The webhook signature did not verify."
            )

        import json

        return json.loads(body.decode() or "{}")

    def parse_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalise a Razorpay event to the shape the state machine expects.

        Only the events that move money are handled. ``order.paid`` is preferred over
        ``payment.authorized`` because authorised is not captured - treating it as paid
        would grant credits for money that can still fall through.
        """
        event = str(payload.get("event") or "")
        entities = payload.get("payload") or {}
        payment = ((entities.get("payment") or {}).get("entity")) or {}
        order = ((entities.get("order") or {}).get("entity")) or {}
        refund = ((entities.get("refund") or {}).get("entity")) or {}

        order_id = payment.get("order_id") or order.get("id") or refund.get("payment_id")
        payment_id = payment.get("id") or refund.get("payment_id")

        if event in ("order.paid", "payment.captured"):
            kind = "paid"
        elif event == "payment.failed":
            kind = "failed"
        elif event in ("refund.created", "refund.processed", "payment.refunded"):
            kind = "refunded"
        else:
            kind = "ignored"

        return {
            # Razorpay sends an idempotency id in the header; the body's own id is used
            # as a fallback so an event can always be de-duplicated somehow.
            "event_id": payload.get("__event_id")
            or f"{event}:{payment_id or order_id}",
            "kind": kind,
            "order_id": order_id,
            "payment_id": payment_id,
            "amount_minor": int(payment.get("amount") or order.get("amount") or 0),
            "currency": payment.get("currency") or order.get("currency") or "INR",
        }
