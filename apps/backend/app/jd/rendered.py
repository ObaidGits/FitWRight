"""Browser-extension fallback: extract from a user's already-rendered DOM (Phase 4).

Some pages can never be extracted server-side - hard logins, aggressive anti-bot
(Cloudflare/DataDome), or heavily client-rendered SPAs behind auth. For these, a
browser extension can capture the DOM the USER is already looking at (in their
authenticated session) and POST it here. We then run the SAME static extractors
(classification -> JSON-LD -> hydration -> DOM scoring) on that HTML - no fetch, no
scraping, no credentials touched. The content came from the user's own browser.

This is the truthful "last mile": we never fabricate; if the provided DOM has no
job description, we say so.
"""

from __future__ import annotations

import logging
import time

from app.jd.canonicalize import canonicalize_url
from app.jd.classify import PageClass, classify_page
from app.jd.extractors.dom import extract_dom_scored
from app.jd.extractors.hydration import extract_hydration
from app.jd.extractors.jsonld import extract_jsonld
from app.jd.models import (
    ConfidenceResult,
    ExtractionExplanation,
    ExtractionResult,
    StageTrace,
)

logger = logging.getLogger(__name__)

__all__ = ["extract_from_rendered", "MAX_RENDERED_BYTES"]

MAX_RENDERED_BYTES = 5 * 1024 * 1024  # 5 MiB cap on user-supplied DOM


def extract_from_rendered(url: str, html: str) -> ExtractionResult:
    """Extract a JD from user-supplied rendered HTML (no network).

    Runs the static cascade (JSON-LD -> hydration -> DOM). Always returns an
    ExtractionResult; empty content + error_code on failure (never fabricated).
    """
    canonical = canonicalize_url(url) if url else ""
    traces: list[StageTrace] = []

    if not html or not html.strip():
        return _empty(url, canonical, "empty_html", "No page content was provided.")

    if len(html) > MAX_RENDERED_BYTES:
        html = html[:MAX_RENDERED_BYTES]

    # Classify first - a rendered login/expired page is still a login/expired page.
    page_class = classify_page(html)
    if page_class in (PageClass.LOGIN_REQUIRED, PageClass.EXPIRED_JOB, PageClass.CAPTCHA, PageClass.WAF_BLOCKED):
        traces.append(StageTrace(stage="classify", duration_ms=0, status="failed", detail=page_class))
        return _empty(
            url, canonical, page_class,
            "The captured page looks like a login, expired, or blocked page - no job description found.",
            traces,
        )

    # JSON-LD (rendered SPAs often inject it post-hydration).
    t0 = time.perf_counter()
    jsonld = extract_jsonld(html)
    if jsonld and len(jsonld.content) >= 300:
        jsonld.source = "headless_dom"
        jsonld.submitted_url = url
        jsonld.canonical_url = canonical
        traces.append(StageTrace(stage="json_ld", duration_ms=(time.perf_counter() - t0) * 1000, status="success", detail=f"{len(jsonld.content)} chars"))
        jsonld.explanation = ExtractionExplanation(
            summary="Extracted from your browser's rendered page (structured data).",
            pipeline_trace=traces,
        )
        return jsonld
    traces.append(StageTrace(stage="json_ld", duration_ms=(time.perf_counter() - t0) * 1000, status="skipped", detail="none"))

    # Hydration state.
    t0 = time.perf_counter()
    hyd = extract_hydration(html)
    if hyd and len(hyd.content) >= 300:
        hyd.source = "headless_dom"
        hyd.submitted_url = url
        hyd.canonical_url = canonical
        traces.append(StageTrace(stage="hydration_json", duration_ms=(time.perf_counter() - t0) * 1000, status="success", detail=f"{len(hyd.content)} chars"))
        hyd.explanation = ExtractionExplanation(
            summary="Extracted from your browser's rendered page (embedded data).",
            pipeline_trace=traces,
        )
        return hyd
    traces.append(StageTrace(stage="hydration_json", duration_ms=(time.perf_counter() - t0) * 1000, status="skipped", detail="none"))

    # DOM scoring (the common case for a rendered page).
    t0 = time.perf_counter()
    dom = extract_dom_scored(html)
    if dom and len(dom.content) >= 200:
        dom.source = "headless_dom"
        dom.submitted_url = url
        dom.canonical_url = canonical
        traces.append(StageTrace(stage="dom_scored", duration_ms=(time.perf_counter() - t0) * 1000, status="success", detail=f"{len(dom.content)} chars"))
        dom.explanation = ExtractionExplanation(
            summary="Extracted from your browser's rendered page.",
            pipeline_trace=traces,
            suggestions=["Verify the text below - it was captured from the live page you were viewing."],
        )
        return dom
    traces.append(StageTrace(stage="dom_scored", duration_ms=(time.perf_counter() - t0) * 1000, status="failed", detail="insufficient content"))

    return _empty(
        url, canonical, "no_content",
        "We couldn't find a job description in the captured page.",
        traces,
    )


def _empty(url: str, canonical: str, code: str, summary: str, traces=None) -> ExtractionResult:
    return ExtractionResult(
        content="",
        confidence=ConfidenceResult(level="LOW", score=0, reasons=[code]),
        explanation=ExtractionExplanation(
            summary=summary,
            pipeline_trace=traces or [],
            suggestions=["Paste the job description text directly."],
        ),
        source="headless_dom",
        submitted_url=url,
        canonical_url=canonical,
        error_code=code,
    )
