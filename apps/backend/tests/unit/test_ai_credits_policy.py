"""Credit policy: conversion, estimates, three-tier resolution, velocity cap."""

from datetime import datetime, timedelta, timezone

import pytest

from app.ai_credits import (
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


class TestPublishedPrice:
    """The charge is the PUBLISHED price now, not a percentile of past token use.

    A variable charge cannot be quoted before the action runs, and a final charge that
    differs from the number the user was shown reads as being cheated. These tests pin
    that the price comes from the admin-editable rows and that nothing about a token
    measurement can move it.
    """

    @pytest.mark.asyncio
    async def test_uses_the_admin_set_price(self):
        class Db:
            async def list_feature_prices(self, only_active: bool = False):
                return [
                    {
                        "feature": "resume_tailor",
                        "label": "Tailored resume",
                        "credits": 20,
                        "is_charged": True,
                        "active": True,
                        "sort_order": 10,
                        "description": None,
                    }
                ]

        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()
        assert await estimate_credits(Db(), "resume_tailor") == 20

    @pytest.mark.asyncio
    async def test_observed_token_usage_does_not_change_the_charge(self):
        """The old behaviour, explicitly rejected: a feature that happened to consume
        more tokens must not silently cost the user more than the price list says."""

        class Db:
            async def list_feature_prices(self, only_active: bool = False):
                return [
                    {
                        "feature": "cover_letter",
                        "label": "Cover letter",
                        "credits": 4,
                        "is_charged": True,
                        "active": True,
                        "sort_order": 10,
                        "description": None,
                    }
                ]

            async def feature_usage_percentile(self, feature, percentile=0.95):
                return 999_000  # enormous real usage

        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()
        assert await estimate_credits(Db(), "cover_letter") == 4

    @pytest.mark.asyncio
    async def test_a_feature_marked_free_costs_nothing(self):
        class Db:
            async def list_feature_prices(self, only_active: bool = False):
                return [
                    {
                        "feature": "match_score",
                        "label": "Match score",
                        "credits": 4,
                        # Free on purpose. The price is retained underneath so turning
                        # charging back on does not require re-entering it.
                        "is_charged": False,
                        "active": True,
                        "sort_order": 10,
                        "description": None,
                    }
                ]

        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()
        assert await estimate_credits(Db(), "match_score") == 0

    @pytest.mark.asyncio
    async def test_an_unpriced_feature_falls_back_rather_than_running_free(self):
        """A missing price row must not mean free. An unpriced feature that runs for
        nothing is a revenue leak nobody notices."""

        class Db:
            async def list_feature_prices(self, only_active: bool = False):
                return []

        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()
        assert await estimate_credits(Db(), "resume_tailor") > 0
        assert await estimate_credits(Db(), "brand_new_feature") > 0

    @pytest.mark.asyncio
    async def test_a_lookup_failure_does_not_block_the_user(self):
        class Db:
            async def list_feature_prices(self, only_active: bool = False):
                raise RuntimeError("database unavailable")

        from app.ai_feature_prices import invalidate_price_cache

        invalidate_price_cache()
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
        """'About 10 more applications' is actionable; '260 credits' is not."""
        text = describe_balance(260, per_action_credits=26)
        assert "application" in text and "10" in text

    def test_singular_reads_naturally(self):
        assert describe_balance(26, per_action_credits=26) == "about 1 more application"

    def test_an_empty_balance_says_so_plainly(self):
        assert "not enough" in describe_balance(0, per_action_credits=26)

    def test_the_per_action_figure_is_supplied_not_assumed(self):
        """It is passed in so the sentence uses the SAME number the pricing screen
        shows. Deriving it independently in two places is how a balance summary ends up
        contradicting the price list beside it."""
        assert describe_balance(100, per_action_credits=10) == "about 10 more applications"
        assert describe_balance(100, per_action_credits=50) == "about 2 more applications"

    def test_a_zero_price_cannot_divide_by_zero(self):
        assert "application" in describe_balance(100, per_action_credits=0)
