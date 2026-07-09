"""Unit tests for rate limiting, lockout, backoff, and CAPTCHA gating (Task 2.4).

Covers fixed-window limiting, exponential backoff + account lockout, the CAPTCHA
gate, and fail-closed behavior on a KVStore outage.

Requirements: 13.1, 13.2, 13.5
"""

from __future__ import annotations

import pytest

from app.auth.captcha import AllowAllCaptchaVerifier, CaptchaResult, CaptchaVerifier
from app.auth.kvstore import LocalKVStore
from app.auth.ratelimit import RateLimiter, RateLimitRule

pytestmark = pytest.mark.unit


class _BrokenKV(LocalKVStore):
    """A KVStore whose operations always raise (simulates an outage)."""

    async def incr(self, key, *, amount=1, ttl_seconds=None):
        raise RuntimeError("kvstore down")

    async def get(self, key):
        raise RuntimeError("kvstore down")

    async def set(self, key, value, *, ttl_seconds=None):
        raise RuntimeError("kvstore down")


class _RejectingCaptcha(CaptchaVerifier):
    async def verify(self, token, *, remote_ip=None):
        if token == "good":
            return CaptchaResult(allowed=True)
        return CaptchaResult(allowed=False, reason="invalid_token")


@pytest.fixture
def kv():
    return LocalKVStore()


class TestFixedWindow:
    async def test_allows_within_limit(self, kv):
        limiter = RateLimiter(kv)
        rule = RateLimitRule(limit=3, window_seconds=60)
        for _ in range(3):
            result = await limiter.check("login", "1.2.3.4", rule)
            assert result.allowed is True

    async def test_blocks_over_limit_with_retry_after(self, kv):
        limiter = RateLimiter(kv)
        rule = RateLimitRule(limit=2, window_seconds=60)
        await limiter.check("login", "1.2.3.4", rule)
        await limiter.check("login", "1.2.3.4", rule)
        result = await limiter.check("login", "1.2.3.4", rule)
        assert result.allowed is False
        assert result.retry_after == 60

    async def test_separate_identifiers_have_separate_windows(self, kv):
        limiter = RateLimiter(kv)
        rule = RateLimitRule(limit=1, window_seconds=60)
        assert (await limiter.check("login", "1.1.1.1", rule)).allowed is True
        assert (await limiter.check("login", "2.2.2.2", rule)).allowed is True

    async def test_default_rule_used_for_known_class(self, kv):
        limiter = RateLimiter(kv)
        # signup default is 5/60; sixth should block.
        results = [await limiter.check("signup", "ip") for _ in range(6)]
        assert results[-1].allowed is False


class TestFailClosed:
    async def test_auth_check_fails_closed_on_outage(self):
        limiter = RateLimiter(_BrokenKV())
        result = await limiter.check("login", "ip")
        assert result.allowed is False
        assert result.fail_closed is True
        assert result.retry_after > 0

    async def test_opt_out_fails_open(self):
        limiter = RateLimiter(_BrokenKV())
        result = await limiter.check("read", "ip", fail_closed=False)
        assert result.allowed is True
        assert result.fail_closed is True

    async def test_is_locked_out_fails_closed_on_outage(self):
        limiter = RateLimiter(_BrokenKV())
        state = await limiter.is_locked_out("acct")
        assert state.locked is True


class TestLockoutAndBackoff:
    async def test_backoff_grows_after_soft_threshold(self, kv):
        limiter = RateLimiter(
            kv, soft_failure_threshold=3, max_failures=10, backoff_base_seconds=1
        )
        backoffs = []
        for _ in range(6):
            state = await limiter.register_failure("acct")
            backoffs.append(state.backoff_seconds)
        # No backoff up to the soft threshold, then exponential growth.
        assert backoffs[0] == 0
        assert backoffs[2] == 0  # 3rd failure still at threshold
        assert backoffs[3] == 1  # 4th
        assert backoffs[4] == 2  # 5th
        assert backoffs[5] == 4  # 6th

    async def test_locks_after_max_failures(self, kv):
        limiter = RateLimiter(kv, max_failures=3)
        for _ in range(2):
            state = await limiter.register_failure("acct")
            assert state.locked is False
        state = await limiter.register_failure("acct")
        assert state.locked is True
        assert state.retry_after > 0

    async def test_lockout_visible_via_is_locked_out(self, kv):
        limiter = RateLimiter(kv, max_failures=2)
        await limiter.register_failure("acct")
        await limiter.register_failure("acct")
        state = await limiter.is_locked_out("acct")
        assert state.locked is True

    async def test_clear_failures_resets(self, kv):
        limiter = RateLimiter(kv, max_failures=2)
        await limiter.register_failure("acct")
        await limiter.register_failure("acct")
        await limiter.clear_failures("acct")
        state = await limiter.is_locked_out("acct")
        assert state.locked is False
        assert state.failures == 0

    async def test_captcha_required_flag_after_soft_threshold(self, kv):
        limiter = RateLimiter(kv, soft_failure_threshold=2, max_failures=10)
        s1 = await limiter.register_failure("acct")
        s2 = await limiter.register_failure("acct")
        s3 = await limiter.register_failure("acct")
        assert s1.captcha_required is False
        assert s2.captcha_required is False
        assert s3.captcha_required is True


class TestCaptchaGate:
    async def test_below_threshold_no_challenge(self, kv):
        limiter = RateLimiter(kv, captcha=_RejectingCaptcha(), soft_failure_threshold=3)
        result = await limiter.captcha_gate(1, token=None)
        assert result.allowed is True
        assert result.reason == "below_threshold"

    async def test_above_threshold_requires_valid_token(self, kv):
        limiter = RateLimiter(kv, captcha=_RejectingCaptcha(), soft_failure_threshold=3)
        assert (await limiter.captcha_gate(5, token="bad")).allowed is False
        assert (await limiter.captcha_gate(5, token="good")).allowed is True

    async def test_no_verifier_fails_open(self, kv):
        limiter = RateLimiter(kv, captcha=None, soft_failure_threshold=3)
        assert (await limiter.captcha_gate(5, token=None)).allowed is True

    async def test_allow_all_verifier_fails_open(self, kv):
        limiter = RateLimiter(
            kv, captcha=AllowAllCaptchaVerifier(), soft_failure_threshold=3
        )
        assert (await limiter.captcha_gate(5, token=None)).allowed is True
