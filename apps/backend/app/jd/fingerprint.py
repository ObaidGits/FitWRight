"""Content fingerprinting for JD extraction (§22 of enhancement plan).

Two complementary primitives, zero external dependencies:

1. ``content_fingerprint`` — a stable SHA-256 over the identifying fields plus a
   *slice* of the description (chars 200-700). The first ~200 chars of most JDs
   are identical company boilerplate ("About Company X, we are a leading..."), so
   using chars 200-700 captures the unique role content while avoiding
   intro-collision for different roles at the same company.

2. ``simhash`` + ``hamming_similarity`` — a 64-bit SimHash for near-duplicate
   detection. Two documents with SimHash Hamming similarity > 0.85 are "likely
   the same job" (e.g. the same posting mirrored on two boards).
"""

from __future__ import annotations

import hashlib
import re

__all__ = [
    "content_fingerprint",
    "simhash",
    "hamming_similarity",
    "is_near_duplicate",
    "NEAR_DUP_THRESHOLD",
]

NEAR_DUP_THRESHOLD = 0.85

_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(s: str | None) -> str:
    """Lowercase + collapse whitespace for stable hashing."""
    if not s:
        return ""
    return _WS_RE.sub(" ", s.strip().lower())


def content_fingerprint(
    title: str | None,
    company: str | None,
    location: str | None,
    description: str | None,
) -> str:
    """SHA-256 fingerprint over identifying fields + description chars 200-700.

    For descriptions shorter than 700 chars, uses the full description.
    """
    desc = _normalize(description)
    if len(desc) >= 700:
        desc_slice = desc[200:700]
    elif len(desc) > 200:
        desc_slice = desc[200:]
    else:
        desc_slice = desc  # too short to skip the intro — use it all

    payload = "\x1f".join((
        _normalize(title),
        _normalize(company),
        _normalize(location),
        desc_slice,
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def simhash(text: str, bits: int = 64) -> int:
    """Compute a ``bits``-wide SimHash of ``text``.

    Uses token shingles weighted by frequency. Deterministic (blake2b of each
    token, no randomness), so hashes are stable across processes/restarts.
    """
    tokens = _tokens(text)
    if not tokens:
        return 0

    # Weight = term frequency.
    weights: dict[str, int] = {}
    for tok in tokens:
        weights[tok] = weights.get(tok, 0) + 1

    v = [0] * bits
    for tok, w in weights.items():
        h = int.from_bytes(
            hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest(), "big"
        )
        for i in range(bits):
            if (h >> i) & 1:
                v[i] += w
            else:
                v[i] -= w

    fingerprint = 0
    for i in range(bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_similarity(a: int, b: int, bits: int = 64) -> float:
    """Return similarity in [0, 1] as 1 - (hamming_distance / bits)."""
    distance = bin(a ^ b).count("1")
    return 1.0 - (distance / bits)


def is_near_duplicate(text_a: str, text_b: str, threshold: float = NEAR_DUP_THRESHOLD) -> bool:
    """Return True if two texts are near-duplicates by SimHash similarity."""
    if not text_a or not text_b:
        return False
    return hamming_similarity(simhash(text_a), simhash(text_b)) >= threshold
