"""A placeholder key is NOT a configured provider.

The bug this pins, found on a real machine: `.env` carried
``LLM_API_KEY=sk-your-openai-key-here`` copied straight from `.env.example`. Every check
in the app asked only "is the key non-empty?", so the deployment reported itself
configured, the UI offered every AI feature, each call went to OpenAI, and OpenAI
answered 401. The app then told the user AI was *unavailable* - which reads as an outage
in a service that had simply never been set up.

"Add your API key" and "the provider is down" send a user to completely different places.
That is why this is worth a guard rather than leaving it to the credential-rejection
memory in app/llm_health.py: that only learns AFTER a failed call, and it cannot tell a
never-configured deployment from a broken one.
"""

from __future__ import annotations

import pytest

from app.llm import LLMConfig, has_usable_credential, is_placeholder_key


class TestPlaceholderDetection:
    @pytest.mark.parametrize(
        "value",
        [
            "sk-your-openai-key-here",
            "sk-your-key-here",
            "YOUR_API_KEY",
            "your-api-key",
            "changeme",
            "change-me-please",
            "replace-with-your-key",
            "sk-example-key",
            "placeholder",
            "sk-xxxxxxxxxxxx",
            "<your-key>",
            "sk-...",
            "sk-",
            "none",
        ],
    )
    def test_template_values_are_recognised(self, value):
        assert is_placeholder_key(value) is True, value

    @pytest.mark.parametrize(
        "value",
        [
            # Shapes that real credentials take. None contains an English word from a
            # template, which is what the check keys on.
            "sk-proj-9dQw4w9WgXcQabcdEFGh1234ijklMNOP5678qrstUVWX",
            "sk-ant-api03-7hK2mNp8QrS4tVw6XyZ1aBc3DeF5gHi9JkL0",
            "AIzaSyD-9tD8mBpq2FvKl3nRxYzWaBcDeFgHiJk",
            "gsk_abc123DEF456ghi789JKL012mno345PQR",
        ],
    )
    def test_real_looking_keys_are_not_flagged(self, value):
        # A false positive here would lock a user out of a working key, which is worse
        # than the bug being fixed.
        assert is_placeholder_key(value) is False, value

    def test_an_empty_key_is_not_a_placeholder(self):
        """Empty is "not set", which the presence check already handles. Conflating them
        would make the reason reported to the user wrong."""
        assert is_placeholder_key("") is False
        assert is_placeholder_key(None) is False


class TestUsableCredential:
    def test_a_placeholder_key_is_not_configured(self):
        config = LLMConfig(
            provider="openai", model="gpt-4o-mini", api_key="sk-your-openai-key-here"
        )
        assert has_usable_credential(config) is False

    def test_a_real_key_is_configured(self):
        config = LLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-proj-9dQw4w9WgXcQabcdEFGh1234ijkl",
        )
        assert has_usable_credential(config) is True

    def test_a_self_hosted_provider_needs_no_key(self):
        """Ollama and openai_compatible have no key, and that is not a misconfiguration.

        This deliberately does NOT require a base URL either. One cannot work without a
        base URL, but that is a different defect from the placeholder bug, and tightening
        it here would silently change what "configured" means for local installs - there
        is an existing test asserting the current behaviour for exactly that reason.
        """
        assert (
            has_usable_credential(
                LLMConfig(
                    provider="ollama",
                    model="llama3",
                    api_key="",
                    api_base="http://localhost:11434",
                )
            )
            is True
        )
        assert (
            has_usable_credential(
                LLMConfig(provider="openai_compatible", model="local", api_key="")
            )
            is True
        )

    def test_an_empty_key_is_not_configured(self):
        assert (
            has_usable_credential(
                LLMConfig(provider="openai", model="gpt-4o-mini", api_key="")
            )
            is False
        )
