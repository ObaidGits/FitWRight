"""Pluggable object-storage provider (design §H, ADR-10).

``STORAGE_PROVIDER`` selects the adapter with no code change:
- ``local`` (dev default) - writes under ``data/avatars`` and serves via a
  backend static path; a real, working adapter (not a stub).
- ``cloudinary`` (free tier) - uploads via the Cloudinary REST API using a
  server-side signature (no browser secret), returns the CDN URL.
- ``s3`` (premium) - reserved; raises a clear error until configured.

The interface is intentionally tiny: ``put`` (bytes -> key + public URL) and
``delete`` (key), which is all the avatar pipeline + orphan GC need.
"""

from __future__ import annotations

import abc
import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "StorageProvider",
    "StorageError",
    "LocalStorageProvider",
    "CloudinaryStorageProvider",
    "CloudinaryTransport",
    "get_storage_provider",
    "reset_storage_provider",
]


class StorageError(Exception):
    """A storage operation failed after exhausting retries (normalized).

    The avatar router maps a ``put`` failure to a clean 503 ``storage_unavailable``
    so a provider outage never leaks internals or dangling URLs.
    """


# HTTP statuses worth retrying (transient): request timeout, too-early, rate
# limit, and the 5xx family. A 4xx like 400/401 is a permanent error - no retry.
_TRANSIENT_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class StorageProvider(abc.ABC):
    """Minimal object-storage contract used by the avatar pipeline."""

    @abc.abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Store ``data`` at ``key`` and return a public/served URL."""

    @abc.abstractmethod
    async def delete(self, key: str) -> None:
        """Delete the object at ``key`` (no-op if absent)."""


class LocalStorageProvider(StorageProvider):
    """Filesystem-backed dev adapter (served via ``/api/v1/media/...``)."""

    def __init__(self, root: Path, base_url: str) -> None:
        self._root = root
        self._base_url = base_url.rstrip("/")
        self._root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        # Prevent path traversal - the key is server-generated, but defense in depth.
        target = (self._root / key).resolve()
        if not str(target).startswith(str(self._root.resolve())):
            raise ValueError("invalid storage key")
        return target

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        path = self._safe_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"{self._base_url}/api/v1/media/{key}"

    async def delete(self, key: str) -> None:
        try:
            self._safe_path(key).unlink(missing_ok=True)
        except (ValueError, OSError):  # pragma: no cover - best-effort delete
            logger.debug("Local storage delete failed for %s", key, exc_info=True)


class CloudinaryTransport(Protocol):
    """Injectable HTTP seam so the Cloudinary adapter is testable offline.

    Implementations POST a multipart/form request and return
    ``(status_code, json_body)``. The default is httpx-backed; tests supply a
    fake to drive success/transient-failure/permanent-failure paths without a
    live Cloudinary account or any network.
    """

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        files: dict[str, Any] | None = None,
    ) -> tuple[int, dict]:
        ...


class _HttpxCloudinaryTransport:
    """Default httpx-backed transport (fixed Cloudinary host - SSRF-safe)."""

    def __init__(self, *, timeout: float = 15.0) -> None:
        self._timeout = timeout

    async def post(
        self,
        url: str,
        *,
        data: dict[str, str],
        files: dict[str, Any] | None = None,
    ) -> tuple[int, dict]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, data=data, files=files)
        try:
            payload = resp.json() if resp.content else {}
        except ValueError:
            payload = {}
        return resp.status_code, payload if isinstance(payload, dict) else {}


class CloudinaryStorageProvider(StorageProvider):
    """Cloudinary REST adapter (server-signed upload; returns the CDN URL).

    Uses the documented signed-upload endpoint over an injectable transport (no
    SDK dependency). Selected only when ``CLOUDINARY_*`` config is present;
    otherwise the factory falls back to local so the app always boots.

    Production hardening: bounded **retries with exponential backoff** on
    transient failures (network errors, 429, 5xx), a hard per-request
    **timeout**, and permanent 4xx failures that fail fast (no wasteful retry).
    ``put`` raises :class:`StorageError` on exhaustion (router -> 503); ``delete``
    is best-effort (a failed delete only leaves an orphan the GC job reclaims).
    """

    def __init__(
        self,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        *,
        transport: CloudinaryTransport | None = None,
        max_attempts: int = 3,
        base_delay: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._cloud = cloud_name
        self._key = api_key
        self._secret = api_secret
        self._transport: CloudinaryTransport = transport or _HttpxCloudinaryTransport()
        self._max_attempts = max(1, max_attempts)
        self._base_delay = base_delay
        self._sleep = sleep or asyncio.sleep

    def _sign(self, params: dict[str, str]) -> str:
        import hashlib

        to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params)) + self._secret
        return hashlib.sha1(to_sign.encode()).hexdigest()

    async def _post_with_retry(
        self, url: str, *, data: dict[str, str], files: dict[str, Any] | None
    ) -> dict:
        """POST with bounded exponential backoff. Raises StorageError on failure."""
        last_detail = "unknown"
        for attempt in range(self._max_attempts):
            is_last = attempt == self._max_attempts - 1
            try:
                status, payload = await self._transport.post(url, data=data, files=files)
            except Exception as exc:  # transport/network error -> transient
                last_detail = f"transport error: {exc!r}"
                if is_last:
                    raise StorageError(last_detail) from exc
                await self._sleep(self._base_delay * (2**attempt))
                continue
            if status < 400:
                return payload
            if status in _TRANSIENT_STATUS and not is_last:
                last_detail = f"transient status {status}"
                await self._sleep(self._base_delay * (2**attempt))
                continue
            # Permanent 4xx (or transient on the last attempt) -> fail.
            raise StorageError(f"cloudinary responded {status}")
        raise StorageError(last_detail)  # pragma: no cover - loop always returns/raises

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        import time

        timestamp = str(int(time.time()))
        public_id = key.rsplit(".", 1)[0]
        params = {"public_id": public_id, "timestamp": timestamp, "overwrite": "true"}
        signature = self._sign(params)
        url = f"https://api.cloudinary.com/v1_1/{self._cloud}/image/upload"
        form = {**params, "api_key": self._key, "signature": signature}
        payload = await self._post_with_retry(
            url, data=form, files={"file": (key, data, content_type)}
        )
        secure_url = payload.get("secure_url")
        if not secure_url or not isinstance(secure_url, str):
            raise StorageError("cloudinary response missing secure_url")
        return secure_url

    async def delete(self, key: str) -> None:
        import time

        public_id = key.rsplit(".", 1)[0]
        timestamp = str(int(time.time()))
        params = {"public_id": public_id, "timestamp": timestamp}
        signature = self._sign(params)
        url = f"https://api.cloudinary.com/v1_1/{self._cloud}/image/destroy"
        form = {**params, "api_key": self._key, "signature": signature}
        try:
            await self._post_with_retry(url, data=form, files=None)
        except StorageError:
            # Best-effort: a failed delete only leaves an orphan for the GC job.
            logger.warning("Cloudinary delete failed for %s (orphan left for GC)", key)


def build_storage_provider(config) -> StorageProvider:
    """Construct the ``StorageProvider`` selected by ``STORAGE_PROVIDER`` (pure).

    Cloudinary when configured, else the local disk provider; ``s3`` is
    reserved. Called only by the composition root (IMPLEMENTATION_PLAN Phase 3).
    """
    choice = config.storage_provider
    if choice == "cloudinary" and config.cloudinary_configured:
        return CloudinaryStorageProvider(
            config.cloudinary_cloud_name, config.cloudinary_api_key, config.cloudinary_api_secret
        )
    if choice == "s3":
        raise RuntimeError("STORAGE_PROVIDER=s3 is not configured in this build.")
    return LocalStorageProvider(config.data_dir / "avatars", config.frontend_base_url)


def get_storage_provider() -> StorageProvider:
    """Return the process-wide storage provider (owned by the composition root)."""
    from app.platform import get_container

    return get_container().storage()


def reset_storage_provider() -> None:
    """Test hook: drop the storage adapter so it rebinds to the current settings.

    Delegates to the composition root's container reset (single cache owner).
    """
    from app.platform import reset_container

    reset_container()
