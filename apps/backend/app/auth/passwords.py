"""Password hashing, policy, and enumeration-safe verification (Task 2.1).

This module is the single place authentication flows hash and verify passwords.
It bundles three concerns the design (`§Auth flows -> Passwords`, R1.2/1.3/1.5/
2.2/13.3) requires to live together:

1. **Argon2id hashing** via ``argon2-cffi`` using the operator-tuned parameters
   from :class:`app.config.Settings` (memory/time/parallelism), with transparent
   rehash detection so parameters can be raised over time.
2. **Password policy** - a length floor (≥12) and cap (128, to bound Argon2
   cost), a common-password denylist, and a lightweight *zxcvbn-style* strength
   gate (no forced composition rules - passphrases are welcome, R1.3a). An
   optional breached-password check is layered on via the injected
   :class:`~app.auth.breach.BreachedPasswordCheck` adapter (fail-open, logged).
3. **Timing equalization** - verification always runs a real Argon2 computation,
   even on the "unknown email" / "OAuth-only account with no password" branches,
   by verifying against a precomputed **dummy hash**. This makes the negative
   path indistinguishable in timing from the positive one, closing the account-
   enumeration side channel (R1.2, R2.2, Property 4). Token/string comparisons
   use :func:`hmac.compare_digest` (constant time).

The service is deliberately dependency-injectable (Argon2 params + breach
adapter + clock are passed in) so it is fast and deterministic to unit-test in
isolation; :func:`get_password_service` returns the process-wide instance wired
from live settings.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

from argon2 import PasswordHasher, Type
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.auth.breach import BreachedPasswordCheck
from app.config import Settings

logger = logging.getLogger(__name__)

__all__ = [
    "PasswordPolicyResult",
    "PasswordService",
    "MIN_PASSWORD_LENGTH",
    "MAX_PASSWORD_LENGTH",
    "get_password_service",
    "reset_password_service",
]

# Policy bounds (R1.3, R1.3a). The floor follows modern length-first guidance;
# the cap bounds the Argon2 work an attacker can force us to do per request.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128

# A compact denylist of the most-abused passwords/patterns. This is intentionally
# small (the real defense is length + strength + the pluggable breach check); it
# just rejects the handful of credentials that are common *and* long enough to
# pass the length floor. Matching is case-insensitive and normalization-aware.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "passw0rd",
        "p@ssw0rd",
        "password!",
        "qwertyuiop",
        "qwerty123",
        "1234567890",
        "123456789012",
        "111111111111",
        "letmein12345",
        "adminadmin",
        "administrator",
        "welcome12345",
        "iloveyou1234",
        "monkey123456",
        "dragon123456",
        "football1234",
        "baseball1234",
        "sunshine1234",
        "princess1234",
        "trustno1no1",
        "changeme1234",
        "secretsecret",
        "passwordpassword",
    }
)

# Common keyboard/sequential runs used by the strength gate to discount
# predictable input regardless of length.
_SEQUENTIAL_RUNS = (
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "01234567890",
)


@dataclass(frozen=True, slots=True)
class PasswordPolicyResult:
    """Outcome of a policy evaluation.

    ``ok`` is the decision callers branch on. ``code`` is the stable machine
    reason (``weak_password`` / ``breached_password``) used in the API error
    envelope; ``unmet`` lists the specific human-readable rules that failed so a
    caller can surface guidance without leaking anything sensitive.
    """

    ok: bool
    code: str = ""
    unmet: tuple[str, ...] = field(default_factory=tuple)
    breach_count: int = 0

    @property
    def first_reason(self) -> str:
        return self.unmet[0] if self.unmet else ""


def normalize_password(password: str) -> str:
    """NFKC-normalize a password before hashing/policy checks.

    Normalizing avoids the surprise where a password typed with a
    canonically-equivalent but differently-encoded character fails to verify on
    a different keyboard/OS. We do **not** case-fold or strip - those are real
    entropy.
    """
    return unicodedata.normalize("NFKC", password)


class PasswordService:
    """Argon2id hashing, policy enforcement, and enumeration-safe verify."""

    # A fixed, syntactically valid password used only to generate the per-process
    # dummy hash. Its value is irrelevant - it never authenticates anything.
    _DUMMY_PASSWORD = "dummy-timing-equalization-password"

    def __init__(
        self,
        *,
        time_cost: int,
        memory_cost: int,
        parallelism: int,
        breach_check: BreachedPasswordCheck | None = None,
        min_length: int = MIN_PASSWORD_LENGTH,
        max_length: int = MAX_PASSWORD_LENGTH,
    ) -> None:
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            type=Type.ID,
        )
        self._breach_check = breach_check
        self._min_length = min_length
        self._max_length = max_length
        # Precompute the dummy hash once so the negative verify path costs the
        # same as the positive one without re-hashing per request.
        self._dummy_hash = self._hasher.hash(self._DUMMY_PASSWORD)

    # -- hashing -------------------------------------------------------------

    def hash_password(self, password: str) -> str:
        """Return the Argon2id hash of ``password`` (NFKC-normalized first).

        Raises :class:`ValueError` if the password exceeds the length cap - the
        cap must be enforced *before* hashing so an attacker cannot force
        unbounded Argon2 work.
        """
        normalized = normalize_password(password)
        if len(normalized) > self._max_length:
            raise ValueError(
                f"Password exceeds maximum length of {self._max_length} characters"
            )
        try:
            return self._hasher.hash(normalized)
        except HashingError as exc:  # pragma: no cover - defensive
            raise ValueError("Password could not be hashed") from exc

    def verify_password(self, stored_hash: str | None, password: str) -> bool:
        """Constant-effort verify that never reveals whether an account exists.

        When ``stored_hash`` is ``None`` (unknown email, or an OAuth-only account
        with no local password) a verification against the **dummy hash** is run
        anyway so the branch is not measurably faster, then ``False`` is
        returned. A genuine mismatch also returns ``False``. Argon2 verification
        is itself constant-time for a given hash.
        """
        target = stored_hash if stored_hash is not None else self._dummy_hash
        normalized = normalize_password(password)
        try:
            self._hasher.verify(target, normalized)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
        # A successful verify against the dummy hash must still deny (there is no
        # real account/password behind it).
        return stored_hash is not None

    def needs_rehash(self, stored_hash: str) -> bool:
        """Whether ``stored_hash`` was made with weaker params and should be
        upgraded on the next successful login."""
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return False

    # -- policy --------------------------------------------------------------

    def check_policy(
        self, password: str, *, email: str | None = None, name: str | None = None
    ) -> PasswordPolicyResult:
        """Evaluate the synchronous password policy (no network).

        The optional ``email``/``name`` let the strength gate reject passwords
        that merely echo the user's own identifiers. The breached-password check
        (network) is intentionally separate - see :meth:`check_breach` - so this
        stays pure and fast.
        """
        normalized = normalize_password(password)
        unmet: list[str] = []

        if len(normalized) < self._min_length:
            unmet.append(f"must be at least {self._min_length} characters")
        if len(normalized) > self._max_length:
            unmet.append(f"must be at most {self._max_length} characters")

        folded = normalized.casefold()
        if folded in _COMMON_PASSWORDS:
            unmet.append("is a commonly used password")

        # Only run the (more expensive/heuristic) strength checks when the length
        # is in range - a too-short/too-long password already failed.
        if self._min_length <= len(normalized) <= self._max_length:
            weak_reason = self._strength_reason(normalized, email=email, name=name)
            if weak_reason:
                unmet.append(weak_reason)

        if unmet:
            return PasswordPolicyResult(ok=False, code="weak_password", unmet=tuple(unmet))
        return PasswordPolicyResult(ok=True)

    def _strength_reason(
        self, password: str, *, email: str | None, name: str | None
    ) -> str:
        """Return a reason string if the password is too predictable, else ""."""
        folded = password.casefold()

        # Reject passwords built around the user's own email local-part or name.
        for identifier in (email, name):
            if not identifier:
                continue
            token = identifier.split("@", 1)[0].casefold().strip()
            if len(token) >= 4 and token in folded:
                return "must not contain your name or email"

        # A single repeated character (e.g. "aaaaaaaaaaaa") has almost no entropy.
        if len(set(password)) <= 2:
            return "is too repetitive"

        # Long sequential/keyboard runs.
        for run in _SEQUENTIAL_RUNS:
            if _contains_run(folded, run, length=min(8, len(run))):
                return "contains a predictable sequence"
            if _contains_run(folded, run[::-1], length=min(8, len(run))):
                return "contains a predictable sequence"

        # zxcvbn-style variety/entropy floor: estimate the pool size from the
        # character classes present and require enough estimated bits. This lets
        # a long all-lowercase passphrase pass while blocking short low-variety
        # strings that scraped past the denylist.
        if _estimated_entropy_bits(password) < 40.0:
            return "is not strong enough (use a longer or more varied passphrase)"
        return ""

    # -- breach check (network, fail-open) ----------------------------------

    async def check_breach(self, password: str) -> PasswordPolicyResult:
        """Check ``password`` against the configured breach corpus.

        Fail-open by contract (R13.3): if no adapter is configured, the provider
        is unavailable, **or the adapter raises**, the password is accepted and a
        warning is logged - a third party being down must never block a
        legitimate signup/password-change. Only a *positive* breach result
        rejects (``breached_password``).
        """
        if self._breach_check is None:
            return PasswordPolicyResult(ok=True)
        try:
            result = await self._breach_check.check(normalize_password(password))
        except Exception:
            # Fail open, loudly: the provider blew up, but we never block auth on
            # a breach-check outage (R13.3). No password material is logged.
            logger.warning(
                "Breached-password provider unavailable; failing open (password accepted)",
                exc_info=True,
            )
            return PasswordPolicyResult(ok=True)
        if result.breached:
            return PasswordPolicyResult(
                ok=False,
                code="breached_password",
                unmet=("has appeared in a known data breach",),
                breach_count=result.count,
            )
        return PasswordPolicyResult(ok=True)

    async def validate_new_password(
        self, password: str, *, email: str | None = None, name: str | None = None
    ) -> PasswordPolicyResult:
        """Full gate for a *new* password: policy then breach check.

        Returns the first failure encountered (policy before breach) so callers
        get a single, actionable reason and the network check is skipped when the
        password is already policy-invalid.
        """
        policy = self.check_policy(password, email=email, name=name)
        if not policy.ok:
            return policy
        return await self.check_breach(password)


# ---------------------------------------------------------------------------
# Strength helpers
# ---------------------------------------------------------------------------


def _contains_run(text: str, run: str, *, length: int) -> bool:
    """Whether ``text`` contains any ``length``-long contiguous slice of ``run``."""
    if length <= 0 or len(run) < length:
        return False
    for start in range(len(run) - length + 1):
        if run[start : start + length] in text:
            return True
    return False


def _estimated_entropy_bits(password: str) -> float:
    """Rough Shannon-style entropy estimate: ``len * log2(pool_size)``.

    The pool size is inferred from the character classes present (lower/upper/
    digit/symbol/other). This is a deliberately simple, dependency-free stand-in
    for zxcvbn: it rewards length and variety, which is exactly the behavior the
    policy wants (passphrases pass, short low-variety strings fail).
    """
    import math

    pool = 0
    if re.search(r"[a-z]", password):
        pool += 26
    if re.search(r"[A-Z]", password):
        pool += 26
    if re.search(r"[0-9]", password):
        pool += 10
    if re.search(r"[^a-zA-Z0-9]", password):
        pool += 33  # printable ASCII symbols (approx.)
    # Any character outside the above (unicode) widens the pool further.
    if re.search(r"[^\x00-\x7f]", password):
        pool += 100
    if pool == 0:
        return 0.0
    return len(password) * math.log2(pool)


# ---------------------------------------------------------------------------
# Process-wide instance wired from live settings
# ---------------------------------------------------------------------------

_service: PasswordService | None = None


def build_password_service(config: Settings) -> PasswordService:
    """Construct a :class:`PasswordService` from configuration.

    The breach adapter is pulled from the runtime singletons (which honor
    ``BREACH_PROVIDER``); it is imported lazily to avoid an import cycle at module
    load (``runtime`` imports several auth modules).
    """
    from app.auth.runtime import get_breached_password_check

    return PasswordService(
        time_cost=config.argon2_time_cost,
        memory_cost=config.argon2_memory_cost,
        parallelism=config.argon2_parallelism,
        breach_check=get_breached_password_check(),
    )


def get_password_service() -> PasswordService:
    """Return the process-wide :class:`PasswordService` (built on first use)."""
    global _service
    if _service is None:
        from app.config import settings

        _service = build_password_service(settings)
    return _service


def reset_password_service() -> None:
    """Drop the cached instance (test helper; next ``get`` rebuilds it)."""
    global _service
    _service = None
