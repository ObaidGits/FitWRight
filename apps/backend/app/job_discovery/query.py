"""Search-query generation for Job Discovery (design §5, Requirements 2.1-2.3).

The recommender needs a normalized :class:`~app.job_discovery.models.SearchQuery`
(target titles + a board-ready keyword string + seniority) before it can fan a
resume out across connectors. This module produces one, in priority order:

1. **LLM path (Req 2.1)** -- the resume text is sent through ``app.llm`` with the
   discovery prompt and the JSON reply is parsed into a ``SearchQuery``.
2. **Deterministic fallback (Req 2.2)** -- if the LLM is unavailable, errors, or
   returns an unusable shape, a pure keyword/role heuristic synthesizes a query
   and marks it ``degraded=True`` so the caller can surface a partial-results
   banner.

Results are cached keyed by ``resume_version`` (Req 2.3): the resume-derived
intent (titles / search_string / seniority) is stable for a given resume
revision, so a repeat call reuses it and never re-hits the LLM. User-supplied
filters (``location`` / ``country_indeed``) are *overlaid* per call rather than
baked into the cache key, so the same cached intent serves different locations.

The LLM entry point is injectable (``llm_complete``) so the fallback and
cache-hit paths are unit-testable without a live provider.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from app.job_discovery.models import SearchFilters, SearchQuery
from app.prompts.discovery import (
    DISCOVERY_QUERY_PROMPT,
    DISCOVERY_QUERY_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

# A JSON-returning async completion, matching ``app.llm.complete_json``'s shape
# (keyword-only ``system_prompt`` / ``schema_type`` / ``max_tokens``). Injected
# in tests; resolved lazily from ``app.llm`` in production.
LLMCompleteJson = Callable[..., Awaitable[dict[str, Any]]]

# Bounded LRU of resume-derived query intent, keyed by resume_version (Req 2.3).
_QUERY_CACHE: OrderedDict[str, SearchQuery] = OrderedDict()
_QUERY_CACHE_MAX = 256

# Max target titles we ever emit (Req 2.1: 1-3 concise titles).
_MAX_TITLES = 3
# Skill keywords appended to the deterministic search string.
_MAX_FALLBACK_KEYWORDS = 6

# Seniority levels, ordered most-senior-first so the first hit wins.
_SENIORITY_ORDER: tuple[str, ...] = (
    "principal",
    "staff",
    "lead",
    "senior",
    "mid",
    "junior",
    "intern",
)
# Extra surface forms that map onto a canonical seniority level.
_SENIORITY_ALIASES: dict[str, str] = {
    "sr": "senior",
    "sr.": "senior",
    "jr": "junior",
    "jr.": "junior",
    "midlevel": "mid",
    "mid-level": "mid",
    "internship": "intern",
}

# Canonical role phrases recognized by the deterministic fallback. Insertion
# order is the tie-break, so output is stable for a given resume.
_ROLE_PHRASES: tuple[str, ...] = (
    "software engineer",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "machine learning engineer",
    "data scientist",
    "data engineer",
    "data analyst",
    "devops engineer",
    "site reliability engineer",
    "cloud engineer",
    "platform engineer",
    "systems engineer",
    "security engineer",
    "qa engineer",
    "mobile developer",
    "android developer",
    "ios developer",
    "web developer",
    "product manager",
    "project manager",
    "business analyst",
    "ux designer",
    "ui designer",
    "product designer",
)

# Words stripped before keyword frequency counting in the fallback. Deliberately
# small: generic resume boilerplate + English function words. Anything role- or
# skill-bearing is intentionally kept.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "you", "your", "our", "are", "was", "were",
        "this", "that", "from", "have", "has", "had", "will", "would", "can",
        "not", "but", "all", "any", "who", "she", "her", "his", "him", "they",
        "them", "their", "its", "into", "out", "over", "under", "than", "then",
        "experience", "experienced", "work", "worked", "working", "team",
        "teams", "project", "projects", "responsible", "responsibilities",
        "role", "roles", "company", "companies", "years", "year", "including",
        "using", "used", "use", "various", "strong", "proven", "skills",
        "skilled", "ability", "abilities", "excellent", "good", "great",
        "resume", "curriculum", "vitae", "summary", "objective", "profile",
        "education", "university", "college", "degree", "bachelor", "master",
        "phd", "gpa", "email", "phone", "linkedin", "github", "http", "https",
        "www", "com", "present", "current", "currently", "led", "build",
        "built", "developed", "designed", "managed", "created", "implemented",
    }
)

# Token pattern: keeps tech-flavoured tokens (c++, c#, node.js, ci/cd) intact.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+.#/-]*")


def clear_query_cache() -> None:
    """Drop the resume-version query cache. Used for test isolation."""
    _QUERY_CACHE.clear()


def _cache_get(resume_version: str | None) -> SearchQuery | None:
    if not resume_version:
        return None
    hit = _QUERY_CACHE.get(resume_version)
    if hit is not None:
        _QUERY_CACHE.move_to_end(resume_version)
    return hit


def _cache_put(resume_version: str | None, query: SearchQuery) -> None:
    if not resume_version:
        return
    _QUERY_CACHE[resume_version] = query
    _QUERY_CACHE.move_to_end(resume_version)
    while len(_QUERY_CACHE) > _QUERY_CACHE_MAX:
        _QUERY_CACHE.popitem(last=False)


def _detect_seniority(text_lower: str) -> str | None:
    """Return a canonical seniority level found in ``text_lower``, or None."""
    tokens = set(_TOKEN_RE.findall(text_lower))
    for token, canonical in _SENIORITY_ALIASES.items():
        if token in tokens:
            return canonical
    for level in _SENIORITY_ORDER:
        if level in tokens:
            return level
    return None


def _detect_titles(text_lower: str) -> list[str]:
    """Match known role phrases in ``text_lower`` (stable order, capped)."""
    titles: list[str] = []
    for phrase in _ROLE_PHRASES:
        if phrase in text_lower and phrase not in titles:
            titles.append(phrase)
            if len(titles) >= _MAX_TITLES:
                break
    return titles


def _top_keywords(text_lower: str, exclude: set[str], limit: int) -> list[str]:
    """Most frequent non-stopword tokens, deterministic (count desc, then a-z)."""
    counts: Counter[str] = Counter()
    for token in _TOKEN_RE.findall(text_lower):
        if len(token) < 2 or token.isdigit():
            continue
        if token in _STOPWORDS or token in exclude:
            continue
        counts[token] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [token for token, _ in ranked[:limit]]


def _build_search_string(titles: list[str], keywords: list[str]) -> str:
    """Compose a boolean-style board query from titles + skill keywords."""
    parts: list[str] = []
    if titles:
        quoted = " OR ".join(f'"{title}"' for title in titles)
        parts.append(f"({quoted})" if len(titles) > 1 else quoted)
    parts.extend(keywords)
    return " ".join(parts).strip()


def build_deterministic_query(
    resume_text: str,
    *,
    resume_version: str | None = None,
    filters: SearchFilters | None = None,
) -> SearchQuery:
    """Synthesize a :class:`SearchQuery` from the resume with NO LLM (Req 2.2).

    Pure and deterministic: the same resume text always yields the same titles,
    search string, and seniority. Always returns ``degraded=True`` -- it is the
    fallback path, and the caller uses that flag to signal reduced quality.
    """
    text_lower = (resume_text or "").lower()

    titles = _detect_titles(text_lower)
    # Exclude words already present in the matched titles from keyword ranking
    # so the search string doesn't echo the titles back as loose keywords.
    title_words = {word for title in titles for word in title.split()}
    keywords = _top_keywords(text_lower, exclude=title_words, limit=_MAX_FALLBACK_KEYWORDS)

    if not titles:
        # No known role phrase matched -- derive a single pseudo-title from the
        # strongest keyword so downstream connectors still have something to
        # search on, rather than an empty title list.
        titles = [keywords[0].title()] if keywords else ["Professional"]

    search_string = _build_search_string(titles, keywords)
    return SearchQuery(
        titles=titles,
        search_string=search_string or titles[0],
        seniority=_detect_seniority(text_lower),
        degraded=True,
        resume_version=resume_version,
    )


def _parse_llm_query(
    payload: dict[str, Any],
    *,
    resume_version: str | None,
) -> SearchQuery:
    """Validate an LLM JSON reply into a SearchQuery. Raises on bad shape.

    Raising (rather than silently degrading) lets the caller fall back to the
    deterministic path and mark the result degraded in one place. Missing or
    wrongly-typed fields collapse to "no usable titles", which is a value-level
    failure the caller treats identically to any other LLM miss.
    """
    raw_titles = payload.get("titles")
    titles: list[str] = []
    if isinstance(raw_titles, list):
        for item in raw_titles:
            if isinstance(item, str) and item.strip() and item.strip() not in titles:
                titles.append(item.strip())
    titles = titles[:_MAX_TITLES]
    if not titles:
        raise ValueError("LLM query payload produced no usable titles")

    raw_search = payload.get("search_string")
    search_string = raw_search.strip() if isinstance(raw_search, str) else ""
    if not search_string:
        # Model gave titles but no search string -- synthesize one from titles
        # rather than discarding an otherwise-valid LLM result.
        search_string = _build_search_string(titles, [])

    raw_seniority = payload.get("seniority")
    seniority: str | None = None
    if isinstance(raw_seniority, str):
        candidate = raw_seniority.strip().lower()
        candidate = _SENIORITY_ALIASES.get(candidate, candidate)
        if candidate in _SENIORITY_ORDER:
            seniority = candidate

    return SearchQuery(
        titles=titles,
        search_string=search_string,
        seniority=seniority,
        degraded=False,
        resume_version=resume_version,
    )


def _apply_filters(query: SearchQuery, filters: SearchFilters | None) -> SearchQuery:
    """Overlay user-supplied location constraints onto a resume-derived query.

    Location is a per-request filter, not part of the cached resume intent, so
    it is merged on the way out. Returns a copy; never mutates the cached value.
    """
    if filters is None:
        return replace(query)
    return replace(
        query,
        location=filters.location or query.location,
        country_indeed=filters.country_indeed or query.country_indeed,
    )


async def _resolve_llm(
    llm_complete: LLMCompleteJson | None,
) -> LLMCompleteJson | None:
    """Return the injected completion fn, else lazily import ``app.llm``.

    The import is deferred so this module (and its deterministic path) load even
    when the optional LLM stack is absent -- importing at module scope would
    couple every consumer to litellm being installed.
    """
    if llm_complete is not None:
        return llm_complete
    try:
        from app.llm import complete_json  # local import by design

        return complete_json
    except Exception:  # noqa: BLE001  # pragma: no cover - import environment dependent
        logger.info("app.llm unavailable; query generation will use the fallback")
        return None


async def generate_search_query(
    resume_text: str,
    *,
    resume_version: str | None = None,
    filters: SearchFilters | None = None,
    config: Any = None,
    llm_complete: LLMCompleteJson | None = None,
    force_refresh: bool = False,
) -> SearchQuery:
    """Produce a :class:`SearchQuery` for ``resume_text`` (Req 2.1-2.3).

    Args:
        resume_text: The candidate's resume as plain text.
        resume_version: Content revision used as the cache key (Req 2.3). When
            falsy, caching is skipped.
        filters: User-supplied constraints; ``location``/``country_indeed`` are
            overlaid onto the result without affecting the cache key.
        config: Optional ``app.llm`` config passed through to the completion.
        llm_complete: Injected JSON completion (tests); defaults to
            ``app.llm.complete_json`` resolved lazily.
        force_refresh: Bypass the cache and regenerate.

    Returns:
        A ``SearchQuery``. ``degraded=True`` marks a deterministic fallback.
    """
    if not force_refresh:
        cached = _cache_get(resume_version)
        if cached is not None:
            return _apply_filters(cached, filters)

    location = filters.location if filters and filters.location else ""
    base: SearchQuery | None = None

    completer = await _resolve_llm(llm_complete)
    if completer is not None:
        try:
            payload = await completer(
                DISCOVERY_QUERY_PROMPT.format(
                    resume_text=resume_text or "",
                    location=location,
                ),
                system_prompt=DISCOVERY_QUERY_SYSTEM_PROMPT,
                config=config,
                schema_type="keywords",
                max_tokens=512,
            )
            base = _parse_llm_query(payload, resume_version=resume_version)
        except Exception as exc:  # noqa: BLE001 - any failure -> deterministic path
            logger.warning("LLM query generation failed (%s); using fallback", exc)
            base = None

    if base is None:
        base = build_deterministic_query(
            resume_text, resume_version=resume_version, filters=filters
        )

    # Cache only high-quality (non-degraded) intent so a transient LLM outage
    # doesn't pin a degraded query for this resume_version until TTL/eviction.
    if not base.degraded:
        _cache_put(resume_version, base)

    return _apply_filters(base, filters)


__all__ = [
    "LLMCompleteJson",
    "build_deterministic_query",
    "clear_query_cache",
    "generate_search_query",
]
