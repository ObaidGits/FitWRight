"""Fetch dispatch for Job Discovery connectors.

A connector never talks to the network directly; it asks this module to fetch a
URL under a declared :data:`~app.job_discovery.models.FetchMode`:

* ``"http"``    → a bounded, streaming ``httpx`` GET. No browser, cheap, used for
  static/server-rendered pages (Req 5.1).
* ``"stealth"`` → delegates to the app-wide headless stealth browser in
  ``app/jd/browser`` (Patchright/Camoufox). The browser is a **single shared
  instance** and every stealth fetch is admitted through a concurrency gate so
  the pool stays bounded (Req 5.2, 5.4).

Both lanes honour the same byte and time bounds as the rest of the ``jd``
fetch stack (Req 5.3): a hard wall-clock timeout and a maximum response size,
past which the body is truncated (never buffered unbounded).

The stealth dependencies (``patchright``/``camoufox``) and the ``app/jd``
browser module are optional: they are imported lazily inside the stealth path,
so the base app — and the ``http`` lane — boot and run without them installed.

Design reference: ``.kiro/specs/job-discovery/design.md`` §5 (fetch lanes).
Requirements: 5.1, 5.2, 5.3, 5.4.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from app.config import settings
from app.job_discovery.models import FetchMode

# --------------------------------------------------------------------------- #
# Byte / time bounds (Req 5.3)
# --------------------------------------------------------------------------- #
# Prefer the shared bounds from the existing ``jd`` fetch stack so the discovery
# lanes stay consistent with the rest of the app. Fall back to conservative
# defaults when the ``jd`` module is not present (e.g. minimal deployments).
try:  # pragma: no cover - trivial import shim
    from app.jd.limits import (  # type: ignore
        FETCH_TIMEOUT_SECONDS as _JD_TIMEOUT_SECONDS,
        MAX_FETCH_BYTES as _JD_MAX_FETCH_BYTES,
    )
except Exception:  # pragma: no cover - jd module optional in this build
    _JD_TIMEOUT_SECONDS = 20.0
    _JD_MAX_FETCH_BYTES = 5_000_000  # 5 MB

DEFAULT_TIMEOUT_SECONDS: float = float(_JD_TIMEOUT_SECONDS)
DEFAULT_MAX_BYTES: int = int(_JD_MAX_FETCH_BYTES)

# A browser fetcher takes a URL plus bounds and returns a :class:`FetchResult`.
BrowserFetch = Callable[..., Awaitable["FetchResult"]]


# --------------------------------------------------------------------------- #
# Result / error types
# --------------------------------------------------------------------------- #
@dataclass
class FetchResult:
    """The raw payload of a single fetch, before any parsing/normalization."""

    url: str
    status: int
    text: str
    content_type: str | None = None
    final_url: str | None = None
    mode: FetchMode = "http"
    # True when the body hit :data:`DEFAULT_MAX_BYTES` and was truncated.
    truncated: bool = False


class FetchError(Exception):
    """A fetch failed for a reason the caller should treat as a source failure.

    Connectors catch this and record a
    :class:`~app.job_discovery.models.SourceFailure`; they never let it abort the
    whole fan-out.
    """


class FetchTimeoutError(FetchError):
    """The fetch exceeded its wall-clock time bound (Req 5.3)."""


# --------------------------------------------------------------------------- #
# Stealth-lane concurrency gate (Req 5.4)
# --------------------------------------------------------------------------- #
# The stealth browser is a single shared instance; admitting every stealth fetch
# through this semaphore caps how many render concurrently. In the full
# FitWright tree this mirrors / delegates to ``app/jd/concurrency`` — here it is
# sized directly from ``JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY`` (defaults to 1).
_stealth_semaphore: asyncio.Semaphore | None = None


def _get_stealth_semaphore() -> asyncio.Semaphore:
    """Return the process-wide stealth concurrency gate, creating it once."""
    global _stealth_semaphore
    if _stealth_semaphore is None:
        limit = max(1, int(settings.JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY))
        _stealth_semaphore = asyncio.Semaphore(limit)
    return _stealth_semaphore


def _reset_stealth_semaphore() -> None:
    """Drop the cached gate so it is rebuilt from current settings.

    Test hook: lets a test change ``JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY`` and
    observe the new limit. Not used on the hot path.
    """
    global _stealth_semaphore
    _stealth_semaphore = None


# --------------------------------------------------------------------------- #
# HTTP lane (Req 5.1, 5.3)
# --------------------------------------------------------------------------- #
async def _fetch_http(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    headers: dict[str, str] | None,
    client: httpx.AsyncClient | None,
) -> FetchResult:
    """Bounded, streaming HTTP GET.

    Streams the body and stops reading at ``max_bytes`` so a hostile/huge
    response can never balloon memory. ``client`` may be injected (shared pool
    or a test transport); otherwise a short-lived client is created and closed.
    """
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
    assert client is not None
    try:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        async with client.stream(
            "GET", url, headers=headers, timeout=timeout
        ) as response:
            status = response.status_code
            content_type = response.headers.get("content-type")
            final_url = str(response.url)
            encoding = response.encoding or "utf-8"
            async for chunk in response.aiter_bytes():
                if total + len(chunk) > max_bytes:
                    remaining = max_bytes - total
                    if remaining > 0:
                        chunks.append(chunk[:remaining])
                        total += remaining
                    truncated = True
                    break
                chunks.append(chunk)
                total += len(chunk)
    except httpx.TimeoutException as exc:
        raise FetchTimeoutError(f"http fetch timed out for {url}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"http fetch failed for {url}: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()

    text = b"".join(chunks).decode(encoding, errors="replace")
    return FetchResult(
        url=url,
        status=status,
        text=text,
        content_type=content_type,
        final_url=final_url,
        mode="http",
        truncated=truncated,
    )


# --------------------------------------------------------------------------- #
# Stealth lane (Req 5.2, 5.4)
# --------------------------------------------------------------------------- #
async def _default_browser_fetch(
    url: str, *, timeout: float, max_bytes: int
) -> FetchResult:
    """Render ``url`` through the shared ``app/jd`` stealth browser.

    Imported lazily so the base app (and the http lane) never depend on the
    optional stealth stack. A missing browser module is surfaced as a
    :class:`FetchError` — one recoverable source failure, not an import crash.
    """
    try:  # pragma: no cover - exercised via a mocked browser in tests
        from app.jd.browser import fetch_rendered  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise FetchError(
            f"stealth fetch unavailable (app/jd browser not installed): {exc}"
        ) from exc

    rendered = await fetch_rendered(url, timeout=timeout, max_bytes=max_bytes)
    if isinstance(rendered, FetchResult):
        return rendered
    text = (
        rendered
        if isinstance(rendered, str)
        else bytes(rendered).decode("utf-8", errors="replace")
    )
    raw = text.encode("utf-8")
    truncated = len(raw) > max_bytes
    if truncated:
        text = raw[:max_bytes].decode("utf-8", errors="replace")
    return FetchResult(
        url=url,
        status=200,
        text=text,
        content_type="text/html",
        final_url=url,
        mode="stealth",
        truncated=truncated,
    )


async def _fetch_stealth(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    browser_fetch: BrowserFetch | None,
    semaphore: asyncio.Semaphore | None,
) -> FetchResult:
    """Run a stealth fetch admitted through the concurrency gate (Req 5.4)."""
    fetch_fn = browser_fetch or _default_browser_fetch
    gate = semaphore or _get_stealth_semaphore()
    async with gate:
        return await fetch_fn(url, timeout=timeout, max_bytes=max_bytes)


# --------------------------------------------------------------------------- #
# Public dispatch
# --------------------------------------------------------------------------- #
async def fetch(
    url: str,
    *,
    fetch_mode: FetchMode = "http",
    timeout: float | None = None,
    max_bytes: int | None = None,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    browser_fetch: BrowserFetch | None = None,
    semaphore: asyncio.Semaphore | None = None,
) -> FetchResult:
    """Fetch ``url`` using the lane named by ``fetch_mode``.

    Args:
        url: Absolute URL to fetch. SSRF validation is the caller's job (the
            site-recipe connector validates via ``app/jd/ssrf`` before calling).
        fetch_mode: ``"http"`` (bounded httpx) or ``"stealth"`` (headless
            browser via ``app/jd/browser``).
        timeout: Wall-clock bound in seconds; defaults to the shared jd bound.
        max_bytes: Response-size cap; body is truncated past this.
        headers: Optional request headers for the http lane.
        client: Optional injected ``httpx.AsyncClient`` (shared pool / test
            transport) for the http lane.
        browser_fetch: Optional injected browser fetcher for the stealth lane
            (tests pass a mock; production uses the lazy ``app/jd`` browser).
        semaphore: Optional explicit stealth concurrency gate; defaults to the
            process-wide gate sized by ``JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY``.

    Returns:
        A :class:`FetchResult` with the (possibly truncated) body.

    Raises:
        FetchError / FetchTimeoutError: on a recoverable fetch failure.
        ValueError: on an unknown ``fetch_mode``.
    """
    effective_timeout = DEFAULT_TIMEOUT_SECONDS if timeout is None else timeout
    effective_max_bytes = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes

    if fetch_mode == "http":
        return await _fetch_http(
            url,
            timeout=effective_timeout,
            max_bytes=effective_max_bytes,
            headers=headers,
            client=client,
        )
    if fetch_mode == "stealth":
        return await _fetch_stealth(
            url,
            timeout=effective_timeout,
            max_bytes=effective_max_bytes,
            browser_fetch=browser_fetch,
            semaphore=semaphore,
        )
    raise ValueError(f"unknown fetch_mode: {fetch_mode!r}")


__all__ = [
    "FetchResult",
    "FetchError",
    "FetchTimeoutError",
    "FetchMode",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_BYTES",
    "fetch",
]
