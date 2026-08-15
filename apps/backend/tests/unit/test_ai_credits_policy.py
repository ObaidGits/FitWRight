"""Credit policy: conversion, estimates, three-tier resolution, velocity cap."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ai_credits import (
    FEATURE_FALLBACK_TOKENS,
    credits_for_tokens,
    describe_balance,
    estimate_credits,
    resolve_allowance,
    resolve_velocity_cap,
    velocity_exceeded,
)

NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


class TestConversion:
    def test_zero_tokens_costs_nothing(self):
        assert credits_for_tokens(0) == 0

    def test_rounds_up_so_small_calls_are_not_free(self):
        """Rounding down would let a stream of tiny calls each cost zero and add up
        to unlimited free usage."""
        assert credits_for_tokens(1) == 1
        assert credits_for_tokens(999) == 1
        assert credits_for_tokens(1000) == 1
        assert credits_for_tokens(1001) == 2

    def test_negative_input_cannot_credit_the_user(self):
        assert credits_for_tokens(-5000) == 0

    def test_returns_an_int_not_a_float(self):
        """No floats in a money path - binary floating point cannot represent
        decimal currency exactly and the error compounds."""
        assert isinstance(credits_for_tokens(12345), int)


class TestEstimates:
    @pytest.mark.asyncio
    async def test_uses_observed_usage_when_available(self):
        class Db:
            async def feature_usage_percentile(self, feature, percentile=0.95):
                return 10000

        got = await estimate_credits(Db(), "resume_tailor")
        # 10000 tokens * 1.3 headroom = 13000 -> 13 credits
        assert got == 13

    @pytest.mark.asyncio
    async def test_falls_back_to_a_conservative_constant_when_data_is_thin(self):
        class Db:
            async def feature_usage_percentile(self, feature, percentile=0.95):
                return None

        got = await estimate_credits(Db(), "cover_letter")
        expected = credits_for_tokens(int(FEATURE_FALLBACK_TOKENS["cover_letter"] * 1.3))
        assert got == expected

    @pytest.mark.asyncio
    async def test_an_unknown_feature_still_gets_an_estimate(self):
        """A new feature must not crash a request just because it has no entry."""
        class Db:
            async def feature_usage_percentile(self, feature, percentile=0.95):
                return None

        assert await estimate_credits(Db(), "brand_new_feature") > 0

    @pytest.mark.asyncio
    async def test_estimation_failure_does_not_block_the_user(self):
        class Db:
            async def feature_usage_percentile(self, feature, percentile=0.95):
                raise RuntimeError("ledger unavailable")

        assert await estimate_credits(Db(), "resume_tailor") > 0


class TestThreeTierResolution:
    def test_inherits_the_global_default_with_no_override(self):
        assert resolve_allowance({}, global_default=50) == 50

    def test_an_override_wins_over_the_global_default(self):
        assert resolve_allowance({"monthly_allowance_override": 10}, global_default=50) == 10

    def test_an_override_of_zero_is_respected_not_treated_as_unset(self):
        """Zero means 'this user gets nothing' - a deliberate restriction that must
        not silently fall through to the generous global default."""
        assert resolve_allowance({"monthly_allowance_override": 0}, global_default=50) == 0

    def test_raising_the_global_default_does_not_widen_an_override(self):
        """The property that makes restrictions trustworthy: an operator adjusting
        the default must not silently re-grant to users they had restricted."""
        account = {"monthly_allowance_override": 10}
        assert resolve_allowance(account, global_default=50) == 10
        assert resolve_allowance(account, global_default=5000) == 10

    def test_velocity_cap_resolves_the_same_way(self):
        assert resolve_velocity_cap({}, global_default=100) == 100
        assert resolve_velocity_cap({"velocity_cap_override": 5}, global_default=100) == 5
        assert resolve_velocity_cap({"velocity_cap_override": 0}, global_default=100) == 0


class TestVelocity:
    def test_a_cap_of_zero_disables_the_check(self):
        assert velocity_exceeded({"velocity_spent": 999}, cap=0, additional=50, now=NOW) is False

    def test_under_the_cap_is_allowed(self):
        acct = {"velocity_spent": 10, "velocity_window_start": NOW.isoformat()}
        assert velocity_exceeded(acct, cap=100, additional=20, now=NOW) is False

    def test_over_the_cap_is_refused(self):
        """Credits alone do not stop a stolen session draining a funded wallet in
        one minute."""
        acct = {"velocity_spent": 90, "velocity_window_start": NOW.isoformat()}
        assert velocity_exceeded(acct, cap=100, additional=20, now=NOW) is True

    def test_exactly_at_the_cap_is_allowed(self):
        acct = {"velocity_spent": 80, "velocity_window_start": NOW.isoformat()}
        assert velocity_exceeded(acct, cap=100, additional=20, now=NOW) is False

    def test_a_stale_window_resets(self):
        acct = {
            "velocity_spent": 999,
            "velocity_window_start": (NOW - timedelta(hours=2)).isoformat(),
        }
        assert velocity_exceeded(acct, cap=100, additional=20, now=NOW) is False

    def test_an_unparseable_window_resets_rather_than_locking_the_user_out(self):
        acct = {"velocity_spent": 999, "velocity_window_start": "garbage"}
        assert velocity_exceeded(acct, cap=100, additional=20, now=NOW) is False


class TestUserFacingDescription:
    def test_describes_balance_in_actions_not_credits(self):
        """'About 12 more tailorings' is actionable; '148 credits' is not."""
        text = describe_balance(260, feature="resume_tailor")
        assert "resume" in text and "10" in text

    def test_singular_reads_naturally(self):
        per_action = credits_for_tokens(int(FEATURE_FALLBACK_TOKENS["resume_tailor"] * 1.3))
        assert describe_balance(per_action, feature="resume_tailor") == (
            "about 1 more tailored resume"
        )

    def test_an_empty_balance_says_so_plainly(self):
        assert "not enough" in describe_balance(0, feature="resume_tailor")
