"""The request-scoped usage meter, and who is deemed to be paying.

Two things are tested here that no endpoint test would catch:

* The meter must accumulate across tasks. A streamed generation finishes its
  provider call inside an async generator, and several endpoints fan out with
  ``asyncio.gather``. If the tally were a plain int in a ContextVar, each child
  would increment a private copy and the request would bill zero.
* ``user_has_own_key`` must ignore the environment key. In a hosted deployment
  ``LLM_API_KEY`` is the OPERATOR's key, so counting it would mark every user as
  self-funded and nothing would ever be charged - a mistake that is invisible in
  tests, where the env key is often the only key present, and surfaces as a bill.
"""

from __future__ import annotations

import asyncio

import pytest

from app.ai_metered import user_has_own_key
from app.ai_usage_meter import current_usage, note_call, start_metering, stop_metering


class TestUsageMeter:
    def test_records_a_single_call(self):
        usage, token = start_metering()
        try:
            note_call(total_tokens=1200, prompt_tokens=1000, completion_tokens=200)
        finally:
            stop_metering(token)
        assert usage.total_tokens == 1200
        assert usage.calls == 1

    def test_accumulates_several_calls_rather_than_overwriting(self):
        """A multi-call endpoint (improve, tailor, the wizard) must bill for all of
        its calls. Overwriting would under-bill every one but the last."""
        usage, token = start_metering()
        try:
            note_call(total_tokens=1000)
            note_call(total_tokens=2500)
            note_call(total_tokens=500)
        finally:
            stop_metering(token)
        assert usage.total_tokens == 4000
        assert usage.calls == 3

    def test_is_a_no_op_when_nothing_is_metering(self):
        """Health probes and background jobs call the LLM too. They must not pay the
        cost of accounting, and must not blow up for lack of a tally."""
        assert current_usage() is None
        note_call(total_tokens=999)  # must not raise
        assert current_usage() is None

    @pytest.mark.asyncio
    async def test_a_child_task_reports_into_the_same_tally(self):
        """THE reason the tally is a mutable object rather than an int.

        A ContextVar is copied into each child task, so a child incrementing an int
        would update its own copy and the request would see zero. Streaming does
        exactly this, and it is the highest-token path in the product.
        """
        usage, token = start_metering()
        try:

            async def child(n: int):
                note_call(total_tokens=n)

            await asyncio.gather(child(100), child(200), child(300))
        finally:
            stop_metering(token)

        assert usage.total_tokens == 600, "child tasks lost their usage"
        assert usage.calls == 3

    @pytest.mark.asyncio
    async def test_an_async_generator_reports_into_the_same_tally(self):
        """The streaming shape specifically: the provider call completes while the
        generator is being consumed, after the handler has already returned."""
        usage, token = start_metering()

        async def stream():
            yield "chunk"
            note_call(total_tokens=4321)  # settled at the end of the stream

        try:
            async for _ in stream():
                pass
        finally:
            stop_metering(token)

        assert usage.total_tokens == 4321

    def test_any_estimated_call_marks_the_whole_request_estimated(self):
        """Pessimistic on purpose: a request that is half measured and half guessed
        is not a measurement, and labelling it as one corrupts reconciliation."""
        usage, token = start_metering()
        try:
            note_call(total_tokens=1000, estimated=False)
            note_call(total_tokens=1000, estimated=True)
        finally:
            stop_metering(token)
        assert usage.estimated is True
        assert usage.estimated_calls == 1

    def test_provenance_records_the_channel_that_served_last(self):
        """After a failover the useful answer is which channel actually served."""
        usage, token = start_metering()
        try:
            note_call(total_tokens=10, channel_id="ch-a", model="m-a")
            note_call(total_tokens=10, channel_id="ch-b", model="m-b")
        finally:
            stop_metering(token)
        assert usage.channel_id == "ch-b"
        assert usage.model == "m-b"

    def test_negative_and_none_tokens_cannot_corrupt_the_tally(self):
        usage, token = start_metering()
        try:
            note_call(total_tokens=-500)
            note_call(total_tokens=None)  # type: ignore[arg-type]
            note_call(total_tokens=100)
        finally:
            stop_metering(token)
        assert usage.total_tokens == 100


class TestWhoIsPaying:
    def test_the_operator_env_key_does_not_make_a_user_self_funded(self, monkeypatch):
        """The costly mistake this guards.

        Key RESOLUTION falls back to the env default by design. Billing must not: in
        a hosted deployment that key is the operator's, so honouring it here would
        silently make every request free.
        """
        from app.config import settings

        monkeypatch.setattr(settings, "llm_api_key", "sk-operator-env-key")
        monkeypatch.setattr(
            "app.config.load_config_file",
            lambda _uid=None: {"provider": "openai", "api_keys": {}},
        )
        assert user_has_own_key("user-1") is False

    def test_a_user_with_their_own_provider_key_is_self_funded(self, monkeypatch):
        monkeypatch.setattr(
            "app.config.load_config_file",
            lambda _uid=None: {"provider": "openai", "api_keys": {"openai": "sk-theirs"}},
        )
        assert user_has_own_key("user-1") is True

    def test_a_self_hosted_endpoint_counts_as_self_funded(self, monkeypatch):
        """Their own Ollama server is their own compute. It costs the operator
        nothing, so it must bypass billing exactly like a key does."""
        monkeypatch.setattr(
            "app.config.load_config_file",
            lambda _uid=None: {
                "provider": "ollama",
                "api_keys": {},
                "api_base": "http://localhost:11434",
            },
        )
        assert user_has_own_key("user-1") is True

    def test_a_config_read_failure_fails_closed(self, monkeypatch):
        """Failing OPEN would hand out a free pass on any transient error."""

        def boom(_uid=None):
            raise RuntimeError("config store unavailable")

        monkeypatch.setattr("app.config.load_config_file", boom)
        assert user_has_own_key("user-1") is False
