"""Razorpay signatures. The only thing standing between a stranger and free credits.

Two algorithms with two different secrets, and confusing them is the classic integration
bug:

* checkout callback: HMAC_SHA256("<order_id>|<payment_id>", KEY_SECRET)
* webhook:           HMAC_SHA256(<raw body bytes>, WEBHOOK_SECRET)

Everything here verifies that a forged, altered, replayed or borrowed payment cannot move
a balance.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app.ai_razorpay import RazorpayProvider, verify_checkout_signature
from app.errors import ApiError

KEY_SECRET = "test_key_secret"
WEBHOOK_SECRET = "test_webhook_secret"


@pytest.fixture
def razorpay(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr(settings, "ai_purchases_enabled", True)
    monkeypatch.setattr(settings, "ai_payment_provider", "razorpay")
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_example")
    monkeypatch.setattr(settings, "razorpay_key_secret", KEY_SECRET)
    monkeypatch.setattr(settings, "razorpay_webhook_secret", WEBHOOK_SECRET)
    return settings


def _checkout_signature(order_id: str, payment_id: str, secret: str = KEY_SECRET) -> str:
    return hmac.new(
        secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256
    ).hexdigest()


def _webhook_signature(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestCheckoutSignature:
    def test_a_genuine_signature_verifies(self, razorpay):
        assert verify_checkout_signature(
            order_id="order_1",
            payment_id="pay_1",
            signature=_checkout_signature("order_1", "pay_1"),
        )

    def test_a_forged_signature_is_rejected(self, razorpay):
        assert not verify_checkout_signature(
            order_id="order_1", payment_id="pay_1", signature="deadbeef"
        )

    def test_a_signature_for_a_different_order_is_rejected(self, razorpay):
        """Reusing a real signature from one order on another must not work."""
        assert not verify_checkout_signature(
            order_id="order_2",
            payment_id="pay_1",
            signature=_checkout_signature("order_1", "pay_1"),
        )

    def test_a_signature_from_the_wrong_secret_is_rejected(self, razorpay):
        assert not verify_checkout_signature(
            order_id="order_1",
            payment_id="pay_1",
            signature=_checkout_signature("order_1", "pay_1", secret="attacker_secret"),
        )

    def test_missing_fields_are_rejected_rather_than_treated_as_empty(self, razorpay):
        assert not verify_checkout_signature(order_id="", payment_id="pay_1", signature="x")
        assert not verify_checkout_signature(order_id="o", payment_id="", signature="x")
        assert not verify_checkout_signature(order_id="o", payment_id="p", signature="")

    def test_unconfigured_credentials_refuse_rather_than_pass(self, monkeypatch):
        """With no secret there is nothing to verify against, so the answer must be no -
        never "close enough"."""
        from app.config import settings

        monkeypatch.setattr(settings, "razorpay_key_id", "")
        monkeypatch.setattr(settings, "razorpay_key_secret", "")
        with pytest.raises(ApiError):
            verify_checkout_signature(order_id="o", payment_id="p", signature="s")


class TestWebhookSignature:
    def test_a_genuine_webhook_verifies_and_parses(self, razorpay):
        body = json.dumps({"event": "order.paid"}).encode()
        payload = RazorpayProvider().verify_webhook(
            body=body, headers={"x-razorpay-signature": _webhook_signature(body)}
        )
        assert payload["event"] == "order.paid"

    def test_an_unsigned_webhook_is_rejected(self, razorpay):
        with pytest.raises(ApiError) as caught:
            RazorpayProvider().verify_webhook(body=b"{}", headers={})
        assert caught.value.status_code == 400

    def test_a_tampered_body_is_rejected(self, razorpay):
        """The signature covers the bytes, so changing the amount after signing fails."""
        original = json.dumps({"event": "order.paid", "amount": 100}).encode()
        signature = _webhook_signature(original)
        tampered = json.dumps({"event": "order.paid", "amount": 999999}).encode()

        with pytest.raises(ApiError):
            RazorpayProvider().verify_webhook(
                body=tampered, headers={"x-razorpay-signature": signature}
            )

    def test_the_key_secret_does_not_verify_a_webhook(self, razorpay):
        """The two secrets are different. Using the key secret here is the classic
        integration mistake and it must fail loudly rather than appear to work."""
        body = json.dumps({"event": "order.paid"}).encode()
        with pytest.raises(ApiError):
            RazorpayProvider().verify_webhook(
                body=body,
                headers={"x-razorpay-signature": _webhook_signature(body, secret=KEY_SECRET)},
            )

    def test_a_missing_webhook_secret_refuses_every_webhook(self, razorpay, monkeypatch):
        """Without it we cannot tell Razorpay from any stranger who found the URL."""
        from app.config import settings

        monkeypatch.setattr(settings, "razorpay_webhook_secret", "")
        body = b"{}"
        with pytest.raises(ApiError) as caught:
            RazorpayProvider().verify_webhook(
                body=body, headers={"x-razorpay-signature": _webhook_signature(body)}
            )
        assert caught.value.status_code == 503


class TestEventNormalisation:
    def test_order_paid_is_treated_as_paid(self, razorpay):
        event = RazorpayProvider().parse_event(
            {
                "event": "order.paid",
                "payload": {
                    "payment": {
                        "entity": {
                            "id": "pay_1",
                            "order_id": "order_1",
                            "amount": 19900,
                            "currency": "INR",
                        }
                    }
                },
            }
        )
        assert event["kind"] == "paid"
        assert event["order_id"] == "order_1"
        assert event["amount_minor"] == 19900

    def test_authorized_is_not_treated_as_paid(self, razorpay):
        """Authorised is not captured. Granting on it would hand out credits for money
        that can still fall through."""
        event = RazorpayProvider().parse_event(
            {
                "event": "payment.authorized",
                "payload": {"payment": {"entity": {"id": "pay_1", "order_id": "order_1"}}},
            }
        )
        assert event["kind"] == "ignored"

    def test_failed_and_refunded_are_recognised(self, razorpay):
        provider = RazorpayProvider()
        failed = provider.parse_event(
            {
                "event": "payment.failed",
                "payload": {"payment": {"entity": {"id": "p", "order_id": "o"}}},
            }
        )
        refunded = provider.parse_event(
            {
                "event": "refund.processed",
                "payload": {"refund": {"entity": {"payment_id": "p"}}},
            }
        )
        assert failed["kind"] == "failed"
        assert refunded["kind"] == "refunded"

    def test_an_unknown_event_is_ignored_not_guessed(self, razorpay):
        assert RazorpayProvider().parse_event({"event": "subscription.charged"})["kind"] == "ignored"


class TestOrderCreationGuards:
    def test_an_amount_below_the_provider_minimum_is_refused_locally(self, razorpay):
        """Caught here with an explanation rather than surfaced as a provider error."""
        with pytest.raises(ApiError) as caught:
            RazorpayProvider().create_order(amount_minor=50, currency="INR", reference="p1")
        assert caught.value.status_code == 400

    def test_missing_credentials_refuse_before_any_network_call(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "razorpay_key_id", "")
        monkeypatch.setattr(settings, "razorpay_key_secret", "")
        with pytest.raises(ApiError) as caught:
            RazorpayProvider().create_order(amount_minor=19900, currency="INR", reference="p1")
        assert caught.value.status_code == 503


@pytest.mark.asyncio
class TestConfirmEndpointRules:
    async def test_a_verified_callback_grants_once(self, isolated_db, razorpay):
        """Both confirmation routes converge on one idempotent grant, so a refresh or a
        double-submit cannot credit twice."""
        from app.database import db

        await db.upsert_credit_pack(
            "starter", label="Starter", credits=100, amount_minor=19900, active=True
        )
        purchase = await db.create_credit_purchase(
            user_id="u-1", pack_id="starter", credits=100,
            amount_minor=19900, currency="INR", provider="razorpay",
        )
        await db.attach_purchase_order(purchase["id"], order_id="order_1")

        first = await db.grant_purchase(purchase["id"], event_id="checkout:pay_1")
        second = await db.grant_purchase(purchase["id"], event_id="checkout:pay_1")

        assert first == "granted"
        assert second == "replayed"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100

    async def test_the_webhook_and_the_callback_do_not_double_grant(
        self, isolated_db, razorpay
    ):
        """The reason having two paths is safe: whichever lands first wins."""
        from app.database import db

        purchase = await db.create_credit_purchase(
            user_id="u-1", pack_id="starter", credits=100,
            amount_minor=19900, currency="INR", provider="razorpay",
        )
        await db.attach_purchase_order(purchase["id"], order_id="order_1")

        await db.grant_purchase(purchase["id"], event_id="checkout:pay_1")
        await db.grant_purchase(purchase["id"], event_id="evt_razorpay_1")

        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100
