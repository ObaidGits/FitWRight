"""Country extraction from a job's free-text location string.

Deliberately not a geocoding service and not an LLM call (auto-apply-brain
Phase 1, .kiro/specs/auto-apply-brain/tasks.md 1.3: "job-fact resolution WITHOUT
an LLM first"). Job boards write location as a short human string - "Bengaluru,
India", "Remote - US", "London, UK" - and a fixed lookup resolves the large
majority of these for free, instantly, with no network call. Only what a lookup
table cannot resolve should ever reach an LLM extractor (Phase 2), and this
module's whole job is to shrink that remainder.

Matching is deliberately conservative: a location string that reads as
ambiguous or fully remote-with-no-country returns None rather than a guess,
because the caller (app.eligibility_rules) treats None as "cannot compute the
conditional answer" and grades the field yellow rather than silently guessing
a knockout answer.
"""
from __future__ import annotations

import re

# Aliases -> ISO 3166-1 alpha-2. Deliberately small: the countries FitWright's
# own user base actually applies to and from, not an exhaustive gazetteer. Add
# entries as real job postings surface a miss (see Admin -> AI spend once
# Phase 2 ships - a location that falls through to the LLM extractor repeatedly
# is the signal to add it here for free).
_COUNTRY_ALIASES: dict[str, str] = {
    "india": "IN",
    "bharat": "IN",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "u.s.a.": "US",
    "u.s.": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "u.k.": "GB",
    "england": "GB",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "singapore": "SG",
    "netherlands": "NL",
    "the netherlands": "NL",
    "ireland": "IE",
    "spain": "SP",
    "italy": "IT",
    "japan": "JP",
    "united arab emirates": "AE",
    "uae": "AE",
    "poland": "PL",
    "brazil": "BR",
    "mexico": "MX",
    "new zealand": "NZ",
}

# US and India both have city/state abbreviations common enough in job-board
# location strings ("Austin, TX", "Pune, MH") to be worth a direct table rather
# than a full geocoder.
_US_STATE_ABBR = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS "
    "MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV "
    "WI WY DC".split()
)
_IN_STATE_ABBR = frozenset(
    "AP AR AS BR CG GA GJ HR HP JK JH KA KL MP MH MN ML MZ NL OD PB RJ SK TN "
    "TS TR UP UK WB DL".split()
)

_REMOTE_ONLY = re.compile(r"^\s*(remote|anywhere|worldwide|global)\s*$", re.IGNORECASE)


def country_from_location(location: str | None) -> str | None:
    """Best-effort ISO 3166-1 alpha-2 country code from a job's location string.

    Returns None on anything ambiguous, including a bare "Remote" with no
    country qualifier - "Remote" alone says nothing about which country's
    sponsorship rules apply, and a caller must not treat that silence as "same
    country as the candidate".
    """
    if not location or not location.strip():
        return None
    text = location.strip()
    if _REMOTE_ONLY.match(text):
        return None

    # Split on the usual separators job boards use between city/state/country
    # ("Pune, Maharashtra, India" / "Remote - US" / "London | UK" /
    # "Remote (India)"). Parentheses are stripped rather than treated as a
    # nested boundary - "(India)" and "India" must match the same alias.
    text = text.replace("(", ",").replace(")", "")
    parts = [p.strip() for p in re.split(r"[,|/]|(?:\s-\s)", text) if p.strip()]
    if not parts:
        return None

    # Prefer the trailing part first (city, ..., COUNTRY is the dominant
    # convention), then fall back to scanning every part for a state/country
    # match, so "US - Remote (California)" still resolves via any position.
    ordered = [parts[-1], *parts[:-1]]
    for part in ordered:
        code = _match_country_token(part)
        if code:
            return code
    return None


def _match_country_token(token: str) -> str | None:
    cleaned = re.sub(r"[^\w\s.]", "", token).strip()
    lowered = cleaned.lower()
    if lowered in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[lowered]
    upper = cleaned.upper()
    if upper in _US_STATE_ABBR:
        return "US"
    if upper in _IN_STATE_ABBR:
        return "IN"
    return None
