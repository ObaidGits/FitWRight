"""Grading for auto-apply-brain Phase 0.

The green / yellow / red grade an application gets is computed here, from
``brain_decisions`` rows, in code - never asked of a model and never estimated
from a single field in isolation. See .kiro/specs/auto-apply-brain/design.md
("The confidence gate") for why: a model rating its own confidence is exactly the
input this design does not trust.

Pure functions, no I/O, so they are unit-testable against hand-built decision
sets without a database.
"""
from __future__ import annotations

from typing import Any, Literal

Grade = Literal["green", "yellow", "red"]

# Sources trusted enough to count toward green. Anything else (a first-time brain
# classification, a brain-drafted answer) can still fill the field, but the
# application it belongs to cannot grade green - see design.md's gate table.
TRUSTED_SOURCES = frozenset(
    {"exact_rule", "cached_classification", "user_answer", "derived_rule"}
)


def grade_decision(decision: dict[str, Any]) -> Grade:
    """Grade one field's decision in isolation.

    A single decision's grade feeds ``grade_application`` (the worst decision
    wins), and is also stored on the row itself (``grade_contribution``) so a
    field can be inspected without recomputing the whole application.
    """
    if not decision.get("filled", False):
        # An unfilled *required* field is red; an unfilled optional one is
        # yellow. This function does not know which - the caller passes that
        # in via `required`, defaulting to the conservative case.
        return "red" if decision.get("required", True) else "yellow"

    if decision.get("readback_ok") is False:
        return "red"

    if decision.get("is_knockout", False) and decision.get("value_source") not in TRUSTED_SOURCES:
        # A knockout question answered by anything other than a trusted source
        # is never trusted, however high its stated confidence - R1.4.
        return "red"

    if decision.get("value_source") not in TRUSTED_SOURCES:
        return "yellow"

    return "green"


def grade_application(
    decisions: list[dict[str, Any]],
    *,
    resume_attached: bool = True,
    stopped: bool = False,
) -> Grade:
    """Grade a whole application: the worst field's grade wins.

    One red field makes the application red even if the other 19 are perfect -
    an application is submitted as one unit, and a single wrong knockout answer
    is exactly as damaging as twenty.
    """
    if stopped or not resume_attached:
        return "red"
    if not decisions:
        # Nothing to grade is not the same as nothing wrong; treat as yellow so
        # an empty decision set never silently qualifies for auto-submit.
        return "yellow"

    grades = {grade_decision(d) for d in decisions}
    if "red" in grades:
        return "red"
    if "yellow" in grades:
        return "yellow"
    return "green"


def held_reasons(decisions: list[dict[str, Any]]) -> list[str]:
    """Root causes for why an application is not green, for the batch report.

    Grouped by cause rather than listed per field, so twenty held applications
    that all hit the same unrecognised question surface as one thing to fix.
    """
    reasons: list[str] = []
    seen: set[str] = set()

    def add(reason: str) -> None:
        if reason not in seen:
            seen.add(reason)
            reasons.append(reason)

    for decision in decisions:
        grade = grade_decision(decision)
        if grade == "green":
            continue
        label = decision.get("label") or decision.get("label_normalized") or "a field"
        if not decision.get("filled", False):
            add(f"Needs an answer: {label}")
        elif decision.get("readback_ok") is False:
            add(f"Could not confirm the value stuck: {label}")
        elif decision.get("is_knockout", False):
            add(f"Screening question needs review: {label}")
        elif decision.get("value_source") == "brain_draft":
            add(f"AI-drafted answer needs review: {label}")
        else:
            add(f"New question, seen for the first time: {label}")
    return reasons
