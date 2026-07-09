"""Pluggable breached-password check (R13.3, ADR-14).

At signup and password change the new password can be checked against a breach
corpus (HIBP k-anonymity range API). This goes through the
:class:`BreachedPasswordCheck` interface so the concrete provider is a per-deploy
choice, and — critically — the check **fails open**: if the provider is
unavailable, the password is accepted (and the failure is logged), never
blocking a legitimate user because a third party is down (R13.3).

The shipped default, :class:`NoopBreachedPasswordCheck`, is the intended
local/dev behavior: no corpus configured, so nothing is ever reported as
breached (fail-open, logged). It is a real adapter, not a stub.
"""

from __future__ import annotations

import abc
import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "BreachResult",
    "BreachedPasswordCheck",
    "NoopBreachedPasswordCheck",
    "HibpRangeClient",
    "HttpxHibpRangeClient",
    "HibpBreachedPasswordCheck",
    "HIBP_RANGE_ENDPOINT",
]

# HaveIBeenPwned k-anonymity range endpoint. Only the first 5 hex chars of the
# SHA-1 of the password are ever appended to this URL; the remaining 35 chars
# (the "suffix") are matched locally against the returned list, so the password
# and its full hash never leave this process (the whole point of k-anonymity).
HIBP_RANGE_ENDPOINT = "https://api.pwnedpasswords.com/range"


@dataclass(frozen=True, slots=True)
class BreachResult:
    """Outcome of a breached-password check.

    ``breached`` is the decision callers branch on. ``count`` is the number of
    times the password was seen in breaches when known (0 otherwise), useful for
    a user-facing "seen N times" hint. ``checked`` is ``False`` when the check
    was skipped or the provider failed open, so callers/metrics can distinguish
    "verified clean" from "not actually checked".
    """

    breached: bool
    count: int = 0
    checked: bool = True


class BreachedPasswordCheck(abc.ABC):
    """Interface for checking whether a password appears in known breaches."""

    @abc.abstractmethod
    async def check(self, password: str) -> BreachResult:
        """Return whether ``password`` is known-breached. Implementations MUST
        fail open (return ``breached=False``) if the provider is unavailable."""


class NoopBreachedPasswordCheck(BreachedPasswordCheck):
    """Default adapter: no breach corpus configured, so nothing is breached.

    Fail-open by design and logged; swapping in a real HIBP-backed provider later
    is a config change, not a code change.
    """

    async def check(self, password: str) -> BreachResult:
        logger.debug("Breached-password check skipped (no provider configured); allowing")
        return BreachResult(breached=False, count=0, checked=False)


class HibpRangeClient(Protocol):
    """Transport for the HIBP range API, injected so the check is testable.

    Implementations receive **only** the 5-char SHA-1 prefix and return the raw
    range response body (lines of ``SUFFIX:COUNT``). Keeping the transport behind
    this seam means unit tests never touch the network and can assert exactly
    what was sent (the prefix, never the full hash/suffix).
    """

    async def get_range(self, prefix: str) -> str:
        """Return the raw range body for a 5-char SHA-1 ``prefix``."""
        ...


class HttpxHibpRangeClient:
    """Default httpx-backed range client (fixed endpoint — SSRF-safe).

    Bounded by ``timeout`` so a slow/hung provider can never stall auth; the
    caller (:class:`HibpBreachedPasswordCheck`) turns any raised error into a
    fail-open result.
    """

    def __init__(self, *, endpoint: str = HIBP_RANGE_ENDPOINT, timeout: float = 3.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout

    async def get_range(self, prefix: str) -> str:
        import httpx

        # "Add-Padding" asks HIBP to pad the response with synthetic entries so
        # the response size can't hint at how many real suffixes matched.
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(
                f"{self._endpoint}/{prefix}",
                headers={"Add-Padding": "true"},
            )
        resp.raise_for_status()
        return resp.text


class HibpBreachedPasswordCheck(BreachedPasswordCheck):
    """Real breached-password check via the HIBP k-anonymity range API (R13.3).

    Flow: SHA-1 the password, split into a 5-char ``prefix`` + 35-char
    ``suffix``, ask the range API for all suffixes sharing that prefix, and look
    for our suffix in the response. **Only the prefix is ever transmitted** —
    the password and its full hash stay in this process.

    Fails **open** on any transport/parse error (returns ``breached=False,
    checked=False`` with a logged warning) so a HIBP outage never blocks a
    legitimate signup or password change (R13.3). No credentials required.
    """

    def __init__(self, client: HibpRangeClient | None = None) -> None:
        self._client = client or HttpxHibpRangeClient()

    async def check(self, password: str) -> BreachResult:
        if not password:
            # Empty input isn't a breach signal; let the length/policy gate own it.
            return BreachResult(breached=False, count=0, checked=False)

        digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        prefix, suffix = digest[:5], digest[5:]

        try:
            body = await self._client.get_range(prefix)
        except Exception:  # noqa: BLE001 - any transport error → fail open
            logger.warning(
                "Breached-password check failed (HIBP unavailable); allowing (fail-open)",
                exc_info=True,
            )
            return BreachResult(breached=False, count=0, checked=False)

        count = _parse_range_count(body, suffix)
        if count > 0:
            return BreachResult(breached=True, count=count, checked=True)
        return BreachResult(breached=False, count=0, checked=True)


def _parse_range_count(body: str, suffix: str) -> int:
    """Return the breach count for ``suffix`` in a HIBP range body, else 0.

    Each line is ``SUFFIX:COUNT``. Padding entries HIBP adds have a count of 0,
    so they never produce a false positive. Malformed lines are skipped.
    """
    suffix = suffix.upper()
    for line in body.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        candidate, _, raw_count = line.partition(":")
        if candidate.strip().upper() != suffix:
            continue
        try:
            return int(raw_count.strip())
        except ValueError:
            return 0
    return 0
