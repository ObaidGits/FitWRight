"""Provider cost: the number the operator's margin depends on.

The tests that matter are about REFUSING TO GUESS. A rate table that silently invents
a price for an unknown model produces a margin report that looks authoritative and is
wrong, and nothing downstream can detect it. So: unknown means zero AND counted.
"""

from __future__ import annotations

import pytest

from app.ai_rates import cost_micros, resolve_rate


class TestRateResolution:
    def test_a_dated_model_id_still_matches_its_family(self):
        """Model ids carry dated suffixes. Exact-key matching would leave every real
        production model unpriced."""
        rate = resolve_rate("openai", "gpt-5-nano-2025-08-07")
        assert rate.known is True
        assert rate.prompt_micros_per_1k > 0

    def test_the_longest_match_wins(self):
        """`gpt-4o-mini` must not be priced as `gpt-4o`, which costs ~16x more."""
        mini = resolve_rate("openai", "gpt-4o-mini")
        full = resolve_rate("openai", "gpt-4o")
        assert mini.prompt_micros_per_1k < full.prompt_micros_per_1k

    def test_an_unknown_model_is_reported_unknown_not_guessed(self):
        rate = resolve_rate("someprovider", "a-model-nobody-has-priced")
        assert rate.known is False
        assert rate.prompt_micros_per_1k == 0

    def test_self_hosted_is_free(self):
        """The operator's own hardware has no per-token charge, and pretending
         otherwise would make local deployments look unprofitable."""
        assert resolve_rate("ollama", "llama3").known is True
        assert resolve_rate("ollama", "llama3").prompt_micros_per_1k == 0

    def test_an_operator_override_beats_the_default(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_rate_overrides", '{"gpt-4o-mini": [1, 2]}')
        rate = resolve_rate("openai", "gpt-4o-mini")
        assert (rate.prompt_micros_per_1k, rate.completion_micros_per_1k) == (1, 2)

    def test_a_malformed_override_falls_back_instead_of_raising(self, monkeypatch):
        """A typo in a reporting config must never take generation down."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_rate_overrides", "{not json")
        assert resolve_rate("openai", "gpt-4o-mini").known is True


class TestCost:
    def test_prices_prompt_and_completion_separately(self):
        cost, known = cost_micros(
            "openai", "gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000
        )
        assert known is True
        # 150 + 600 per 1k respectively.
        assert cost == 750

    def test_an_unknown_split_is_priced_at_the_dearer_rate(self):
        """Many providers report only a total. Pricing that at the cheap prompt rate
        would understate cost and overstate margin - a report that flatters itself is
        worse than no report."""
        total_only, _ = cost_micros("openai", "gpt-4o-mini", total_tokens=1000)
        completion_only, _ = cost_micros("openai", "gpt-4o-mini", completion_tokens=1000)
        assert total_only == completion_only

    def test_an_unknown_model_costs_zero_and_says_so(self):
        cost, known = cost_micros("x", "y", total_tokens=100000)
        assert cost == 0
        assert known is False

    def test_zero_usage_costs_nothing(self):
        assert cost_micros("openai", "gpt-4o-mini")[0] == 0


@pytest.mark.asyncio
class TestSpendSummary:
    async def test_aggregates_cost_credits_and_calls(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-a")
        for _ in range(3):
            await db.record_usage_only(
                "u-a",
                feature="resume_tailor",
                credits_charged=4,
                total_tokens=1000,
                provider_cost_micros=750,
                outcome="ok",
            )

        summary = await db.ai_spend_summary(days=30)
        assert summary["calls"] == 3
        assert summary["credits_charged"] == 12
        assert summary["provider_cost_micros"] == 2250
        assert summary["by_feature"][0]["feature"] == "resume_tailor"

    async def test_counts_unpriced_calls_so_a_partial_rate_table_is_visible(
        self, isolated_db
    ):
        """Without this the margin figure looks complete while missing whole models."""
        from app.database import db

        await db.get_or_create_credit_account("u-b")
        await db.record_usage_only(
            "u-b",
            feature="cover_letter",
            credits_charged=2,
            total_tokens=5000,
            provider_cost_micros=0,
            outcome="ok",
        )

        summary = await db.ai_spend_summary(days=30)
        assert summary["unpriced_calls"] == 1

    async def test_a_zero_token_row_is_not_counted_as_unpriced(self, isolated_db):
        """A failed call that burned nothing is genuinely free, not unpriced - and
        conflating them would make the rate table look broken after any outage."""
        from app.database import db

        await db.get_or_create_credit_account("u-c")
        await db.record_usage_only(
            "u-c",
            feature="cover_letter",
            credits_charged=0,
            total_tokens=0,
            provider_cost_micros=0,
            outcome="failed",
        )

        summary = await db.ai_spend_summary(days=30)
        assert summary["unpriced_calls"] == 0
        assert summary["failed_calls"] == 1

    async def test_breaks_down_by_day_feature_channel_and_user(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-d")
        await db.record_usage_only(
            "u-d",
            feature="interview_prep",
            channel_id="ch-1",
            credits_charged=6,
            total_tokens=9000,
            provider_cost_micros=1200,
            outcome="ok",
        )

        summary = await db.ai_spend_summary(days=30)
        assert summary["by_day"] and summary["by_day"][0]["calls"] == 1
        assert summary["by_channel"][0]["channel_id"] == "ch-1"
        assert summary["top_users"][0]["user_id"] == "u-d"
