"""Receipts, purchase notification, and the settings that drive them.

What these pin, and why each matters:

* **Tax is broken OUT of an inclusive total.** Adding it on top would make the receipt
  disagree with the amount actually taken from the customer's card - the one number they
  can verify independently.
* **No GSTIN means no tax line and no tax id**, and the document calls itself a receipt
  rather than a tax invoice. Issuing a "tax invoice" while unregistered would be wrong in a
  way that matters, and a fresh startup below the threshold is the default case.
* **A notification failure never fails the purchase.** Credits are already granted when it
  runs; raising would turn a successful payment into an error and invite a second one.
* **A receipt is scoped to its owner**, and "not yours" is indistinguishable from "does not
  exist" so the endpoint cannot be used to probe other people's purchase ids.
"""

from __future__ import annotations

import pytest

from app.ai_receipts import build_receipt, build_receipt_html
from app.app_settings import SellerDetails, invalidate_settings_cache


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    invalidate_settings_cache()
    yield
    invalidate_settings_cache()


def _purchase(**over):
    base = {
        "id": "p-1",
        "user_id": "u-1",
        "pack_id": "starter",
        "credits": 200,
        "amount_minor": 14900,
        "currency": "INR",
        "state": "granted",
        "invoice_number": "INV-2026-08-15-abc",
        "created_at": "2026-08-15T10:00:00+00:00",
        "granted_at": "2026-08-15T10:01:00+00:00",
    }
    base.update(over)
    return base


class TestReceiptWithoutGst:
    """The default for an unregistered startup."""

    def test_is_a_receipt_not_a_tax_invoice(self):
        receipt = build_receipt(
            _purchase(), seller=SellerDetails(business_name="FitWright"), buyer_name="A", buyer_email="a@b.c"
        )
        assert receipt.kind == "receipt"
        assert receipt.title == "Payment Receipt"
        assert receipt.shows_tax is False

    def test_charges_no_tax_and_shows_no_tax_id(self):
        receipt = build_receipt(
            _purchase(), seller=SellerDetails(business_name="FitWright"), buyer_name="A", buyer_email="a@b.c"
        )
        assert receipt.tax_minor == 0
        assert receipt.subtotal_minor == receipt.total_minor

        html = build_receipt_html(receipt)
        assert "GSTIN" not in html
        assert "GST (" not in html
        assert "not a tax invoice" in html

    def test_a_tax_percent_without_a_gstin_is_not_applied(self):
        """The settings layer refuses to store this, but the renderer must not trust that -
        an existing row from before the check would otherwise print tax with no tax id."""
        seller = SellerDetails(business_name="FitWright", gstin="", tax_percent=18)
        receipt = build_receipt(_purchase(), seller=seller, buyer_name="A", buyer_email="a@b.c")
        assert receipt.tax_minor == 0
        assert receipt.kind == "receipt"


class TestReceiptWithGst:
    def test_tax_is_extracted_from_the_inclusive_total(self):
        """The customer paid 14900. Tax comes OUT of that, so the total still matches their
        card statement: 14900 * 18 / 118 = 2273."""
        seller = SellerDetails(business_name="FitWright", gstin="29ABCDE1234F1Z5", tax_percent=18)
        receipt = build_receipt(_purchase(), seller=seller, buyer_name="A", buyer_email="a@b.c")

        assert receipt.total_minor == 14900, "unchanged - this is what was charged"
        assert receipt.tax_minor == 2273
        assert receipt.subtotal_minor == 14900 - 2273
        assert receipt.subtotal_minor + receipt.tax_minor == receipt.total_minor

    def test_becomes_a_tax_invoice_and_prints_the_gstin(self):
        seller = SellerDetails(business_name="FitWright", gstin="29ABCDE1234F1Z5", tax_percent=18)
        receipt = build_receipt(_purchase(), seller=seller, buyer_name="A", buyer_email="a@b.c")
        html = build_receipt_html(receipt)

        assert receipt.title == "Tax Invoice"
        assert "29ABCDE1234F1Z5" in html
        assert "GST (18%)" in html


class TestReceiptDocument:
    def test_escapes_user_supplied_values(self):
        """A name is user-controlled and the document is HTML."""
        receipt = build_receipt(
            _purchase(),
            seller=SellerDetails(business_name="FitWright"),
            buyer_name='<script>alert("x")</script>',
            buyer_email="a@b.c",
        )
        html = build_receipt_html(receipt)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_a_refunded_payment_says_so_on_its_own_receipt(self):
        """Someone holding a receipt for money they got back, with nothing indicating it,
        has a document that misleads them."""
        receipt = build_receipt(
            _purchase(state="refunded"),
            seller=SellerDetails(business_name="FitWright"),
            buyer_name="A",
            buyer_email="a@b.c",
        )
        assert "refunded" in build_receipt_html(receipt).lower()

    def test_has_no_external_asset_references(self):
        """The renderer loads this string with no network, so a stylesheet or webfont link
        would silently not apply and the receipt would print unstyled."""
        receipt = build_receipt(
            _purchase(), seller=SellerDetails(business_name="FitWright"), buyer_name="A", buyer_email="a@b.c"
        )
        html = build_receipt_html(receipt)
        assert "<link" not in html
        assert "https://" not in html


@pytest.mark.asyncio
class TestPurchaseNotification:
    async def test_a_missing_purchase_is_reported_not_raised(self, isolated_db):
        from app.ai_purchase_notify import notify_purchase_complete

        result = await notify_purchase_complete("does-not-exist")
        assert result["error"] == "purchase_not_found"
        assert result["notified"] is False

    async def test_an_incomplete_purchase_is_not_announced(self, isolated_db, owner_id):
        """A 'paid' row whose credits have not landed would promise something the balance
        does not show."""
        from app.ai_purchase_notify import notify_purchase_complete

        purchase = await isolated_db.create_credit_purchase(
            user_id=owner_id,
            pack_id="starter",
            credits=200,
            amount_minor=14900,
            currency="INR",
            provider="razorpay",
        )
        result = await notify_purchase_complete(purchase["id"])
        assert result["notified"] is False
        assert "state=created" in str(result.get("skipped"))

    async def test_notifies_in_app_without_any_mail_configured(self, isolated_db, owner_id):
        """The in-app half must not depend on mail: it is the delivery that always works."""
        from app.ai_purchase_notify import notify_purchase_complete

        purchase = await isolated_db.create_credit_purchase(
            user_id=owner_id,
            pack_id="starter",
            credits=200,
            amount_minor=14900,
            currency="INR",
            provider="razorpay",
        )
        await isolated_db.grant_purchase(purchase["id"], event_id="ev-1")

        result = await notify_purchase_complete(purchase["id"])
        assert result["notified"] is True
        assert result["emailed"] is False, "no provider configured"

    async def test_is_idempotent_across_webhook_redelivery(self, isolated_db, owner_id):
        """Providers redeliver. One purchase must not produce two notifications."""
        from app.ai_purchase_notify import notify_purchase_complete

        purchase = await isolated_db.create_credit_purchase(
            user_id=owner_id,
            pack_id="starter",
            credits=200,
            amount_minor=14900,
            currency="INR",
            provider="razorpay",
        )
        await isolated_db.grant_purchase(purchase["id"], event_id="ev-1")

        await notify_purchase_complete(purchase["id"])
        await notify_purchase_complete(purchase["id"])

        from app.notifications.repo import get_notification_repo

        rows = await get_notification_repo().list(owner_id, limit=50)
        purchase_rows = [r for r in rows if r.get("type") == "credits_purchased"]
        assert len(purchase_rows) == 1


@pytest.mark.asyncio
class TestSellerSettings:
    async def test_a_tax_percent_requires_a_gstin(self, isolated_db):
        """Charging tax without a registration number produces a document that is wrong in
        a way that matters, so it is refused rather than stored."""
        from app.app_settings import save_seller_details

        with pytest.raises(ValueError, match="GSTIN"):
            await save_seller_details(
                isolated_db, {"business_name": "FitWright", "gstin": "", "tax_percent": 18}
            )

    async def test_saves_and_reads_back(self, isolated_db):
        from app.app_settings import get_seller_details, save_seller_details

        await save_seller_details(
            isolated_db,
            {
                "business_name": "FitWright",
                "address": "Bengaluru",
                "gstin": "29ABCDE1234F1Z5",
                "tax_percent": 18,
            },
        )
        seller = await get_seller_details(isolated_db)
        assert seller.business_name == "FitWright"
        assert seller.charges_tax is True


@pytest.mark.asyncio
class TestMailSettings:
    async def test_the_environment_wins_over_a_stored_row(self, isolated_db, monkeypatch):
        """An operator who configured SMTP in the environment must not see the panel
        silently override it - so the row is a fallback and the source says which won."""
        from app.app_settings import get_mail_transport, save_mail_transport
        from app.config import settings

        await save_mail_transport(
            isolated_db,
            {"provider": "smtp", "smtp_host": "db.example.com", "from_email": "db@example.com"},
            secret="db-secret",
        )
        invalidate_settings_cache()
        monkeypatch.setattr(settings, "email_provider", "smtp")
        monkeypatch.setattr(settings, "email_smtp_host", "env.example.com")
        monkeypatch.setattr(settings, "email_from", "env@example.com")

        mail = await get_mail_transport(isolated_db)
        assert mail.source == "env"
        assert mail.smtp_host == "env.example.com"

    async def test_the_secret_is_stored_encrypted_not_in_the_json(self, isolated_db):
        """A password inside the value blob would be readable by anything that can read the
        row, and would land in any dump or log that echoed settings."""
        from app.app_settings import MAIL_KEY, save_mail_transport

        await save_mail_transport(
            isolated_db,
            {"provider": "smtp", "smtp_host": "h", "from_email": "f@e.c"},
            secret="super-secret-pw",
        )
        row = await isolated_db.get_app_setting(MAIL_KEY)
        assert "super-secret-pw" not in str(row["value"])
        assert row["secret_ciphertext"]
        assert "super-secret-pw" not in row["secret_ciphertext"]

    async def test_a_blank_secret_keeps_the_stored_one(self, isolated_db):
        """The form submits blank whenever an unrelated field was edited; treating that as
        'clear the password' would break delivery constantly."""
        from app.app_settings import get_mail_transport, save_mail_transport

        await save_mail_transport(
            isolated_db,
            {"provider": "smtp", "smtp_host": "h", "from_email": "f@e.c"},
            secret="keep-me",
        )
        invalidate_settings_cache()
        await save_mail_transport(
            isolated_db,
            {"provider": "smtp", "smtp_host": "h2", "from_email": "f@e.c"},
            secret=None,
        )
        invalidate_settings_cache()
        mail = await get_mail_transport(isolated_db)
        assert mail.smtp_host == "h2", "the edit applied"
        assert mail.secret == "keep-me", "the password survived it"

    async def test_smtp_without_a_host_is_refused(self, isolated_db):
        from app.app_settings import save_mail_transport

        with pytest.raises(ValueError, match="host"):
            await save_mail_transport(isolated_db, {"provider": "smtp", "from_email": "f@e.c"})

    async def test_a_provider_without_a_from_address_is_refused(self, isolated_db):
        """Every provider rejects a message with no sender, and that error surfaces far
        from the setting that caused it."""
        from app.app_settings import save_mail_transport

        with pytest.raises(ValueError, match="from"):
            await save_mail_transport(isolated_db, {"provider": "smtp", "smtp_host": "h"})

    async def test_an_event_can_be_turned_off(self, isolated_db):
        from app.app_settings import get_mail_transport, save_mail_transport

        await save_mail_transport(
            isolated_db,
            {
                "provider": "smtp",
                "smtp_host": "h",
                "from_email": "f@e.c",
                "enabled_events": {"purchase_receipt": False},
            },
            secret="x",
        )
        invalidate_settings_cache()
        mail = await get_mail_transport(isolated_db)
        assert mail.sends("purchase_receipt") is False
        # An event nobody has configured defaults to ON, so adding a new one does not
        # require every existing deployment to opt in.
        assert mail.sends("welcome") is True
