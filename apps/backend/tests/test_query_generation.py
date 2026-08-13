"""Unit tests for ``app.job_discovery.query`` (Requirements 2.1-2.3).

These tests never touch a live LLM: the completion function is injected. They
cover the deterministic fallback (Req 2.2), the resume-version cache-hit path
(Req 2.3), and filter overlay.
"""

from __future__ import annotations

import pytest

from app.job_discovery.models import SearchFilters
from app.job_discovery.query import (
    build_deterministic_query,
    clear_query_cache,
    generate_search_query,
)

pytestmark = pytest.mark.unit

RESUME = """
Jane Doe — Senior Software Engineer

Summary: Backend engineer with 7 years building distributed systems.
Skills: Python, FastAPI, PostgreSQL, Kubernetes, Redis, Python.
Experience: Designed microservices in Python and FastAPI on Kubernetes.
"""


@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts with an empty query cache."""
    clear_query_cache()
    yield
    clear_query_cache()


# --------------------------------------------------------------------------- #
# Deterministic fallback (Req 2.2)
# --------------------------------------------------------------------------- #


def test_deterministic_query_is_pure_and_stable():
    q1 = build_deterministic_query(RESUME, resume_version="v1")
    q2 = build_deterministic_query(RESUME, resume_version="v1")

    assert q1 == q2  # dataclass equality -> fully deterministic
    assert q1.degraded is True
    assert q1.resume_version == "v1"


def test_deterministic_query_extracts_titles_skills_and_seniority():
    q = build_deterministic_query(RESUME, resume_version="v1")

    # Role phrases are detected from a known vocabulary.
    assert "software engineer" in q.titles
    assert len(q.titles) <= 3

    # Seniority is inferred from the resume text.
    assert q.seniority == "senior"

    # Strongest skills surface in the search string; boilerplate does not.
    assert "python" in q.search_string.lower()
    assert "fastapi" in q.search_string.lower()
    # A matched title is quoted into the boolean query, not echoed as a keyword.
    assert '"software engineer"' in q.search_string


def test_deterministic_query_falls_back_to_pseudo_title_when_no_role_matches():
    q = build_deterministic_query(
        "Loves gardening, cooking, and painting watercolors.",
        resume_version="v9",
    )
    # No known role phrase -> at least one non-empty title is still produced.
    assert q.titles
    assert q.titles[0].strip()
    assert q.search_string.strip()
    assert q.degraded is True


@pytest.mark.asyncio
async def test_generate_falls_back_when_llm_raises():
    async def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    q = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=boom
    )

    assert q.degraded is True
    assert "software engineer" in q.titles


@pytest.mark.asyncio
async def test_generate_falls_back_on_unusable_llm_shape():
    async def bad_shape(*args, **kwargs):
        return {"titles": "not-a-list"}  # invalid -> triggers fallback

    q = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=bad_shape
    )

    assert q.degraded is True  # parser rejected the shape, used the heuristic


# --------------------------------------------------------------------------- #
# LLM path + resume-version cache (Req 2.1, 2.3)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_path_parses_payload():
    async def fake_llm(*args, **kwargs):
        return {
            "titles": ["Backend Engineer", "Platform Engineer"],
            "search_string": '("Backend Engineer") python fastapi',
            "seniority": "senior",
        }

    q = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=fake_llm
    )

    assert q.degraded is False
    assert q.titles == ["Backend Engineer", "Platform Engineer"]
    assert q.search_string == '("Backend Engineer") python fastapi'
    assert q.seniority == "senior"
    assert q.resume_version == "v1"


@pytest.mark.asyncio
async def test_cache_hit_skips_second_llm_call():
    calls = {"n": 0}

    async def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return {
            "titles": ["Backend Engineer"],
            "search_string": "backend python",
            "seniority": "senior",
        }

    q1 = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=counting_llm
    )
    q2 = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=counting_llm
    )

    assert calls["n"] == 1  # second call served from cache (Req 2.3)
    assert q1.titles == q2.titles
    assert q1.search_string == q2.search_string


@pytest.mark.asyncio
async def test_force_refresh_bypasses_cache():
    calls = {"n": 0}

    async def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return {"titles": ["Backend Engineer"], "search_string": "x", "seniority": None}

    await generate_search_query(RESUME, resume_version="v1", llm_complete=counting_llm)
    await generate_search_query(
        RESUME, resume_version="v1", llm_complete=counting_llm, force_refresh=True
    )

    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_degraded_result_is_not_cached():
    async def boom(*args, **kwargs):
        raise RuntimeError("down")

    calls = {"n": 0}

    async def counting_llm(*args, **kwargs):
        calls["n"] += 1
        return {"titles": ["Backend Engineer"], "search_string": "x", "seniority": None}

    # First call fails -> degraded fallback, must NOT be cached.
    first = await generate_search_query(RESUME, resume_version="v1", llm_complete=boom)
    assert first.degraded is True

    # Second call with a working LLM must actually invoke it (no stale cache).
    second = await generate_search_query(
        RESUME, resume_version="v1", llm_complete=counting_llm
    )
    assert calls["n"] == 1
    assert second.degraded is False


@pytest.mark.asyncio
async def test_filters_overlaid_without_affecting_cache_key():
    async def fake_llm(*args, **kwargs):
        return {"titles": ["Backend Engineer"], "search_string": "backend", "seniority": None}

    q_in = await generate_search_query(
        RESUME,
        resume_version="v1",
        filters=SearchFilters(location="Bengaluru", country_indeed="india"),
        llm_complete=fake_llm,
    )
    assert q_in.location == "Bengaluru"
    assert q_in.country_indeed == "india"

    # Same resume_version, different location -> cache hit for the intent, but
    # the new location is overlaid rather than the cached one being returned.
    q_other = await generate_search_query(
        RESUME,
        resume_version="v1",
        filters=SearchFilters(location="Remote"),
        llm_complete=fake_llm,
    )
    assert q_other.location == "Remote"
    assert q_other.titles == q_in.titles
