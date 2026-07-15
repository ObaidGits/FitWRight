"""Integration tests for the public reviews endpoint (Connect page)."""

import pytest
from httpx import ASGITransport, AsyncClient

import app.routers.reviews as reviews_mod
from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def captured_emails(monkeypatch):
    sent = []

    async def _fake_send(sender, message, **kwargs):
        sent.append(message)
        return True

    monkeypatch.setattr(reviews_mod, "send_email_safe", _fake_send)
    monkeypatch.setattr(reviews_mod.settings, "contact_recipient_email", "owner@example.com", raising=False)
    return sent


def _valid(**overrides):
    base = {
        "rating": 5,
        "title": "Saved me hours",
        "body": "FitWright tailored my resume in seconds and the diff was spot on.",
        "name": "Grace Hopper",
        "elapsed_ms": 4000,
    }
    base.update(overrides)
    return base


def _hdr(ip: str) -> dict[str, str]:
    return {"x-forwarded-for": ip}


class TestReviewSubmit:
    async def test_valid_review_notifies_owner(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/reviews/", json=_valid(), headers=_hdr("11.0.0.1"))
        assert resp.status_code == 200
        assert resp.json()["reference"]
        assert len(captured_emails) == 1
        assert captured_emails[0].to == "owner@example.com"

    async def test_anonymous_review_allowed(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/reviews/", json=_valid(name=None), headers=_hdr("11.0.0.2")
            )
        assert resp.status_code == 200

    async def test_rating_out_of_range_422(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/reviews/", json=_valid(rating=6), headers=_hdr("11.0.0.3"))
        assert resp.status_code == 422
        assert captured_emails == []

    async def test_short_body_422(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/reviews/", json=_valid(body="ok"), headers=_hdr("11.0.0.4"))
        assert resp.status_code == 422

    async def test_header_injection_in_title_422(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/reviews/", json=_valid(title="Nice\r\nBcc: x@y.com"), headers=_hdr("11.0.0.5")
            )
        assert resp.status_code == 422
        assert captured_emails == []

    async def test_honeypot_dropped(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/reviews/",
                json=_valid(company_website="http://spam"),
                headers=_hdr("11.0.0.6"),
            )
        assert resp.status_code == 200
        assert captured_emails == []

    async def test_too_fast_dropped(self, client, captured_emails):
        async with client:
            resp = await client.post("/api/v1/reviews/", json=_valid(elapsed_ms=100), headers=_hdr("11.0.0.7"))
        assert resp.status_code == 200
        assert captured_emails == []

    async def test_bad_email_422(self, client, captured_emails):
        async with client:
            resp = await client.post(
                "/api/v1/reviews/", json=_valid(email="nope"), headers=_hdr("11.0.0.8")
            )
        assert resp.status_code == 422

    async def test_rate_limited_after_burst(self, client, captured_emails):
        async with client:
            statuses = []
            for i in range(5):
                r = await client.post(
                    "/api/v1/reviews/",
                    json=_valid(title=f"Probe {i}", body=f"Distinct review body number {i} here."),
                    headers=_hdr("11.0.0.50"),
                )
                statuses.append(r.status_code)
        assert 429 in statuses
