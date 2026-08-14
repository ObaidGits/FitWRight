"""Hardening and observability: the guards that only matter when something is wrong.

Each of these exists because the failure it catches is silent. An uncapped input costs
the operator money without erroring; a channel failing behind failover looks healthy; a
broken accounting invariant produces numbers that still add up on screen.
"""

from __future__ import annotations

import pytest

from app.ai_input_limits import (
    InputTooLarge,
    ceiling_for,
    check_input_size,
    estimate_tokens_for_text,
)


class TestInputCeiling:
    def test_a_normal_resume_passes(self):
        """The limits must not police real users. A long CV is ~4k tokens."""
        resume = "Senior engineer with wide experience. " * 400
        assert check_input_size("resume_parse", resume) > 0

    def test_a_pasted_book_is_refused(self):
        book = "x" * 5_000_000
        with pytest.raises(InputTooLarge) as caught:
            check_input_size("resume_tailor", book)
        assert caught.value.status_code == 413

    def test_the_refusal_tells_the_user_the_limit(self):
        """A 413 with no number leaves the user guessing how much to cut."""
        with pytest.raises(InputTooLarge) as caught:
            check_input_size("outreach", "x" * 5_000_000)
        assert caught.value.details["limit_tokens"] > 0
        assert caught.value.details["estimated_tokens"] > caught.value.details["limit_tokens"]

    def test_all_parts_count_together(self):
        """Resume plus job description, not either alone - splitting one oversized
        payload across two fields must not slip through."""
        half = "x" * 100_000
        combined = estimate_tokens_for_text(half, half)
        assert combined == estimate_tokens_for_text(half) * 2

    def test_an_unknown_feature_still_gets_a_ceiling(self):
        """A new feature is covered the day it ships, not the day someone remembers
        to add it to the table."""
        assert ceiling_for("a_feature_added_next_year") > 0

    def test_ceilings_differ_by_feature(self):
        """One global number would either block real resumes or permit abuse
        everywhere else."""
        assert ceiling_for("resume_parse") > ceiling_for("outreach")

    def test_none_parts_are_tolerated(self):
        assert estimate_tokens_for_text(None, "abcd", None) == 1


@pytest.mark.asyncio
class TestChannelPerformance:
    async def test_reports_success_rate_and_p95(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-1")
        # 9 fast successes and one very slow failure.
        for i in range(9):
            await db.record_usage_only(
                "u-1",
                feature="cover_letter",
                channel_id="ch-a",
                credits_charged=1,
                total_tokens=100,
                latency_ms=100 + i,
                outcome="ok",
            )
        await db.record_usage_only(
            "u-1",
            feature="cover_letter",
            channel_id="ch-a",
            credits_charged=0,
            total_tokens=0,
            latency_ms=9000,
            outcome="failed",
        )

        rows = await db.channel_performance(days=7)
        row = next(r for r in rows if r["channel_id"] == "ch-a")
        assert row["calls"] == 10
        assert row["success_rate"] == 0.9
        # The point of p95 over a mean: the slow outlier must be visible.
        assert row["p95_latency_ms"] == 9000

    async def test_the_worst_channel_sorts_first(self, isolated_db):
        """An operator should not have to scroll to find the broken one."""
        from app.database import db

        await db.get_or_create_credit_account("u-2")
        for _ in range(5):
            await db.record_usage_only(
                "u-2", feature="cover_letter", channel_id="ch-good",
                credits_charged=1, total_tokens=10, latency_ms=50, outcome="ok",
            )
        for _ in range(5):
            await db.record_usage_only(
                "u-2", feature="cover_letter", channel_id="ch-bad",
                credits_charged=0, total_tokens=0, latency_ms=50, outcome="failed",
            )

        rows = await db.channel_performance(days=7)
        assert rows[0]["channel_id"] == "ch-bad"

    async def test_a_single_sample_does_not_index_past_the_end(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-3")
        await db.record_usage_only(
            "u-3", feature="cover_letter", channel_id="ch-solo",
            credits_charged=1, total_tokens=10, latency_ms=123, outcome="ok",
        )
        rows = await db.channel_performance(days=7)
        assert rows[0]["p95_latency_ms"] == 123


@pytest.mark.asyncio
class TestReconciliation:
    async def test_a_healthy_system_reports_ok(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-1")
        report = await db.credit_reconciliation()
        assert report["status"] == "ok"
        assert all(v == 0 for v in report["findings"].values())

    async def test_an_unswept_expired_hold_is_reported(self, isolated_db, monkeypatch):
        """The exact failure that silently freezes user balances."""
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        from app.ai_allowance import ensure_allowance

        await ensure_allowance("u-2")
        await db.reserve_credits(
            "u-2", feature="resume_tailor", credits=5, idempotency_key="k", ttl_seconds=0
        )

        report = await db.credit_reconciliation()
        assert report["findings"]["expired_holds_not_swept"] >= 1
        assert report["status"] == "attention"

    async def test_a_negative_balance_is_reported(self, isolated_db):
        from app.database import db
        from app.models import CreditAccount

        await db.get_or_create_credit_account("u-3")
        async with db.session_factory() as session:
            row = await session.get(CreditAccount, "u-3")
            row.wallet_credits = -5
            await session.commit()

        report = await db.credit_reconciliation()
        assert report["findings"]["negative_component_balances"] == 1


@pytest.mark.asyncio
class TestRetention:
    async def test_old_usage_rows_are_trimmed(self, isolated_db):
        from app.database import db
        from app.models import AiUsageLedger

        await db.get_or_create_credit_account("u-1")
        await db.record_usage_only(
            "u-1", feature="cover_letter", credits_charged=1, total_tokens=10, outcome="ok"
        )
        async with db.session_factory() as session:
            rows = (await session.execute(__import__("sqlalchemy").select(AiUsageLedger))).scalars().all()
            rows[0].created_at = "2019-01-01T00:00:00+00:00"
            await session.commit()

        removed = await db.trim_ai_usage_ledger(older_than_days=400)
        assert removed == 1

    async def test_recent_rows_survive(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-2")
        await db.record_usage_only(
            "u-2", feature="cover_letter", credits_charged=1, total_tokens=10, outcome="ok"
        )
        assert await db.trim_ai_usage_ledger(older_than_days=400) == 0

    async def test_credit_transactions_survive_a_trim(self, isolated_db, monkeypatch):
        """A grant row explains how a balance came to be. Deleting it would make the
        balance unexplainable, which is worse than a large table.

        Tested behaviourally: backdate BOTH a usage row and a grant row well past the
        horizon, trim, and confirm only the usage row goes.
        """
        import sqlalchemy as sa

        from app.config import settings
        from app.database import db
        from app.models import AiUsageLedger, CreditTransaction

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        await db.get_or_create_credit_account("u-3")
        await db.grant_credits(
            "u-3", credits=10, kind="admin_adjust", idempotency_key="k-old",
            reason="ancient goodwill",
        )
        await db.record_usage_only(
            "u-3", feature="cover_letter", credits_charged=1, total_tokens=10, outcome="ok"
        )

        old = "2019-01-01T00:00:00+00:00"
        async with db.session_factory() as session:
            for model in (AiUsageLedger, CreditTransaction):
                for row in (await session.execute(sa.select(model))).scalars().all():
                    row.created_at = old
            await session.commit()

        await db.trim_ai_usage_ledger(older_than_days=400)

        async with db.session_factory() as session:
            usage = (await session.execute(sa.select(AiUsageLedger))).scalars().all()
            grants = (await session.execute(sa.select(CreditTransaction))).scalars().all()

        assert usage == []
        assert len(grants) == 1, "a grant row was trimmed - the balance is now unexplainable"


@pytest.mark.asyncio
class TestAlerts:
    async def test_a_failing_channel_is_flagged_high(self, isolated_db):
        from app.ai_alerts import evaluate_ai_alerts
        from app.database import db

        await db.get_or_create_credit_account("u-1")
        for _ in range(25):
            await db.record_usage_only(
                "u-1", feature="cover_letter", channel_id="ch-bad",
                credits_charged=0, total_tokens=0, latency_ms=10, outcome="failed",
            )

        findings = await evaluate_ai_alerts(days=7)
        kinds = {f["kind"] for f in findings}
        assert "channel_error_rate" in kinds
        assert findings[0]["severity"] == "high"

    async def test_too_few_calls_is_not_judged(self, isolated_db):
        """Two failures out of two is noise. A jumpy alert trains the operator to
        ignore it."""
        from app.ai_alerts import evaluate_ai_alerts
        from app.database import db

        await db.get_or_create_credit_account("u-2")
        for _ in range(2):
            await db.record_usage_only(
                "u-2", feature="cover_letter", channel_id="ch-new",
                credits_charged=0, total_tokens=0, latency_ms=10, outcome="failed",
            )

        findings = await evaluate_ai_alerts(days=7)
        assert not any(f["kind"] == "channel_error_rate" for f in findings)

    async def test_an_approaching_cap_warns_before_it_trips(self, isolated_db):
        """Learning from a cap that already tripped means an outage first."""
        from app.ai_alerts import evaluate_ai_alerts
        from app.database import db

        channel = await db.create_ai_channel(
            name="Capped", provider="openai", model="gpt-4o-mini",
            api_base=None, monthly_cost_cap_cents=100,
        )
        await db.get_or_create_credit_account("u-3")
        await db.record_usage_only(
            "u-3", feature="cover_letter", channel_id=channel["id"],
            credits_charged=1, total_tokens=10,
            provider_cost_micros=850_000,  # 85% of 100 cents
            latency_ms=10, outcome="ok",
        )

        findings = await evaluate_ai_alerts(days=7)
        assert any(f["kind"] == "channel_cap_approaching" for f in findings)
