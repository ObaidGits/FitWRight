"""Rank job listings against a resume (Job Discovery — Requirement 7).

This is a thin orchestration layer over the *existing* fit machinery that
``routers/jobs.py::analyze_job`` already uses:

* :func:`app.services.improver.extract_job_keywords_cached` — content-addressed
  LLM keyword extraction (identical JDs reuse the cached breakdown).
* :func:`app.services.refiner.calculate_keyword_match` — deterministic keyword
  coverage score in ``0..100``.

For each :class:`~app.job_discovery.models.JobListing` we extract JD keywords,
score them against the resume's processed data, attach the matched/missing
keyword sets, and flag listings scored without a full description as
``partial`` (Req 7.2). Results are sorted by score descending with a fully
deterministic tie-break: recency, then source priority, then a stable
fingerprint/url fallback (Req 7.3).

The heavy dependencies (``app.services.*``, which pull in the LLM + cache
stack) are imported lazily *inside* :func:`rank_listings` so importing this
module is cheap and so unit tests can inject deterministic fakes without ever
touching the real LLM. This mirrors the local-import pattern
``extract_job_keywords_cached`` itself uses.

Design reference: ``.kiro/specs/job-discovery/design.md`` §8 (ranking).
Requirements: 7.1, 7.2, 7.3.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.job_discovery.models import JobListing, Recommendation

# --------------------------------------------------------------------------- #
# Injectable collaborator types
# --------------------------------------------------------------------------- #

# ``extract_job_keywords_cached(user_id, job_description) -> keyword dict``
KeywordExtractor = Callable[[str, str], Awaitable[Mapping[str, Any]]]
# ``calculate_keyword_match(resume_processed, jd_keywords) -> 0..100``
MatchScorer = Callable[[Mapping[str, Any], Mapping[str, Any]], float]

# JD-keyword fields, in coverage-priority order, that make up the match set.
# Mirrors ``services/refiner.calculate_keyword_match`` and ``analyze_job``.
_KEYWORD_FIELDS = ("required_skills", "preferred_skills", "keywords")

# Source tie-break priority (lower = ranked first on an exact score+recency
# tie). Fixed job boards come before custom site recipes; anything unknown
# (recipe slugs, new connectors) falls to ``_DEFAULT_PRIORITY`` and is then
# tie-broken deterministically by fingerprint/url.
DEFAULT_SOURCE_PRIORITY: dict[str, int] = {
    "indeed": 0,
    "naukri": 1,
    "linkedin": 2,
    "glassdoor": 3,
    "google": 4,
    "zip_recruiter": 5,
    "bayt": 6,
}
_DEFAULT_PRIORITY = 100


# --------------------------------------------------------------------------- #
# Pure, deterministic helpers (independently unit-tested)
# --------------------------------------------------------------------------- #


def collect_keywords(keywords: Mapping[str, Any]) -> list[str]:
    """Flatten the JD keyword breakdown into a deduped, order-stable list.

    Combines ``required_skills`` + ``preferred_skills`` + ``keywords`` (the
    same union :func:`calculate_keyword_match` scores over), dropping empties
    and case-insensitive duplicates while preserving first-seen order.
    """
    out: list[str] = []
    seen: set[str] = set()
    for field in _KEYWORD_FIELDS:
        value = keywords.get(field)
        if not isinstance(value, (list, tuple)):
            continue
        for raw in value:
            if not isinstance(raw, (str, int, float)):
                continue
            term = str(raw).strip()
            key = term.casefold()
            if term and key not in seen:
                seen.add(key)
                out.append(term)
    return out


def keyword_in_text(keyword: str, text: str) -> bool:
    """Word-boundary, case-insensitive keyword presence check.

    Matches the substring-avoiding logic of ``refiner._keyword_in_text`` so
    the matched/missing split here is consistent with the scored coverage.
    """
    kw = (keyword or "").strip()
    if not kw:
        return False
    return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text or "", re.IGNORECASE) is not None


def resume_text(resume_processed: Mapping[str, Any] | None) -> str:
    """Flatten a processed-resume dict into one searchable lowercased blob.

    Recursively concatenates every string/number leaf. Used only for the
    matched/missing keyword split; the authoritative fit score comes from the
    injected :data:`MatchScorer`.
    """
    if not resume_processed:
        return ""
    parts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for v in node.values():
                _walk(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                _walk(v)
        elif isinstance(node, str):
            parts.append(node)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            parts.append(str(node))

    _walk(resume_processed)
    return " ".join(parts).lower()


def listing_jd_text(listing: JobListing) -> tuple[str, bool]:
    """Return ``(jd_text, partial)`` for scoring a listing.

    A listing with a non-empty description is scored on the full text; one
    without falls back to the available title + company + location snippet and
    is flagged ``partial`` (Req 7.2).
    """
    description = (listing.description or "").strip()
    if description:
        return description, False
    snippet = " ".join(
        part for part in (listing.title, listing.company, listing.location) if part
    ).strip()
    return snippet, True


def _sort_key(rec: Recommendation, source_priority: Mapping[str, int]) -> tuple:
    """Deterministic ordering key: score desc, recency desc, source priority,
    then a stable fingerprint/url fallback (Req 7.3)."""
    listing = rec.listing
    # Newer first: larger timestamp must sort earlier, so negate it. Missing
    # dates sort last within an otherwise-equal group.
    if listing.posted_at is not None:
        recency = -listing.posted_at.timestamp()
    else:
        recency = float("inf")
    priority = source_priority.get(listing.source, _DEFAULT_PRIORITY)
    stable = listing.fingerprint or listing.url or ""
    return (-rec.match_score, recency, priority, listing.source, stable)


def sort_recommendations(
    recommendations: Sequence[Recommendation],
    source_priority: Mapping[str, int] | None = None,
) -> list[Recommendation]:
    """Order recommendations by the deterministic Req 7.3 ranking."""
    priority = source_priority if source_priority is not None else DEFAULT_SOURCE_PRIORITY
    return sorted(recommendations, key=lambda r: _sort_key(r, priority))


def build_recommendation(
    listing: JobListing,
    keywords: Mapping[str, Any],
    match_score: float,
    resume_blob: str,
    *,
    partial: bool,
) -> Recommendation:
    """Assemble a :class:`Recommendation` with matched/missing keyword sets."""
    all_keywords = collect_keywords(keywords)
    matched: list[str] = []
    missing: list[str] = []
    for kw in all_keywords:
        (matched if keyword_in_text(kw, resume_blob) else missing).append(kw)
    # Clamp to the model's documented 0..100 range defensively.
    score = max(0.0, min(100.0, float(match_score)))
    return Recommendation(
        listing=listing,
        match_score=score,
        partial=partial,
        matched=matched,
        missing=missing,
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


async def rank_listings(
    user_id: str,
    listings: Sequence[JobListing],
    resume_processed: Mapping[str, Any] | None,
    *,
    keyword_extractor: KeywordExtractor | None = None,
    match_scorer: MatchScorer | None = None,
    source_priority: Mapping[str, int] | None = None,
) -> list[Recommendation]:
    """Score and rank ``listings`` against a resume (Req 7).

    Args:
        user_id: owner id, threaded into the content-addressed keyword cache.
        listings: normalized, deduped job listings to rank.
        resume_processed: the resume's structured/processed data (may be
            ``None`` — then nothing is "matched" and every score collapses to
            0, so results order purely by recency/source).
        keyword_extractor: async ``(user_id, jd_text) -> keyword dict``.
            Defaults to :func:`app.services.improver.extract_job_keywords_cached`
            (lazily imported). Injectable for deterministic tests.
        match_scorer: sync ``(resume, keywords) -> 0..100``. Defaults to
            :func:`app.services.refiner.calculate_keyword_match` (lazily
            imported). Injectable for deterministic tests.
        source_priority: optional override of the tie-break source ordering.

    Returns:
        Recommendations sorted per Req 7.3.
    """
    if keyword_extractor is None:
        from app.services.improver import extract_job_keywords_cached as keyword_extractor
    if match_scorer is None:
        from app.services.refiner import calculate_keyword_match as match_scorer

    resume_blob = resume_text(resume_processed)
    resume_for_score: Mapping[str, Any] = resume_processed or {}

    recommendations: list[Recommendation] = []
    for listing in listings:
        jd_text, partial = listing_jd_text(listing)
        try:
            keywords = await keyword_extractor(user_id, jd_text)
        except Exception:  # noqa: BLE001 - LLM unavailable is non-fatal for ranking
            # When the LLM is unavailable, fall back to empty keywords (title-only scoring)
            keywords = {}
        score = match_scorer(resume_for_score, keywords)
        recommendations.append(
            build_recommendation(
                listing,
                keywords,
                score,
                resume_blob,
                partial=partial,
            )
        )

    return sort_recommendations(recommendations, source_priority)


__all__ = [
    "KeywordExtractor",
    "MatchScorer",
    "DEFAULT_SOURCE_PRIORITY",
    "collect_keywords",
    "keyword_in_text",
    "resume_text",
    "listing_jd_text",
    "sort_recommendations",
    "build_recommendation",
    "rank_listings",
]
