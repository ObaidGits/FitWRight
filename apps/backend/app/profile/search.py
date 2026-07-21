"""Profile Search - ranked, highlighted search across the profile document.

Searches the user's own canonical profile (experience / education / projects /
skills / certifications / achievements + identity) with deterministic ranking
and match highlighting. It is **pure** (operates on the in-memory
``ProfileData``), so it needs no external search infra to be useful today; the
result contract is stable so a Postgres FTS/GIN or embedding backend can replace
the ranker later without changing callers (the same seam idea as the similarity
provider).

Ranking: exact-token and prefix matches on the primary field (title/name)
outrank body matches; more matched query tokens rank higher. Highlights wrap the
matched substrings in ``[[...]]`` sentinels the UI renders as marks (kept markup-
free here so the API stays presentation-agnostic).
"""

from __future__ import annotations

import re
from typing import Any

from app.profile.schemas import ProfileData

__all__ = ["search_profile", "SearchRecord"]

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


class SearchRecord:
    """One indexed, searchable unit of the profile."""

    __slots__ = ("type", "uid", "section", "title", "subtitle", "body")

    def __init__(self, *, type: str, uid: str, section: str, title: str, subtitle: str = "", body: str = "") -> None:
        self.type = type
        self.uid = uid
        self.section = section
        self.title = title
        self.subtitle = subtitle
        self.body = body


def _index(profile: ProfileData) -> list[SearchRecord]:
    """Flatten the profile into searchable records (stable order)."""
    records: list[SearchRecord] = []
    ident = profile.identity
    if ident.name or ident.headline:
        records.append(
            SearchRecord(
                type="identity",
                uid="identity",
                section="overview",
                title=ident.name or ident.headline,
                subtitle=ident.headline,
                body=" ".join([ident.currentRole, ident.currentCompany, ident.industry, ident.location]),
            )
        )
    if profile.summary:
        records.append(
            SearchRecord(type="summary", uid="summary", section="overview", title="Summary", body=profile.summary)
        )
    for e in profile.workExperience:
        records.append(
            SearchRecord(
                type="experience",
                uid=e.uid,
                section="experience",
                title=e.title,
                subtitle=e.company,
                body=" ".join([*e.description, *e.tech, e.years, e.location or ""]),
            )
        )
    for ed in profile.education:
        records.append(
            SearchRecord(
                type="education",
                uid=ed.uid,
                section="education",
                title=ed.degree or ed.institution,
                subtitle=ed.institution,
                body=" ".join([ed.years, ed.description or ""]),
            )
        )
    for p in profile.personalProjects:
        records.append(
            SearchRecord(
                type="project",
                uid=p.uid,
                section="projects",
                title=p.name,
                subtitle=p.role,
                body=" ".join([*p.description, *p.tech]),
            )
        )
    for c in profile.certifications:
        records.append(
            SearchRecord(type="certification", uid=c.uid, section="overview", title=c.name, subtitle=c.issuer)
        )
    for a in profile.achievements:
        records.append(
            SearchRecord(type="achievement", uid=a.uid, section="overview", title=a.title, body=a.description or "")
        )
    for group, cat in (
        (profile.skills.technical, "technical"),
        (profile.skills.tools, "tools"),
        (profile.skills.languages, "languages"),
        (profile.skills.soft, "soft"),
    ):
        for s in group:
            records.append(
                SearchRecord(
                    type="skill",
                    uid=s.uid,
                    section="skills",
                    title=s.displayName or s.canonical,
                    subtitle=cat,
                    body=" ".join(s.aliases),
                )
            )
    return records


def _highlight(text: str, query_tokens: set[str]) -> str:
    """Wrap matched whole tokens in ``[[...]]`` sentinels (presentation-agnostic)."""
    if not text:
        return text

    def repl(m: re.Match) -> str:
        return f"[[{m.group(0)}]]" if m.group(0).lower() in query_tokens else m.group(0)

    return re.sub(r"[A-Za-z0-9]+", repl, text)


def search_profile(profile: ProfileData, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Return ranked, highlighted matches for ``query`` within ``profile``."""
    q_tokens = set(_tokens(query))
    if not q_tokens:
        return []

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for order, rec in enumerate(_index(profile)):
        title_tokens = set(_tokens(rec.title))
        sub_tokens = set(_tokens(rec.subtitle))
        body_tokens = set(_tokens(rec.body))

        title_hits = q_tokens & title_tokens
        sub_hits = q_tokens & sub_tokens
        body_hits = q_tokens & body_tokens
        matched = title_hits | sub_hits | body_hits
        if not matched:
            # Prefix match on title (autocomplete-style) as a weaker signal.
            if any(t.startswith(qt) for t in title_tokens for qt in q_tokens):
                title_hits = {"__prefix__"}
                matched = title_hits
            else:
                continue

        # Weighted score: title >> subtitle > body; reward coverage of the query.
        score = 3.0 * len(title_hits) + 1.5 * len(sub_hits) + 1.0 * len(body_hits)
        score += 2.0 * (len(matched & q_tokens) / len(q_tokens))
        scored.append(
            (
                score,
                -order,  # stable: earlier records win ties
                {
                    "type": rec.type,
                    "uid": rec.uid,
                    "section": rec.section,
                    "title": _highlight(rec.title, q_tokens),
                    "subtitle": _highlight(rec.subtitle, q_tokens),
                    "snippet": _highlight(rec.body[:160], q_tokens),
                    "score": round(score, 2),
                },
            )
        )

    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [item for _, _, item in scored[:limit]]
