"""Unit tests for the JobSpy connector (``app.job_discovery.connectors.jobspy``).

The real ``jobspy``/``pandas`` stack lives behind the optional ``job-discovery``
extra and is NOT installed in the base test environment, so these tests inject a
fake ``scrape_jobs`` that returns a **recorded JobSpy-shaped DataFrame** — a
lightweight stand-in exposing the same ``to_dict(orient="records")`` /
``empty`` surface the mapping consumes, populated with columns a real JobSpy
run emits (``site``, ``job_url``, ``date_posted``, split salary columns, a
``NaN`` hole, a description-less "partial" row). This pins the
DataFrame→RawListing mapping, ``country_indeed``/filter pass-through, the
threadpool dispatch, and the per-site failure → SourceFailure contract without a
pandas dependency at test time.

Requirements: 3.1, 3.2, 3.3, 3.4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.job_discovery.connectors import jobspy as jobspy_mod
from app.job_discovery.connectors.base import Connector, FailureReport, run_connector
from app.job_discovery.connectors.jobspy import JobSpyConnector
from app.job_discovery.models import SearchFilters, SearchQuery, SourceFailure

pytestmark = pytest.mark.unit

_NAN = float("nan")


class _RecordedDataFrame:
    """Minimal pandas-``DataFrame`` stand-in over recorded row dicts."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    @property
    def empty(self) -> bool:
        return not self._rows

    def to_dict(self, orient: str = "records") -> list[dict[str, Any]]:
        assert orient == "records"
        return [dict(r) for r in self._rows]


def _recorded_indeed_df() -> _RecordedDataFrame:
    """A recorded two-row Indeed result: one full listing, one partial row."""
    return _RecordedDataFrame(
        [
            {
                "site": "indeed",
                "title": "  Senior Backend Engineer ",
                "company": "Acme Corp",
                "location": "Bengaluru, India",
                "job_url": "https://indeed.com/viewjob?jk=abc123",
                "job_url_direct": "https://acme.example/jobs/be",
                "description": "Build APIs in Python and Go.",
                "is_remote": True,
                "date_posted": "2026-08-01",
                "min_amount": 2500000,
                "max_amount": 3500000,
                "currency": "INR",
                "interval": "yearly",
                "job_type": "fulltime",
                "company_url": "https://acme.example",
            },
            {
                # Search-page-only row: no description, NaN remoteness, no salary.
                "site": "indeed",
                "title": "Platform Engineer",
                "company": "Globex",
                "location": "Remote",
                "job_url": "https://indeed.com/viewjob?jk=def456",
                "description": None,
                "is_remote": _NAN,
                "date_posted": _NAN,
                "min_amount": _NAN,
                "max_amount": _NAN,
            },
            {
                # Unusable row (no title) — must be dropped.
                "site": "indeed",
                "title": _NAN,
                "company": "Ghost Inc",
                "job_url": "https://indeed.com/viewjob?jk=ghost",
            },
        ]
    )


def _query() -> SearchQuery:
    return SearchQuery(
        titles=["Backend Engineer"],
        search_string="backend engineer python",
        location="Bengaluru",
        country_indeed="india",
    )


def _filters(**overrides: Any) -> SearchFilters:
    base = dict(location="Bengaluru", is_remote=True, hours_old=72, results_wanted=25)
    base.update(overrides)
    return SearchFilters(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Protocol conformance
# --------------------------------------------------------------------------- #
def test_connector_satisfies_protocol():
    assert isinstance(JobSpyConnector(sites=["indeed"]), Connector)


# --------------------------------------------------------------------------- #
# DataFrame -> RawListing mapping
# --------------------------------------------------------------------------- #
async def test_maps_recorded_dataframe_to_raw_listings():
    def fake_scrape(**kwargs: Any) -> _RecordedDataFrame:
        return _recorded_indeed_df()

    connector = JobSpyConnector(sites=["indeed"], scrape_fn=fake_scrape)
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(), failures)

    # Titleless row dropped; two usable rows remain.
    assert [r.title for r in rows] == ["Senior Backend Engineer", "Platform Engineer"]
    assert failures == []

    full = rows[0]
    assert full.source == "indeed"
    assert full.company == "Acme Corp"
    assert full.url == "https://indeed.com/viewjob?jk=abc123"
    assert full.is_remote is True
    assert full.description == "Build APIs in Python and Go."
    assert full.partial is False
    assert full.posted_at == datetime(2026, 8, 1)
    assert full.salary == "INR 2500000-3500000 per yearly"
    assert full.extra == {
        "job_type": "fulltime",
        "site": "indeed",
        "company_url": "https://acme.example",
    }


async def test_partial_and_nan_handling():
    connector = JobSpyConnector(
        sites=["indeed"], scrape_fn=lambda **_: _recorded_indeed_df()
    )
    rows = await connector.search(_query(), _filters(), [])

    partial = rows[1]
    assert partial.title == "Platform Engineer"
    assert partial.description is None
    assert partial.partial is True
    # NaN cells degrade cleanly, never leak the sentinel.
    assert partial.is_remote is None
    assert partial.posted_at is None
    assert partial.salary is None
    assert partial.extra == {"site": "indeed"}


async def test_empty_dataframe_yields_no_rows_no_failure():
    connector = JobSpyConnector(
        sites=["indeed"], scrape_fn=lambda **_: _RecordedDataFrame([])
    )
    failures: list[SourceFailure] = []
    assert await connector.search(_query(), _filters(), failures) == []
    assert failures == []


# --------------------------------------------------------------------------- #
# Filter / country_indeed pass-through (Req 3.4)
# --------------------------------------------------------------------------- #
async def test_filters_and_country_indeed_passed_through():
    captured: list[dict[str, Any]] = []

    def spy_scrape(**kwargs: Any) -> _RecordedDataFrame:
        captured.append(kwargs)
        return _RecordedDataFrame([])

    connector = JobSpyConnector(sites=["indeed"], scrape_fn=spy_scrape)
    await connector.search(_query(), _filters(), [])

    assert len(captured) == 1
    params = captured[0]
    assert params["site_name"] == ["indeed"]
    assert params["search_term"] == "backend engineer python"
    assert params["location"] == "Bengaluru"
    assert params["is_remote"] is True
    assert params["hours_old"] == 72
    assert params["results_wanted"] == 25
    assert params["country_indeed"] == "india"


async def test_country_indeed_falls_back_to_query_and_default_results():
    captured: list[dict[str, Any]] = []

    def spy_scrape(**kwargs: Any) -> _RecordedDataFrame:
        captured.append(kwargs)
        return _RecordedDataFrame([])

    connector = JobSpyConnector(
        sites=["indeed"], results_wanted=15, scrape_fn=spy_scrape
    )
    # Filters omit country_indeed and results_wanted -> fall back to query/default.
    filters = SearchFilters(location="Pune")
    await connector.search(_query(), filters, [])

    params = captured[0]
    assert params["country_indeed"] == "india"  # from the query
    assert params["results_wanted"] == 15  # connector default
    # None-valued filters are dropped so JobSpy applies its own defaults.
    assert "is_remote" not in params
    assert "hours_old" not in params


# --------------------------------------------------------------------------- #
# Per-site failure isolation (Req 3.2)
# --------------------------------------------------------------------------- #
async def test_per_site_failure_isolated_others_still_return():
    def scrape(**kwargs: Any) -> _RecordedDataFrame:
        (site,) = kwargs["site_name"]
        if site == "naukri":
            raise RuntimeError("HTTP 403 Forbidden")
        return _recorded_indeed_df()

    connector = JobSpyConnector(sites=["indeed", "naukri"], scrape_fn=scrape)
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(), failures)

    # Indeed rows still returned despite naukri failing.
    assert [r.title for r in rows] == ["Senior Backend Engineer", "Platform Engineer"]
    assert len(failures) == 1
    assert failures[0].source == "naukri"
    assert failures[0].kind == "blocked"
    assert "403" in failures[0].reason


async def test_search_never_raises_under_run_connector_contract():
    def scrape(**kwargs: Any) -> _RecordedDataFrame:
        raise RuntimeError("scraper exploded")

    connector = JobSpyConnector(sites=["indeed"], scrape_fn=scrape)
    report = FailureReport()
    rows = await run_connector(connector, _query(), _filters(), report)

    assert rows == []
    assert report.degraded is True
    assert report.failures[0].source == "indeed"


# --------------------------------------------------------------------------- #
# Missing optional dependency degrades, never crashes (Req 3.1)
# --------------------------------------------------------------------------- #
async def test_missing_jobspy_dependency_degrades_to_failure(monkeypatch):
    def boom() -> Any:
        raise ImportError("No module named 'jobspy'")

    monkeypatch.setattr(jobspy_mod, "_load_scrape_jobs", boom)

    connector = JobSpyConnector(sites=["indeed"])  # no injected scrape_fn
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(), failures)

    assert rows == []
    assert len(failures) == 1
    assert failures[0].source == "jobspy"
    assert failures[0].kind == "unavailable"
    assert "not installed" in failures[0].reason
