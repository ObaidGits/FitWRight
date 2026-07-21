"""Unit tests for the default pluggable auth interface adapters.

The shipped defaults are real, intended local behavior (not stubs):
- :class:`LoggingEmailSender` logs the message rather than delivering it;
- :class:`AllowAllCaptchaVerifier` fails open (allows) and logs;
- :class:`NoopBreachedPasswordCheck` fails open (not breached) and logs.
"""

import logging

import pytest

from app.auth import (
    AllowAllCaptchaVerifier,
    BreachedPasswordCheck,
    CaptchaVerifier,
    EmailMessage,
    EmailSender,
    LoggingEmailSender,
    NoopBreachedPasswordCheck,
)

pytestmark = pytest.mark.unit


class TestLoggingEmailSender:
    async def test_logs_and_does_not_raise(self, caplog):
        sender = LoggingEmailSender()
        assert isinstance(sender, EmailSender)
        msg = EmailMessage(
            to="user@example.com",
            subject="Verify your email",
            text_body="Visit https://app.example.com/verify?token=abc to verify.",
        )
        with caplog.at_level(logging.INFO, logger="app.auth.email"):
            await sender.send(msg)
        assert "dev-email" in caplog.text
        assert "user@example.com" in caplog.text

    async def test_prefers_html_body_in_preview(self, caplog):
        sender = LoggingEmailSender()
        msg = EmailMessage(
            to="u@e.com",
            subject="Reset",
            text_body="text version",
            html_body="<a href='https://app/reset'>reset link</a>",
        )
        with caplog.at_level(logging.INFO, logger="app.auth.email"):
            await sender.send(msg)
        assert "reset link" in caplog.text

    async def test_long_body_is_truncated(self, caplog):
        sender = LoggingEmailSender()
        msg = EmailMessage(to="u@e.com", subject="s", text_body="x" * 5000)
        with caplog.at_level(logging.INFO, logger="app.auth.email"):
            await sender.send(msg)
        assert "..." in caplog.text  # preview truncation marker


class TestAllowAllCaptchaVerifier:
    async def test_allows_when_no_token(self):
        verifier = AllowAllCaptchaVerifier()
        assert isinstance(verifier, CaptchaVerifier)
        result = await verifier.verify(None)
        assert result.allowed is True
        assert result.reason == "disabled"

    async def test_allows_with_token_and_ip(self):
        verifier = AllowAllCaptchaVerifier()
        result = await verifier.verify("some-token", remote_ip="203.0.113.7")
        assert result.allowed is True


class TestNoopBreachedPasswordCheck:
    async def test_never_breached_and_marked_unchecked(self):
        check = NoopBreachedPasswordCheck()
        assert isinstance(check, BreachedPasswordCheck)
        result = await check.check("hunter2-correct-horse-battery")
        assert result.breached is False
        assert result.count == 0
        # Fail-open must be distinguishable from a verified-clean result.
        assert result.checked is False
