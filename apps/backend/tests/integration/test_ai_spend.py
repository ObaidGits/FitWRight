"""The spend guard: what endpoints actually call.

Each test corresponds to a way this goes wrong in production: charging for our own
outage, charging a user who brought their own key, refusing mid-save instead of
up-front, and collapsing three different failures into one message.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.ai_spend import InsufficientCredits, ai_spend, check_can_spend
from app.errors import ApiError
from app.models import CreditAccount


async def _fund(db, user_id: str, *, allowance: int = 0, wallet: int = 0):
    await db.get_or_create_credit_account(user_id)
    async with db.session_factory() as session:
        row = await session.get(CreditAccount, user_id)
        row.allowance_credits = allowance
        row.wallet_credits = wallet
        # Stamp the CURRENT period so the lazy allowance grant treats this account as
        # already topped up for the month. Without it, `allowance=0` would be read as
        # "never granted" and refilled - and a test that means "this user is out of
        # credits" would silently become "this user has 50".
        row.allowance_period_start = datetime.now(timezone.utc).isoformat()
        await session.commit()


@pytest.fixture
def credits_on(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "ai_credits_enabled", True)
    return settings


@pytest.mark.asyncio
class TestFlagOff:
    async def test_is_a_passthrough_when_disabled(self, isolated_db, owner_id, monkeypatch):
        """The whole feature ships dark - flag off must not touch a balance."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        async with ai_spend(owner_id, feature="cover_letter") as spend:
            spend.record(total_tokens=5000)
            assert spend.billing_bypassed is True

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["lifetime_spent"] == 0


@pytest.mark.asyncio
class TestOwnKey:
    async def test_own_key_is_metered_but_never_charged(self, isolated_db, owner_id, credits_on):
        """A user on their own key costs the operator nothing, so billing them
        would be indefensible - and it makes out-of-credits a choice, not a wall."""
        await _fund(isolated_db, owner_id, wallet=1000)
        async with ai_spend(owner_id, feature="cover_letter", has_own_key=True) as spend:
            spend.record(total_tokens=9000)
            assert spend.billing_bypassed is True

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 1000, "charged nothing"

        history = await isolated_db.list_usage(owner_id)
        assert len(history) == 1, "still metered for observability"
        assert history[0]["credits_charged"] == 0


@pytest.mark.asyncio
class TestRefusal:
    async def test_refuses_before_any_work_when_short(self, isolated_db, owner_id, credits_on):
        """Refusal must happen up-front, never mid-save."""
        await _fund(isolated_db, owner_id, wallet=1)
        did_work = False

        with pytest.raises(InsufficientCredits) as exc:
            async with ai_spend(owner_id, feature="resume_tailor"):
                did_work = True  # pragma: no cover - must never run

        assert did_work is False, "the body must not run when funds are short"
        assert exc.value.status_code == 402
        assert exc.value.code == "insufficient_credits"

    async def test_the_message_points_at_the_free_alternative(
        self, isolated_db, owner_id, credits_on
    ):
        """Out of credits is a choice, not a dead end - the copy must say so."""
        await _fund(isolated_db, owner_id, wallet=0)
        with pytest.raises(InsufficientCredits) as exc:
            async with ai_spend(owner_id, feature="resume_tailor"):
                pass
        assert "own provider key" in exc.value.message

    async def test_blocked_account_gets_a_distinct_error(
        self, isolated_db, owner_id, credits_on
    ):
        """Three causes, three messages. Collapsing them is how an AI credential
        problem once rendered as 'You are offline'."""
        await _fund(isolated_db, owner_id, wallet=1000)
        await isolated_db.set_credit_policy(owner_id, ai_disabled=True)

        with pytest.raises(ApiError) as exc:
            async with ai_spend(owner_id, feature="cover_letter"):
                pass
        assert exc.value.status_code == 403
        assert exc.value.code == "ai_disabled"
        assert exc.value.code != "insufficient_credits"

    async def test_velocity_breach_is_a_429_not_a_402(
        self, isolated_db, owner_id, credits_on, monkeypatch
    ):
        """A funded user going too fast is rate-limited, not told they are broke -
        the remedy is 'wait', not 'buy more'."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_velocity_cap_per_hour", 1)
        await _fund(isolated_db, owner_id, wallet=1000)

        with pytest.raises(ApiError) as exc:
            async with ai_spend(owner_id, feature="resume_tailor"):
                pass
        assert exc.value.status_code == 429


@pytest.mark.asyncio
class TestSettlement:
    async def test_settles_at_actual_usage(self, isolated_db, owner_id, credits_on):
        await _fund(isolated_db, owner_id, wallet=1000)
        async with ai_spend(owner_id, feature="cover_letter") as spend:
            spend.record(total_tokens=3000, channel_id="c1", model="gpt-5-nano")

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["lifetime_spent"] == 3, "3000 tokens -> 3 credits"
        assert acct["reserved_credits"] == 0, "hold fully resolved"

    async def test_an_exception_releases_the_hold_and_charges_nothing(
        self, isolated_db, owner_id, credits_on
    ):
        """Provider 5xx, timeout, or a bug in our own code: the user must not pay."""
        await _fund(isolated_db, owner_id, wallet=1000)

        with pytest.raises(RuntimeError):
            async with ai_spend(owner_id, feature="resume_tailor") as spend:
                spend.record(total_tokens=5000)
                raise RuntimeError("provider exploded")

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 1000, "charged nothing"
        assert acct["reserved_credits"] == 0, "hold released, not stranded"
        assert acct["lifetime_spent"] == 0

    async def test_a_failure_leaves_a_provable_zero_charge_row(
        self, isolated_db, owner_id, credits_on
    ):
        """'We did not bill for this' must be provable, not merely absent."""
        await _fund(isolated_db, owner_id, wallet=1000)
        with pytest.raises(RuntimeError):
            async with ai_spend(owner_id, feature="resume_tailor"):
                raise RuntimeError("boom")

        history = await isolated_db.list_usage(owner_id)
        assert len(history) == 1
        assert history[0]["outcome"] == "failed"
        assert history[0]["credits_charged"] == 0

    async def test_not_recording_usage_releases_rather_than_guesses(
        self, isolated_db, owner_id, credits_on
    ):
        """If the caller never reported usage we do not know the cost, so we must
        not invent one - release and log instead."""
        await _fund(isolated_db, owner_id, wallet=1000)
        async with ai_spend(owner_id, feature="cover_letter"):
            pass  # no spend.record(...)

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["wallet_credits"] == 1000
        assert acct["reserved_credits"] == 0

    async def test_estimated_usage_is_flagged_in_the_ledger(
        self, isolated_db, owner_id, credits_on
    ):
        """An estimate must never be indistinguishable from a measurement, or
        reconciling against the provider's invoice is impossible."""
        await _fund(isolated_db, owner_id, wallet=1000)
        async with ai_spend(owner_id, feature="cover_letter") as spend:
            spend.record(total_tokens=2000, estimated=True)

        history = await isolated_db.list_usage(owner_id)
        assert history[0]["tokens_estimated"] is True


@pytest.mark.asyncio
class TestPreflightCheck:
    async def test_reports_cost_without_taking_a_hold(self, isolated_db, owner_id, credits_on):
        """The UI hint must not consume a hold just to render a number."""
        await _fund(isolated_db, owner_id, wallet=1000)
        decision = await check_can_spend(owner_id, "resume_tailor")
        assert decision.allowed is True
        assert decision.estimated_credits > 0

        acct = await isolated_db.get_or_create_credit_account(owner_id)
        assert acct["reserved_credits"] == 0

    async def test_reports_own_key_as_a_bypass(self, isolated_db, owner_id, credits_on):
        await _fund(isolated_db, owner_id)
        decision = await check_can_spend(owner_id, "resume_tailor", has_own_key=True)
        assert decision.allowed is True
        assert decision.billing_bypassed is True
