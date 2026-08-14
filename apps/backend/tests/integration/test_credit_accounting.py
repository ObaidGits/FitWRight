"""Reserve-then-settle accounting: the properties that keep money correct.

Every test here corresponds to a way real prepaid systems lose money or trust:
concurrent overdraft, double-charging on retry, billing for the operator's own
outage, and balances frozen by a crashed worker.
"""

import asyncio
from uuid import uuid4

import pytest

from app.models import CreditAccount


async def _account(db, user_id: str, *, allowance: int = 0, wallet: int = 0):
    """Create an account and fund it directly (bypassing grant idempotency)."""
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = allowance
        row.wallet_credits = wallet
        await session.commit()
    return await db.get_or_create_credit_account(user_id)


@pytest.mark.asyncio
class TestReserve:
    async def test_reserve_holds_credits_without_spending_them(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        status, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=30, idempotency_key=str(uuid4())
        )
        assert status == "created" and res is not None

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        # Held, not spent: the wallet is untouched but availability has dropped.
        assert acct["wallet_credits"] == 100
        assert acct["reserved_credits"] == 30
        assert acct["available_credits"] == 70

    async def test_refuses_when_balance_is_short(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=5)
        status, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=30, idempotency_key=str(uuid4())
        )
        assert status == "insufficient" and res is None

    async def test_concurrent_reservations_cannot_overdraw(self, isolated_db, owner_id):
        """THE test. Ten parallel attempts against a balance of 10, 4 credits each.

        Reading the balance then writing it would let all ten pass the same check
        before any of them wrote. Only two can legitimately win.
        """
        await _account(isolated_db, owner_id, wallet=10)

        results = await asyncio.gather(
            *[
                isolated_db.reserve_credits(
                    owner_id, feature="tailor", credits=4, idempotency_key=str(uuid4())
                )
                for _ in range(10)
            ]
        )
        created = [s for s, _ in results if s == "created"]
        assert len(created) == 2, f"expected exactly 2 winners, got {len(created)}"

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["reserved_credits"] == 8
        # The invariant that matters: availability never goes negative.
        assert acct["available_credits"] >= 0

    async def test_replayed_idempotency_key_reuses_the_same_hold(self, isolated_db, owner_id):
        """A retried HTTP request must not take a second hold."""
        await _account(isolated_db, owner_id, wallet=100)
        key = str(uuid4())
        first_status, first = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=25, idempotency_key=key
        )
        second_status, second = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=25, idempotency_key=key
        )
        assert first_status == "created"
        assert second_status == "replayed"
        assert first["id"] == second["id"]

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["reserved_credits"] == 25, "replay must not double-hold"

    async def test_blocked_account_cannot_reserve(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        await isolated_db.set_credit_policy(owner_id, state="blocked")
        status, _ = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=1, idempotency_key=str(uuid4())
        )
        assert status == "blocked"

    async def test_per_user_kill_switch_cannot_reserve(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        await isolated_db.set_credit_policy(owner_id, ai_disabled=True)
        status, _ = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=1, idempotency_key=str(uuid4())
        )
        assert status == "blocked"


@pytest.mark.asyncio
class TestSettle:
    async def test_settles_at_actual_cost_and_releases_the_rest(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=30, idempotency_key=str(uuid4())
        )
        assert await isolated_db.settle_reservation(res["id"], actual_credits=12) == "settled"

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 88, "charged actual, not the held estimate"
        assert acct["reserved_credits"] == 0, "remainder released"
        assert acct["lifetime_spent"] == 12

    async def test_spends_allowance_before_wallet(self, isolated_db, owner_id):
        """The free grant expires; the purchased balance does not. Burning the
        expiring one first is strictly better for the user."""
        await _account(isolated_db, owner_id, allowance=10, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=25, idempotency_key=str(uuid4())
        )
        await isolated_db.settle_reservation(res["id"], actual_credits=25)

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["allowance_credits"] == 0
        assert acct["wallet_credits"] == 85, "only the 15 not covered by allowance"

    async def test_never_charges_more_than_was_held(self, isolated_db, owner_id):
        """The hold is the user's guarantee of the worst case. If the real cost
        overran it, that is the operator's to absorb."""
        await _account(isolated_db, owner_id, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=10, idempotency_key=str(uuid4())
        )
        await isolated_db.settle_reservation(res["id"], actual_credits=999)

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 90, "capped at the 10 that were held"

    async def test_settling_twice_is_refused(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=10, idempotency_key=str(uuid4())
        )
        assert await isolated_db.settle_reservation(res["id"], actual_credits=10) == "settled"
        assert await isolated_db.settle_reservation(res["id"], actual_credits=10) == "not_held"

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 90, "charged once, not twice"


@pytest.mark.asyncio
class TestRelease:
    async def test_release_charges_nothing(self, isolated_db, owner_id):
        """Provider 5xx, timeout, our bug: the user must not pay for it."""
        await _account(isolated_db, owner_id, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=30, idempotency_key=str(uuid4())
        )
        assert await isolated_db.release_reservation(res["id"]) == "released"

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 100
        assert acct["reserved_credits"] == 0
        assert acct["lifetime_spent"] == 0

    async def test_sweep_frees_an_abandoned_hold(self, isolated_db, owner_id):
        """A crashed worker must not freeze part of a balance forever - the user
        would see 'insufficient credits' while their dashboard showed plenty."""
        await _account(isolated_db, owner_id, wallet=100)
        _, res = await isolated_db.reserve_credits(
            owner_id,
            feature="tailor",
            credits=40,
            idempotency_key=str(uuid4()),
            ttl_seconds=-1,  # already expired
        )
        assert (await isolated_db.get_or_create_credit_account(owner_id))["reserved_credits"] == 40

        assert await isolated_db.sweep_expired_reservations() == 1
        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["reserved_credits"] == 0
        assert acct["wallet_credits"] == 100, "swept, not charged"
        assert res is not None

    async def test_sweep_leaves_live_holds_alone(self, isolated_db, owner_id):
        await _account(isolated_db, owner_id, wallet=100)
        await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=40, idempotency_key=str(uuid4()), ttl_seconds=900
        )
        assert await isolated_db.sweep_expired_reservations() == 0
        assert (await isolated_db.get_or_create_credit_account(owner_id))["reserved_credits"] == 40


@pytest.mark.asyncio
class TestGrants:
    async def test_grant_is_idempotent(self, isolated_db, owner_id):
        """A redelivered payment webhook or a double-run refill job must grant once."""
        await _account(isolated_db, owner_id)
        key = "purchase:evt_123"
        assert await isolated_db.grant_credits(
            owner_id, credits=500, kind="purchase", idempotency_key=key
        ) == "granted"
        assert await isolated_db.grant_credits(
            owner_id, credits=500, kind="purchase", idempotency_key=key
        ) == "replayed"

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 500, "granted once"

    async def test_monthly_refill_replaces_rather_than_accumulates(self, isolated_db, owner_id):
        """Unused free allowance does not roll over (and the UI says so)."""
        await _account(isolated_db, owner_id)
        await isolated_db.grant_credits(
            owner_id,
            credits=50,
            kind="monthly_refill",
            idempotency_key="refill:2026-08",
            to_wallet=False,
        )
        await isolated_db.grant_credits(
            owner_id,
            credits=50,
            kind="monthly_refill",
            idempotency_key="refill:2026-09",
            to_wallet=False,
        )
        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["allowance_credits"] == 50, "replaced, not 100"

    async def test_purchased_credits_survive_a_refill(self, isolated_db, owner_id):
        """Expiring paid credits is the most resented pattern in prepaid products."""
        await _account(isolated_db, owner_id)
        await isolated_db.grant_credits(
            owner_id, credits=200, kind="purchase", idempotency_key="p1"
        )
        await isolated_db.grant_credits(
            owner_id,
            credits=50,
            kind="monthly_refill",
            idempotency_key="refill:2026-08",
            to_wallet=False,
        )
        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 200
        assert acct["allowance_credits"] == 50


@pytest.mark.asyncio
class TestPolicy:
    async def test_override_can_be_set_and_cleared(self, isolated_db, owner_id):
        """Cleared means 'inherit the global default' - distinct from zero."""
        await _account(isolated_db, owner_id)
        acct = await isolated_db.set_credit_policy(owner_id, monthly_allowance_override=25)
        assert acct["monthly_allowance_override"] == 25

        acct = await isolated_db.set_credit_policy(owner_id, monthly_allowance_override=None)
        assert acct["monthly_allowance_override"] is None

    async def test_untouched_fields_are_preserved(self, isolated_db, owner_id):
        """The sentinel default must not silently wipe the other override."""
        await _account(isolated_db, owner_id)
        await isolated_db.set_credit_policy(owner_id, velocity_cap_override=60)
        await isolated_db.set_credit_policy(owner_id, monthly_allowance_override=25)
        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["velocity_cap_override"] == 60
        assert acct["monthly_allowance_override"] == 25

    async def test_lowering_a_limit_does_not_create_a_negative_balance(
        self, isolated_db, owner_id
    ):
        """Tightening stops further spend; it never retroactively charges."""
        await _account(isolated_db, owner_id, allowance=100)
        _, res = await isolated_db.reserve_credits(
            owner_id, feature="tailor", credits=40, idempotency_key=str(uuid4())
        )
        await isolated_db.settle_reservation(res["id"], actual_credits=40)
        await isolated_db.set_credit_policy(owner_id, monthly_allowance_override=10)

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["allowance_credits"] == 60, "existing balance untouched"
        assert acct["available_credits"] >= 0
