"""The autofill profile's sourcing rules.

Phase 1 moved this endpoint from "derived from the resume" to "Profile first,
resume as fallback". These tests pin the three rules that matter:

1. A curated Profile value always beats a resume-derived one. The user edits
   Profile deliberately; the resume is parsed text.
2. The resume still fills gaps, so a fresh account is useful before anyone has
   opened the Profile page.
3. Eligibility answers are never inferred. A wrong visa status or salary
   auto-rejects an application, so unknown must stay blank.

Plus a contract test that the previously shipped field names all survive, since
an already-installed extension build reads this response.
"""
import pytest

from app.routers.extension import AutofillProfile, get_autofill_profile

USER = "user-1"


class FakeDb:
    """Minimal stand-in exposing only what the endpoint touches."""

    def __init__(self, profile_data=None, resume=None):
        self._profile = {"data": profile_data} if profile_data is not None else None
        self._resume = resume

    async def get_profile(self, user_id):
        return self._profile

    # `_resolve_resume` reads through these two.
    async def get_resume(self, user_id, resume_id):
        return self._resume

    async def list_resumes(self, user_id, **kwargs):
        return [self._resume] if self._resume else []

    async def get_master_resume(self, user_id):
        return self._resume


RESUME = {
    "resume_id": "r-1",
    "filename": "cv.pdf",
    "processed_data": {
        "personal_info": {
            "name": "Resume Name",
            "email": "resume@example.test",
            "phone": "111",
            "location": "Resume City",
            "linkedin": "resume-li",
        },
        "experience": [{"title": "Resume Title", "company": "Resume Co"}],
    },
}

PROFILE = {
    "identity": {
        "name": "Profile Name",
        "email": "profile@example.test",
        "location": "Pune, India",
        "currentRole": "Staff Engineer",
        "currentCompany": "Profile Co",
        "yearsExperience": 7,
        "workAuthorization": "Indian citizen",
        "visaStatus": "Not required",
        "noticePeriod": "30 days",
        "salaryExpectation": "40 LPA",
        "relocation": True,
        "availability": "1_month",
        "remotePreference": "hybrid",
        "address": {
            "line1": "12 MG Road",
            "line2": "Flat 4",
            "city": "Pune",
            "state": "Maharashtra",
            "postalCode": "411001",
            "country": "India",
        },
    },
    "education": [{"institution": "IIT Bombay", "degree": "B.Tech CSE", "years": "2014-2018"}],
    "workExperience": [{"title": "Ignored", "company": "Ignored Co", "current": False}],
}


async def build(profile_data=None, resume=None):
    return await get_autofill_profile(
        resume_id=None, user_id=USER, db=FakeDb(profile_data, resume)
    )


class TestPrecedence:
    async def test_profile_wins_over_resume(self):
        result = await build(PROFILE, RESUME)
        assert result.full_name == "Profile Name"
        assert result.email == "profile@example.test"
        assert result.location == "Pune, India"
        assert result.current_title == "Staff Engineer"
        assert result.current_company == "Profile Co"
        assert result.years_experience == 7

    async def test_resume_fills_gaps_profile_left_empty(self):
        """Phone and LinkedIn are absent from this Profile, so the resume shows."""
        result = await build(PROFILE, RESUME)
        assert result.phone == "111"
        assert result.linkedin == "resume-li"

    async def test_resume_only_account_still_works(self):
        result = await build(None, RESUME)
        assert result.full_name == "Resume Name"
        assert result.first_name == "Resume"
        assert result.last_name == "Name"
        assert result.current_title == "Resume Title"

    async def test_profile_only_account_still_works(self):
        result = await build(PROFILE, None)
        assert result.full_name == "Profile Name"
        assert result.resume_id is None
        assert result.resume_pdf_path is None

    async def test_empty_everything_is_not_an_error(self):
        result = await build(None, None)
        assert result == AutofillProfile()


class TestStructuredAddress:
    async def test_address_parts_round_trip(self):
        result = await build(PROFILE, RESUME)
        assert result.address_line1 == "12 MG Road"
        assert result.address_line2 == "Flat 4"
        assert result.city == "Pune"
        assert result.state == "Maharashtra"
        assert result.postal_code == "411001"
        assert result.country == "India"

    async def test_no_address_leaves_parts_blank_not_guessed(self):
        """"Resume City" must not be smeared across city/state/country."""
        result = await build({"identity": {"location": "Resume City"}}, RESUME)
        assert (result.city, result.state, result.postal_code, result.country) == ("", "", "", "")


class TestKnockoutAnswers:
    async def test_eligibility_comes_from_profile(self):
        result = await build(PROFILE, RESUME)
        assert result.work_authorization == "Indian citizen"
        assert result.visa_status == "Not required"
        assert result.notice_period == "30 days"
        assert result.salary_expectation == "40 LPA"
        assert result.willing_to_relocate is True
        assert result.availability == "1_month"
        assert result.remote_preference == "hybrid"

    async def test_unknown_eligibility_stays_blank(self):
        """A blank field is correct; a guessed one auto-rejects the application."""
        result = await build(None, RESUME)
        assert result.work_authorization == ""
        assert result.visa_status == ""
        assert result.notice_period == ""
        assert result.salary_expectation == ""
        assert result.willing_to_relocate is None

    async def test_relocation_absent_is_none_not_false(self):
        """None means unanswered; False means "no" and would be a real answer."""
        result = await build({"identity": {"name": "X"}}, None)
        assert result.willing_to_relocate is None


class TestEducation:
    async def test_highest_education_exposed(self):
        result = await build(PROFILE, RESUME)
        assert result.highest_degree == "B.Tech CSE"
        assert result.highest_institution == "IIT Bombay"
        assert result.education_years == "2014-2018"


class TestYearsOfExperience:
    """Years is the one number that is Profile-first but resume-derivable.

    It sits outside the eligibility block on purpose: it is computed from
    experience dates rather than guessed, so an estimate beats a blank when a
    filter asks for "minimum 5 years". The Profile value still wins outright.
    """

    async def test_profile_value_wins(self):
        result = await build(PROFILE, RESUME)
        assert result.years_experience == 7

    async def test_resume_only_account_does_not_crash_and_stays_none_or_number(self):
        result = await build(None, RESUME)
        assert result.years_experience is None or isinstance(result.years_experience, float)

    async def test_profile_zero_is_respected_not_treated_as_missing(self):
        """A genuine 0 (career starter) must not fall through to the resume."""
        result = await build({"identity": {"name": "X", "yearsExperience": 0}}, RESUME)
        assert result.years_experience == 0


class TestKnownGaps:
    """Documented gaps handed to Phase 2 rather than papered over."""

    async def test_sponsorship_has_no_profile_source_yet(self):
        """`requiresSponsorship` is asked as its own question and has no Profile
        field. Deriving it from visaStatus would be exactly the inference the
        eligibility rule forbids, so it stays absent here and remains a
        local-only answer until the Phase 2 field registry can hold it.
        """
        result = await build(PROFILE, RESUME)
        assert not hasattr(result, "requires_sponsorship")


class TestBackwardsCompatibility:
    def test_previously_shipped_fields_all_survive(self):
        """An installed extension build reads these names; none may disappear."""
        shipped = {
            "full_name", "first_name", "last_name", "email", "phone", "location",
            "linkedin", "github", "website", "current_title", "current_company",
            "years_experience", "resume_id", "resume_filename", "resume_pdf_path",
            "preferences",
        }
        assert shipped <= set(AutofillProfile.model_fields)

    async def test_current_role_prefers_profile_flagged_current(self):
        """With no explicit currentRole, the entry flagged `current` wins."""
        data = {
            "identity": {"name": "X"},
            "workExperience": [
                {"title": "Older", "company": "Old Co", "current": False},
                {"title": "Now", "company": "New Co", "current": True},
            ],
        }
        result = await build(data, RESUME)
        assert result.current_title == "Now"
        assert result.current_company == "New Co"
