"""Site-recipe connector — scrape a user-defined custom job board (design §4).

A :class:`~app.job_discovery.models.SiteRecipe` describes how to search one
custom site the fixed-board connectors don't cover: a URL template with a
``{query}`` placeholder, a fetch lane, and a JSON extraction ``schema``. This
connector turns that recipe into a stream of
:class:`~app.job_discovery.connectors.base.RawListing` rows, in four steps:

1. **Render** ``search_url_template`` with the URL-encoded search term.
2. **Validate** the rendered URL through :func:`app.jd.ssrf.validate_url`
   *before any fetch* — fail-closed, so a recipe pointing at loopback / cloud
   metadata / any internal host is refused (Req 4.4, 11.3).
3. **Fetch** the page via :mod:`app.job_discovery.fetch` (the ``http`` or
   ``stealth`` lane the recipe declares), honouring the shared byte/time bounds.
4. **Extract** structured records with Crawl4AI's LLM extraction strategy driven
   by the recipe ``schema``, then **map** each record onto ``RawListing`` —
   known fields are mapped, everything else rides along in ``extra`` (nothing is
   silently lost, but only recognised fields shape the canonical listing).

Per the connector contract (:mod:`app.job_discovery.connectors.base`) a single
source failure is **collected, never raised**: an SSRF rejection, a fetch error,
or an extraction error each append exactly one
:class:`~app.job_discovery.models.SourceFailure` and return ``[]`` so the
fan-out stays partial-success.

Crawl4AI is an optional dependency (the ``job-discovery`` extra) and is imported
lazily inside the default extractor, so the base app boots without it; when it
is absent a recipe fetch degrades to a recorded failure rather than an import
crash. The extractor is injectable so extraction is unit-tested against saved
HTML fixtures with no browser or LLM.

Requirements: 4.2, 4.3, 4.4, 4.5, 5, 11.3.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote_plus, urljoin

from app.jd.ssrf import SsrfError as SSRFError, validate_fetch_url as validate_url
from app.job_discovery import fetch as fetch_mod
from app.job_discovery.connectors.base import RawListing, classify_failure
from app.job_discovery.fetch import FetchError, FetchResult
from app.job_discovery.models import (
    FetchMode,
    SearchFilters,
    SearchQuery,
    SiteRecipe,
    SourceFailure,
)
from app.job_discovery.recipes import QUERY_PLACEHOLDER

logger = logging.getLogger(__name__)

# An extractor turns fetched page text + the recipe schema + the recipe base URL
# into a list of raw record dicts. Injected in tests; the production default
# (:func:`crawl4ai_extractor`) lazily wires Crawl4AI + the configured LLM.
Extractor = Callable[[str, dict, str], Awaitable[list[dict[str, Any]]]]

# RawListing fields a recipe record can populate directly. Any other extracted
# key is preserved verbatim in ``RawListing.extra`` (Req 4.3 map/drop).
_KNOWN_FIELDS: frozenset[str] = frozenset(
    {"title", "company", "location", "url", "description", "salary", "is_remote"}
)

# Truthy / falsy string forms accepted for the boolean ``is_remote`` field.
_TRUE_STRINGS = frozenset({"true", "1", "yes", "y", "remote", "wfh"})
_FALSE_STRINGS = frozenset({"false", "0", "no", "n", "onsite", "on-site"})


class ExtractionError(RuntimeError):
    """Structured extraction failed for a recipe page (recoverable source failure)."""


def _as_str(value: Any) -> str:
    """Coerce an extracted value to a trimmed string; ``None``/absent → ``""``."""
    if value is None:
        return ""
    return str(value).strip()


def _as_opt_str(value: Any) -> str | None:
    """Like :func:`_as_str` but a blank/absent value stays ``None``."""
    text = _as_str(value)
    return text or None


def _as_opt_bool(value: Any) -> bool | None:
    """Best-effort parse of an extracted remote flag; unknown → ``None``."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    return None


async def crawl4ai_extractor(page_text: str, schema: dict, base_url: str) -> list[dict[str, Any]]:
    """Default extractor: Crawl4AI LLM extraction over already-fetched HTML.

    Imported lazily so the base app never depends on the optional
    ``job-discovery`` extra. Crawl4AI processes the raw HTML we already fetched
    (via the ``raw://`` scheme) rather than fetching again, so the SSRF guard and
    the shared fetch bounds still gate every network egress. A missing dependency
    surfaces as :class:`ExtractionError` — one recorded source failure, never an
    import crash.
    """
    try:  # pragma: no cover - exercised only with the optional extra + a live LLM
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig  # type: ignore
        from crawl4ai.extraction_strategy import (  # type: ignore
            LLMExtractionStrategy,
        )
    except Exception as exc:  # pragma: no cover - optional dependency absent
        raise ExtractionError(
            f"crawl4ai not installed (install the 'job-discovery' extra): {exc}"
        ) from exc

    strategy = LLMExtractionStrategy(  # pragma: no cover - needs live provider
        schema=schema or None,
        extraction_type="schema" if schema else "block",
    )
    run_config = CrawlerRunConfig(extraction_strategy=strategy)  # pragma: no cover
    async with AsyncWebCrawler() as crawler:  # pragma: no cover
        result = await crawler.arun(f"raw://{page_text}", config=run_config)
        import json  # pragma: no cover

        extracted = getattr(result, "extracted_content", None)
        if not extracted:
            return []
        data = json.loads(extracted) if isinstance(extracted, str) else extracted
        if isinstance(data, dict):
            return [data]
        return [rec for rec in data if isinstance(rec, dict)]


class SiteRecipeConnector:
    """A :class:`~app.job_discovery.connectors.base.Connector` for one site recipe.

    Collaborators (fetch, URL validator, extractor) are injectable so the whole
    pipeline — URL rendering, the SSRF gate, field mapping — is unit-testable
    against saved HTML fixtures with no network, browser, or LLM.
    """

    def __init__(
        self,
        recipe: SiteRecipe,
        *,
        fetch_fn: Callable[..., Awaitable[FetchResult]] | None = None,
        url_validator: Callable[[str], str] | None = None,
        extractor: Extractor | None = None,
        timeout: float | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.recipe = recipe
        # Stable identifier surfaced in results and failure reports.
        self.name: str = recipe.slug
        self.fetch_mode: FetchMode = recipe.fetch_mode
        self._fetch = fetch_fn or fetch_mod.fetch
        self._validate = url_validator or validate_url
        self._extract = extractor or crawl4ai_extractor
        self._timeout = timeout
        self._max_bytes = max_bytes

    def render_url(self, query: SearchQuery) -> str:
        """Render the recipe's ``search_url_template`` for ``query``.

        The ``{query}`` placeholder is replaced with the URL-encoded search term
        (the boolean search string, falling back to the joined titles).
        """
        term = query.search_string or " ".join(query.titles)
        return self.recipe.search_url_template.replace(
            QUERY_PLACEHOLDER, quote_plus(term)
        )

    async def search(
        self,
        query: SearchQuery,
        filters: SearchFilters,
        failures: list[SourceFailure],
    ) -> list[RawListing]:
        """Fetch + extract listings for ``query``; collect one failure on error."""
        # 1. Render the search URL.
        try:
            url = self.render_url(query)
        except Exception as exc:  # noqa: BLE001 - never raise for one source
            failures.append(
                SourceFailure(
                    source=self.name,
                    reason=f"could not render search URL: {exc}",
                    kind="error",
                )
            )
            return []

        # 2. SSRF guard — fail-closed, BEFORE any fetch.
        try:
            self._validate(url)
        except SSRFError as exc:
            logger.warning("site recipe %s refused by SSRF guard: %s", self.name, exc)
            failures.append(
                SourceFailure(
                    source=self.name,
                    reason=f"blocked by SSRF guard: {exc}",
                    kind="blocked",
                )
            )
            return []

        # 3. Fetch via the declared lane.
        try:
            result = await self._fetch(
                url,
                fetch_mode=self.fetch_mode,
                timeout=self._timeout,
                max_bytes=self._max_bytes,
            )
        except FetchError as exc:
            failures.append(
                SourceFailure(
                    source=self.name, reason=str(exc), kind=classify_failure(exc)
                )
            )
            return []

        # 4. Structured extraction into the recipe schema.
        try:
            records = await self._extract(
                result.text, self.recipe.schema, self.recipe.base_url
            )
        except Exception as exc:  # noqa: BLE001 - extraction failure is recoverable
            failures.append(
                SourceFailure(
                    source=self.name,
                    reason=f"extraction failed: {exc}",
                    kind=classify_failure(exc),
                )
            )
            return []

        return self._map_records(records)

    def _map_records(self, records: list[dict[str, Any]]) -> list[RawListing]:
        """Map extracted record dicts onto :class:`RawListing`, dropping unusable rows.

        A record with no title is unusable for downstream ranking and is dropped.
        Relative ``url`` values are resolved against the recipe ``base_url``.
        Unrecognised keys are preserved in :attr:`RawListing.extra`. A record
        with no description is flagged ``partial`` (Req 7.2/8: search-page rows).
        """
        listings: list[RawListing] = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            title = _as_str(rec.get("title"))
            if not title:
                # No title → not a usable listing; drop rather than emit noise.
                continue

            raw_url = _as_str(rec.get("url"))
            url = urljoin(self.recipe.base_url, raw_url) if raw_url else ""
            description = _as_opt_str(rec.get("description"))
            extra = {k: v for k, v in rec.items() if k not in _KNOWN_FIELDS}

            listings.append(
                RawListing(
                    source=self.name,
                    title=title,
                    company=_as_str(rec.get("company")),
                    location=_as_str(rec.get("location")),
                    url=url,
                    is_remote=_as_opt_bool(rec.get("is_remote")),
                    description=description,
                    salary=_as_opt_str(rec.get("salary")),
                    partial=description is None,
                    extra=extra,
                )
            )
        return listings


__all__ = [
    "ExtractionError",
    "Extractor",
    "SiteRecipeConnector",
    "crawl4ai_extractor",
]
