"""Unit tests for app.eligibility_rules — the visa-bug fix, pinned in isolation.

The property that matters most: a rule that cannot be resolved (job country
unknown) must fall back to the DEFAULT, never silently claim the same-country
value - a wrong knockout answer is worse than an unanswered one.
"""
from __future__ import annotations

from app.eligibility_rules import ConditionalAnswer, resolve_conditional


def _rule(**overrides) -> ConditionalAnswer:
    base = {"enabled": True, "default": "Yes - requires sponsorship", "same_country_value": "No"}
    base.update(overrides)
    return ConditionalAnswer(**base)


class TestResolveConditional:
    def test_disabled_rule_returns_the_flat_default_and_is_not_derived(self):
        rule = _rule(enabled=False)
        value, derived = resolve_conditional(
            "visaStatus", rule, job_country="US", profile_country="IN"
        )
        assert value == "Yes - requires sponsorship"
        assert derived is False

    def test_same_country_resolves_to_the_same_country_value(self):
        # The exact bug this phase fixes: a job in the candidate's own country
        # answers No, not whatever was saved last.
        value, derived = resolve_conditional(
            "visaStatus", _rule(), job_country="IN", profile_country="IN"
        )
        assert value == "No"
        assert derived is True

    def test_different_country_resolves_to_the_default(self):
        value, derived = resolve_conditional(
            "visaStatus", _rule(), job_country="US", profile_country="IN"
        )
        assert value == "Yes - requires sponsorship"
        assert derived is True

    def test_country_comparison_is_case_insensitive(self):
        value, _ = resolve_conditional(
            "visaStatus", _rule(), job_country="in", profile_country="IN"
        )
        assert value == "No"

    def test_unknown_job_country_falls_back_to_default_not_same_country_value(self):
        # The fallback-honesty guarantee (tasks.md 1.6): an unresolvable job
        # country must never silently pick the same-country ("no sponsorship
        # needed") answer.
        value, derived = resolve_conditional(
            "visaStatus", _rule(), job_country=None, profile_country="IN"
        )
        assert value == "Yes - requires sponsorship"
        assert derived is False

    def test_unknown_profile_country_also_falls_back_to_default(self):
        value, derived = resolve_conditional(
            "visaStatus", _rule(), job_country="US", profile_country=None
        )
        assert value == "Yes - requires sponsorship"
        assert derived is False

    def test_no_rule_configured_returns_blank_and_is_not_derived(self):
        value, derived = resolve_conditional(
            "visaStatus", None, job_country="US", profile_country="IN"
        )
        assert value == ""
        assert derived is False

    def test_accepts_a_plain_dict_as_well_as_the_model(self):
        raw = {"enabled": True, "default": "Yes", "same_country_value": "No"}
        value, derived = resolve_conditional(
            "workAuthorization", raw, job_country="IN", profile_country="IN"
        )
        assert value == "No"
        assert derived is True
