"""SSRF-hardened URL fetcher (design §D / threat model, Requirement 9.2 / Property 4).

Defense layers, all enforced here:
- **scheme allow-list** (http/https only) + **port allow-list** (80/443);
- **DNS resolved once, connection pinned to the validated IP** (anti
  DNS-rebinding) - the TLS SNI/cert still use the original hostname;
- **every resolved IP validated** against private/loopback/link-local/CGNAT/
  metadata/reserved ranges (IPv4 + IPv6, incl. IPv4-mapped);
- **each redirect hop re-validated** (max 3), never auto-followed by the client;
- **streamed byte cap** enforced during read (Content-Length never trusted) with
  a **decompression-bomb** bound (the cap is on *decompressed* bytes);
- **wall-clock timeout**; **no auth headers forwarded**; **no internal error or
  header leakage** to the caller (a single opaque ``fetch_failed``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "SsrfError", "validate_fetch_url", "is_ip_blocked",
    "fetch_url_safely", "fetch_raw_safely",
]

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}
_MAX_REDIRECTS = 3
_MAX_BYTES = 3 * 1024 * 1024  # 3 MiB decompressed cap
_MAX_BYTES_BINARY = 10 * 1024 * 1024  # 10 MiB cap for binary (PDF) fetches (§20)
_TIMEOUT_SECONDS = 10.0
_BINARY_TIMEOUT_SECONDS = 30.0  # PDFs/OCR sources may be larger/slower (§20)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_USER_AGENT = "FitWrightBot/1.0 (+job-description-import)"


class SsrfError(Exception):
    """A fetch was blocked/failed. ``reason`` is for logs/metrics only.

    The router surfaces a single opaque ``fetch_failed`` to the caller - the
    reason (blocked IP, bad scheme, timeout, too big) is never leaked, so the
    endpoint can't be used as an internal port/host scanner.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def is_ip_blocked(ip_str: str) -> bool:
    """Whether ``ip_str`` is a non-publicly-routable / dangerous address."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    # Unwrap IPv4-mapped IPv6 (::ffff:a.b.c.d) so the v4 rules apply.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if not ip.is_global:
        # Covers private, loopback, link-local (incl. 169.254.169.254 metadata),
        # reserved, multicast, unspecified.
        return True
    if ip.version == 4 and ip in _CGNAT:
        return True
    return False


def validate_fetch_url(url: str) -> tuple[str, str, int]:
    """Validate scheme + port; return ``(scheme, host, port)`` or raise SsrfError."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfError(f"scheme_not_allowed:{scheme}")
    host = parsed.hostname
    if not host:
        raise SsrfError("no_host")
    port = parsed.port or (443 if scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise SsrfError(f"port_not_allowed:{port}")
    return scheme, host, port


async def _resolve_and_pin(host: str, port: int) -> str:
    """Resolve ``host`` once, validate every address, return one pinned IP."""
    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, host, port, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise SsrfError("dns_failure") from exc
    ips = [info[4][0] for info in infos]
    if not ips:
        raise SsrfError("dns_empty")
    # Conservative: if ANY resolved address is blocked, refuse (a domain that
    # resolves to a mix of public + private is treated as hostile).
    for ip in ips:
        if is_ip_blocked(ip):
            raise SsrfError(f"blocked_ip:{ip}")
    return ips[0]


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Force the connection to a pre-validated IP while keeping hostname TLS.

    Rewrites the request URL host to the pinned IP (so the socket connects to the
    address we already validated - closing the DNS-rebinding TOCTOU) and sets the
    ``sni_hostname`` extension + ``Host`` header to the original hostname so TLS
    SNI, certificate verification, and virtual-host routing all stay correct.
    """

    def __init__(self, hostname: str, pinned_ip: str, **kw) -> None:
        super().__init__(**kw)
        self._hostname = hostname
        self._pinned_ip = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_host = request.url.host
        if original_host == self._hostname:
            request.headers["Host"] = (
                self._hostname
                if request.url.port in (None, 80, 443)
                else f"{self._hostname}:{request.url.port}"
            )
            request.url = request.url.copy_with(host=self._pinned_ip)
            request.extensions = {**request.extensions, "sni_hostname": self._hostname}
        return await super().handle_async_request(request)


async def fetch_url_safely(url: str) -> str:
    """Fetch ``url`` HTML with the full SSRF guard. Raises SsrfError on any issue."""
    try:
        return await asyncio.wait_for(_fetch(url), timeout=_TIMEOUT_SECONDS + 2)
    except asyncio.TimeoutError as exc:
        raise SsrfError("timeout") from exc


async def fetch_raw_safely(
    url: str, *, accept: str = "*/*", max_bytes: int = _MAX_BYTES_BINARY
) -> tuple[bytes, str]:
    """Fetch ``url`` and return ``(raw_bytes, content_type)`` with the full SSRF guard.

    Used for binary payloads (e.g. PDFs) where the text decoder is inappropriate.
    Enforces a larger byte cap (default 10 MiB) and a longer wall-clock timeout.
    Raises :class:`SsrfError` on any issue.
    """
    try:
        return await asyncio.wait_for(
            _fetch_raw(url, accept=accept, max_bytes=max_bytes),
            timeout=_BINARY_TIMEOUT_SECONDS + 2,
        )
    except asyncio.TimeoutError as exc:
        raise SsrfError("timeout") from exc


async def _fetch_raw(url: str, *, accept: str, max_bytes: int) -> tuple[bytes, str]:
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        scheme, host, port = validate_fetch_url(current)
        pinned = await _resolve_and_pin(host, port)
        transport = _PinnedTransport(host, pinned, retries=0)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(_BINARY_TIMEOUT_SECONDS),
                follow_redirects=False,
                max_redirects=0,
                headers={"User-Agent": _USER_AGENT, "Accept": accept},
            ) as client:
                async with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise SsrfError("redirect_no_location")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code >= 400:
                        raise SsrfError(f"http_{resp.status_code}")
                    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise SsrfError("too_large")
                        chunks.append(chunk)
                    return b"".join(chunks), content_type
        except httpx.HTTPError as exc:
            raise SsrfError("transport_error") from exc
        finally:
            await transport.aclose()
    raise SsrfError("too_many_redirects")


async def _fetch(url: str) -> str:
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        scheme, host, port = validate_fetch_url(current)
        pinned = await _resolve_and_pin(host, port)
        transport = _PinnedTransport(host, pinned, retries=0)
        try:
            async with httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(_TIMEOUT_SECONDS),
                follow_redirects=False,
                max_redirects=0,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            ) as client:
                async with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise SsrfError("redirect_no_location")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code >= 400:
                        raise SsrfError(f"http_{resp.status_code}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > _MAX_BYTES:
                            raise SsrfError("too_large")
                        chunks.append(chunk)
                    return b"".join(chunks).decode("utf-8", errors="replace")
        except httpx.HTTPError as exc:
            raise SsrfError("transport_error") from exc
        finally:
            await transport.aclose()
    raise SsrfError("too_many_redirects")
