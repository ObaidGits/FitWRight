"""Pluggable transactional email sender (ADR-14).

Auth flows (verification links, password-reset links, email-change confirmation)
send mail through the :class:`EmailSender` interface, never a concrete provider.
The shipped default, :class:`LoggingEmailSender`, is the *intended* local/dev
behavior — it logs the message (subject + recipient + a redacted body preview)
so a developer running without an email provider can copy the verification/reset
link straight from the server log. It is a real, working adapter, not a stub.

A hosted deployment sets a provider (e.g. Resend/Brevo) via config in a later
task; because call sites depend only on this interface, wiring a real provider
is an adapter swap with no flow changes (ADR-14 "free-tier is config").
"""

from __future__ import annotations

import abc
import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeEmailMessage
from typing import Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "EmailMessage",
    "EmailSender",
    "LoggingEmailSender",
    "SmtpEmailSender",
    "SmtpTransport",
    "ResendEmailSender",
    "ResendHttpClient",
    "HttpxResendClient",
    "RESEND_SEND_ENDPOINT",
    "build_verification_email",
    "build_password_reset_email",
    "build_email_change_email",
    "send_email_safe",
]

# Resend transactional-email send endpoint (fixed — SSRF-safe).
RESEND_SEND_ENDPOINT = "https://api.resend.com/emails"


async def send_email_safe(sender: "EmailSender", message: "EmailMessage") -> bool:
    """Best-effort deliver ``message``, swallowing hard failures (logged).

    The enumeration-safe auth flows (verification/reset request) MUST return
    their uniform acknowledgement even when the transactional-email provider is
    down (design §Reliability): a provider outage must never surface as a 500 —
    which would leak, via the differing response, that the address is registered
    — nor block the caller. The failure is logged (no token/PII beyond what the
    sender itself logs) so it can be alerted on / retried / queued by ops, and
    the caller proceeds with the same uniform ack. Returns ``True`` on delivery,
    ``False`` if the send failed and was swallowed.
    """
    try:
        await sender.send(message)
        return True
    except Exception:
        logger.warning(
            "Transactional email delivery failed; continuing (uniform ack preserved)",
            exc_info=True,
        )
        return False


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A transactional email to send.

    ``text_body`` is the plain-text alternative; ``html_body`` is optional rich
    content. At least one body should be provided by callers.
    """

    to: str
    subject: str
    text_body: str
    html_body: str | None = None


def _link(base_url: str, path: str, raw_token: str) -> str:
    """Build a frontend link carrying a raw token in the query string.

    The raw token lives only in this link and in the email body preview logged
    by the dev sender — never in the database (only its ``sha256`` is stored).
    """
    from urllib.parse import quote

    return f"{base_url.rstrip('/')}{path}?token={quote(raw_token, safe='')}"


def build_verification_email(*, to: str, raw_token: str, base_url: str) -> "EmailMessage":
    """Compose the email-verification message (link → ``/verify``)."""
    link = _link(base_url, "/verify", raw_token)
    return EmailMessage(
        to=to,
        subject="Verify your email address",
        text_body=(
            "Welcome to FitWright! Confirm your email address to finish setting "
            f"up your account:\n\n{link}\n\n"
            "If you didn't create an account, you can ignore this message."
        ),
    )


def build_password_reset_email(*, to: str, raw_token: str, base_url: str) -> "EmailMessage":
    """Compose the password-reset message (link → ``/reset``)."""
    link = _link(base_url, "/reset", raw_token)
    return EmailMessage(
        to=to,
        subject="Reset your password",
        text_body=(
            "We received a request to reset your FitWright password. Use the link "
            f"below to choose a new one:\n\n{link}\n\n"
            "If you didn't request this, you can safely ignore this message — your "
            "password will not change."
        ),
    )


def build_email_change_email(*, to: str, raw_token: str, base_url: str) -> "EmailMessage":
    """Compose the email-change confirmation message (link → ``/verify-email``).

    Sent to the *new* address in a verify-before-switch email change (R7.4): the
    account's primary email only changes once this link is confirmed.
    """
    link = _link(base_url, "/verify-email", raw_token)
    return EmailMessage(
        to=to,
        subject="Confirm your new email address",
        text_body=(
            "You asked to change the email address on your FitWright account to "
            "this one. Confirm the change using the link below:\n\n"
            f"{link}\n\n"
            "Your account email will not change until you confirm. If you didn't "
            "request this, you can safely ignore this message."
        ),
    )


class EmailSender(abc.ABC):
    """Interface for delivering transactional email."""

    @abc.abstractmethod
    async def send(self, message: EmailMessage) -> None:
        """Deliver ``message``. Implementations should raise on hard failure so
        callers can decide whether to retry or degrade."""


def _preview(body: str, *, limit: int = 200) -> str:
    """Single-line, length-bounded preview of a body for safe logging."""
    collapsed = " ".join(body.split())
    if len(collapsed) > limit:
        return collapsed[:limit] + "…"
    return collapsed


class LoggingEmailSender(EmailSender):
    """Default dev adapter: logs the email instead of delivering it.

    Intended for local development and CI, where there is no mail provider. The
    body preview lets a developer retrieve verification/reset links from logs;
    it is truncated and single-lined to avoid dumping large payloads.
    """

    def __init__(self, *, level: int = logging.INFO) -> None:
        self._level = level

    async def send(self, message: EmailMessage) -> None:
        body = message.html_body or message.text_body or ""
        logger.log(
            self._level,
            "[dev-email] to=%s subject=%r body_preview=%r",
            message.to,
            message.subject,
            _preview(body),
        )


def _build_mime(message: "EmailMessage", *, sender: str) -> MimeEmailMessage:
    """Render an :class:`EmailMessage` into a MIME message with the given From.

    Sets a plain-text body (always present) and, when supplied, an HTML
    alternative so clients can pick the richer rendering.
    """
    mime = MimeEmailMessage()
    mime["From"] = sender
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime.set_content(message.text_body or "")
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html")
    return mime


class SmtpTransport(Protocol):
    """Minimal transport seam over ``smtplib`` so the SMTP sender is testable.

    Tests monkeypatch this with an in-memory fake — no real socket is opened.
    """

    def send(self, mime: MimeEmailMessage) -> None:
        """Deliver a fully-rendered MIME message (synchronously)."""
        ...


class _SmtplibTransport:
    """Default ``smtplib``-backed SMTP transport (STARTTLS, authenticated).

    Synchronous by nature; :class:`SmtpEmailSender` runs it in a worker thread
    so it never blocks the event loop.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        timeout: float,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls
        self._timeout = timeout

    def send(self, mime: MimeEmailMessage) -> None:
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as client:
            client.ehlo()
            if self._use_tls:
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self._username:
                client.login(self._username, self._password)
            client.send_message(mime)


class SmtpEmailSender(EmailSender):
    """Real SMTP transactional sender (stdlib ``smtplib``/``email``, TLS).

    Reads host/port/user/password/from from config. Because ``smtplib`` is
    blocking, delivery runs in a worker thread via :func:`asyncio.to_thread` so
    the event loop is never stalled. Raises on hard failure so
    :func:`send_email_safe` can swallow it and preserve the uniform ack.

    Selected by ``EMAIL_PROVIDER=smtp``. Live delivery requires deploy-time SMTP
    credentials; when the host is missing the factory falls back to the dev
    logging sender rather than constructing an unusable sender.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 587,
        username: str = "",
        password: str = "",
        sender: str,
        use_tls: bool = True,
        timeout: float = 10.0,
        transport: SmtpTransport | None = None,
    ) -> None:
        if not host:
            raise ValueError("SmtpEmailSender requires an SMTP host")
        if not sender:
            raise ValueError("SmtpEmailSender requires a From address (EMAIL_FROM)")
        self._sender = sender
        self._transport: SmtpTransport = transport or _SmtplibTransport(
            host=host,
            port=port,
            username=username,
            password=password,
            use_tls=use_tls,
            timeout=timeout,
        )

    async def send(self, message: EmailMessage) -> None:
        mime = _build_mime(message, sender=self._sender)
        await asyncio.to_thread(self._transport.send, mime)


class ResendHttpClient(Protocol):
    """Transport for the Resend send API, injected so the sender is testable."""

    async def post_json(self, url: str, *, headers: dict[str, str], json: dict) -> dict:
        """POST ``json`` to ``url`` with ``headers``; return the decoded JSON object."""
        ...


class HttpxResendClient:
    """Default httpx-backed Resend client (fixed endpoint — SSRF-safe)."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self._timeout = timeout

    async def post_json(self, url: str, *, headers: dict[str, str], json: dict) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=json)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("Resend returned a non-object response")
        return payload


class ResendEmailSender(EmailSender):
    """Real Resend HTTP transactional sender (injected httpx client).

    Posts the message to Resend's send endpoint with a Bearer API key. Reads the
    API key and From address from config. Raises on hard failure so
    :func:`send_email_safe` can swallow it and preserve the uniform ack.

    Selected by ``EMAIL_PROVIDER=resend``. Live delivery requires a deploy-time
    ``EMAIL_API_KEY``; when it is missing the factory falls back to the dev
    logging sender rather than constructing an unusable sender.
    """

    def __init__(
        self,
        *,
        api_key: str,
        sender: str,
        client: ResendHttpClient | None = None,
        endpoint: str = RESEND_SEND_ENDPOINT,
    ) -> None:
        if not api_key:
            raise ValueError("ResendEmailSender requires an API key (EMAIL_API_KEY)")
        if not sender:
            raise ValueError("ResendEmailSender requires a From address (EMAIL_FROM)")
        self._api_key = api_key
        self._sender = sender
        self._client = client or HttpxResendClient()
        self._endpoint = endpoint

    async def send(self, message: EmailMessage) -> None:
        body: dict[str, object] = {
            "from": self._sender,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text_body or "",
        }
        if message.html_body:
            body["html"] = message.html_body
        await self._client.post_json(
            self._endpoint,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
