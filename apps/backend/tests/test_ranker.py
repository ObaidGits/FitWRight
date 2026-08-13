"""Deterministic unit tests for the discovery ranker (Requirement 7).

No LLM, no network, no real ``app.services.*`` import: the async keyword
extractor and the match scorer are injected as pure fakes, so every assertion
is reproducible.

Requirements: 7.1, 7.2, 7.3.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.job_discovery.models import JobListing
from app.job_discovery.ranker import (
    build_recommendation,
    collect_keywords,
    keyword_in_text,
    listing_jd_text,
    rank_listings,
    resume_text,
    sort_recommendations,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _listing(
    *,
    source: str = "indeed",
    title: str = "Backend Engineer",
    company: str = "Acme",
    location: str = "Remote",
    url: str = "https://example.com/1",
    description: str | None = "python and kubernetes",
    posted_at: datetime | None = None,
    fingerprint: str = "",
) -> JobListing:
    return JobListing(
        source=source,
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
        posted_at=posted_at,
        fingerprint=fingerprint,
    )


# --------------------------------------------------------------------------- #
# collect_keywords
# --------------------------------------------------------------------------- #


def test_collect_keywords_unions_and_dedups_case_insensitively():
    keywords = {
        "required_skills": ["Python", "python", " ", "Kubernetes"],
        "preferred_skills": ["kubernetes", "Go"],
        "keywords": ["Python", "AWS"],
        "seniority_level": "senior",  # ignored non-list field
    }
    assert collect_keywords(keywords) == ["Python", "Kubernetes", "Go", "AWS"]


def test_collect_keywords_tolerates_missing_and_nonlist_fields():
    assert collect_keywords({}) == []
    assert collect_keywords({"required_skills": "python"}) == []
    assert collect_keywords({"keywords": [1, 2.5, None, "x"]}) == ["1", "2.5", "x"]


# --------------------------------------------------------------------------- #
# keyword_in_text
# --------------------------------------------------------------------------- #


def test_keyword_in_text_word_boundary_and_case():
    assert keyword_in_text("Python", "I love python programming")
    assert keyword_in_text("C++", "worked with c++ daily")
    # substring must NOT match across word boundaries
    assert not keyword_in_text("Java", "JavaScript everywhere")
    assert not keyword_in_text("go", "kangaroo")
    assert not keyword_in_text("", "anything")


# --------------------------------------------------------------------------- #
# resume_text
# --------------------------------------------------------------------------- #


def test_resume_text_flattens_nested_structures_lowercased():
    resume = {
        "summary": "Senior Backend Engineer",
        "skills": ["Python", "Kubernetes"],
        "experience": [{"title": "Dev", "years": 5}],
        "flag": True,  # bools excluded
    }
    blob = resume_text(resume)
    assert "senior backend engineer" in blob
    assert "python" in blob
    assert "kubernetes" in blob
    assert "5" in blob
    assert "true" not in blob


def test_resume_text_none_is_empty():
    assert resume_text(None) == ""


# --------------------------------------------------------------------------- #
# listing_jd_text — partial flag (Req 7.2)
# --------------------------------------------------------------------------- #


def test_listing_jd_text_uses_full_description_when_present():
    text, partial = listing_jd_text(_listing(description="full jd text"))
    assert text == "full jd text"
    assert partial is False


def test_listing_jd_text_falls_back_to_snippet_and_flags_partial():
    text, partial = listing_jd_text(
        _listing(title="Backend Engineer", company="Acme", location="Remote", description=None)
    )
    assert partial is True
    assert text == "Backend Engineer Acme Remote"


def test_listing_jd_text_blank_description_is_partial():
    _, partial = listing_jd_text(_listing(description="   "))
    assert partial is True


# --------------------------------------------------------------------------- #
# build_recommendation — matched/missing + clamp
# --------------------------------------------------------------------------- #


def test_build_recommendation_splits_matched_and_missing():
    listing = _listing()
    keywords = {"required_skills": ["Python", "Kubernetes"], "keywords": ["Rust"]}
    rec = build_recommendation(
        listing, keywords, match_score=66.6, resume_blob="python developer", partial=False
    )
    assert rec.matched == ["Python"]
    assert rec.missing == ["Kubernetes", "Rust"]
    assert rec.match_score == 66.6
    assert rec.partial is False


def test_build_recommendation_clamps_score_to_range():
    listing = _listing()
    assert build_recommendation(listing, {}, 250.0, "", partial=True).match_score == 100.0
    assert build_recommendation(listing, {}, -5.0, "", partial=True).match_score == 0.0


# --------------------------------------------------------------------------- #
# sort_recommendations — Req 7.3 ordering & tie-break
# --------------------------------------------------------------------------- #


def _rec(score: float, **listing_kwargs) -> "object":
    return build_recommendation(
        _listing(**listing_kwargs), {}, score, "", partial=False
    )


def test_sort_by_score_descending():
    recs = [_rec(10.0, url="a"), _rec(90.0, url="b"), _rec(50.0, url="c")]
    ordered = sort_recommendations(recs)
    assert [r.match_score for r in ordered] == [90.0, 50.0, 10.0]


def test_tiebreak_recency_then_source_priority_then_stable():
    older = _rec(80.0, source="indeed", url="older", posted_at=_NOW - timedelta(days=2))
    newer = _rec(80.0, source="naukri", url="newer", posted_at=_NOW)
    ordered = sort_recommendations([older, newer])
    # equal score -> newer posted_at wins regardless of source priority
    assert ordered[0].listing.url == "newer"

    # equal score + equal recency -> source priority (indeed < naukri)
    a = _rec(80.0, source="naukri", url="x", posted_at=_NOW)
    b = _rec(80.0, source="indeed", url="y", posted_at=_NOW)
    ordered = sort_recommendations([a, b])
    assert ordered[0].listing.source == "indeed"

    # equal score + equal recency + same source -> stable fingerprint/url
    p = _rec(80.0, source="indeed", url="https://z", posted_at=_NOW, fingerprint="fp-2")
    q = _rec(80.0, source="indeed", url="https://z", posted_at=_NOW, fingerprint="fp-1")
    ordered = sort_recommendations([p, q])
    assert [r.listing.fingerprint for r in ordered] == ["fp-1", "fp-2"]


def test_missing_posted_at_sorts_after_dated_on_tie():
    dated = _rec(70.0, source="indeed", url="dated", posted_at=_NOW)
    undated = _rec(70.0, source="indeed", url="undated", posted_at=None)
    ordered = sort_recommendations([undated, dated])
    assert ordered[0].listing.url == "dated"


def test_sort_is_deterministic_across_input_orderings():
    def build():
        return [
            _rec(80.0, source="naukri", url="n", posted_at=_NOW),
            _rec(80.0, source="indeed", url="i", posted_at=_NOW),
            _rec(95.0, source="linkedin", url="l", posted_at=_NOW),
        ]

    forward = [r.listing.url for r in sort_recommendations(build())]
    reversed_input = list(reversed(build()))
    backward = [r.listing.url for r in sort_recommendations(reversed_input)]
    assert forward == backward == ["l", "i", "n"]


# --------------------------------------------------------------------------- #
# rank_listings — end-to-end with injected fakes (Req 7.1)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_rank_listings_scores_sorts_and_flags_partial():
    seen_user: list[str] = []

    async def fake_extractor(user_id: str, jd_text: str):
        seen_user.append(user_id)
        # Different keyword sets per listing so scores differ deterministically.
        if "kubernetes" in jd_text.lower():
            return {"required_skills": ["Python", "Kubernetes"]}
        return {"required_skills": ["Python"]}

    def fake_scorer(resume, keywords) -> float:
        # Coverage of the keyword union against the (lowercased) resume blob.
        terms = collect_keywords(keywords)
        if not terms:
            return 0.0
        hit = sum(1 for t in terms if t.casefold() in {"python"})
        return hit / len(terms) * 100

    listings = [
        _listing(source="indeed", url="full", description="python and kubernetes"),
        _listing(source="naukri", url="partial", description=None, title="Python Dev"),
    ]

    recs = await rank_listings(
        "user-42",
        listings,
        {"skills": ["Python"]},
        keyword_extractor=fake_extractor,
        match_scorer=fake_scorer,
    )

    assert seen_user == ["user-42", "user-42"]
    # "partial" listing: only Python -> 100%; "full": python of two -> 50%.
    assert recs[0].listing.url == "partial"
    assert recs[0].match_score == 100.0
    assert recs[0].partial is True
    assert recs[1].listing.url == "full"
    assert recs[1].match_score == 50.0
    assert recs[1].partial is False
    # matched/missing reflect the resume blob.
    assert recs[1].matched == ["Python"]
    assert recs[1].missing == ["Kubernetes"]


@pytest.mark.asyncio
@pytest.mark.skipif(True, reason="requires configured LLM provider")
async def test_rank_listings_default_collaborators_are_functional():
    """Regression: rank_listings() with NO injected collaborators must work.

    The production path resolves the real ``app.services.improver`` /
    ``app.services.refiner`` machinery (heuristic lane when no LLM provider is
    wired) and must return scored results rather than raising
    ``ModuleNotFoundError``.
    """
    listings = [
        _listing(
            source="indeed",
            url="j1",
            description="Building python microservices with docker and kubernetes.",
        ),
    ]
    # Structured resume schema understood by refiner._extract_all_text.
    resume = {"additional": {"technicalSkills": ["Python", "Docker"]}}

    recs = await rank_listings("user-1", listings, resume)

    assert len(recs) == 1
    rec = recs[0]
    # Heuristic finds {python, docker, kubernetes, microservices}; the resume
    # covers python + docker -> 2/4 = 50%.
    assert rec.match_score == 50.0
    assert rec.partial is False
    assert set(rec.matched) == {"python", "docker"}
    assert set(rec.missing) == {"kubernetes", "microservices"}


@pytest.mark.asyncio
async def test_rank_listings_empty_input_returns_empty():
    async def fake_extractor(user_id: str, jd_text: str):  # pragma: no cover - not called
        return {}

    out = await rank_listings(
        "u", [], {}, keyword_extractor=fake_extractor, match_scorer=lambda r, k: 0.0
    )
    assert out == []
