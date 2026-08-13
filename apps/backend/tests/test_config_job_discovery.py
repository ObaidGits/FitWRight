"""Unit tests for the JOB_DISCOVERY settings block in ``app.config``."""

from __future__ import annotations

import os

import pytest

from app.config import Settings


@pytest.fixture
def clean_env(monkeypatch):
    """Remove any JOB_DISCOVERY* env vars so defaults are observable."""
    for key in list(os.environ):
        if key.startswith("JOB_DISCOVERY"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_job_discovery_defaults(clean_env):
    s = Settings(_env_file=None)

    # Kill-switch ships OFF.
    assert s.JOB_DISCOVERY is False

    # Low/conservative defaults.
    assert s.JOB_DISCOVERY_JOBSPY_SITES == "indeed"
    assert s.JOB_DISCOVERY_CACHE_TTL_SECONDS == 3600
    assert s.JOB_DISCOVERY_MAX_RESULTS == 50
    assert s.JOB_DISCOVERY_MAX_RECIPES == 20
    assert s.JOB_DISCOVERY_STEALTH_MAX_CONCURRENCY == 1

    # Parsed helper.
    assert s.job_discovery_jobspy_sites == ["indeed"]


def test_job_discovery_env_override(clean_env):
    clean_env.setenv("JOB_DISCOVERY", "true")
    clean_env.setenv("JOB_DISCOVERY_JOBSPY_SITES", "indeed, naukri ,linkedin")
    clean_env.setenv("JOB_DISCOVERY_MAX_RESULTS", "10")

    s = Settings(_env_file=None)

    assert s.JOB_DISCOVERY is True
    assert s.JOB_DISCOVERY_MAX_RESULTS == 10
    assert s.job_discovery_jobspy_sites == ["indeed", "naukri", "linkedin"]
