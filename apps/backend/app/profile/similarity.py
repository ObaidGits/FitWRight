"""Similarity Engine - pure, deterministic entity matching for the Merge Engine.

Given an incoming entity (from an imported resume) and the existing profile
entities of the same kind, the engine scores how likely they refer to the *same*
real-world thing (the same job, degree, project, skill...). The Merge Engine
(``app/profile/merge.py``) uses these scores to decide add vs. update vs.
duplicate - it never guesses on its own.

Design choices:
- **Pure + deterministic** (stdlib ``difflib`` only): no I/O, trivially testable,
  identical results across runs. An AI-assisted matcher can later refine the
  borderline band without changing this contract.
- **Field-weighted**: each entity kind weights the fields that actually identify
  it (company+title for a job, institution+degree for education), so a changed
  description doesn't make two clearly-identical jobs look distinct.
- **Normalized tokens**: case/whitespace/punctuation-insensitive so
  "Senior SWE" and "senior swe" match.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

__all__ = [
    "text_similarity",
    "token_set_similarity",
    "experience_similarity",
    "education_similarity",
    "project_similarity",
    "certification_similarity",
    "achievement_similarity",
    "skill_identity",
    "best_match",
    "DUPLICATE_THRESHOLD",
    "MATCH_THRESHOLD",
]

# Score >= this => the incoming item is (almost) certainly the same entity -> a
# duplicate/update candidate. Between MATCH and DUPLICATE => a likely match the
# user should confirm. Below MATCH => treated as a new item.
DUPLICATE_THRESHOLD = 0.82
MATCH_THRESHOLD = 0.55

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def _norm(value: Any) -> str:
    """Lowercased, punctuation-stripped, whitespace-collapsed comparison token."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    lowered = _NON_ALNUM.sub(" ", value.lower())
    return _WS.sub(" ", lowered).strip()


def text_similarity(a: Any, b: Any) -> float:
    """Character-level ratio of two normalized strings (0..1)."""
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _tokens(value: Any) -> set[str]:
    n = _norm(value)
    return set(n.split()) if n else set()


def token_set_similarity(a: Any, b: Any) -> float:
    """Jaccard overlap of the two token sets (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _list_token_similarity(a: Any, b: Any) -> float:
    """Token overlap across two lists of strings (e.g. bullet lists / tech)."""
    ta = _tokens(" ".join(x for x in (a or []) if isinstance(x, str)))
    tb = _tokens(" ".join(x for x in (b or []) if isinstance(x, str)))
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _weighted(parts: list[tuple[float, float]]) -> float:
    """Combine ``(score, weight)`` pairs into a weighted mean (0 if no weight)."""
    total = sum(w for _, w in parts)
    if total <= 0:
        return 0.0
    return sum(s * w for s, w in parts) / total


def experience_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Similarity of two work-experience entries (company+title dominate)."""
    return _weighted(
        [
            (text_similarity(a.get("company"), b.get("company")), 0.4),
            (text_similarity(a.get("title"), b.get("title")), 0.35),
            (text_similarity(a.get("years"), b.get("years")), 0.15),
            (_list_token_similarity(a.get("description"), b.get("description")), 0.1),
        ]
    )


def education_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Similarity of two education entries (institution+degree dominate)."""
    return _weighted(
        [
            (text_similarity(a.get("institution"), b.get("institution")), 0.5),
            (text_similarity(a.get("degree"), b.get("degree")), 0.35),
            (text_similarity(a.get("years"), b.get("years")), 0.15),
        ]
    )


def project_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Similarity of two projects (name dominates)."""
    return _weighted(
        [
            (text_similarity(a.get("name"), b.get("name")), 0.6),
            (_list_token_similarity(a.get("tech"), b.get("tech")), 0.2),
            (_list_token_similarity(a.get("description"), b.get("description")), 0.2),
        ]
    )


def certification_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Similarity of two certifications (name + issuer)."""
    return _weighted(
        [
            (text_similarity(a.get("name"), b.get("name")), 0.7),
            (text_similarity(a.get("issuer"), b.get("issuer")), 0.3),
        ]
    )


def achievement_similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Similarity of two achievements/awards (title dominates)."""
    return _weighted(
        [
            (text_similarity(a.get("title"), b.get("title")), 0.8),
            (text_similarity(a.get("kind"), b.get("kind")), 0.2),
        ]
    )


def skill_identity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Whether two skills are the same canonical skill (1.0/0.0, fuzzy fallback).

    Canonical equality is authoritative (the Canonical Skill Engine already
    normalized aliases); otherwise fall back to display-name text similarity so
    near-identical free-text skills still dedupe.
    """
    ca, cb = _norm(a.get("canonical") or a.get("displayName")), _norm(
        b.get("canonical") or b.get("displayName")
    )
    if ca and ca == cb:
        return 1.0
    return text_similarity(a.get("displayName"), b.get("displayName"))


def best_match(
    incoming: dict[str, Any],
    candidates: list[dict[str, Any]],
    scorer,
    *,
    threshold: float = MATCH_THRESHOLD,
) -> tuple[int, float] | None:
    """Return ``(index, score)`` of the best candidate above ``threshold``.

    Ties resolve to the earliest candidate (stable). ``None`` when nothing clears
    the threshold (=> the incoming item is new).
    """
    best_idx = -1
    best_score = 0.0
    for i, cand in enumerate(candidates):
        score = scorer(incoming, cand)
        if score > best_score:
            best_score = score
            best_idx = i
    if best_idx >= 0 and best_score >= threshold:
        return best_idx, best_score
    return None
