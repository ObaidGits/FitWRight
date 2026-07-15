"""Enhanced DOM extraction with container scoring (§14 of enhancement plan).

Upgrades the current extract.py with a scoring engine that evaluates containers
by semantic weight, text density, heading keywords, and negative signals.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from app.jd.models import ConfidenceResult, ExtractionResult

__all__ = ["extract_dom_scored"]

VERSION = "1.0.0"
_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg")
_WS_RE = re.compile(r"[ \t\f\v]+")
_NL_RE = re.compile(r"\n{3,}")
_JOB_KEYWORDS = frozenset({
    "responsibilities", "qualifications", "requirements", "about the role",
    "what you", "benefits", "about us", "what we offer", "must have",
    "nice to have", "experience", "skills",
})
_JOB_CLASSES = frozenset({
    "job-description", "posting-page", "job-content", "posting-description",
    "jobdescription", "jobdesciption",  # intentional: common typo on real ATS pages
    "job-details", "job-posting", "vacancy-description",
})


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    return _NL_RE.sub("\n\n", text).strip()


def _score_container(element: Tag) -> float:
    """Score a DOM container for likelihood of being the job description."""
    score = 0.0

    # Semantic tag weight
    if element.name in ("article", "main"):
        score += 30
    if element.get("role") == "main":
        score += 25

    # Class-based signals
    classes = " ".join(element.get("class", [])).lower()
    if any(c in classes for c in _JOB_CLASSES):
        score += 25

    # ID-based signals
    elem_id = (element.get("id") or "").lower()
    if any(c in elem_id for c in ("job", "posting", "description", "vacancy")):
        score += 20

    # Text density
    text = element.get_text()
    text_len = len(text)
    child_tags = len(element.find_all())
    if child_tags > 0:
        density = text_len / child_tags
        score += min(density / 10, 20)

    # Job-heading signals
    headings = element.find_all(["h1", "h2", "h3", "h4"])
    for h in headings:
        h_text = h.get_text().lower()
        if any(k in h_text for k in _JOB_KEYWORDS):
            score += 10

    # Negative signals
    if element.find("form", {"action": re.compile(r"login|sign.?in", re.I)}):
        score -= 50
    first_200 = text[:200].lower()
    if "cookie" in first_200:
        score -= 20
    if "sign in" in first_200 or "log in" in first_200:
        score -= 30

    return score


def extract_dom_scored(html: str, *, max_chars: int = 20000) -> ExtractionResult | None:
    """Extract job description using container scoring.

    Returns ExtractionResult if meaningful content found (>= 400 chars), else None.
    """
    if not html or not html.strip():
        return None

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()

    # Score all potential containers
    candidates: list[tuple[float, Tag]] = []
    for selector in ["article", "main", "[role=main]", "section", "div"]:
        for elem in soup.select(selector):
            score = _score_container(elem)
            if score > 0:
                candidates.append((score, elem))

    # Sort by score descending
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Try the best candidate first
    content = ""
    for score, elem in candidates[:5]:  # Check top 5
        text = _normalize(elem.get_text(separator="\n"))
        if len(text) >= 400:
            content = text
            break

    # Fallback to full body
    if not content or len(content) < 400:
        body_text = _normalize((soup.body or soup).get_text(separator="\n"))
        if len(body_text) >= 400:
            content = body_text

    if not content or len(content) < 200:
        return None

    # Truncate if too long
    if len(content) > max_chars:
        content = content[:max_chars].rsplit("\n", 1)[0].strip()

    # Determine confidence
    score_val = 55 if len(content) >= 800 else 45 if len(content) >= 400 else 30
    level = "MEDIUM" if score_val >= 45 else "LOW"

    return ExtractionResult(
        content=content,
        confidence=ConfidenceResult(
            level=level, score=score_val,
            reasons=[f"DOM extraction: {len(content)} chars", "Container scoring applied"],
        ),
        source="dom_semantic",
    )
