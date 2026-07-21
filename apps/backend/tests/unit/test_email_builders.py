"""Unit tests for the branded transactional-email builders (auth verification).

Covers the professional verification / reset / email-change emails: each must
carry BOTH a plain-text and an HTML body, with branding, a call-to-action
button (HTML), a plain-URL fallback, an expiration notice, a security note, and
a support contact - and the raw token must appear only inside the link (in the
text body so link extraction and HTML-stripping clients both work).
"""

from __future__ import annotations

import pytest

from app.auth.email import (
    build_email_change_email,
    build_password_reset_email,
    build_verification_email,
)
from app.auth.email import _humanize_duration  # noqa: PLC2701 - internal helper under test

pytestmark = pytest.mark.unit

_BASE = "https://app.fitwright.tech"
_TOKEN = "raw-token-abc123"


class TestVerificationEmail:
    def _msg(self, **kw):
        return build_verification_email(
            to="user@example.com", raw_token=_TOKEN, base_url=_BASE, **kw
        )

    def test_has_text_and_html_bodies(self):
        msg = self._msg()
        assert msg.text_body, "text body required"
        assert msg.html_body, "html body required"

    def test_link_carries_token_and_targets_verify(self):
        msg = self._msg()
        expected = f"{_BASE}/verify?token={_TOKEN}"
        # Present in BOTH bodies (button href + plain fallback in text).
        assert expected in msg.text_body
        assert expected in msg.html_body

    def test_html_has_branding_button_and_support(self):
        msg = self._msg()
        html = msg.html_body
        assert "FitWright" in html  # branding
        assert "Verify email address" in html  # CTA button label
        assert "support@fitwright.tech" in html  # support contact
        assert "expire" in html.lower()  # expiration notice
        assert "copy and paste" in html.lower()  # plain-URL fallback affordance

    def test_text_has_expiry_security_and_support(self):
        msg = self._msg(expires_seconds=60 * 60 * 24)
        text = msg.text_body.lower()
        assert "expire in 24 hours" in text
        assert "didn't create" in text  # security note
        assert "support@fitwright.tech" in msg.text_body

    def test_expiry_notice_reflects_configured_ttl(self):
        assert "expire in 30 minutes" in self._msg(expires_seconds=60 * 30).text_body
        assert "expire in 1 hour" in self._msg(expires_seconds=60 * 60).text_body

    def test_subject_mentions_verification(self):
        assert "verify" in self._msg().subject.lower()


class TestResetAndChangeEmails:
    def test_reset_targets_reset_route_with_token(self):
        msg = build_password_reset_email(to="u@example.com", raw_token=_TOKEN, base_url=_BASE)
        assert f"{_BASE}/reset?token={_TOKEN}" in msg.text_body
        assert msg.html_body and "Reset password" in msg.html_body

    def test_email_change_targets_verify_email_route(self):
        msg = build_email_change_email(to="new@example.com", raw_token=_TOKEN, base_url=_BASE)
        assert f"{_BASE}/verify-email?token={_TOKEN}" in msg.text_body
        assert msg.html_body and "Confirm email address" in msg.html_body


class TestHumanizeDuration:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (60 * 60 * 24, "24 hours"),
            (60 * 60, "1 hour"),
            (60 * 30, "30 minutes"),
            (60, "1 minute"),
            (45, "45 seconds"),
        ],
    )
    def test_phrases(self, seconds, expected):
        assert _humanize_duration(seconds) == expected
