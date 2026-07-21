"""Readability-lite job-description extraction (design §D, R9.1).

Pulls the main textual content out of a fetched HTML page with a lightweight
BeautifulSoup heuristic (drops script/style/nav/header/footer/aside; prefers the
densest ``<article>``/``<main>``/content container; falls back to whole-body
text). Flags **low confidence** when the result is short or looks like a
boilerplate/blocked page, so the UI asks the user to verify before tailoring.
No network here - pure transform, fully testable.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

__all__ = ["extract_job_description"]

_DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg")
_MIN_CONFIDENT_CHARS = 400
_WS_RE = re.compile(r"[ \t\f\v]+")
_NL_RE = re.compile(r"\n{3,}")


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.split("\n")]
    text = "\n".join(ln for ln in lines if ln)
    return _NL_RE.sub("\n\n", text).strip()


def extract_job_description(html: str, *, max_chars: int = 20000) -> tuple[str, bool]:
    """Return ``(content, low_confidence)`` extracted from ``html``.

    ``low_confidence`` is True when extraction is short/uncertain (the caller
    surfaces a "please verify" prompt before tailoring - R9.1).
    """
    if not html or not html.strip():
        return "", True

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(_DROP_TAGS):
        tag.decompose()

    # Prefer a semantic content container with the most text.
    candidates = soup.select("article, main, [role=main], .job-description, #job-description")
    best_text = ""
    for node in candidates:
        text = _normalize(node.get_text(separator="\n"))
        if len(text) > len(best_text):
            best_text = text

    body_text = _normalize((soup.body or soup).get_text(separator="\n"))
    # Use the container only if it captured a meaningful share of the body.
    content = best_text if len(best_text) >= max(_MIN_CONFIDENT_CHARS, len(body_text) * 0.3) else body_text

    if len(content) > max_chars:
        content = content[:max_chars].rsplit("\n", 1)[0].strip()

    low_confidence = _is_low_confidence(content)
    return content, low_confidence


def _is_low_confidence(content: str) -> bool:
    if len(content) < _MIN_CONFIDENT_CHARS:
        return True
    lowered = content.lower()
    # Common bot-wall / empty-shell signals.
    signals = (
        "enable javascript",
        "verify you are human",
        "captcha",
        "access denied",
        "are you a robot",
        "please turn on javascript",
    )
    if any(s in lowered for s in signals):
        return True
    return False
