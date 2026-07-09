"""Pluggable CAPTCHA verifier (R13.2, ADR-14).

Beyond a soft failure threshold, auth endpoints can require a CAPTCHA/Turnstile
challenge. Verification goes through the :class:`CaptchaVerifier` interface so a
concrete provider (Cloudflare Turnstile, hCaptcha, …) is wired per deployment
without touching the auth flows.

The shipped default, :class:`AllowAllCaptchaVerifier`, is the intended local/dev
behavior: it **allows** every request (no challenge configured) and logs that it
did so — a deliberate fail-open, consistent with the design's "pluggable hook,
concrete choice per deploy". It is a real adapter, not a stub.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "CaptchaResult",
    "CaptchaVerifier",
    "AllowAllCaptchaVerifier",
    "SiteverifyClient",
    "HttpxSiteverifyClient",
    "TurnstileCaptchaVerifier",
    "TURNSTILE_SITEVERIFY_ENDPOINT",
]

# Cloudflare Turnstile server-side verification endpoint (fixed — SSRF-safe).
TURNSTILE_SITEVERIFY_ENDPOINT = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


@dataclass(frozen=True, slots=True)
class CaptchaResult:
    """Outcome of a CAPTCHA verification.

    ``allowed`` is the only value callers must branch on. ``reason`` is a short
    machine-readable tag for logging/metrics (e.g. ``"disabled"``,
    ``"provider_error"``, ``"invalid_token"``).
    """

    allowed: bool
    reason: str = ""


class CaptchaVerifier(abc.ABC):
    """Interface for verifying a CAPTCHA challenge token."""

    @abc.abstractmethod
    async def verify(self, token: str | None, *, remote_ip: str | None = None) -> CaptchaResult:
        """Verify ``token`` (optionally bound to ``remote_ip``)."""


class AllowAllCaptchaVerifier(CaptchaVerifier):
    """Default adapter: no CAPTCHA configured, so every request is allowed.

    Fail-open by design and logged, so the decision is auditable and swapping in
    a real provider later is a config change, not a code change.
    """

    async def verify(self, token: str | None, *, remote_ip: str | None = None) -> CaptchaResult:
        logger.debug("CAPTCHA verification skipped (no provider configured); allowing request")
        return CaptchaResult(allowed=True, reason="disabled")


class SiteverifyClient(Protocol):
    """Transport for a CAPTCHA siteverify POST, injected so the verifier is testable."""

    async def post_form(self, url: str, data: dict[str, str]) -> dict:
        """POST ``data`` as a form to ``url`` and return the decoded JSON object."""
        ...


class HttpxSiteverifyClient:
    """Default httpx-backed siteverify client (fixed endpoint — SSRF-safe).

    Bounded by ``timeout`` so a slow provider can never stall an auth request;
    the caller turns any raised error into a fail-open ``allowed=True`` result.
    """

    def __init__(self, *, timeout: float = 5.0) -> None:
        self._timeout = timeout

    async def post_form(self, url: str, data: dict[str, str]) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("siteverify returned a non-object response")
        return payload


class TurnstileCaptchaVerifier(CaptchaVerifier):
    """Real Cloudflare Turnstile verifier (R13.2).

    Posts the client-supplied token (and optional remote IP) to Turnstile's
    ``siteverify`` endpoint with the deploy's secret and branches on the
    ``success`` field. Consistent with the design's fail-open posture (R13),
    any provider/transport error results in ``allowed=True`` with a logged
    warning — a CAPTCHA provider outage must never lock users out — while a
    well-formed ``success=false`` response is a real ``allowed=False`` decision.

    Selected by ``CAPTCHA_PROVIDER=turnstile``; requires ``CAPTCHA_SECRET``. A
    missing token is rejected up front (nothing to verify).
    """

    def __init__(
        self,
        *,
        secret: str,
        client: SiteverifyClient | None = None,
        endpoint: str = TURNSTILE_SITEVERIFY_ENDPOINT,
    ) -> None:
        self._secret = secret
        self._client = client or HttpxSiteverifyClient()
        self._endpoint = endpoint

    async def verify(self, token: str | None, *, remote_ip: str | None = None) -> CaptchaResult:
        if not token:
            return CaptchaResult(allowed=False, reason="missing_token")

        data = {"secret": self._secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip

        try:
            payload = await self._client.post_form(self._endpoint, data)
        except Exception:  # noqa: BLE001 - any transport error → fail open (R13)
            logger.warning(
                "CAPTCHA verification failed (Turnstile unavailable); allowing (fail-open)",
                exc_info=True,
            )
            return CaptchaResult(allowed=True, reason="provider_error")

        if payload.get("success") is True:
            return CaptchaResult(allowed=True, reason="verified")
        return CaptchaResult(allowed=False, reason="invalid_token")
