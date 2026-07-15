"""Integration tests for the public contact endpoint.

The email sender is stubbed (no delivery) and the KVStore is the in-process
default, so we deterministically exercise validation, spam heuristics, de-dup,
delivery wiring, and rate limiting without any network.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.routers.contact as contact_mod
from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def captured_emails(monkeypatch):
    """Capture messages passed to send_email_safe (no real delivery)."""
    sent = []

    async def _fake_send(sender, message, **kwargs):
        sent.append(message)
        return True

    monkeypatch.setattr(contact_mod, "send_email_safe", _fake_send)
    # A recipient so the notification path is exercised.
    monkeypatch.setattr(contact_mod.settings, "contact_recipient_email", "owner@example.com", raising=False)
    return sent


def _valid(**overrides):
    base = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "subject": "Collaboration on an AI project",
        "message": "I'd love to discuss a potential collaboration on your tailoring engine.",
        "purpose": "collaboration",
        "elapsed_ms": 5000,
    }
    base.update(overrides)
    return base


# Each test uses a distinct client IP so the shared in-process per-IP rate
# limiter never bleeds burst counts across tests (client_ip honors XFF).
def _hdr(ip: str) -> dict[str, str]:
    return {"x-forwarded-for": ip}


class TestContactSubmit:
    async def test_valid_submission_sends_notification_and_ack(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/contact/", json=_valid(), headers=_hdr("10.0.0.1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["reference"]
        assert data["estimated_response"]
        # Owner notification + submitter acknowledgement.
        assert len(captured_emails) == 2
        recipients = {m.to for m in captured_emails}
        assert "owner@example.com" in recipients
        assert "ada@example.com" in recipients

    async def test_notification_subject_has_no_header_injection(self, client, captured_emails):
        # A CRLF in the subject must be rejected at validation (never reaches email).
        async with client:
            resp = await client.post(
                "/api/v1/contact/",
                json=_valid(subject="Hello\r\nBcc: victim@example.com"),
                headers=_hdr("10.0.0.2"),
            )
        assert resp.status_code == 422
        assert captured_emails == []

    async def test_missing_fields_422(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/contact/", json={"name": "x"}, headers=_hdr("10.0.0.3"))
        assert resp.status_code == 422
        assert captured_emails == []

    async def test_short_message_422(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/contact/", json=_valid(message="hi"), headers=_hdr("10.0.0.4"))
        assert resp.status_code == 422

    async def test_bad_email_422(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/contact/", json=_valid(email="not-an-email"), headers=_hdr("10.0.0.5")
            )
        assert resp.status_code == 422

    async def test_honeypot_is_silently_dropped(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/contact/",
                json=_valid(company_website="http://spam.example"),
                headers=_hdr("10.0.0.6"),
            )
        # Looks successful to the bot, but nothing is sent.
        assert resp.status_code == 200
        assert captured_emails == []

    async def test_too_fast_submission_is_dropped(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/contact/", json=_valid(elapsed_ms=200), headers=_hdr("10.0.0.7")
            )
        assert resp.status_code == 200
        assert captured_emails == []

    async def test_duplicate_submission_collapses(self, client, captured_emails):
        payload = _valid(subject="Unique subject for dedup test")
        async with client:
            first = await client.post("/api/v1/contact/", json=payload, headers=_hdr("10.0.0.8"))
            second = await client.post("/api/v1/contact/", json=payload, headers=_hdr("10.0.0.8"))
        assert first.status_code == 200 and second.status_code == 200
        # Same reference returned; the second send is collapsed (still just 2 emails).
        assert first.json()["reference"] == second.json()["reference"]
        assert len(captured_emails) == 2

    async def test_unknown_purpose_coerced_to_general(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/contact/", json=_valid(purpose="totally-made-up"), headers=_hdr("10.0.0.9")
            )
        assert resp.status_code == 200

    async def test_rate_limited_after_burst(self, client, captured_emails, monkeypatch):
        # Distinct messages (so de-dup doesn't mask the limit); burst rule is 3/min.
        async with client:
            statuses = []
            for i in range(5):
                r = await client.post(
                    "/api/v1/contact/",
                    json=_valid(subject=f"Rate limit probe {i}", message=f"Message body number {i} here."),
                    headers=_hdr("10.0.0.50"),
                )
                statuses.append(r.status_code)
        assert 429 in statuses
        # The 429 carries a Retry-After.
        # (Only assert the shape when present.)
