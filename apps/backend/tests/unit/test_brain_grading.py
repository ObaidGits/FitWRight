"""Unit tests for app.brain_grading — pure functions, no database.

Auto-apply-brain Phase 0 (.kiro/specs/auto-apply-brain/). Grading is computed
from decision rows in code, never asked of a model, so these tests pin the rule
table directly against hand-built decision sets.
"""
from __future__ import annotations

from app.brain_grading import grade_application, grade_decision, held_reasons


def _decision(**overrides):
    base = {
        "label": "Field",
        "label_normalized": "field",
        "value_source": "exact_rule",
        "confidence": 1.0,
        "is_knockout": False,
        "filled": True,
        "readback_ok": True,
        "required": True,
    }
    base.update(overrides)
    return base


class TestGradeDecision:
    def test_trusted_source_filled_and_verified_is_green(self):
        assert grade_decision(_decision()) == "green"

    def test_unfilled_required_field_is_red(self):
        assert grade_decision(_decision(filled=False, required=True)) == "red"

    def test_unfilled_optional_field_is_yellow(self):
        assert grade_decision(_decision(filled=False, required=False)) == "yellow"

    def test_failed_readback_is_red_even_from_a_trusted_source(self):
        assert grade_decision(_decision(readback_ok=False)) == "red"

    def test_first_time_brain_classification_is_yellow(self):
        assert grade_decision(_decision(value_source="brain_classification")) == "yellow"

    def test_brain_draft_is_yellow(self):
        assert grade_decision(_decision(value_source="brain_draft")) == "yellow"

    def test_knockout_from_a_trusted_source_is_green(self):
        assert grade_decision(_decision(is_knockout=True, value_source="user_answer")) == "green"

    def test_knockout_from_an_untrusted_source_is_red_not_yellow(self):
        # This is R1.4 and the whole reason grading is not a simple confidence
        # threshold: a fresh classification on a knockout question is never
        # trusted, however high its stated confidence.
        decision = _decision(is_knockout=True, value_source="brain_classification", confidence=0.99)
        assert grade_decision(decision) == "red"

    def test_cached_classification_counts_as_trusted(self):
        assert grade_decision(_decision(value_source="cached_classification")) == "green"

    def test_derived_rule_counts_as_trusted(self):
        assert grade_decision(_decision(value_source="derived_rule")) == "green"


class TestGradeApplication:
    def test_all_green_decisions_grade_green(self):
        decisions = [_decision(), _decision(label="Other")]
        assert grade_application(decisions) == "green"

    def test_one_red_decision_fails_the_whole_application(self):
        # The load-bearing property: an application is submitted as one unit, so
        # one wrong knockout answer is as damaging as twenty.
        decisions = [_decision(), _decision(filled=False, required=True)]
        assert grade_application(decisions) == "red"

    def test_one_yellow_among_greens_grades_yellow(self):
        decisions = [_decision(), _decision(value_source="brain_draft")]
        assert grade_application(decisions) == "yellow"

    def test_red_outranks_yellow(self):
        decisions = [
            _decision(value_source="brain_draft"),
            _decision(filled=False, required=True),
        ]
        assert grade_application(decisions) == "red"

    def test_missing_resume_is_red_regardless_of_fields(self):
        assert grade_application([_decision()], resume_attached=False) == "red"

    def test_a_stop_condition_is_red_regardless_of_fields(self):
        assert grade_application([_decision()], stopped=True) == "red"

    def test_empty_decision_set_is_yellow_not_green(self):
        # Nothing to grade must never silently qualify for auto-submit.
        assert grade_application([]) == "yellow"


class TestHeldReasons:
    def test_green_application_has_no_reasons(self):
        assert held_reasons([_decision()]) == []

    def test_groups_identical_causes_into_one_reason(self):
        decisions = [
            _decision(label="A", filled=False, required=True),
            _decision(label="B", filled=False, required=True),
        ]
        reasons = held_reasons(decisions)
        assert len(reasons) == 2  # one per distinct label, not deduplicated away
        assert all("Needs an answer" in r for r in reasons)

    def test_knockout_reason_is_distinguishable_from_a_plain_miss(self):
        decisions = [_decision(is_knockout=True, value_source="brain_classification")]
        reasons = held_reasons(decisions)
        assert any("Screening question" in r for r in reasons)

    def test_readback_failure_has_its_own_reason(self):
        decisions = [_decision(readback_ok=False)]
        reasons = held_reasons(decisions)
        assert any("confirm the value stuck" in r for r in reasons)
