"""Regression guard for the resume-upload CSRF contract.

The frontend uploads resumes with a *raw* fetch (FormData can't go through the
JSON apiFetch helper). That raw request must still carry the double-submit
``X-CSRF-Token`` header, or the backend rejects the mutation with ``csrf_failed``
in hosted mode. A bug where the header was omitted made every upload fail with
"csrf_failed" for authenticated (hosted) users. This test pins the backend
contract the frontend fix relies on: upload is rejected WITHOUT the header and
accepted (reaches the handler, not a 403) WITH it.
"""
import io

import pytest

from app.config import settings as app_settings
from tests.integration.test_authz_matrix import _client, hosted  # noqa: F401
from tests.integration.test_auth_api import _login, _seed_active_user

# asyncio_mode = "auto" (pyproject) runs these async tests without an explicit marker.

_TINY_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _file_part():
    return {"file": ("resume.pdf", io.BytesIO(_TINY_PDF), "application/pdf")}


class TestUploadCsrf:
    async def test_upload_without_csrf_header_is_rejected(self, auth_env, hosted):
        await _seed_active_user(auth_env, "csrf-upload@example.com")
        async with _client() as client:
            await _login(client, "csrf-upload@example.com")
            resp = await client.post("/api/v1/resumes/upload", files=_file_part())
        assert resp.status_code == 403
        # Middleware CSRF rejections use the minimal {"detail": <code>} shape.
        assert resp.json()["detail"] == "csrf_failed"

    async def test_upload_with_csrf_header_passes_csrf_gate(self, auth_env, hosted):
        await _seed_active_user(auth_env, "csrf-upload2@example.com")
        async with _client() as client:
            await _login(client, "csrf-upload2@example.com")
            csrf = client.cookies.get("csrf")
            assert csrf, "login must set the JS-readable per-session csrf cookie"
            resp = await client.post(
                "/api/v1/resumes/upload",
                files=_file_part(),
                headers={"X-CSRF-Token": csrf},
            )
        # The CSRF gate must be passed: anything but 403/csrf_failed proves it.
        # (The tiny stub may fail document parsing with 422 — that's fine; it
        # means the request got past auth+CSRF into the handler.)
        assert resp.status_code != 403
        if resp.status_code >= 400:
            assert resp.json().get("detail") != "csrf_failed"
