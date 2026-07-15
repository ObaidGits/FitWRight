"""Hydration JSON extractor (§5 Level 3 of enhancement plan).

Extracts job data from framework hydration state embedded in static HTML:
- Next.js: <script id="__NEXT_DATA__" type="application/json">
- Nuxt: window.__NUXT__
- Remix: window.__remixContext

Zero additional network cost — parses from already-fetched HTML.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = ["extract_hydration"]

VERSION = "1.0.0"

# Patterns to find hydration state in script tags
_NEXT_DATA_RE = re.compile(r'<script\s+id="__NEXT_DATA__"\s+type="application/json"[^>]*>(.*?)</script>', re.S)
_NUXT_RE = re.compile(r'window\.__NUXT__\s*=\s*(\{.*?\});?\s*(?:</script>|$)', re.S)
_REMIX_RE = re.compile(r'window\.__remixContext\s*=\s*(\{.*?\});?\s*(?:</script>|$)', re.S)


def extract_hydration(html: str) -> ExtractionResult | None:
    """Extract job data from framework hydration state.

    Returns ExtractionResult if job-like content found, None otherwise.
    """
    if not html:
        return None

    # Try Next.js first (most common)
    result = _try_nextjs(html)
    if result:
        return result

    # Try Nuxt
    result = _try_nuxt(html)
    if result:
        return result

    return None


def _try_nextjs(html: str) -> ExtractionResult | None:
    """Parse __NEXT_DATA__ for job content."""
    match = _NEXT_DATA_RE.search(html)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return None

    # Navigate Next.js page props structure
    props = data.get("props", {}).get("pageProps", {})
    if not props:
        return None

    # Look for job-like objects in pageProps
    return _search_for_job(props, "hydration_json", "nextjs:props.pageProps")


def _try_nuxt(html: str) -> ExtractionResult | None:
    """Parse window.__NUXT__ for job content."""
    match = _NUXT_RE.search(html)
    if not match:
        return None

    try:
        # Nuxt state is often not valid JSON (uses JS object syntax)
        # Try direct parse first
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        return None

    return _search_for_job(data, "hydration_json", "nuxt:state")


def _search_for_job(data: dict, source: str, prefix: str, depth: int = 0) -> ExtractionResult | None:
    """Recursively search a dict for job-like content.

    Looks for objects with title + description (or similar patterns).
    Max depth 5 to avoid infinite recursion on circular references.
    """
    if depth > 5:
        return None

    # Direct hit: object has title + description-like field
    title = data.get("title") or data.get("jobTitle") or data.get("name", "")
    description = (
        data.get("description") or data.get("jobDescription") or
        data.get("content") or data.get("body") or data.get("descriptionHtml") or ""
    )

    if title and description and len(str(description)) >= 200:
        # Strip HTML if present
        desc_str = str(description)
        if "<" in desc_str:
            desc_str = re.sub(r"<[^>]+>", "\n", desc_str)
            desc_str = re.sub(r"\n{3,}", "\n\n", desc_str).strip()

        content = f"{title}\n\n{desc_str}"
        company = data.get("company", "") or data.get("companyName", "") or data.get("organizationName", "")
        location = data.get("location", "") or data.get("locationName", "")

        return ExtractionResult(
            content=content,
            title=FieldProvenance(
                value=str(title), source="hydration_json", confidence=85,
                extractor_version=VERSION, raw_location=f"{prefix}.title",
            ),
            company=FieldProvenance(
                value=str(company), source="hydration_json", confidence=80,
                extractor_version=VERSION, raw_location=f"{prefix}.company",
            ) if company else None,
            location=FieldProvenance(
                value=str(location), source="hydration_json", confidence=75,
                extractor_version=VERSION, raw_location=f"{prefix}.location",
            ) if location else None,
            confidence=ConfidenceResult(
                level="HIGH", score=80,
                reasons=["Framework hydration state", f"Source: {prefix}"],
            ),
            source="hydration_json",
        )

    # Recursive search in nested objects
    for key, value in data.items():
        if isinstance(value, dict) and depth < 5:
            result = _search_for_job(value, source, f"{prefix}.{key}", depth + 1)
            if result:
                return result
        elif isinstance(value, list):
            for i, item in enumerate(value[:10]):  # Cap list iteration
                if isinstance(item, dict):
                    result = _search_for_job(item, source, f"{prefix}.{key}[{i}]", depth + 1)
                    if result:
                        return result

    return None
