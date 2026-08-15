"""Country-conditional eligibility answers (auto-apply-brain Phase 1).

Fixes the concrete bug this phase exists for: ``identity.visaStatus`` (and its
three siblings) was one flat string, filled identically on every application
regardless of where the job actually is. "Do you need sponsorship?" must answer
No for a job in the candidate's own country and Yes elsewhere - one saved
answer cannot express that, a rule can.

Deliberately NOT a rule builder or an expression language (see design.md,
"Deliberately not in this design"): exactly four fields, each with exactly two
values - a default and a same-country override. The UI question is one
checkbox: "this answer depends on the job's country".
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# The only fields this applies to. Adding a fifth is a deliberate product
# decision, not something a caller can do by passing a new key - see the
# module docstring on why this stays a closed, tiny set.
ConditionalField = Literal[
    "visaStatus", "workAuthorization", "relocation", "salaryExpectation"
]

CONDITIONAL_FIELDS: tuple[ConditionalField, ...] = (
    "visaStatus",
    "workAuthorization",
    "relocation",
    "salaryExpectation",
)


class ConditionalAnswer(BaseModel):
    """One field's country-conditional rule, as stored in the profile document.

    ``enabled=False`` (the default) means the field behaves exactly as before
    this phase shipped: one flat value, from ``default``. Existing profiles
    with no rule configured therefore need no migration and no data change -
    the flat string an existing user already saved becomes this rule's
    ``default`` for free the first time the Answers page reads it.
    """

    enabled: bool = False
    default: str = ""
    same_country_value: str = ""


def resolve_conditional(
    field: ConditionalField,
    rule: ConditionalAnswer | dict[str, Any] | None,
    *,
    job_country: str | None,
    profile_country: str | None,
) -> tuple[str, bool]:
    """Resolve one eligibility field for the job currently being filled.

    Returns ``(value, is_derived)``. ``is_derived`` is False whenever the rule
    is off or a country could not be determined - in both cases the caller is
    using the plain stored value, not a computed one, and the value_source
    reported to the decision trail must say so (``exact_rule``, not
    ``derived_rule`` - see app.brain_grading and R2.3/R1.4).

    Deliberately pure and synchronous: same input always yields the same
    output, so this is unit-testable without a database or a job posting.
    """
    if rule is None:
        return "", False
    if isinstance(rule, dict):
        rule = ConditionalAnswer(**rule)

    if not rule.enabled:
        return rule.default, False

    if not job_country or not profile_country:
        # Fallback honesty (tasks.md 1.6): when the job's country cannot be
        # determined, the DEFAULT is used - never the same-country value, which
        # would silently claim "no sponsorship needed" for a job whose country
        # is simply unknown. is_derived stays False, which is what makes the
        # caller grade this yellow rather than green: a guessed knockout answer
        # must never look as trustworthy as a computed one.
        return rule.default, False

    if job_country.strip().upper() == profile_country.strip().upper():
        return rule.same_country_value, True

    return rule.default, True
