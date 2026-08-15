"""Pack prices and discounts. Every test here guards money.

The dangerous mistakes in discount logic are quiet ones: a sale that never expires, a
"discount" that raises the price, or a page advertising one figure while the charge is
another. Each has a test below.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.ai_pack_pricing import discounted_amount, effective_offer, percent_off, sale_is_live


def _row(**over):
    base = {
        "id": "starter",
        "label": "Starter",
        "credits": 100,
        "amount_minor": 19900,  # 199.00
        "currency": "INR",
        "sale_amount_minor": None,
        "sale_label": None,
        "sale_starts_at": None,
        "sale_ends_at": None,
        "active": True,
        "sort_order": 100,
        "description": None,
    }
    base.update(over)
    return base


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


class TestDiscountMath:
    def test_a_percentage_becomes_an_exact_integer(self):
        """Computed ONCE at save time. Storing the percentage would re-multiply it on
        every render and every check, and a one-paisa disagreement between the buy screen
        and the webhook amount check fails a real customer's purchase."""
        assert discounted_amount(19900, 20) == 15920

    def test_rounding_never_exceeds_the_original_price(self):
        assert discounted_amount(100, 0) == 100
        assert discounted_amount(100, 100) == 0

    def test_a_nonsense_percentage_is_clamped(self):
        assert discounted_amount(1000, -50) == 1000
        assert discounted_amount(1000, 500) == 0

    def test_percent_off_is_display_only_and_never_negative(self):
        assert percent_off(19900, 15920) == 20
        # A "sale" that is not cheaper reports no saving rather than a negative one.
        assert percent_off(19900, 25000) == 0


class TestSaleWindow:
    def test_a_sale_with_no_window_is_live(self):
        """An offer with no dates is simply on until the operator ends it."""
        assert sale_is_live(sale_amount_minor=1000, sale_starts_at=None, sale_ends_at=None)

    def test_a_future_sale_is_not_live_yet(self):
        assert not sale_is_live(
            sale_amount_minor=1000,
            sale_starts_at=_iso(days=1),
            sale_ends_at=_iso(days=2),
        )

    def test_an_expired_sale_is_not_live(self):
        """THE one that matters: an offer that keeps selling at the discount because
        nothing reverted it is a slow leak nobody notices."""
        assert not sale_is_live(
            sale_amount_minor=1000,
            sale_starts_at=_iso(days=-9),
            sale_ends_at=_iso(days=-1),
        )

    def test_no_sale_price_means_no_sale(self):
        assert not sale_is_live(
            sale_amount_minor=None, sale_starts_at=None, sale_ends_at=_iso(days=1)
        )

    def test_an_unparseable_date_is_treated_as_absent(self):
        """A typo in a date must not silently charge the regular price to customers who
        were shown a discount."""
        assert sale_is_live(
            sale_amount_minor=1000, sale_starts_at="not-a-date", sale_ends_at=None
        )


class TestEffectivePrice:
    def test_no_sale_charges_the_regular_price(self):
        offer = effective_offer(_row())
        assert offer.amount_minor == 19900
        assert offer.on_sale is False
        # No comparison price, so the UI cannot render an empty "was" line.
        assert offer.compare_at_minor is None

    def test_a_live_sale_charges_the_sale_price_and_shows_the_old_one(self):
        offer = effective_offer(
            _row(sale_amount_minor=15920, sale_label="Launch offer", sale_ends_at=_iso(days=3))
        )
        assert offer.amount_minor == 15920
        assert offer.compare_at_minor == 19900
        assert offer.percent_off == 20
        assert offer.sale_label == "Launch offer"

    def test_an_expired_sale_reverts_to_the_regular_price_automatically(self):
        offer = effective_offer(
            _row(sale_amount_minor=9900, sale_ends_at=_iso(days=-1))
        )
        assert offer.amount_minor == 19900
        assert offer.on_sale is False

    def test_a_sale_that_is_not_cheaper_is_ignored(self):
        """Defence in depth - the write path refuses these, but a bad row must never
        charge MORE under a discount label."""
        offer = effective_offer(_row(sale_amount_minor=25000))
        assert offer.amount_minor == 19900
        assert offer.on_sale is False


@pytest.mark.asyncio
class TestPackStorage:
    async def test_a_pack_can_be_created_and_read_back(self, isolated_db):
        from app.database import db

        created = await db.upsert_credit_pack(
            "starter", label="Starter", credits=100, amount_minor=19900, active=True
        )
        assert created["credits"] == 100
        assert (await db.get_credit_pack("starter"))["amount_minor"] == 19900

    async def test_a_sale_above_the_regular_price_is_refused(self, isolated_db):
        """An operator fat-fingering a "discount" that raises the price would otherwise
        be charging more under a banner that says less."""
        from app.database import db

        await db.upsert_credit_pack("s", label="S", credits=10, amount_minor=1000)
        with pytest.raises(ValueError, match="lower than the regular price"):
            await db.upsert_credit_pack("s", sale_amount_minor=2000)

    async def test_lowering_the_regular_price_under_a_sale_is_refused(self, isolated_db):
        """Validation judges the RESULTING row, not the patch: dropping the regular price
        alone could otherwise leave a stale sale sitting above it."""
        from app.database import db

        await db.upsert_credit_pack(
            "s", label="S", credits=10, amount_minor=2000, sale_amount_minor=1500
        )
        with pytest.raises(ValueError):
            await db.upsert_credit_pack("s", amount_minor=1000)

    async def test_a_pack_with_no_credits_cannot_exist(self, isolated_db):
        """It would take money for nothing."""
        from app.database import db

        with pytest.raises(ValueError):
            await db.upsert_credit_pack("empty", label="E", credits=0, amount_minor=999)

    async def test_only_active_packs_are_offered(self, isolated_db):
        from app.database import db

        await db.upsert_credit_pack("on", label="On", credits=10, amount_minor=100, active=True)
        await db.upsert_credit_pack("off", label="Off", credits=10, amount_minor=100, active=False)

        offered = await db.list_credit_packs(only_active=True)
        assert [p["id"] for p in offered] == ["on"]
        # The admin still sees both - a withdrawn pack is a state to manage, not a row
        # to lose, because purchases reference it.
        assert len(await db.list_credit_packs()) == 2

    async def test_deleting_a_pack_leaves_purchase_history_readable(self, isolated_db):
        """Purchases record the pack id, credits and price they were charged, so history
        survives - which is why there is no foreign key."""
        from app.database import db

        await db.upsert_credit_pack("gone", label="Gone", credits=50, amount_minor=5000)
        purchase = await db.create_credit_purchase(
            user_id="u-1", pack_id="gone", credits=50,
            amount_minor=5000, currency="INR", provider="fake",
        )
        assert await db.delete_credit_pack("gone") is True

        history = await db.list_purchases("u-1")
        assert history[0]["pack_id"] == "gone"
        assert history[0]["credits"] == 50
        assert purchase["id"] == history[0]["id"]

    async def test_packs_are_ordered_for_display(self, isolated_db):
        from app.database import db

        await db.upsert_credit_pack("b", label="B", credits=10, amount_minor=100, sort_order=2)
        await db.upsert_credit_pack("a", label="A", credits=10, amount_minor=100, sort_order=1)
        assert [p["id"] for p in await db.list_credit_packs()] == ["a", "b"]


@pytest.mark.asyncio
class TestPurchaseUsesTheOfferedPrice:
    async def test_a_discounted_pack_charges_the_discounted_price(
        self, isolated_db, monkeypatch
    ):
        """The buy screen and the order must agree by construction - both go through
        effective_offer."""
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "ai_purchases_enabled", True)
        monkeypatch.setattr(settings, "ai_payment_provider", "fake")

        await db.upsert_credit_pack(
            "starter", label="Starter", credits=100, amount_minor=19900,
            sale_amount_minor=15920, active=True,
        )

        from app.ai_purchases import start_purchase

        result = await start_purchase("u-1", "starter")
        purchase = (await db.list_purchases("u-1"))[0]
        assert purchase["amount_minor"] == 15920
        assert result["order"]["amount_minor"] == 15920

    async def test_an_inactive_pack_cannot_be_bought(self, isolated_db, monkeypatch):
        from app.config import settings
        from app.database import db
        from app.errors import ApiError

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "ai_purchases_enabled", True)
        monkeypatch.setattr(settings, "ai_payment_provider", "fake")
        await db.upsert_credit_pack("hidden", label="H", credits=10, amount_minor=100, active=False)

        from app.ai_purchases import start_purchase

        with pytest.raises(ApiError):
            await start_purchase("u-1", "hidden")

    async def test_a_later_price_change_does_not_alter_a_purchase_in_flight(
        self, isolated_db, monkeypatch
    ):
        """The purchase records what the customer was shown. Re-reading the pack at grant
        time would let a price edit change what someone already agreed to pay."""
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "ai_purchases_enabled", True)
        monkeypatch.setattr(settings, "ai_payment_provider", "fake")

        await db.upsert_credit_pack(
            "starter", label="Starter", credits=100, amount_minor=19900, active=True
        )
        from app.ai_purchases import start_purchase

        await start_purchase("u-1", "starter")
        await db.upsert_credit_pack("starter", amount_minor=49900)

        purchase = (await db.list_purchases("u-1"))[0]
        assert purchase["amount_minor"] == 19900
