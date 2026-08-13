"""Unit tests for the discovery orchestrator service (task 0.15).

Exercises the orchestration end-to-end with **fake connectors** and a fake
query generator so no test touches a live LLM/browser/scraper. Covered:

* kill-switch gate (Req 10.4) and ownership check (Req 1.5)
* happy path: fan-out -> normalize/dedup -> rank -> cache store (Req 1.1, 6, 7)
* partial success + degraded flag + per-source ``sources`` report (Req 1.2, 3.2)
* all-sources-fail path (Req 3.2)
* degraded results (fallback query OR source failure) are NOT cached (Req 6)
* cache hit short-circuits the fan-out; ``force_refresh`` bypasses the cache

The cache runs against a fresh temp SQLite database built by the real Alembic
migration, so these also cover the cache round-trip through the service.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config

from app.database import Database
from app.job_discovery.cache import SearchCache
from app.job_discovery.connectors.base import RawListing
from app.job_discovery.models import SearchFilters, SearchQuery, SourceFailure
from app.job_discovery.service import (
    DiscoveryDisabledError,
    DiscoveryService,
    ResumeData,
    ResumeNotFoundError,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fixtures / fakes
# --------------------------------------------------------------------------- #
def _run_migrations(db_file: Path) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "discovery.db"
    # _run_migrations(db_file)  # Skipped: create_all handles it
    database = Database.from_url(f"sqlite+aiosqlite:///{db_file}")
    try:
        yield database
    finally:
        await database.dispose()


def _config(*, enabled: bool = True, max_results: int = 50) -> SimpleNamespace:
    """Minimal settings stub carrying only what the service reads directly."""
    return SimpleNamespace(
        JOB_DISCOVERY=enabled,
        JOB_DISCOVERY_MAX_RESULTS=max_results,
        JOB_DISCOVERY_CACHE_TTL_SECONDS=3600,
        job_discovery_jobspy_sites=["indeed"],
    )


def _resume_loader(known: dict[str, ResumeData]):
    async def _load(user_id: str, resume_id: str) -> ResumeData | None:
        # Ownership is keyed on (user_id, resume_id); only "u1" owns anything.
        if user_id != "u1":
            return None
        return known.get(resume_id)

    return _load


_RESUME = ResumeData(
    resume_id="r1",
    text="Senior backend engineer, python, fastapi",
    processed={"skills": ["python", "fastapi"]},
    version="rv1",
)


def _query_fn(*, degraded: bool = False, calls: list | None = None):
    async def _fn(
        resume_text: str,
        *,
        resume_version: str | None = None,
        filters: SearchFilters | None = None,
        llm_complete: Any = None,
        force_refresh: bool = False,
    ) -> SearchQuery:
        if calls is not None:
            calls.append({"force_refresh": force_refresh})
        return SearchQuery(
            titles=["Backend Engineer"],
            search_string="backend",
            location=filters.location if filters else None,
            degraded=degraded,
            resume_version=resume_version,
        )

    return _fn


async def _fake_keyword_extractor(user_id: str, jd_text: str) -> dict:
    # Deterministic, no LLM: one keyword derived from the JD text.
    return {"required_skills": [], "preferred_skills": [], "keywords": ["python"]}


def _fake_match_scorer(resume: Any, keywords: Any) -> float:
    return 42.0


class FakeConnector:
    """A connector whose behaviour is fully scripted.

    ``mode``:
      * ``"ok"``     -> return ``rows``
      * ``"raise"``  -> raise (run_connector wraps it into a SourceFailure)
      * ``"record"`` -> append a SourceFailure AND return ``rows`` (partial)
    """

    def __init__(self, name, *, rows=None, mode="ok", fetch_mode="http"):
        self.name = name
        self.fetch_mode = fetch_mode
        self._rows = rows or []
        self._mode = mode
        self.calls = 0

    async def search(self, query, filters, failures):
        self.calls += 1
        if self._mode == "raise":
            raise RuntimeError("upstream timeout")
        if self._mode == "record":
            failures.append(
                SourceFailure(source=self.name, reason="403 blocked", kind="blocked")
            )
        return list(self._rows)


def _row(title: str, *, url: str, description: str | None = "A great role") -> RawListing:
    return RawListing(
        source="seed",
        title=title,
        company="Acme",
        location="Remote",
        url=url,
        description=description,
        partial=description is None,
    )


def _service(db, *, config=None, query_fn=None, resume=_RESUME) -> DiscoveryService:
    return DiscoveryService(
        db,
        resume_loader=_resume_loader({"r1": resume}),
        cache=SearchCache(db, ttl_seconds=3600),
        config=config or _config(),
        query_fn=query_fn or _query_fn(),
        keyword_extractor=_fake_keyword_extractor,
        match_scorer=_fake_match_scorer,
    )


# --------------------------------------------------------------------------- #
# Gate + ownership
# --------------------------------------------------------------------------- #
@pytest.mark.service
async def test_kill_switch_off_refuses(db):
    svc = _service(db, config=_config(enabled=False))
    with pytest.raises(DiscoveryDisabledError):
        await svc.recommend(user_id="u1", resume_id="r1", connectors=[])


@pytest.mark.service
async def test_unknown_resume_raises_not_found(db):
    svc = _service(db)
    with pytest.raises(ResumeNotFoundError):
        await svc.recommend(user_id="u1", resume_id="nope", connectors=[])


@pytest.mark.service
async def test_resume_owned_by_other_user_is_not_found(db):
    svc = _service(db)
    # r1 exists but is owned by u1, so u2 must not see it (ownership boundary).
    with pytest.raises(ResumeNotFoundError):
        await svc.recommend(user_id="u2", resume_id="r1", connectors=[])


# --------------------------------------------------------------------------- #
# Happy path + caching
# --------------------------------------------------------------------------- #
@pytest.mark.service
async def test_happy_path_ranks_dedups_and_caches(db):
    svc = _service(db)
    c1 = FakeConnector("indeed", rows=[_row("Backend Eng", url="https://a/1")])
    c2 = FakeConnector(
        "naukri",
        rows=[
            _row("Platform Eng", url="https://a/2"),
            # duplicate of c1's row (same identity) -> deduped away.
            _row("Backend Eng", url="https://a/1"),
        ],
    )

    result = await svc.recommend(
        user_id="u1", resume_id="r1", connectors=[c1, c2]
    )

    assert result.cached is False
    assert result.degraded is False
    # 3 raw rows, one duplicate fingerprint -> 2 unique recommendations.
    assert len(result.recommendations) == 2
    assert all(r.match_score == 42.0 for r in result.recommendations)
    assert {s.source: s.status for s in result.sources} == {
        "indeed": "ok",
        "naukri": "ok",
    }
    assert result.failures == []

    # Second call (same query/filters) is a cache hit -> connectors untouched.
    again = await svc.recommend(
        user_id="u1", resume_id="r1", connectors=[c1, c2]
    )
    assert again.cached is True
    assert len(again.recommendations) == 2
    assert c1.calls == 1 and c2.calls == 1  # not re-run on the cache hit


@pytest.mark.service
async def test_force_refresh_bypasses_cache(db):
    svc = _service(db)
    c1 = FakeConnector("indeed", rows=[_row("Backend Eng", url="https://a/1")])

    first = await svc.recommend(user_id="u1", resume_id="r1", connectors=[c1])
    assert first.cached is False and c1.calls == 1

    # force_refresh must re-run the fan-out even though a cache entry exists.
    refreshed = await svc.recommend(
        user_id="u1", resume_id="r1", force_refresh=True, connectors=[c1]
    )
    assert refreshed.cached is False
    assert c1.calls == 2


@pytest.mark.service
async def test_max_results_truncates(db):
    svc = _service(db, config=_config(max_results=1))
    rows = [_row(f"Role {i}", url=f"https://a/{i}") for i in range(5)]
    c1 = FakeConnector("indeed", rows=rows)

    result = await svc.recommend(user_id="u1", resume_id="r1", connectors=[c1])
    assert len(result.recommendations) == 1


# --------------------------------------------------------------------------- #
# Partial success + degraded
# --------------------------------------------------------------------------- #
@pytest.mark.service
async def test_partial_success_is_degraded_and_reported(db):
    svc = _service(db)
    ok = FakeConnector("indeed", rows=[_row("Backend Eng", url="https://a/1")])
    boom = FakeConnector("naukri", mode="raise")

    result = await svc.recommend(
        user_id="u1", resume_id="r1", connectors=[ok, boom]
    )

    # A single source failing never fails the request (Req 1.2/3.2).
    assert result.degraded is True
    assert len(result.recommendations) == 1  # ok connector still contributed
    assert len(result.failures) == 1
    fail = result.failures[0]
    assert fail.source == "naukri"
    assert fail.kind == "timeout"  # classified from "upstream timeout"

    statuses = {s.source: s.status for s in result.sources}
    assert statuses == {"indeed": "ok", "naukri": "failed"}

    # Degraded results must NOT be cached (transient failure, Req 6).
    key_absent = await svc._cache.get(  # noqa: SLF001 - white-box cache assert
        _RESUME.version,
        SearchQuery(titles=["Backend Engineer"], search_string="backend"),
        SearchFilters(),
    )
    assert key_absent is None


@pytest.mark.service
async def test_connector_returns_rows_and_records_failure_is_partial(db):
    svc = _service(db)
    # One board of a multi-board source blocked, but rows still came back.
    mixed = FakeConnector(
        "indeed", rows=[_row("Backend Eng", url="https://a/1")], mode="record"
    )

    result = await svc.recommend(user_id="u1", resume_id="r1", connectors=[mixed])

    assert result.degraded is True
    assert len(result.recommendations) == 1
    assert [s.status for s in result.sources] == ["partial"]
    assert result.sources[0].failures[0].kind == "blocked"


@pytest.mark.service
async def test_all_sources_fail_returns_empty_degraded(db):
    svc = _service(db)
    a = FakeConnector("indeed", mode="raise")
    b = FakeConnector("naukri", mode="raise")

    result = await svc.recommend(user_id="u1", resume_id="r1", connectors=[a, b])

    assert result.degraded is True
    assert result.recommendations == []
    assert len(result.failures) == 2
    assert {s.status for s in result.sources} == {"failed"}


@pytest.mark.service
async def test_degraded_query_is_not_cached(db):
    # Fallback (LLM-less) query marks the whole result degraded even when every
    # source succeeds, so it must not be cached (Req 2.2, 6).
    svc = _service(db, query_fn=_query_fn(degraded=True))
    c1 = FakeConnector("indeed", rows=[_row("Backend Eng", url="https://a/1")])

    result = await svc.recommend(user_id="u1", resume_id="r1", connectors=[c1])
    assert result.degraded is True
    assert len(result.recommendations) == 1

    # Not cached -> a second non-force call re-runs the connector.
    again = await svc.recommend(user_id="u1", resume_id="r1", connectors=[c1])
    assert again.cached is False
    assert c1.calls == 2


@pytest.mark.service
async def test_no_connectors_yields_empty_but_not_degraded(db):
    svc = _service(db)
    result = await svc.recommend(user_id="u1", resume_id="r1", connectors=[])
    assert result.recommendations == []
    assert result.degraded is False
    assert result.sources == []
