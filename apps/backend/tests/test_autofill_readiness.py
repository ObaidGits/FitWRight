"""Autofill readiness and the answers badge count.

Readiness exists to answer one question honestly: how much of a real application
form can be filled without the user typing? So the rules worth pinning are the
ones that stop it from flattering the user -

* it reads the *same* builder the extension fills from, so a field the extension
  would leave blank can never be reported as covered;
* `willing_to_relocate` is a tri-state - a stored "no" is an answer, an absent
  one is not, and a naive truthiness check would call "no" a gap forever;
* eligibility gaps are reported as their own group, because those are the ones a
  user retypes on every single form (they are never inferred from a resume).

Uses the same fake-database convention as `test_extension_autofill_profile.py`:
these are rules about assembling a payload, not about SQL.
"""
from app.routers.application_fields import READINESS_FIELDS, autofill_readiness, field_summary

USER = "user-1"


class FakeDb:
    """Minimal stand-in: the reads these endpoints make.

    The resume lookups exist because readiness reuses the autofill builder, which
    falls back to a resume - that reuse is the point, so the fake has to satisfy
    it rather than route around it.
    """

    def __init__(self, profile_data=None, fields=None, resume=None):
        self._profile = {"data": profile_data} if profile_data is not None else None
        self._fields = fields or []
        self._resume = resume

    async def get_profile(self, user_id):
        return self._profile

    async def get_resume(self, user_id, resume_id):
        return self._resume

    async def list_resumes(self, user_id, **kwargs):
        return [self._resume] if self._resume else []

    async def get_master_resume(self, user_id):
        return self._resume

    async def list_application_fields(self, user_id, *, status=None):
        if status:
            return [f for f in self._fields if f["status"] == status]
        return list(self._fields)


async def readiness(profile_data=None):
    return await autofill_readiness(user_id=USER, db=FakeDb(profile_data))


class TestReadinessFieldList:
    def test_every_key_exists_on_the_autofill_profile(self):
        """A typo here would report a real answer as missing forever."""
        from app.routers.extension import AutofillProfile

        fields = set(AutofillProfile.model_fields)
        for key, _label, _group in READINESS_FIELDS:
            assert key in fields, f"{key} is not an AutofillProfile field"

    def test_groups_are_known(self):
        for _key, _label, group in READINESS_FIELDS:
            assert group in {"essential", "common", "eligibility"}

    def test_every_field_has_a_human_label(self):
        for key, label, _group in READINESS_FIELDS:
            assert label.strip(), f"{key} has no label"
            assert label != key, f"{key} needs a human label, not its key"

    def test_eligibility_fields_are_the_never_inferred_ones(self):
        """Eligibility is defined by consequence, not by taste. Keep it aligned
        with the knockout list the rest of the module refuses to guess."""
        eligibility = {key for key, _l, group in READINESS_FIELDS if group == "eligibility"}
        assert {"work_authorization", "visa_status", "salary_expectation"} <= eligibility


class TestReadinessReporting:
    async def test_empty_profile_reports_everything_missing(self):
        result = await readiness(None)
        assert result.covered == 0
        assert result.total == len(READINESS_FIELDS)
        assert len(result.missing) == len(READINESS_FIELDS)
        assert result.has_resume is False

    async def test_answers_reduce_the_gap(self):
        result = await readiness(
            {
                "identity": {
                    "name": "Ada Lovelace",
                    "email": "ada@example.test",
                    "phone": "+15550100",
                    "address": {"city": "London", "country": "UK"},
                }
            }
        )
        missing = {f.label for f in result.missing}
        assert "Your name" not in missing
        assert "Email address" not in missing
        assert "City" not in missing
        assert "Country" not in missing
        assert result.covered == 5

    async def test_stored_no_counts_as_an_answer(self):
        """`relocation: False` is an answer, not an empty field."""
        result = await readiness({"identity": {"relocation": False}})
        assert "Willing to relocate" not in {f.label for f in result.missing}
        assert result.covered == 1

    async def test_absent_relocation_is_still_a_gap(self):
        result = await readiness({"identity": {"name": "Ada"}})
        assert "Willing to relocate" in {f.label for f in result.missing}

    async def test_blank_string_is_not_an_answer(self):
        """A Profile field cleared to whitespace must not count as filled."""
        result = await readiness({"identity": {"name": "   ", "email": ""}})
        missing = {f.label for f in result.missing}
        assert "Your name" in missing
        assert "Email address" in missing
        assert result.covered == 0

    async def test_eligibility_gaps_are_grouped_as_such(self):
        result = await readiness(None)
        eligibility = {f.label for f in result.missing if f.group == "eligibility"}
        assert "Work authorization" in eligibility
        assert "Salary expectation" in eligibility
        # Contact details are not eligibility - a resume can supply them.
        assert "Email address" not in eligibility

    async def test_counts_never_exceed_the_total(self):
        result = await readiness({"identity": {"name": "Ada"}})
        assert result.covered + len(result.missing) == result.total


class TestFieldSummary:
    async def test_counts_split_by_status(self):
        db = FakeDb(
            fields=[
                {"id": "1", "status": "needs_answer"},
                {"id": "2", "status": "answered"},
                {"id": "3", "status": "answered"},
            ]
        )
        summary = await field_summary(user_id=USER, db=db)
        assert summary.needs_answer == 1
        assert summary.answered == 2
        assert summary.total == 3

    async def test_empty_registry_is_all_zeroes(self):
        summary = await field_summary(user_id=USER, db=FakeDb())
        assert (summary.needs_answer, summary.answered, summary.total) == (0, 0, 0)

    async def test_ignored_fields_are_not_counted_as_needing_an_answer(self):
        """A question the user chose to ignore must not keep nagging in the badge."""
        db = FakeDb(fields=[{"id": "1", "status": "ignored"}])
        summary = await field_summary(user_id=USER, db=db)
        assert summary.needs_answer == 0
        assert summary.total == 1
