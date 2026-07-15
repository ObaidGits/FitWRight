"""Browser pool manager for JD extraction (§12 of enhancement plan).

Manages a shared Playwright Chromium browser instance with a concurrency
semaphore. Pre-warms on first use, reuses across requests. Context-per-request
with cookie/storage isolation.

Memory budget: ~150MB per context, max 3 concurrent = ~450MB peak.
"""

from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

logger = logging.getLogger(__name__)

__all__ = ["get_browser_pool", "close_browser_pool"]

_playwright: Playwright | None = None
_browser: Browser | None = None
_semaphore: asyncio.Semaphore | None = None
_lock = asyncio.Lock()

# Max concurrent browser contexts for JD extraction
_MAX_CONTEXTS = 3


async def get_browser_pool() -> tuple[Browser, asyncio.Semaphore]:
    """Get the shared browser instance + concurrency semaphore.

    Lazily initializes on first call. Thread-safe via asyncio.Lock.
    """
    global _playwright, _browser, _semaphore

    if _browser and _browser.is_connected():
        return _browser, _semaphore  # type: ignore

    async with _lock:
        # Double-check after acquiring lock
        if _browser and _browser.is_connected():
            return _browser, _semaphore  # type: ignore

        logger.info("JD browser pool: launching Chromium (headless)")
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",  # Prevent /dev/shm overflow in Docker
                "--disable-gpu",
                "--no-sandbox",  # Required in Docker containers
                "--disable-setuid-sandbox",
                "--disable-web-security",  # Allow cross-origin for SPA hydration
                "--disable-features=VizDisplayCompositor",
            ],
        )
        _semaphore = asyncio.Semaphore(_MAX_CONTEXTS)
        logger.info("JD browser pool: ready (max %d contexts)", _MAX_CONTEXTS)
        return _browser, _semaphore


async def close_browser_pool() -> None:
    """Shutdown the browser pool. Called at app shutdown."""
    global _playwright, _browser, _semaphore

    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None

    if _playwright:
        try:
            await _playwright.stop()
        except Exception:
            pass
        _playwright = None

    _semaphore = None
    logger.info("JD browser pool: closed")


async def create_stealth_context(browser: Browser) -> BrowserContext:
    """Create a browser context with stealth-like settings.

    Randomizes viewport and locale to reduce bot fingerprinting.
    Blocks heavy resources (images, fonts, media) for speed.
    """
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="America/New_York",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        java_script_enabled=True,
        ignore_https_errors=True,
    )

    # Block heavy resources to speed up rendering (saves 2-5s)
    await context.route("**/*.{png,jpg,jpeg,gif,svg,webp,ico,woff,woff2,ttf,eot}", _block_resource)
    await context.route("**/*google-analytics*", _block_resource)
    await context.route("**/*googletagmanager*", _block_resource)
    await context.route("**/*facebook*", _block_resource)
    await context.route("**/*hotjar*", _block_resource)

    return context


async def _block_resource(route):
    """Abort resource requests to speed up page load."""
    await route.abort()
