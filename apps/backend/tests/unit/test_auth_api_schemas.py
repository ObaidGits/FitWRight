"""Unit tests for the SafeUser projection + ADR-7 error envelope (Task 4).

Requirements: 7.5
"""

from __future__ import annotations

import pytest

from app.errors import ApiError, error_envelope
from app.schemas.auth import (
    SAFE_USER_FIELDS,
    LoginRequest,
    SafeUser,
    SignupRequest,
    assert_safe_user,
)

pytestmark = pytest.mark.unit


class TestSafeUser:
    def test_serializes_only_safe_fields(self):
        user = SafeUser.build(
            id="u1",
            name="Alice",
            email="alice@example.com",
            role="user",
            status="active",
            email_verified=True,
            aal="aal1",
            avatar_url=None,
        )
        payload = user.model_dump()
        assert set(payload) <= SAFE_USER_FIELDS
        assert payload["emailVerified"] is True
        # The safeguard passes for a clean payload.
        assert assert_safe_user(payload) == payload

    def test_forbids_extra_fields(self):
        # A field outside the whitelist cannot even be constructed onto SafeUser.
        with pytest.raises(Exception):
            SafeUser(
                id="u1",
                name="A",
                email="a@b.c",
                role="user",
                status="active",
                emailVerified=True,
                aal="aal1",
                password_hash="secret",  # type: ignore[call-arg]
            )

    def test_assert_safe_user_rejects_leaked_field(self):
        leaky = {"id": "u1", "email": "a@b.c", "password_hash": "secret"}
        with pytest.raises(ValueError):
            assert_safe_user(leaky)


class TestRequestValidation:
    @pytest.mark.parametrize("email", ["alice@example.com", "a.b+c@sub.example.org"])
    def test_accepts_reasonable_emails(self, email):
        assert SignupRequest(email=email, password="x" * 12, name="A").email == email

    @pytest.mark.parametrize("email", ["nope", "no@domain", "two@@at.com", "sp ace@x.com"])
    def test_rejects_malformed_emails(self, email):
        with pytest.raises(Exception):
            LoginRequest(email=email, password="x")


class TestErrorEnvelope:
    def test_envelope_omits_details_when_absent(self):
        assert error_envelope("bad", "Bad thing") == {
            "error": {"code": "bad", "message": "Bad thing"}
        }

    def test_envelope_includes_details(self):
        env = error_envelope("weak_password", "nope", details={"unmet": ["too short"]})
        assert env["error"]["details"] == {"unmet": ["too short"]}

    def test_api_error_defaults_message_from_code(self):
        err = ApiError(429, "rate_limited", headers={"Retry-After": "5"})
        assert err.status_code == 429
        assert err.code == "rate_limited"
        assert err.message == "rate limited"
        assert err.headers == {"Retry-After": "5"}
