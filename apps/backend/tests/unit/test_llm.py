"""Unit tests for LLM capability helpers in app.llm."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm import _appears_truncated, _get_retry_temperature, _supports_temperature


# ---------------------------------------------------------------------------
# _supports_temperature
# ---------------------------------------------------------------------------


class TestSupportsTemperature:
    """Tests for _supports_temperature()."""

    def test_none_temperature_returns_true(self):
        """When temperature is None, the caller isn't setting a value - allow."""
        assert _supports_temperature("gpt-4", None) is True

    def test_ollama_always_true(self):
        """Ollama models support temperature even when not in registry."""
        assert _supports_temperature("ollama/llama3", 0.7) is True
        assert _supports_temperature("ollama_chat/llama3", 0.7) is True

    @patch("app.llm.litellm.get_model_info")
    def test_openai_gpt4_supports_temperature(self, mock_get_model_info):
        """GPT-4 has temperature in supported_openai_params."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens", "top_p"]
        }
        assert _supports_temperature("gpt-4", 0.7) is True

    @patch("app.llm.litellm.get_model_info")
    def test_model_without_temperature_param(self, mock_get_model_info):
        """Model registry omits temperature -> not supported."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["max_tokens"]
        }
        assert _supports_temperature("some-model", 0.7) is False

    @patch("app.llm.litellm.get_model_info")
    def test_opus4_deprecated_temperature(self, mock_get_model_info):
        """Anthropic Opus 4.x deprecated temperature entirely."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _supports_temperature("anthropic/claude-opus-4-7", 0.7) is False
        # Also check with temperature=1 - still deprecated
        assert _supports_temperature("anthropic/claude-opus-4-7", 1.0) is False

    @patch("app.llm.litellm.get_model_info")
    def test_kimi_k26_only_allows_one(self, mock_get_model_info):
        """Moonshot kimi-k2.6 only allows temperature=1."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _supports_temperature("openai/kimi-k2.6", 0.7) is False
        assert _supports_temperature("openai/kimi-k2.6", 1.0) is True

    @patch("app.llm.litellm.get_model_info")
    def test_model_not_in_registry(self, mock_get_model_info):
        """Unknown model not in registry - be conservative, skip temperature."""
        mock_get_model_info.side_effect = Exception("model not found")
        assert _supports_temperature("unknown-vendor/model", 0.7) is False

    @patch("app.llm.litellm.get_model_info")
    def test_case_insensitive_model_name(self, mock_get_model_info):
        """Provider-specific checks are case-insensitive."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _supports_temperature("Anthropic/Claude-Opus-4-7", 0.7) is False
        assert _supports_temperature("OPENAI/KIMI-K2.6", 0.7) is False
        assert _supports_temperature("openai/KIMI-K2.6", 1.0) is True


# ---------------------------------------------------------------------------
# _get_retry_temperature
# ---------------------------------------------------------------------------


class TestGetRetryTemperature:
    """Tests for _get_retry_temperature()."""

    @patch("app.llm.litellm.get_model_info")
    def test_openai_progression(self, mock_get_model_info):
        """Standard retry temperature progression for supported models."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _get_retry_temperature("gpt-4", 0) == 0.1
        assert _get_retry_temperature("gpt-4", 1) == 0.3
        assert _get_retry_temperature("gpt-4", 2) == 0.5
        assert _get_retry_temperature("gpt-4", 3) == 0.7
        assert _get_retry_temperature("gpt-4", 10) == 0.7  # clamped

    @patch("app.llm.litellm.get_model_info")
    def test_opus4_returns_none(self, mock_get_model_info):
        """Opus 4 doesn't support temperature -> None on all retries."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _get_retry_temperature("anthropic/claude-opus-4-7", 0) is None
        assert _get_retry_temperature("anthropic/claude-opus-4-7", 3) is None

    @patch("app.llm.litellm.get_model_info")
    def test_kimi_k26_returns_one(self, mock_get_model_info):
        """Kimi K2.6 only allows temperature=1 -> always 1.0."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _get_retry_temperature("openai/kimi-k2.6", 0) == 1.0
        assert _get_retry_temperature("openai/kimi-k2.6", 1) == 1.0
        assert _get_retry_temperature("openai/kimi-k2.6", 5) == 1.0

    @patch("app.llm.litellm.get_model_info")
    def test_custom_base_temp(self, mock_get_model_info):
        """Custom base_temp is respected for supported models."""
        mock_get_model_info.return_value = {
            "supported_openai_params": ["temperature", "max_tokens"]
        }
        assert _get_retry_temperature("gpt-4", 0, base_temp=0.2) == 0.2
        assert _get_retry_temperature("gpt-4", 1, base_temp=0.2) == 0.3


# ---------------------------------------------------------------------------
# _appears_truncated
# ---------------------------------------------------------------------------


class TestAppearsTruncated:
    """Tests for _appears_truncated() with schema_type awareness."""

    # --- resume schema ---

    def test_resume_empty_work_experience(self):
        """Empty workExperience array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [],
            "education": [{"degree": "BS"}],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_empty_education(self):
        """Empty education array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_empty_skills(self):
        """Empty skills array in resume structure is suspicious."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [{"degree": "BS"}],
            "skills": [],
        }
        assert _appears_truncated(data, schema_type="resume") is True

    def test_resume_valid(self):
        """Well-formed resume with all sections present is not truncated."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            "education": [{"degree": "BS"}],
            "skills": ["Python"],
        }
        assert _appears_truncated(data, schema_type="resume") is False

    def test_resume_missing_fields_not_empty(self):
        """Missing fields are not the same as empty arrays - not flagged."""
        data = {
            "personalInfo": {"name": "John"},
            "workExperience": [{"title": "Dev"}],
            # education and skills omitted
        }
        assert _appears_truncated(data, schema_type="resume") is False

    # --- enrichment schema ---

    def test_enrichment_missing_keys(self):
        """Missing required keys in enrichment output is suspicious."""
        data = {"analysis_summary": "Good resume"}
        assert _appears_truncated(data, schema_type="enrichment") is True

    def test_enrichment_empty_arrays(self):
        """Empty items_to_enrich and questions are valid (resume already strong)."""
        data = {
            "items_to_enrich": [],
            "questions": [],
            "analysis_summary": "Already strong",
        }
        assert _appears_truncated(data, schema_type="enrichment") is False

    def test_enrichment_populated(self):
        """Populated enrichment output is not truncated."""
        data = {
            "items_to_enrich": [{"item_id": "exp_0"}],
            "questions": [{"question_id": "q_0"}],
            "analysis_summary": "Needs work",
        }
        assert _appears_truncated(data, schema_type="enrichment") is False

    # --- diff schema ---

    def test_diff_empty_changes(self):
        """Empty changes array in diff output is valid (no changes needed)."""
        data = {"changes": [], "strategy_notes": "No changes needed"}
        assert _appears_truncated(data, schema_type="diff") is False

    def test_diff_populated(self):
        """Populated diff output is not truncated."""
        data = {"changes": [{"path": "summary", "action": "replace"}]}
        assert _appears_truncated(data, schema_type="diff") is False

    # --- keywords schema ---

    def test_keywords_empty(self):
        """Empty keyword lists are valid (sparse job description)."""
        data = {"required_skills": [], "preferred_skills": [], "keywords": []}
        assert _appears_truncated(data, schema_type="keywords") is False

    # --- default / unknown schema ---

    def test_default_schema_acts_like_resume(self):
        """Default schema_type behaves like 'resume' for backwards compatibility."""
        data = {"workExperience": [], "education": [{"degree": "BS"}]}
        assert _appears_truncated(data) is True

    def test_unknown_schema_no_heuristics(self):
        """Unknown schema types have no truncation heuristics."""
        data = {"anything": []}
        assert _appears_truncated(data, schema_type="custom") is False


# ---------------------------------------------------------------------------
# complete_json JSON mode fallback
# ---------------------------------------------------------------------------


class TestCompleteJsonFallback:
    """Tests for JSON mode fallback in complete_json()."""

    @pytest.mark.asyncio
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name")
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_parse_error(
        self, mock_supports_json, mock_get_name, mock_get_router
    ):
        """When JSON mode returns invalid JSON, fallback to prompt-only mode.

        First call: JSON mode enabled -> returns malformed JSON (trailing comma)
          -> _extract_json succeeds -> json.loads fails -> JSONDecodeError
        Second call: JSON mode disabled -> returns valid JSON -> success
        """
        mock_supports_json.return_value = True
        mock_get_name.return_value = "openrouter/openai/gpt-5.4"

        # First response: malformed beyond the repair pass (missing comma
        # between members) -> json.loads fails AND _repair_json can't fix it,
        # so the JSON-mode fallback still triggers. (A merely trailing-comma
        # body is now auto-repaired and would not exercise this path.)
        bad_choice = MagicMock()
        bad_choice.message.content = '{"items_to_enrich": [] "questions": []}'
        bad_response = MagicMock()
        bad_response.choices = [bad_choice]

        # Second response: valid JSON without JSON mode
        good_choice = MagicMock()
        good_choice.message.content = '{"items_to_enrich": [], "questions": [], "analysis_summary": "ok"}'
        good_response = MagicMock()
        good_response.choices = [good_choice]

        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=[bad_response, good_response])
        config = MagicMock()
        config.provider = "openrouter"
        config.reasoning_effort = None
        mock_get_router.return_value = (router, config)

        from app.llm import complete_json

        result = await complete_json(
            prompt="Test prompt",
            schema_type="enrichment",
            retries=2,
        )

        assert result == {
            "items_to_enrich": [],
            "questions": [],
            "analysis_summary": "ok",
        }
        # Verify JSON mode was used on first call but not second
        calls = router.acompletion.call_args_list
        assert calls[0].kwargs.get("response_format") == {"type": "json_object"}
        assert "response_format" not in calls[1].kwargs

    @pytest.mark.asyncio
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name")
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_response_format_rejection(
        self, mock_supports_json, mock_get_name, mock_get_router
    ):
        """Issue #857: an OpenAI-compatible server (e.g. LM Studio) rejects
        ``response_format={"type": "json_object"}`` with a 400.

        First call: JSON mode enabled -> server raises ``BadRequestError``
          ("'response_format.type' must be 'json_schema' or 'text'").
        Second call: JSON mode disabled -> returns valid JSON -> success.

        Before the fix the 400 was re-raised immediately (the existing fallback
        only handled malformed JSON, not rejection of the parameter itself),
        so the wizard turn failed with a 500.
        """
        import litellm

        mock_supports_json.return_value = True
        mock_get_name.return_value = "openai/gemma-4-e2b"

        # First call raises the exact LM Studio rejection over the wire.
        rejection = litellm.BadRequestError(
            "OpenAIException - Error code: 400 - "
            "{'error': \"'response_format.type' must be 'json_schema' or 'text'\"}",
            model="openai/gemma-4-e2b",
            llm_provider="openai",
        )

        good_choice = MagicMock()
        good_choice.message.content = '{"answer": "ok"}'
        good_response = MagicMock()
        good_response.choices = [good_choice]

        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=[rejection, good_response])
        config = MagicMock()
        config.provider = "openai_compatible"
        config.reasoning_effort = None
        mock_get_router.return_value = (router, config)

        from app.llm import complete_json

        result = await complete_json(
            prompt="Test prompt",
            schema_type="resume",
            retries=2,
        )

        assert result == {"answer": "ok"}
        # JSON mode was sent on the first (rejected) call, dropped on the retry.
        calls = router.acompletion.call_args_list
        assert calls[0].kwargs.get("response_format") == {"type": "json_object"}
        assert "response_format" not in calls[1].kwargs

    @pytest.mark.asyncio
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name")
    @patch("app.llm._supports_json_mode")
    async def test_json_mode_fallback_on_varied_rejection_wording(
        self, mock_supports_json, mock_get_name, mock_get_router
    ):
        """The fallback must trigger across provider wording, not just LM Studio's.

        Guards against narrowing the heuristic so much that a genuine
        response_format rejection phrased as "not supported" is missed (which
        would re-introduce issue #857 for that provider).
        """
        import litellm

        mock_supports_json.return_value = True
        mock_get_name.return_value = "openai/some-local-model"

        rejection = litellm.BadRequestError(
            "OpenAIException - Error code: 400 - "
            "{'error': 'response_format json_object is not supported by this model'}",
            model="openai/some-local-model",
            llm_provider="openai",
        )

        good_choice = MagicMock()
        good_choice.message.content = '{"answer": "ok"}'
        good_response = MagicMock()
        good_response.choices = [good_choice]

        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=[rejection, good_response])
        config = MagicMock()
        config.provider = "openai_compatible"
        config.reasoning_effort = None
        mock_get_router.return_value = (router, config)

        from app.llm import complete_json

        result = await complete_json(
            prompt="Test prompt", schema_type="resume", retries=2
        )

        assert result == {"answer": "ok"}
        assert "response_format" not in router.acompletion.call_args_list[1].kwargs

    @pytest.mark.asyncio
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name")
    @patch("app.llm._supports_json_mode")
    async def test_unrelated_bad_request_is_not_swallowed(
        self, mock_supports_json, mock_get_name, mock_get_router
    ):
        """A 400 unrelated to response_format must still propagate, not retry.

        Uses a context-length error that *also names* response_format - the
        false-positive case raised in review (cubic/Kilo). Dropping JSON mode
        would not help, so the fallback must NOT fire and the error must surface.
        """
        import litellm

        mock_supports_json.return_value = True
        mock_get_name.return_value = "openai/gpt-4o"

        rejection = litellm.BadRequestError(
            "OpenAIException - Error code: 400 - {'error': 'maximum context "
            "length exceeded while using response_format=json_object'}",
            model="openai/gpt-4o",
            llm_provider="openai",
        )

        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=rejection)
        config = MagicMock()
        config.provider = "openai"
        config.reasoning_effort = None
        mock_get_router.return_value = (router, config)

        from app.llm import complete_json

        with pytest.raises(litellm.BadRequestError):
            await complete_json(prompt="Test prompt", schema_type="resume", retries=2)

        # No retry: an unrelated 400 fails fast (Router already handles retries).
        assert router.acompletion.await_count == 1


# ---------------------------------------------------------------------------
# complete() dynamic timeout
# ---------------------------------------------------------------------------


class TestCompleteDynamicTimeout:
    """Tests for complete() using _calculate_timeout()."""

    @pytest.mark.asyncio
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name")
    @patch("app.llm._calculate_timeout")
    @patch("app.llm._supports_temperature")
    async def test_uses_calculate_timeout(
        self, mock_supports_temp, mock_calc_timeout, mock_get_name, mock_get_router
    ):
        """complete() passes provider and max_tokens to _calculate_timeout."""
        mock_supports_temp.return_value = True
        mock_calc_timeout.return_value = 180
        mock_get_name.return_value = "deepseek/deepseek-chat"

        choice = MagicMock()
        choice.message.content = "Hello"
        response = MagicMock()
        response.choices = [choice]

        router = MagicMock()
        router.acompletion = AsyncMock(return_value=response)
        config = MagicMock()
        config.provider = "deepseek"
        mock_get_router.return_value = (router, config)

        from app.llm import complete

        await complete(prompt="Hi", max_tokens=8192)

        mock_calc_timeout.assert_called_once_with("completion", 8192, "deepseek")
        router.acompletion.assert_awaited_once()
        assert router.acompletion.call_args.kwargs["timeout"] == 180


class TestFinalAnswerBoundary:
    def test_reasoning_is_never_promoted_to_final_content(self):
        from app.llm import _extract_message_text, _extract_reasoning_text

        message = {
            "content": None,
            "reasoning_content": "We should produce JSON next.",
        }
        assert _extract_message_text(message) is None
        assert _extract_reasoning_text(message) == "We should produce JSON next."

    @patch("app.llm.litellm.get_model_info")
    def test_explicit_non_streaming_capability_is_honored(self, mock_info):
        from app.llm import LLMConfig, provider_supports_streaming

        mock_info.return_value = {"supports_native_streaming": False}
        config = LLMConfig(provider="openai", model="batch-only", api_key="test")
        assert provider_supports_streaming(config) is False

    @patch("app.llm.litellm.get_model_info")
    def test_unknown_streaming_capability_is_runtime_probed(self, mock_info):
        from app.llm import LLMConfig, provider_supports_streaming

        mock_info.return_value = {}
        config = LLMConfig(provider="openai_compatible", model="custom", api_key="")
        assert provider_supports_streaming(config) is True


class TestStructuredBoundaryValidation:
    @pytest.mark.asyncio
    @patch("app.llm.litellm.supports_response_schema", return_value=False)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/test")
    @patch("app.llm._supports_json_mode", return_value=False)
    async def test_schema_invalid_response_retries_then_succeeds(
        self, _json_mode, _model_name, mock_get_router, _schema_support
    ):
        from pydantic import BaseModel
        from app.llm import complete_json

        class Payload(BaseModel):
            names: list[str]

        invalid = MagicMock()
        invalid.message.content = '{"names": "not-a-list"}'
        valid = MagicMock()
        valid.message.content = '{"names": ["Ada"]}'
        responses = []
        for choice in (invalid, valid):
            response = MagicMock()
            response.choices = [choice]
            responses.append(response)

        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=responses)
        config = MagicMock(provider="openai", reasoning_effort=None)
        mock_get_router.return_value = (router, config)

        result = await complete_json(
            "Return names", response_model=Payload, schema_type="custom", retries=1
        )
        assert result == {"names": ["Ada"]}
        assert router.acompletion.await_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.litellm.supports_response_schema", return_value=True)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/test")
    @patch("app.llm._supports_json_mode", return_value=True)
    async def test_native_json_schema_is_sent_when_supported(
        self, _json_mode, _model_name, mock_get_router, _schema_support
    ):
        from pydantic import BaseModel
        from app.llm import complete_json

        class Payload(BaseModel):
            answer: str

        choice = MagicMock()
        choice.message.content = '{"answer": "ok"}'
        response = MagicMock(choices=[choice])
        router = MagicMock()
        router.acompletion = AsyncMock(return_value=response)
        mock_get_router.return_value = (
            router,
            MagicMock(provider="openai", reasoning_effort=None),
        )

        await complete_json("Answer", response_model=Payload, schema_type="custom")
        response_format = router.acompletion.call_args.kwargs["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["schema"]["type"] == "object"


class TestReasoningEffortCapability:
    def test_omits_effort_when_not_set(self):
        from app.llm import _supports_reasoning_effort

        assert _supports_reasoning_effort("openai/custom", None) is False
        assert _supports_reasoning_effort("openai/custom", "") is False

    def test_honors_explicit_effort_for_custom_endpoints(self):
        # Fix 14: custom endpoints (openai_compatible -> "openai/", ollama)
        # honor an explicit effort regardless of the registry's guess;
        # drop_params guards endpoints that don't accept it.
        from app.llm import _supports_reasoning_effort

        assert _supports_reasoning_effort("openai/deepseek-v4-flash-free", "medium") is True
        assert _supports_reasoning_effort("ollama_chat/gemma3", "high") is True

    @patch("app.llm.litellm.get_model_info", side_effect=RuntimeError("unknown"))
    def test_omits_effort_for_unknown_first_party_model(self, _mock_info):
        # A bare (first-party OpenAI-style) unknown model stays conservative.
        from app.llm import _supports_reasoning_effort

        assert _supports_reasoning_effort("gpt-6-experimental", "medium") is False

    @patch("app.llm.litellm.get_model_info")
    def test_registry_governs_first_party_models(self, mock_info):
        from app.llm import _supports_reasoning_effort

        # No prefix -> registry path. Unsupported -> omit; supported -> honor.
        mock_info.return_value = {"supported_openai_params": ["temperature"]}
        assert _supports_reasoning_effort("gpt-3.5-turbo", "medium") is False

        mock_info.return_value = {
            "supported_openai_params": ["reasoning_effort"],
            "supports_reasoning": True,
            "supports_minimal_reasoning_effort": True,
        }
        assert _supports_reasoning_effort("o3", "minimal") is True
        assert _supports_reasoning_effort("o3", "medium") is True


class TestProviderErrorClassification:
    def test_authentication_and_rate_limit_are_actionable(self):
        import litellm
        from app.llm import classify_llm_error

        auth = litellm.AuthenticationError(
            "secret upstream detail", model="test", llm_provider="openai"
        )
        status, code, message, retryable = classify_llm_error(auth)
        assert (status, code, retryable) == (424, "llm_authentication_failed", False)
        assert "secret upstream detail" not in message

        limited = litellm.RateLimitError(
            "quota", model="test", llm_provider="openai"
        )
        assert classify_llm_error(limited)[:2] == (429, "llm_rate_limited")

    def test_wrapped_timeout_is_retryable(self):
        from app.llm import classify_llm_error

        try:
            try:
                raise TimeoutError("upstream timeout")
            except TimeoutError as cause:
                raise ValueError("wrapped") from cause
        except ValueError as wrapped:
            status, code, _message, retryable = classify_llm_error(wrapped)
        assert (status, code, retryable) == (504, "llm_timeout", True)


def test_invalid_provider_content_has_stable_api_error_contract():
    from app.llm import classify_llm_error, llm_api_error

    exc = ValueError("rejected provider body containing private details")
    assert classify_llm_error(exc)[:2] == (422, "llm_response_invalid")

    api_error = llm_api_error(exc, stage="wizard", details={"item_id": "exp_0"})
    assert api_error.status_code == 422
    assert api_error.code == "llm_response_invalid"
    assert api_error.details == {
        "stage": "wizard",
        "retryable": True,
        "item_id": "exp_0",
    }
    assert "private details" not in api_error.message


# ---------------------------------------------------------------------------
# Reasoning-model budget handling (fixes 1, 2, 3)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _fake_choice(content, *, finish_reason="stop", reasoning=None):
    """A completion choice with explicit fields (avoids MagicMock truthiness)."""
    message = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(
        message=message, text=None, delta=None, finish_reason=finish_reason
    )


def _fake_response(content, *, finish_reason="stop", reasoning=None, model="m"):
    return SimpleNamespace(
        choices=[_fake_choice(content, finish_reason=finish_reason, reasoning=reasoning)],
        model=model,
        usage=SimpleNamespace(total_tokens=0),
    )


class TestReasoningBudgetHealthCheck:
    """Fix 1 & 2: health check is reasoning-aware and uses a real budget."""

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=512)
    @patch("app.llm.litellm.acompletion", new_callable=AsyncMock)
    async def test_reasoning_truncation_is_healthy_with_warning(self, mock_acomp, _safe):
        from app.llm import LLMConfig, check_llm_health

        mock_acomp.return_value = _fake_response(
            "", finish_reason="length", reasoning="internal reasoning..."
        )
        cfg = LLMConfig(
            provider="openai_compatible",
            model="deepseek-r1",
            api_key="",
            api_base="http://local/v1",
        )
        res = await check_llm_health(cfg)
        assert res["healthy"] is True
        assert res["warning_code"] == "reasoning_truncated"
        # The probe must use the larger clamped budget, never the old 64.
        assert mock_acomp.call_args.kwargs["max_tokens"] == 512

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=512)
    @patch("app.llm.litellm.acompletion", new_callable=AsyncMock)
    async def test_empty_without_reasoning_stays_unhealthy(self, mock_acomp, _safe):
        from app.llm import LLMConfig, check_llm_health

        # finish_reason=length but NO reasoning -> genuinely empty -> unhealthy.
        mock_acomp.return_value = _fake_response("", finish_reason="length", reasoning=None)
        cfg = LLMConfig(
            provider="openai_compatible", model="m", api_key="", api_base="http://local/v1"
        )
        res = await check_llm_health(cfg)
        assert res["healthy"] is False
        assert res["error_code"] == "empty_content"

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=512)
    @patch("app.llm.litellm.acompletion", new_callable=AsyncMock)
    async def test_probe_uses_clamped_budget_not_a_tiny_fixed_value(self, mock_acomp, safe):
        # Fix 10: the probe budget comes from get_safe_max_tokens (parity with
        # real calls), never the old hardcoded 64.
        from app.llm import HEALTH_CHECK_MAX_TOKENS, LLMConfig, check_llm_health

        mock_acomp.return_value = _fake_response("pong")
        cfg = LLMConfig(provider="openai", model="gpt-4", api_key="k")
        res = await check_llm_health(cfg)
        assert res["healthy"] is True
        safe.assert_called_once_with("gpt-4", HEALTH_CHECK_MAX_TOKENS)
        assert mock_acomp.call_args.kwargs["max_tokens"] == 512


class TestReasoningBudgetCompletion:
    """Fix 2 & 3: empty content + finish_reason=length auto-bumps the budget."""

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=2108)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/deepseek-r1")
    @patch("app.llm._supports_temperature", return_value=False)
    @patch("app.llm._supports_reasoning_effort", return_value=False)
    async def test_complete_retries_with_larger_budget(
        self, _re, _temp, _name, mock_get_router, _safe
    ):
        from app.llm import complete

        router = MagicMock()
        router.acompletion = AsyncMock(
            side_effect=[
                _fake_response("", finish_reason="length"),
                _fake_response("Working!", finish_reason="stop"),
            ]
        )
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        result = await complete("Hi", max_tokens=60)
        assert result == "Working!"
        assert router.acompletion.await_count == 2
        # First attempt used the tiny caller budget; the retry used the bump.
        assert router.acompletion.call_args_list[0].kwargs["max_tokens"] == 60
        assert router.acompletion.call_args_list[1].kwargs["max_tokens"] == 2108

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=999)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/gpt-x")
    @patch("app.llm._supports_temperature", return_value=False)
    @patch("app.llm._supports_reasoning_effort", return_value=False)
    async def test_complete_recovers_when_empty_then_content(
        self, _re, _temp, _name, mock_get_router, _safe
    ):
        # A genuine empty stop is now RETRIED (free models are non-deterministic),
        # so a following non-empty response succeeds instead of hard-failing.
        from app.llm import complete

        router = MagicMock()
        router.acompletion = AsyncMock(
            side_effect=[
                _fake_response("", finish_reason="stop"),
                _fake_response("recovered", finish_reason="stop"),
            ]
        )
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        assert await complete("Hi", max_tokens=60) == "recovered"
        assert router.acompletion.await_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.litellm.supports_response_schema", return_value=False)
    @patch("app.llm._supports_json_mode", return_value=False)
    @patch("app.llm.get_safe_max_tokens", return_value=6144)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/deepseek-r1")
    @patch("app.llm._supports_reasoning_effort", return_value=False)
    async def test_complete_json_bumps_budget_on_empty_length(
        self, _re, _name, mock_get_router, _safe, _jsonmode, _schema
    ):
        from app.llm import complete_json

        router = MagicMock()
        router.acompletion = AsyncMock(
            side_effect=[
                _fake_response("", finish_reason="length"),
                _fake_response('{"ok": true}', finish_reason="stop"),
            ]
        )
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        result = await complete_json("go", max_tokens=4096, retries=1)
        assert result == {"ok": True}
        assert router.acompletion.await_count == 2
        assert router.acompletion.call_args_list[1].kwargs["max_tokens"] == 6144


# ---------------------------------------------------------------------------
# Router LRU cache (fix 9)
# ---------------------------------------------------------------------------


class TestRouterCache:
    def test_router_cached_per_config_and_reused(self, monkeypatch):
        import app.llm as llm

        llm._router_cache.clear()
        built: list[str] = []

        def fake_build(cfg):
            built.append(llm._config_fingerprint(cfg))
            return object()

        monkeypatch.setattr(llm, "_build_router", fake_build)
        a = llm.LLMConfig(provider="openai", model="m1", api_key="k1")
        b = llm.LLMConfig(provider="openai", model="m2", api_key="k2")

        r_a1, _ = llm.get_router(a)
        r_b, _ = llm.get_router(b)
        r_a2, _ = llm.get_router(a)  # must reuse, not rebuild (no thrash)

        assert r_a1 is r_a2
        assert r_b is not r_a1
        # Each distinct config built exactly once despite interleaving.
        assert built.count(llm._config_fingerprint(a)) == 1
        assert len(built) == 2
        llm._router_cache.clear()

    def test_router_cache_evicts_least_recently_used(self, monkeypatch):
        import app.llm as llm

        llm._router_cache.clear()
        monkeypatch.setattr(llm, "_build_router", lambda cfg: object())
        monkeypatch.setattr(llm, "_ROUTER_CACHE_MAX", 3)
        for i in range(5):
            llm.get_router(llm.LLMConfig(provider="openai", model=f"m{i}", api_key="k"))
        assert len(llm._router_cache) == 3
        llm._router_cache.clear()


# ---------------------------------------------------------------------------
# Central max_tokens clamp (fix 11)
# ---------------------------------------------------------------------------


class TestCentralClamp:
    @pytest.mark.asyncio
    @patch("app.llm.litellm.supports_response_schema", return_value=False)
    @patch("app.llm.litellm.get_model_info", return_value={"max_output_tokens": 1000})
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="known/model")
    async def test_complete_json_clamps_first_attempt_to_known_limit(
        self, _name, mock_get_router, _info, _schema
    ):
        from app.llm import complete_json

        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_fake_response('{"ok": true}'))
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        result = await complete_json("go", max_tokens=8192, retries=0)
        assert result == {"ok": True}
        # Caller asked for 8192; the KNOWN model cap (1000) is enforced.
        assert router.acompletion.call_args.kwargs["max_tokens"] == 1000

    @patch("app.llm.litellm.get_model_info", return_value={})
    def test_unknown_model_keeps_requested_budget(self, _info):
        from app.llm import _clamp_to_model_limit

        # Custom/self-hosted (unknown) model must NOT be shrunk to the fallback.
        assert _clamp_to_model_limit("openai/custom", 8192) == 8192

    @patch("app.llm.litellm.get_model_info", return_value={"max_output_tokens": 4096})
    def test_known_model_clamps_down_only(self, _info):
        from app.llm import _clamp_to_model_limit

        assert _clamp_to_model_limit("known/model", 8192) == 4096
        assert _clamp_to_model_limit("known/model", 2048) == 2048  # never raises it


# ---------------------------------------------------------------------------
# Timeout provider factors (fix 12)
# ---------------------------------------------------------------------------


class TestCalculateTimeoutProviders:
    def test_custom_and_reasoning_providers_have_factors(self):
        from app.llm import _calculate_timeout, LLM_TIMEOUT_COMPLETION

        base = LLM_TIMEOUT_COMPLETION  # token_factor is 1.0 at 4096
        assert _calculate_timeout("completion", 4096, "openai_compatible") == int(base * 1.5)
        assert _calculate_timeout("completion", 4096, "deepseek") == int(base * 1.5)
        assert _calculate_timeout("completion", 4096, "gemini") == int(base * 1.2)
        # A still-unknown provider keeps the 1.0 default (no regression).
        assert _calculate_timeout("completion", 4096, "somethingelse") == base


# ---------------------------------------------------------------------------
# Reliability layer: JSON repair, lenient parse, empty-content retry
# ---------------------------------------------------------------------------


class TestJsonRepair:
    def test_trailing_comma(self):
        import json
        from app.llm import _repair_json

        assert json.loads(_repair_json('{"a": 1, "b": [1, 2,],}')) == {"a": 1, "b": [1, 2]}

    def test_surrounding_prose(self):
        import json
        from app.llm import _repair_json

        raw = 'Sure! Here is the JSON:\n{"name": "Ada"}\nHope that helps.'
        assert json.loads(_repair_json(raw)) == {"name": "Ada"}

    def test_code_fence(self):
        import json
        from app.llm import _repair_json

        assert json.loads(_repair_json('```json\n{"a": 1}\n```')) == {"a": 1}

    def test_truncated_object_and_array(self):
        import json
        from app.llm import _repair_json

        # Truncated mid-array -> repair closes the array and object.
        assert json.loads(_repair_json('{"a": 1, "b": [1, 2, 3')) == {"a": 1, "b": [1, 2, 3]}

    def test_truncated_string(self):
        import json
        from app.llm import _repair_json

        assert json.loads(_repair_json('{"a": "hello')) == {"a": "hello"}

    def test_smart_quotes(self):
        import json
        from app.llm import _repair_json

        assert json.loads(_repair_json('{\u201ckey\u201d: \u201cvalue\u201d}')) == {"key": "value"}

    def test_loads_lenient_passes_through_valid(self):
        from app.llm import _loads_lenient

        assert _loads_lenient('{"a": 1}') == {"a": 1}


class TestCompleteJsonRepairRecovery:
    @pytest.mark.asyncio
    @patch("app.llm.litellm.supports_response_schema", return_value=False)
    @patch("app.llm._supports_json_mode", return_value=False)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/free-model")
    async def test_malformed_json_recovered_without_extra_call(
        self, _name, mock_get_router, _jsonmode, _schema
    ):
        from app.llm import complete_json

        # Model returns JSON with a trailing comma - previously a hard parse
        # failure that burned a retry; now repaired in place on attempt 1.
        router = MagicMock()
        router.acompletion = AsyncMock(
            return_value=_fake_response('{"required_skills": ["Python"],}')
        )
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        result = await complete_json("Extract", schema_type="keywords", retries=2)
        assert result == {"required_skills": ["Python"]}
        assert router.acompletion.await_count == 1  # repaired, no wasted retry


class TestCompleteEmptyRetry:
    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=4096)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/free-model")
    @patch("app.llm._supports_temperature", return_value=True)
    @patch("app.llm._supports_reasoning_effort", return_value=False)
    async def test_genuine_empty_is_retried_then_succeeds(
        self, _re, _temp, _name, mock_get_router, _safe
    ):
        from app.llm import complete

        # First: empty content on a normal stop (non-deterministic free model).
        # Second: real content. complete() must retry rather than fail.
        router = MagicMock()
        router.acompletion = AsyncMock(
            side_effect=[
                _fake_response("", finish_reason="stop"),
                _fake_response("Working!", finish_reason="stop"),
            ]
        )
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        out = await complete("Say hi", max_tokens=1024)
        assert out == "Working!"
        assert router.acompletion.await_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.get_safe_max_tokens", return_value=4096)
    @patch("app.llm.get_router")
    @patch("app.llm.get_model_name", return_value="openai/free-model")
    @patch("app.llm._supports_temperature", return_value=True)
    @patch("app.llm._supports_reasoning_effort", return_value=False)
    async def test_persistent_empty_fails_after_three_attempts(
        self, _re, _temp, _name, mock_get_router, _safe
    ):
        from app.llm import complete

        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_fake_response("", finish_reason="stop"))
        mock_get_router.return_value = (router, MagicMock(provider="openai", reasoning_effort=None))

        with pytest.raises(ValueError):
            await complete("Say hi", max_tokens=1024)
        assert router.acompletion.await_count == 3


# ---------------------------------------------------------------------------
# api_base only applies to custom-endpoint providers (Gemini 404 fix)
# ---------------------------------------------------------------------------


class TestApiBaseProviderGating:
    def test_provider_uses_custom_base(self):
        from app.llm import provider_uses_custom_base

        assert provider_uses_custom_base("openai_compatible") is True
        assert provider_uses_custom_base("ollama") is True
        for cloud in ("openai", "anthropic", "gemini", "openrouter", "deepseek", "groq"):
            assert provider_uses_custom_base(cloud) is False

    @patch("app.llm.load_config_file")
    def test_get_llm_config_ignores_base_for_cloud_provider(self, mock_load):
        from app.llm import get_llm_config

        # A stale base left over from openai_compatible must NOT reach gemini.
        mock_load.return_value = {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "api_base": "https://opencode.ai/zen/v1",
            "api_keys": {},
        }
        cfg = get_llm_config()
        assert cfg.provider == "gemini"
        assert cfg.api_base is None  # leaked base ignored -> uses Google default

    @patch("app.llm.load_config_file")
    def test_get_llm_config_keeps_base_for_custom_provider(self, mock_load):
        from app.llm import get_llm_config

        mock_load.return_value = {
            "provider": "openai_compatible",
            "model": "deepseek-v4-flash-free",
            "api_base": "https://opencode.ai/zen/v1",
            "api_keys": {},
        }
        cfg = get_llm_config()
        assert cfg.api_base == "https://opencode.ai/zen/v1"


class TestStructuredOutputProbe:
    """check_structured_output() - the capability verdict that predicts whether
    JSON-dependent features (resume tailoring) will work on a model."""

    @pytest.mark.asyncio
    @patch("app.llm.complete_json", new_callable=AsyncMock)
    async def test_reliable_when_all_attempts_pass(self, mock_cj):
        from app.llm import LLMConfig, check_structured_output

        mock_cj.return_value = {"ok": True, "tags": ["a", "b"]}
        cfg = LLMConfig(provider="openai", model="good", api_key="k")
        res = await check_structured_output(cfg, attempts=2)
        assert res["structured_ok"] is True
        assert res["structured_verdict"] == "reliable"
        assert res["structured_successes"] == 2
        assert mock_cj.await_count == 2

    @pytest.mark.asyncio
    @patch("app.llm.complete_json", new_callable=AsyncMock)
    async def test_unsupported_when_all_attempts_return_invalid_json(self, mock_cj):
        from app.llm import LLMConfig, check_structured_output

        # ValueError -> classify_llm_error -> llm_response_invalid (content).
        mock_cj.side_effect = ValueError("not json")
        cfg = LLMConfig(provider="openai_compatible", model="weak", api_key="")
        res = await check_structured_output(cfg, attempts=2)
        assert res["structured_ok"] is False
        assert res["structured_verdict"] == "unsupported"
        assert res["structured_successes"] == 0

    @pytest.mark.asyncio
    @patch("app.llm.complete_json", new_callable=AsyncMock)
    async def test_flaky_when_some_attempts_pass(self, mock_cj):
        from app.llm import LLMConfig, check_structured_output

        mock_cj.side_effect = [ValueError("bad json"), {"ok": True, "tags": ["a", "b"]}]
        cfg = LLMConfig(provider="openai", model="mid", api_key="k")
        res = await check_structured_output(cfg, attempts=2)
        assert res["structured_ok"] is True
        assert res["structured_verdict"] == "flaky"
        assert res["structured_successes"] == 1

    @pytest.mark.asyncio
    @patch("app.llm.complete_json", new_callable=AsyncMock)
    async def test_connection_error_short_circuits_to_unknown(self, mock_cj):
        from app.llm import LLMConfig, check_structured_output

        # A timeout is NOT a structured-output problem -> verdict "unknown" with
        # the classified connection reason, not a capability judgement.
        mock_cj.side_effect = TimeoutError()
        cfg = LLMConfig(provider="openai", model="m", api_key="k")
        res = await check_structured_output(cfg, attempts=2)
        assert res["structured_ok"] is None
        assert res["structured_verdict"] == "unknown"
        assert res["structured_error_code"] == "llm_timeout"
        # Short-circuits on the first non-content error.
        assert mock_cj.await_count == 1
