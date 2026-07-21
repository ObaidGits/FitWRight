"""Rate limiting, lockout, backoff, and CAPTCHA gating (Task 2.4).

Auth is the most-attacked surface, so this module concentrates the abuse
controls the design (`§Abuse`, R13.1/13.2/13.4/13.5) requires:

- **Fixed-window counters** per ``(ip, route-class)`` and per ``user_id`` via the
  KVStore's atomic :meth:`~app.auth.kvstore.base.KVStore.incr` (TTL applied on
  key creation -> the window). This is the token-bucket-equivalent used across
  workers because the counter lives in shared state (ADR-6).
- **Exponential backoff + account lockout.** Repeated auth failures for an
  account/IP raise a backoff delay (``base * 2^(failures-1)``, capped) and, past
  a hard threshold, lock the account for a cooldown - audited by the caller as
  ``auth.login_failed`` / lockout.
- **CAPTCHA gating.** Past a *soft* failure threshold, :meth:`captcha_gate`
  requires a challenge, verified through the pluggable
  :class:`~app.auth.captcha.CaptchaVerifier` (fail-open when unconfigured).
- **Fail-closed on outage.** If the KVStore is unavailable, auth rate-limit
  checks **deny** with a ``Retry-After`` (R13.5) - an attacker must not get an
  unlimited window just because the shared store blinked. (Read-path scoping
  fails *open* elsewhere; that asymmetry is intentional.)

Everything is injectable (KVStore + CAPTCHA verifier + clock + tunable limits)
for isolated tests; :func:`get_rate_limiter` returns the process-wide instance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from app.auth.captcha import CaptchaResult, CaptchaVerifier
from app.auth.kvstore import KVStore

logger = logging.getLogger(__name__)

__all__ = [
    "RateLimitRule",
    "RateLimitResult",
    "LockoutResult",
    "RateLimiter",
    "AUTH_RULES",
    "get_rate_limiter",
    "reset_rate_limiter",
]


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """A fixed-window limit: at most ``limit`` events per ``window_seconds``."""

    limit: int
    window_seconds: int


# Per-route-class default rules (events per window). Auth endpoints are strict;
# these are the caller-facing knobs a router passes to :meth:`check`.
AUTH_RULES: dict[str, RateLimitRule] = {
    # Login/signup: tight per-IP window to blunt credential stuffing.
    "login": RateLimitRule(limit=10, window_seconds=60),
    "signup": RateLimitRule(limit=5, window_seconds=60),
    # Token issuance endpoints (verification/reset resend) - email amplification.
    "verify": RateLimitRule(limit=5, window_seconds=300),
    "reset": RateLimitRule(limit=5, window_seconds=300),
    # Step-up re-auth.
    "step_up": RateLimitRule(limit=10, window_seconds=300),
    # OAuth start/callback.
    "oauth": RateLimitRule(limit=20, window_seconds=60),
}


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Outcome of a rate-limit check.

    ``allowed`` is the decision. ``retry_after`` (seconds) is populated when
    denied so the caller can set the ``Retry-After`` header (429). ``count`` is
    the current window count, ``fail_closed`` marks a denial caused by a KVStore
    outage rather than genuine over-limit traffic.
    """

    allowed: bool
    retry_after: int = 0
    count: int = 0
    fail_closed: bool = False


@dataclass(frozen=True, slots=True)
class LockoutResult:
    """State of an account/IP lockout counter after registering a failure."""

    locked: bool
    failures: int
    retry_after: int = 0
    backoff_seconds: int = 0
    captcha_required: bool = False


class RateLimiter:
    """Fixed-window rate limiting + lockout/backoff over a :class:`KVStore`."""

    # Namespacing so counters never collide with other KVStore users.
    _RL_PREFIX = "rl"
    _FAIL_PREFIX = "authfail"
    _LOCK_PREFIX = "authlock"

    def __init__(
        self,
        kvstore: KVStore,
        *,
        captcha: CaptchaVerifier | None = None,
        clock: Callable[[], datetime] | None = None,
        max_failures: int = 10,
        soft_failure_threshold: int = 3,
        lockout_seconds: int = 900,
        failure_window_seconds: int = 900,
        backoff_base_seconds: int = 1,
        backoff_max_seconds: int = 60,
    ) -> None:
        self._kv = kvstore
        self._captcha = captcha
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._max_failures = max_failures
        self._soft_threshold = soft_failure_threshold
        self._lockout_seconds = lockout_seconds
        self._failure_window = failure_window_seconds
        self._backoff_base = backoff_base_seconds
        self._backoff_max = backoff_max_seconds

    # -- fixed-window limiting ----------------------------------------------

    async def check(
        self,
        route_class: str,
        identifier: str,
        rule: RateLimitRule | None = None,
        *,
        fail_closed: bool = True,
    ) -> RateLimitResult:
        """Register one hit against ``(route_class, identifier)`` and decide.

        ``rule`` defaults to :data:`AUTH_RULES` for the route class (or a modest
        fallback). On a KVStore outage the result honors ``fail_closed``: auth
        endpoints deny with ``Retry-After`` (R13.5), while a caller that opts out
        (``fail_closed=False``) is allowed through.
        """
        effective = rule or AUTH_RULES.get(route_class) or RateLimitRule(30, 60)
        key = f"{self._RL_PREFIX}:{route_class}:{identifier}"
        try:
            count = await self._kv.incr(key, ttl_seconds=effective.window_seconds)
        except Exception:
            logger.warning(
                "Rate-limit store unavailable for %s; fail_closed=%s", key, fail_closed
            )
            if fail_closed:
                return RateLimitResult(
                    allowed=False,
                    retry_after=effective.window_seconds,
                    fail_closed=True,
                )
            return RateLimitResult(allowed=True, fail_closed=True)
        if count > effective.limit:
            return RateLimitResult(
                allowed=False, retry_after=effective.window_seconds, count=count
            )
        return RateLimitResult(allowed=True, count=count)

    # -- failure tracking / lockout / backoff -------------------------------

    def _backoff_for(self, failures: int) -> int:
        """Exponential backoff (seconds) for the Nth consecutive failure."""
        if failures <= self._soft_threshold:
            return 0
        exponent = failures - self._soft_threshold - 1
        return min(self._backoff_base * (2**exponent), self._backoff_max)

    async def register_failure(self, identifier: str) -> LockoutResult:
        """Record an auth failure for ``identifier`` and return the new state.

        Increments a windowed failure counter; once it reaches ``max_failures``
        the account/IP is locked for ``lockout_seconds``. A KVStore outage
        fails closed (reports locked) so failures are never silently forgotten.
        """
        fail_key = f"{self._FAIL_PREFIX}:{identifier}"
        try:
            failures = await self._kv.incr(fail_key, ttl_seconds=self._failure_window)
        except Exception:
            logger.warning("Failure-counter store unavailable for %s; failing closed", identifier)
            return LockoutResult(
                locked=True, failures=self._max_failures, retry_after=self._lockout_seconds
            )

        backoff = self._backoff_for(failures)
        captcha_required = failures > self._soft_threshold

        if failures >= self._max_failures:
            lock_key = f"{self._LOCK_PREFIX}:{identifier}"
            try:
                await self._kv.set(
                    lock_key, "1", ttl_seconds=self._lockout_seconds
                )
            except Exception:
                logger.warning("Could not persist lockout for %s", identifier)
            return LockoutResult(
                locked=True,
                failures=failures,
                retry_after=self._lockout_seconds,
                backoff_seconds=backoff,
                captcha_required=captcha_required,
            )
        return LockoutResult(
            locked=False,
            failures=failures,
            backoff_seconds=backoff,
            captcha_required=captcha_required,
        )

    async def is_locked_out(self, identifier: str) -> LockoutResult:
        """Return the current lockout state without recording a failure.

        Fails closed on a KVStore outage (treats as locked) so an attacker
        cannot bypass an existing lockout by knocking the store over.
        """
        lock_key = f"{self._LOCK_PREFIX}:{identifier}"
        fail_key = f"{self._FAIL_PREFIX}:{identifier}"
        try:
            locked = await self._kv.get(lock_key)
            failures_raw = await self._kv.get(fail_key)
        except Exception:
            logger.warning("Lockout store unavailable for %s; failing closed", identifier)
            return LockoutResult(
                locked=True, failures=self._max_failures, retry_after=self._lockout_seconds
            )
        failures = int(failures_raw) if failures_raw and failures_raw.isdigit() else 0
        if locked is not None:
            return LockoutResult(
                locked=True,
                failures=failures,
                retry_after=self._lockout_seconds,
                captcha_required=True,
            )
        return LockoutResult(
            locked=False,
            failures=failures,
            backoff_seconds=self._backoff_for(failures),
            captcha_required=failures > self._soft_threshold,
        )

    async def clear_failures(self, identifier: str) -> None:
        """Clear the failure counter + lockout for ``identifier`` (on success)."""
        try:
            await self._kv.delete(f"{self._FAIL_PREFIX}:{identifier}")
            await self._kv.delete(f"{self._LOCK_PREFIX}:{identifier}")
        except Exception:
            logger.warning("Could not clear failure state for %s", identifier)

    # -- CAPTCHA gate --------------------------------------------------------

    async def captcha_gate(
        self, failures: int, token: str | None, *, remote_ip: str | None = None
    ) -> CaptchaResult:
        """Decide whether a CAPTCHA is required and, if so, verify it.

        Below the soft threshold no challenge is needed (allowed). At/above it,
        the injected verifier decides - which, when unconfigured, fails open
        (allows) by design (R13.2/13.3). A ``None`` token past the threshold with
        a *configured* verifier is rejected.
        """
        if failures <= self._soft_threshold:
            return CaptchaResult(allowed=True, reason="below_threshold")
        if self._captcha is None:
            return CaptchaResult(allowed=True, reason="disabled")
        return await self._captcha.verify(token, remote_ip=remote_ip)


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------

_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Return the process-wide :class:`RateLimiter` (built on first use)."""
    global _limiter
    if _limiter is None:
        from app.auth.runtime import get_captcha_verifier, get_kvstore

        _limiter = RateLimiter(get_kvstore(), captcha=get_captcha_verifier())
    return _limiter


def reset_rate_limiter() -> None:
    """Drop the cached instance (test helper)."""
    global _limiter
    _limiter = None
