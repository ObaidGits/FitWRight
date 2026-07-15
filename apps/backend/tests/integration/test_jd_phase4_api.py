"""Integration tests for Phase 4 JD endpoints: extract-rendered, webhook, health."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings as app_settings
from app.main import app

_RENDERED_HTML = (
    '<html lang="en"><body><article><h1>Senior Platform Engineer</h1>'
    "<h2>Responsibilities</h2><ul><li>Design and build scalable backend services</li>"
    "<li>Lead technical architecture decisions</li></ul>"
    "<h2>Qualifications</h2><ul><li>5+ years backend experience</li></ul><p>"
    + ("We value collaboration and continuous improvement. " * 12)
    + "</p></article></body></html>"
)


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture(autouse=True)
def _reset_kv(isolated_db):
    from app.platform import reset_container
    reset_container()
    yield
    reset_container()


class TestExtractRendered:
    async def test_happy_path(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.post(
                "/api/v1/jobs/extract-rendered",
                json={"url": "https://spa.example.com/jobs/1", "html": _RENDERED_HTML},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"]
        assert body["source"] == "headless_dom"
        assert body["schema_version"] is not None

    async def test_disabled_returns_404(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "jd_extension_fallback_enabled", False)
        async with _client() as c:
            resp = await c.post(
                "/api/v1/jobs/extract-rendered",
                json={"url": "https://spa.example.com/jobs/1", "html": _RENDERED_HTML},
            )
        assert resp.status_code == 404

    async def test_empty_html_rejected_by_schema(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.post(
                "/api/v1/jobs/extract-rendered",
                json={"url": "https://spa.example.com/jobs/1", "html": ""},
            )
        assert resp.status_code == 422  # min_length violation


class TestAdapterHealth:
    async def test_health_endpoint(self, isolated_db, owner_id):
        async with _client() as c:
            resp = await c.get("/api/v1/jobs/jd/adapter-health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall"] in ("healthy", "degraded")
        assert "ashby" in body["adapters"]


class TestWebhook:
    async def test_disabled_returns_404(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "jd_webhook_enabled", False)
        async with _client() as c:
            resp = await c.post("/api/v1/jobs/webhook", json={"url": "https://x.com/j"})
        assert resp.status_code == 404

    async def test_invalid_signature_401(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "jd_webhook_enabled", True)
        monkeypatch.setattr(app_settings, "jd_webhook_secret", "topsecret")
        async with _client() as c:
            resp = await c.post(
                "/api/v1/jobs/webhook",
                content=json.dumps({"url": "https://x.com/j", "description": "x"}),
                headers={"X-JD-Signature": "wrong"},
            )
        assert resp.status_code == 401

    async def test_valid_signature_ingests(self, isolated_db, owner_id, monkeypatch):
        monkeypatch.setattr(app_settings, "jd_webhook_enabled", True)
        monkeypatch.setattr(app_settings, "jd_webhook_secret", "topsecret")
        payload = {
            "url": "https://acme.com/careers/eng",
            "title": "Backend Engineer",
            "company": "Acme",
            "description": "Build and ship reliable software with a great team. " * 8,
        }
        raw = json.dumps(payload)
        sig = hmac.new(b"topsecret", raw.encode(), hashlib.sha256).hexdigest()
        async with _client() as c:
            resp = await c.post(
                "/api/v1/jobs/webhook",
                content=raw,
                headers={"X-JD-Signature": sig, "content-type": "application/json"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["content_length"] > 0
