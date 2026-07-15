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
    "build_contact_notification_email",
    "build_contact_acknowledgement_email",
    "build_review_notification_email",
    "send_email_safe",
    "PermanentEmailError",
]

# Resend transactional-email send endpoint (fixed — SSRF-safe).
RESEND_SEND_ENDPOINT = "https://api.resend.com/emails"

# Default bounded retry policy for transient send failures (see send_email_safe).
_EMAIL_MAX_ATTEMPTS = 3
_EMAIL_BASE_DELAY = 0.5


class PermanentEmailError(Exception):
    """A send failed for a non-retryable reason (bad recipient/sender, 4xx).

    Senders raise this to tell :func:`send_email_safe` NOT to retry — retrying a
    rejected recipient or an auth/validation error only wastes attempts and can
    look like abuse to the provider. Any *other* exception is treated as
    transient and retried with backoff.
    """


async def send_email_safe(
    sender: "EmailSender",
    message: "EmailMessage",
    *,
    attempts: int = _EMAIL_MAX_ATTEMPTS,
    base_delay: float = _EMAIL_BASE_DELAY,
    sleep=None,
) -> bool:
    """Best-effort deliver ``message`` with bounded retry, swallowing failures.

    The enumeration-safe auth flows (verification/reset request) MUST return
    their uniform acknowledgement even when the transactional-email provider is
    down (design §Reliability): a provider outage must never surface as a 500 —
    which would leak, via the differing response, that the address is registered
    — nor block the caller.

    Transient failures (network blips, disconnects, 429/5xx) are retried up to
    ``attempts`` times with exponential backoff; a :class:`PermanentEmailError`
    (bad recipient/sender, permanent 4xx) is **not** retried. On final failure
    the error is logged (no token/PII beyond what the sender logs) so ops can
    alert/retry, and the caller proceeds with the uniform ack. Returns ``True``
    on delivery, ``False`` if it failed and was swallowed. Callers with a durable
    queue (notifications) treat ``False`` as "retry on the next pass".
    """
    import asyncio as _asyncio

    _sleep = sleep or _asyncio.sleep
    attempts = max(1, attempts)
    for attempt in range(attempts):
        try:
            await sender.send(message)
            return True
        except PermanentEmailError:
            logger.warning(
                "Transactional email permanently rejected; not retrying "
                "(uniform ack preserved)",
                exc_info=True,
            )
            return False
        except Exception:
            if attempt == attempts - 1:
                logger.warning(
                    "Transactional email delivery failed after %d attempt(s); "
                    "continuing (uniform ack preserved)",
                    attempts,
                    exc_info=True,
                )
                return False
            await _sleep(base_delay * (2**attempt))
    return False  # pragma: no cover - loop always returns


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


# Product identity used across every transactional auth email (branding).
_BRAND_NAME = "FitWright"
_SUPPORT_EMAIL = "support@fitwright.tech"


def _humanize_duration(seconds: int) -> str:
    """Render a TTL (seconds) as a short human phrase for the expiry notice."""
    seconds = max(0, int(seconds))
    if seconds % 3600 == 0 and seconds >= 3600:
        hours = seconds // 3600
        return f"{hours} hour" + ("s" if hours != 1 else "")
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} minute" + ("s" if minutes != 1 else "")
    return f"{seconds} second" + ("s" if seconds != 1 else "")


def _html_escape(value: str) -> str:
    """Escape a value for safe interpolation into the HTML body."""
    import html

    return html.escape(value, quote=True)


def _render_branded_email(
    *,
    heading: str,
    intro: str,
    cta_label: str,
    link: str,
    expires_phrase: str,
    security_note: str,
) -> tuple[str, str]:
    """Render the shared branded (text, html) pair for a link-carrying email.

    Both bodies include: branding, a primary call-to-action (button in HTML), a
    plain-URL fallback, an expiration notice, a security note, and a support
    contact. The **plain URL is always present verbatim in the text body** so
    link extraction (and copy/paste in clients that strip HTML) always works.
    """
    text_body = (
        f"{heading}\n\n"
        f"{intro}\n\n"
        f"{cta_label}:\n{link}\n\n"
        f"This link will expire in {expires_phrase}.\n\n"
        f"{security_note}\n\n"
        f"Need help? Contact us at {_SUPPORT_EMAIL}.\n\n"
        f"— The {_BRAND_NAME} team"
    )

    safe_heading = _html_escape(heading)
    safe_intro = _html_escape(intro)
    safe_cta = _html_escape(cta_label)
    safe_link_text = _html_escape(link)
    safe_link_href = _html_escape(link)
    safe_expires = _html_escape(expires_phrase)
    safe_security = _html_escape(security_note)
    safe_support = _html_escape(_SUPPORT_EMAIL)

    html_body = f"""\
<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2933;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f7;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e4e7eb;">
          <tr><td style="padding:24px 32px;background:#111827;color:#ffffff;font-size:20px;font-weight:600;">{_BRAND_NAME}</td></tr>
          <tr><td style="padding:32px;">
            <h1 style="margin:0 0 16px;font-size:20px;font-weight:600;color:#111827;">{safe_heading}</h1>
            <p style="margin:0 0 24px;font-size:15px;line-height:1.5;color:#374151;">{safe_intro}</p>
            <table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 24px;">
              <tr><td style="border-radius:8px;background:#2563eb;">
                <a href="{safe_link_href}" style="display:inline-block;padding:12px 28px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:8px;">{safe_cta}</a>
              </td></tr>
            </table>
            <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">If the button doesn't work, copy and paste this link into your browser:</p>
            <p style="margin:0 0 24px;font-size:13px;word-break:break-all;"><a href="{safe_link_href}" style="color:#2563eb;">{safe_link_text}</a></p>
            <p style="margin:0 0 8px;font-size:13px;color:#6b7280;">This link will expire in {safe_expires}.</p>
            <p style="margin:0 0 24px;font-size:13px;color:#6b7280;">{safe_security}</p>
            <hr style="border:none;border-top:1px solid #e4e7eb;margin:0 0 16px;" />
            <p style="margin:0;font-size:12px;color:#9aa5b1;">Need help? Contact us at <a href="mailto:{safe_support}" style="color:#2563eb;">{safe_support}</a>.</p>
          </td></tr>
        </table>
        <p style="margin:16px 0 0;font-size:12px;color:#9aa5b1;">© {_BRAND_NAME}</p>
      </td></tr>
    </table>
  </body>
</html>"""
    return text_body, html_body


def build_verification_email(
    *, to: str, raw_token: str, base_url: str, expires_seconds: int = 60 * 60 * 24
) -> "EmailMessage":
    """Compose the branded email-verification message (link → ``/verify``).

    Includes an HTML + plain-text body with branding, a verify button, a
    plain-URL fallback, an expiration notice, a security note, and a support
    contact (R5). The raw token appears only in the link (only its ``sha256`` is
    stored). ``expires_seconds`` drives the human-readable expiry notice.
    """
    link = _link(base_url, "/verify", raw_token)
    text_body, html_body = _render_branded_email(
        heading=f"Welcome to {_BRAND_NAME}!",
        intro=(
            "Confirm your email address to finish setting up your account and "
            "start tailoring resumes."
        ),
        cta_label="Verify email address",
        link=link,
        expires_phrase=_humanize_duration(expires_seconds),
        security_note=(
            "If you didn't create a FitWright account, you can safely ignore this "
            "message — no account will be activated."
        ),
    )
    return EmailMessage(
        to=to,
        subject=f"Verify your email address — {_BRAND_NAME}",
        text_body=text_body,
        html_body=html_body,
    )


def build_password_reset_email(
    *, to: str, raw_token: str, base_url: str, expires_seconds: int = 60 * 30
) -> "EmailMessage":
    """Compose the branded password-reset message (link → ``/reset``)."""
    link = _link(base_url, "/reset", raw_token)
    text_body, html_body = _render_branded_email(
        heading="Reset your password",
        intro=(
            "We received a request to reset your FitWright password. Use the "
            "button below to choose a new one."
        ),
        cta_label="Reset password",
        link=link,
        expires_phrase=_humanize_duration(expires_seconds),
        security_note=(
            "If you didn't request this, you can safely ignore this message — your "
            "password will not change."
        ),
    )
    return EmailMessage(
        to=to,
        subject=f"Reset your password — {_BRAND_NAME}",
        text_body=text_body,
        html_body=html_body,
    )


def build_email_change_email(
    *, to: str, raw_token: str, base_url: str, expires_seconds: int = 60 * 60 * 24
) -> "EmailMessage":
    """Compose the branded email-change confirmation (link → ``/verify-email``).

    Sent to the *new* address in a verify-before-switch email change (R7.4): the
    account's primary email only changes once this link is confirmed.
    """
    link = _link(base_url, "/verify-email", raw_token)
    text_body, html_body = _render_branded_email(
        heading="Confirm your new email address",
        intro=(
            "You asked to change the email address on your FitWright account to "
            "this one. Confirm the change using the button below. Your account "
            "email will not change until you confirm."
        ),
        cta_label="Confirm email address",
        link=link,
        expires_phrase=_humanize_duration(expires_seconds),
        security_note=(
            "If you didn't request this, you can safely ignore this message — your "
            "account email will not change."
        ),
    )
    return EmailMessage(
        to=to,
        subject=f"Confirm your new email address — {_BRAND_NAME}",
        text_body=text_body,
        html_body=html_body,
    )


def build_contact_notification_email(
    *,
    to: str,
    reference: str,
    name: str,
    email: str,
    subject: str,
    message: str,
    purpose: str,
    company: str | None = None,
    linkedin: str | None = None,
    project_type: str | None = None,
    budget: str | None = None,
) -> "EmailMessage":
    """Compose the notification sent to the site owner for a contact submission.

    The submitter's address goes in ``Reply-To`` semantics via the body (the
    ``From`` is always the app's own verified sender, so provider SPF/DKIM stays
    intact and the message is not treated as spoofed). All values are already
    validated/sanitized by :class:`~app.schemas.contact.ContactRequest`.
    """
    lines = [
        f"New contact submission ({reference})",
        "",
        f"Name:    {name}",
        f"Email:   {email}",
        f"Purpose: {purpose}",
    ]
    if company:
        lines.append(f"Company: {company}")
    if linkedin:
        lines.append(f"LinkedIn: {linkedin}")
    if project_type:
        lines.append(f"Project: {project_type}")
    if budget:
        lines.append(f"Budget:  {budget}")
    lines += ["", f"Subject: {subject}", "", "Message:", message]
    return EmailMessage(
        to=to,
        subject=f"[FitWright contact] {subject}",
        text_body="\n".join(lines),
    )


def build_review_notification_email(
    *,
    to: str,
    reference: str,
    rating: int,
    title: str,
    body: str,
    name: str | None = None,
    email: str | None = None,
) -> "EmailMessage":
    """Compose the notification sent to the owner for a submitted review."""
    stars = "★" * max(0, min(5, rating)) + "☆" * (5 - max(0, min(5, rating)))
    lines = [
        f"New product review ({reference})",
        "",
        f"Rating:  {stars} ({rating}/5)",
        f"Title:   {title}",
        f"From:    {name or 'Anonymous'}",
    ]
    if email:
        lines.append(f"Email:   {email}")
    lines += ["", "Review:", body]
    return EmailMessage(
        to=to,
        subject=f"[FitWright review] {rating}★ — {title}",
        text_body="\n".join(lines),
    )


def build_contact_acknowledgement_email(
    *, to: str, name: str, reference: str, subject: str
) -> "EmailMessage":
    """Compose the auto-acknowledgement sent back to the submitter."""
    first = name.split(" ", 1)[0] if name else "there"
    return EmailMessage(
        to=to,
        subject="Thanks for reaching out — FitWright",
        text_body=(
            f"Hi {first},\n\n"
            "Thanks for getting in touch. Your message has been received and I'll "
            "get back to you as soon as I can — typically within 1–2 business days.\n\n"
            f"Reference: {reference}\n"
            f"Subject: {subject}\n\n"
            "If your message is time-sensitive, just reply to this email.\n\n"
            "— Obaidullah Zeeshan · FitWright"
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
        try:
            await asyncio.to_thread(self._transport.send, mime)
        except (
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
            smtplib.SMTPNotSupportedError,
        ) as exc:
            # Permanent: recipient/sender rejected or feature unsupported — no
            # amount of retrying will help, so don't (send_email_safe honors this).
            raise PermanentEmailError(str(exc)) from exc


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
        try:
            await self._client.post_json(
                self._endpoint,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except Exception as exc:
            # A permanent 4xx (bad request / auth / invalid recipient — but NOT
            # 429 rate limit) should not be retried; 429 and 5xx are transient.
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if isinstance(status, int) and 400 <= status < 500 and status != 429:
                raise PermanentEmailError(f"resend responded {status}") from exc
            raise
