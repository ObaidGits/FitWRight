"""LiteLLM wrapper for multi-provider AI support."""

import asyncio
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import litellm
from litellm import Router
from litellm.router import RetryPolicy
from pydantic import BaseModel, ValidationError

from app.ai_routing import (
    channels_are_configured,
    record_channel_outcome,
    resolve_channel_route,
)
from app.ai_usage_meter import current_usage, note_call
from app.config import load_config_file, save_user_llm_config, settings
from app.errors import ApiError

LITELLM_LOGGER_NAMES = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


def _configure_litellm_logging() -> None:
    """Align LiteLLM logger levels with application settings."""
    numeric_level = getattr(logging, settings.log_llm, logging.WARNING)
    for logger_name in LITELLM_LOGGER_NAMES:
        logging.getLogger(logger_name).setLevel(numeric_level)


_configure_litellm_logging()

# Let LiteLLM drop provider-unsupported params (reasoning_effort, non-default
# temperature, etc.) instead of raising UnsupportedParamsError. This replaces
# the hardcoded per-model compatibility branches this module used to carry.
litellm.drop_params = True

# Let LiteLLM auto-drop `thinking_blocks` from assistant messages when required
# for a given turn (e.g., tool-call turns missing the blocks). Defensive; no
# current code path sends thinking, but future-proofs the Router.
litellm.modify_params = True

# LLM timeout configuration (seconds) - base values
LLM_TIMEOUT_HEALTH_CHECK = 30
LLM_TIMEOUT_COMPLETION = 120
LLM_TIMEOUT_JSON = 180  # JSON completions may take longer

# Health-check probe output budget. Must be generous enough for a reasoning
# model to complete its hidden reasoning AND emit a visible token, otherwise a
# small budget is consumed entirely by reasoning (finish_reason="length") and
# the probe sees empty content. Clamped to the model's real limit at call time.
HEALTH_CHECK_MAX_TOKENS = 512

# JSON-010: JSON extraction safety limits
MAX_JSON_EXTRACTION_RECURSION = 10
MAX_JSON_CONTENT_SIZE = 1024 * 1024  # 1MB

# Default token budget for structured JSON completions (e.g. resume parsing).
# Chosen to accommodate large resumes while staying within most providers'
# output limits. Callers should use get_safe_max_tokens() so this is
# automatically clamped to the model's actual capacity.
DEFAULT_JSON_MAX_TOKENS = 8192


class LLMRequestCancelled(asyncio.CancelledError):
    """Structured provider work was cancelled by its owning request."""


async def _await_with_cancellation(
    awaitable: Awaitable[Any],
    cancel_check: Callable[[], Awaitable[bool]] | None,
) -> Any:
    """Await provider work while polling an optional distributed cancel check.

    LiteLLM owns the underlying transport task. Cancelling and awaiting it here
    closes that work promptly and prevents an abandoned request from continuing
    to consume provider time in the background.
    """
    task = asyncio.ensure_future(awaitable)
    if cancel_check is None:
        return await task

    try:
        if await cancel_check():
            raise LLMRequestCancelled()
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.5)
            if done:
                return task.result()
            if await cancel_check():
                raise LLMRequestCancelled()
    except BaseException:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


class LLMConfig(BaseModel):
    """LLM configuration model."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


def _normalize_api_base(provider: str, api_base: str | None) -> str | None:
    """Normalize api_base for LiteLLM provider-specific expectations.

    When using proxies/aggregators, users often paste a base URL that already
    includes a version segment (e.g., `/v1`). Some LiteLLM provider handlers
    append those segments internally, which can lead to duplicated paths like
    `/v1/v1/...` and cause 404s.

    For the `openai` provider, LiteLLM uses the upstream OpenAI client which
    handles `/v1` correctly - we MUST preserve whatever the user pasted so
    that OpenAI-compatible endpoints like llama.cpp (http://localhost:8080/v1)
    round-trip intact. See issue #751.
    """
    if not api_base:
        return None

    base = api_base.strip()
    if not base:
        return None

    base = base.rstrip("/")

    # OpenAI / OpenAI-compatible: preserve the URL as-is. The OpenAI client
    # resolves paths correctly whether the base includes /v1 or not.
    if provider in ("openai", "openai_compatible"):
        return base or None

    # Anthropic handler appends '/v1/messages'. If base already ends with '/v1',
    # strip it to avoid '/v1/v1/messages'.
    if provider == "anthropic" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # Gemini handler appends '/v1/models/...'. If base already ends with '/v1',
    # strip it to avoid '/v1/v1/models/...'.
    if provider == "gemini" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # OpenRouter base is https://openrouter.ai/api/v1. LiteLLM appends /v1
    # internally, so strip it to avoid /v1/v1.
    if provider == "openrouter" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # Ollama doesn't use /v1 paths. Strip common suffixes users might paste:
    # /v1, /api/chat, /api/generate
    if provider == "ollama":
        for suffix in ("/v1", "/api/chat", "/api/generate", "/api"):
            if base.endswith(suffix):
                base = base[: -len(suffix)].rstrip("/")
                break

    return base or None


# Sentinel passed to the OpenAI client when the user leaves api_key blank for
# openai_compatible. The client validates non-empty strings but not the value
# format; local servers that don't check auth ignore it.
_OPENAI_COMPATIBLE_SENTINEL = "sk-no-key"


def _effective_api_key(provider: str, api_key: str) -> str:
    """Return the api_key to pass to LiteLLM.

    For openai_compatible with a blank key, substitute a sentinel so the
    OpenAI client accepts the call. Other providers pass through unchanged.
    """
    if provider == "openai_compatible" and not api_key:
        return _OPENAI_COMPATIBLE_SENTINEL
    return api_key


def _extract_text_parts(value: Any, depth: int = 0, max_depth: int = 10) -> list[str]:
    """Recursively extract text segments from nested response structures.

    Handles strings, lists, dicts with 'text'/'content'/'value' keys, and objects
    with text/content attributes. Limits recursion depth to avoid cycles.

    Args:
        value: Input value that may contain text in strings, lists, dicts, or objects.
        depth: Current recursion depth.
        max_depth: Maximum recursion depth before returning no content.

    Returns:
        A list of extracted text segments.
    """
    if depth >= max_depth:
        return []

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        parts: list[str] = []
        next_depth = depth + 1
        for item in value:
            parts.extend(_extract_text_parts(item, next_depth, max_depth))
        return parts

    if isinstance(value, dict):
        next_depth = depth + 1
        if "text" in value:
            return _extract_text_parts(value.get("text"), next_depth, max_depth)
        if "content" in value:
            return _extract_text_parts(value.get("content"), next_depth, max_depth)
        if "value" in value:
            return _extract_text_parts(value.get("value"), next_depth, max_depth)
        return []

    next_depth = depth + 1
    if hasattr(value, "text"):
        return _extract_text_parts(value.text, next_depth, max_depth)
    if hasattr(value, "content"):
        return _extract_text_parts(value.content, next_depth, max_depth)

    return []


def _join_text_parts(parts: list[str]) -> str | None:
    """Join text parts with newlines, filtering empty strings.

    Args:
        parts: Candidate text segments.

    Returns:
        Joined string or None if the result is empty.
    """
    joined = "\n".join(part for part in parts if part).strip()
    return joined or None


def _extract_message_text(message: Any) -> str | None:
    """Extract only the provider's final answer from ``message.content``.

    Reasoning channels are deliberately excluded. Promoting
    ``reasoning_content`` or ``thinking`` to final output leaks internal
    reasoning and, for structured calls, feeds prose into the JSON parser.
    Providers must place their user-visible answer in the standard content
    channel; a reasoning-only response is treated as incomplete.
    """
    return _join_text_parts(_extract_text_parts(_safe_get(message, "content")))


def _extract_reasoning_text(message: Any) -> str | None:
    """Extract provider reasoning for explicit, internal diagnostics only.

    This helper must never be used as an application response or structured
    payload fallback. Keeping it separate makes that boundary mechanically
    reviewable while still allowing safe presence/length diagnostics.
    """
    reasoning = _join_text_parts(
        _extract_text_parts(_safe_get(message, "reasoning_content"))
    )
    if reasoning:
        return reasoning
    return _join_text_parts(_extract_text_parts(_safe_get(message, "thinking")))


def _safe_get(obj: Any, key: str) -> Any:
    """Get attribute or dict key from an object."""
    if hasattr(obj, key):
        return getattr(obj, key)
    if isinstance(obj, dict):
        return obj.get(key)
    return None


def _extract_choice_text(choice: Any) -> str | None:
    """Extract plain text from a LiteLLM choice object.

    Tries message.content first, then choice.text, then choice.delta. Handles both
    object attributes and dict keys.
    """
    content = _extract_message_text(_safe_get(choice, "message"))
    if content:
        return content

    for attr in ("text", "delta"):
        value = _safe_get(choice, attr)
        if value is not None:
            extracted = _join_text_parts(_extract_text_parts(value))
            if extracted:
                return extracted

    return None


def _finish_reason(response: Any) -> str | None:
    """Return ``choices[0].finish_reason`` for a completion response, or None.

    ``"length"`` means the model hit ``max_tokens`` before finishing - the key
    signal that a reasoning model spent the whole budget on hidden reasoning
    tokens and had no room left to emit visible content.
    """
    try:
        choices = _safe_get(response, "choices") or []
        if not choices:
            return None
        return _safe_get(choices[0], "finish_reason")
    except Exception:  # pragma: no cover - defensive only
        return None


def _to_code_block(content: str | None, language: str = "text") -> str:
    """Wrap content in a markdown code block for client display."""
    text = (content or "").strip()
    if not text:
        text = "<empty>"
    return f"```{language}\n{text}\n```"


# Regex for provider-style API-key tokens that may appear in upstream error
# messages (OpenAI / Anthropic / OpenRouter / DeepSeek all use ``sk-...``;
# Google AI Studio uses ``AIza...``). The OpenAI client already partially
# masks keys in its error text but leaves the first ~8 and last ~4 chars
# visible, which is enough to identify the provider and correlate with the
# user's stored key. We redact any remaining key-like run before we surface
# the message to the client via ``error_detail``.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # sk-<anything-non-whitespace>, covering both plain and already-masked
    # tokens (e.g., ``sk-ant-a****...7QAA``). Minimum length of 12 avoids
    # matching harmless substrings like ``sk-foo``.
    re.compile(r"sk-[A-Za-z0-9_\-*.]{12,}"),
    # Google AI Studio.
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    # Generic Bearer tokens in an Authorization header line.
    re.compile(r"(?i)(Bearer\s+)[^\s\"']+"),
)


def _scrub_secrets(text: str) -> str:
    """Redact API-key-like substrings before the text leaves the server.

    Applied to ``error_detail`` on the failing-health-check path so that
    upstream exception messages (which may include partially-masked keys)
    can't be used by a Settings-page viewer to identify which provider /
    key variant is configured.
    """
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("<redacted>", redacted)
    return redacted


_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "openai",
    "openai_compatible": "openai_compatible",
    "anthropic": "anthropic",
    "gemini": "google",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "groq": "groq",
    "ollama": "ollama",
}


# Providers where the user commonly runs a local server without auth. For
# these, we MUST NOT fall back to ``settings.llm_api_key`` (the env-level
# default), because the env var may hold a real paid-API key that would then
# leak to a local/compatible endpoint the user set up expecting no auth.
_PROVIDERS_WITHOUT_ENV_KEY_FALLBACK: frozenset[str] = frozenset(
    {"openai_compatible", "ollama"}
)


# Providers whose endpoint IS a user-supplied Base URL. Every other provider
# must use LiteLLM's built-in endpoint for that provider, so a stored/env
# ``api_base`` (which only makes sense for a custom endpoint) must never be
# applied to them. Without this guard a Base URL saved for ``openai_compatible``
# leaks into e.g. ``gemini`` on a provider switch and the request 404s against
# the wrong host even with a correct key + model.
_CUSTOM_BASE_PROVIDERS: frozenset[str] = frozenset({"openai_compatible", "ollama"})


def provider_uses_custom_base(provider: str) -> bool:
    """Whether ``provider``'s endpoint is a user-supplied Base URL.

    Only these providers should ever carry an ``api_base``; cloud providers
    (openai/anthropic/gemini/openrouter/deepseek/groq) always use their own
    default endpoint, so any stored base is ignored for them.
    """
    return provider in _CUSTOM_BASE_PROVIDERS


#: Fragments that only ever appear in a template value, never in a real credential.
#: Provider keys are opaque base62-ish strings; the words below come from the example
#: env file and from documentation snippets people paste by mistake.
_PLACEHOLDER_MARKERS = (
    "your",
    "here",
    "changeme",
    "change-me",
    "replace",
    "example",
    "placeholder",
    "xxxx",
    "<",
    ">",
    "...",
)


def is_placeholder_key(api_key: str | None) -> bool:
    """Whether this "key" is obviously a template value rather than a credential.

    Why this exists: ``LLM_API_KEY=sk-your-openai-key-here`` copied from
    ``.env.example`` is a non-empty string, so every presence check treated the
    deployment as configured. The UI then showed AI as available, every feature
    attempted a real call, and the provider answered 401 - so the app reported
    "unavailable" for something that had simply never been set up. "Add your API key"
    and "the provider is down" are different instructions to the user, and one of them
    was unreachable.

    Detects only the unmistakable cases. A wrong-but-plausible key still has to be
    learned from the provider's refusal (see app/llm_health.py); this catches the value
    that could never have worked, without a network call.
    """
    if not api_key:
        return False
    lowered = api_key.strip().lower()
    if lowered in ("sk-", "sk-...", "none", "null"):
        return True
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def has_usable_credential(config: "LLMConfig") -> bool:
    """Whether this config could actually talk to a provider.

    The single answer to "is AI set up?", so every surface agrees. A placeholder key
    counts as NOT set up: telling a user to add their key is useful, whereas showing the
    feature and then reporting the provider unavailable is not.

    Self-hosted providers (Ollama, openai_compatible) need no key and are treated as
    configured, exactly as before. Requiring a base URL from them was tempting - one
    cannot work without it - but that is a different problem from this one, and changing
    it here would quietly alter what "configured" means for local installs.
    """
    if config.provider in _PROVIDERS_WITHOUT_ENV_KEY_FALLBACK:
        return True
    return bool(config.api_key) and not is_placeholder_key(config.api_key)


def resolve_api_key(stored: dict, provider: str) -> str:
    """Resolve the effective API key from stored config.

    Priority: top-level ``api_key`` > ``api_keys[provider]`` > env/settings
    default - EXCEPT for providers in ``_PROVIDERS_WITHOUT_ENV_KEY_FALLBACK``
    (``openai_compatible`` / ``ollama``), where the env-level default is
    skipped so a paid OpenAI key in ``LLM_API_KEY`` cannot leak to a local
    self-hosted server when the user leaves the provider key blank.

    This is the single source of truth for key resolution. Every code path
    that needs an API key (runtime, config display, health check, test
    endpoint) must call this function instead of reading ``stored["api_key"]``
    directly.
    """
    api_key = stored.get("api_key", "")
    if not api_key:
        api_keys = stored.get("api_keys", {})
        if not isinstance(api_keys, dict):
            api_keys = {}
        config_provider = _PROVIDER_KEY_MAP.get(provider, provider)
        env_default = (
            ""
            if provider in _PROVIDERS_WITHOUT_ENV_KEY_FALLBACK
            else settings.llm_api_key
        )
        api_key = api_keys.get(config_provider, env_default)
    return api_key


def get_llm_config(user_id: str | None = None) -> LLMConfig:
    """Get current LLM configuration, resolving the caller's API key per-user.

    ``user_id`` selects whose encrypted provider keys are read (R10.6); when
    omitted it falls back to the request-scoped effective user (published by the
    ``get_effective_user_id`` dependency) or the bootstrap owner locally, so one
    user's key never serves another user's LLM calls.

    Priority for api_key: top-level api_key > api_keys[provider] > env/settings
    Priority for reasoning_effort: config.json > env/settings

    Runs a one-shot migration for existing gpt-5 users: if provider is openai,
    model contains 'gpt-5', and reasoning_effort is ABSENT from config.json
    (not merely empty), persist reasoning_effort='minimal' to preserve the
    behavior the removed hardcoded branch provided. Users who clear the
    field explicitly (empty string persisted by the PUT handler) will not
    have it restored.
    """
    stored = load_config_file(user_id)
    provider = stored.get("provider", settings.llm_provider)
    model = stored.get("model", settings.llm_model)

    # One-shot migration: preserve old gpt-5 reasoning_effort behavior for
    # existing configs. Gated on ABSENT key so users can opt out by clearing
    # the field (PUT handler persists an empty string on clear).
    if (
        provider == "openai"
        and "gpt-5" in model.lower()
        and "reasoning_effort" not in stored
    ):
        stored["reasoning_effort"] = "minimal"
        try:
            save_user_llm_config(stored, user_id)
            logging.info(
                "Migrated gpt-5 config to preserve reasoning_effort=minimal "
                "(set REASONING_EFFORT= or clear in Settings to disable)"
            )
        except Exception as e:
            # Non-fatal - retry on next call.
            logging.warning("Failed to persist gpt-5 migration: %s", e)

    api_key = resolve_api_key(stored, provider)

    raw_re = stored.get("reasoning_effort", settings.reasoning_effort)
    # Normalize empty string to None - user explicitly cleared.
    reasoning_effort = raw_re if raw_re else None

    # Only custom-endpoint providers carry a Base URL. Ignoring it for cloud
    # providers prevents a stale base (e.g. left over from openai_compatible)
    # from being sent to gemini/openai/anthropic/... and 404-ing.
    raw_api_base = stored.get("api_base", settings.llm_api_base)
    api_base = raw_api_base if provider_uses_custom_base(provider) else None

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
        reasoning_effort=reasoning_effort,
    )


def get_model_name(config: LLMConfig) -> str:
    """Convert provider/model to LiteLLM format.

    For most providers, adds the provider prefix if not already present.
    For OpenRouter, always adds 'openrouter/' prefix since OpenRouter models
    use nested prefixes like 'openrouter/anthropic/claude-3.5-sonnet'.
    """
    provider_prefixes = {
        "openai": "",  # OpenAI models don't need prefix
        # openai_compatible: route via LiteLLM's openai/ prefix so the OpenAI
        # client handles the request; works for llama.cpp, vLLM, LM Studio,
        # and any server exposing the OpenAI Chat Completions API shape.
        "openai_compatible": "openai/",
        "anthropic": "anthropic/",
        "openrouter": "openrouter/",
        "gemini": "gemini/",
        "deepseek": "deepseek/",
        "groq": "groq/",
        "ollama": "ollama_chat/",  # ollama_chat/ routes to /api/chat (supports messages array)
    }

    prefix = provider_prefixes.get(config.provider, "")

    # OpenRouter is special: always add openrouter/ prefix unless already present
    # OpenRouter models use nested format: openrouter/anthropic/claude-3.5-sonnet
    if config.provider == "openrouter":
        if config.model.startswith("openrouter/"):
            return config.model
        return f"openrouter/{config.model}"

    # For other providers, don't add prefix if model already has a known prefix
    known_prefixes = [
        "openrouter/",
        "anthropic/",
        "gemini/",
        "deepseek/",
        "groq/",
        "ollama/",
        "ollama_chat/",
        "openai/",
    ]
    if any(config.model.startswith(p) for p in known_prefixes):
        return config.model

    # Add provider prefix for models that need it
    return f"{prefix}{config.model}" if prefix else config.model


# ---------------------------------------------------------------------------
# Router - centralises transport retries, cooldowns, and error-type policies
# ---------------------------------------------------------------------------

# Bounded LRU cache of Routers keyed by config fingerprint. A single-slot cache
# thrashed under multi-user/hosted load: alternating users (different keys)
# rebuilt the Router on nearly every request. Keeping one Router per distinct
# config removes that thrash while bounding memory (least-recently-used configs
# are evicted).
_ROUTER_CACHE_MAX = 32
_router_cache: "OrderedDict[str, Router]" = OrderedDict()
_router_lock = threading.Lock()


def _config_fingerprint(config: LLMConfig) -> str:
    """Generate a fingerprint to detect config changes.

    Uses Python's built-in ``hash()`` on the API key - stable within a
    single process (which is the cache lifetime), collision-resistant,
    and not a cryptographic function so it won't trigger CodeQL alerts.
    The raw key is never stored in the fingerprint string.
    """
    key_hash = hash(config.api_key) if config.api_key else 0
    return f"{config.provider}|{config.model}|{key_hash}|{config.api_base}"


def _build_router(config: LLMConfig) -> Router:
    """Build a LiteLLM Router with error-type retry policies."""
    model_name = get_model_name(config)

    litellm_params: dict[str, Any] = {"model": model_name}
    effective_key = _effective_api_key(config.provider, config.api_key)
    if effective_key:
        litellm_params["api_key"] = effective_key
    api_base = _normalize_api_base(config.provider, config.api_base)
    if api_base:
        litellm_params["api_base"] = api_base

    return Router(
        model_list=[
            {
                "model_name": "primary",
                "litellm_params": litellm_params,
            }
        ],
        num_retries=3,
        retry_policy=RetryPolicy(
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            TimeoutErrorRetries=2,
            RateLimitErrorRetries=3,
            ContentPolicyViolationErrorRetries=0,
            InternalServerErrorRetries=2,
        ),
        # Cooldowns disabled: with a single deployment and no fallback,
        # cooldowns would blackout the backend on transient failures.
        # Re-enable when a fallback deployment is added.
        disable_cooldowns=True,
    )


#: Shared retry policy. The distinctions matter: retrying an auth failure or a
#: malformed request is pointless (they fail identically every time and on every
#: provider), while a timeout or rate limit is exactly what a retry is for.
_RETRY_POLICY = RetryPolicy(
    AuthenticationErrorRetries=0,
    BadRequestErrorRetries=0,
    TimeoutErrorRetries=2,
    RateLimitErrorRetries=3,
    ContentPolicyViolationErrorRetries=0,
    InternalServerErrorRetries=2,
)


def build_channel_router(deployments: list[dict[str, Any]]) -> Router:
    """Build a Router over one or more operator-configured channels.

    ``deployments`` is an ordered list of ``{"model": ..., "api_key": ...,
    "api_base": ...}`` dicts, best first, as produced by
    :func:`app.ai_channels.select_channels` plus credential lookup.

    All deployments share the model alias ``"primary"``, which is how LiteLLM is
    told they are interchangeable: it will try the first and fall back through the
    rest on a retryable error.

    Cooldowns are ENABLED here, unlike the single-deployment router above. That
    router had to disable them - benching its only deployment would black out the
    whole backend on a transient blip. With a fallback list, benching a sick
    provider is the entire point, and its own docstring said to re-enable this once
    a fallback existed.
    """
    if not deployments:
        raise ValueError("build_channel_router requires at least one deployment")

    model_list = []
    for dep in deployments:
        params: dict[str, Any] = {"model": dep["model"]}
        if dep.get("api_key"):
            params["api_key"] = dep["api_key"]
        if dep.get("api_base"):
            params["api_base"] = dep["api_base"]
        model_list.append({"model_name": "primary", "litellm_params": params})

    return Router(
        model_list=model_list,
        num_retries=3,
        retry_policy=_RETRY_POLICY,
        # A single channel is back to the original hazard: benching it leaves
        # nowhere to go, so only enable cooldowns when there is somewhere to fall.
        disable_cooldowns=len(model_list) < 2,
    )


def get_router(config: LLMConfig | None = None) -> tuple[Router, LLMConfig]:
    """Get (or build) the LiteLLM Router for ``config``.

    Routers are cached per distinct config fingerprint in a bounded LRU, so a
    given provider/model/key/base reuses its Router across requests instead of
    rebuilding it whenever a different user's config was used in between.
    Returns the Router and the config it was built from.
    """
    if config is None:
        config = get_llm_config()

    key = _config_fingerprint(config)
    with _router_lock:
        router = _router_cache.get(key)
        if router is None:
            router = _build_router(config)
            _router_cache[key] = router
            logging.info("LiteLLM Router built for %s/%s", config.provider, config.model)
            # Evict least-recently-used entries beyond the cap.
            while len(_router_cache) > _ROUTER_CACHE_MAX:
                _router_cache.popitem(last=False)
        else:
            # Mark most-recently-used.
            _router_cache.move_to_end(key)

    return router, config


# ---------------------------------------------------------------------------
# Operator channels (spec: ai-provider-admin, Phase 1)
# ---------------------------------------------------------------------------

#: Channel routers are cached like config routers, and for the same reason: building
#: a Router per request would add real latency to every generation. Keyed on the
#: deployment set, so a channel edit, a health change, or a reorder produces a
#: different key and therefore a fresh router - stale routing is worse than a rebuild.
_channel_router_cache: "OrderedDict[str, Router]" = OrderedDict()


class ChannelsUnavailable(ApiError):
    """Every operator channel is down, and the caller has no key of their own.

    A DISTINCT state, and the reason this class exists rather than reusing the
    generic completion failure: "we are having trouble" and "you need to configure
    something" are different instructions, and this codebase has already shipped a
    bug where an AI credential problem rendered as "You are offline" and sent users
    to check their wifi.
    """

    def __init__(self) -> None:
        super().__init__(
            503,
            "ai_unavailable",
            "AI features are temporarily unavailable while we restore a provider. "
            "Nothing is wrong with your account - please try again shortly. You can "
            "also add your own provider key in Settings to continue right away.",
        )


def _channel_deployment_fingerprint(deployments: list[dict[str, Any]]) -> str:
    parts = [f"{d.get('model')}|{d.get('api_base') or ''}" for d in deployments]
    return "channels:" + ";".join(parts)


def _config_from_channel(route) -> LLMConfig:
    """An ``LLMConfig`` describing the channel that will be tried FIRST.

    Everything downstream - the model-limit clamp, temperature and reasoning-effort
    support, the metrics provider label - already speaks LLMConfig. Presenting the
    channel this way means none of it needs to learn about channels at all.
    """
    base = get_llm_config()
    primary = route.candidates[0]
    return base.model_copy(
        update={
            "provider": primary.provider,
            "model": primary.model,
            "api_base": primary.api_base or None,
            # The channel's own credential is already inside the router's deployment
            # list. Blanking it here keeps the operator's key out of a config object
            # that gets logged and fingerprinted.
            "api_key": "",
        }
    )


def _get_channel_router(route) -> Router:
    key = _channel_deployment_fingerprint(route.deployments)
    with _router_lock:
        router = _channel_router_cache.get(key)
        if router is None:
            router = build_channel_router(route.deployments)
            _channel_router_cache[key] = router
            logging.info(
                "LiteLLM channel router built for %d deployment(s)", len(route.deployments)
            )
            while len(_channel_router_cache) > _ROUTER_CACHE_MAX:
                _channel_router_cache.popitem(last=False)
        else:
            _channel_router_cache.move_to_end(key)
    return router


def _config_has_usable_credential(config: LLMConfig) -> bool:
    """Whether the non-channel fallback could actually serve a call."""
    if config.provider in _PROVIDERS_WITHOUT_ENV_KEY_FALLBACK:
        # Self-hosted: a base URL is the credential.
        return bool(config.api_base)
    return bool(config.api_key) and not is_placeholder_key(config.api_key)


def _guard_input_size(*parts: str | None) -> None:
    """Refuse an oversized input before spending anything on it (task 6.1).

    Enforced here, at the same choke points as metering, for the same reason: an
    endpoint-by-endpoint check is one someone eventually forgets, and the omission is
    invisible because the endpoint still works.

    A no-op when no feature is in context - health probes, the channel test and
    background jobs have no user-supplied payload to police.
    """
    ctx = current_usage()
    if not ctx or not ctx.feature:
        return
    from app.ai_input_limits import check_input_size

    check_input_size(ctx.feature, *parts)


async def _resolve_router(config: LLMConfig | None):
    """Pick the router for this call: operator channels, or the caller's own config.

    Returns ``(router, config, route)`` where ``route`` is None when channels were not
    used. Precedence, in order:

      1. An EXPLICIT config wins. Health probes and the admin channel test pass one,
         and they are asking about a specific provider - silently redirecting them
         through a channel would make them test something other than what they named.
      2. A user on their OWN key stays on it. They cost the operator nothing, so
         spending an operator channel on them would be backwards.
      3. An operator channel, if any is healthy and permitted for this feature.
      4. The existing single-provider path.

    If channels are configured but none are usable AND step 4 has no credential, this
    raises :class:`ChannelsUnavailable` rather than letting the call fail as a generic
    provider error - the user needs to know it is our outage, not their setup.
    """
    if config is not None:
        router, cfg = get_router(config)
        return router, cfg, None

    ctx = current_usage()
    feature = ctx.feature if ctx else None

    if feature and not (ctx and ctx.has_own_key):
        route = await resolve_channel_route(feature)
        if route:
            cfg = _config_from_channel(route)
            return _get_channel_router(route), cfg, route

        # No usable channel. Decide BEFORE building a router: an unusable fallback
        # config makes the provider client itself throw (a bare model name with no
        # provider), and that raw transport error would reach the user instead of the
        # clean outage message. Resolve the config, judge it, then build.
        fallback = get_llm_config()
        if not _config_has_usable_credential(fallback) and await channels_are_configured():
            raise ChannelsUnavailable()
        router, cfg = get_router(fallback)
        return router, cfg, None

    router, cfg = get_router(config)
    return router, cfg, None


async def check_llm_health(
    config: LLMConfig | None = None,
    *,
    include_details: bool = False,
    test_prompt: str | None = None,
) -> dict[str, Any]:
    """Check if the LLM provider is accessible and working."""
    if config is None:
        config = get_llm_config()

    # Check if API key is configured. Ollama and openai_compatible local
    # servers often run without auth, so a blank key is acceptable for those
    # providers - a sentinel is passed downstream (see _effective_api_key)
    # to satisfy the OpenAI client's non-empty-string validation.
    if config.provider not in ("ollama", "openai_compatible") and not config.api_key:
        return {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": "api_key_missing",
        }

    model_name = get_model_name(config)

    prompt = test_prompt or "Hi"

    try:
        # Make a test call with timeout. The probe budget must be large enough
        # for a REASONING model to finish its hidden reasoning AND still emit a
        # visible token; a tiny budget (the former 64) is spent entirely on
        # reasoning, returning finish_reason="length" with empty content and a
        # false "unhealthy". Clamp to the model's real output limit.
        probe_max_tokens = get_safe_max_tokens(model_name, HEALTH_CHECK_MAX_TOKENS)
        # Pass API key directly to avoid race conditions with global os.environ
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": probe_max_tokens,
            "api_key": _effective_api_key(config.provider, config.api_key),
            "api_base": _normalize_api_base(config.provider, config.api_base),
            "timeout": LLM_TIMEOUT_HEALTH_CHECK,
        }
        if _supports_reasoning_effort(model_name, config.reasoning_effort):
            kwargs["reasoning_effort"] = config.reasoning_effort

        response = await litellm.acompletion(**kwargs)
        content = _extract_choice_text(response.choices[0])
        if not content:
            finish_reason = _finish_reason(response)
            reasoning = _extract_reasoning_text(_safe_get(response.choices[0], "message"))
            # A reasoning model that hit the token cap mid-reasoning
            # (finish_reason="length") with visible reasoning output IS
            # reachable, authenticated, and generating - it simply needs a
            # larger budget, which real feature calls provide. Treat this as
            # healthy-with-warning rather than a misleading configuration
            # failure (fixes the false "Connection failed" for reasoning models).
            if finish_reason == "length" and reasoning:
                logging.info(
                    "LLM health check: reasoning model exhausted probe budget "
                    "on internal reasoning; treating as healthy",
                    extra={"provider": config.provider, "model": config.model},
                )
                result: dict[str, Any] = {
                    "healthy": True,
                    "provider": config.provider,
                    "model": config.model,
                    "response_model": response.model if response else None,
                    "warning_code": "reasoning_truncated",
                    "warning": (
                        "This is a reasoning model. The connection works, but the "
                        "short test used its whole budget on internal reasoning. "
                        "Real requests use a larger budget and will return output."
                    ),
                }
                if include_details:
                    result["test_prompt"] = _to_code_block(prompt)
                    result["model_output"] = _to_code_block(None)
                    result["reasoning_content"] = _to_code_block(reasoning)
                return result
            # LLM-003: Genuine empty response (no reasoning, or a normal stop)
            # marks health as unhealthy.
            logging.warning(
                "LLM health check returned empty content",
                extra={"provider": config.provider, "model": config.model},
            )
            result = {
                "healthy": False,
                "provider": config.provider,
                "model": config.model,
                "response_model": response.model if response else None,
                "error_code": "empty_content",
                "message": "LLM returned empty response",
            }
            if include_details:
                result["test_prompt"] = _to_code_block(prompt)
                result["model_output"] = _to_code_block(None)
            return result

        result = {
            "healthy": True,
            "provider": config.provider,
            "model": config.model,
            "response_model": response.model if response else None,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(content)
            # Surface reasoning/thinking text separately ONLY when the model
            # also returned distinct primary content. If message.content was
            # empty, _extract_choice_text already folded the reasoning text
            # into `content` above - surfacing it here too would duplicate
            # identical text in "Model output" and "Model thinking".
            msg = response.choices[0].message
            primary_content = _join_text_parts(
                _extract_text_parts(_safe_get(msg, "content"))
            )
            reasoning_text = None
            if primary_content:
                reasoning_text = (
                    _join_text_parts(_extract_text_parts(_safe_get(msg, "reasoning_content")))
                    or _join_text_parts(_extract_text_parts(_safe_get(msg, "thinking")))
                )
            result["reasoning_content"] = (
                _to_code_block(reasoning_text) if reasoning_text else None
            )
        return result
    except Exception as e:
        # Log full exception details server-side, but do not expose them to clients
        logging.exception(
            "LLM health check failed",
            extra={"provider": config.provider, "model": config.model},
        )

        # Provide a minimal, actionable client-facing hint without leaking secrets.
        error_code = "health_check_failed"
        message = str(e)
        if "404" in message and "/v1/v1/" in message:
            error_code = "duplicate_v1_path"
        elif "404" in message:
            error_code = "not_found_404"
        elif "<!doctype html" in message.lower() or "<html" in message.lower():
            error_code = "html_response"
        result = {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": error_code,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(None)
            # Scrub api-key-like tokens before surfacing the upstream error
            # text so the Settings UI can't be used to read back even a
            # partially-masked copy of the configured key.
            result["error_detail"] = _to_code_block(_scrub_secrets(message))
        return result


class _ProbeChange(BaseModel):
    path: str
    action: str
    value: str


class _StructuredProbeOutput(BaseModel):
    """Representative schema for the structured-output capability probe.

    Deliberately mirrors the NESTED shape resume tailoring actually demands (an
    object containing an array of objects with fixed string keys, plus a scalar)
    rather than a trivial flat object. A trivial `{ok, tags}` probe is passed
    even by weak models that then fail the real diff/keyword schema; this nested
    shape is a far better predictor of whether tailoring will succeed.
    """

    changes: list[_ProbeChange]
    notes: str


_STRUCTURED_PROBE_PROMPT = (
    'Return ONLY a JSON object with exactly two keys:\n'
    '  "changes": an array of exactly TWO objects, each with the string keys '
    '"path", "action", and "value";\n'
    '  "notes": a short string.\n'
    'Example (match this shape exactly):\n'
    '{"changes": [{"path": "summary", "action": "replace", "value": "text"}, '
    '{"path": "skills", "action": "reorder", "value": "text"}], "notes": "done"}\n'
    'Output only the JSON object - no prose, no markdown, no code fence.'
)


async def check_structured_output(
    config: LLMConfig | None = None,
    *,
    attempts: int = 2,
) -> dict[str, Any]:
    """Probe whether a model reliably returns valid STRUCTURED (JSON) output.

    This is the capability that actually decides whether features like resume
    tailoring work - a plain "Hi" health check passes for models that later fail
    to produce parseable JSON. Runs the same ``complete_json`` path the features
    use, a few times, and returns a clear verdict:

    - ``reliable``    - every attempt returned valid structured output.
    - ``flaky``       - some attempts succeeded; generation may need a retry.
    - ``unsupported`` - no attempt produced valid structured output; features
                        that need JSON will likely fail on this model.

    A non-content provider error (auth / rate limit / timeout / unavailable /
    request rejected) short-circuits to ``verdict="unknown"`` with the classified
    reason, since that is a connection problem, not a structured-output verdict.
    """
    if config is None:
        config = get_llm_config()

    attempts = max(1, min(attempts, 3))
    successes = 0
    for _ in range(attempts):
        try:
            await complete_json(
                _STRUCTURED_PROBE_PROMPT,
                config=config,
                system_prompt="You output only valid JSON matching the requested shape.",
                max_tokens=256,
                retries=1,
                schema_type="diff",
                response_model=_StructuredProbeOutput,
            )
            successes += 1
        except Exception as exc:  # noqa: BLE001 - classified below
            _status, code, message, _retryable = classify_llm_error(exc)
            if code != "llm_response_invalid":
                # Not a structured-output problem - a real connection/provider
                # error. Report it as such rather than a capability verdict.
                logging.info(
                    "Structured-output probe hit a provider error (%s); "
                    "reporting as connection issue",
                    code,
                    extra={"provider": config.provider, "model": config.model},
                )
                return {
                    "structured_ok": None,
                    "structured_verdict": "unknown",
                    "structured_attempts": attempts,
                    "structured_successes": successes,
                    "structured_error_code": code,
                    "structured_message": message,
                }

    if successes == attempts:
        verdict, ok, msg = (
            "reliable",
            True,
            "This model reliably returns the structured output the app needs.",
        )
    elif successes > 0:
        verdict, ok, msg = (
            "flaky",
            True,
            "This model works but sometimes returns invalid structured output; "
            "generation may occasionally need a retry.",
        )
    else:
        verdict, ok, msg = (
            "unsupported",
            False,
            "This model failed to return valid structured output. Features like "
            "resume tailoring may fail on it - choose a model that supports "
            "JSON/structured output.",
        )
    return {
        "structured_ok": ok,
        "structured_verdict": verdict,
        "structured_attempts": attempts,
        "structured_successes": successes,
        "structured_message": msg,
    }


# ---------------------------------------------------------------------------
# AI metrics instrumentation (admin-panel-upgrade Req 4.1)
# ---------------------------------------------------------------------------
#
# Every non-cancelled provider round-trip (the Router-backed completion functions
# below) is counted once via the in-process AiMetricsService. The current metric
# model is binary (success/failure), so cancelled streams are omitted rather than
# misclassified; see ``stream_complete`` and its focused metrics test. Only the
# allowlisted aggregate signals are recorded - total calls / success / failure /
# timeouts / retries / per-provider counts / total tokens / latency. Rejected
# fields (temperature, prompt/completion length, model version, system prompt,
# tool calls, reasoning tokens, ids) are never passed. See app/admin/ai_metrics.py.


def classify_llm_error(exc: BaseException) -> tuple[int, str, str, bool]:
    """Classify provider failures into a stable, secret-safe API contract.

    The exception chain is inspected because completion helpers may wrap the
    transport exception. No upstream body, model name, URL, or credential data
    is returned to clients.
    """
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__

    def contains(*names: str) -> bool:
        classes = tuple(
            cls
            for name in names
            if isinstance((cls := getattr(litellm, name, None)), type)
        )
        return bool(classes) and any(isinstance(item, classes) for item in chain)

    if contains("AuthenticationError"):
        return 424, "llm_authentication_failed", "The AI provider rejected its credentials. Check the provider key in Settings.", False
    if contains("RateLimitError"):
        return 429, "llm_rate_limited", "The AI provider is rate-limiting requests. Please wait and retry.", True
    if any(_is_timeout_error(item) for item in chain):
        return 504, "llm_timeout", "The AI provider did not respond in time. Please retry.", True
    if contains("BadRequestError", "ContextWindowExceededError"):
        return 422, "llm_request_rejected", "The configured AI model rejected this request. Verify the model and provider settings.", False
    if any(isinstance(item, (ValueError, ValidationError, json.JSONDecodeError)) for item in chain):
        return 422, "llm_response_invalid", "The AI provider returned an invalid response. Please retry or choose another model.", True
    if contains("ServiceUnavailableError", "InternalServerError", "APIConnectionError"):
        return 503, "llm_provider_unavailable", "The AI provider is temporarily unavailable. Please retry shortly.", True
    return 502, "llm_provider_error", "The AI provider could not complete the request. Please verify Settings or retry.", True


def llm_api_error(
    exc: BaseException,
    *,
    stage: str,
    details: dict[str, Any] | None = None,
) -> "ApiError":
    """Build the standard secret-safe API error for a provider operation.

    Call this only at a boundary known to be executing provider work; database,
    storage, and programming failures must retain their own error semantics.
    """
    from app.errors import ApiError

    status, code, message, retryable = classify_llm_error(exc)
    safe_details: dict[str, Any] = {"stage": stage, "retryable": retryable}
    if details:
        safe_details.update(details)
    return ApiError(
        status_code=status,
        code=code,
        message=message,
        details=safe_details,
    )


def _is_timeout_error(exc: BaseException) -> bool:
    """Best-effort classification of an exception as an LLM timeout.

    Covers the stdlib ``TimeoutError`` (which ``asyncio.TimeoutError`` aliases
    on 3.11+) and LiteLLM's own ``Timeout`` type when available.
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    lite_timeout = getattr(litellm, "Timeout", None)
    if lite_timeout is not None and isinstance(exc, lite_timeout):
        return True
    return False


def _usage_total_tokens(response: Any) -> int:
    """Extract the allowlisted aggregate ``usage.total_tokens`` (0 if absent).

    Only the aggregate is read - never the prompt/completion breakdown, which is
    a rejected field for the admin AI metrics allowlist.
    """
    try:
        usage = _safe_get(response, "usage")
        total = _safe_get(usage, "total_tokens")
        return int(total) if total else 0
    except Exception:
        return 0


def _router_retry_count(value: Any) -> int:
    """Return a Router retry count only when LiteLLM explicitly exposes one.

    Successful Router calls publish ``x-litellm-attempted-retries`` in the
    response's private ``additional_headers`` metadata. Exhausted LiteLLM
    exceptions publish both ``num_retries`` (actual attempts) and
    ``max_retries``. Reading only these documented, numeric counters avoids
    guessing from configured limits, exception wording, prompts, or other
    potentially sensitive metadata.
    """

    def _non_negative_int(raw: Any) -> int | None:
        if isinstance(raw, bool):
            return None
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    try:
        hidden = _safe_get(value, "_hidden_params")
        headers = _safe_get(hidden, "additional_headers")
        explicit = _safe_get(headers, "x-litellm-attempted-retries")
        parsed = _non_negative_int(explicit)
        if parsed is not None:
            return parsed
    except Exception:
        pass

    # LiteLLM sets this pair after its Router exhausts retries. Requiring both
    # fields avoids mistaking a deployment's configured ``num_retries`` for an
    # observed retry count.
    try:
        actual = _non_negative_int(_safe_get(value, "num_retries"))
        maximum = _non_negative_int(_safe_get(value, "max_retries"))
        if actual is not None and maximum is not None:
            return actual
    except Exception:
        pass
    return 0


def _record_ai_call(
    provider: str | None,
    *,
    ok: bool,
    timed_out: bool = False,
    retried: bool | int = False,
    tokens: int = 0,
    latency_ms: float = 0.0,
) -> None:
    """Best-effort record of one AI provider call to the AiMetricsService.

    The import is lazy so ``llm.py`` stays import-light and no import cycle can
    form. This never raises - metrics must never break an LLM call (mirrors the
    defensive AdminMetricsMiddleware).
    """
    try:
        from app.admin.ai_metrics import get_ai_metrics_service

        get_ai_metrics_service().record_call(
            provider,
            ok=ok,
            timed_out=timed_out,
            retried=retried,
            tokens=tokens,
            latency_ms=latency_ms,
        )
    except Exception:  # pragma: no cover - metrics must never break a call
        pass


async def complete(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Make a completion request to the LLM.

    Transport retries (429, 500, timeout) are handled by the Router.
    """
    _guard_input_size(prompt, system_prompt)
    router, config, _route = await _resolve_router(config)
    model_name = get_model_name(config)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # AI metrics (Req 4.1): time + classify this single provider round-trip.
    _start = time.perf_counter()
    _ok = False
    _timed_out = False
    _tokens = 0
    _router_retries = 0
    # Central clamp (fix 11): never send more than a KNOWN model limit on the
    # first attempt; unknown/custom models keep the caller's request.
    effective_max_tokens = _clamp_to_model_limit(model_name, max_tokens)
    try:
        content: str | None = None
        # Up to 3 attempts to get non-empty content. Two empty-content shapes
        # are recovered instead of failing:
        #   * finish_reason="length" -> reasoning model spent the budget on
        #     hidden reasoning; retry with a larger, model-clamped budget.
        #   * genuine empty (normal stop, no content) -> the free model is
        #     non-deterministic; retry (nudging temperature when supported).
        # This directly hardens the small plain-text calls (title, outreach,
        # cover letter) against the intermittent empty responses free models emit.
        attempt_temperature = temperature
        _MAX_EMPTY_ATTEMPTS = 3
        for _attempt in range(_MAX_EMPTY_ATTEMPTS):
            kwargs: dict[str, Any] = {
                "model": "primary",
                "messages": messages,
                "max_tokens": effective_max_tokens,
                "timeout": _calculate_timeout(
                    "completion", effective_max_tokens, config.provider
                ),
            }
            if _supports_temperature(model_name, attempt_temperature):
                kwargs["temperature"] = attempt_temperature
            if _supports_reasoning_effort(model_name, config.reasoning_effort):
                kwargs["reasoning_effort"] = config.reasoning_effort

            response = await router.acompletion(**kwargs)
            _router_retries = _router_retry_count(response)
            _tokens = _usage_total_tokens(response)

            content = _extract_choice_text(response.choices[0])
            if content:
                break
            if _attempt >= _MAX_EMPTY_ATTEMPTS - 1:
                break
            bumped = get_safe_max_tokens(model_name, effective_max_tokens + 2048)
            if _finish_reason(response) == "length" and bumped > effective_max_tokens:
                logging.info(
                    "Empty content with finish_reason=length; retrying with a "
                    "larger budget (%d -> %d) for %s",
                    effective_max_tokens,
                    bumped,
                    model_name,
                )
                effective_max_tokens = bumped
            else:
                # Genuine empty response: nudge temperature (when supported) and
                # retry, since the provider is non-deterministic.
                logging.info(
                    "Empty content (finish_reason=%s); retrying for %s",
                    _finish_reason(response),
                    model_name,
                )
                attempt_temperature = min(1.0, (attempt_temperature or 0.7) + 0.2)

        if not content:
            raise ValueError("Empty response from LLM")
        # Strip thinking tags from reasoning models (deepseek-r1, qwq, etc.)
        if "<think>" in content:
            content = _strip_thinking_tags(content)
            if not content:
                raise ValueError("Response contained only thinking content, no output")
        _ok = True
        return content
    except Exception as e:
        _timed_out = _is_timeout_error(e)
        if _router_retries == 0:
            _router_retries = _router_retry_count(e)
        # Log the actual error server-side for debugging
        logging.error(f"LLM completion failed: {e}", extra={
                      "model": model_name})
        raise ValueError(
            "LLM completion failed. Please check your API configuration and try again."
        ) from e
    finally:
        _record_ai_call(
            config.provider,
            ok=_ok,
            timed_out=_timed_out,
            retried=_router_retries,
            tokens=_tokens,
            latency_ms=(time.perf_counter() - _start) * 1000,
        )
        # Billing tally, recorded ALONGSIDE the anonymous metrics above rather
        # than inside them: that system's contract forbids retaining per-user
        # detail, this one requires it. Two systems, same choke point.
        note_call(
            total_tokens=_tokens,
            estimated=_ok and _tokens == 0,
            provider=config.provider,
            model=model_name,
            channel_id=_route.primary_channel_id if _route else None,
            latency_ms=(time.perf_counter() - _start) * 1000,
        )
        # Channel health drives failover: consecutive failures bench a channel so the
        # next request starts lower down the list instead of retrying a dead provider.
        if _route:
            await record_channel_outcome(
                _route.primary_channel_id,
                ok=_ok,
                error_class=None if _ok else ("timeout" if _timed_out else "error"),
            )


# ---------------------------------------------------------------------------
# Streaming completion (P4 Resilience - Streaming AI, R1)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StreamUsage:
    """Token usage for a (possibly cancelled) streamed generation (R1.7)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class StreamResult:
    """Mutable accumulator threaded through :func:`stream_complete`.

    The generator yields token *deltas*; the caller also reads ``text`` (the full
    accumulated output) and ``usage`` (token accounting, populated on the final
    chunk or estimated for a cancelled stream) after iteration ends. ``cancelled``
    records whether the stream stopped early via the cancel check.
    """

    text: str = ""
    usage: StreamUsage = field(default_factory=StreamUsage)
    cancelled: bool = False


def provider_supports_streaming(config: LLMConfig | None = None) -> bool:
    """Return whether the active model is known not to support native streams.

    LiteLLM's model registry exposes ``supports_native_streaming``. A declared
    negative is authoritative. Registry entries that predate the flag and
    unknown compatible models remain runtime-probed instead of being disabled;
    their stream errors are classified by the SSE endpoint.
    """
    if config is None:
        config = get_llm_config()
    model_name = get_model_name(config)
    try:
        info = litellm.get_model_info(model_name) or {}
        supports = info.get("supports_native_streaming")
        return supports is not False
    except Exception:
        # Unknown is not the same as unsupported. This preserves streaming for
        # custom OpenAI-compatible servers while allowing explicit registry
        # negatives to take the deterministic non-stream path.
        return True


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for providers that omit usage.

    Used only when the provider does not report usage on a streamed response
    (common for local/compatible servers and on cancellation) so cost accounting
    still records a non-zero, order-of-magnitude-correct figure (R1.7).
    """
    return max(0, (len(text) + 3) // 4)


async def stream_complete(
    prompt: str,
    result: StreamResult,
    *,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Stream a completion, yielding token deltas (R1.1).

    ``result`` is mutated in place: ``result.text`` accumulates the full output,
    ``result.usage`` is filled from the provider's final chunk (or estimated),
    and ``result.cancelled`` is set if ``cancel_check`` returned truthy mid-flight.
    Cancellation aborts the provider iterator and leaves no persisted state
    (Property 3) - persistence only ever happens on explicit accept, never here.

    Raises the underlying provider error so the caller can emit a terminal
    ``error`` SSE event and fall back to the non-stream path (R1.3).
    """
    _guard_input_size(prompt, system_prompt)
    router, config, _route = await _resolve_router(config)
    model_name = get_model_name(config)
    # Central clamp (fix 11): cap to a KNOWN model limit; unknown/custom models
    # keep the caller's request.
    max_tokens = _clamp_to_model_limit(model_name, max_tokens)

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    kwargs: dict[str, Any] = {
        "model": "primary",
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
        # Ask OpenAI-family providers to include usage in the final chunk; other
        # providers ignore it (drop_params) and we fall back to an estimate.
        "stream_options": {"include_usage": True},
        "timeout": _calculate_timeout("completion", max_tokens, config.provider),
    }
    if _supports_temperature(model_name, temperature):
        kwargs["temperature"] = temperature
    if _supports_reasoning_effort(model_name, config.reasoning_effort):
        kwargs["reasoning_effort"] = config.reasoning_effort

    # AI metrics (Req 4.1): time + classify this streamed provider round-trip.
    # The current metric model has no cancellation state and requires every
    # recorded call to be success or failure. Cancelled/consumer-closed streams
    # are therefore deliberately omitted rather than misclassified; completed
    # and provider-failed streams are still recorded once.
    _start = time.perf_counter()
    _ok = False
    _timed_out = False
    _router_retries = 0
    _cancelled_for_metrics = False
    saw_reasoning = False
    try:
        try:
            response = await router.acompletion(**kwargs)
            _router_retries = _router_retry_count(response)
        except Exception as _e:
            _timed_out = _is_timeout_error(_e)
            _router_retries = _router_retry_count(_e)
            raise
        try:
            async for chunk in response:
                if cancel_check is not None and await cancel_check():
                    result.cancelled = True
                    _cancelled_for_metrics = True
                    break
                # Capture usage if the provider reports it on any chunk.
                usage = _safe_get(chunk, "usage")
                if usage is not None:
                    result.usage = StreamUsage(
                        prompt_tokens=int(_safe_get(usage, "prompt_tokens") or 0),
                        completion_tokens=int(_safe_get(usage, "completion_tokens") or 0),
                        total_tokens=int(_safe_get(usage, "total_tokens") or 0),
                    )
                choices = _safe_get(chunk, "choices") or []
                if not choices:
                    continue
                delta = _safe_get(choices[0], "delta")
                if _extract_reasoning_text(delta):
                    saw_reasoning = True
                piece = _join_text_parts(_extract_text_parts(_safe_get(delta, "content")))
                if piece:
                    result.text += piece
                    yield piece
        finally:
            # Best-effort close of the provider stream so a cancelled/aborted
            # generation frees the upstream connection promptly (no leak, R1.5).
            aclose = getattr(response, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:  # pragma: no cover - provider close best-effort
                    pass

        # Strip reasoning tags from providers that embed them in content.
        if "<think>" in result.text:
            result.text = _strip_thinking_tags(result.text)

        if not result.cancelled and not result.text:
            detail = (
                "Provider streamed reasoning but no final answer"
                if saw_reasoning
                else "Provider stream completed without final content"
            )
            raise ValueError(detail)

        # Fill in usage if the provider didn't report it (or on cancellation).
        if result.usage.total_tokens == 0 and result.text:
            est = _estimate_tokens(result.text)
            result.usage = StreamUsage(completion_tokens=est, total_tokens=est)

        _ok = not result.cancelled
    except (GeneratorExit, asyncio.CancelledError):
        # Closing the async generator is another cancellation shape. Preserve
        # result semantics while keeping it out of provider success/failure.
        _cancelled_for_metrics = True
        raise
    finally:
        if not _cancelled_for_metrics:
            _record_ai_call(
                config.provider,
                ok=_ok,
                timed_out=_timed_out,
                retried=_router_retries,
                tokens=result.usage.total_tokens,
                latency_ms=(time.perf_counter() - _start) * 1000,
            )
        # Metered even when CANCELLED, unlike the health metrics above. A user who
        # stops a stream halfway still caused the tokens the provider generated,
        # and the operator is invoiced for them. Excluding cancellations here would
        # make abandoning streams a free way to consume the operator's budget.
        note_call(
            total_tokens=result.usage.total_tokens,
            prompt_tokens=result.usage.prompt_tokens,
            completion_tokens=result.usage.completion_tokens,
            estimated=result.usage.total_tokens == 0 and bool(result.text),
            provider=config.provider,
            model=model_name,
            channel_id=_route.primary_channel_id if _route else None,
            latency_ms=(time.perf_counter() - _start) * 1000,
        )
        # Guarded by the same cancellation check as the metrics above, and that guard
        # is load-bearing here for a second reason: awaiting inside an async
        # generator's teardown during GeneratorExit is not allowed. Skipping the
        # await on cancellation avoids it entirely.
        if _route and not _cancelled_for_metrics:
            await record_channel_outcome(
                _route.primary_channel_id,
                ok=_ok,
                error_class=None if _ok else ("timeout" if _timed_out else "error"),
            )


def _supports_json_mode(model_name: str) -> bool:
    """Check if the model supports JSON mode via LiteLLM's model registry.

    Queries LiteLLM's model info for every provider (including openai,
    anthropic, etc.) so that capability is always determined from the
    registry rather than a hardcoded provider list.

    Ollama models support JSON mode natively (format="json") but are
    often not in LiteLLM's registry (custom/local models), so we
    always return True for ollama.

    Args:
        model_name: LiteLLM-formatted model name (from get_model_name).
    """
    # Ollama supports JSON mode natively via format="json" even when
    # models aren't in LiteLLM's registry (custom, quantized, etc.)
    if model_name.startswith(("ollama/", "ollama_chat/")):
        return True

    try:
        info = litellm.get_model_info(model=model_name)
        supported_params = info.get("supported_openai_params", [])
        return "response_format" in supported_params
    except Exception:
        # Model not in LiteLLM's registry - fall back to prompt-only JSON
        # mode (the system prompt already instructs "respond with valid JSON
        # only"). This avoids sending response_format to models that may
        # reject it.
        logging.debug("Model %s not in LiteLLM registry, skipping JSON mode", model_name)
        return False


def _is_response_format_unsupported(error: Exception) -> bool:
    """Return True if a 400 indicates the server rejected ``response_format``.

    Some OpenAI-compatible servers (e.g. LM Studio, older llama.cpp builds) are
    reported as supporting ``response_format`` by LiteLLM's registry but reject
    the ``{"type": "json_object"}`` we send for JSON mode, returning a 400 such
    as ``'response_format.type' must be 'json_schema' or 'text'`` (issue #857).

    Detecting this lets ``complete_json`` fall back to prompt-only JSON mode
    instead of failing the whole request, while genuine bad requests (e.g.
    context-length errors) still propagate.

    Requires both a mention of ``response_format`` *and* a rejection/validation
    cue, so that an unrelated 400 which merely names the parameter (e.g. a
    context-length error) does not trigger a pointless fallback retry. The cue
    list stays broad enough to catch varied provider wording ("must be ...",
    "not supported", "unsupported", "not allowed", "invalid") rather than any
    single provider's exact message.
    """
    msg = str(error).lower()
    if "response_format" not in msg:
        return False
    rejection_cues = ("must be", "not support", "unsupported", "not allowed", "invalid")
    return any(cue in msg for cue in rejection_cues)


FALLBACK_MAX_TOKENS = 4096

def get_safe_max_tokens(model_name: str, requested: int = DEFAULT_JSON_MAX_TOKENS) -> int:
    """Return a token count safe for the given model, clamped to its output limit.

    Queries LiteLLM's model registry for ``max_output_tokens`` and returns
    ``min(requested, model_limit)`` so callers never send a value that exceeds
    what the backend actually supports.

    If the model is not in the registry (e.g. custom Ollama models), it falls
    back to a safe conservative limit (FALLBACK_MAX_TOKENS).

    Args:
        model_name: LiteLLM-formatted model name (from get_model_name).
        requested: Desired token budget; defaults to DEFAULT_JSON_MAX_TOKENS.

    Returns:
        Safe token count, clamped correctly and always >= 1.
    """
    safe_requested = max(1, requested)

    try:
        info = litellm.get_model_info(model=model_name)
        model_limit = info.get("max_output_tokens") or info.get("max_tokens")
        if model_limit and isinstance(model_limit, int) and model_limit > 0:
            safe = min(safe_requested, model_limit)
            if safe < safe_requested:
                logging.debug(
                    "max_tokens clamped %d -> %d for model %s (model limit)",
                    safe_requested,
                    safe,
                    model_name,
                )
            return safe
    except Exception:
        pass  # Model not in registry, drop down to fallback logic

    safe = min(safe_requested, FALLBACK_MAX_TOKENS)
    logging.debug(
        "Model %s not in LiteLLM registry, clamping requested max_tokens %d -> %d constraint",
        model_name,
        safe_requested,
        safe,
    )
    return safe


def _clamp_to_model_limit(model_name: str, requested: int) -> int:
    """Clamp ``requested`` to the model's real output cap when the registry knows it.

    Unlike :func:`get_safe_max_tokens`, an UNKNOWN/custom model (common for
    self-hosted ``openai_compatible`` endpoints) keeps the caller's requested
    value rather than being shrunk to the conservative fallback - shrinking a
    self-hosted model's budget could needlessly truncate large resumes. This is
    the central guard that stops a caller's raw ``max_tokens`` from exceeding a
    *known* provider limit (which would 400 or truncate), addressing the
    previously un-clamped first attempt in complete()/complete_json()/stream.
    """
    safe_requested = max(1, requested)
    try:
        info = litellm.get_model_info(model=model_name) or {}
        limit = info.get("max_output_tokens") or info.get("max_tokens")
        if isinstance(limit, int) and limit > 0 and limit < safe_requested:
            logging.debug(
                "Clamping max_tokens %d -> %d for %s (known model limit)",
                safe_requested,
                limit,
                model_name,
            )
            return limit
    except Exception:  # pragma: no cover - registry lookup is best-effort
        pass
    return safe_requested


def _appears_truncated(data: dict, schema_type: str = "resume") -> bool:
    """LLM-001: Check if JSON data appears to be truncated.

    Detects suspicious patterns indicating incomplete responses.
    The checks are schema-aware so that enrichment/diff/keyword outputs
    are not evaluated against resume-structure heuristics.

    Args:
        data: Parsed JSON dict.
        schema_type: Expected schema - "resume" (full resume), "enrichment"
            (analyze output), "diff" (diff changes), "keywords", or
            "interview_prep".
            Determines which fields are checked for truncation.
    """
    if not isinstance(data, dict):
        return False

    if schema_type == "resume":
        # Full resume structure: check for empty required arrays
        suspicious_empty_arrays = ["workExperience", "education", "skills"]
        for key in suspicious_empty_arrays:
            if key in data and data[key] == []:
                # Log warning - these are rarely empty in real resumes
                logging.warning(
                    "Possible truncation detected: '%s' is empty",
                    key,
                )
                return True
        return False

    if schema_type == "enrichment":
        # Enrichment analyze returns items_to_enrich + questions.
        # Empty arrays are valid (resume is already strong).
        # Only flag if keys are entirely missing (LLM ignored structure).
        if "items_to_enrich" not in data or "questions" not in data:
            logging.warning(
                "Possible truncation detected: enrichment missing required keys"
            )
            return True
        return False

    if schema_type == "interview_prep":
        required = {
            "role_fit_analysis",
            "resume_questions",
            "project_follow_ups",
            "skill_gaps",
            "talking_points",
        }
        missing = required - set(data)
        if missing:
            logging.warning(
                "Possible truncation detected: interview_prep missing required keys: %s",
                ", ".join(sorted(missing)),
            )
            return True
        return False

    # For "diff", "keywords", and unknown schemas: no truncation heuristics.
    # Diff may legitimately return empty changes; keywords may return empty
    # lists when the job description has no actionable terms.
    return False


def _supports_reasoning_effort(model_name: str, effort: str | None) -> bool:
    """Return whether the registry advertises the requested reasoning control.

    Unknown/custom models are treated conservatively: omitting an optional
    tuning parameter is safer than turning an otherwise valid completion into a
    provider 400. LiteLLM still receives the model's natural default behavior.
    """
    if not effort:
        return False
    # Custom / self-hosted endpoints honor an explicitly configured effort
    # (fix 14). These map to the ``openai/`` (openai_compatible) and
    # ``ollama``/``ollama_chat`` prefixes. LiteLLM's registry only *guesses* the
    # capabilities of a user's private server (it often returns a generic OpenAI
    # entry that omits reasoning), so its opinion is not authoritative here. We
    # therefore trust the user's explicit choice and rely on
    # ``litellm.drop_params=True`` to strip the param before sending when the
    # endpoint does not accept it (verified: no 400) - enabling reasoning on
    # capable custom models without breaking ones that ignore it. Real
    # first-party ``openai`` models carry no ``openai/`` prefix and fall through
    # to the registry check below, so their behavior is unchanged.
    if model_name.startswith(("openai/", "ollama/", "ollama_chat/")):
        return True
    try:
        info = litellm.get_model_info(model=model_name) or {}
        params = info.get("supported_openai_params", []) or []
        if "reasoning_effort" not in params and not info.get("supports_reasoning"):
            return False
        flag_by_effort = {
            "minimal": "supports_minimal_reasoning_effort",
            "low": "supports_low_reasoning_effort",
            "high": "supports_max_reasoning_effort",
        }
        flag = flag_by_effort.get(effort)
        return flag is None or info.get(flag) is not False
    except Exception:
        logging.debug(
            "Model %s not in LiteLLM registry; omitting reasoning_effort", model_name
        )
        return False


def _supports_temperature(model_name: str, temperature: float | None = None) -> bool:
    """Check if the model supports the given temperature value.

    Uses LiteLLM model registry for capability detection, with
    provider-specific fallbacks for known restrictions:
      - Anthropic claude-opus-4.*: temperature is deprecated
      - Moonshot kimi-k2.6: only temperature=1 allowed

    Queries LiteLLM's model info for every provider so that capability is
    always determined from the registry rather than a hardcoded list.

    Args:
        model_name: LiteLLM-formatted model name (from get_model_name).
        temperature: The temperature value to check. If None, returns True
            (caller isn't setting a specific value).

    Returns:
        True if the model supports the given temperature, False otherwise.
    """
    if temperature is None:
        return True

    # Ollama models are often not in LiteLLM's registry (custom/local),
    # but they universally support temperature.
    if model_name.startswith(("ollama/", "ollama_chat/")):
        return True

    try:
        info = litellm.get_model_info(model=model_name)
        supported_params = info.get("supported_openai_params", [])
        if "temperature" not in supported_params:
            return False
    except Exception:
        # Model not in LiteLLM's registry - be conservative and skip
        # temperature to avoid BadRequestError from unsupported params.
        logging.debug(
            "Model %s not in LiteLLM registry, skipping temperature", model_name
        )
        return False

    # Provider-specific restrictions not captured by the registry.
    # Anthropic Opus 4.x deprecated temperature entirely.
    if "claude-opus-4" in model_name.lower():
        return False

    # Moonshot kimi-k2.6 only allows temperature=1.
    if "kimi-k2.6" in model_name.lower() and temperature != 1.0:
        return False

    return True


def _get_retry_temperature(model_name: str, attempt: int, base_temp: float = 0.1) -> float | None:
    """LLM-002: Get temperature for retry attempt.

    Returns None if the model does not support temperature at all.
    Returns 1.0 for models that only support temperature=1.
    Otherwise returns increasing temperatures for retry variation.
    """
    # Moonshot kimi-k2.6 only allows temperature=1.
    if "kimi-k2.6" in model_name.lower():
        return 1.0

    if not _supports_temperature(model_name, base_temp):
        return None

    temperatures = [base_temp, 0.3, 0.5, 0.7]
    return temperatures[min(attempt, len(temperatures) - 1)]


def _calculate_timeout(
    operation: str,
    max_tokens: int = 4096,
    provider: str = "openai",
) -> int:
    """LLM-005: Calculate adaptive timeout based on operation and parameters."""
    base_timeouts = {
        "health_check": LLM_TIMEOUT_HEALTH_CHECK,
        "completion": LLM_TIMEOUT_COMPLETION,
        "json": LLM_TIMEOUT_JSON,
    }

    base = base_timeouts.get(operation, LLM_TIMEOUT_COMPLETION)

    # Scale by token count (relative to 4096 baseline)
    token_factor = max(1.0, max_tokens / 4096)

    # Provider-specific latency adjustments. Includes the self-hosted/aggregator
    # and reasoning-heavy providers that were previously missing (and silently
    # got the 1.0 default), which under-budgeted their typically higher latency.
    provider_factors = {
        "openai": 1.0,
        "anthropic": 1.2,
        "openrouter": 1.5,  # Aggregator: more variable latency
        "openai_compatible": 1.5,  # Self-hosted / gateway: unknown latency profile
        "deepseek": 1.5,  # Reasoning models emit many hidden tokens
        "gemini": 1.2,
        "groq": 1.0,
        "ollama": 2.0,  # Local models can be slower
    }
    provider_factor = provider_factors.get(provider, 1.0)

    return int(base * token_factor * provider_factor)


def _strip_thinking_tags(content: str) -> str:
    """Strip thinking/reasoning tags from model output.

    Ollama thinking models (deepseek-r1, qwq, etc.) wrap their reasoning
    in <think>...</think> tags. The actual answer follows after the closing
    tag. Strip these so JSON extraction finds the real output.
    """
    # Remove <think>...</think> blocks (including multiline)
    stripped = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
    # Also handle unclosed <think> tag (model may still be "thinking" at end)
    stripped = re.sub(r"<think>.*", "", stripped, flags=re.DOTALL)
    return stripped.strip()


def _extract_json(content: str, _depth: int = 0) -> str:
    """Extract JSON from LLM response, handling various formats.

    LLM-001: Improved to detect and reject likely truncated JSON.
    LLM-007: Improved error messages for debugging.
    JSON-010: Added recursion depth and size limits.
    """
    # JSON-010: Safety limits
    if _depth > MAX_JSON_EXTRACTION_RECURSION:
        raise ValueError(
            f"JSON extraction exceeded max recursion depth: {_depth}")
    if len(content) > MAX_JSON_CONTENT_SIZE:
        raise ValueError(
            f"Content too large for JSON extraction: {len(content)} bytes")

    original = content

    # Strip thinking model tags (deepseek-r1, qwq, etc.)
    if "<think>" in content:
        content = _strip_thinking_tags(content)

    # Remove markdown code blocks
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0]
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1]
            # Remove language identifier if present (e.g., "json\n{...")
            if content.startswith(("json", "JSON")):
                content = content[4:]

    content = content.strip()

    # If content starts with {, find the matching }
    if content.startswith("{"):
        depth = 0
        end_idx = -1
        in_string = False
        escape_next = False

        for i, char in enumerate(content):
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end_idx = i
                    break

        # LLM-001: Check for unbalanced braces - loop ended without depth reaching 0
        if end_idx == -1 and depth != 0:
            logging.warning(
                "JSON extraction found unbalanced braces (depth=%d), possible truncation",
                depth,
            )

        if end_idx != -1:
            return content[: end_idx + 1]

    # Try to find JSON object in the content (only if not already at start)
    start_idx = content.find("{")
    if start_idx > 0:
        # Only recurse if { is found after position 0 to avoid infinite recursion
        return _extract_json(content[start_idx:], _depth + 1)

    # LLM-007: Log unrecognized format for debugging
    logging.error(
        "Could not extract JSON from response format. Content preview: %s",
        content[:200] if content else "<empty>",
    )
    raise ValueError(f"No JSON found in response: {original[:200]}")


def _repair_json(text: str) -> str:
    """Best-effort repair of near-valid JSON from weaker/free models.

    Fixes the common ways a free model breaks strict JSON without changing the
    data's meaning: surrounding prose, ``//`` comments, trailing commas, smart
    quotes, and truncated output (unclosed strings/objects/arrays). Returns a
    candidate string for ``json.loads``; callers still validate the parsed
    result, so an over-eager repair can only fail closed (never fabricates).
    """
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s).rsplit("```", 1)[0].strip()

    # Trim to the outermost JSON container (drop leading/trailing prose).
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if starts:
        s = s[min(starts):]
    end = max(s.rfind("}"), s.rfind("]"))
    if end != -1:
        s = s[: end + 1]

    # Strip // line comments and /* */ block comments outside strings is hard;
    # do a conservative line-level strip of leading `//` comment lines.
    s = re.sub(r"(?m)^\s*//.*$", "", s)
    # Normalize smart quotes that models sometimes emit.
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
    # Remove trailing commas before a closing } or ].
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    # Balance quotes/brackets for truncated output. Scan string-aware and append
    # the closers still open at end-of-text (in reverse order).
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "{") or (ch == "]" and stack[-1] == "[")):
                stack.pop()
    if in_string:
        s += '"'
    # Drop a dangling trailing comma introduced by truncation, then close.
    s = re.sub(r",\s*$", "", s.rstrip())
    for opener in reversed(stack):
        s += "}" if opener == "{" else "]"
    return s


def _loads_lenient(json_str: str) -> Any:
    """Parse JSON, transparently applying :func:`_repair_json` on failure."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = _repair_json(json_str)
        parsed = json.loads(repaired)  # may raise; caller handles/retries
        logging.info("Recovered malformed JSON via repair pass")
        return parsed


async def complete_json(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    retries: int = 2,
    schema_type: str = "resume",
    response_model: type[BaseModel] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """Make a completion request expecting a validated JSON object.

    Provider-native JSON Schema is preferred when a Pydantic ``response_model``
    is supplied and the model registry advertises support. Otherwise this uses
    JSON-object mode and finally prompt-only JSON after a classified provider
    rejection. Content-quality retries cover malformed, truncated, and
    schema-invalid output; transport retries remain owned by LiteLLM Router.

    ``response_model`` validates at this shared trust boundary while the
    original dictionary is returned so provider aliases and extra, explicitly
    tolerated workflow fields are preserved. ``cancel_check`` is polled while
    the provider request is in flight; cancellation closes the transport task
    and raises :class:`LLMRequestCancelled` without recording a false failure.
    """
    _guard_input_size(prompt, system_prompt)
    router, config, _route = await _resolve_router(config)
    model_name = get_model_name(config)

    # Build messages
    json_system = (
        system_prompt or ""
    ) + "\n\nYou must respond with valid JSON only. No explanations, no markdown."
    messages = [
        {"role": "system", "content": json_system},
        {"role": "user", "content": prompt},
    ]

    # Capability negotiation: native JSON Schema gives the strongest contract,
    # then JSON-object mode, then prompt-only JSON when the provider rejects the
    # advertised response format.
    use_json_mode = _supports_json_mode(model_name)
    use_json_schema = False
    if response_model is not None:
        try:
            use_json_schema = bool(litellm.supports_response_schema(model_name))
        except Exception:
            use_json_schema = False
    response_format_mode: Literal["json_schema", "json_object", "prompt"] = (
        "json_schema" if use_json_schema else "json_object" if use_json_mode else "prompt"
    )

    # Output budget for this call. A truncation-triggered retry raises this
    # (clamped to the model's real limit) rather than re-issuing an identically
    # capped request that would truncate again - fewer wasted, doomed retries.
    # Central clamp (fix 11): cap the first attempt to a KNOWN model limit;
    # unknown/custom models keep the caller's request.
    effective_max_tokens = _clamp_to_model_limit(model_name, max_tokens)

    for attempt in range(retries + 1):
        _attempt_start = time.perf_counter()
        _attempt_ok = False
        _attempt_timed_out = False
        _attempt_tokens = 0
        _attempt_router_retries = 0
        _call_started = False
        _attempt_cancelled = False
        try:
            kwargs: dict[str, Any] = {
                "model": "primary",
                "messages": messages,
                "max_tokens": effective_max_tokens,
                "timeout": _calculate_timeout("json", effective_max_tokens, config.provider),
            }
            # LLM-002: Increase temperature on retry for variation
            retry_temp = _get_retry_temperature(model_name, attempt)
            if retry_temp is not None:
                kwargs["temperature"] = retry_temp
            if _supports_reasoning_effort(model_name, config.reasoning_effort):
                kwargs["reasoning_effort"] = config.reasoning_effort

            if response_format_mode == "json_schema" and response_model is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": response_model.__name__,
                        "strict": True,
                        "schema": response_model.model_json_schema(),
                    },
                }
            elif response_format_mode == "json_object":
                kwargs["response_format"] = {"type": "json_object"}

            # Metrics are finalized only after structured content validation.
            # Each Router invocation is one call; ``attempt > 0`` contributes
            # exactly one app-level content retry, while explicit Router retry
            # metadata contributes transport retries without inference.
            _call_started = True
            try:
                response = await _await_with_cancellation(
                    router.acompletion(**kwargs), cancel_check
                )
            except LLMRequestCancelled:
                _attempt_cancelled = True
                raise
            except Exception as _call_exc:
                _attempt_timed_out = _is_timeout_error(_call_exc)
                _attempt_router_retries = _router_retry_count(_call_exc)
                raise
            _attempt_router_retries = _router_retry_count(response)
            _attempt_tokens = _usage_total_tokens(response)
            content = _extract_choice_text(response.choices[0])

            if not content:
                # A reasoning model can spend the whole budget on hidden
                # reasoning (finish_reason="length"), leaving no visible JSON.
                # Raise the budget (model-clamped) so the content retry can
                # actually emit the object instead of truncating identically.
                if _finish_reason(response) == "length":
                    bumped = get_safe_max_tokens(model_name, effective_max_tokens + 2048)
                    if bumped > effective_max_tokens:
                        effective_max_tokens = bumped
                raise ValueError("Empty response from LLM")

            logging.debug(
                f"LLM response (attempt {attempt + 1}): {content[:300]}")

            # Extract and parse JSON (with a repair pass for near-valid output).
            json_str = _extract_json(content)
            result = _loads_lenient(json_str)
            if not isinstance(result, dict):
                raise ValueError("Structured response must be a JSON object")
            if response_model is not None:
                response_model.model_validate(result)

            # LLM-001: Check if parsed result appears truncated
            if isinstance(result, dict) and _appears_truncated(result, schema_type):
                if attempt < retries:
                    logging.warning(
                        "Parsed JSON appears truncated (attempt %d/%d), retrying",
                        attempt + 1,
                        retries + 1,
                    )
                    if schema_type == "resume":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL sections. Do not truncate."
                        )
                    elif schema_type == "enrichment":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL keys: items_to_enrich, questions, analysis_summary. Do not truncate."
                        )
                    elif schema_type == "interview_prep":
                        hint = (
                            "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL keys: role_fit_analysis, resume_questions, project_follow_ups, skill_gaps, talking_points. Do not truncate."
                        )
                    else:
                        hint = (
                            "\n\nIMPORTANT: Output ONLY a valid JSON object. Start with { and end with }."
                        )
                    messages[-1]["content"] = prompt + hint
                    # Truncation usually means the output hit the token cap.
                    # Give the retry more room (clamped to the model's limit) so
                    # it can actually complete instead of truncating identically.
                    bumped = get_safe_max_tokens(model_name, effective_max_tokens + 2048)
                    if bumped > effective_max_tokens:
                        effective_max_tokens = bumped
                    continue
                raise ValueError(
                    f"Structured {schema_type} response remained incomplete after "
                    f"{retries + 1} attempts"
                )

            _attempt_ok = True
            return result

        except json.JSONDecodeError as e:
            # Content quality - malformed JSON, retry with prompt hint
            logging.warning(f"JSON parse failed (attempt {attempt + 1}): {e}")
            if response_format_mode != "prompt":
                # A provider that claimed native structured support returned
                # malformed JSON. Step down one capability level for the retry.
                response_format_mode = (
                    "json_object"
                    if response_format_mode == "json_schema" and use_json_mode
                    else "prompt"
                )
                logging.warning(
                    "Structured response mode failed for %s; retrying with %s (attempt %d)",
                    model_name,
                    response_format_mode,
                    attempt + 1,
                )
            if attempt < retries:
                messages[-1]["content"] = (
                    prompt
                    + "\n\nIMPORTANT: Output ONLY a valid JSON object. Start with { and end with }."
                )
                continue
            raise ValueError(
                f"Failed to parse JSON after {retries + 1} attempts: {e}")

        except (ValueError, ValidationError) as e:
            # Empty/extraction/truncation/schema failures are content-quality
            # failures. Retry with an explicit contract reminder; never accept a
            # partially valid object merely because the provider returned 2xx.
            logging.warning(
                "Structured content validation failed (attempt %d/%d): %s",
                attempt + 1,
                retries + 1,
                e,
            )
            if attempt < retries:
                messages[-1]["content"] = (
                    prompt
                    + "\n\nIMPORTANT: Return one COMPLETE JSON object matching every requested field and type. No prose."
                )
                continue
            raise ValueError(
                f"Invalid structured response after {retries + 1} attempts"
            ) from e

        except litellm.BadRequestError as e:
            # JSON-012b: some OpenAI-compatible servers (e.g. LM Studio) report
            # response_format support via the registry but reject
            # {"type": "json_object"} with a 400 (issue #857). The Router does
            # not retry bad requests, so recover here by disabling JSON mode and
            # retrying prompt-only. Unrelated 400s (e.g. context length) still
            # propagate.
            if (
                response_format_mode != "prompt"
                and _is_response_format_unsupported(e)
            ):
                rejected_mode = response_format_mode
                response_format_mode = (
                    "json_object"
                    if rejected_mode == "json_schema" and use_json_mode
                    else "prompt"
                )
                logging.warning(
                    "Provider rejected %s response format for %s; retrying with %s (attempt %d)",
                    rejected_mode,
                    model_name,
                    response_format_mode,
                    attempt + 1,
                )
                if attempt < retries:
                    continue
            raise

        except Exception:
            # Transport errors - Router already retried with backoff.
            # Cooldowns are disabled (see _build_router); no additional
            # retry is attempted here.
            raise
        finally:
            if _call_started and not _attempt_cancelled:
                _record_ai_call(
                    config.provider,
                    ok=_attempt_ok,
                    timed_out=_attempt_timed_out,
                    retried=_attempt_router_retries + (1 if attempt > 0 else 0),
                    tokens=_attempt_tokens,
                    latency_ms=(time.perf_counter() - _attempt_start) * 1000,
                )
            # Per ATTEMPT, deliberately. A retried structured call burns tokens on
            # every attempt and the provider bills for each, so accumulating them
            # is the honest total. The reserve caps what the user can be charged, so
            # a pathological retry loop lands on the operator, not the user.
            if _call_started:
                note_call(
                    total_tokens=_attempt_tokens,
                    estimated=_attempt_ok and _attempt_tokens == 0,
                    provider=config.provider,
                    model=model_name,
                    channel_id=_route.primary_channel_id if _route else None,
                    latency_ms=(time.perf_counter() - _attempt_start) * 1000,
                )
                if _route and not _attempt_cancelled:
                    await record_channel_outcome(
                        _route.primary_channel_id,
                        ok=_attempt_ok,
                        error_class=None
                        if _attempt_ok
                        else ("timeout" if _attempt_timed_out else "error"),
                    )

    raise ValueError(f"Failed after {retries + 1} attempts")
