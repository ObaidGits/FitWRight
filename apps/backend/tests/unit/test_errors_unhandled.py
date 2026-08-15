"""Unhandled-exception -> envelope handler (app.errors._handle_unexpected_error).

Covers the gap reported by the user: an unrelated bug or a DB hiccup mid-request
previously fell through to FastAPI's bare default ``{"detail": "Internal Server
Error"}`` with no request id to correlate against server logs. These tests pin
that a request id now always comes back, and - critically - that this new
catch-all does not swallow the existing ``HTTPException``/``ApiError`` paths
that 213 call sites across the routers still rely on.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.errors import ApiError, install_error_handlers


def _mint_request_id_middleware():
    """Minimal stand-in for RequestContextMiddleware: sets request.state.request_id."""

    class _Mint(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.request_id = "req-fixed-test-id"
            return await call_next(request)

    return _Mint


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(_mint_request_id_middleware())
    install_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise ValueError("something exploded deep in a service call")

    @app.get("/http-exc")
    async def http_exc():
        raise HTTPException(status_code=404, detail="not found here")

    @app.get("/api-error")
    async def api_error():
        raise ApiError(429, "rate_limited", "Too many requests")

    return app


class TestUnhandledExceptionEnvelope:
    def test_unhandled_exception_returns_500_envelope_with_request_id(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/boom")

        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "internal_error"
        # Client message must stay generic - the real cause is server-side only.
        assert "something exploded" not in body["error"]["message"]
        assert body["error"]["details"]["request_id"] == "req-fixed-test-id"
        assert resp.headers["X-Request-ID"] == "req-fixed-test-id"

    def test_unhandled_exception_is_logged_with_cause_and_request_id(self, caplog):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        with caplog.at_level(logging.ERROR, logger="app.errors"):
            client.get("/boom")

        joined = "\n".join(r.message for r in caplog.records)
        assert "req-fixed-test-id" in joined
        assert "ValueError" in joined
        assert "something exploded deep in a service call" in joined

    def test_missing_request_id_omits_it_rather_than_crashing(self):
        # No request-id-minting middleware installed - the handler must not
        # assume request.state.request_id always exists.
        app = FastAPI()
        install_error_handlers(app)

        @app.get("/boom")
        async def boom():
            raise RuntimeError("boom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")

        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "internal_error"
        assert "X-Request-ID" not in resp.headers


class TestExistingErrorPathsUnaffected:
    """The new catch-all must not intercept errors that already have a home."""

    def test_http_exception_still_renders_legacy_detail_shape(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/http-exc")

        assert resp.status_code == 404
        # Legacy shape per errors.py's own docstring: {"detail": ...}, NOT the
        # ADR-7 envelope. If the generic handler ever shadowed HTTPException,
        # this would flip to {"error": {...}} and every one of the 213
        # `raise HTTPException(...)` call sites in the routers would regress.
        assert resp.json() == {"detail": "not found here"}

    def test_api_error_still_renders_adr7_envelope_unchanged(self):
        app = _build_app()
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get("/api-error")

        assert resp.status_code == 429
        body = resp.json()
        assert body["error"]["code"] == "rate_limited"
        assert body["error"]["message"] == "Too many requests"
