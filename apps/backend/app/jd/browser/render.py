"""Browser rendering + extraction for JS-heavy sites (§12 of enhancement plan).

Launches Playwright, renders the page, scrolls to load lazy content, dismisses
cookie banners, then extracts from the rendered DOM.

Timeout: 12s per render (within the 20s global budget).
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.jd.browser.distributed import (
    DistributedRenderGate,
    edge_render_configured,
    render_via_edge,
)
from app.jd.browser.pool import close_browser_pool, create_stealth_context, get_browser_pool
from app.jd.extractors.dom import extract_dom_scored
from app.jd.extractors.jsonld import extract_jsonld
from app.jd.models import (
    ConfidenceResult,
    ExtractionExplanation,
    ExtractionResult,
    StageTrace,
)

logger = logging.getLogger(__name__)

__all__ = ["render_and_extract"]

_RENDER_TIMEOUT_MS = 12000  # 12s max for page render
_STABILIZATION_WAIT_MS = 2000  # Wait for dynamic content after load

# Common cookie banner selectors to dismiss
_COOKIE_SELECTORS = [
    "button:has-text('Accept')",
    "button:has-text('Accept all')",
    "button:has-text('I agree')",
    "#accept-cookies",
    ".cookie-accept",
    "[data-testid='cookie-accept']",
    "button[id*='cookie']",
    "button[class*='consent']",
]


async def render_and_extract(url: str, *, timeout_ms: int = _RENDER_TIMEOUT_MS) -> ExtractionResult | None:
    """Render a page with Playwright and extract job content.

    Returns ExtractionResult if meaningful content found, None otherwise.
    Acquires a semaphore slot from the browser pool (max 3 concurrent).
    """
    t_start = time.perf_counter()

    # --- Edge rendering (Phase 4): offload the browser to an external service ---
    if edge_render_configured():
        edge_result = await _render_via_edge_and_extract(url)
        if edge_result is not None:
            return edge_result
        # Edge failed -> fall through to the local browser pool.

    # --- Distributed render gate (Phase 4): cross-worker global concurrency cap ---
    gate = _make_render_gate()
    if not await gate.acquire():
        logger.warning("JD render gate: global capacity reached, refusing render")
        return None

    try:
        browser, semaphore = await get_browser_pool()
    except Exception as exc:
        logger.error("JD browser pool init failed: %s", exc)
        await gate.release()
        return None

    # Acquire semaphore (blocks if pool is full - backpressure)
    try:
        async with asyncio.timeout(timeout_ms / 1000 + 5):
            await semaphore.acquire()
    except asyncio.TimeoutError:
        logger.warning("JD browser pool: timeout waiting for semaphore")
        await gate.release()
        return None

    context = None
    page = None
    try:
        context = await create_stealth_context(browser)
        page = await context.new_page()

        # Navigate with timeout
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            logger.debug("JD browser: navigation failed for %s: %s", url, exc)
            return None

        # Wait for network idle (most content loaded)
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # networkidle timeout is non-fatal - content may already be there

        # Scroll to bottom to trigger lazy-loaded sections
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)  # Wait for lazy content to render
        except Exception:
            pass

        # Try dismissing cookie banners (best-effort, non-blocking)
        for selector in _COOKIE_SELECTORS[:3]:  # Try first 3 only (speed)
            try:
                btn = page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    await btn.click(timeout=500)
                    await page.wait_for_timeout(300)
                    break
            except Exception:
                continue

        # Wait for content stabilization
        await page.wait_for_timeout(_STABILIZATION_WAIT_MS)

        # Extract the rendered HTML
        rendered_html = await page.content()

        elapsed_ms = (time.perf_counter() - t_start) * 1000
        logger.info("JD browser: rendered %s in %.0fms (%d bytes)", url, elapsed_ms, len(rendered_html))

        # Try JSON-LD from rendered page (some SPAs inject it after hydration)
        jsonld_result = extract_jsonld(rendered_html)
        if jsonld_result and len(jsonld_result.content) >= 400:
            jsonld_result.source = "headless_dom"  # Mark as browser-sourced
            return jsonld_result

        # DOM extraction from rendered content
        dom_result = extract_dom_scored(rendered_html)
        if dom_result and len(dom_result.content) >= 300:  # Lower threshold for browser (already expensive)
            # Boost confidence slightly - browser-rendered is more complete than static
            dom_result.source = "headless_dom"
            return dom_result

        return None

    except Exception as exc:
        logger.error("JD browser: error rendering %s: %s", url, exc)
        return None
    finally:
        semaphore.release()
        await gate.release()
        if page:
            try:
                await page.close()
            except Exception:
                pass
        if context:
            try:
                await context.close()
            except Exception:
                pass


def _make_render_gate() -> DistributedRenderGate:
    """Build the cross-worker render gate from config + the shared KV store."""
    from app.config import settings
    from app.auth.runtime import get_kvstore
    return DistributedRenderGate(get_kvstore(), settings.jd_distributed_render_max)


async def _render_via_edge_and_extract(url: str) -> ExtractionResult | None:
    """Render via the external edge service, then run the normal extractors."""
    html = await render_via_edge(url)
    if not html or len(html) < 200:
        return None
    jsonld_result = extract_jsonld(html)
    if jsonld_result and len(jsonld_result.content) >= 400:
        jsonld_result.source = "headless_dom"
        return jsonld_result
    dom_result = extract_dom_scored(html)
    if dom_result and len(dom_result.content) >= 300:
        dom_result.source = "headless_dom"
        return dom_result
    return None
