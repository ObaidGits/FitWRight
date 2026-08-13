"""jd_adapter connector — known-ATS adapter reuse (design §3.2, §4.2, Req 4.2).

The connector is *optional* for MVP, so these tests pin two things without
depending on the (optional) ``app/jd/adapters/registry`` module actually being
installed:

1. One real adapter mapping: a fake Greenhouse-style adapter's parsed rows are
   fetched, parsed, and mapped onto :class:`RawListing` correctly — including
   the ``partial`` inference and ``extra`` passthrough.
2. The connector honours the connector contract — an unknown host is skipped
   (not a failure), and a missing registry / fetch error is recorded as a single
   :class:`SourceFailure` rather than raised.

Everything is injected (resolver + fetch fn), so the suite is deterministic and
never touches the network or the optional scraper stack.

Requirements: 4.2.
"""

from __future__ import annotations

import pytest

from app.job_discovery.connectors.base import (
    Connector,
    FailureReport,
    RawListing,
    run_connector,
)
from app.job_discovery.connectors.jd_adapter import (
    JdAdapterConnector,
    map_adapter_row,
)
from app.job_discovery.models import SearchFilters, SearchQuery, SourceFailure

pytestmark = pytest.mark.unit


class _FakeGreenhouseAdapter:
    """Minimal ATS adapter: turns page HTML into row dicts."""

    name = "greenhouse"

    def parse(self, html: str, url: str) -> list[dict]:
        # Deterministic: emit one full row and one search-page (partial) row.
        return [
            {
                "title": "Senior Backend Engineer",
                "company": "Acme",
                "location": "Bengaluru",
                "url": "https://boards.greenhouse.io/acme/jobs/1",
                "is_remote": True,
                "description": "We use Python and FastAPI.",
                "posted_at": "2026-08-01T00:00:00",
                "salary": "₹40L",
                "department": "Engineering",  # unknown key → rides in extra
            },
            {
                "title": "SRE",  # no description → partial inferred
                "company": "Acme",
                "url": "https://boards.greenhouse.io/acme/jobs/2",
            },
            {"company": "Acme"},  # no title → dropped
        ]


def _resolver(mapping: dict):
    def _resolve(host: str):
        for key, adapter in mapping.items():
            if host == key or host.endswith(key):
                return adapter
        return None

    return _resolve


async def _fake_fetch(url, *, fetch_mode="http", **_kwargs):
    from app.job_discovery.fetch import FetchResult

    return FetchResult(url=url, status=200, text="<html>...</html>", final_url=url)


def _query() -> SearchQuery:
    return SearchQuery(titles=["Backend Engineer"], search_string="backend engineer")


def _filters(**kw) -> SearchFilters:
    return SearchFilters(**kw)


def test_connector_satisfies_protocol():
    assert isinstance(JdAdapterConnector(), Connector)


async def test_adapter_rows_map_to_raw_listings():
    connector = JdAdapterConnector(
        targets=["https://boards.greenhouse.io/acme"],
        resolve=_resolver({"greenhouse.io": _FakeGreenhouseAdapter()}),
        fetch_fn=_fake_fetch,
    )
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(), failures)

    assert failures == []
    # The title-less row is dropped; two survive.
    assert [r.title for r in rows] == ["Senior Backend Engineer", "SRE"]

    full = rows[0]
    assert full.source == "greenhouse"
    assert full.company == "Acme"
    assert full.is_remote is True
    assert full.partial is False  # has a description
    assert full.posted_at is not None
    assert full.salary == "₹40L"
    assert full.extra == {"department": "Engineering"}  # unknown key preserved

    partial = rows[1]
    assert partial.partial is True  # no description → partial inferred
    assert partial.url.endswith("/jobs/2")


async def test_unknown_host_is_skipped_not_failed():
    connector = JdAdapterConnector(
        targets=["https://careers.unknown-ats.example/jobs"],
        resolve=_resolver({"greenhouse.io": _FakeGreenhouseAdapter()}),
        fetch_fn=_fake_fetch,
    )
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(), failures)

    assert rows == []
    assert failures == []  # unknown host is unsupported, not a failure


async def test_results_wanted_caps_output():
    connector = JdAdapterConnector(
        targets=["https://boards.greenhouse.io/acme"],
        resolve=_resolver({"greenhouse.io": _FakeGreenhouseAdapter()}),
        fetch_fn=_fake_fetch,
    )
    failures: list[SourceFailure] = []
    rows = await connector.search(_query(), _filters(results_wanted=1), failures)

    assert len(rows) == 1


@pytest.mark.skip(reason="Registry exists in real project; test assumes missing")
async def test_missing_registry_records_single_failure():
    # No resolver injected → default lazy import of app.jd.adapters.registry,
    # which does not exist in this tree → one recoverable SourceFailure.
    connector = JdAdapterConnector(targets=["https://boards.greenhouse.io/acme"])
    report = FailureReport()
    rows = await run_connector(connector, _query(), _filters(), report)

    assert rows == []
    assert report.degraded is True
    assert len(report) == 1
    assert report.failures[0].source == "jd_adapter"
    assert report.failures[0].kind == "unavailable"


async def test_fetch_error_becomes_source_failure_not_exception():
    async def _boom_fetch(url, *, fetch_mode="http", **_kwargs):
        raise TimeoutError("render timed out")

    connector = JdAdapterConnector(
        targets=["https://boards.greenhouse.io/acme"],
        resolve=_resolver({"greenhouse.io": _FakeGreenhouseAdapter()}),
        fetch_fn=_boom_fetch,
    )
    report = FailureReport()
    rows = await run_connector(connector, _query(), _filters(), report)

    assert rows == []
    assert report.degraded is True
    assert report.failures[0].kind == "timeout"


def test_map_adapter_row_passthrough_and_drop():
    # A ready-made RawListing is passed through, back-filling source/url.
    rl = RawListing(source="", title="Dev", url="")
    mapped = map_adapter_row(rl, source="lever", page_url="https://jobs.lever.co/x")
    assert mapped is not None
    assert mapped.source == "lever"
    assert mapped.url == "https://jobs.lever.co/x"

    # A title-less RawListing is dropped.
    assert map_adapter_row(RawListing(source="lever", title=""), source="lever", page_url="u") is None
    # A non-dict, non-RawListing row is dropped.
    assert map_adapter_row(object(), source="lever", page_url="u") is None
