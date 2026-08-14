"""Buying credits. The rules here protect real money, in both directions.

Every test is a rule that, if broken, either takes money without delivering or delivers
without taking money:

* A client callback must never grant. Anyone who can read the page can send one.
* A redelivered webhook must not grant twice. Providers redeliver by design.
* A late event must not move a purchase backwards.
* A refund must claw back, even into a negative balance - otherwise buy/spend/refund is
  a free lunch.
* An amount lower than what we recorded must not buy the full pack.
"""

from __future__ import annotations

import json

import pytest

from app.ai_purchases import (
    FakePaymentProvider,
    PurchasesDisabled,
    available_packs,
    handle_webhook,
    start_purchase,
)
from app.errors import ApiError


@pytest.fixture
def purchases_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr(settings, "ai_purchases_enabled", True)
    monkeypatch.setattr(settings, "ai_payment_provider", "fake")
    monkeypatch.setattr(
        settings,
        "ai_credit_packs",
        json.dumps([{"id": "small", "credits": 100, "amount_minor": 19900, "currency": "INR"}]),
    )
    return settings


def _webhook(kind: str, *, order_id: str, event_id: str, amount_minor: int = 19900):
    body = json.dumps(
        {
            "event_id": event_id,
            "kind": kind,
            "order_id": order_id,
            "payment_id": "pay_1",
            "amount_minor": amount_minor,
            "currency": "INR",
        }
    ).encode()
    return {"body": body, "headers": {"x-fake-signature": "valid"}}


class TestPacks:
    def test_nothing_is_on_sale_by_default(self):
        """No default pricing. A default price is a guess, and a guess here is either a
        loss on every sale or an overcharge."""
        assert available_packs() == []

    def test_a_malformed_pack_list_sells_nothing(self, monkeypatch):
        """It must not degrade into a zero price or a free pack."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credit_packs", "{not json")
        assert available_packs() == []


@pytest.mark.asyncio
class TestPurchasesDisabledByDefault:
    async def test_starting_a_purchase_is_refused(self, isolated_db, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_purchases_enabled", False)
        with pytest.raises(PurchasesDisabled):
            await start_purchase("u-1", "small")

    async def test_refused_when_credits_are_off(self, isolated_db, monkeypatch):
        """Selling credits that nothing will spend or track."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_purchases_enabled", True)
        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        with pytest.raises(PurchasesDisabled):
            await start_purchase("u-1", "small")

    async def test_an_unimplemented_provider_refuses_rather_than_half_working(
        self, isolated_db, monkeypatch, purchases_on
    ):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_payment_provider", "razorpay")
        with pytest.raises(PurchasesDisabled):
            await start_purchase("u-1", "small")


class TestWebhookVerification:
    def test_an_unsigned_webhook_is_rejected(self):
        """The only thing between an attacker and free credits."""
        provider = FakePaymentProvider()
        with pytest.raises(ApiError):
            provider.verify_webhook(body=b"{}", headers={})

    def test_a_bad_signature_is_rejected(self):
        provider = FakePaymentProvider()
        with pytest.raises(ApiError):
            provider.verify_webhook(body=b"{}", headers={"x-fake-signature": "forged"})


@pytest.mark.asyncio
class TestGrantOnlyOnWebhook:
    async def test_starting_a_purchase_grants_nothing(self, isolated_db, purchases_on):
        """The client will claim success. That claim must not move a balance."""
        from app.database import db

        result = await start_purchase("u-1", "small")
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 0
        assert result["purchase_id"]

    async def test_a_verified_paid_webhook_grants_credits(self, isolated_db, purchases_on):
        from app.database import db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]

        outcome = await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))
        assert outcome["status"] == "granted"

        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100

    async def test_purchased_credits_go_to_the_wallet_not_the_allowance(
        self, isolated_db, purchases_on
    ):
        """The allowance is wiped at the month boundary. Someone who paid must not lose
        what they bought."""
        from app.database import db

        started = await start_purchase("u-1", "small")
        await handle_webhook(**_webhook("paid", order_id=started["order"]["order_id"], event_id="ev-1"))

        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100
        assert account["allowance_credits"] == 0

    async def test_a_redelivered_webhook_does_not_grant_twice(self, isolated_db, purchases_on):
        """Providers redeliver by design. Without idempotency a retry doubles a
        customer's credits."""
        from app.database import db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]
        first = await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))
        second = await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))

        assert first["status"] == "granted"
        assert second["status"] == "replayed"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100

    async def test_an_underpaid_amount_does_not_buy_the_pack(self, isolated_db, purchases_on):
        """The amount is re-checked against what WE recorded. A forged or mistaken event
        must not buy more than was paid for."""
        from app.database import db

        started = await start_purchase("u-1", "small")
        outcome = await handle_webhook(
            **_webhook("paid", order_id=started["order"]["order_id"], event_id="ev-1", amount_minor=1)
        )
        assert outcome["status"] == "failed"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 0

    async def test_an_unknown_order_is_ignored(self, isolated_db, purchases_on):
        outcome = await handle_webhook(**_webhook("paid", order_id="nope", event_id="ev-x"))
        assert outcome["status"] == "ignored"

    async def test_an_event_without_an_id_is_ignored(self, isolated_db, purchases_on):
        """With no event id there is no way to be idempotent, so doing nothing is the
        only safe answer."""
        body = json.dumps({"kind": "paid", "order_id": "x"}).encode()
        outcome = await handle_webhook(body=body, headers={"x-fake-signature": "valid"})
        assert outcome["status"] == "ignored"


@pytest.mark.asyncio
class TestForwardOnlyState:
    async def test_a_late_failure_cannot_undo_a_grant(self, isolated_db, purchases_on):
        """Providers deliver out of order. A stale `failed` must not revoke paid credits."""
        from app.database import db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]
        await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))
        await db.fail_purchase(started["purchase_id"], reason="late", event_id="ev-2")

        purchases = await db.list_purchases("u-1")
        assert purchases[0]["state"] == "granted"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 100


@pytest.mark.asyncio
class TestRefundClawBack:
    async def test_a_refund_takes_the_credits_back(self, isolated_db, purchases_on):
        from app.database import db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]
        await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))
        outcome = await handle_webhook(**_webhook("refunded", order_id=order_id, event_id="ev-2"))

        assert outcome["status"] == "refunded"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 0

    async def test_claw_back_may_go_negative_and_blocks_the_account(
        self, isolated_db, purchases_on
    ):
        """Otherwise buy, spend, refund is a free lunch. A blocked account with a
        negative balance is a conversation; silently absorbing the loss is not."""
        from app.database import CreditAccount, db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]
        await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))

        # Spend most of it.
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, "u-1")
            row.wallet_credits = 10
            await session.commit()

        await handle_webhook(**_webhook("refunded", order_id=order_id, event_id="ev-2"))

        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] < 0
        assert account["state"] == "blocked"

    async def test_a_repeated_refund_is_a_no_op(self, isolated_db, purchases_on):
        from app.database import db

        started = await start_purchase("u-1", "small")
        order_id = started["order"]["order_id"]
        await handle_webhook(**_webhook("paid", order_id=order_id, event_id="ev-1"))
        await handle_webhook(**_webhook("refunded", order_id=order_id, event_id="ev-2"))
        again = await db.refund_purchase(started["purchase_id"], event_id="ev-3")

        assert again == "replayed"
        account = await db.get_or_create_credit_account("u-1")
        assert account["wallet_credits"] == 0


@pytest.mark.asyncio
class TestInvoiceAndReconciliation:
    async def test_an_invoice_number_is_assigned_when_the_sale_is_real(
        self, isolated_db, purchases_on
    ):
        from app.database import db

        started = await start_purchase("u-1", "small")
        await handle_webhook(**_webhook("paid", order_id=started["order"]["order_id"], event_id="ev-1"))

        purchases = await db.list_purchases("u-1")
        assert purchases[0]["invoice_number"]

    async def test_reconciliation_is_clean_after_a_normal_sale(self, isolated_db, purchases_on):
        from app.database import db

        started = await start_purchase("u-1", "small")
        await handle_webhook(**_webhook("paid", order_id=started["order"]["order_id"], event_id="ev-1"))

        report = await db.purchase_reconciliation()
        assert report["status"] == "ok"

    async def test_a_users_purchases_are_scoped_to_them(self, isolated_db, purchases_on):
        from app.database import db

        await start_purchase("u-1", "small")
        await start_purchase("u-2", "small")

        assert len(await db.list_purchases("u-1")) == 1
        assert len(await db.list_purchases("u-2")) == 1
