"""Shared test fixtures for FitWright backend tests."""

# ---------------------------------------------------------------------------
# Hermetic test environment (MUST run before ``app.config`` is imported).
#
# ``Settings`` is a module-level singleton built at import from os.environ + the
# ``.env`` file. A developer who has populated ``.env`` for a real deployment
# (e.g. SINGLE_USER_MODE=false + a Postgres DATABASE_URL + provider secrets)
# would otherwise see that config bleed into the unit/integration suite, causing
# spurious failures that do NOT reproduce in CI (which has no ``.env``). We pin
# the settings-relevant variables to their zero-config defaults here - os.environ
# takes precedence over ``.env`` in pydantic-settings, and explicit
# ``Settings(...)`` kwargs in hosted-mode tests still override these - so the
# suite is hermetic and ``pytest`` behaves identically locally and in CI.
# ---------------------------------------------------------------------------
import os as _os

_HERMETIC_ENV = {
    "SINGLE_USER_MODE": "true",
    "DEPLOYMENT_PROFILE": "",
    "DATABASE_URL": "",
    "MIGRATION_DATABASE_URL": "",
    "DB_SSL": "",
    "KVSTORE_URL": "",
    "STORAGE_PROVIDER": "local",
    "CLOUDINARY_CLOUD_NAME": "",
    "CLOUDINARY_API_KEY": "",
    "CLOUDINARY_API_SECRET": "",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "OAUTH_REDIRECT_URI": "",
    "EMAIL_VERIFICATION": "",
    "EMAIL_PROVIDER": "",
    "INTERNAL_JOB_TOKEN": "",
    "SESSION_SECRET": "",
    "IP_HASH_SECRET": "",
    "SCHEDULER_MODE": "external_cron",
}
for _key, _value in _HERMETIC_ENV.items():
    _os.environ[_key] = _value

import copy
import importlib

import pytest


@pytest.fixture(autouse=True)
def _hermetic_jd_robots(monkeypatch):
    """Keep the JD robots.txt check hermetic (no network) across the suite.

    The v2 orchestrator runs a robots.txt policy check before fetching (§26).
    That check performs its OWN network fetch (separate from the mocked
    ``orchestrator.fetch_url_safely``), which would make otherwise-hermetic tests
    hit the network. We force the robots fetch to fail here, which the checker
    treats as fail-OPEN (allow) - exactly the production behavior when robots.txt
    is unreachable. Phase-3 robots tests override this by patching the same
    symbol or by exercising the pure parser/decision functions directly.
    """
    import app.jd.robots as _robots_mod

    async def _no_network(_url, *a, **k):
        raise _robots_mod.SsrfError("test_no_network")

    monkeypatch.setattr(_robots_mod, "fetch_url_safely", _no_network)
    # Reset orchestrator singletons so leftover instances from a prior test don't
    # retain a real-KV robots checker.
    try:
        import app.jd.orchestrator as _orch
        monkeypatch.setattr(_orch, "_robots", None)
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_status_llm_health_cache():
    """Clear the /status LLM-health probe cache before each test.

    The endpoint caches its LLM-health probe (short TTL, single-flight) to avoid
    a live provider round-trip per public /status call. That cache is
    module-level, so it must be reset between tests or a result from one test
    would leak into another sharing the same provider/model/key fingerprint.
    """
    from app.routers.health import reset_status_llm_health_cache

    reset_status_llm_health_cache()
    yield


# ---------------------------------------------------------------------------
# Sample resume data - full ResumeData-compatible dict
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_resume() -> dict:
    """A realistic resume dict matching the ResumeData schema."""
    return {
        "personalInfo": {
            "name": "Jane Doe",
            "title": "Senior Backend Engineer",
            "email": "jane@example.com",
            "phone": "+1-555-0100",
            "location": "San Francisco, CA",
            "website": "https://janedoe.dev",
            "linkedin": "linkedin.com/in/janedoe",
            "github": "github.com/janedoe",
        },
        "summary": "Backend engineer with 6 years of experience building scalable Python APIs and microservices.",
        "workExperience": [
            {
                "id": 1,
                "title": "Senior Backend Engineer",
                "company": "Acme Corp",
                "location": "San Francisco, CA",
                "years": "Jan 2021 - Present",
                "description": [
                    "Built REST APIs serving 50K requests/day using Python and FastAPI",
                    "Led migration from monolith to microservices architecture",
                    "Mentored 3 junior developers on backend best practices",
                ],
            },
            {
                "id": 2,
                "title": "Software Engineer",
                "company": "StartupCo",
                "location": "New York, NY",
                "years": "Jun 2018 - Dec 2020",
                "description": [
                    "Developed payment processing system handling $2M monthly",
                    "Wrote unit and integration tests improving coverage from 40% to 85%",
                ],
            },
        ],
        "education": [
            {
                "id": 1,
                "institution": "MIT",
                "degree": "B.S. Computer Science",
                "years": "2014 - 2018",
                "description": "Graduated with honors, Dean's List",
            }
        ],
        "personalProjects": [
            {
                "id": 1,
                "name": "OpenAPI Generator",
                "role": "Creator & Maintainer",
                "years": "Mar 2021 - Present",
                "description": [
                    "CLI tool generating API clients from OpenAPI specs",
                    "500+ GitHub stars, used by 30+ companies",
                ],
            }
        ],
        "additional": {
            "technicalSkills": ["Python", "FastAPI", "Docker", "AWS", "PostgreSQL", "Redis"],
            "languages": ["English (Native)", "Spanish (Conversational)"],
            "certificationsTraining": ["AWS Solutions Architect Associate"],
            "awards": ["Employee of the Year 2022"],
        },
        "customSections": {},
        "sectionMeta": [],
    }


@pytest.fixture
def sample_resume_copy(sample_resume) -> dict:
    """Deep copy of sample_resume for mutation-safe tests."""
    return copy.deepcopy(sample_resume)


# ---------------------------------------------------------------------------
# Job-related fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_job_keywords() -> dict:
    """Extracted job keywords matching the LLM output format."""
    return {
        "required_skills": ["Python", "FastAPI", "Docker", "Kubernetes"],
        "preferred_skills": ["AWS", "Terraform", "GraphQL"],
        "experience_requirements": ["5+ years backend development"],
        "education_requirements": ["Bachelor's in CS or equivalent"],
        "key_responsibilities": [
            "Design and build scalable APIs",
            "Lead technical architecture decisions",
        ],
        "keywords": ["microservices", "CI/CD", "agile", "REST API"],
        "experience_years": 5,
        "seniority_level": "senior",
    }


@pytest.fixture
def sample_job_description() -> str:
    """A realistic job description text."""
    return (
        "Senior Backend Engineer at TechCorp\n\n"
        "We are looking for a Senior Backend Engineer to join our platform team. "
        "You will design and build scalable APIs using Python and FastAPI. "
        "Experience with Docker, Kubernetes, and AWS is required. "
        "Terraform and GraphQL experience is a plus.\n\n"
        "Requirements:\n"
        "- 5+ years backend development experience\n"
        "- Strong Python skills with FastAPI or similar frameworks\n"
        "- Experience with microservices architecture\n"
        "- Familiarity with CI/CD pipelines and agile methodologies\n"
        "- Bachelor's degree in CS or equivalent\n"
    )


# ---------------------------------------------------------------------------
# Master resume - used for alignment validation
# ---------------------------------------------------------------------------

@pytest.fixture
def master_resume(sample_resume) -> dict:
    """Master resume (source of truth) - same as sample_resume by default."""
    return copy.deepcopy(sample_resume)


# ---------------------------------------------------------------------------
# ResumeChange fixtures for diff-based tests
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_changes():
    """A set of ResumeChange dicts covering all action types."""
    from app.schemas.models import ResumeChange

    return [
        ResumeChange(
            path="summary",
            action="replace",
            original="Backend engineer with 6 years of experience building scalable Python APIs and microservices.",
            value="Senior backend engineer with 6 years building scalable Python APIs, microservices, and cloud infrastructure on AWS.",
            reason="Added cloud/AWS keywords from JD",
        ),
        ResumeChange(
            path="workExperience[0].description[0]",
            action="replace",
            original="Built REST APIs serving 50K requests/day using Python and FastAPI",
            value="Designed and built REST APIs serving 50K requests/day using Python, FastAPI, and Docker",
            reason="Added Docker keyword from JD",
        ),
        ResumeChange(
            path="workExperience[0].description",
            action="append",
            original=None,
            value="Implemented CI/CD pipelines with GitHub Actions reducing deploy time by 40%",
            reason="Added CI/CD keyword from JD",
        ),
        ResumeChange(
            path="additional.technicalSkills",
            action="reorder",
            original=None,
            value=["Python", "FastAPI", "Docker", "AWS", "PostgreSQL", "Redis"],
            reason="Already in good order, no change needed",
        ),
    ]


# ---------------------------------------------------------------------------
# Isolated database - swap the global TinyDB singleton for a temp-file DB
# ---------------------------------------------------------------------------

@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    """Replace the global ``db`` singleton with a disposable temp-file SQLite DB
    across ``app.database`` and every router module that imported it.

    Lets endpoint / e2e tests run against a REAL (but isolated) database instead
    of a MagicMock, so persistence, the master-resume invariant, and CRUD are
    actually exercised - without touching the developer's real database. A
    temp **file** (not ``:memory:``) is required: SQLite's connection pool gives
    each connection its own in-memory DB, so the async + sync engines would not
    share state.
    """
    import app.database as database_module
    from app.database import Database

    test_db = Database(db_path=tmp_path / "isolated_db.db")
    monkeypatch.setattr(database_module, "db", test_db)
    for router_name in (
        "resumes",
        "jobs",
        "enrichment",
        "config",
        "health",
        "applications",
        "resume_wizard",
        "versions",
        "notifications",
    ):
        try:
            module = importlib.import_module(f"app.routers.{router_name}")
        except ModuleNotFoundError:
            continue
        if hasattr(module, "db"):
            monkeypatch.setattr(module, "db", test_db)
    try:
        yield test_db
    finally:
        await test_db.close()


@pytest.fixture
async def owner_id(isolated_db) -> str:
    """The single-user bootstrap owner id for the isolated db.

    Endpoints resolve the effective ``user_id`` to this owner in
    ``SINGLE_USER_MODE`` (the local default), so tests that verify persistence by
    calling the scoped ``db`` facade directly thread this id through the owned
    methods (Task 3).
    """
    from app.auth.owner import ensure_owner

    return await ensure_owner(isolated_db)
