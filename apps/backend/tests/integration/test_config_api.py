"""Integration tests for configuration endpoints."""

from unittest.mock import patch, AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestLlmConfig:
    """GET/PUT /api/v1/config/llm-api-key"""

    @patch("app.routers.config._load_config")
    async def test_get_llm_config(self, mock_load, client):
        mock_load.return_value = {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "sk-1234567890abcdef",
            "api_base": None,
        }
        async with client:
            resp = await client.get("/api/v1/config/llm-api-key")
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "openai"
        # API key should be masked
        assert "****" in data["api_key"] or "*" in data["api_key"]

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_llm_config(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put("/api/v1/config/llm-api-key", json={
                "provider": "anthropic",
                "model": "claude-3-sonnet",
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["provider"] == "anthropic"

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_llm_config_does_not_persist_api_key(
        self, mock_load, mock_save, mock_log_health, client
    ):
        # PUT /llm-api-key persists provider/model/api_base but NO LONGER the
        # api_key - keys live in the encrypted per-provider store (PUT
        # /config/api-keys). Writing the legacy single slot here is exactly what
        # made providers overwrite each other, so it must not happen.
        mock_load.return_value = {}
        async with client:
            resp = await client.put("/api/v1/config/llm-api-key", json={
                "provider": "openai_compatible",
                "model": "llama-3.1-8b",
                "api_key": "local-secret-key",
                "api_base": "http://localhost:8080/v1",
            })

        assert resp.status_code == 200
        saved_config = mock_save.call_args.args[0]
        assert saved_config["provider"] == "openai_compatible"
        assert saved_config["model"] == "llama-3.1-8b"
        assert saved_config["api_base"] == "http://localhost:8080/v1"
        # The provided key is ignored for persistence (the bug fix).
        assert saved_config.get("api_key") != "local-secret-key"
        data = resp.json()
        assert data["provider"] == "openai_compatible"
        assert data["api_base"] == "http://localhost:8080/v1"

    # --- Base URL clearing (issue #760) ----------------------------------
    # The frontend sends api_base: null (or "") when the field is cleared.
    # A null/blank value that is *present* in the request must clear the
    # stored override; an *omitted* field must leave it unchanged.

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_null_api_base_clears_stale_value(
        self, mock_load, mock_save, mock_log_health, client
    ):
        mock_load.return_value = {
            "provider": "openrouter",
            "model": "x",
            "api_base": "http://stale.example/v1",
        }
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openrouter", "api_base": None},
            )
        assert resp.status_code == 200
        saved_config = mock_save.call_args.args[0]
        assert saved_config["api_base"] is None
        assert resp.json()["api_base"] is None

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_blank_api_base_is_normalized_to_none(
        self, mock_load, mock_save, mock_log_health, client
    ):
        mock_load.return_value = {"provider": "openrouter", "api_base": "http://stale/v1"}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openrouter", "api_base": "   "},
            )
        assert resp.status_code == 200
        assert mock_save.call_args.args[0]["api_base"] is None

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_omitting_api_base_leaves_it_unchanged(
        self, mock_load, mock_save, mock_log_health, client
    ):
        # Custom-base provider: omitting api_base must leave the saved base
        # untouched (the base legitimately belongs to this provider).
        mock_load.return_value = {"provider": "openai_compatible", "api_base": "http://keep/v1"}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"model": "new-model"},  # api_base intentionally omitted
            )
        assert resp.status_code == 200
        assert mock_save.call_args.args[0]["api_base"] == "http://keep/v1"
        mock_log_health.assert_awaited_once()

    # --- Gemini 404 bug: a stale Base URL must NOT leak to a cloud provider ---
    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_switching_to_cloud_provider_drops_stale_base(
        self, mock_load, mock_save, mock_log_health, client
    ):
        # User was on openai_compatible (base saved), then switches to gemini.
        mock_load.return_value = {
            "provider": "openai_compatible",
            "model": "deepseek-v4-flash-free",
            "api_base": "https://opencode.ai/zen/v1",
        }
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "gemini", "model": "gemini-2.5-flash"},
            )
        assert resp.status_code == 200
        # Stored base cleared, and the response never reports the leaked base.
        assert mock_save.call_args.args[0]["api_base"] is None
        assert resp.json()["api_base"] is None

    # --- Fix 7: provider is validated against the supported set -----------
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_rejects_unknown_provider(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "not-a-provider", "model": "x"},
            )
        assert resp.status_code == 422
        mock_save.assert_not_called()

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_accepts_every_supported_provider(
        self, mock_load, mock_save, _health, client
    ):
        mock_load.return_value = {}
        providers = [
            "openai",
            "openai_compatible",
            "anthropic",
            "openrouter",
            "gemini",
            "deepseek",
            "groq",
            "ollama",
        ]
        async with client:
            for p in providers:
                resp = await client.put(
                    "/api/v1/config/llm-api-key", json={"provider": p, "model": "m"}
                )
                assert resp.status_code == 200, p

    # --- Fix 8: api_base must be a valid http(s) URL when provided --------
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_rejects_malformed_api_base(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            for bad in ("not a url", "ftp://host/v1", "justtext", "://missing-scheme"):
                resp = await client.put(
                    "/api/v1/config/llm-api-key",
                    json={"provider": "openai_compatible", "model": "m", "api_base": bad},
                )
                assert resp.status_code == 422, bad
        mock_save.assert_not_called()

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_accepts_valid_api_base(self, mock_load, mock_save, _health, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={
                    "provider": "openai_compatible",
                    "model": "m",
                    "api_base": "https://opencode.ai/zen/v1",
                },
            )
        assert resp.status_code == 200
        assert mock_save.call_args.args[0]["api_base"] == "https://opencode.ai/zen/v1"

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_rejects_blank_model(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openai", "model": "   "},
            )
        assert resp.status_code == 422
        mock_save.assert_not_called()

    # --- Fix 6: reasoning_effort round-trips through PUT ------------------
    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_persists_reasoning_effort(
        self, mock_load, mock_save, _health, client
    ):
        mock_load.return_value = {"provider": "openai", "model": "gpt-5"}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openai", "model": "gpt-5", "reasoning_effort": "high"},
            )
        assert resp.status_code == 200
        assert mock_save.call_args.args[0]["reasoning_effort"] == "high"
        assert resp.json()["reasoning_effort"] == "high"

    @patch("app.routers.config._log_llm_health_check", new_callable=AsyncMock)
    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_clears_reasoning_effort_with_empty_string(
        self, mock_load, mock_save, _health, client
    ):
        mock_load.return_value = {
            "provider": "openai",
            "model": "gpt-5",
            "reasoning_effort": "high",
        }
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openai", "model": "gpt-5", "reasoning_effort": ""},
            )
        assert resp.status_code == 200
        # Persisted as "" (so the gpt-5 auto-migration does not re-fire) and
        # surfaced as null in the response.
        assert mock_save.call_args.args[0]["reasoning_effort"] == ""
        assert resp.json()["reasoning_effort"] is None

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_rejects_invalid_reasoning_effort(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put(
                "/api/v1/config/llm-api-key",
                json={"provider": "openai", "model": "gpt-5", "reasoning_effort": "extreme"},
            )
        assert resp.status_code == 422
        mock_save.assert_not_called()


class TestLlmTest:
    """POST /api/v1/config/llm-test"""

    @pytest.fixture(autouse=True)
    def _stub_structured_probe(self):
        # The endpoint runs a structured-output probe after a healthy connection;
        # stub it so these connectivity tests stay hermetic (no real provider).
        with patch(
            "app.routers.config.check_structured_output", new_callable=AsyncMock
        ) as m:
            m.return_value = {
                "structured_ok": True,
                "structured_verdict": "reliable",
                "structured_attempts": 2,
                "structured_successes": 2,
                "structured_message": "ok",
            }
            yield m

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_success(self, mock_load, mock_health, client):
        mock_load.return_value = {"provider": "openai", "model": "gpt-4", "api_key": "sk-test"}
        mock_health.return_value = {
            "healthy": True,
            "provider": "openai",
            "model": "gpt-4",
            "test_prompt": "Hi",
            "model_output": "Hello!",
        }
        async with client:
            resp = await client.post("/api/v1/config/llm-test")
        assert resp.status_code == 200
        assert resp.json()["healthy"] is True

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_failure(self, mock_load, mock_health, client):
        mock_load.return_value = {}
        mock_health.return_value = {
            "healthy": False,
            "error_code": "api_key_missing",
        }
        async with client:
            resp = await client.post("/api/v1/config/llm-test")
        assert resp.status_code == 200
        assert resp.json()["healthy"] is False

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_uses_request_api_key_for_optional_provider(
        self, mock_load, mock_health, client
    ):
        mock_load.return_value = {
            "provider": "openai_compatible",
            "model": "stored-model",
            "api_key": "stored-key",
            "api_base": "http://localhost:8080/v1",
        }
        mock_health.return_value = {
            "healthy": True,
            "provider": "openai_compatible",
            "model": "llama-3.1-8b",
            "test_prompt": "Hi",
            "model_output": "Hello!",
        }
        async with client:
            resp = await client.post("/api/v1/config/llm-test", json={
                "provider": "openai_compatible",
                "model": "llama-3.1-8b",
                "api_key": "typed-local-key",
                "api_base": "http://localhost:1234/v1",
            })

        assert resp.status_code == 200
        config = mock_health.await_args.args[0]
        assert config.provider == "openai_compatible"
        assert config.model == "llama-3.1-8b"
        assert config.api_key == "typed-local-key"
        assert config.api_base == "http://localhost:1234/v1"

    # --- Fix 13: llm-test honors reasoning_effort (test == prod behavior) ---
    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_uses_request_reasoning_effort(
        self, mock_load, mock_health, client
    ):
        mock_load.return_value = {"provider": "openai", "model": "gpt-5"}
        mock_health.return_value = {"healthy": True, "provider": "openai", "model": "gpt-5"}
        async with client:
            resp = await client.post(
                "/api/v1/config/llm-test",
                json={"provider": "openai", "model": "gpt-5", "reasoning_effort": "high"},
            )
        assert resp.status_code == 200
        config = mock_health.call_args.args[0]
        assert config.reasoning_effort == "high"

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_falls_back_to_stored_reasoning_effort(
        self, mock_load, mock_health, client
    ):
        mock_load.return_value = {
            "provider": "openai",
            "model": "gpt-5",
            "reasoning_effort": "low",
        }
        mock_health.return_value = {"healthy": True, "provider": "openai", "model": "gpt-5"}
        async with client:
            resp = await client.post(
                "/api/v1/config/llm-test",
                json={"provider": "openai", "model": "gpt-5"},
            )
        assert resp.status_code == 200
        assert mock_health.call_args.args[0].reasoning_effort == "low"

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_connection_test_falls_back_to_stored_key_when_request_omits_it(
        self, mock_load, mock_health, client
    ):
        mock_load.return_value = {
            "provider": "openai_compatible",
            "model": "stored-model",
            "api_key": "stored-local-key",
            "api_base": "http://localhost:8080/v1",
        }
        mock_health.return_value = {
            "healthy": True,
            "provider": "openai_compatible",
            "model": "llama-3.1-8b",
            "test_prompt": "Hi",
            "model_output": "Hello!",
        }
        async with client:
            resp = await client.post("/api/v1/config/llm-test", json={
                "provider": "openai_compatible",
                "model": "llama-3.1-8b",
                "api_base": "http://localhost:8080/v1",
            })

        assert resp.status_code == 200
        config = mock_health.await_args.args[0]
        assert config.provider == "openai_compatible"
        assert config.model == "llama-3.1-8b"
        assert config.api_key == "stored-local-key"
        assert config.api_base == "http://localhost:8080/v1"

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_healthy_connection_merges_structured_verdict(
        self, mock_load, mock_health, client
    ):
        """A connected-but-weak model surfaces a clear structured-output verdict
        + warning, so the user learns it may fail BEFORE tailoring."""
        mock_load.return_value = {"provider": "openai_compatible", "model": "weak"}
        mock_health.return_value = {"healthy": True, "provider": "openai_compatible", "model": "weak"}
        with patch(
            "app.routers.config.check_structured_output", new_callable=AsyncMock
        ) as probe:
            probe.return_value = {
                "structured_ok": False,
                "structured_verdict": "unsupported",
                "structured_attempts": 2,
                "structured_successes": 0,
                "structured_message": "This model failed to return valid structured output.",
            }
            async with client:
                resp = await client.post("/api/v1/config/llm-test")
        body = resp.json()
        assert body["healthy"] is True  # connection is fine
        assert body["structured_verdict"] == "unsupported"
        assert body["structured_ok"] is False
        # Elevated to a visible warning even though the connection works.
        assert body["warning_code"] == "structured_output_unsupported"
        assert body["warning"]

    @patch("app.routers.config.check_llm_health", new_callable=AsyncMock)
    @patch("app.routers.config._load_config")
    async def test_unhealthy_connection_skips_structured_probe(
        self, mock_load, mock_health, client
    ):
        mock_load.return_value = {}
        mock_health.return_value = {"healthy": False, "error_code": "api_key_missing"}
        with patch(
            "app.routers.config.check_structured_output", new_callable=AsyncMock
        ) as probe:
            async with client:
                resp = await client.post("/api/v1/config/llm-test")
        assert resp.json()["healthy"] is False
        probe.assert_not_awaited()


class TestFeatureConfig:
    """GET/PUT /api/v1/config/features"""

    @patch("app.routers.config._load_config")
    async def test_get_features(self, mock_load, client):
        mock_load.return_value = {
            "enable_cover_letter": True,
            "enable_outreach_message": False,
            "enable_interview_prep": True,
        }
        async with client:
            resp = await client.get("/api/v1/config/features")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enable_cover_letter"] is True
        assert data["enable_outreach_message"] is False
        assert data["enable_interview_prep"] is True

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_features(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put("/api/v1/config/features", json={
                "enable_cover_letter": True,
                "enable_interview_prep": True,
            })
        assert resp.status_code == 200
        data = resp.json()
        assert data["enable_cover_letter"] is True
        assert data["enable_interview_prep"] is True
        saved = mock_save.call_args.args[0]
        assert saved["enable_cover_letter"] is True
        assert saved["enable_interview_prep"] is True


class TestFeaturePrompts:
    """GET/PUT /api/v1/config/feature-prompts"""

    @patch("app.routers.config._load_config")
    async def test_get_feature_prompts(self, mock_load, client):
        mock_load.return_value = {
            "cover_letter_prompt": "Custom cover prompt",
            "outreach_message_prompt": "",
        }
        async with client:
            resp = await client.get("/api/v1/config/feature-prompts")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cover_letter_prompt"] == "Custom cover prompt"
        assert data["outreach_message_prompt"] == ""
        assert "{job_description}" in data["cover_letter_default"]
        assert "{resume_data}" in data["cover_letter_default"]
        assert "{output_language}" in data["cover_letter_default"]
        assert "{job_description}" in data["outreach_message_default"]
        assert "{resume_data}" in data["outreach_message_default"]
        assert "{output_language}" in data["outreach_message_default"]

    @patch("app.routers.config._load_config")
    async def test_put_feature_prompts_rejects_missing_placeholders(self, mock_load, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put("/api/v1/config/feature-prompts", json={
                "cover_letter_prompt": "Use {job_description} only",
            })

        assert resp.status_code == 422
        assert resp.json()["detail"] == {
            "code": "missing_placeholders",
            "field": "cover_letter_prompt",
            "missing": ["{resume_data}", "{output_language}"],
        }

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_feature_prompts_strips_and_clears_values(
        self, mock_load, mock_save, client
    ):
        mock_load.return_value = {
            "cover_letter_prompt": "Old cover prompt",
            "outreach_message_prompt": "Old outreach prompt",
        }
        async with client:
            resp = await client.put("/api/v1/config/feature-prompts", json={
                "cover_letter_prompt": "  {job_description}\n{resume_data}\n{output_language}\n  ",
                "outreach_message_prompt": "   ",
            })

        assert resp.status_code == 200
        saved_config = mock_save.call_args.args[0]
        assert saved_config["cover_letter_prompt"] == (
            "{job_description}\n{resume_data}\n{output_language}"
        )
        assert saved_config["outreach_message_prompt"] == ""
        data = resp.json()
        assert data["cover_letter_prompt"] == (
            "{job_description}\n{resume_data}\n{output_language}"
        )
        assert data["outreach_message_prompt"] == ""


class TestLanguageConfig:
    """GET/PUT /api/v1/config/language"""

    @patch("app.routers.config._load_config")
    async def test_get_language(self, mock_load, client):
        mock_load.return_value = {"ui_language": "en", "content_language": "es"}
        async with client:
            resp = await client.get("/api/v1/config/language")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ui_language"] == "en"
        assert data["content_language"] == "es"
        assert "en" in data["supported_languages"]

    @patch("app.routers.config._save_config")
    @patch("app.routers.config._load_config")
    async def test_put_invalid_language_returns_400(self, mock_load, mock_save, client):
        mock_load.return_value = {}
        async with client:
            resp = await client.put("/api/v1/config/language", json={
                "ui_language": "invalid_lang",
            })
        assert resp.status_code == 400


class TestResetDatabase:
    """POST /api/v1/config/reset"""

    @patch("app.routers.config.db", new_callable=AsyncMock)
    async def test_reset_with_correct_token(self, mock_db, client):
        async with client:
            resp = await client.post("/api/v1/config/reset", json={
                "confirm": "RESET_ALL_DATA",
            })
        assert resp.status_code == 200
        mock_db.reset_database.assert_called_once()

    async def test_reset_without_token_returns_400(self, client):
        async with client:
            resp = await client.post("/api/v1/config/reset", json={
                "confirm": "wrong_token",
            })
        assert resp.status_code == 400

    async def test_reset_missing_body_returns_422(self, client):
        async with client:
            resp = await client.post("/api/v1/config/reset")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Encrypted multi-provider API key store (Workstream B)
# ---------------------------------------------------------------------------

class TestEncryptedApiKeys:
    """Per-provider keys are encrypted at rest, independent, and never leak."""

    @pytest.fixture
    def keys_env(self, isolated_db, tmp_path, monkeypatch):
        """Isolate the key store (temp DB), config.json, and crypto secret."""
        from app import crypto
        from app.config import settings
        import app.config as config_module

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        monkeypatch.setattr(config_module, "CONFIG_FILE_PATH", tmp_path / "config.json")
        crypto.reset_cache()
        yield isolated_db
        crypto.reset_cache()

    async def test_saving_provider_b_does_not_erase_provider_a(self, keys_env, client):
        async with client:
            r1 = await client.post("/api/v1/config/api-keys", json={"openai": "sk-openai-key"})
            assert r1.status_code == 200
            r2 = await client.post("/api/v1/config/api-keys", json={"anthropic": "sk-anthropic-key"})
            assert r2.status_code == 200
            status = await client.get("/api/v1/config/api-keys")
        configured = {p["provider"]: p for p in status.json()["providers"]}
        assert configured["openai"]["configured"] is True
        assert configured["anthropic"]["configured"] is True

    async def test_keys_encrypted_at_rest(self, keys_env, owner_id, client):
        async with client:
            await client.post("/api/v1/config/api-keys", json={"openai": "sk-plaintext-123"})
        ciphertexts = keys_env.get_api_key_ciphertexts(owner_id)
        assert "openai" in ciphertexts
        # Stored value is ciphertext, not the plaintext key.
        assert "sk-plaintext-123" not in ciphertexts["openai"]
        from app import crypto
        assert crypto.decrypt(ciphertexts["openai"]) == "sk-plaintext-123"

    async def test_responses_never_contain_raw_key(self, keys_env, client):
        async with client:
            await client.post("/api/v1/config/api-keys", json={"openai": "sk-rawsecret-9999"})
            status = await client.get("/api/v1/config/api-keys")
        body = status.text
        assert "sk-rawsecret-9999" not in body
        # Masked form shows only the tail.
        openai = {p["provider"]: p for p in status.json()["providers"]}["openai"]
        assert openai["masked_key"].endswith("9999")

    async def test_gemini_resolves_to_google_slot(self, keys_env, client):
        from app.config import load_config_file
        from app.llm import resolve_api_key

        async with client:
            await client.post("/api/v1/config/api-keys", json={"google": "sk-google-key"})
        stored = load_config_file()
        # The gemini LLM provider maps to the google key-store slot.
        assert resolve_api_key(stored, "gemini") == "sk-google-key"

    async def test_delete_single_provider(self, keys_env, client):
        async with client:
            await client.post("/api/v1/config/api-keys", json={"openai": "a", "anthropic": "b"})
            await client.delete("/api/v1/config/api-keys/openai")
            status = await client.get("/api/v1/config/api-keys")
        configured = {p["provider"]: p["configured"] for p in status.json()["providers"]}
        assert configured["openai"] is False
        assert configured["anthropic"] is True


class TestLegacyKeyMigration:
    """migrate_legacy_keys folds config.json secrets into the encrypted store."""

    @pytest.fixture
    def keys_env(self, isolated_db, tmp_path, monkeypatch):
        from app import crypto
        from app.config import settings
        import app.config as config_module

        monkeypatch.setattr(settings, "data_dir", tmp_path)
        monkeypatch.setattr(config_module, "CONFIG_FILE_PATH", tmp_path / "config.json")
        crypto.reset_cache()
        yield isolated_db
        crypto.reset_cache()

    async def test_migration_folds_and_clears_legacy_slots(self, keys_env, monkeypatch, tmp_path):
        import json
        import app.config as config_module
        from app.config import migrate_legacy_keys, get_api_keys_from_config

        # Legacy config.json: a plural map AND a single legacy api_key.
        config = {
            "provider": "anthropic",
            "model": "claude",
            "api_keys": {"openai": "legacy-openai"},
            "api_key": "legacy-anthropic-single",
        }
        config_module.CONFIG_FILE_PATH.write_text(json.dumps(config))

        migrate_legacy_keys()

        keys = get_api_keys_from_config()
        assert keys["openai"] == "legacy-openai"
        # The single legacy key is mapped via the active provider (anthropic).
        assert keys["anthropic"] == "legacy-anthropic-single"
        # config.json no longer holds the secrets.
        on_disk = json.loads(config_module.CONFIG_FILE_PATH.read_text())
        assert "api_keys" not in on_disk
        assert "api_key" not in on_disk
        assert on_disk["model"] == "claude"  # non-secret config preserved

    async def test_migration_is_idempotent_and_non_clobbering(self, keys_env, owner_id, monkeypatch):
        import json
        import app.config as config_module
        from app.config import migrate_legacy_keys, get_api_keys_from_config

        # Pre-existing encrypted key for openai must NOT be clobbered.
        from app import crypto
        keys_env.set_api_key_ciphertext(owner_id, "openai", crypto.encrypt("already-stored"))

        config_module.CONFIG_FILE_PATH.write_text(
            json.dumps({"provider": "openai", "api_keys": {"openai": "legacy-should-not-win"}})
        )
        migrate_legacy_keys()
        migrate_legacy_keys()  # idempotent second run

        keys = get_api_keys_from_config()
        assert keys["openai"] == "already-stored"  # not clobbered

    async def test_migration_noop_without_legacy(self, keys_env):
        import app.config as config_module
        from app.config import migrate_legacy_keys

        # No config.json at all -> no-op, no crash.
        if config_module.CONFIG_FILE_PATH.exists():
            config_module.CONFIG_FILE_PATH.unlink()
        migrate_legacy_keys()


@pytest.mark.asyncio
async def test_concurrent_disjoint_api_key_patches_do_not_clobber(isolated_db, owner_id):
    """Two provider updates racing in separate transactions must both survive."""
    import asyncio

    await asyncio.gather(
        asyncio.to_thread(
            isolated_db.patch_api_key_ciphertexts,
            owner_id,
            {"openai": "cipher-a"},
        ),
        asyncio.to_thread(
            isolated_db.patch_api_key_ciphertexts,
            owner_id,
            {"anthropic": "cipher-b"},
        ),
    )

    assert isolated_db.get_api_key_ciphertexts(owner_id) == {
        "openai": "cipher-a",
        "anthropic": "cipher-b",
    }
