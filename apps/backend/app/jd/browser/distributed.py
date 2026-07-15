"""Distributed browser pool + edge rendering (§Production Architecture, Phase 4).

Two capabilities that let browser rendering scale beyond a single worker:

1. **DistributedRenderGate** — a KV-backed global concurrency limiter. The
   per-process ``asyncio.Semaphore`` in ``pool.py`` bounds renders *within* one
   worker; this bounds the TOTAL concurrent renders across ALL workers/instances
   sharing the KV store (Redis in production). Prevents N workers × 3 contexts
   from overwhelming memory/CPU or a shared render budget. Degrades to a no-op
   when ``max_concurrent <= 0`` (per-process limiting only) or when the KV op
   fails (fail-open — never block a render on a bookkeeping error).

2. **render_via_edge** — delegate rendering to an external/edge renderer
   (e.g. a browserless.io / self-hosted Chromium farm) via ``jd_edge_render_url``.
   Returns the rendered HTML so the normal extractors run on it. This offloads
   the heavy browser process off the API workers entirely (edge rendering).
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

__all__ = ["DistributedRenderGate", "render_via_edge", "edge_render_configured"]

_SLOT_TTL = 30  # seconds; a slot auto-expires so a crashed holder can't wedge the gate
_EDGE_TIMEOUT = 20.0


class DistributedRenderGate:
    """KV-backed global render-slot limiter (cross-worker backpressure)."""

    def __init__(self, kv, max_concurrent: int):
        self._kv = kv
        self._max = max_concurrent
        self._key = "jd:render:slots"
        self._acquired = False

    @property
    def enabled(self) -> bool:
        return self._max > 0

    async def acquire(self) -> bool:
        """Try to reserve a global render slot. Returns True if reserved/allowed.

        Fail-open: if the limiter is disabled or the KV errors, allow the render.
        """
        if not self.enabled:
            return True
        try:
            n = await self._kv.incr(self._key, ttl_seconds=_SLOT_TTL)
        except Exception:
            logger.debug("JD render gate: KV incr failed, allowing render", exc_info=True)
            return True
        if n > self._max:
            # Over capacity — give the slot back and refuse.
            try:
                await self._kv.incr(self._key, amount=-1, ttl_seconds=_SLOT_TTL)
            except Exception:
                pass
            return False
        self._acquired = True
        return True

    async def release(self) -> None:
        if not self.enabled or not self._acquired:
            return
        self._acquired = False
        try:
            n = await self._kv.incr(self._key, amount=-1, ttl_seconds=_SLOT_TTL)
            if n < 0:
                # Counter underflow (e.g. after a TTL reset) — clamp to 0.
                await self._kv.set(self._key, "0", ttl_seconds=_SLOT_TTL)
        except Exception:
            logger.debug("JD render gate: KV release failed", exc_info=True)

    async def __aenter__(self) -> bool:
        return await self.acquire()

    async def __aexit__(self, *exc) -> None:
        await self.release()


def edge_render_configured() -> bool:
    from app.config import settings
    return bool(getattr(settings, "jd_edge_render_url", "").strip())


async def render_via_edge(url: str) -> str | None:
    """Render ``url`` via the configured external edge renderer; return HTML.

    The edge endpoint is an operator-configured, trusted service. It receives
    ``{"url": ...}`` and must return either raw HTML (text/html) or JSON
    ``{"html": "..."}``. Returns None on any failure (caller falls back to the
    local browser pool).
    """
    from app.config import settings

    edge_url = (settings.jd_edge_render_url or "").strip()
    if not edge_url:
        return None

    import httpx

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=_EDGE_TIMEOUT) as client:
            resp = await client.post(edge_url, json={"url": url})
            if resp.status_code >= 400:
                logger.warning("JD edge render: HTTP %d", resp.status_code)
                return None
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                data = resp.json()
                html = data.get("html") or data.get("content") or ""
            else:
                html = resp.text
            elapsed = (time.perf_counter() - t0) * 1000
            logger.info("JD edge render: %d bytes in %.0fms", len(html), elapsed)
            return html or None
    except Exception as exc:
        logger.warning("JD edge render failed: %s", exc)
        return None
