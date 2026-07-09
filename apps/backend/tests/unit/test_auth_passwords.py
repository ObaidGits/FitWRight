"""Unit tests for password hashing, policy, and enumeration-safe verify (Task 2.1).

Covers Argon2id hash/verify + rehash detection, the password policy (length
floor/cap, denylist, zxcvbn-style strength gate), the breach hook (fail-open),
and — the security-critical piece — the dummy-hash timing equalization that keeps
the unknown-account branch from being measurably faster (R1.2, R2.2, Property 4).

Requirements: 1.2, 1.3, 1.5, 2.2, 13.3
"""

from __future__ import annotations

import time

import pytest

from app.auth.breach import BreachedPasswordCheck, BreachResult
from app.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    PasswordService,
)

pytestmark = pytest.mark.unit


# Fast Argon2 params for tests — real security params come from settings in prod.
def _service(breach_check: BreachedPasswordCheck | None = None) -> PasswordService:
    return PasswordService(
        time_cost=1,
        memory_cost=8,
        parallelism=1,
        breach_check=breach_check,
    )


class _AlwaysBreached(BreachedPasswordCheck):
    async def check(self, password: str) -> BreachResult:
        return BreachResult(breached=True, count=42, checked=True)


class _NeverBreached(BreachedPasswordCheck):
    async def check(self, password: str) -> BreachResult:
        return BreachResult(breached=False, count=0, checked=True)


class _BrokenProvider(BreachedPasswordCheck):
    async def check(self, password: str) -> BreachResult:
        # A well-behaved adapter fails open; this simulates one that already did.
        return BreachResult(breached=False, checked=False)


class _VerifySpy:
    """Wraps a real ``PasswordHasher``, recording which hash each verify hit.

    The C-extension ``PasswordHasher.verify`` is read-only and cannot be
    monkeypatched in place, so we swap the whole hasher for this proxy.
    """

    def __init__(self, real):
        self._real = real
        self.seen: list[str] = []

    def hash(self, password):
        return self._real.hash(password)

    def verify(self, hash_str, password):
        self.seen.append(hash_str)
        return self._real.verify(hash_str, password)

    def check_needs_rehash(self, hash_str):
        return self._real.check_needs_rehash(hash_str)


# ---------------------------------------------------------------------------
# Hashing / verification
# ---------------------------------------------------------------------------


class TestHashVerify:
    def test_hash_is_argon2id_and_verifies(self):
        svc = _service()
        password = "correct horse battery staple"
        hashed = svc.hash_password(password)
        assert hashed.startswith("$argon2id$")
        assert svc.verify_password(hashed, password) is True

    def test_wrong_password_fails(self):
        svc = _service()
        hashed = svc.hash_password("correct horse battery staple")
        assert svc.verify_password(hashed, "wrong horse battery staple") is False

    def test_hash_never_stores_plaintext(self):
        svc = _service()
        password = "correct horse battery staple"
        hashed = svc.hash_password(password)
        assert password not in hashed

    def test_two_hashes_differ_by_salt(self):
        svc = _service()
        a = svc.hash_password("correct horse battery staple")
        b = svc.hash_password("correct horse battery staple")
        assert a != b  # random salt

    def test_length_cap_enforced_before_hashing(self):
        svc = _service()
        with pytest.raises(ValueError, match="maximum length"):
            svc.hash_password("a" * (MAX_PASSWORD_LENGTH + 1))

    def test_nfkc_normalization_makes_equivalent_forms_verify(self):
        svc = _service()
        # U+00C5 (Å) vs "A" + U+030A (combining ring) normalize to the same NFKC.
        hashed = svc.hash_password("passphrase-\u00c5-value-here")
        assert svc.verify_password(hashed, "passphrase-A\u030a-value-here") is True

    def test_needs_rehash_true_for_weaker_params(self):
        weak = PasswordService(time_cost=1, memory_cost=8, parallelism=1)
        strong = PasswordService(time_cost=3, memory_cost=64, parallelism=1)
        hashed = weak.hash_password("correct horse battery staple")
        assert strong.needs_rehash(hashed) is True
        assert weak.needs_rehash(hashed) is False

    def test_needs_rehash_false_for_garbage(self):
        svc = _service()
        assert svc.needs_rehash("not-a-hash") is False


# ---------------------------------------------------------------------------
# Dummy-hash timing equalization (Property 4)
# ---------------------------------------------------------------------------


class TestTimingEqualization:
    def test_none_hash_still_runs_argon2_verify(self):
        """The unknown-account branch MUST perform a real Argon2 verify."""
        svc = _service()
        spy = _VerifySpy(svc._hasher)
        svc._hasher = spy
        assert svc.verify_password(None, "anything at all here") is False
        # Verified against the precomputed dummy hash, not skipped.
        assert spy.seen == [svc._dummy_hash]

    def test_real_account_verifies_against_stored_hash(self):
        svc = _service()
        hashed = svc.hash_password("correct horse battery staple")
        spy = _VerifySpy(svc._hasher)
        svc._hasher = spy
        assert svc.verify_password(hashed, "correct horse battery staple") is True
        assert spy.seen == [hashed]

    def test_dummy_verify_success_still_denies(self):
        """Even if the supplied password equalled the dummy password, deny."""
        svc = _service()
        assert svc.verify_password(None, svc._DUMMY_PASSWORD) is False

    def test_none_and_real_branches_have_comparable_timing(self):
        """Statistical guard: the None branch is not a fast no-op.

        Both branches run one Argon2 verify, so their timings should be within
        the same order of magnitude. Tolerance is deliberately generous to avoid
        CI flakiness — the assertion only fails if the None branch clearly skips
        the hash (near-zero time).
        """
        svc = _service()
        hashed = svc.hash_password("correct horse battery staple")

        def median_time(fn, n=15):
            samples = []
            for _ in range(n):
                start = time.perf_counter()
                fn()
                samples.append(time.perf_counter() - start)
            samples.sort()
            return samples[len(samples) // 2]

        real_median = median_time(lambda: svc.verify_password(hashed, "nope nope nope nope"))
        none_median = median_time(lambda: svc.verify_password(None, "nope nope nope nope"))
        # The None branch must take at least ~30% of the real branch's time.
        assert none_median >= real_median * 0.3


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_accepts_strong_passphrase(self):
        svc = _service()
        result = svc.check_policy("correct horse battery staple garden")
        assert result.ok is True

    def test_rejects_too_short(self):
        svc = _service()
        result = svc.check_policy("short1")
        assert result.ok is False
        assert result.code == "weak_password"
        assert any("at least" in reason for reason in result.unmet)

    def test_rejects_over_cap(self):
        svc = _service()
        result = svc.check_policy("a" * (MAX_PASSWORD_LENGTH + 5))
        assert result.ok is False
        assert any("at most" in reason for reason in result.unmet)

    def test_rejects_common_password(self):
        svc = _service()
        result = svc.check_policy("password1234")
        assert result.ok is False
        assert any("commonly used" in reason for reason in result.unmet)

    def test_rejects_repetitive(self):
        svc = _service()
        result = svc.check_policy("aaaaaaaaaaaaaa")
        assert result.ok is False

    def test_rejects_sequential(self):
        svc = _service()
        result = svc.check_policy("abcdefghijklmno")
        assert result.ok is False
        assert any("predictable" in reason for reason in result.unmet)

    def test_rejects_password_containing_email_localpart(self):
        svc = _service()
        result = svc.check_policy(
            "janedoe-is-my-login", email="janedoe@example.com"
        )
        assert result.ok is False
        assert any("name or email" in reason for reason in result.unmet)

    def test_rejects_password_containing_name(self):
        svc = _service()
        result = svc.check_policy("jonathan-smith-secure", name="jonathan")
        assert result.ok is False

    def test_min_length_boundary(self):
        svc = _service()
        # Exactly MIN_PASSWORD_LENGTH, varied enough to pass strength.
        pw = "Tr0ub4dour&3x"  # 13 chars
        assert len(pw) >= MIN_PASSWORD_LENGTH
        assert svc.check_policy(pw).ok is True


# ---------------------------------------------------------------------------
# Breach hook (fail-open)
# ---------------------------------------------------------------------------


class TestBreachHook:
    async def test_breached_password_rejected(self):
        svc = _service(breach_check=_AlwaysBreached())
        result = await svc.check_breach("correct horse battery staple")
        assert result.ok is False
        assert result.code == "breached_password"
        assert result.breach_count == 42

    async def test_clean_password_allowed(self):
        svc = _service(breach_check=_NeverBreached())
        assert (await svc.check_breach("correct horse battery staple")).ok is True

    async def test_no_adapter_fails_open(self):
        svc = _service(breach_check=None)
        assert (await svc.check_breach("password1234")).ok is True

    async def test_provider_failure_fails_open(self):
        svc = _service(breach_check=_BrokenProvider())
        assert (await svc.check_breach("correct horse battery staple")).ok is True

    async def test_validate_new_password_runs_policy_before_breach(self):
        # Weak password should be rejected on policy without consulting breach.
        svc = _service(breach_check=_AlwaysBreached())
        result = await svc.validate_new_password("short1")
        assert result.ok is False
        assert result.code == "weak_password"

    async def test_validate_new_password_passes_both_gates(self):
        svc = _service(breach_check=_NeverBreached())
        result = await svc.validate_new_password("correct horse battery staple")
        assert result.ok is True
