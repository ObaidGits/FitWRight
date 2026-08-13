"""Anti-bot bypass fetchers for protected job sites.

Uses the 2026 best-practice tools:
- curl-cffi: TLS fingerprint impersonation (bypasses Akamai WAF)
- nodriver: Undetected Chrome via CDP (bypasses Cloudflare challenges)

These are lazy-imported so the base app works without them installed.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# =========================================================================== #
# curl-cffi based fetcher (for Akamai-protected sites like Foundit)
# =========================================================================== #

async def fetch_with_tls_impersonation(url: str, **kwargs) -> str:
    """Fetch URL using curl-cffi with Chrome TLS impersonation.
    
    Bypasses Akamai/EdgeSuite WAF by matching Chrome's exact TLS fingerprint.
    """
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        resp = await session.get(url, timeout=20, **kwargs)
        resp.raise_for_status()
        return resp.text


# =========================================================================== #
# nodriver based fetcher (for Cloudflare-protected sites like Instahyre)
# =========================================================================== #

async def fetch_with_nodriver(url: str, wait_seconds: int = 5) -> str:
    """Fetch URL using nodriver (undetected Chrome).
    
    Bypasses Cloudflare JS challenges automatically.
    Uses CDP directly — no Selenium/ChromeDriver to detect.
    """
    import nodriver as uc

    browser = await uc.start(headless=True)
    try:
        page = await browser.get(url)
        await asyncio.sleep(wait_seconds)  # let CF challenge resolve + page render
        html = await page.get_content()
        return html
    finally:
        browser.stop()


# =========================================================================== #
# Platform-specific fetchers
# =========================================================================== #

async def fetch_foundit_jobs(search_term: str, location: str = "", max_results: int = 15) -> list[dict[str, Any]]:
    """Fetch Foundit (Monster India) jobs via the CF-bypass service.

    Requires the cloudflarebypassforscraping sidecar container running.
    Set CF_BYPASS_URL env var (default: http://cf-bypass:8000).
    """
    import os
    import httpx
    from urllib.parse import quote

    cf_bypass = os.getenv("CF_BYPASS_URL", "http://cf-bypass:8000")
    target = f"https://www.foundit.in/srp/results?query={search_term}&locations={location or 'india'}"
    url = f"{cf_bypass}/html?url={quote(target, safe='')}"

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        html = resp.text

    listings = []
    # Foundit's rendered DOM: div.cardContainer > div.cardHead > div.infoSection
    #   > div#jobCardTitle.jobTitle (title)
    #   > div.companyName > p (company)
    cards = re.findall(
        r'<div id="(\d+)" class="cardContainer">(.*?)(?=<div id="\d+" class="cardContainer">|<div class="srpFooter|\Z)',
        html, re.DOTALL
    )

    for job_id, card in cards[:max_results]:
        title_m = re.search(r'class="jobTitle">\s*([^<]+?)\s*</div>', card)
        company_m = re.search(r'class="companyName"><p>\s*([^<]+?)\s*(?:</p>|<)', card)
        loc_m = re.search(r'class="[^"]*location[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
        exp_m = re.search(r'class="[^"]*experience[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)

        if title_m:
            listings.append({
                "title": title_m.group(1).strip(),
                "company": company_m.group(1).strip() if company_m else "",
                "location": loc_m.group(1).strip() if loc_m else (location or "India"),
                "url": f"https://www.foundit.in/job/{job_id}",
                "description": exp_m.group(1).strip() if exp_m else None,
            })

    return listings


async def _fetch_via_cf_bypass(target_url: str, timeout: int = 90) -> str:
    """Fetch a URL through the CF-bypass sidecar (renders JS + bypasses WAF)."""
    import os
    import httpx
    from urllib.parse import quote

    cf_bypass = os.getenv("CF_BYPASS_URL", "http://cf-bypass:8000")
    url = f"{cf_bypass}/html?url={quote(target_url, safe='')}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def fetch_instahyre_jobs(search_term: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Fetch Instahyre jobs via the CF-bypass service."""
    target = f"https://www.instahyre.com/search-jobs/?search={search_term}"
    html = await _fetch_via_cf_bypass(target)

    listings = []
    # Instahyre renders opportunity cards after JS
    cards = re.findall(
        r'class="[^"]*(?:opportunity|job-card|job-item)[^"]*"(.*?)(?=class="[^"]*(?:opportunity|job-card|job-item)|<footer|\Z)',
        html, re.DOTALL
    )
    for card in cards[:max_results]:
        title_m = re.search(r'class="[^"]*(?:job-title|designation|title)[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
        company_m = re.search(r'class="[^"]*(?:company|employer)[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
        loc_m = re.search(r'class="[^"]*location[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
        if title_m:
            listings.append({
                "title": title_m.group(1).strip(),
                "company": company_m.group(1).strip() if company_m else "",
                "location": loc_m.group(1).strip() if loc_m else "India",
                "url": target,
            })

    # Fallback: look for any job-title-like text
    if not listings:
        titles = re.findall(r'>([A-Z][A-Za-z /\-]{8,55}(?:Developer|Engineer|Manager|Architect|Analyst|Designer))<', html)
        seen = set()
        for t in titles:
            t = t.strip()
            if t not in seen:
                seen.add(t)
                listings.append({"title": t, "company": "", "location": "India", "url": target})
            if len(listings) >= max_results:
                break

    return listings


async def fetch_hirist_jobs(search_term: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Fetch Hirist jobs via the CF-bypass service."""
    slug = search_term.lower().replace(" ", "-")
    # Hirist search results page (not the /j/ single-job page)
    target = f"https://www.hirist.tech/jobs?q={search_term}"
    try:
        html = await _fetch_via_cf_bypass(target)
    except Exception:
        # Try the category page format
        target = f"https://www.hirist.tech/j/{slug}-jobs"
        html = await _fetch_via_cf_bypass(target)

    listings = []
    import json

    # Extract from __NEXT_DATA__ if present (Hirist is Next.js)
    nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if nd:
        try:
            data = json.loads(nd.group(1))
            props = data.get("props", {}).get("pageProps", {})
            for key in ("jobs", "jobList", "searchResults", "relatedJobs", "similarJobs"):
                jobs = props.get(key)
                if isinstance(jobs, list) and jobs:
                    for job in jobs[:max_results]:
                        if isinstance(job, dict) and (job.get("title") or job.get("designation")):
                            listings.append({
                                "title": job.get("title") or job.get("designation", ""),
                                "company": job.get("companyName", job.get("company", "")),
                                "location": job.get("location", "India"),
                                "url": f"https://www.hirist.tech/j/{job.get('id', slug + '-jobs')}",
                            })
                    if listings:
                        break
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # Fallback: DOM extraction from rendered page
    if not listings:
        cards = re.findall(r'class="[^"]*(?:job-?card|jobTuple|listing)[^"]*"(.*?)(?=class="[^"]*(?:job-?card|jobTuple|listing)|<footer|\Z)', html, re.DOTALL)
        for card in cards[:max_results]:
            title_m = re.search(r'class="[^"]*(?:designation|job-?title|title)[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
            company_m = re.search(r'class="[^"]*(?:comp|company)[^"]*"[^>]*>\s*([^<]+?)\s*<', card, re.I)
            if title_m:
                listings.append({
                    "title": title_m.group(1).strip(),
                    "company": company_m.group(1).strip() if company_m else "",
                    "location": "India",
                    "url": target,
                })

    # Last resort: any job-title-shaped text
    if not listings:
        titles = re.findall(r'>([A-Z][A-Za-z /\-]{8,55}(?:Developer|Engineer|Architect|Lead))<', html)
        seen = set()
        for t in titles:
            t = t.strip()
            if t not in seen:
                seen.add(t)
                listings.append({"title": t, "company": "", "location": "India", "url": target})
            if len(listings) >= max_results:
                break

    return listings


async def fetch_ycombinator_jobs(search_term: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Fetch YC startup jobs using nodriver (client-rendered site)."""
    url = f"https://www.ycombinator.com/companies?query={search_term}"
    
    html = await _fetch_via_cf_bypass(url)
    
    listings = []
    import json

    # YC renders company cards after JS loads
    # Look for company data in rendered HTML
    cards = re.findall(r'<a[^>]*href="(/companies/[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL)
    for href, card_content in cards[:max_results]:
        # Extract company name (usually the first text node)
        name_m = re.search(r'>([^<]{3,50})<', card_content)
        desc_m = re.search(r'class="[^"]*[Dd]escription[^"]*"[^>]*>([^<]+)', card_content)
        if name_m:
            listings.append({
                "title": f"Roles at {name_m.group(1).strip()}",
                "company": name_m.group(1).strip(),
                "location": "",
                "url": f"https://www.ycombinator.com{href}",
                "description": desc_m.group(1).strip() if desc_m else None,
            })

    # Fallback: __NEXT_DATA__
    if not listings:
        nd = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if nd:
            try:
                data = json.loads(nd.group(1))
                companies = data.get("props", {}).get("pageProps", {}).get("companies", [])
                for co in (companies or [])[:max_results]:
                    if isinstance(co, dict):
                        listings.append({
                            "title": f"Roles at {co.get('name', '?')}",
                            "company": co.get("name", ""),
                            "location": co.get("location", ""),
                            "url": co.get("website", url),
                            "description": co.get("one_liner", ""),
                        })
            except (json.JSONDecodeError, KeyError):
                pass

    return listings
