"""LIVE end-to-end LLM tests - one per AI feature, against a REAL provider.

Unlike the mocked unit/contract tests, these actually call the configured LLM
provider and assert the real output shape for every AI-native feature in the
app. They are the "true test" of whether tailoring, cover letters, interview
prep, enrichment, wizard, and profile AI genuinely work with a given model.

They are GATED so normal CI never spends provider quota:

    RUN_LIVE_LLM=1  -  required to run at all.

Provider selection (either works):
  1. Explicit (recommended, provider-agnostic): set LIVE_LLM_* env vars:
       RUN_LIVE_LLM=1 \
       LIVE_LLM_PROVIDER=openai_compatible \
       LIVE_LLM_MODEL=deepseek-v4-flash-free \
       LIVE_LLM_API_KEY=sk-... \
       LIVE_LLM_API_BASE=https://opencode.ai/zen/v1 \
       LIVE_LLM_REASONING_EFFORT=          # optional: minimal|low|medium|high
         uv run pytest -q tests/live/test_live_llm_features.py
  2. Fallback: if LIVE_LLM_* are unset, the app's own resolved configuration
     (Settings / encrypted key store / env) is used; the suite skips if no key
     is resolvable for a key-requiring provider.

Assertions are deliberately STRUCTURAL (non-empty, correct types/keys) because
real model wording varies - the goal is "the feature produced valid output",
not an exact string match.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

RUN_LIVE = os.getenv("RUN_LIVE_LLM") == "1"

pytestmark = pytest.mark.skipif(
    not RUN_LIVE,
    reason=(
        "Live LLM tests spend real provider quota. Set RUN_LIVE_LLM=1 (and "
        "LIVE_LLM_* env vars or a configured provider) to run them."
    ),
)

# --- Realistic sample inputs shared across features -------------------------

RESUME_DATA: dict = {
    "personalInfo": {
        "name": "Alex Morgan",
        "title": "Senior Software Engineer",
        "email": "alex.morgan@example.com",
        "phone": "+1 555 0142",
        "location": "San Francisco, CA",
    },
    "summary": (
        "Senior software engineer with 8 years building reliable, high-scale "
        "web platforms, focused on clean architecture and measurable impact."
    ),
    "workExperience": [
        {
            "id": 1,
            "title": "Senior Software Engineer",
            "company": "Northwind Labs",
            "location": "San Francisco, CA",
            "years": "Mar 2021 - Present",
            "description": [
                "Led a 5-engineer team rebuilding the billing platform, cutting invoice errors by 38%.",
                "Designed an event-driven ingestion pipeline handling 20M+ events/day.",
            ],
        }
    ],
    "education": [
        {
            "id": 1,
            "institution": "University of Washington",
            "degree": "B.S. Computer Science",
            "years": "2013 - 2017",
            "description": "Graduated with honors.",
        }
    ],
    "personalProjects": [
        {
            "id": 1,
            "name": "OpenLedger",
            "role": "Creator",
            "years": "2022 - Present",
            "description": ["Open-source double-entry accounting library."],
        }
    ],
    "additional": {
        "technicalSkills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AWS"],
        "languages": ["English"],
        "certificationsTraining": ["AWS Solutions Architect"],
        "awards": [],
    },
    "customSections": {},
    "sectionMeta": [],
}

RESUME_MD = (
    "# Alex Morgan\n"
    "Senior Software Engineer - San Francisco, CA\n\n"
    "## Summary\n"
    "Senior software engineer with 8 years building high-scale web platforms.\n\n"
    "## Experience\n"
    "**Senior Software Engineer**, Northwind Labs (Mar 2021 - Present)\n"
    "- Led a 5-engineer team rebuilding the billing platform, cutting invoice errors by 38%.\n"
    "- Designed an event-driven ingestion pipeline handling 20M+ events/day.\n\n"
    "## Education\n"
    "B.S. Computer Science, University of Washington (2013 - 2017)\n\n"
    "## Skills\n"
    "Python, FastAPI, PostgreSQL, Docker, AWS\n"
)

JD = (
    "Senior Backend Engineer. We need Python, FastAPI, PostgreSQL, Docker, AWS, "
    "and Kubernetes. You will design scalable REST APIs, optimize database "
    "performance, and mentor engineers. 5+ years experience, CI/CD, and "
    "microservices architecture required."
)


# --- Live provider wiring ---------------------------------------------------


@pytest.fixture(scope="module")
def live_config():
    """Build the LLMConfig to test against (LIVE_LLM_* env, else app config)."""
    from app.llm import LLMConfig, get_llm_config

    provider = os.getenv("LIVE_LLM_PROVIDER")
    key = os.getenv("LIVE_LLM_API_KEY", "")
    if provider:
        if not key and provider not in ("ollama", "openai_compatible"):
            pytest.skip(f"LIVE_LLM_API_KEY required for provider {provider}.")
        effort = os.getenv("LIVE_LLM_REASONING_EFFORT") or None
        return LLMConfig(
            provider=provider,
            model=os.getenv("LIVE_LLM_MODEL", ""),
            api_key=key,
            api_base=os.getenv("LIVE_LLM_API_BASE") or None,
            reasoning_effort=effort,  # validated by the model (Literal or None)
        )
    cfg = get_llm_config()
    if not cfg.api_key and cfg.provider not in ("ollama", "openai_compatible"):
        pytest.skip(
            "No live LLM configured. Set LIVE_LLM_* env vars or configure a "
            "provider key in Settings."
        )
    return cfg


@pytest.fixture(autouse=True)
def _use_live_config(live_config, monkeypatch):
    """Route every feature's get_llm_config() to the live config.

    Services resolve config via their own ``get_llm_config`` reference (for
    model name / token budgets) and the LLM call layer resolves it again, so we
    patch the canonical definition AND every module that imported it. The Router
    LRU is cleared around each test for isolation.
    """
    import app.llm as llm

    llm._router_cache.clear()

    def _fake(user_id=None):
        return live_config

    monkeypatch.setattr(llm, "get_llm_config", _fake)
    for name, mod in list(sys.modules.items()):
        if not name.startswith("app."):
            continue
        if mod is llm:
            continue
        if getattr(mod, "get_llm_config", None) is not None:
            monkeypatch.setattr(mod, "get_llm_config", _fake, raising=False)
    yield
    llm._router_cache.clear()


LIVE_TIMEOUT = 300  # reasoning models on free tiers can be slow


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


# ===========================================================================
# 1. Generic provider completion (used by cover letter / outreach / title /
#    JD cleaning) + streaming (tailor stream path)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generic_completion():
    from app.llm import complete

    out = await complete(prompt="Reply with a single short sentence saying hello.")
    assert _nonempty_str(out)


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_streaming_completion():
    from app.llm import StreamResult, stream_complete

    result = StreamResult()
    deltas: list[str] = []
    async for piece in stream_complete(
        prompt="List three programming languages, comma separated.",
        result=result,
    ):
        deltas.append(piece)
    assert _nonempty_str(result.text)
    assert result.usage.total_tokens >= 0


# ===========================================================================
# 2. Resume parsing (upload -> structured JSON)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_parse_resume_to_json():
    from app.services.parser import parse_resume_to_json

    data = await parse_resume_to_json(RESUME_MD)
    assert isinstance(data, dict)
    assert isinstance(data.get("personalInfo"), dict)
    # The parser should recover the candidate name from the markdown.
    assert _nonempty_str(data["personalInfo"].get("name"))


# ===========================================================================
# 3. JD keyword extraction (the core JD analysis feature)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_extract_job_keywords():
    from app.services.improver import extract_job_keywords

    kw = await extract_job_keywords(JD)
    assert isinstance(kw, dict)
    # At least one of the keyword buckets must be populated.
    buckets = (
        kw.get("required_skills") or []
    ) + (kw.get("preferred_skills") or []) + (kw.get("keywords") or [])
    assert len(buckets) > 0


# ===========================================================================
# 4. Skill target planning (pre-tailor pass)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_skill_target_plan():
    from app.services.improver import extract_job_keywords, generate_skill_target_plan

    kw = await extract_job_keywords(JD)
    plan = await generate_skill_target_plan(RESUME_DATA, JD, kw)
    assert isinstance(plan, dict)
    assert isinstance(plan.get("target_skills"), list)


# ===========================================================================
# 5. Resume diffs (targeted tailoring changes)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_resume_diffs():
    from app.services.improver import extract_job_keywords, generate_resume_diffs

    kw = await extract_job_keywords(JD)
    result = await generate_resume_diffs(
        RESUME_MD, JD, kw, original_resume_data=RESUME_DATA
    )
    assert hasattr(result, "changes")
    assert isinstance(result.changes, list)


# ===========================================================================
# 6. Full resume improvement (tailoring rewrite)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_improve_resume():
    from app.services.improver import extract_job_keywords, improve_resume

    kw = await extract_job_keywords(JD)
    improved = await improve_resume(
        RESUME_MD, JD, kw, original_resume_data=RESUME_DATA
    )
    assert isinstance(improved, dict)
    # personalInfo must be preserved verbatim (never AI-invented).
    assert improved["personalInfo"]["name"] == RESUME_DATA["personalInfo"]["name"]
    assert isinstance(improved.get("workExperience"), list)


# ===========================================================================
# 7. Multi-pass refinement (keyword injection + ATS alignment)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_refine_resume():
    from app.services.improver import extract_job_keywords, improve_resume
    from app.services.refiner import refine_resume

    kw = await extract_job_keywords(JD)
    improved = await improve_resume(RESUME_MD, JD, kw, original_resume_data=RESUME_DATA)
    result = await refine_resume(improved, RESUME_DATA, JD, kw)
    assert result is not None
    assert isinstance(result.refined_data, dict)
    assert isinstance(result.refined_data.get("workExperience"), list)


# ===========================================================================
# 8. Cover letter
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_cover_letter():
    from app.services.cover_letter import generate_cover_letter

    letter = await generate_cover_letter(RESUME_DATA, JD)
    assert _nonempty_str(letter)


# ===========================================================================
# 9. Cold outreach message
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_outreach_message():
    from app.services.cover_letter import generate_outreach_message

    msg = await generate_outreach_message(RESUME_DATA, JD)
    assert _nonempty_str(msg)


# ===========================================================================
# 10. Resume title generation (the previously 60-token, now 512-token path)
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_resume_title():
    from app.services.cover_letter import generate_resume_title

    title = await generate_resume_title(JD, "en", "Alex Morgan")
    assert _nonempty_str(title)


# ===========================================================================
# 11. Interview preparation
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_generate_interview_prep():
    from app.services.interview_prep import generate_interview_prep

    prep = await generate_interview_prep(RESUME_DATA, JD)
    dumped = prep.model_dump()
    for key in (
        "role_fit_analysis",
        "resume_questions",
        "project_follow_ups",
        "skill_gaps",
        "talking_points",
    ):
        assert key in dumped


# ===========================================================================
# 12. Enrichment - analyze resume for weak items
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_enrichment_analyze():
    from app.llm import complete_json
    from app.prompts.enrichment import ANALYZE_RESUME_PROMPT
    from app.schemas.enrichment import AnalysisResponse

    prompt = ANALYZE_RESUME_PROMPT.format(
        resume_json=json.dumps(RESUME_DATA), output_language="English"
    )
    result = await complete_json(
        prompt, max_tokens=4096, schema_type="enrichment", response_model=AnalysisResponse
    )
    assert "items_to_enrich" in result
    assert "questions" in result


# ===========================================================================
# 13. Enrichment - regenerate an experience item's bullets
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_enrichment_regenerate_experience():
    from app.routers.enrichment import _regenerate_experience_or_project
    from app.schemas.enrichment import RegenerateItemInput

    item = RegenerateItemInput(
        item_id="exp_0",
        item_type="experience",
        title="Senior Software Engineer",
        subtitle="Northwind Labs",
        current_content=[
            "Worked on the billing platform.",
            "Helped with the data pipeline.",
        ],
    )
    out = await _regenerate_experience_or_project(
        item, "Make these bullets more specific and impactful.", "English"
    )
    assert isinstance(out.new_content, list)
    assert len(out.new_content) > 0


# ===========================================================================
# 14. Enrichment - regenerate the skills section
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_enrichment_regenerate_skills():
    from app.routers.enrichment import _regenerate_skills
    from app.schemas.enrichment import RegenerateItemInput

    item = RegenerateItemInput(
        item_id="skills",
        item_type="skills",
        title="Technical Skills",
        current_content=["Python", "FastAPI", "PostgreSQL"],
    )
    out = await _regenerate_skills(
        item, "Group and prioritize skills for a backend role.", "English"
    )
    assert isinstance(out.new_content, list)
    assert len(out.new_content) > 0


# ===========================================================================
# 15. Resume wizard - draft bullets from a plain description
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_wizard_draft_bullets():
    from app.services.resume_wizard import draft_bullets

    bullets = await draft_bullets(
        section="experience",
        title="Backend Engineer",
        company="Acme",
        description="Built REST APIs in Python and improved database performance.",
    )
    assert isinstance(bullets, list)
    assert len(bullets) > 0


# ===========================================================================
# 16. Resume wizard - parse a pasted blob into structured entries
# ===========================================================================


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_wizard_parse_entries():
    from app.services.resume_wizard import parse_entries

    text = (
        "Software Engineer, Acme Corp, 2019-2022. Built payment APIs in Python. "
        "Improved latency by 30%."
    )
    entries = await parse_entries(section="experience", text=text)
    assert isinstance(entries, list)


# ===========================================================================
# 17. Profile AI - suggest an improved summary
# ===========================================================================


def _sample_profile():
    from app.profile.schemas import ProfileData, ProfileExperience, ProfileIdentity

    return ProfileData(
        identity=ProfileIdentity(headline="Senior Software Engineer", currentRole="Backend Engineer"),
        summary="Engineer who builds web platforms.",
        workExperience=[
            ProfileExperience(
                title="Senior Software Engineer",
                company="Northwind Labs",
                years="2021 - Present",
                description=[
                    "Built the billing platform.",
                    "Worked on the data pipeline.",
                ],
            )
        ],
    )


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_profile_suggest_summary():
    from app.profile.ai import suggest_summary

    profile = _sample_profile()
    result = await suggest_summary(profile)
    assert result["kind"] == "summary"
    assert _nonempty_str(result.get("suggestion"))


@pytest.mark.timeout(LIVE_TIMEOUT)
async def test_profile_suggest_experience_bullets():
    from app.profile.ai import suggest_experience_bullets

    profile = _sample_profile()
    uid = profile.workExperience[0].uid
    result = await suggest_experience_bullets(profile, uid)
    assert result["kind"] == "experience_bullets"
    assert isinstance(result.get("suggestion"), list)
    assert len(result["suggestion"]) > 0
