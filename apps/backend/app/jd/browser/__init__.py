"""Browser rendering subsystem for JD extraction (Phase 1)."""

from app.jd.browser.pool import get_browser_pool, close_browser_pool
from app.jd.browser.decision import needs_browser
from app.jd.browser.render import render_and_extract

__all__ = ["get_browser_pool", "close_browser_pool", "needs_browser", "render_and_extract"]
