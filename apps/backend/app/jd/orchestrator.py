"""JD extraction v2 orchestrator (§4 of enhancement plan).

Coordinates the extraction cascade: API -> JSON-LD -> Hydration -> DOM -> Playwright.
Stops at the first HIGH confidence result. Respects the global timeout budget.
Integrates: multi-layer cache (L2/L5), SingleFlight dedup, drift/circuit-breaker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

from app.jd.adapters.registry import get_adapter
from app.jd.browser.decision import needs_browser
from app.jd.browser.render import render_and_extract
from app.jd.cache import JdCache
from app.jd.canonicalize import canonicalize_url, redact_url
from app.jd.classify import PageClass, classify_page
from app.jd.drift import DriftMonitor
from app.jd.extractors.dom import extract_dom_scored
from app.jd.extractors.hydration import extract_hydration
from app.jd.extractors.jsonld import extract_jsonld
from app.jd.extractors.pdf import (
    detect_unsupported_source,
    extract_pdf,
    is_pdf_url,
    looks_like_pdf,
)
from app.jd.fingerprint import content_fingerprint
from app.jd.i18n import detect_language
from app.jd.models import (
    ConfidenceResult,
    ExtractionExplanation,
    ExtractionResult,
    StageTrace,
)
from app.jd.monitoring.cost import CostMonitor, OperationCost
from app.jd.robots import RobotsChecker
from app.jd.ssrf import SsrfError, fetch_raw_safely, fetch_url_safely

logger = logging.getLogger(__name__)

__all__ = ["orchestrate_v2"]

_VERSION = "2.1.0"
_CRAWL_DELAY_MAX = 5.0  # cap enforced Crawl-delay (seconds) to protect latency

# Module-level singletons (lazy-init on first use)
_cache: JdCache | None = None
_drift: DriftMonitor | None = None
_robots: RobotsChecker | None = None
_cost: CostMonitor | None = None


def _get_cache() -> JdCache:
    global _cache
    if _cache is None:
        from app.auth.runtime import get_kvstore
        _cache = JdCache(get_kvstore())
    return _cache


def _get_drift() -> DriftMonitor:
    global _drift
    if _drift is None:
        from app.auth.runtime import get_kvstore
        _drift = DriftMonitor(get_kvstore())
    return _drift


def _get_robots() -> RobotsChecker:
    global _robots
    if _robots is None:
        from app.auth.runtime import get_kvstore
        _robots = RobotsChecker(get_kvstore())
    return _robots


def _get_cost() -> CostMonitor:
    global _cost
    if _cost is None:
        from app.auth.runtime import get_kvstore
        _cost = CostMonitor(get_kvstore())
    return _cost


def _finalize(result: ExtractionResult) -> ExtractionResult:
    """Attach language + content fingerprint to a successful result (§21, §22).

    Cheap (< 1ms), runs on any result with content. Idempotent.
    """
    if result.content and not result.fingerprint:
        try:
            result.fingerprint = content_fingerprint(
                result.title.value if result.title else None,
                result.company.value if result.company else None,
                result.location.value if result.location else None,
                result.content,
            )
        except Exception:
            pass
    if result.content and not result.language:
        try:
            result.language = detect_language(text=result.content) or ""
        except Exception:
            pass

    # ML content scoring (Phase 4): extra confidence signal for the AMBIGUOUS
    # extraction paths only (DOM/headless/PDF) - never overrides authoritative
    # API/JSON-LD, never fabricates content. Off by default (jd_ml_scoring_enabled).
    try:
        from app.config import settings
        if (
            settings.jd_ml_scoring_enabled
            and result.content
            and result.source in ("dom_semantic", "headless_dom", "pdf_ocr")
        ):
            from app.jd.ml_scorer import score_content
            ml = score_content(result.content)
            result.confidence.reasons.append(f"ML content score {ml:.2f}")
            if ml < 0.35 and result.confidence.level != "LOW":
                # Model strongly doubts this is a real JD -> downgrade + warn.
                result.confidence.level = "LOW"
                result.confidence.score = min(result.confidence.score, 40)
                result.explanation.warnings.append(
                    "Automated quality check flagged this content as possibly not a job description."
                )
            elif ml >= 0.8 and result.confidence.level == "MEDIUM":
                # Model is confident -> nudge MEDIUM up a little.
                result.confidence.score = min(100, result.confidence.score + 5)
    except Exception:
        logger.debug("JD ML scoring failed", exc_info=True)
    return result


async def _link_and_meter(result: ExtractionResult) -> None:
    """Post-cascade: near-duplicate linking (§22) + observability metrics (§34).

    Runs outside the timeout envelope - all steps are cheap and best-effort.
    """
    try:
        from app.productivity.metrics import get_productivity_metrics
        metrics = get_productivity_metrics()
    except Exception:
        metrics = None

    # Near-duplicate linking: if this content fingerprint was seen at a DIFFERENT
    # URL, link the records; otherwise register this URL under the fingerprint.
    if result.content and result.fingerprint:
        try:
            cache = _get_cache()
            canonical = result.canonical_url or result.submitted_url
            existing = await cache.get_url_by_fingerprint(result.fingerprint)
            if existing and existing != canonical:
                result.near_duplicate_of = existing
                if metrics:
                    metrics.jd_near_duplicate()
            else:
                await cache.register_fingerprint(result.fingerprint, canonical)
        except Exception:
            logger.debug("JD near-duplicate linking failed", exc_info=True)

    # Extraction outcome metric (by source, or the classified error code).
    if metrics:
        try:
            if result.content:
                metrics.jd_extract(result.source)
            elif result.error_code:
                metrics.incr(f"jd_extract_failed_total.{result.error_code}")
        except Exception:
            pass


async def _enforce_crawl_delay(host: str, delay: float) -> None:
    """Best-effort per-domain Crawl-delay throttle (§26, ADR-13).

    Capped at ``_CRAWL_DELAY_MAX`` so an adversarial/huge Crawl-delay can't blow
    the latency budget. Uses a KV timestamp per host; races are acceptable (this
    is politeness, not correctness).
    """
    delay = min(delay, _CRAWL_DELAY_MAX)
    if delay <= 0:
        return
    try:
        from app.auth.runtime import get_kvstore
        kv = get_kvstore()
        key = f"jd:crawl:{host.lower()}"
        now = time.time()
        last_raw = await kv.get(key)
        if last_raw:
            wait = float(last_raw) + delay - now
            if wait > 0:
                await asyncio.sleep(min(wait, delay))
        await kv.set(key, str(time.time()), ttl_seconds=int(delay) + 5)
    except Exception:
        logger.debug("JD crawl-delay enforcement failed", exc_info=True)


async def _extract_pdf_stage(url, canonical, traces, settings) -> ExtractionResult | None:
    """Fetch a PDF via the raw SSRF-safe fetcher and extract text (§20).

    Returns an ExtractionResult (success or classified failure), or None if the
    payload turned out not to be a PDF (caller should continue the HTML cascade).
    """
    t0 = time.perf_counter()
    try:
        data, content_type = await fetch_raw_safely(url, accept="application/pdf,*/*")
    except SsrfError as exc:
        traces.append(StageTrace(
            stage="pdf_fetch", duration_ms=(time.perf_counter() - t0) * 1000,
            status="failed", detail=exc.reason,
        ))
        logger.info("JD v2: PDF fetch blocked/failed for %s: %s", redact_url(canonical), exc.reason)
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=["Could not download the PDF"]),
            explanation=ExtractionExplanation(
                summary="Could not download the PDF.",
                pipeline_trace=traces,
                suggestions=["Check the URL, or paste the job description text directly."],
            ),
            submitted_url=url, canonical_url=canonical, error_code="fetch_failed",
        )

    unsupported = detect_unsupported_source(url, content_type)
    if unsupported:
        traces.append(StageTrace(stage="pdf_classify", duration_ms=0, status="failed", detail=unsupported))
        result = ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=[unsupported]),
            explanation=ExtractionExplanation(
                summary="This document format isn't supported.", pipeline_trace=traces,
                suggestions=["Paste the job description text directly."],
            ),
            submitted_url=url, canonical_url=canonical, error_code=unsupported,
        )
        return result

    if not looks_like_pdf(data, content_type):
        traces.append(StageTrace(
            stage="pdf_classify", duration_ms=0, status="skipped",
            detail="not a PDF; continuing HTML cascade",
        ))
        return None  # not actually a PDF -> let the HTML cascade handle it

    result = extract_pdf(data, url, ocr_enabled=settings.jd_ocr_enabled)
    result.canonical_url = canonical
    result.submitted_url = url
    try:
        from app.productivity.metrics import get_productivity_metrics
        get_productivity_metrics().jd_pdf("ok" if result.content else (result.error_code or "failed"))
    except Exception:
        pass
    status = "success" if result.content else "failed"
    traces.append(StageTrace(
        stage="pdf_extract", duration_ms=(time.perf_counter() - t0) * 1000,
        status=status, detail=f"{len(result.content)} chars" if result.content else (result.error_code or "no text"),
    ))
    result.explanation.pipeline_trace = traces + result.explanation.pipeline_trace
    if not result.explanation.summary:
        result.explanation.summary = "Extracted from PDF."
    return result


async def orchestrate_v2(
    user_id: str,
    url: str,
    *,
    use_ai: bool = False,
    timeout: float = 20.0,
    force_refresh: bool = False,
) -> ExtractionResult:
    """Run the full v2 extraction cascade with timeout envelope.

    Returns ExtractionResult (always - even on failure, wraps in LOW confidence).
    """
    try:
        result = await asyncio.wait_for(
            _run_cascade(user_id, url, use_ai=use_ai, force_refresh=force_refresh),
            timeout=timeout,
        )
        result = _finalize(result)
        await _link_and_meter(result)
        return result
    except asyncio.TimeoutError:
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=["Global timeout exceeded"]),
            explanation=ExtractionExplanation(
                summary="Extraction timed out after 20 seconds.",
                warnings=["The page took too long to process."],
                suggestions=["Paste the job description text directly."],
            ),
            submitted_url=url,
        )


async def _run_cascade(user_id: str, url: str, *, use_ai: bool = False, force_refresh: bool = False) -> ExtractionResult:
    """Execute the extraction cascade: Cache -> API -> JSON-LD -> Hydration -> DOM -> Playwright."""
    traces: list[StageTrace] = []
    canonical = canonicalize_url(url)
    start = time.perf_counter()
    cache = _get_cache()
    drift = _get_drift()

    # --- Stage 0: Cache lookup (L2 result) ---
    if not force_refresh:
        t0 = time.perf_counter()
        cached = await cache.get_result(canonical)
        if cached:
            cached.submitted_url = url
            traces.append(StageTrace(
                stage="cache_l2", duration_ms=(time.perf_counter() - t0) * 1000,
                status="success", detail="cache hit"
            ))
            cached.explanation = ExtractionExplanation(
                summary="Served from cache (fast path).",
                pipeline_trace=traces,
            )
            logger.debug("JD v2: cache hit for %s", redact_url(canonical))
            return cached

        # Check error cache (L5) - avoid hammering broken URLs
        err = await cache.get_error(canonical)
        if err:
            traces.append(StageTrace(
                stage="cache_l5", duration_ms=(time.perf_counter() - t0) * 1000,
                status="success", detail=f"error cached: {err.get('reason', 'unknown')}"
            ))
            return ExtractionResult(
                content="",
                confidence=ConfidenceResult(level="LOW", score=0, reasons=["Recently failed (cached)"]),
                explanation=ExtractionExplanation(
                    summary="This URL recently failed. Try again in a few minutes.",
                    pipeline_trace=traces,
                    suggestions=["Wait a moment and retry.", "Paste the job description text directly."],
                ),
                submitted_url=url, canonical_url=canonical,
            )
        traces.append(StageTrace(stage="cache", duration_ms=(time.perf_counter() - t0) * 1000, status="skipped", detail="miss"))

    from app.config import settings

    # --- Stage 0.2: Unsupported-source detection (Google Docs, Notion, DOCX) ---
    unsupported = detect_unsupported_source(url)
    if unsupported:
        traces.append(StageTrace(stage="classify_source", duration_ms=0, status="failed", detail=unsupported))
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=[unsupported]),
            explanation=ExtractionExplanation(
                summary="This document type isn't supported for automatic extraction.",
                pipeline_trace=traces,
                warnings=[unsupported],
                suggestions=["Export to a job page or PDF, or paste the job description text directly."],
            ),
            submitted_url=url, canonical_url=canonical, error_code=unsupported, source="manual_upload",
        )

    # --- Stage 0.3: robots.txt policy check (§26, fail-open) ---
    if settings.jd_robots_check_enabled:
        t0 = time.perf_counter()
        try:
            decision = await _get_robots().check(url)
        except Exception:  # never let robots checking break extraction
            decision = None
        if decision is not None and not decision.allowed:
            traces.append(StageTrace(
                stage="robots", duration_ms=(time.perf_counter() - t0) * 1000,
                status="failed", detail=decision.reason,
            ))
            await cache.set_error(canonical, "robots_disallowed")
            try:
                from app.productivity.metrics import get_productivity_metrics
                get_productivity_metrics().jd_robots_blocked()
            except Exception:
                pass
            return ExtractionResult(
                content="",
                confidence=ConfidenceResult(level="LOW", score=0, reasons=["ROBOTS_DISALLOWED"]),
                explanation=ExtractionExplanation(
                    summary="This site's robots.txt disallows automated access to this page.",
                    pipeline_trace=traces,
                    warnings=["ROBOTS_DISALLOWED"],
                    suggestions=["Open the page in your browser and paste the job description text directly."],
                ),
                submitted_url=url, canonical_url=canonical, error_code="ROBOTS_DISALLOWED",
            )
        # Respect Crawl-delay when the site declares one (bounded - §26/ADR-13).
        if decision is not None and decision.crawl_delay > 0:
            await _enforce_crawl_delay(urlparse(url).hostname or "", decision.crawl_delay)
        traces.append(StageTrace(
            stage="robots", duration_ms=(time.perf_counter() - t0) * 1000,
            status="success", detail=decision.reason if decision else "checked",
        ))

    # --- Stage 0.5: PDF routing by URL (§20) ---
    if settings.jd_pdf_enabled and is_pdf_url(url):
        pdf_result = await _extract_pdf_stage(url, canonical, traces, settings)
        if pdf_result is not None:
            if pdf_result.content:
                await cache.set_result(canonical, pdf_result)
                await _get_cost().record(user_id, OperationCost.STATIC_FETCH)
            else:
                await cache.set_error(canonical, pdf_result.error_code or "pdf_failed")
            return pdf_result

    # --- Stage 1: Platform API (with drift/circuit-breaker check) ---
    t0 = time.perf_counter()
    adapter = get_adapter(canonical)
    if adapter:
        # Check circuit breaker before calling the adapter's API
        platform_healthy = await drift.is_healthy(adapter.PLATFORM_ID)
        if not platform_healthy:
            traces.append(StageTrace(
                stage="platform_api", duration_ms=0,
                status="skipped", detail=f"circuit open for {adapter.PLATFORM_ID}"
            ))
        else:
            parsed = urlparse(canonical)
            api_url = adapter.extract_api_url(parsed)
            if api_url:
                try:
                    raw = await fetch_url_safely(api_url)
                    data = json.loads(raw)
                    result = adapter.parse_response(data, canonical)
                    result.submitted_url = url
                    result.canonical_url = canonical
                    result.explanation = ExtractionExplanation(
                        summary=f"Extracted via {adapter.PLATFORM_ID} API with HIGH confidence.",
                        pipeline_trace=traces + [StageTrace(
                            stage="platform_api", duration_ms=(time.perf_counter() - t0) * 1000,
                            status="success", detail=f"adapter={adapter.PLATFORM_ID}"
                        )],
                    )
                    await drift.record_success(adapter.PLATFORM_ID)
                    await cache.set_result(canonical, result)
                    logger.info("JD v2: %s API success for %s", adapter.PLATFORM_ID, redact_url(canonical))
                    return result
                except (SsrfError, json.JSONDecodeError, KeyError, TypeError) as exc:
                    traces.append(StageTrace(
                        stage="platform_api", duration_ms=(time.perf_counter() - t0) * 1000,
                        status="failed", detail=f"{adapter.PLATFORM_ID}: {type(exc).__name__}"
                    ))
                    await drift.record_failure(adapter.PLATFORM_ID)
                    logger.debug("JD v2: %s API failed: %s", adapter.PLATFORM_ID, exc)
            else:
                traces.append(StageTrace(
                    stage="platform_api", duration_ms=(time.perf_counter() - t0) * 1000,
                    status="skipped", detail="URL slug unparseable"
                ))
    else:
        traces.append(StageTrace(
            stage="platform_api", duration_ms=(time.perf_counter() - t0) * 1000,
            status="skipped", detail="No platform detected"
        ))

    # --- Stage 2: Fetch static HTML ---
    t0 = time.perf_counter()
    try:
        html = await fetch_url_safely(url)
        await _get_cost().record(user_id, OperationCost.STATIC_FETCH)
        traces.append(StageTrace(
            stage="fetch_static", duration_ms=(time.perf_counter() - t0) * 1000,
            status="success", detail=f"{len(html)} bytes"
        ))
    except SsrfError as exc:
        traces.append(StageTrace(
            stage="fetch_static", duration_ms=(time.perf_counter() - t0) * 1000,
            status="failed", detail=exc.reason
        ))
        # SECURITY: never surface the SSRF reason (blocked IP / port / scheme)
        # to the client - it would turn this endpoint into an internal scanner.
        # The reason is logged server-side and cached internally only.
        logger.info("JD v2: fetch blocked/failed for %s: %s", redact_url(canonical), exc.reason)
        await cache.set_error(canonical, f"fetch_failed:{exc.reason}")
        return ExtractionResult(
            content="",
            confidence=ConfidenceResult(level="LOW", score=0, reasons=["Could not fetch the page"]),
            explanation=ExtractionExplanation(
                summary="Could not fetch the page.",
                pipeline_trace=traces,
                warnings=["The page could not be retrieved."],
                suggestions=["Check the URL is correct.", "Paste the job description text directly."],
            ),
            submitted_url=url,
            canonical_url=canonical,
            error_code="fetch_failed",
        )

    # --- Stage 2.2: Content-type PDF detection (URLs without a .pdf suffix) ---
    if settings.jd_pdf_enabled and html[:5] == "%PDF-":
        pdf_result = await _extract_pdf_stage(url, canonical, traces, settings)
        if pdf_result is not None:
            if pdf_result.content:
                await cache.set_result(canonical, pdf_result)
            else:
                await cache.set_error(canonical, pdf_result.error_code or "pdf_failed")
            return pdf_result

    # Detect page language once from the authoritative <html lang> (§21). Used to
    # tag HTML-path results; falls back to a content heuristic in _finalize.
    page_lang = detect_language(html=html) if settings.jd_i18n_enabled else ""

    # --- Stage 2.5: Page Classification (fast, < 1ms) ---
    page_class = classify_page(html)
    if page_class == PageClass.CAPTCHA:
        traces.append(StageTrace(stage="classify", duration_ms=0, status="failed", detail="CAPTCHA detected"))
        return ExtractionResult(
            content="", confidence=ConfidenceResult(level="LOW", score=0, reasons=["CAPTCHA detected"]),
            explanation=ExtractionExplanation(
                summary="The site blocked automated access with a CAPTCHA.",
                pipeline_trace=traces,
                warnings=["CAPTCHA or bot challenge detected."],
                suggestions=["Paste the job description text directly."],
            ),
            submitted_url=url, canonical_url=canonical,
        )
    elif page_class == PageClass.WAF_BLOCKED:
        traces.append(StageTrace(stage="classify", duration_ms=0, status="failed", detail="WAF blocked"))
        return ExtractionResult(
            content="", confidence=ConfidenceResult(level="LOW", score=0, reasons=["WAF blocked"]),
            explanation=ExtractionExplanation(
                summary="The site's firewall blocked automated access.",
                pipeline_trace=traces,
                warnings=["Web Application Firewall detected."],
                suggestions=["Paste the job description text directly."],
            ),
            submitted_url=url, canonical_url=canonical,
        )
    elif page_class == PageClass.LOGIN_REQUIRED:
        traces.append(StageTrace(stage="classify", duration_ms=0, status="failed", detail="Login required"))
        return ExtractionResult(
            content="", confidence=ConfidenceResult(level="LOW", score=0, reasons=["Login required"]),
            explanation=ExtractionExplanation(
                summary="This page requires authentication.",
                pipeline_trace=traces,
                warnings=["Login wall detected."],
                suggestions=["Sign in to the job page, then paste the description text."],
            ),
            submitted_url=url, canonical_url=canonical,
        )
    elif page_class == PageClass.EXPIRED_JOB:
        traces.append(StageTrace(stage="classify", duration_ms=0, status="failed", detail="Job expired"))
        return ExtractionResult(
            content="", confidence=ConfidenceResult(level="LOW", score=0, reasons=["Job expired/removed"]),
            explanation=ExtractionExplanation(
                summary="This job posting is no longer available.",
                pipeline_trace=traces,
                warnings=["The job appears to have been removed or expired."],
                suggestions=["Check if the job is still posted.", "Look for the same role on the company's careers page."],
            ),
            submitted_url=url, canonical_url=canonical,
        )
    else:
        traces.append(StageTrace(stage="classify", duration_ms=0, status="success", detail=page_class))

    # --- Stage 3: JSON-LD extraction (from fetched HTML, zero cost) ---
    t0 = time.perf_counter()
    jsonld_result = extract_jsonld(html)
    if jsonld_result and len(jsonld_result.content) >= 400:
        jsonld_result.submitted_url = url
        jsonld_result.canonical_url = canonical
        jsonld_result.language = page_lang
        traces.append(StageTrace(
            stage="json_ld", duration_ms=(time.perf_counter() - t0) * 1000,
            status="success", detail=f"{len(jsonld_result.content)} chars"
        ))
        jsonld_result.explanation = ExtractionExplanation(
            summary="Extracted from JSON-LD structured data with HIGH confidence.",
            pipeline_trace=traces,
        )
        logger.info("JD v2: JSON-LD success for %s", redact_url(canonical))
        await cache.set_result(canonical, jsonld_result)
        return jsonld_result
    else:
        traces.append(StageTrace(
            stage="json_ld", duration_ms=(time.perf_counter() - t0) * 1000,
            status="skipped" if not jsonld_result else "failed",
            detail="No JobPosting found" if not jsonld_result else f"Too short: {len(jsonld_result.content if jsonld_result else '')} chars"
        ))

    # --- Stage 3.5: Hydration JSON (__NEXT_DATA__, __NUXT__, zero cost) ---
    t0 = time.perf_counter()
    hydration_result = extract_hydration(html)
    if hydration_result and len(hydration_result.content) >= 400:
        hydration_result.submitted_url = url
        hydration_result.canonical_url = canonical
        hydration_result.language = page_lang
        traces.append(StageTrace(
            stage="hydration_json", duration_ms=(time.perf_counter() - t0) * 1000,
            status="success", detail=f"{len(hydration_result.content)} chars"
        ))
        hydration_result.explanation = ExtractionExplanation(
            summary="Extracted from framework hydration state (pre-rendered data).",
            pipeline_trace=traces,
        )
        logger.info("JD v2: Hydration JSON success for %s", redact_url(canonical))
        await cache.set_result(canonical, hydration_result)
        return hydration_result
    else:
        traces.append(StageTrace(
            stage="hydration_json", duration_ms=(time.perf_counter() - t0) * 1000,
            status="skipped" if not hydration_result else "failed",
            detail="No hydration state found" if not hydration_result else "Content too short"
        ))

    # --- Stage 4: Enhanced DOM extraction (from same HTML, zero cost) ---
    t0 = time.perf_counter()
    dom_result = extract_dom_scored(html)
    if dom_result and len(dom_result.content) >= 400:
        dom_result.submitted_url = url
        dom_result.canonical_url = canonical
        dom_result.language = page_lang
        traces.append(StageTrace(
            stage="dom_scored", duration_ms=(time.perf_counter() - t0) * 1000,
            status="success", detail=f"{len(dom_result.content)} chars"
        ))
        dom_result.explanation = ExtractionExplanation(
            summary="Extracted from DOM via container scoring.",
            pipeline_trace=traces,
            suggestions=["If the result looks incomplete, paste the text directly."] if dom_result.confidence.level != "HIGH" else [],
        )
        logger.info("JD v2: DOM extraction success for %s (%s)", redact_url(canonical), dom_result.confidence.level)
        await cache.set_result(canonical, dom_result)
        return dom_result
    else:
        traces.append(StageTrace(
            stage="dom_scored", duration_ms=(time.perf_counter() - t0) * 1000,
            status="failed", detail="Insufficient content"
        ))

    # --- Stage 5: Playwright browser rendering (expensive, last resort) ---
    dom_chars = len(dom_result.content) if dom_result else 0
    domain = (urlparse(url).hostname or "")
    # Cost guard: Playwright is the only paid step in the cascade. Skip it if the
    # user/global budget is exhausted (§25) - degrade to the LOW-confidence
    # fallback rather than incur runaway cost.
    within_budget = True
    if settings.jd_cost_monitoring_enabled:
        try:
            within_budget = await _get_cost().check_budget(user_id)
        except Exception:
            within_budget = True
    if not within_budget:
        traces.append(StageTrace(
            stage="playwright", duration_ms=0,
            status="skipped", detail="cost budget exhausted",
        ))
    elif needs_browser(html, domain, dom_chars):
        t0 = time.perf_counter()
        try:
            browser_result = await render_and_extract(url)
            await _get_cost().record(user_id, OperationCost.PLAYWRIGHT)
            try:
                from app.productivity.metrics import get_productivity_metrics
                get_productivity_metrics().jd_render(
                    "ok" if browser_result and len(browser_result.content) >= 300 else "failed"
                )
            except Exception:
                pass
            if browser_result and len(browser_result.content) >= 300:
                browser_result.submitted_url = url
                browser_result.canonical_url = canonical
                browser_result.language = browser_result.language or page_lang
                traces.append(StageTrace(
                    stage="playwright", duration_ms=(time.perf_counter() - t0) * 1000,
                    status="success", detail=f"{len(browser_result.content)} chars"
                ))
                browser_result.explanation = ExtractionExplanation(
                    summary="Extracted via browser rendering (JavaScript-rendered content).",
                    pipeline_trace=traces,
                    suggestions=["Content was rendered via a browser. Some dynamic elements may be missing."],
                )
                logger.info("JD v2: Playwright success for %s (%d chars)", redact_url(canonical), len(browser_result.content))
                return browser_result
            else:
                traces.append(StageTrace(
                    stage="playwright", duration_ms=(time.perf_counter() - t0) * 1000,
                    status="failed", detail="Insufficient content after render"
                ))
        except Exception as exc:
            traces.append(StageTrace(
                stage="playwright", duration_ms=(time.perf_counter() - t0) * 1000,
                status="failed", detail=str(exc)[:100]
            ))
            logger.debug("JD v2: Playwright failed for %s: %s", redact_url(canonical), exc)
    else:
        traces.append(StageTrace(
            stage="playwright", duration_ms=0,
            status="skipped", detail="Browser not needed (sufficient static content or not an SPA)"
        ))

    # --- All strategies exhausted ---
    total_ms = (time.perf_counter() - start) * 1000
    logger.info("JD v2: All strategies failed for %s (%.0fms)", redact_url(canonical), total_ms)
    await cache.set_error(canonical, "all_strategies_failed")
    return ExtractionResult(
        content=dom_result.content if dom_result else "",
        confidence=ConfidenceResult(level="LOW", score=15, reasons=["All extraction strategies produced insufficient content"]),
        explanation=ExtractionExplanation(
            summary="Could not extract a meaningful job description from this page.",
            pipeline_trace=traces,
            warnings=["The page may require JavaScript to render content."],
            suggestions=[
                "Paste the job description text directly.",
                "If this is a JavaScript-heavy site, the content may not be accessible to automated tools.",
            ],
        ),
        submitted_url=url,
        canonical_url=canonical,
        partial=True,
    )
