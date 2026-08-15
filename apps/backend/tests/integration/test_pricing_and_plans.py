"""Admin-editable feature prices, plans, and the daily search cap (migration 0040).

The three things these pin, in order of how badly each would hurt if it broke:

1. **A price edit changes what is charged.** The whole point of moving prices into the
   database was that an operator can change them without a redeploy. If the charge kept
   coming from code, the admin panel would be a decoration.

2. **A plan decides the monthly allowance.** It replaced one global number, so a user on
   a paid tier must actually receive that tier's credits - and a per-user override must
   still beat the plan, or an operator who deliberately restricted an account would have
   that silently undone by a plan edit.

3. **Search is capped, never charged.** These are different limits with different
   remedies, and collapsing them would tell a user who ran out of searches to buy
   credits - which would not give them another search.
"""

from __future__ import annotations

import pytest

from app.ai_feature_prices import invalidate_price_cache


@pytest.fixture(autouse=True)
def _clear_price_cache():
    """Prices are cached in-process; a stale cache would leak between tests."""
    invalidate_price_cache()
    yield
    invalidate_price_cache()


@pytest.fixture
def credits_on(monkeypatch):
    """Charging is off by default (the feature ships dark), so these tests turn it on."""
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    return settings


@pytest.mark.asyncio
class TestFeaturePrices:
    async def test_an_edited_price_is_what_gets_charged(self, isolated_db, owner_id, credits_on):
        """The admin panel has to be load-bearing, not decorative."""
        from app.ai_spend import ai_spend

        await isolated_db.upsert_feature_price(
            "cover_letter", label="Cover letter", credits=4, is_charged=True, active=True
        )
        await isolated_db.grant_credits(
            owner_id, credits=1000, kind="purchase", idempotency_key="t1", to_wallet=True
        )

        invalidate_price_cache()
        async with ai_spend(owner_id, feature="cover_letter") as spend:
            spend.record(total_tokens=1000)
        first = (await isolated_db.get_or_create_credit_account(owner_id))["lifetime_spent"]
        assert first == 4

        # Operator raises the price. No redeploy, no restart.
        await isolated_db.upsert_feature_price("cover_letter", credits=11)
        invalidate_price_cache()

        async with ai_spend(owner_id, feature="cover_letter") as spend:
            spend.record(total_tokens=1000)
        second = (await isolated_db.get_or_create_credit_account(owner_id))["lifetime_spent"]
        assert second - first == 11, "the new price took effect immediately"

    async def test_a_charged_feature_cannot_be_priced_at_zero(self, isolated_db):
        """Almost always a half-finished edit. Making something free has its own switch,
        so a zero price on a charged feature is rejected rather than silently applied."""
        with pytest.raises(ValueError, match="at least one credit"):
            await isolated_db.upsert_feature_price(
                "outreach", label="Outreach", credits=0, is_charged=True
            )

    async def test_turning_charging_off_preserves_the_price(self, isolated_db):
        """So switching it back on does not require remembering the old number."""
        await isolated_db.upsert_feature_price(
            "outreach", label="Outreach", credits=7, is_charged=True
        )
        row = await isolated_db.upsert_feature_price("outreach", is_charged=False)
        assert row["is_charged"] is False
        assert row["credits"] == 7, "the number is retained underneath"

    async def test_a_negative_price_is_refused(self, isolated_db):
        """It would credit the user for using a feature."""
        with pytest.raises(ValueError, match="negative"):
            await isolated_db.upsert_feature_price(
                "outreach", label="Outreach", credits=-5, is_charged=True
            )


@pytest.mark.asyncio
class TestPlans:
    async def test_the_plans_credits_are_what_gets_granted(self, isolated_db, owner_id, credits_on):
        from app.ai_allowance import ensure_allowance

        await isolated_db.upsert_subscription_plan(
            "paid", label="Job Hunt", price_minor=29900, monthly_credits=2000, active=True
        )
        await isolated_db.get_or_create_credit_account(owner_id)
        await isolated_db.set_account_plan(owner_id, "paid")

        account = await ensure_allowance(owner_id)
        assert account["allowance_credits"] == 2000

    async def test_a_per_user_override_still_beats_the_plan(
        self, isolated_db, owner_id, credits_on
    ):
        """An operator who deliberately restricted or comped one account must not have
        that quietly widened by an edit to the plan."""
        from app.ai_allowance import ensure_allowance

        await isolated_db.upsert_subscription_plan(
            "paid", label="Job Hunt", price_minor=29900, monthly_credits=2000, active=True
        )
        await isolated_db.get_or_create_credit_account(owner_id)
        await isolated_db.set_account_plan(owner_id, "paid")
        await isolated_db.set_credit_policy(owner_id, monthly_allowance_override=25)

        account = await ensure_allowance(owner_id)
        assert account["allowance_credits"] == 25

    async def test_only_one_plan_can_be_the_default(self, isolated_db):
        """Two defaults would make "which tier does a new user land on?" depend on row
        order - the kind of ambiguity that surfaces once real users are on the wrong one."""
        await isolated_db.upsert_subscription_plan(
            "free", label="Free", monthly_credits=300, is_default=True, active=True
        )
        await isolated_db.upsert_subscription_plan(
            "other", label="Other", monthly_credits=500, is_default=True, active=True
        )

        plans = {p["id"]: p for p in await isolated_db.list_subscription_plans()}
        assert plans["other"]["is_default"] is True
        assert plans["free"]["is_default"] is False, "the previous default was cleared"

    async def test_an_account_on_a_retired_plan_falls_back_to_default(self, isolated_db, owner_id):
        """The account is not at fault for a plan the operator deleted."""
        from app.ai_plans import resolve_account_plan

        await isolated_db.upsert_subscription_plan(
            "free", label="Free", monthly_credits=300, is_default=True, active=True
        )
        await isolated_db.get_or_create_credit_account(owner_id)
        await isolated_db.set_account_plan(owner_id, "deleted_tier")

        account = await isolated_db.get_or_create_credit_account(owner_id)
        plan = await resolve_account_plan(isolated_db, account)
        assert plan.id == "free"

    async def test_a_null_plan_resolves_to_default_without_a_backfill(
        self, isolated_db, owner_id
    ):
        """Resolved at read time on purpose: a backfill would miss every account created
        after it ran."""
        from app.ai_plans import resolve_account_plan

        await isolated_db.upsert_subscription_plan(
            "free", label="Free", monthly_credits=300, is_default=True, active=True
        )
        account = await isolated_db.get_or_create_credit_account(owner_id)
        assert account.get("plan_id") is None

        plan = await resolve_account_plan(isolated_db, account)
        assert plan.id == "free"

    async def test_with_no_plans_seeded_the_configured_setting_still_wins(
        self, isolated_db, owner_id, credits_on, monkeypatch
    ):
        """An install that never seeded plans has often still set the env allowance
        deliberately. The built-in stand-in must not quietly override it and change what
        existing users are granted."""
        from app.ai_allowance import ensure_allowance
        from app.ai_plans import resolve_account_plan
        from app.config import settings

        monkeypatch.setattr(settings, "ai_monthly_allowance_credits", 50)

        account = await isolated_db.get_or_create_credit_account(owner_id)
        plan = await resolve_account_plan(isolated_db, account)
        assert plan.is_fallback is True, "no plan rows exist in this database"

        granted = await ensure_allowance(owner_id)
        assert granted["allowance_credits"] == 50, "the configured value, not the stand-in"


@pytest.mark.asyncio
class TestDailySearchCap:
    async def test_counts_up_to_the_limit_then_refuses(self, isolated_db, owner_id):
        from app.ai_plans import PlanView, consume_search

        plan = PlanView(
            id="free",
            label="Free",
            price_minor=0,
            currency="INR",
            monthly_credits=300,
            search_daily_limit=2,
            is_default=True,
        )

        first = await consume_search(isolated_db, owner_id, plan)
        second = await consume_search(isolated_db, owner_id, plan)
        third = await consume_search(isolated_db, owner_id, plan)

        assert first.allowed and first.used == 1
        assert second.allowed and second.used == 2
        assert not third.allowed, "the third exceeds a limit of 2"
        assert third.remaining == 0

    async def test_an_uncapped_plan_still_records_volume(self, isolated_db, owner_id):
        """So an operator can see the usage before deciding whether to cap it."""
        from app.ai_plans import PlanView, consume_search

        plan = PlanView(
            id="unlimited",
            label="Unlimited",
            price_minor=99900,
            currency="INR",
            monthly_credits=9000,
            search_daily_limit=None,
            is_default=False,
        )

        for _ in range(5):
            result = await consume_search(isolated_db, owner_id, plan)
        assert result.allowed
        assert result.used == 5
        assert result.remaining is None

    async def test_a_zero_limit_refuses_the_first_search(self, isolated_db, owner_id):
        from app.ai_plans import PlanView, consume_search

        plan = PlanView(
            id="none",
            label="No searches",
            price_minor=0,
            currency="INR",
            monthly_credits=0,
            search_daily_limit=0,
            is_default=False,
        )
        result = await consume_search(isolated_db, owner_id, plan)
        assert not result.allowed

    async def test_checking_does_not_consume(self, isolated_db, owner_id):
        """The balance screen calls this on every page load; rendering a number must
        not spend one."""
        from app.ai_plans import PlanView, check_search_allowance

        plan = PlanView(
            id="free",
            label="Free",
            price_minor=0,
            currency="INR",
            monthly_credits=300,
            search_daily_limit=3,
            is_default=True,
        )
        for _ in range(3):
            await check_search_allowance(isolated_db, owner_id, plan)
        result = await check_search_allowance(isolated_db, owner_id, plan)
        assert result.used == 0
        assert result.allowed
