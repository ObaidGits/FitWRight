"""P4 Resilience — streaming AI SSE endpoint integration tests.

The LLM layer is stubbed (no network/provider) so we deterministically exercise
the SSE framing, flag gate, capability probe, per-user concurrency cap, cancel
signalling, and cost-accounting `done` event.
"""

import json

import pytest
from httpx import ASGITransport, AsyncClient

import app.routers.resumes as resumes_mod
from app.llm import StreamResult
from app.main import app
from app.resilience.registry import StreamRegistry
from app.auth.kvstore.local import LocalKVStore


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def enable_streaming(monkeypatch):
    monkeypatch.setattr(resumes_mod.settings, "streaming_ai_enabled", True, raising=False)
    monkeypatch.setattr(resumes_mod, "provider_supports_streaming", lambda *a, **k: True)


@pytest.fixture
def fresh_registry(monkeypatch):
    """Give the streaming path an isolated in-process registry per test."""
    reg = StreamRegistry(LocalKVStore())
    monkeypatch.setattr(resumes_mod, "get_stream_registry", lambda: reg)
    return reg


@pytest.fixture
def tailored_context(monkeypatch):
    """Stub the AI generation context loader so no DB tailored-resume is needed."""
    async def _ctx(user_id, resume_id):
        return ({"summary": "x"}, "job description", "en")

    monkeypatch.setattr(resumes_mod, "_load_ai_generation_context", _ctx)


def _stub_stream(pieces, *, cancelled=False, total_tokens=7):
    async def _fake_stream(prompt, result: StreamResult, **kwargs):
        cancel_check = kwargs.get("cancel_check")
        for p in pieces:
            if cancel_check is not None and await cancel_check():
                result.cancelled = True
                break
            result.text += p
            yield p
        else:
            result.cancelled = cancelled
        result.usage.total_tokens = total_tokens
        result.usage.completion_tokens = total_tokens

    return _fake_stream


def _parse_sse(body: str):
    """Parse an SSE body into a list of (event, data-dict) tuples."""
    events = []
    for block in body.strip().split("\n\n"):
        if not block.strip():
            continue
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        events.append((event, data))
    return events


class TestStreamingEndpoint:
    async def test_streams_tokens_then_done_with_usage(
        self, client, enable_streaming, fresh_registry, tailored_context, monkeypatch
    ):
        monkeypatch.setattr(
            resumes_mod, "stream_complete", _stub_stream(["Hello ", "world"])
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/res-1/cover-letter/stream?request_id=req-12345678"
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]
        assert "token" in kinds
        assert kinds[-1] == "done"
        tokens = [d["text"] for e, d in events if e == "token"]
        assert "".join(tokens) == "Hello world"
        done = [d for e, d in events if e == "done"][0]
        assert done["cancelled"] is False
        assert done["text"] == "Hello world"
        assert done["usage"]["total_tokens"] == 7

    async def test_flag_off_returns_409_for_fallback(
        self, client, fresh_registry, tailored_context, monkeypatch
    ):
        monkeypatch.setattr(
            resumes_mod.settings, "streaming_ai_enabled", False, raising=False
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/res-1/cover-letter/stream?request_id=req-12345678"
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "streaming_disabled"

    async def test_capability_probe_negative_returns_409(
        self, client, monkeypatch, fresh_registry, tailored_context
    ):
        monkeypatch.setattr(
            resumes_mod.settings, "streaming_ai_enabled", True, raising=False
        )
        monkeypatch.setattr(resumes_mod, "provider_supports_streaming", lambda *a, **k: False)
        async with client:
            resp = await client.post(
                "/api/v1/resumes/res-1/cover-letter/stream?request_id=req-12345678"
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "streaming_unsupported"

    async def test_unknown_kind_404(self, client, enable_streaming, fresh_registry):
        async with client:
            resp = await client.post(
                "/api/v1/resumes/res-1/bogus/stream?request_id=req-12345678"
            )
        assert resp.status_code == 404

    async def test_concurrency_cap_returns_429(
        self, client, enable_streaming, fresh_registry, tailored_context, monkeypatch
    ):
        # Pre-fill the registry to the cap so the endpoint rejects the start.
        for i in range(resumes_mod.settings.stream_max_concurrent_per_user):
            await fresh_registry.try_register(
                # single-user mode: effective user is the bootstrap owner; the
                # cap is per-user, so pre-fill under the same id the endpoint uses.
                (await _owner_id()),
                f"pre-{i}",
                max_concurrent=resumes_mod.settings.stream_max_concurrent_per_user,
                heartbeat_ttl=60,
            )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/res-1/cover-letter/stream?request_id=req-over-cap"
            )
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "stream_limit_reached"

    async def test_cancel_endpoint_is_idempotent(self, client):
        async with client:
            resp = await client.post("/api/v1/resumes/stream/req-unknown/cancel")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True


async def _owner_id() -> str:
    from app.auth.owner import resolve_owner_id_sync

    return resolve_owner_id_sync()


class TestImprovePreviewStream:
    """POST /resumes/improve/preview/stream — stage-progress SSE for tailoring."""

    @pytest.fixture
    def stub_db(self, monkeypatch):
        from unittest.mock import AsyncMock

        db = AsyncMock()
        db.get_resume.return_value = {
            "resume_id": "res-1",
            "processed_data": {"summary": "x"},
            "content": "# Resume",
        }
        db.get_job.return_value = {"job_id": "job-1", "content": "Senior Python role."}
        monkeypatch.setattr(resumes_mod, "db", db)
        return db

    @pytest.fixture
    def stub_flow(self, monkeypatch):
        """Replace the pipeline with a fake that emits every real stage boundary."""
        from unittest.mock import MagicMock

        async def _fake_flow(*, emit_stage, cancel_check, **kwargs):
            for stage in ("keywords", "plan", "rewrite", "refine", "score"):
                if await cancel_check():
                    raise resumes_mod._TailorStreamCancelled()
                await emit_stage(stage, "start")
                await emit_stage(stage, "done")
            result = MagicMock()
            result.model_dump.return_value = {
                "request_id": "rid-1",
                "data": {"job_id": "job-1", "resume_id": None},
            }
            return result

        monkeypatch.setattr(resumes_mod, "_improve_preview_flow", _fake_flow)

    async def test_streams_all_stages_then_done(
        self, client, enable_streaming, fresh_registry, stub_db, stub_flow
    ):
        async with client:
            resp = await client.post(
                "/api/v1/resumes/improve/preview/stream?request_id=tailor-12345678",
                json={"resume_id": "res-1", "job_id": "job-1"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]
        assert kinds[-1] == "done"
        # Every real stage boundary is surfaced, in order.
        stages = [(d["stage"], d["status"]) for e, d in events if e == "stage"]
        assert stages == [
            ("keywords", "start"), ("keywords", "done"),
            ("plan", "start"), ("plan", "done"),
            ("rewrite", "start"), ("rewrite", "done"),
            ("refine", "start"), ("refine", "done"),
            ("score", "start"), ("score", "done"),
        ]
        done = [d for e, d in events if e == "done"][0]
        assert done["result"]["data"]["job_id"] == "job-1"

    async def test_flag_off_returns_409_for_fallback(
        self, client, fresh_registry, stub_db, monkeypatch
    ):
        monkeypatch.setattr(
            resumes_mod.settings, "streaming_ai_enabled", False, raising=False
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/improve/preview/stream?request_id=tailor-12345678",
                json={"resume_id": "res-1", "job_id": "job-1"},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "streaming_disabled"

    async def test_short_request_id_422(
        self, client, enable_streaming, fresh_registry, stub_db
    ):
        async with client:
            resp = await client.post(
                "/api/v1/resumes/improve/preview/stream?request_id=short",
                json={"resume_id": "res-1", "job_id": "job-1"},
            )
        assert resp.status_code == 422

    async def test_missing_resume_404(
        self, client, enable_streaming, fresh_registry, monkeypatch
    ):
        from unittest.mock import AsyncMock

        db = AsyncMock()
        db.get_resume.return_value = None
        monkeypatch.setattr(resumes_mod, "db", db)
        async with client:
            resp = await client.post(
                "/api/v1/resumes/improve/preview/stream?request_id=tailor-12345678",
                json={"resume_id": "nope", "job_id": "job-1"},
            )
        assert resp.status_code == 404

    async def test_precancelled_stream_emits_cancelled_done(
        self, client, enable_streaming, fresh_registry, stub_db, stub_flow
    ):
        # Pre-cancel so the flow's first cancel_check aborts before any LLM work.
        owner = await _owner_id()
        await fresh_registry.request_cancel(owner, "tailor-cancelled99")
        async with client:
            resp = await client.post(
                "/api/v1/resumes/improve/preview/stream?request_id=tailor-cancelled99",
                json={"resume_id": "res-1", "job_id": "job-1"},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        done = [d for e, d in events if e == "done"][0]
        assert done.get("cancelled") is True


class TestStreamingUpload:
    """POST /api/v1/resumes/upload/stream — honest per-stage parse SSE."""

    async def test_streams_parse_stages_then_done(
        self, client, enable_streaming, isolated_db, monkeypatch
    ):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(
            resumes_mod, "parse_document", AsyncMock(return_value="# Jane Doe\nEngineer")
        )
        monkeypatch.setattr(
            resumes_mod,
            "parse_resume_to_json",
            AsyncMock(return_value={"personalInfo": {"name": "Jane Doe"}}),
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/upload/stream",
                files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        stages = [(d["stage"], d["status"]) for e, d in events if e == "stage"]
        # Honest boundaries: received → extracting → structuring, each completing.
        assert ("received", "done") in stages
        assert ("extracting", "active") in stages
        assert ("extracting", "done") in stages
        assert ("structuring", "active") in stages
        assert ("structuring", "done") in stages
        done = [d for e, d in events if e == "done"][0]
        assert done["result"]["processing_status"] == "ready"
        assert done["result"]["resume_id"]

    async def test_flag_off_returns_409_for_fallback(self, client, monkeypatch):
        monkeypatch.setattr(
            resumes_mod.settings, "streaming_ai_enabled", False, raising=False
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/upload/stream",
                files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "streaming_disabled"

    async def test_slow_parse_emits_heartbeats(
        self, client, enable_streaming, isolated_db, monkeypatch
    ):
        """A slow parse/LLM stage MUST emit SSE heartbeats so the connection
        never idles past a platform router timeout (Heroku H15/H28). This is the
        regression guard for the intermittent Heroku "Application Error" on
        upload: without heartbeats a 30-90s LLM structuring call sends no bytes
        and the router severs the stream mid-parse."""
        import asyncio as _asyncio
        from unittest.mock import AsyncMock

        # Tight heartbeat cadence so the test is fast; parse sleeps past it.
        monkeypatch.setattr(
            resumes_mod.settings, "stream_heartbeat_seconds", 0.05, raising=False
        )

        async def _slow_parse(*_a, **_k):
            await _asyncio.sleep(0.2)
            return "# Jane Doe\nEngineer"

        monkeypatch.setattr(resumes_mod, "parse_document", _slow_parse)
        monkeypatch.setattr(
            resumes_mod,
            "parse_resume_to_json",
            AsyncMock(return_value={"personalInfo": {"name": "Jane Doe"}}),
        )
        async with client:
            resp = await client.post(
                "/api/v1/resumes/upload/stream",
                files={"file": ("resume.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        heartbeats = [d for e, d in events if e == "heartbeat"]
        assert heartbeats, "expected at least one heartbeat during the slow parse"
        assert any(h.get("stage") == "extracting" for h in heartbeats)
        # The stream still completes normally after the slow stage.
        done = [d for e, d in events if e == "done"]
        assert done and done[0]["result"]["processing_status"] == "ready"

    async def test_empty_text_emits_error_event(
        self, client, enable_streaming, isolated_db, monkeypatch
    ):
        from unittest.mock import AsyncMock

        monkeypatch.setattr(resumes_mod, "parse_document", AsyncMock(return_value="   "))
        async with client:
            resp = await client.post(
                "/api/v1/resumes/upload/stream",
                files={"file": ("scanned.pdf", b"%PDF-1.4 img", "application/pdf")},
            )
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        errors = [d for e, d in events if e == "error"]
        assert errors and errors[0]["code"] == "empty_text"

    async def test_invalid_type_is_json_400_before_stream(self, client, enable_streaming):
        async with client:
            resp = await client.post(
                "/api/v1/resumes/upload/stream",
                files={"file": ("resume.txt", b"hello", "text/plain")},
            )
        assert resp.status_code == 400
