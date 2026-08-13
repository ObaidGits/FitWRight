"""Additional platform connectors for Job Discovery — FIXED version.

Based on live inspection of each site's actual HTML/API structure.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.job_discovery.connectors.base import (
    Connector,
    RawListing,
    classify_failure,
)
from app.job_discovery.models import (
    FetchMode,
    SearchFilters,
    SearchQuery,
    SourceFailure,
)

logger = logging.getLogger(__name__)

__all__ = ["ExtraPlatformConnector", "EXTRA_PLATFORMS"]

EXTRA_PLATFORMS = {
    "remotive", "weworkremotely", "simplyhired", "hirist",
    "foundit", "wellfound", "ycombinator", "instahyre",
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# We Work Remotely publishes no searchable API - `?search=` on the RSS endpoint
# is silently ignored - so role coverage comes from fanning out over the
# category feeds. The firehose feed stays in the list because it carries the
# newest postings across categories that have no dedicated feed.
_WWR_FEEDS = (
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss",
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
    "https://weworkremotely.com/categories/remote-product-jobs.rss",
    "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    "https://weworkremotely.com/categories/remote-customer-support-jobs.rss",
    "https://weworkremotely.com/remote-jobs.rss",
)


async def _fetch_impersonated(url: str, timeout: int = 45) -> str:
    """GET *url* with a real browser's TLS fingerprint, falling back to httpx.

    Some boards (SimplyHired) answer a plain ``httpx`` request with ``403``
    purely on the TLS/JA3 fingerprint - the request never reaches their
    application. ``curl_cffi`` impersonates Chrome's handshake and the same URL
    returns ``200``.

    ``curl_cffi`` ships in the optional ``job-discovery`` extra, so it is
    imported lazily and the plain-``httpx`` path remains as a fallback: without
    the extra the caller still gets whatever the board is willing to serve
    (usually the 403, surfaced as a recorded SourceFailure) rather than an
    ImportError crash.
    """
    try:
        from curl_cffi.requests import AsyncSession  # type: ignore[import-not-found]
    except ImportError:
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, headers=_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async with AsyncSession() as session:
        resp = await session.get(url, impersonate="chrome124", timeout=timeout)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{resp.status_code}' for url '{url}'",
                request=httpx.Request("GET", url),
                response=httpx.Response(resp.status_code),
            )
        return resp.text


def _testid_text(card: str, testid: str) -> str:
    """Return the visible text of the element carrying ``data-testid=<testid>``.

    Tolerates the wrapper element being an ``h2``/``span``/``p``/``div`` and any
    nesting inside it (SimplyHired wraps its titles in a button), by stripping
    tags from the captured span and collapsing whitespace.
    """
    match = re.search(
        rf'data-testid="{testid}"[^>]*>(.*?)</(?:h2|span|p|div|a)>', card, re.DOTALL
    )
    if not match:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", match.group(1))).strip()


class ExtraPlatformConnector:
    """Connector for non-JobSpy platforms."""

    name: str = "extra"
    fetch_mode: FetchMode = "http"

    def __init__(self, sites: list[str]) -> None:
        self.sites = [s for s in sites if s in EXTRA_PLATFORMS]

    async def search(
        self,
        query: SearchQuery,
        filters: SearchFilters,
        failures: list[SourceFailure],
    ) -> list[RawListing]:
        if not self.sites:
            return []

        all_listings: list[RawListing] = []
        for site in self.sites:
            try:
                listings = await self._fetch_site(site, query, filters)
                all_listings.extend(listings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures.append(SourceFailure(
                    source=site, reason=str(exc)[:200], kind=classify_failure(exc),
                ))
        return all_listings

    async def _fetch_site(self, site: str, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        fetchers = {
            "remotive": self._fetch_remotive,
            "weworkremotely": self._fetch_weworkremotely,
            "simplyhired": self._fetch_simplyhired,
            "hirist": self._fetch_hirist,
            "foundit": self._fetch_foundit,
            "wellfound": self._fetch_wellfound,
            "ycombinator": self._fetch_ycombinator,
            "instahyre": self._fetch_instahyre,
        }
        fetcher = fetchers.get(site)
        return await fetcher(query, filters) if fetcher else []

    # ------------------------------------------------------------------ #
    # Remotive — public JSON API (most reliable)
    # ------------------------------------------------------------------ #
    async def _fetch_remotive(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        url = "https://remotive.com/api/remote-jobs"
        params: dict[str, Any] = {"limit": filters.results_wanted or 20}
        if search_term:
            params["search"] = search_term

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        listings = []
        for job in data.get("jobs", [])[:filters.results_wanted or 20]:
            listings.append(RawListing(
                source="remotive",
                title=job.get("title", ""),
                company=job.get("company_name", ""),
                location=job.get("candidate_required_location", "Remote"),
                url=job.get("url", ""),
                is_remote=True,
                description=job.get("description", ""),
                salary=job.get("salary", None),
            ))
        return listings

    # ------------------------------------------------------------------ #
    # We Work Remotely — 403 with plain httpx, use Accept header trick
    # ------------------------------------------------------------------ #
    async def _fetch_weworkremotely(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Fetch WWR jobs from its public category RSS feeds.

        WWR blocks scrapers and its RSS endpoint ignores a ``search`` parameter,
        so there is no server-side search: the only way to cover a role is to
        pull the category feeds and match locally. ``remote-jobs.rss`` alone is
        the newest ~100 postings across *every* category, which usually contains
        no match for a specific role - hence the per-category fan-out.

        Matching is token-based. An exact-phrase test on the title discards
        almost everything, because WWR titles read ``"Proxify AB: Senior
        Fullstack Developer (Python)"`` rather than ``"python developer"``. A
        listing is kept when every search token appears in the title, and the
        looser any-token pass runs only when the strict pass finds nothing; the
        ranker scores relevance properly downstream.
        """
        search_term = query.search_string or " ".join(query.titles)
        wanted = filters.results_wanted or 15

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            responses = await asyncio.gather(
                *(client.get(feed) for feed in _WWR_FEEDS), return_exceptions=True
            )

        # Parse every feed that came back, de-duplicating by job URL: the
        # category feeds and the firehose feed overlap.
        by_url: dict[str, tuple[str, str]] = {}
        for resp in responses:
            if isinstance(resp, BaseException) or resp.status_code != 200:
                continue
            for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                title_m = re.search(r"<title>(.*?)</title>", item)
                link_m = re.search(r"<link>(.*?)</link>", item)
                company_m = re.search(r"<company><!\[CDATA\[(.*?)\]\]></company>", item)
                if not company_m:
                    company_m = re.search(r"<dc:creator><!\[CDATA\[(.*?)\]\]></dc:creator>", item)
                title = title_m.group(1) if title_m else ""
                job_url = link_m.group(1) if link_m else ""
                if not title or not job_url or job_url in by_url:
                    continue
                by_url[job_url] = (title, company_m.group(1) if company_m else "")

        tokens = [t for t in search_term.lower().split() if t]
        if tokens:
            strict = [
                (u, t, c) for u, (t, c) in by_url.items()
                if all(tok in t.lower() for tok in tokens)
            ]
            matches = strict or [
                (u, t, c) for u, (t, c) in by_url.items()
                if any(tok in t.lower() for tok in tokens)
            ]
        else:
            matches = [(u, t, c) for u, (t, c) in by_url.items()]

        return [
            RawListing(
                source="weworkremotely",
                title=title,
                company=company,
                location="Remote",
                url=job_url,
                is_remote=True,
                description=None,
                partial=True,
            )
            for job_url, title, company in matches[:wanted]
        ]

    # ------------------------------------------------------------------ #
    # SimplyHired — Chakra UI, data-testid selectors
    # ------------------------------------------------------------------ #
    async def _fetch_simplyhired(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        location = filters.location or ""
        url = (
            "https://www.simplyhired.com/search"
            f"?q={quote_plus(search_term)}&l={quote_plus(location)}"
        )

        html = await _fetch_impersonated(url)

        # Chakra emits a huge inline <style> block inside every card, which is
        # what defeated the previous title regexes. Drop the styles first and
        # the remaining markup is a clean, addressable card list.
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

        listings: list[RawListing] = []
        cards = [
            card
            for card in re.split(r'(?=<div[^>]*data-testid="searchSerpJob")', html)
            if 'data-testid="searchSerpJobTitle"' in card
        ]

        for card in cards[: filters.results_wanted or 15]:
            title = _testid_text(card, "searchSerpJobTitle")
            if not title:
                continue
            card_location = _testid_text(card, "searchSerpJobLocation") or location
            # Each card carries the canonical job id; the visible anchor is a
            # button, so build the URL from the key rather than an href.
            key_m = re.search(r'data-jobkey="([^"]+)"', card)
            job_url = (
                f"https://www.simplyhired.com/job/{key_m.group(1)}" if key_m else ""
            )
            listings.append(RawListing(
                source="simplyhired",
                title=title,
                company=_testid_text(card, "companyName"),
                location=card_location,
                url=job_url,
                is_remote="remote" in card_location.lower(),
                partial=True,
            ))
        return listings

    # ------------------------------------------------------------------ #
    # Hirist — Next.js app, data in pageProps
    # ------------------------------------------------------------------ #
    async def _fetch_hirist(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Hirist — browser-rendered (Next.js SPA)."""
        search_term = query.search_string or " ".join(query.titles)
        try:
            from app.job_discovery.connectors.browser_fetch import fetch_hirist_jobs
            jobs = await fetch_hirist_jobs(search_term, filters.results_wanted or 10)
            return [RawListing(source="hirist", title=j["title"], company=j.get("company",""),
                    location=j.get("location","India"), url=j.get("url",""), partial=True) for j in jobs]
        except ImportError:
            logger.warning("Patchright not installed — hirist connector unavailable")
            return []
        except Exception as exc:
            logger.warning("Hirist browser fetch failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Remotive — public JSON API (most reliable)
    # ------------------------------------------------------------------ #
    async def _fetch_remotive(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        url = "https://remotive.com/api/remote-jobs"
        params: dict[str, Any] = {"limit": filters.results_wanted or 20}
        if search_term:
            params["search"] = search_term

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        listings = []
        for job in data.get("jobs", [])[:filters.results_wanted or 20]:
            listings.append(RawListing(
                source="remotive",
                title=job.get("title", ""),
                company=job.get("company_name", ""),
                location=job.get("candidate_required_location", "Remote"),
                url=job.get("url", ""),
                is_remote=True,
                description=job.get("description", ""),
                salary=job.get("salary", None),
            ))
        return listings

    # ------------------------------------------------------------------ #
    # We Work Remotely — 403 with plain httpx, use Accept header trick
    # ------------------------------------------------------------------ #
    async def _fetch_weworkremotely(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Fetch WWR jobs from its public category RSS feeds.

        WWR blocks scrapers and its RSS endpoint ignores a ``search`` parameter,
        so there is no server-side search: the only way to cover a role is to
        pull the category feeds and match locally. ``remote-jobs.rss`` alone is
        the newest ~100 postings across *every* category, which usually contains
        no match for a specific role - hence the per-category fan-out.

        Matching is token-based. An exact-phrase test on the title discards
        almost everything, because WWR titles read ``"Proxify AB: Senior
        Fullstack Developer (Python)"`` rather than ``"python developer"``. A
        listing is kept when every search token appears in the title, and the
        looser any-token pass runs only when the strict pass finds nothing; the
        ranker scores relevance properly downstream.
        """
        search_term = query.search_string or " ".join(query.titles)
        wanted = filters.results_wanted or 15

        async with httpx.AsyncClient(timeout=30, headers=_HEADERS) as client:
            responses = await asyncio.gather(
                *(client.get(feed) for feed in _WWR_FEEDS), return_exceptions=True
            )

        # Parse every feed that came back, de-duplicating by job URL: the
        # category feeds and the firehose feed overlap.
        by_url: dict[str, tuple[str, str]] = {}
        for resp in responses:
            if isinstance(resp, BaseException) or resp.status_code != 200:
                continue
            for item in re.findall(r"<item>(.*?)</item>", resp.text, re.DOTALL):
                title_m = re.search(r"<title>(.*?)</title>", item)
                link_m = re.search(r"<link>(.*?)</link>", item)
                company_m = re.search(r"<company><!\[CDATA\[(.*?)\]\]></company>", item)
                if not company_m:
                    company_m = re.search(r"<dc:creator><!\[CDATA\[(.*?)\]\]></dc:creator>", item)
                title = title_m.group(1) if title_m else ""
                job_url = link_m.group(1) if link_m else ""
                if not title or not job_url or job_url in by_url:
                    continue
                by_url[job_url] = (title, company_m.group(1) if company_m else "")

        tokens = [t for t in search_term.lower().split() if t]
        if tokens:
            strict = [
                (u, t, c) for u, (t, c) in by_url.items()
                if all(tok in t.lower() for tok in tokens)
            ]
            matches = strict or [
                (u, t, c) for u, (t, c) in by_url.items()
                if any(tok in t.lower() for tok in tokens)
            ]
        else:
            matches = [(u, t, c) for u, (t, c) in by_url.items()]

        return [
            RawListing(
                source="weworkremotely",
                title=title,
                company=company,
                location="Remote",
                url=job_url,
                is_remote=True,
                description=None,
                partial=True,
            )
            for job_url, title, company in matches[:wanted]
        ]

    # ------------------------------------------------------------------ #
    # SimplyHired — Chakra UI, data-testid selectors
    # ------------------------------------------------------------------ #
    async def _fetch_simplyhired(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        location = filters.location or ""
        url = (
            "https://www.simplyhired.com/search"
            f"?q={quote_plus(search_term)}&l={quote_plus(location)}"
        )

        html = await _fetch_impersonated(url)

        # Chakra emits a huge inline <style> block inside every card, which is
        # what defeated the previous title regexes. Drop the styles first and
        # the remaining markup is a clean, addressable card list.
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)

        listings: list[RawListing] = []
        cards = [
            card
            for card in re.split(r'(?=<div[^>]*data-testid="searchSerpJob")', html)
            if 'data-testid="searchSerpJobTitle"' in card
        ]

        for card in cards[: filters.results_wanted or 15]:
            title = _testid_text(card, "searchSerpJobTitle")
            if not title:
                continue
            card_location = _testid_text(card, "searchSerpJobLocation") or location
            # Each card carries the canonical job id; the visible anchor is a
            # button, so build the URL from the key rather than an href.
            key_m = re.search(r'data-jobkey="([^"]+)"', card)
            job_url = (
                f"https://www.simplyhired.com/job/{key_m.group(1)}" if key_m else ""
            )
            listings.append(RawListing(
                source="simplyhired",
                title=title,
                company=_testid_text(card, "companyName"),
                location=card_location,
                url=job_url,
                is_remote="remote" in card_location.lower(),
                partial=True,
            ))
        return listings

    # ------------------------------------------------------------------ #
    # Hirist — Next.js app, data in pageProps
    # ------------------------------------------------------------------ #
    async def _fetch_hirist(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        # Hirist URL pattern: /j/{search}-jobs
        slug = search_term.lower().replace(" ", "-")
        url = f"https://www.hirist.tech/j/{slug}-jobs"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        listings = []
        # Hirist is Next.js — extract __NEXT_DATA__
        nd_match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nd_match:
            import json
            try:
                data = json.loads(nd_match.group(1))
                props = data.get("props", {}).get("pageProps", {})
                # jobMandateInfo contains the job details
                job_info = props.get("jobMandateInfo", {})
                if isinstance(job_info, dict) and job_info.get("title"):
                    listings.append(RawListing(
                        source="hirist",
                        title=job_info.get("title", ""),
                        company=job_info.get("companyName", ""),
                        location=job_info.get("location", "India"),
                        url=f"https://www.hirist.tech/j/{slug}-jobs",
                        is_remote=None,
                        description=job_info.get("description", None),
                    ))
            except (json.JSONDecodeError, KeyError):
                pass

        # Also try to find related jobs in the HTML
        related = re.findall(r'"title":"([^"]{5,80})"[^}]*"companyName":"([^"]*)"', html)
        for title, company in related[:filters.results_wanted or 10]:
            if not any(l.title == title for l in listings):
                listings.append(RawListing(
                    source="hirist",
                    title=title,
                    company=company,
                    location="India",
                    url=f"https://www.hirist.tech/j/{title.lower().replace(' ','-')}-jobs",
                    is_remote=None,
                    partial=True,
                ))
        return listings

    # ------------------------------------------------------------------ #
    # Foundit (Monster India) — Next.js SPA, extract from page JSON
    # ------------------------------------------------------------------ #
    async def _fetch_foundit(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Foundit (Monster India) — browser-rendered SPA."""
        search_term = query.search_string or " ".join(query.titles)
        location = filters.location or ""
        try:
            from app.job_discovery.connectors.browser_fetch import fetch_foundit_jobs
            jobs = await fetch_foundit_jobs(search_term, location, filters.results_wanted or 15)
            return [RawListing(source="foundit", title=j["title"], company=j.get("company",""),
                    location=j.get("location",location or "India"), url=j.get("url",""),
                    is_remote="remote" in j.get("location","").lower(), partial=True) for j in jobs]
        except ImportError:
            logger.warning("Patchright not installed — foundit connector unavailable")
            return []
        except Exception as exc:
            logger.warning("Foundit browser fetch failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Wellfound (AngelList) — Apollo GraphQL SSR data
    # ------------------------------------------------------------------ #
    async def _fetch_wellfound(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        search_term = query.search_string or " ".join(query.titles)
        # Use role-based URL which has data
        slug = search_term.lower().replace(" ", "-")
        url = f"https://wellfound.com/role/r/{slug}"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=_HEADERS) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        listings = []
        import json
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))
                apollo = data.get("props", {}).get("pageProps", {}).get("apolloState", {})
                # Extract job-like objects from Apollo cache
                for key, val in apollo.items():
                    if not isinstance(val, dict):
                        continue
                    typename = val.get("__typename", "")
                    if typename in ("StartupJobPosting", "JobListing", "StartupResult"):
                        title = val.get("title") or val.get("name", "")
                        if title and len(title) > 3:
                            listings.append(RawListing(
                                source="wellfound",
                                title=title,
                                company=val.get("companyName", val.get("startup", {}).get("name", "") if isinstance(val.get("startup"), dict) else ""),
                                location=val.get("locationNames", val.get("location", "")) if isinstance(val.get("locationNames"), str) else "",
                                url=f"https://wellfound.com/role/r/{slug}",
                                is_remote=val.get("remote", None),
                                description=val.get("description", None),
                                partial=not val.get("description"),
                            ))
                    if len(listings) >= (filters.results_wanted or 10):
                        break
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Fallback: regex for job titles in HTML
        if not listings:
            titles = re.findall(r'"title":"([^"]{5,60})"', html)
            seen = set()
            for t in titles:
                if t not in seen and not t.startswith(("http", "{")):
                    seen.add(t)
                    listings.append(RawListing(
                        source="wellfound", title=t, company="", location="",
                        url=f"https://wellfound.com/role/r/{slug}",
                        is_remote=None, partial=True,
                    ))
                if len(listings) >= (filters.results_wanted or 10):
                    break
        return listings

    # ------------------------------------------------------------------ #
    # Y Combinator Work at a Startup — try Algolia search (public)
    # ------------------------------------------------------------------ #
    async def _fetch_ycombinator(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Y Combinator — browser-rendered directory."""
        search_term = query.search_string or " ".join(query.titles)
        try:
            from app.job_discovery.connectors.browser_fetch import fetch_ycombinator_jobs
            jobs = await fetch_ycombinator_jobs(search_term, filters.results_wanted or 10)
            return [RawListing(source="ycombinator", title=j["title"], company=j.get("company",""),
                    location=j.get("location",""), url=j.get("url",""),
                    description=j.get("description"), partial=True) for j in jobs]
        except ImportError:
            logger.warning("Patchright not installed — ycombinator connector unavailable")
            return []
        except Exception as exc:
            logger.warning("YC browser fetch failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    # Instahyre — blocks scrapers, use Googlebot UA trick
    # ------------------------------------------------------------------ #
    async def _fetch_instahyre(self, query: SearchQuery, filters: SearchFilters) -> list[RawListing]:
        """Instahyre — browser-rendered, blocks HTTP scrapers."""
        search_term = query.search_string or " ".join(query.titles)
        try:
            from app.job_discovery.connectors.browser_fetch import fetch_instahyre_jobs
            jobs = await fetch_instahyre_jobs(search_term, filters.results_wanted or 10)
            return [RawListing(source="instahyre", title=j["title"], company=j.get("company",""),
                    location=j.get("location","India"), url=j.get("url",""), partial=True) for j in jobs]
        except ImportError:
            logger.warning("Patchright not installed — instahyre connector unavailable")
            return []
        except Exception as exc:
            logger.warning("Instahyre browser fetch failed: %s", exc)
            return []
