"""Unit tests for CSRF derivation, pre-session tokens, and ``next`` (Task 2.3).

Covers per-session double-submit derivation/verification, the signed pre-session
token (login-CSRF defense) with dual-key rotation, and the open-redirect guard.

Requirements: 12.1, 12.2, 11.4
"""

from __future__ import annotations

import pytest

from app.auth.csrf import (
    derive_csrf_token,
    issue_presession_token,
    presession_double_submit_ok,
    validate_next_path,
    verify_csrf_token,
    verify_presession_token,
)

pytestmark = pytest.mark.unit


class TestPerSessionCsrf:
    def test_derive_is_deterministic(self):
        a = derive_csrf_token("session-1", "secret-abc")
        b = derive_csrf_token("session-1", "secret-abc")
        assert a == b

    def test_derive_depends_on_session_and_secret(self):
        base = derive_csrf_token("session-1", "secret-abc")
        assert derive_csrf_token("session-2", "secret-abc") != base
        assert derive_csrf_token("session-1", "secret-xyz") != base

    def test_verify_accepts_matching_token(self):
        token = derive_csrf_token("session-1", "secret-abc")
        assert verify_csrf_token(token, "session-1", "secret-abc") is True

    def test_verify_rejects_wrong_token(self):
        assert verify_csrf_token("deadbeef", "session-1", "secret-abc") is False

    def test_verify_rejects_none_and_empty(self):
        assert verify_csrf_token(None, "session-1", "secret-abc") is False
        assert verify_csrf_token("", "session-1", "secret-abc") is False

    def test_verify_rejects_token_from_other_session(self):
        other = derive_csrf_token("session-2", "secret-abc")
        assert verify_csrf_token(other, "session-1", "secret-abc") is False


class TestPreSessionToken:
    def test_issue_and_verify_roundtrip(self):
        token = issue_presession_token("session-secret-value")
        assert verify_presession_token(token, "session-secret-value") is True

    def test_tokens_are_unique(self):
        secret = "session-secret-value"
        assert issue_presession_token(secret) != issue_presession_token(secret)

    def test_verify_rejects_wrong_secret(self):
        token = issue_presession_token("secret-one-value")
        assert verify_presession_token(token, "secret-two-value") is False

    def test_verify_rejects_tampered_token(self):
        token = issue_presession_token("session-secret-value")
        nonce, _, sig = token.partition(".")
        tampered = f"{nonce}.{'0' * len(sig)}"
        assert verify_presession_token(tampered, "session-secret-value") is False

    def test_verify_rejects_malformed(self):
        assert verify_presession_token("no-separator", "s") is False
        assert verify_presession_token(None, "s") is False
        assert verify_presession_token("", "s") is False

    def test_dual_key_rotation_accepts_previous_secret(self):
        old_token = issue_presession_token("old-secret-value")
        # After rotation, the old token still validates via secret_prev.
        assert (
            verify_presession_token(
                old_token, "new-secret-value", secret_prev="old-secret-value"
            )
            is True
        )

    def test_double_submit_requires_cookie_header_match(self):
        secret = "session-secret-value"
        token = issue_presession_token(secret)
        assert presession_double_submit_ok(token, token, secret) is True
        other = issue_presession_token(secret)
        assert presession_double_submit_ok(token, other, secret) is False

    def test_double_submit_rejects_missing(self):
        secret = "session-secret-value"
        token = issue_presession_token(secret)
        assert presession_double_submit_ok(None, token, secret) is False
        assert presession_double_submit_ok(token, None, secret) is False


class TestNextValidation:
    @pytest.mark.parametrize(
        "path",
        ["/", "/home", "/app/resumes", "/a/b/c?x=1", "/settings#section"],
    )
    def test_accepts_same_origin_paths(self, path):
        assert validate_next_path(path) == path

    @pytest.mark.parametrize(
        "path",
        [
            "//evil.com",
            "https://evil.com",
            "http://evil.com",
            "ftp://evil.com",
            "\\evil.com",
            "/\\evil.com",
            "javascript:alert(1)",
            "relative/path",
            "",
            None,
            "/path\nwith-newline",
            "/path\twith-tab",
        ],
    )
    def test_rejects_unsafe(self, path):
        assert validate_next_path(path) is None
