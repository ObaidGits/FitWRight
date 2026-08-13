"""Discovery orchestrator — the one service that runs a recommend request.

This is the conductor for the whole Job Discovery pipeline (design §7). Given a
``(user_id, resume_id, filters)`` it runs, in order:

1. **Kill-switch gate** — refuse everything unless ``JOB_DISCOVERY`` is on
   (:class:`DiscoveryDisabledError`, Req 10.4).
2. **Ownership check** — resolve the resume through an injected
   :data:`ResumeLoader` scoped by ``user_id``; a miss (absent *or* not owned)
   raises :class:`ResumeNotFoundError` (Req 1.5). Site-recipe connectors are
   likewise built only from *this* user's recipes.
3. **Query generation** — :func:`app.job_discovery.query.generate_search_query`
   turns the resume into a :class:`SearchQuery`, degrading deterministically if
   the LLM is unavailable (Req 2.2).
4. **Cache lookup** — a content-addressed hit
   (:mod:`app.job_discovery.cache`) short-circuits the fan-out and returns the
   stored response with ``cached=True`` (Req 6). ``force_refresh`` skips it.
5. **Connector fan-out with partial success** — every enabled connector runs
   concurrently, each behind :func:`run_connector` so a single source failing
   is *collected, never raised* (Req 1.2, 3.2). Per-connector attribution is
   preserved by giving each its own :class:`FailureReport`.
6. **Normalize + dedup** — :func:`app.job_discovery.normalize.normalize`.
7. **Rank** — :func:`app.job_discovery.ranker.rank_listings` against the
   resume, truncated to ``JOB_DISCOVERY_MAX_RESULTS`` (Req 7).
8. **Cache store** — non-degraded results are persisted so a transient source
   failure never pins a partial result until TTL (Req 6).

The result carries a per-source ``sources`` report and a ``degraded`` flag
(true when the query fell back *or* any source failed) so the router (Wave 3)
can surface a partial-results banner (Req 1.2, 3.2).

Every external collaborator (resume loader, connectors, query fn, ranker, cache)
is injectable so the orchestration is unit-testable with fakes and never touches
a live LLM, browser, or scraper.

Design reference: ``.kiro/specs/job-discovery/design.md`` §7 (orchestrator).
Requirements: 1.1, 1.2, 1.3, 1.5, 2.2, 3.2, 6, 7, 10.4.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import Settings, settings
from app.database import Database
from app.job_discovery.cache import SearchCache
from app.job_discovery.connectors.base import (
    Connector,
    FailureReport,
    RawListing,
    run_connector,
)
from app.job_discovery.models import (
    JobListing,
    Recommendation,
    SearchFilters,
    SearchQuery,
    SourceFailure,
)
from app.job_discovery.normalize import normalize as normalize_listings
from app.job_discovery.query import generate_search_query
from app.job_discovery.ranker import rank_listings
from app.job_discovery.recipes import list_recipes

logger = logging.getLogger(__name__)

__all__ = [
    "DiscoveryError",
    "DiscoveryDisabledError",
    "ResumeNotFoundError",
    "ResumeData",
    "SourceReport",
    "DiscoveryResult",
    "DiscoveryService",
    "default_connector_builder",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class DiscoveryError(Exception):
    """Base class for discovery-service errors."""


class DiscoveryDisabledError(DiscoveryError):
    """The ``JOB_DISCOVERY`` kill-switch is off; the service refuses work (Req 10.4)."""


class ResumeNotFoundError(DiscoveryError):
    """No resume with that id is owned by the caller (ownership check, Req 1.5)."""


# --------------------------------------------------------------------------- #
# Collaborator shapes (all injectable)
# --------------------------------------------------------------------------- #
@dataclass
class ResumeData:
    """The resume inputs the pipeline needs, resolved by a :data:`ResumeLoader`.

    :attr:`text` feeds query generation; :attr:`processed` (the structured
    resume) feeds ranking; :attr:`version` keys both the query cache and the
    search cache so a resume edit invalidates stale results (Req 2.3, 6).
    """

    resume_id: str
    text: str = ""
    processed: Mapping[str, Any] | None = None
    version: str | None = None


# ``(user_id, resume_id) -> ResumeData | None``. Returning ``None`` means the
# resume does not exist OR is not owned by ``user_id`` — the loader IS the
# ownership boundary, so the service treats both identically (Req 1.5).
ResumeLoader = Callable[[str, str], Awaitable["ResumeData | None"]]

# ``user_id -> [Connector, ...]``: assemble the enabled sources for a user.
ConnectorBuilder = Callable[[str], Awaitable[Sequence[Connector]]]

# ``resume_text -> SearchQuery`` (keyword-only extras). Matches
# ``query.generate_search_query``.
QueryFn = Callable[..., Awaitable[SearchQuery]]

# ``(user_id, listings, resume_processed) -> [Recommendation, ...]``. Matches
# ``ranker.rank_listings``.
RankFn = Callable[..., Awaitable[list[Recommendation]]]


# --------------------------------------------------------------------------- #
# Result shapes
# --------------------------------------------------------------------------- #
@dataclass
class SourceReport:
    """Per-connector outcome for the response ``sources`` block (Req 1.2, 3.2).

    ``status`` is one of:
        ``ok``      — returned rows, no failures
        ``partial`` — returned rows *and* recorded ≥1 failure (e.g. one JobSpy
                      board blocked while others succeeded)
        ``failed``  — returned nothing and recorded ≥1 failure
        ``empty``   — returned nothing, no failure (a clean no-results source)
    """

    source: str
    status: str
    count: int = 0
    failures: list[SourceFailure] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    """The orchestrator's output; the router maps this to the wire response.

    ``degraded`` is the OR of a fallback query and any source failure, so the
    client can show a partial-results banner. ``cached`` marks a cache hit.
    """

    recommendations: list[Recommendation] = field(default_factory=list)
    query: SearchQuery | None = None
    degraded: bool = False
    cached: bool = False
    failures: list[SourceFailure] = field(default_factory=list)
    sources: list[SourceReport] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # JSON round-tripping (for the content-addressed cache)
    # ------------------------------------------------------------------ #
    def to_payload(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for the cache (``cached`` is runtime-only)."""
        return {
            "recommendations": [_rec_to_dict(r) for r in self.recommendations],
            "query": _query_to_dict(self.query) if self.query else None,
            "degraded": self.degraded,
            "failures": [_failure_to_dict(f) for f in self.failures],
            "sources": [_source_to_dict(s) for s in self.sources],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "DiscoveryResult":
        """Rebuild a result from a cached payload, marking it ``cached=True``."""
        return cls(
            recommendations=[
                _rec_from_dict(r) for r in payload.get("recommendations", [])
            ],
            query=_query_from_dict(payload.get("query")),
            degraded=bool(payload.get("degraded", False)),
            cached=True,
            failures=[_failure_from_dict(f) for f in payload.get("failures", [])],
            sources=[_source_from_dict(s) for s in payload.get("sources", [])],
        )


# --------------------------------------------------------------------------- #
# (de)serialization helpers — kept explicit so datetimes survive the JSON hop.
# --------------------------------------------------------------------------- #
def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _dt_from_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _listing_to_dict(listing: JobListing) -> dict[str, Any]:
    return {
        "source": listing.source,
        "title": listing.title,
        "company": listing.company,
        "location": listing.location,
        "url": listing.url,
        "is_remote": listing.is_remote,
        "description": listing.description,
        "posted_at": _dt_to_iso(listing.posted_at),
        "salary": listing.salary,
        "fingerprint": listing.fingerprint,
    }


def _listing_from_dict(data: Mapping[str, Any]) -> JobListing:
    return JobListing(
        source=data.get("source", ""),
        title=data.get("title", ""),
        company=data.get("company", ""),
        location=data.get("location", ""),
        url=data.get("url", ""),
        is_remote=data.get("is_remote"),
        description=data.get("description"),
        posted_at=_dt_from_iso(data.get("posted_at")),
        salary=data.get("salary"),
        fingerprint=data.get("fingerprint", ""),
    )


def _rec_to_dict(rec: Recommendation) -> dict[str, Any]:
    return {
        "listing": _listing_to_dict(rec.listing),
        "match_score": rec.match_score,
        "partial": rec.partial,
        "matched": list(rec.matched),
        "missing": list(rec.missing),
    }


def _rec_from_dict(data: Mapping[str, Any]) -> Recommendation:
    return Recommendation(
        listing=_listing_from_dict(data.get("listing", {})),
        match_score=float(data.get("match_score", 0.0)),
        partial=bool(data.get("partial", False)),
        matched=list(data.get("matched", [])),
        missing=list(data.get("missing", [])),
    )


def _query_to_dict(query: SearchQuery) -> dict[str, Any]:
    return {
        "titles": list(query.titles),
        "search_string": query.search_string,
        "seniority": query.seniority,
        "location": query.location,
        "country_indeed": query.country_indeed,
        "degraded": query.degraded,
    }


def _query_from_dict(data: Any) -> SearchQuery | None:
    if not isinstance(data, Mapping):
        return None
    return SearchQuery(
        titles=list(data.get("titles", [])),
        search_string=data.get("search_string", ""),
        seniority=data.get("seniority"),
        location=data.get("location"),
        country_indeed=data.get("country_indeed"),
        degraded=bool(data.get("degraded", False)),
    )


def _failure_to_dict(f: SourceFailure) -> dict[str, Any]:
    return {"source": f.source, "reason": f.reason, "kind": f.kind}


def _failure_from_dict(data: Mapping[str, Any]) -> SourceFailure:
    return SourceFailure(
        source=data.get("source", ""),
        reason=data.get("reason", ""),
        kind=data.get("kind"),
    )


def _source_to_dict(s: SourceReport) -> dict[str, Any]:
    return {
        "source": s.source,
        "status": s.status,
        "count": s.count,
        "failures": [_failure_to_dict(f) for f in s.failures],
    }


def _source_from_dict(data: Mapping[str, Any]) -> SourceReport:
    return SourceReport(
        source=data.get("source", ""),
        status=data.get("status", "empty"),
        count=int(data.get("count", 0)),
        failures=[_failure_from_dict(f) for f in data.get("failures", [])],
    )


# --------------------------------------------------------------------------- #
# Default connector builder
# --------------------------------------------------------------------------- #
async def default_connector_builder(
    user_id: str, *, db: Database, config: Settings
) -> list[Connector]:
    """Assemble the production connector set for ``user_id`` (design §7).

    JobSpy fixed-board fast lane (from ``JOB_DISCOVERY_JOBSPY_SITES``) plus one
    :class:`SiteRecipeConnector` per *enabled* recipe the user owns. Connector
    classes lazily import their optional scraper/browser deps, so building the
    set never requires the ``job-discovery`` extra to be installed.
    """
    # Imported here (not at module top) to keep the base import graph free of
    # the connectors' optional-dependency surface.
    from app.job_discovery.connectors.jobspy import JobSpyConnector
    from app.job_discovery.connectors.site_recipe import SiteRecipeConnector

    connectors: list[Connector] = []

    sites = config.job_discovery_jobspy_sites
    if sites:
        connectors.append(JobSpyConnector(sites=sites))

    for recipe in await list_recipes(db, user_id):
        if recipe.enabled:
            connectors.append(SiteRecipeConnector(recipe))

    return connectors


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
class DiscoveryService:
    """Runs one recommend request end-to-end (design §7).

    Stable collaborators are held on the instance; per-request inputs go to
    :meth:`recommend`. Everything is injectable so the orchestration can be
    unit-tested with fakes and never reaches a live LLM/browser/scraper.

    Args:
        db: discovery data-access facade (cache + recipes).
        resume_loader: ``(user_id, resume_id) -> ResumeData | None``; the
            ownership boundary (Req 1.5).
        cache: content-addressed search cache; defaults to one over ``db`` with
            the configured TTL (Req 6).
        config: settings snapshot (kill-switch, sites, caps).
        connector_builder: ``user_id -> [Connector]``; defaults to
            :func:`default_connector_builder` bound to ``db``/``config``.
        query_fn / rank_fn: the query-generation and ranking entry points;
            overridable for tests.
        keyword_extractor / match_scorer: forwarded into ``rank_fn`` so ranking
            runs without the real LLM in tests.
        llm_complete: forwarded into ``query_fn``.
    """

    def __init__(
        self,
        db: Database,
        *,
        resume_loader: ResumeLoader,
        cache: SearchCache | None = None,
        config: Settings = settings,
        connector_builder: ConnectorBuilder | None = None,
        query_fn: QueryFn = generate_search_query,
        rank_fn: RankFn = rank_listings,
        keyword_extractor: Any = None,
        match_scorer: Any = None,
        llm_complete: Any = None,
    ) -> None:
        self._db = db
        self._resume_loader = resume_loader
        self._cache = cache if cache is not None else SearchCache(db)
        self._config = config
        self._connector_builder = connector_builder
        self._query_fn = query_fn
        self._rank_fn = rank_fn
        self._keyword_extractor = keyword_extractor
        self._match_scorer = match_scorer
        self._llm_complete = llm_complete

    # ------------------------------------------------------------------ #
    async def recommend(
        self,
        *,
        user_id: str,
        resume_id: str,
        filters: SearchFilters | None = None,
        force_refresh: bool = False,
        connectors: Sequence[Connector] | None = None,
    ) -> DiscoveryResult:
        """Produce ranked recommendations for a resume (Req 1).

        Args:
            user_id: caller identity; scopes ownership and recipe selection.
            resume_id: resume to recommend against.
            filters: optional user search constraints (location, remote, …).
            force_refresh: bypass the content-addressed cache and re-fan-out.
            connectors: explicit connector set (tests inject fakes); when
                ``None`` the configured builder assembles them.

        Raises:
            DiscoveryDisabledError: the kill-switch is off (Req 10.4).
            ResumeNotFoundError: the resume is absent or not owned (Req 1.5).
        """
        # 1. Kill-switch gate (Req 10.4).
        if not self._config.JOB_DISCOVERY:
            raise DiscoveryDisabledError("job discovery is disabled")

        # 2. Ownership check via the injected loader (Req 1.5).
        resume = await self._resume_loader(user_id, resume_id)
        if resume is None:
            raise ResumeNotFoundError(
                f"resume {resume_id!r} not found for user {user_id!r}"
            )

        active_filters = filters or SearchFilters()

        # 3. Query generation (Req 2). Degrades deterministically on LLM miss.
        query = await self._query_fn(
            resume.text,
            resume_version=resume.version,
            filters=active_filters,
            llm_complete=self._llm_complete,
            force_refresh=force_refresh,
        )

        # 4. Content-addressed cache lookup (Req 6).
        if not force_refresh:
            cached_payload = await self._cache.get(
                resume.version, query, active_filters
            )
            if cached_payload is not None:
                logger.debug("discovery cache hit for user %s resume %s", user_id, resume_id)
                return DiscoveryResult.from_payload(cached_payload)

        # 5. Connector fan-out with partial success (Req 1.2, 3.2).
        if connectors is None:
            connectors = await self._build_connectors(user_id)

        report = FailureReport()
        source_reports: list[SourceReport] = []
        raw: list[RawListing] = []

        if connectors:
            fanned = await asyncio.gather(
                *(self._run_one(c, query, active_filters) for c in connectors)
            )
            for connector, rows, sub in fanned:
                raw.extend(rows)
                report.failures.extend(sub.failures)
                source_reports.append(
                    _summarize_source(connector, rows, sub.failures)
                )

        # 6. Normalize + dedup (Req 6.1).
        listings = normalize_listings(raw)

        # 7. Rank + truncate to the configured cap (Req 7).
        recommendations = await self._rank_fn(
            user_id,
            listings,
            resume.processed,
            keyword_extractor=self._keyword_extractor,
            match_scorer=self._match_scorer,
        )
        max_results = int(self._config.JOB_DISCOVERY_MAX_RESULTS)
        if max_results >= 0:
            recommendations = recommendations[:max_results]

        degraded = bool(query.degraded) or report.degraded

        result = DiscoveryResult(
            recommendations=recommendations,
            query=query,
            degraded=degraded,
            cached=False,
            failures=list(report.failures),
            sources=source_reports,
        )

        # 8. Cache store — only clean (non-degraded) results, so a transient
        #    source failure or a fallback query is never pinned until TTL (Req 6).
        if not degraded:
            await self._cache.store(
                resume.version, query, active_filters, result.to_payload()
            )

        return result

    # ------------------------------------------------------------------ #
    async def cached_recommendation(
        self,
        *,
        user_id: str,
        resume_id: str,
        filters: SearchFilters | None = None,
    ) -> DiscoveryResult | None:
        """Return the last *fresh* cached recommendation without a fan-out (design §9).

        Backs ``GET /discovery/recommend/{resume_id}``: it re-derives the content
        cache key (kill-switch gate -> ownership check -> query generation) and
        returns the stored :class:`DiscoveryResult` on a hit, or ``None`` when the
        cache is cold/expired. It never runs connectors, normalization, or ranking,
        so the cheap "show my last results" path stays cheap and never scrapes (Req 6).

        Raises:
            DiscoveryDisabledError: the kill-switch is off (Req 10.4).
            ResumeNotFoundError: the resume is absent or not owned (Req 1.5).
        """
        # 1. Kill-switch gate (Req 10.4).
        if not self._config.JOB_DISCOVERY:
            raise DiscoveryDisabledError("job discovery is disabled")

        # 2. Ownership check via the injected loader (Req 1.5).
        resume = await self._resume_loader(user_id, resume_id)
        if resume is None:
            raise ResumeNotFoundError(
                f"resume {resume_id!r} not found for user {user_id!r}"
            )

        active_filters = filters or SearchFilters()

        # 3. Re-derive the query (cached by resume version, Req 2.3) so the
        #    content-addressed key matches a prior recommend call's key (Req 6).
        query = await self._query_fn(
            resume.text,
            resume_version=resume.version,
            filters=active_filters,
            llm_complete=self._llm_complete,
            force_refresh=False,
        )

        # 4. Cache-only read: a miss returns None (no fan-out).
        payload = await self._cache.get(resume.version, query, active_filters)
        if payload is None:
            return None
        return DiscoveryResult.from_payload(payload)

    # ------------------------------------------------------------------ #
    async def _build_connectors(self, user_id: str) -> Sequence[Connector]:
        if self._connector_builder is not None:
            return await self._connector_builder(user_id)
        return await default_connector_builder(
            user_id, db=self._db, config=self._config
        )

    @staticmethod
    async def _run_one(
        connector: Connector, query: SearchQuery, filters: SearchFilters
    ) -> tuple[Connector, list[RawListing], FailureReport]:
        """Run one connector behind :func:`run_connector` with its own report.

        A per-connector :class:`FailureReport` lets us attribute failures back to
        the source that produced them even though the fan-out runs concurrently.
        """
        sub = FailureReport()
        rows = await run_connector(connector, query, filters, sub)
        return connector, rows, sub


def _summarize_source(
    connector: Connector, rows: Sequence[RawListing], failures: Sequence[SourceFailure]
) -> SourceReport:
    """Classify one connector's outcome into a :class:`SourceReport` (Req 1.2)."""
    name = getattr(connector, "name", "unknown")
    count = len(rows)
    fail_list = list(failures)
    if count and fail_list:
        status = "partial"
    elif count:
        status = "ok"
    elif fail_list:
        status = "failed"
    else:
        status = "empty"
    return SourceReport(source=name, status=status, count=count, failures=fail_list)
