"""Cost caps, channel probes, audit trail, and the env-key import.

Each of these closes a hole where the system LOOKED protected and was not:

* A cap that is stored, editable, and enforced nowhere is worse than no cap, because
  the operator stops watching.
* A channel with no way to test it can only be validated by sending real user traffic
  through it.
* A balance change with no administrator attached is a dispute nobody can settle.
"""

from __future__ import annotations


import pytest

from app.ai_channels import over_monthly_cap, select_channels


def _channel(**over):
    base = {
        "id": "ch-1",
        "name": "Primary",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_base": None,
        "priority": 100,
        "state": "active",
        "structured_verdict": "reliable",
        "monthly_cost_cap_cents": None,
    }
    base.update(over)
    return base


class TestCostCap:
    def test_no_cap_means_unlimited(self):
        assert over_monthly_cap(_channel(monthly_cost_cap_cents=None), 10**9) is False

    def test_zero_means_unlimited_not_forbidden(self):
        """A channel capped at zero would be indistinguishable from a disabled one,
        and `disabled` already exists as a state."""
        assert over_monthly_cap(_channel(monthly_cost_cap_cents=0), 10**9) is False

    def test_the_cents_to_micros_conversion_is_right(self):
        """Getting this wrong by 10,000x means a cap that never fires or one that
        fires instantly. 500 cents = 5_000_000 micros."""
        ch = _channel(monthly_cost_cap_cents=500)
        assert over_monthly_cap(ch, 4_999_999) is False
        assert over_monthly_cap(ch, 5_000_000) is True

    def test_a_capped_out_channel_is_not_selected(self):
        """The enforcement point. The field was editable in the admin UI for a while
        while doing nothing at all."""
        channels = [_channel(monthly_cost_cap_cents=100)]
        picked = select_channels(
            channels,
            {},
            feature="cover_letter",
            spend_by_channel={"ch-1": 1_000_000},  # exactly 100 cents
        )
        assert picked == []

    def test_a_channel_under_its_cap_is_still_selected(self):
        channels = [_channel(monthly_cost_cap_cents=100)]
        picked = select_channels(
            channels, {}, feature="cover_letter", spend_by_channel={"ch-1": 500_000}
        )
        assert len(picked) == 1

    def test_traffic_moves_to_the_uncapped_channel(self):
        """A cap must divert rather than deny while another channel can serve."""
        channels = [
            _channel(id="ch-capped", priority=1, monthly_cost_cap_cents=1),
            _channel(id="ch-open", priority=2, monthly_cost_cap_cents=None),
        ]
        picked = select_channels(
            channels,
            {},
            feature="cover_letter",
            spend_by_channel={"ch-capped": 10_000_000},
        )
        assert [c.id for c in picked] == ["ch-open"]


class TestStructuredVerdict:
    def test_clean_json_is_reliable(self):
        from app.ai_channel_test import _judge_structured

        assert _judge_structured('{"ok": true}') == "reliable"

    def test_json_wrapped_in_prose_is_flaky_not_unsupported(self):
        """Flaky, because a stricter prompt or the existing repair pass usually
        recovers it - calling it unsupported would bar the channel from features it
        can actually serve."""
        from app.ai_channel_test import _judge_structured

        assert _judge_structured('Sure! ```json\n{"ok": true}\n```') == "flaky"

    def test_prose_with_no_json_is_unsupported(self):
        from app.ai_channel_test import _judge_structured

        assert _judge_structured("I cannot output JSON.") == "unsupported"

    def test_an_empty_response_is_unsupported(self):
        from app.ai_channel_test import _judge_structured

        assert _judge_structured("") == "unsupported"

    def test_a_bare_list_is_flaky_not_reliable(self):
        """The schemas this app parses are objects. A model that returns a list is
        parseable but not what the features expect."""
        from app.ai_channel_test import _judge_structured

        assert _judge_structured("[1, 2, 3]") == "flaky"


class TestProbeErrorClassification:
    def test_an_auth_failure_says_check_the_key(self):
        from app.ai_channel_test import _classify

        cls, msg = _classify(Exception("AuthenticationError: invalid api key"))
        assert cls == "auth"
        assert "credential" in msg.lower() or "key" in msg.lower()

    def test_a_bad_model_name_is_not_reported_as_an_auth_problem(self):
        """The distinction that saves an operator from rotating a working key."""
        from app.ai_channel_test import _classify

        cls, _msg = _classify(Exception("The model `gpt-9` does not exist"))
        assert cls == "model"

    def test_a_quota_refusal_is_its_own_class(self):
        from app.ai_channel_test import _classify

        cls, _msg = _classify(Exception("429 insufficient_quota"))
        assert cls == "rate_limit"


@pytest.mark.asyncio
class TestEnvKeyAdoption:
    async def test_imports_the_env_key_as_a_disabled_channel(
        self, isolated_db, monkeypatch
    ):
        """Disabled, not active: creating it live would move real traffic onto a new
        code path during a deploy without anyone choosing that."""
        from app.ai_channel_import import adopt_env_key_as_channel
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "llm_api_key", "sk-operator")
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")

        created = await adopt_env_key_as_channel()
        assert created is not None
        assert created["state"] != "active"

        channels = await db.list_ai_channels()
        assert len(channels) == 1

    async def test_is_idempotent(self, isolated_db, monkeypatch):
        """Runs on every boot. Re-importing would multiply channels each restart."""
        from app.ai_channel_import import adopt_env_key_as_channel
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "llm_api_key", "sk-operator")
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")

        await adopt_env_key_as_channel()
        second = await adopt_env_key_as_channel()

        assert second is None
        assert len(await db.list_ai_channels()) == 1

    async def test_never_touches_an_operator_managed_setup(
        self, isolated_db, monkeypatch
    ):
        """Once channels exist the operator owns them. Silently re-adding one they
        deleted would be indistinguishable from a bug."""
        from app.ai_channel_import import adopt_env_key_as_channel
        from app.config import settings
        from app.database import db

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "llm_api_key", "sk-operator")
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")

        await db.create_ai_channel(
            name="Mine", provider="anthropic", model="claude-haiku-4.5", api_base=None
        )
        assert await adopt_env_key_as_channel() is None
        assert len(await db.list_ai_channels()) == 1

    async def test_does_nothing_while_credits_are_disabled(
        self, isolated_db, monkeypatch
    ):
        from app.ai_channel_import import adopt_env_key_as_channel
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        monkeypatch.setattr(settings, "llm_api_key", "sk-operator")
        assert await adopt_env_key_as_channel() is None

    async def test_does_nothing_without_a_key_to_import(self, isolated_db, monkeypatch):
        from app.ai_channel_import import adopt_env_key_as_channel
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(settings, "llm_api_key", "")
        monkeypatch.setattr(settings, "llm_provider", "openai")
        monkeypatch.setattr(settings, "llm_model", "gpt-4o-mini")
        assert await adopt_env_key_as_channel() is None


@pytest.mark.asyncio
class TestChannelSpendQuery:
    async def test_sums_this_month_per_channel(self, isolated_db):
        from app.database import db

        await db.get_or_create_credit_account("u-1")
        for cid, cost in (("ch-a", 100), ("ch-a", 250), ("ch-b", 900)):
            await db.record_usage_only(
                "u-1",
                feature="cover_letter",
                channel_id=cid,
                credits_charged=1,
                total_tokens=100,
                provider_cost_micros=cost,
                outcome="ok",
            )

        spend = await db.channel_spend_micros_this_month()
        assert spend["ch-a"] == 350
        assert spend["ch-b"] == 900

    async def test_rows_with_no_channel_are_excluded(self, isolated_db):
        """Own-key and fallback calls cost the operator nothing per channel, so
        attributing them to a cap would throttle channels for traffic they never
        served."""
        from app.database import db

        await db.get_or_create_credit_account("u-2")
        await db.record_usage_only(
            "u-2",
            feature="cover_letter",
            credits_charged=0,
            total_tokens=100,
            provider_cost_micros=500,
            outcome="ok",
        )
        assert await db.channel_spend_micros_this_month() == {}
