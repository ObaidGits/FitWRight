"""Which router serves a call - the decision that spends somebody's money.

The precedence here is not arbitrary and each rule guards a specific mistake:

* An explicit config must win, or the admin's "test this channel" button would test
  something other than the channel named.
* A user on their own key must NOT be routed through an operator channel: they were
  free, and routing them would make them expensive.
* A feature with a healthy channel must use it, or the whole channels feature is
  decoration - which is precisely the state this file was written to end.
* Channels configured but all down, with no fallback credential, must raise a
  DISTINCT outage error rather than a generic provider failure.
"""

from __future__ import annotations

import pytest

from app.ai_channels import ChannelCandidate
from app.ai_routing import ChannelRoute
from app.ai_usage_meter import start_metering, stop_metering
from app.llm import ChannelsUnavailable, LLMConfig, _resolve_router


def _route(*, channel_id: str = "ch-1", provider: str = "openai", model: str = "gpt-x"):
    cand = ChannelCandidate(
        id=channel_id,
        name="Primary",
        provider=provider,
        model=model,
        api_base=None,
        priority=100,
    )
    return ChannelRoute([cand], [{"model": f"{provider}/{model}", "api_key": "sk-op", "api_base": None}])


@pytest.fixture
def no_channels(monkeypatch):
    async def _none(_feature, **_kw):
        return None

    monkeypatch.setattr("app.llm.resolve_channel_route", _none)


@pytest.fixture
def one_channel(monkeypatch):
    async def _one(_feature, **_kw):
        return _route()

    monkeypatch.setattr("app.llm.resolve_channel_route", _one)


@pytest.mark.asyncio
class TestRouterPrecedence:
    async def test_an_explicit_config_is_never_redirected(self, one_channel):
        """The admin channel test and the health probe name a provider deliberately."""
        pinned = LLMConfig(provider="anthropic", model="claude-x", api_key="sk-user")
        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            _router, cfg, route = await _resolve_router(pinned)
        finally:
            stop_metering(token)

        assert route is None, "an explicitly pinned config was redirected to a channel"
        assert cfg.provider == "anthropic"

    async def test_a_self_funded_user_stays_on_their_own_key(self, one_channel):
        """Routing them through an operator channel would turn a user who cost
        nothing into one who costs money."""
        usage, token = start_metering(
            feature="resume_tailor", user_id="u1", has_own_key=True
        )
        try:
            _router, _cfg, route = await _resolve_router(None)
        finally:
            stop_metering(token)

        assert route is None

    async def test_a_feature_with_a_healthy_channel_uses_it(self, one_channel):
        """The actual point: without this, the channels admin UI is decoration."""
        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            _router, cfg, route = await _resolve_router(None)
        finally:
            stop_metering(token)

        assert route is not None
        assert route.primary_channel_id == "ch-1"
        # The config presented downstream describes the CHANNEL, so token clamping
        # and reasoning-effort support are decided for the model actually called.
        assert cfg.model == "gpt-x"
        assert cfg.api_key == "", "the operator credential leaked into the config object"

    async def test_a_call_with_no_feature_context_uses_the_normal_path(self, one_channel):
        """Background jobs and health probes have no billing feature. They must not
        silently consume operator channels."""
        _router, _cfg, route = await _resolve_router(None)
        assert route is None

    async def test_falls_back_when_no_channel_is_usable(self, no_channels, monkeypatch):
        monkeypatch.setattr(
            "app.llm.get_llm_config",
            lambda *_a, **_k: LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-env"),
        )
        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            _router, cfg, route = await _resolve_router(None)
        finally:
            stop_metering(token)

        assert route is None
        assert cfg.api_key == "sk-env", "did not fall back to the working credential"


@pytest.mark.asyncio
class TestOutageIsDistinct:
    async def test_all_channels_down_with_no_fallback_raises_a_distinct_error(
        self, no_channels, monkeypatch
    ):
        """Our outage must not render as "check your API configuration".

        This codebase has already shipped a bug where an AI credential problem told
        users they were offline; the three causes must stay three messages.
        """
        monkeypatch.setattr(
            "app.llm.get_llm_config",
            lambda *_a, **_k: LLMConfig(provider="openai", model="gpt-4o-mini", api_key=""),
        )

        async def _configured():
            return True

        monkeypatch.setattr("app.llm.channels_are_configured", _configured)

        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            with pytest.raises(ChannelsUnavailable) as caught:
                await _resolve_router(None)
        finally:
            stop_metering(token)

        assert caught.value.status_code == 503
        # It must point at the free way to keep working, not just apologise.
        assert "own provider key" in str(caught.value.message).lower()

    async def test_no_channels_configured_is_not_reported_as_an_outage(
        self, no_channels, monkeypatch
    ):
        """A deployment that simply does not offer hosted AI is not broken. Telling
        the user to "try again shortly" would be a lie they act on forever."""
        monkeypatch.setattr(
            "app.llm.get_llm_config",
            lambda *_a, **_k: LLMConfig(provider="openai", model="gpt-4o-mini", api_key=""),
        )

        async def _not_configured():
            return False

        monkeypatch.setattr("app.llm.channels_are_configured", _not_configured)

        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            _router, _cfg, route = await _resolve_router(None)
        finally:
            stop_metering(token)
        assert route is None


@pytest.mark.asyncio
class TestChannelRouterCaching:
    async def test_the_same_channel_set_reuses_its_router(self, one_channel):
        """Building a Router per request would add latency to every generation."""
        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            first, _c1, _r1 = await _resolve_router(None)
            second, _c2, _r2 = await _resolve_router(None)
        finally:
            stop_metering(token)
        assert first is second

    async def test_a_different_channel_set_gets_a_different_router(self, monkeypatch):
        """A channel edit or a health change must not be served by a stale router."""
        seq = [_route(channel_id="ch-1", model="gpt-x"), _route(channel_id="ch-2", model="gpt-y")]

        async def _next(_feature, **_kw):
            return seq.pop(0)

        monkeypatch.setattr("app.llm.resolve_channel_route", _next)

        usage, token = start_metering(feature="resume_tailor", user_id="u1")
        try:
            first, _c1, _r1 = await _resolve_router(None)
            second, _c2, _r2 = await _resolve_router(None)
        finally:
            stop_metering(token)
        assert first is not second
