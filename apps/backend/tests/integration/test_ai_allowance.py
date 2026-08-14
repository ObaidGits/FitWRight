"""The free allowance: granted once, renewed once, never twice.

The tests that matter here are the ones about DOUBLE granting and about the period
boundary. A refill that can run twice hands out free money; one that never runs denies
users the month they were promised; one anchored to local time does both, for
different users, at different hours.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.ai_allowance import current_period, ensure_allowance, run_credit_maintenance_job
from app.models import CreditAccount


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    monkeypatch.setattr(settings, "ai_monthly_allowance_credits", 50)
    return settings


class TestPeriod:
    def test_is_anchored_to_utc(self):
        """Not local time: "the month" must not depend on which server answered, or
        on where the user happens to be at midnight."""
        # 23:30 on the last day of January, in a zone 5:30 AHEAD of UTC, is still
        # January in UTC - and a local-time implementation would call it February.
        late = datetime(2026, 1, 31, 23, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
        assert current_period(late) == "2026-01"

    def test_formats_as_sortable_year_month(self):
        """The format is load-bearing: the refill query compares it lexically."""
        assert current_period(datetime(2026, 3, 1, tzinfo=timezone.utc)) == "2026-03"
        assert current_period(datetime(2026, 12, 1, tzinfo=timezone.utc)) == "2026-12"
        assert "2026-03" < "2026-12"


@pytest.mark.asyncio
class TestEnsureAllowance:
    async def test_a_new_account_receives_its_allowance_on_first_touch(
        self, isolated_db, credits_on
    ):
        """This IS the signup grant. No hook to forget, and it covers users who
        already existed before the feature shipped."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        account = await ensure_allowance(uid)

        assert account is not None
        assert account["allowance_credits"] == 50
        assert account["available_credits"] == 50

    async def test_concurrent_first_touches_grant_exactly_once(
        self, isolated_db, credits_on
    ):
        """The test the idempotency key exists for.

        Repeated SEQUENTIAL calls are stopped by the period check - which is why a
        "call it five times" test proves nothing about the key. Concurrent calls all
        pass that check before any of them writes, and only the unique key on the
        transaction stops each one granting a full allowance. Two parallel requests
        from a fresh user is not exotic; it is what a page that loads the balance
        while the user clicks Generate looks like.
        """
        import asyncio

        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)

        await asyncio.gather(*(ensure_allowance(uid) for _ in range(8)))

        account = await db.get_or_create_credit_account(uid)
        assert account["allowance_credits"] == 50, "concurrent grants stacked"
        assert account["lifetime_granted"] == 50, "granted more than once"

    async def test_calling_it_repeatedly_grants_once(self, isolated_db, credits_on):
        """It runs on every touch, so this is the property that stops it from being a
        money printer."""
        uid = f"u-{uuid4().hex[:8]}"
        for _ in range(5):
            account = await ensure_allowance(uid)

        assert account is not None
        assert account["allowance_credits"] == 50
        assert account["lifetime_granted"] == 50

    async def test_a_rolled_over_period_renews_the_allowance(
        self, isolated_db, credits_on
    ):
        """Models a real month boundary: the last grant belongs to a PREVIOUS period.

        Deliberately not "grant, then backdate, then grant again" - that reuses the
        current period's idempotency key, so the second call is a correct replay and
        the test would pass while proving nothing.
        """
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, uid)
            row.allowance_credits = 3  # what is left of last month
            row.allowance_period_start = "2020-01-01T00:00:00+00:00"
            await session.commit()

        account = await ensure_allowance(uid)
        assert account is not None
        assert account["allowance_credits"] == 50, "the new period did not refill"

    async def test_the_allowance_replaces_rather_than_accumulates(
        self, isolated_db, credits_on
    ):
        """Use-it-or-lose-it. Rolling the free grant over would let a dormant account
        build a balance the operator must honour indefinitely."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, uid)
            row.allowance_credits = 40  # unspent from last month
            row.allowance_period_start = "2020-01-01T00:00:00+00:00"
            await session.commit()

        account = await ensure_allowance(uid)
        assert account is not None
        assert account["allowance_credits"] == 50, "allowance accumulated across periods"

    async def test_purchased_credits_survive_a_refill(self, isolated_db, credits_on):
        """The asymmetry that must hold: bought credits never expire, so a refill
        must not touch the wallet."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, uid)
            row.wallet_credits = 120
            row.allowance_credits = 2
            row.allowance_period_start = "2020-01-01T00:00:00+00:00"
            await session.commit()

        account = await ensure_allowance(uid)
        assert account is not None
        assert account["wallet_credits"] == 120, "a refill destroyed purchased credits"
        assert account["available_credits"] == 170

    async def test_a_per_user_override_beats_the_global_default(
        self, isolated_db, credits_on
    ):
        """An operator who restricted someone must not have that undone by a refill."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)
        await db.set_credit_policy(uid, monthly_allowance_override=5)

        account = await ensure_allowance(uid)
        assert account is not None
        assert account["allowance_credits"] == 5

    async def test_an_unparseable_period_repairs_itself(self, isolated_db, credits_on):
        """A corrupt stamp must not strand a user with no allowance forever."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, uid)
            row.allowance_period_start = "not-a-date"
            await session.commit()

        account = await ensure_allowance(uid)
        assert account is not None
        assert account["allowance_credits"] == 50

    async def test_does_nothing_while_the_feature_is_off(self, isolated_db, monkeypatch):
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        uid = f"u-{uuid4().hex[:8]}"
        assert await ensure_allowance(uid) is None


@pytest.mark.asyncio
class TestMaintenanceJob:
    async def test_releases_an_expired_hold(self, isolated_db, credits_on):
        """A hold left by a killed worker otherwise freezes credits forever: the user
        sees a balance they cannot spend, with no error that explains why."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await ensure_allowance(uid)
        status, reservation = await db.reserve_credits(
            uid, feature="resume_tailor", credits=10, idempotency_key="k1", ttl_seconds=0
        )
        assert status == "created"

        before = await db.get_or_create_credit_account(uid)
        assert before["reserved_credits"] == 10

        result = await run_credit_maintenance_job()
        assert result["swept"] >= 1

        after = await db.get_or_create_credit_account(uid)
        assert after["reserved_credits"] == 0, "the expired hold was never released"

    async def test_refills_a_dormant_account(self, isolated_db, credits_on):
        """The safety net: an operator viewing a dormant user must not see last
        month's figure."""
        from app.database import db

        uid = f"u-{uuid4().hex[:8]}"
        await db.get_or_create_credit_account(uid)

        result = await run_credit_maintenance_job()
        assert result["refilled"] >= 1

        account = await db.get_or_create_credit_account(uid)
        assert account["allowance_credits"] == 50

    async def test_still_sweeps_when_credits_are_disabled(self, isolated_db, monkeypatch):
        """Holds can exist from before the flag was turned off. Refusing to sweep
        them would leave those balances frozen permanently."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        result = await run_credit_maintenance_job()
        assert result["status"] == "ok"
        assert result["refilled"] == 0
