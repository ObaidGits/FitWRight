"""Email delivery resilience tests: bounded retry, backoff, and permanent
vs transient classification.

Offline — no SMTP/Resend account or network. Uses fake senders/transports and a
no-op sleep so retry/backoff is exercised deterministically. Live provider
delivery is NOT verified here (needs credentials; documented as such).
"""

from __future__ import annotations

import smtplib

import pytest

from app.auth.email import (
    EmailMessage,
    PermanentEmailError,
    ResendEmailSender,
    SmtpEmailSender,
    send_email_safe,
)

MSG = EmailMessage(to="u@example.com", subject="s", text_body="b")


class _Sender:
    """Fake EmailSender scripted with a sequence of outcomes."""

    def __init__(self, outcomes):
        # each outcome: None (success) | Exception instance (raised)
        self.outcomes = list(outcomes)
        self.calls = 0

    async def send(self, message):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if outcome is not None:
            raise outcome


def _sleeps():
    calls: list[float] = []

    async def fake_sleep(d):
        calls.append(d)

    return calls, fake_sleep


async def test_success_first_try_no_retry():
    s = _Sender([None])
    sleeps, sleep = _sleeps()
    assert await send_email_safe(s, MSG, sleep=sleep) is True
    assert s.calls == 1
    assert sleeps == []


async def test_transient_then_success_retries_with_backoff():
    s = _Sender([RuntimeError("blip"), RuntimeError("blip"), None])
    sleeps, sleep = _sleeps()
    assert await send_email_safe(s, MSG, attempts=3, base_delay=0.1, sleep=sleep) is True
    assert s.calls == 3
    assert sleeps == [0.1, 0.2]  # exponential backoff


async def test_transient_exhausts_attempts_then_false():
    s = _Sender([RuntimeError("x"), RuntimeError("x"), RuntimeError("x")])
    sleeps, sleep = _sleeps()
    assert await send_email_safe(s, MSG, attempts=3, base_delay=0.1, sleep=sleep) is False
    assert s.calls == 3
    assert len(sleeps) == 2


async def test_permanent_error_is_not_retried():
    s = _Sender([PermanentEmailError("550 no such user"), None])
    sleeps, sleep = _sleeps()
    assert await send_email_safe(s, MSG, attempts=3, sleep=sleep) is False
    assert s.calls == 1  # gave up immediately
    assert sleeps == []


# -- sender-level classification -------------------------------------------


class _RaisingSmtpTransport:
    def __init__(self, exc):
        self._exc = exc

    def send(self, mime):
        raise self._exc


async def test_smtp_recipient_refused_is_permanent():
    sender = SmtpEmailSender(
        host="h", sender="from@x.com",
        transport=_RaisingSmtpTransport(smtplib.SMTPRecipientsRefused({})),
    )
    with pytest.raises(PermanentEmailError):
        await sender.send(MSG)


async def test_smtp_generic_error_is_transient():
    sender = SmtpEmailSender(
        host="h", sender="from@x.com",
        transport=_RaisingSmtpTransport(smtplib.SMTPServerDisconnected("bye")),
    )
    with pytest.raises(smtplib.SMTPServerDisconnected):
        await sender.send(MSG)  # NOT PermanentEmailError → will be retried


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _HttpErr(Exception):
    def __init__(self, code):
        super().__init__(f"http {code}")
        self.response = _Resp(code)


class _ResendClient:
    def __init__(self, exc):
        self._exc = exc

    async def post_json(self, url, *, headers, json):
        raise self._exc


async def test_resend_4xx_is_permanent():
    sender = ResendEmailSender(api_key="k", sender="from@x.com", client=_ResendClient(_HttpErr(400)))
    with pytest.raises(PermanentEmailError):
        await sender.send(MSG)


async def test_resend_429_is_transient():
    sender = ResendEmailSender(api_key="k", sender="from@x.com", client=_ResendClient(_HttpErr(429)))
    with pytest.raises(_HttpErr):
        await sender.send(MSG)  # rate limit → transient, retried by send_email_safe


async def test_resend_5xx_is_transient():
    sender = ResendEmailSender(api_key="k", sender="from@x.com", client=_ResendClient(_HttpErr(503)))
    with pytest.raises(_HttpErr):
        await sender.send(MSG)
