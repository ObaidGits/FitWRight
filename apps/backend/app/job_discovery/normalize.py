"""Normalize raw connector output into canonical listings, then dedupe.

Connectors emit :class:`~app.job_discovery.connectors.base.RawListing` --
loosely-typed, source-shaped rows. This module imports that **canonical**
``RawListing`` (never redefines it) so the pipeline has exactly one raw type.
:func:`to_job_listing` converts each row into a canonical
:class:`~app.job_discovery.models.JobListing` (trimmed, whitespace-collapsed,
fingerprinted), and :func:`dedupe` collapses duplicates that share a
fingerprint, **keeping the richest record**.

"Richest" means the record carrying the most information: a longer description
wins first (it is the strongest signal for downstream ranking), then the record
with more populated fields. Ties keep the first record seen, so the result is
deterministic and stable in input order.

The raw ``partial`` flag marks a row scraped without a full JD body; by the
connector contract (see ``connectors/base``) such a row also carries no
``description``. The canonical ``JobListing`` has no ``partial`` field -- the
ranker re-derives partial-ness from description presence
(:func:`app.job_discovery.ranker.listing_jd_text`) -- so partial-ness is
preserved through the description, not a separate flag. Dedup honors this too:
a full listing (non-empty description) always outranks a partial duplicate.
The raw ``extra`` bag is source-specific debug data with no canonical home and
is intentionally not carried onto ``JobListing``.

Fingerprinting lives in :mod:`app.jd.fingerprint`; the canonical shapes live in
:mod:`app.job_discovery.models`.

Requirements: 6.1.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.jd.fingerprint import content_fingerprint as _content_fingerprint


def _normalize_url_for_fingerprint(url: str) -> str:
    """Strip tracking params and trailing slashes so cosmetically different URLs
    pointing to the same job produce the same fingerprint."""
    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    parsed = urlparse(url.strip())
    # Strip known tracking query params
    params = parse_qs(parsed.query, keep_blank_values=True)
    cleaned = {
        k: v for k, v in params.items()
        if not k.startswith(("utm_", "ref", "fbclid", "gclid", "mc_"))
        and k not in ("source", "medium", "campaign")
    }
    clean_query = urlencode(cleaned, doseq=True)
    # Strip trailing slash from path
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, clean_query, ""))


def fingerprint(title: str, company: str, location: str, url: str) -> str:
    """Compute a fingerprint using the real content_fingerprint fn.

    URLs are normalized (tracking params stripped, trailing slash removed) so
    cosmetically different links to the same job posting produce the same hash.
    """
    normalized_url = _normalize_url_for_fingerprint(url) if url else ""
    return _content_fingerprint(title, company, location, normalized_url)
from app.job_discovery.connectors.base import RawListing
from app.job_discovery.models import JobListing

_WS = re.compile(r"\s+")


def _clean(value: str | None) -> str:
    """Trim and collapse internal whitespace; ``None`` becomes ``""``."""
    if not value:
        return ""
    return _WS.sub(" ", value).strip()


def _clean_opt(value: str | None) -> str | None:
    """Like :func:`_clean` but keeps an absent/blank value as ``None``."""
    return _clean(value) or None


def to_job_listing(raw: RawListing) -> JobListing:
    """Convert a :class:`RawListing` into a canonical :class:`JobListing`.

    The required string fields (``title``/``company``/``location``/``url``) are
    coerced to ``""`` when absent so the canonical shape stays fully typed. The
    fingerprint is computed from the *cleaned* identity fields, so cosmetic
    input noise never forks it.
    """
    title = _clean(raw.title)
    company = _clean(raw.company)
    location = _clean(raw.location)
    url = (raw.url or "").strip()
    return JobListing(
        source=raw.source,
        title=title,
        company=company,
        location=location,
        url=url,
        is_remote=raw.is_remote,
        description=_clean_opt(raw.description),
        posted_at=raw.posted_at,
        salary=_clean_opt(raw.salary),
        fingerprint=fingerprint(title, company, location, url),
    )


def _richness(listing: JobListing) -> tuple[int, int]:
    """Comparable richness key for a listing; higher is richer.

    Ranks by description length first (the single most valuable field for the
    ranker), then by the count of populated fields as a tie-breaker.
    """
    desc_len = len(listing.description or "")
    populated = sum(
        1
        for value in (
            listing.description,
            listing.salary,
            listing.company,
            listing.location,
            listing.is_remote,
            listing.posted_at,
        )
        if value not in (None, "")
    )
    return (desc_len, populated)


def dedupe(listings: Iterable[JobListing]) -> list[JobListing]:
    """Collapse listings sharing a fingerprint, keeping the richest record.

    Output order follows the first appearance of each fingerprint (stable); on
    a richness tie the earliest-seen record wins.
    """
    best: dict[str, JobListing] = {}
    order: list[str] = []
    for listing in listings:
        fp = listing.fingerprint
        existing = best.get(fp)
        if existing is None:
            best[fp] = listing
            order.append(fp)
        elif _richness(listing) > _richness(existing):
            best[fp] = listing
    return [best[fp] for fp in order]


def normalize(raws: Iterable[RawListing]) -> list[JobListing]:
    """Convert raw connector rows to canonical listings and deduplicate."""
    return dedupe(to_job_listing(raw) for raw in raws)


__all__ = ["dedupe", "normalize", "to_job_listing"]
