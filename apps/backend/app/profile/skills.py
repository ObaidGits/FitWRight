"""Canonical Skill Engine - pure, deterministic skill normalization (ADR-12).

``canonicalize`` maps a free-text skill to a stable canonical id and a clean
display name using a small built-in alias table. It is intentionally pure (no
I/O, no DB): a future shared ``skill_taxonomy`` reference table can *accelerate*
normalization/autocomplete, but the engine never depends on it. Deterministic
output keeps it trivially testable and safe on the write path.
"""

from __future__ import annotations

import re

__all__ = ["canonicalize", "make_skill_dict", "suggest_skills"]

# Common alias -> canonical display name. Small on purpose; the shared taxonomy
# (later phase) is the scalable source. Keys are compared case-insensitively
# against the normalized token.
_ALIASES: dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "reactjs": "React",
    "react.js": "React",
    "react": "React",
    "nodejs": "Node.js",
    "node": "Node.js",
    "node.js": "Node.js",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "golang": "Go",
    "cpp": "C++",
    "c++": "C++",
    "csharp": "C#",
    "c#": "C#",
    "aws": "AWS",
    "gcp": "Google Cloud",
    "ml": "Machine Learning",
    "ai": "Artificial Intelligence",
}

_WS_RE = re.compile(r"\s+")


def _normalize_token(raw: str) -> str:
    """Lowercased, whitespace-collapsed comparison token."""
    return _WS_RE.sub(" ", raw.strip().lower())


def canonicalize(raw: str) -> tuple[str, str]:
    """Return ``(canonical_id, display_name)`` for a free-text skill.

    ``canonical_id`` is a lowercase slug used for equality/dedup; ``display_name``
    is the cleaned, human-facing label (alias-corrected where known).
    """
    token = _normalize_token(raw)
    if not token:
        return "", ""
    display = _ALIASES.get(token, raw.strip())
    canonical = _normalize_token(display)
    return canonical, display


def make_skill_dict(raw: str, *, category: str = "technical") -> dict:
    """Build a ``Skill``-shaped dict from free text (uid minted by the model)."""
    canonical, display = canonicalize(raw)
    return {
        "canonical": canonical,
        "displayName": display,
        "category": category,
        "aliases": [raw.strip()] if raw.strip() and raw.strip() != display else [],
    }


# Distinct canonical display names known to the engine (autocomplete corpus).
_KNOWN_DISPLAY_NAMES: tuple[str, ...] = tuple(
    sorted({name for name in _ALIASES.values()})
)


def suggest_skills(query: str, *, limit: int = 8) -> list[dict[str, str]]:
    """Prefix/substring autocomplete over the known canonical skills (pure).

    Prefix matches rank above substring matches; results are stable and capped.
    A future shared ``skill_taxonomy`` table can widen the corpus without
    changing this contract.
    """
    token = _normalize_token(query)
    if not token:
        return []
    prefix: list[str] = []
    substring: list[str] = []
    for name in _KNOWN_DISPLAY_NAMES:
        low = name.lower()
        if low.startswith(token):
            prefix.append(name)
        elif token in low:
            substring.append(name)
    ordered = prefix + substring
    out: list[dict[str, str]] = []
    for name in ordered[:limit]:
        canonical, display = canonicalize(name)
        out.append({"canonical": canonical, "displayName": display, "category": "technical"})
    return out
