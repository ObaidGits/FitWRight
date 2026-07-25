"""Focused observability tests for LLM completion accounting."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

import app.llm as llm

pytestmark = pytest.mark.unit


class _Names(BaseModel):
    names: list[str]


def _response(content: str | None, *, tokens: int, router_retries: int = 0):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(total_tokens=tokens),
        _hidden_params={
            "additional_headers": {
                "x-litellm-attempted-retries": str(router_retries),
                "authorization": "secret-must-not-be-recorded",
            }
        },
    )


def _configure_json(monkeypatch, router):
    config = SimpleNamespace(provider="openai", reasoning_effort=None)
    monkeypatch.setattr(llm, "get_router", lambda _config=None: (router, config))
    monkeypatch.setattr(llm, "get_model_name", lambda config: "openai/test")
    monkeypatch.setattr(llm, "_supports_json_mode", lambda model: False)
    monkeypatch.setattr(llm.litellm, "supports_response_schema", lambda model: False)
    monkeypatch.setattr(llm, "_get_retry_temperature", lambda model, attempt: None)


@pytest.mark.parametrize(
    ("bad_content", "good_content", "schema_type", "response_model", "expected"),
    [
        ('{"answer": }', '{"answer": "ok"}', "custom", None, {"answer": "ok"}),
        (None, '{"answer": "ok"}', "custom", None, {"answer": "ok"}),
        (
            '{"analysis_summary": "incomplete"}',
            '{"items_to_enrich": [], "questions": [], "analysis_summary": "ok"}',
            "enrichment",
            None,
            {"items_to_enrich": [], "questions": [], "analysis_summary": "ok"},
        ),
        ('{"names": "Ada"}', '{"names": ["Ada"]}', "custom", _Names, {"names": ["Ada"]}),
    ],
    ids=["malformed", "empty", "truncated", "schema-invalid"],
)
async def test_complete_json_records_content_failure_then_validated_success(
    monkeypatch,
    bad_content,
    good_content,
    schema_type,
    response_model,
    expected,
):
    """A provider 2xx is not success until JSON and schema checks pass."""
    router = SimpleNamespace(
        acompletion=AsyncMock(
            side_effect=[
                _response(bad_content, tokens=11, router_retries=2),
                _response(good_content, tokens=13, router_retries=1),
            ]
        )
    )
    _configure_json(monkeypatch, router)
    record = MagicMock()
    monkeypatch.setattr(llm, "_record_ai_call", record)

    result = await llm.complete_json(
        "return JSON",
        retries=1,
        schema_type=schema_type,
        response_model=response_model,
    )

    assert result == expected
    assert record.call_count == 2
    first, second = record.call_args_list
    assert first.args == ("openai",)
    assert first.kwargs["ok"] is False
    assert first.kwargs["tokens"] == 11
    assert first.kwargs["retried"] == 2  # observed Router retries only
    assert second.kwargs["ok"] is True
    assert second.kwargs["tokens"] == 13
    # One observed Router retry + exactly one app-level content retry.
    assert second.kwargs["retried"] == 2
    # The recorder receives only the existing allowlisted aggregate fields.
    assert set(first.kwargs) == {
        "ok", "timed_out", "retried", "tokens", "latency_ms"
    }


async def test_complete_json_cancels_inflight_provider_without_false_failure(monkeypatch):
    """Distributed request cancellation closes provider work mid-await."""
    started = asyncio.Event()
    stopped = asyncio.Event()
    cancel_requested = asyncio.Event()

    async def blocked_completion(**_kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    router = SimpleNamespace(acompletion=AsyncMock(side_effect=blocked_completion))
    _configure_json(monkeypatch, router)
    record = MagicMock()
    monkeypatch.setattr(llm, "_record_ai_call", record)

    async def cancel_check() -> bool:
        return cancel_requested.is_set()

    task = asyncio.create_task(
        llm.complete_json("return JSON", cancel_check=cancel_check)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    cancel_requested.set()

    with pytest.raises(llm.LLMRequestCancelled):
        await asyncio.wait_for(task, timeout=2)

    assert stopped.is_set()
    record.assert_not_called()


async def test_complete_records_exhausted_router_retries_from_exception(monkeypatch):
    """Actual exception retry metadata is counted; configured limits are not guessed."""
    error = RuntimeError("provider unavailable")
    error.num_retries = 2
    error.max_retries = 3
    router = SimpleNamespace(acompletion=AsyncMock(side_effect=error))
    config = SimpleNamespace(provider="openai", reasoning_effort=None)
    monkeypatch.setattr(llm, "get_router", lambda _config=None: (router, config))
    monkeypatch.setattr(llm, "get_model_name", lambda config: "openai/test")
    monkeypatch.setattr(llm, "_supports_temperature", lambda *args: False)
    record = MagicMock()
    monkeypatch.setattr(llm, "_record_ai_call", record)

    with pytest.raises(ValueError, match="LLM completion failed"):
        await llm.complete("hello")

    assert record.call_count == 1
    assert record.call_args.kwargs["ok"] is False
    assert record.call_args.kwargs["retried"] == 2


async def test_retry_count_ignores_unpaired_or_sensitive_metadata():
    """No retry count is inferred from limits, text, prompts, or arbitrary metadata."""
    value = SimpleNamespace(
        num_retries=3,
        _hidden_params={
            "additional_headers": {"authorization": "secret"},
            "prompt": "private prompt",
        },
    )
    assert llm._router_retry_count(value) == 0


class _AsyncStream:
    def __init__(self):
        self.closed = False
        self._chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="unused"))],
                usage=None,
            )
        ]
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self):
        self.closed = True


async def test_cancelled_stream_is_neither_success_nor_failure(monkeypatch):
    """The current metric model has no cancellation bucket, so omit the call."""
    stream = _AsyncStream()
    router = SimpleNamespace(acompletion=AsyncMock(return_value=stream))
    config = SimpleNamespace(provider="openai", reasoning_effort=None)
    monkeypatch.setattr(llm, "get_router", lambda _config=None: (router, config))
    monkeypatch.setattr(llm, "get_model_name", lambda config: "openai/test")
    monkeypatch.setattr(llm, "_supports_temperature", lambda *args: False)
    record = MagicMock()
    monkeypatch.setattr(llm, "_record_ai_call", record)

    async def cancelled():
        return True

    result = llm.StreamResult()
    pieces = [
        piece
        async for piece in llm.stream_complete(
            "hello", result, cancel_check=cancelled
        )
    ]

    assert pieces == []
    assert result.cancelled is True
    assert stream.closed is True
    record.assert_not_called()
