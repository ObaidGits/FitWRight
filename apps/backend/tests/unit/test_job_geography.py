"""Unit tests for app.job_geography — pure string matching, no I/O."""
from __future__ import annotations

from app.job_geography import country_from_location


class TestCountryFromLocation:
    def test_trailing_country_name(self):
        assert country_from_location("Pune, India") == "IN"
        assert country_from_location("London, UK") == "GB"
        assert country_from_location("New York, United States") == "US"

    def test_multi_part_city_state_country(self):
        assert country_from_location("Pune, Maharashtra, India") == "IN"

    def test_remote_with_country_qualifier(self):
        assert country_from_location("Remote - US") == "US"
        assert country_from_location("Remote (India)") == "IN"

    def test_bare_remote_is_unresolvable(self):
        # No country qualifier at all - must not guess, per the module
        # docstring: "Remote" alone says nothing about which country's
        # sponsorship rules apply.
        assert country_from_location("Remote") is None
        assert country_from_location("Anywhere") is None
        assert country_from_location("Worldwide") is None

    def test_us_state_abbreviation_resolves_to_us(self):
        assert country_from_location("Austin, TX") == "US"

    def test_indian_state_abbreviation_resolves_to_in(self):
        assert country_from_location("Pune, MH") == "IN"

    def test_blank_or_missing_is_unresolvable(self):
        assert country_from_location("") is None
        assert country_from_location(None) is None
        assert country_from_location("   ") is None

    def test_unrecognised_location_is_unresolvable_not_a_guess(self):
        assert country_from_location("Somewhere, Nowhereland") is None

    def test_case_insensitive(self):
        assert country_from_location("pune, INDIA") == "IN"
        assert country_from_location("Berlin, GERMANY") == "DE"
