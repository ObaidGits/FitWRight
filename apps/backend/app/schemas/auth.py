"""Pydantic v2 request/response models for the auth + user surface (Task 4).

``SafeUser`` is the **only** shape a user is ever serialized into on the wire
(R7.5): it carries ``id/name/email/role/status/emailVerified/aal`` (+ optional
``avatarUrl``) and *nothing else* - never ``password_hash``, tokens, or internal
flags. ``model_config = extra="forbid"`` plus :func:`assert_safe_user` are the
serialization safeguard: a field that is not explicitly whitelisted here can not
be constructed into (or leak out of) a ``SafeUser``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "SAFE_USER_FIELDS",
    "SafeUser",
    "assert_safe_user",
    "SignupRequest",
    "LoginRequest",
    "UpdateProfileRequest",
    "SessionSummary",
    "SessionListResponse",
    "SignupPendingResponse",
    "MessageResponse",
    "VerificationRequestRequest",
    "VerificationConfirmRequest",
    "ForgotPasswordRequest",
    "ResetPasswordRequest",
    "UniformAckResponse",
    "StepUpRequest",
    "ChangePasswordRequest",
    "ChangeEmailRequest",
    "EmailChangeConfirmRequest",
]

# The exhaustive whitelist of fields a user may be serialized with. Any other
# attribute (password_hash, mfa_enrolled, created_at, ...) is intentionally absent.
SAFE_USER_FIELDS: frozenset[str] = frozenset(
    {"id", "name", "email", "role", "status", "emailVerified", "aal", "avatarUrl"}
)


class SafeUser(BaseModel):
    """The single, leak-proof public projection of a user (R7.5)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    email: str
    role: str
    status: str
    emailVerified: bool
    aal: str
    avatarUrl: str | None = None

    @classmethod
    def build(
        cls,
        *,
        id: str,
        name: str,
        email: str,
        role: str,
        status: str,
        email_verified: bool,
        aal: str,
        avatar_url: str | None = None,
    ) -> "SafeUser":
        """Construct a ``SafeUser`` from explicit, whitelisted fields only.

        Callers pass individual values (never ``**row.__dict__``), so a new
        column on the ``users`` table can never accidentally ride along into the
        response.
        """
        return cls(
            id=id,
            name=name,
            email=email,
            role=role,
            status=status,
            emailVerified=email_verified,
            aal=aal,
            avatarUrl=avatar_url,
        )


def assert_safe_user(payload: dict) -> dict:
    """Defense-in-depth: verify a serialized user carries only safe fields.

    Raises :class:`ValueError` if ``payload`` contains any key outside
    :data:`SAFE_USER_FIELDS`. Used at the response boundary and in tests so a
    regression that widens ``SafeUser`` fails loudly rather than leaking.
    """
    extra = set(payload) - SAFE_USER_FIELDS
    if extra:
        raise ValueError(f"SafeUser payload leaked non-safe fields: {sorted(extra)}")
    return payload


def _validate_email_shape(value: str) -> str:
    """Minimal, dependency-free email sanity check (normalization happens later).

    Full RFC validation is neither necessary nor desirable here: the address is
    NFKC+lowercased+trimmed downstream, uniqueness is enforced by the DB, and
    deliverability is proven by the verification step. We only reject obviously
    malformed input (missing local/domain part, whitespace) so a typo fails fast.
    """
    candidate = value.strip()
    if not candidate or candidate.count("@") != 1:
        raise ValueError("must be a valid email address")
    local, _, domain = candidate.partition("@")
    if not local or not domain or "." not in domain or any(c.isspace() for c in candidate):
        raise ValueError("must be a valid email address")
    return candidate


class SignupRequest(BaseModel):
    """Create-account payload.

    ``captcha_token`` is the optional challenge response; it is only *required*
    (and verified) once signup attempts from an IP cross the soft threshold and a
    CAPTCHA provider is configured (R13.2). When no provider is wired the field
    is ignored (fail-open).
    """

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200)
    captcha_token: str | None = Field(default=None, max_length=4096)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _validate_email_shape(v)


class LoginRequest(BaseModel):
    """Login payload. ``remember_me`` selects the longer absolute session cap.

    ``captcha_token`` is the optional challenge response, required (and verified)
    only past the soft failure threshold when a CAPTCHA provider is configured
    (R13.2); ignored otherwise (fail-open).
    """

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1)
    remember_me: bool = False
    captcha_token: str | None = Field(default=None, max_length=4096)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _validate_email_shape(v)


class UpdateProfileRequest(BaseModel):
    """``PATCH /users/me`` - name only (role/status are ignored, R7.2).

    ``updated_at`` is the optimistic-concurrency token the client last saw; when
    provided and stale it yields a 409 (R Reliability §``PATCH /users/me``).
    """

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    updated_at: str | None = None


class SessionSummary(BaseModel):
    """One active session in the device list (never the raw token, R3.5)."""

    id: str
    deviceLabel: str | None = None
    ipHash: str | None = None
    createdAt: str
    lastSeenAt: str
    current: bool = False


class SessionListResponse(BaseModel):
    """Device-management list of the caller's active sessions."""

    sessions: list[SessionSummary]


class SignupPendingResponse(BaseModel):
    """Uniform signup response when email verification is required (R1.6, R1.2).

    Returned identically whether or not the email was already registered, so the
    endpoint discloses nothing about account existence (Property 4).
    """

    status: str = "pending_verification"


class MessageResponse(BaseModel):
    """Generic acknowledgement for state-changing auth actions."""

    message: str
    count: int | None = None


class VerificationRequestRequest(BaseModel):
    """``POST /auth/verify/request`` - (re)send an email-verification link.

    ``email`` is optional: an authenticated caller re-sends for their own
    account (email ignored); an anonymous caller supplies the address. Either
    way the response is uniform, so it never discloses whether an account exists
    (R5.5, Property 4).
    """

    email: str | None = Field(default=None, max_length=320)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str | None) -> str | None:
        return _validate_email_shape(v) if v is not None else None


class VerificationConfirmRequest(BaseModel):
    """``POST /auth/verify/confirm`` - redeem a verification token."""

    token: str = Field(min_length=1, max_length=512)


class ForgotPasswordRequest(BaseModel):
    """``POST /auth/password/forgot`` - request a password-reset link.

    The response is uniform regardless of whether the email is registered
    (R6.1/6.5, Property 4).
    """

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _validate_email_shape(v)


class ResetPasswordRequest(BaseModel):
    """``POST /auth/password/reset`` - set a new password with a reset token.

    For an OAuth-only account (no existing password) this *sets* a password,
    linking password auth (R6.3).
    """

    token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=1)


class UniformAckResponse(BaseModel):
    """A deliberately non-committal acknowledgement.

    Returned identically for both the "email exists" and "email unknown" paths of
    verify/request and password/forgot so the endpoint discloses nothing about
    account existence (R5.5, R6.5, Property 4).
    """

    status: str = "ok"


class StepUpRequest(BaseModel):
    """``POST /auth/step-up`` - re-verify the current password to open a sudo
    window for sensitive actions (R9.1). MFA is a future additive factor."""

    password: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    """``POST /auth/password/change`` - change the password from within a session.

    Requires a recent step-up (R9.1). The current password is re-verified
    (constant-time) and the new one is policy/breach-checked before the swap
    (R7.3).
    """

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


class ChangeEmailRequest(BaseModel):
    """``POST /users/me/email`` - begin a verify-before-switch email change.

    Requires a recent step-up (R9.1). A confirmation link is sent to the *new*
    address; the primary email is switched only after that link is confirmed
    (R7.4), so the account never moves to an unverified address.
    """

    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        return _validate_email_shape(v)


class EmailChangeConfirmRequest(BaseModel):
    """``POST /users/me/email/confirm`` - redeem an email-change token.

    The token was delivered to the new address, proving ownership; confirming it
    swaps the account's primary email to that address (R7.4).
    """

    token: str = Field(min_length=1, max_length=512)
