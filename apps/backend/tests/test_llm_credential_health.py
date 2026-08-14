"""A refused provider key must be remembered, so the next upload is stopped early.

The bug: `llm_configured` is satisfied by the deployment-level LLM_API_KEY, so a
user who had configured nothing still passed the frontend gate. The upload ran, the
text was extracted, and only then did the provider answer 401 - and nothing
remembered, so the next attempt repeated the whole trip.
"""

from __future__ import annotations

import pytest

from app import llm_health

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean():
    llm_health.reset_for_tests()
    yield
    llm_health.reset_for_tests()


class TestRecordingRefusals:
    def test_a_refusal_is_remembered_against_the_user(self):
        assert llm_health.credentials_rejected("u1") is None
        llm_health.mark_credentials_rejected("u1", "openai", "resume_parse")
        rejection = llm_health.credentials_rejected("u1")
        assert rejection is not None
        assert rejection.provider == "openai"
        assert rejection.detail == "resume_parse"

    def test_refusals_do_not_leak_between_users(self):
        """One user's bad key must not disable AI for everyone else."""
        llm_health.mark_credentials_rejected("u1", "openai")
        assert llm_health.credentials_rejected("u2") is None

    def test_saving_a_new_key_reopens_the_gate(self):
        """Otherwise a user who has just fixed their key stays locked out."""
        llm_health.mark_credentials_rejected("u1", "openai")
        llm_health.clear_credentials_rejected("u1")
        assert llm_health.credentials_rejected("u1") is None

    def test_anonymous_callers_are_ignored(self):
        llm_health.mark_credentials_rejected("", "openai")
        assert llm_health.credentials_rejected(None) is None
        assert llm_health.credentials_rejected("") is None
