"""Channel route resolution: the seam between configured channels and LiteLLM.

The property under test throughout: returning ``None`` must mean "fall back to the
per-user key path", and must never be confused with "AI is unavailable". Collapsing
those is how an out-of-channels state ends up rendering as a bogus "you are offline"
- a bug this codebase has already shipped once.
"""

import pytest

from app.ai_routing import record_channel_outcome, resolve_channel_route
from app.llm import build_channel_router


def _channel(cid, *, provider="openai", model="gpt-5-nano", verdict="reliable", priority=100):
    return {
        "id": cid,
        "name": cid,
        "provider": provider,
        "model": model,
        "api_base": None,
        "priority": priority,
        "state": "active",
        "structured_verdict": verdict,
        "created_at": "2026-01-01",
    }


@pytest.mark.asyncio
class TestFlagGating:
    async def test_returns_none_when_the_flag_is_off(self, monkeypatch):
        """The whole feature ships dark: flag off means the existing per-user-key
        path is used and nothing else is even queried."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", False)
        assert await resolve_channel_route("cover_letter") is None


@pytest.mark.asyncio
class TestResolution:
    async def test_returns_none_when_no_channels_are_configured(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr("app.database.db.list_ai_channels", _async_returning([]))
        monkeypatch.setattr("app.database.db.get_ai_channel_health", _async_returning({}))
        assert await resolve_channel_route("cover_letter") is None

    async def test_returns_none_when_the_lookup_itself_fails(self, monkeypatch):
        """A database blip must degrade to the per-user path, not break AI."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)

        async def boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.database.db.list_ai_channels", boom)
        assert await resolve_channel_route("cover_letter") is None

    async def test_skips_a_channel_with_no_readable_credential(self, monkeypatch):
        """A key can become unreadable after an encryption-secret change - the exact
        failure that already bit this app. Such a channel must be skipped, not added
        and left to fail every request."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(
            "app.database.db.list_ai_channels",
            _async_returning([_channel("a", priority=1), _channel("b", priority=2)]),
        )
        monkeypatch.setattr("app.database.db.get_ai_channel_health", _async_returning({}))
        # Only "b" has a usable key.
        monkeypatch.setattr("app.ai_routing._load_channel_keys", _keys({"b": "sk-b"}))

        route = await resolve_channel_route("cover_letter")
        assert route is not None
        assert route.primary_channel_id == "b"
        assert len(route.deployments) == 1

    async def test_local_providers_do_not_require_a_key(self, monkeypatch):
        """Ollama and OpenAI-compatible servers usually have no auth - requiring a
        key would make a working local setup unusable."""
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(
            "app.database.db.list_ai_channels",
            _async_returning([_channel("local", provider="ollama", model="gemma3:4b")]),
        )
        monkeypatch.setattr("app.database.db.get_ai_channel_health", _async_returning({}))
        monkeypatch.setattr("app.ai_routing._load_channel_keys", _keys({}))

        route = await resolve_channel_route("cover_letter")
        assert route is not None
        assert route.primary_channel_id == "local"

    async def test_returns_none_when_every_channel_is_unusable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(
            "app.database.db.list_ai_channels", _async_returning([_channel("a")])
        )
        monkeypatch.setattr("app.database.db.get_ai_channel_health", _async_returning({}))
        monkeypatch.setattr("app.ai_routing._load_channel_keys", _keys({}))
        assert await resolve_channel_route("cover_letter") is None

    async def test_structured_gating_applies_through_the_resolver(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "ai_credits_enabled", True)
        monkeypatch.setattr(
            "app.database.db.list_ai_channels",
            _async_returning([_channel("bad", verdict="unsupported")]),
        )
        monkeypatch.setattr("app.database.db.get_ai_channel_health", _async_returning({}))
        monkeypatch.setattr("app.ai_routing._load_channel_keys", _keys({"bad": "k"}))

        # Free-text is fine...
        assert await resolve_channel_route("cover_letter") is not None
        # ...but a JSON feature must not use it.
        assert await resolve_channel_route("resume_tailor") is None


@pytest.mark.asyncio
class TestHealthRecording:
    async def test_never_raises_on_a_none_channel(self):
        await record_channel_outcome(None, ok=True)

    async def test_swallows_a_repository_failure(self, monkeypatch):
        """Health recording must not fail a request that already succeeded."""

        async def boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.database.db.record_ai_channel_result", boom)
        await record_channel_outcome("c1", ok=True)


def _keys(mapping):
    """Async stub for the credential loader, keyed by CHANNEL ID.

    Keys moved from the shared `api_keys` table (where they could never be stored -
    see migration 0036) onto the channel row, so the loader is async now and no longer
    namespaces ids behind a "channel:" prefix.
    """

    async def _load():
        return dict(mapping)

    return _load



class TestRouterConstruction:
    def test_a_single_deployment_keeps_cooldowns_disabled(self):
        """Benching the only deployment leaves nowhere to fall - the original
        hazard the in-code comment warned about."""
        router = build_channel_router([{"model": "openai/gpt-5-nano", "api_key": "k"}])
        assert router.disable_cooldowns is True

    def test_multiple_deployments_enable_cooldowns(self):
        """With somewhere to fall back to, benching a sick provider is the point."""
        router = build_channel_router(
            [
                {"model": "openai/gpt-5-nano", "api_key": "k1"},
                {"model": "anthropic/claude-haiku-4-5", "api_key": "k2"},
            ]
        )
        assert router.disable_cooldowns is False

    def test_all_deployments_share_the_primary_alias(self):
        """Sharing one alias is how LiteLLM is told they are interchangeable."""
        router = build_channel_router(
            [
                {"model": "openai/gpt-5-nano", "api_key": "k1"},
                {"model": "anthropic/claude-haiku-4-5", "api_key": "k2"},
            ]
        )
        names = {m["model_name"] for m in router.model_list}
        assert names == {"primary"}

    def test_refuses_an_empty_deployment_list(self):
        with pytest.raises(ValueError):
            build_channel_router([])


def _async_returning(value):
    async def _fn(*_a, **_k):
        return value

    return _fn
