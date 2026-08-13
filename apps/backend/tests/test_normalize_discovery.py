"""Deterministic unit tests for discovery normalize + dedup (Wave 1).

Covers fingerprint stability, ``RawListing`` -> ``JobListing`` conversion, and
the "keep the richest record" dedup precedence.

Requirements: 6.1.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.job_discovery.normalize import fingerprint
from app.job_discovery.connectors.base import RawListing
from app.job_discovery.normalize import (
    dedupe,
    normalize,
    to_job_listing,
)

pytestmark = pytest.mark.unit


def _raw(**overrides) -> RawListing:
    base = {
        "source": "indeed",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Bengaluru, IN",
        "url": "https://example.com/jobs/1",
    }
    base.update(overrides)
    return RawListing(**base)


# --------------------------------------------------------------------------- #
# fingerprint
# --------------------------------------------------------------------------- #


def test_fingerprint_is_deterministic():
    a = fingerprint("Engineer", "Acme", "Pune", "https://x.test/1")
    b = fingerprint("Engineer", "Acme", "Pune", "https://x.test/1")
    assert a == b
    assert len(a) == 64  # sha256 hex digest


def test_fingerprint_ignores_case_and_whitespace():
    a = fingerprint(
        "Senior  Backend   Engineer", "Acme", "Bengaluru", "https://x.test/1"
    )
    b = fingerprint(
        "senior backend engineer", "  ACME ", " bengaluru ", "https://x.test/1"
    )
    assert a == b


def test_fingerprint_ignores_url_tracking_and_trailing_slash():
    a = fingerprint("E", "Acme", "Pune", "https://x.test/jobs/1")
    b = fingerprint(
        "E", "Acme", "Pune", "https://x.test/jobs/1/?utm_source=foo&ref=bar"
    )
    assert a == b


def test_fingerprint_distinguishes_different_postings():
    a = fingerprint("Engineer", "Acme", "Pune", "https://x.test/1")
    b = fingerprint("Engineer", "Globex", "Pune", "https://x.test/1")
    assert a != b


# --------------------------------------------------------------------------- #
# to_job_listing
# --------------------------------------------------------------------------- #


def test_to_job_listing_cleans_and_fingerprints():
    raw = _raw(
        title="  Senior   Backend  Engineer ",
        company=" Acme ",
        description="  Build   things. ",
    )
    listing = to_job_listing(raw)
    assert listing.title == "Senior Backend Engineer"
    assert listing.company == "Acme"
    assert listing.description == "Build things."
    assert listing.fingerprint == fingerprint(
        "Senior Backend Engineer",
        "Acme",
        "Bengaluru, IN",
        "https://example.com/jobs/1",
    )


def test_to_job_listing_coerces_missing_required_fields_to_empty():
    raw = RawListing(source="recipe", title="X", company=None, location=None, url=None)
    listing = to_job_listing(raw)
    assert listing.company == ""
    assert listing.location == ""
    assert listing.url == ""
    assert isinstance(listing.fingerprint, str) and listing.fingerprint


def test_to_job_listing_blank_optionals_become_none():
    raw = _raw(description="   ", salary="")
    listing = to_job_listing(raw)
    assert listing.description is None
    assert listing.salary is None


# --------------------------------------------------------------------------- #
# dedupe precedence
# --------------------------------------------------------------------------- #


def test_dedupe_keeps_record_with_longer_description():
    thin = to_job_listing(_raw(description="Short."))
    rich = to_job_listing(
        _raw(description="A much longer and more detailed description.")
    )
    out = dedupe([thin, rich])
    assert len(out) == 1
    assert out[0].description == "A much longer and more detailed description."


def test_dedupe_prefers_more_populated_fields_when_descriptions_tie():
    sparse = to_job_listing(_raw(description="Same.", salary=None, is_remote=None))
    full = to_job_listing(
        _raw(
            description="Same.",
            salary="\u20b940L",
            is_remote=True,
            posted_at=datetime.now(timezone.utc),
        )
    )
    out = dedupe([sparse, full])
    assert len(out) == 1
    assert out[0].salary == "\u20b940L"


def test_dedupe_tie_keeps_first_seen():
    first = to_job_listing(_raw(description="Same.", source="indeed"))
    second = to_job_listing(_raw(description="Same.", source="naukri"))
    out = dedupe([first, second])
    assert len(out) == 1
    assert out[0].source == "indeed"


def test_dedupe_preserves_distinct_listings_in_order():
    a = to_job_listing(_raw(company="Acme", url="https://x.test/a"))
    b = to_job_listing(_raw(company="Globex", url="https://x.test/b"))
    c = to_job_listing(_raw(company="Initech", url="https://x.test/c"))
    out = dedupe([a, b, c])
    assert [listing.company for listing in out] == ["Acme", "Globex", "Initech"]


def test_dedupe_collapses_across_url_variants():
    a = to_job_listing(_raw(url="https://x.test/jobs/1", description="short"))
    b = to_job_listing(
        _raw(url="https://x.test/jobs/1/?utm_source=x", description="a longer body")
    )
    assert a.fingerprint == b.fingerprint
    out = dedupe([a, b])
    assert len(out) == 1
    assert out[0].description == "a longer body"


# --------------------------------------------------------------------------- #
# canonical RawListing (connectors.base) contract
# --------------------------------------------------------------------------- #


def test_raw_listing_is_the_canonical_connector_type():
    # normalize must consume connectors.base.RawListing, not a private copy.
    from app.job_discovery.connectors.base import RawListing as CanonicalRaw

    assert RawListing is CanonicalRaw


def test_to_job_listing_accepts_partial_and_extra_fields():
    # partial rows carry no full JD body; extra is source-specific debug data.
    raw = _raw(description=None, partial=True, extra={"job_id": "abc"})
    listing = to_job_listing(raw)
    # partial-ness is preserved as an absent description (re-derived downstream
    # by the ranker), not as a dropped flag.
    assert listing.description is None
    assert listing.title == "Senior Backend Engineer"


def test_dedupe_full_listing_outranks_partial_duplicate():
    partial = to_job_listing(_raw(description=None, partial=True))
    full = to_job_listing(_raw(description="Full job description body.", partial=False))
    # Partial seen first, but the full duplicate must win.
    out = dedupe([partial, full])
    assert len(out) == 1
    assert out[0].description == "Full job description body."


# --------------------------------------------------------------------------- #
# normalize (end-to-end)
# --------------------------------------------------------------------------- #


def test_normalize_converts_and_dedupes():
    raws = [
        _raw(description="short"),
        _raw(description="a longer richer body for the same posting"),
        _raw(company="Globex", url="https://x.test/other"),
    ]
    out = normalize(raws)
    assert len(out) == 2
    by_company = {listing.company: listing for listing in out}
    assert (
        by_company["Acme"].description == "a longer richer body for the same posting"
    )
    assert "Globex" in by_company
