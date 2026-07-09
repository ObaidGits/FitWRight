"""Contract tests for the pluggable auth provider adapters (ADR-14, R13).

Covers the real adapters wired by ``app.auth.runtime``:

- ``HibpBreachedPasswordCheck`` — HIBP k-anonymity range API (R13.3). The HTTP
  transport is injected, so these assert exactly what leaves the process (only
  the 5-char SHA-1 prefix — never the password or full hash), correct parsing
  of breached vs clean responses, and fail-open on transport error.
- ``SmtpEmailSender`` — stdlib SMTP transport is monkeypatched (no real socket):
  asserts MIME construction/recipients.
- ``ResendEmailSender`` — injected httpx-style client: asserts the send payload
  and that a hard failure surfaces as an exception the enumeration-safe flow
  swallows via ``send_email_safe`` (logged, uniform ack preserved).
- ``TurnstileCaptchaVerifier`` — injected siteverify client: success/failure and
  fail-open on provider error (R13.2).

Requirements: 13.2, 13.3.
"""

import hashlib

import pytest

from app.auth.breach import HibpBreachedPasswordCheck
from app.auth.captcha import TurnstileCaptchaVerifier
from app.auth.email import (
    EmailMessage,
    ResendEmailSender,
    SmtpEmailSender,
    send_email_safe,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# HIBP breached-password check (R13.3)
# ---------------------------------------------------------------------------


class _FakeRangeClient:
    """Records the prefix it was asked for and returns a canned range body."""

    def __init__(self, body: str = "", *, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.requested_prefix: str | None = None

    async def get_range(self, prefix: str) -> str:
        self.requested_prefix = prefix
        if self.error is not None:
            raise self.error
        return self.body


def _sha1_parts(password: str) -> tuple[str, str]:
    digest = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
    return digest[:5], digest[5:]


class TestHibpBreachedPasswordCheck:
    async def test_sends_only_prefix_never_password_or_full_hash(self):
        password = "correct horse battery staple"
        prefix, suffix = _sha1_parts(password)
        client = _FakeRangeClient(body=f"{suffix}:42\n")
        check = HibpBreachedPasswordCheck(client=client)

        await check.check(password)

        # Only the 5-char prefix is transmitted; the full hash/suffix and the
        # password itself never leave the process (the point of k-anonymity).
        assert client.requested_prefix == prefix
        assert len(client.requested_prefix) == 5
        assert suffix not in client.requested_prefix
        assert password not in client.requested_prefix

    async def test_breached_password_is_detected_with_count(self):
        password = "password123"
        _, suffix = _sha1_parts(password)
        # Include a padding-style zero-count line and an unrelated suffix.
        body = f"0000000000000000000000000000000000A:0\n{suffix}:1337\nDEADBEEF:5\n"
        check = HibpBreachedPasswordCheck(client=_FakeRangeClient(body=body))

        result = await check.check(password)

        assert result.breached is True
        assert result.count == 1337
        assert result.checked is True

    async def test_clean_password_is_not_breached(self):
        password = "a-very-unique-passphrase-not-in-any-list"
        body = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA:3\nBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB:9\n"
        check = HibpBreachedPasswordCheck(client=_FakeRangeClient(body=body))

        result = await check.check(password)

        assert result.breached is False
        assert result.count == 0
        assert result.checked is True

    async def test_padding_entry_with_zero_count_is_not_a_match(self):
        password = "padded"
        _, suffix = _sha1_parts(password)
        # HIBP padding entries carry a real suffix with count 0.
        check = HibpBreachedPasswordCheck(client=_FakeRangeClient(body=f"{suffix}:0\n"))

        result = await check.check(password)

        assert result.breached is False
        assert result.count == 0

    async def test_fails_open_on_transport_error(self):
        check = HibpBreachedPasswordCheck(
            client=_FakeRangeClient(error=RuntimeError("network down"))
        )

        result = await check.check("anything")

        # Fail-open: never block auth because HIBP is unavailable (R13.3).
        assert result.breached is False
        assert result.checked is False


# ---------------------------------------------------------------------------
# SMTP email sender
# ---------------------------------------------------------------------------


class _FakeSmtpTransport:
    """In-memory SMTP transport — captures the MIME message, opens no socket."""

    def __init__(self) -> None:
        self.sent = []

    def send(self, mime) -> None:
        self.sent.append(mime)


class TestSmtpEmailSender:
    async def test_builds_mime_with_recipients_subject_and_body(self):
        transport = _FakeSmtpTransport()
        sender = SmtpEmailSender(
            host="smtp.example.com", sender="noreply@fitwright.app", transport=transport
        )

        await sender.send(
            EmailMessage(to="user@example.com", subject="Verify", text_body="link-here")
        )

        assert len(transport.sent) == 1
        mime = transport.sent[0]
        assert mime["To"] == "user@example.com"
        assert mime["From"] == "noreply@fitwright.app"
        assert mime["Subject"] == "Verify"
        assert "link-here" in mime.get_content()

    async def test_includes_html_alternative_when_present(self):
        transport = _FakeSmtpTransport()
        sender = SmtpEmailSender(
            host="smtp.example.com", sender="noreply@fitwright.app", transport=transport
        )

        await sender.send(
            EmailMessage(
                to="user@example.com",
                subject="Hi",
                text_body="text",
                html_body="<b>html</b>",
            )
        )

        mime = transport.sent[0]
        assert mime.is_multipart()
        subtypes = {part.get_content_subtype() for part in mime.iter_parts()}
        assert {"plain", "html"} <= subtypes

    def test_construction_requires_host_and_sender(self):
        with pytest.raises(ValueError):
            SmtpEmailSender(host="", sender="a@b.co")
        with pytest.raises(ValueError):
            SmtpEmailSender(host="smtp.example.com", sender="")


# ---------------------------------------------------------------------------
# Resend email sender
# ---------------------------------------------------------------------------


class _FakeResendClient:
    """Captures the Resend send payload; optionally raises to simulate failure."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    async def post_json(self, url, *, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.error is not None:
            raise self.error
        return {"id": "email_123"}


class TestResendEmailSender:
    async def test_posts_expected_payload_and_auth_header(self):
        client = _FakeResendClient()
        sender = ResendEmailSender(
            api_key="re_secret", sender="noreply@fitwright.app", client=client
        )

        await sender.send(
            EmailMessage(
                to="user@example.com",
                subject="Reset",
                text_body="reset-link",
                html_body="<a>reset</a>",
            )
        )

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["headers"]["Authorization"] == "Bearer re_secret"
        assert call["json"]["from"] == "noreply@fitwright.app"
        assert call["json"]["to"] == ["user@example.com"]
        assert call["json"]["subject"] == "Reset"
        assert call["json"]["text"] == "reset-link"
        assert call["json"]["html"] == "<a>reset</a>"

    async def test_hard_failure_is_swallowed_by_send_email_safe(self, caplog):
        client = _FakeResendClient(error=RuntimeError("resend 500"))
        sender = ResendEmailSender(
            api_key="re_secret", sender="noreply@fitwright.app", client=client
        )
        message = EmailMessage(to="user@example.com", subject="Hi", text_body="body")

        with caplog.at_level("WARNING"):
            delivered = await send_email_safe(sender, message)

        # Enumeration-safe flows must not surface a provider outage as a 500.
        assert delivered is False
        assert "delivery failed" in caplog.text.lower()

    def test_construction_requires_api_key_and_sender(self):
        with pytest.raises(ValueError):
            ResendEmailSender(api_key="", sender="a@b.co")
        with pytest.raises(ValueError):
            ResendEmailSender(api_key="k", sender="")


# ---------------------------------------------------------------------------
# Turnstile CAPTCHA verifier (R13.2)
# ---------------------------------------------------------------------------


class _FakeSiteverifyClient:
    """Returns a canned siteverify payload; optionally raises to simulate error."""

    def __init__(self, payload: dict | None = None, *, error: Exception | None = None) -> None:
        self.payload = payload or {}
        self.error = error
        self.calls = []

    async def post_form(self, url, data):
        self.calls.append({"url": url, "data": data})
        if self.error is not None:
            raise self.error
        return self.payload


class TestTurnstileCaptchaVerifier:
    async def test_success_response_allows(self):
        client = _FakeSiteverifyClient(payload={"success": True})
        verifier = TurnstileCaptchaVerifier(secret="s", client=client)

        result = await verifier.verify("token-abc", remote_ip="203.0.113.5")

        assert result.allowed is True
        assert result.reason == "verified"
        # Secret + token + remote ip are posted to siteverify.
        data = client.calls[0]["data"]
        assert data["secret"] == "s"
        assert data["response"] == "token-abc"
        assert data["remoteip"] == "203.0.113.5"

    async def test_failure_response_rejects(self):
        client = _FakeSiteverifyClient(payload={"success": False})
        verifier = TurnstileCaptchaVerifier(secret="s", client=client)

        result = await verifier.verify("bad-token")

        assert result.allowed is False
        assert result.reason == "invalid_token"

    async def test_missing_token_is_rejected(self):
        client = _FakeSiteverifyClient(payload={"success": True})
        verifier = TurnstileCaptchaVerifier(secret="s", client=client)

        result = await verifier.verify(None)

        assert result.allowed is False
        assert result.reason == "missing_token"
        assert client.calls == []  # nothing to verify → no provider call

    async def test_provider_error_fails_open(self, caplog):
        client = _FakeSiteverifyClient(error=RuntimeError("turnstile down"))
        verifier = TurnstileCaptchaVerifier(secret="s", client=client)

        with caplog.at_level("WARNING"):
            result = await verifier.verify("token")

        # Fail-open on provider error (R13): a CAPTCHA outage must not lock users out.
        assert result.allowed is True
        assert result.reason == "provider_error"
