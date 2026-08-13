"""Connector protocol + single-source failure contract (design §3.2, §6).

The cardinal rule: a single source failing must NOT propagate out of the
fan-out. These tests pin :func:`run_connector`'s enforcement of that contract
and the :class:`FailureReport` collection surface.

Requirements: 3.2, 4.
"""

from __future__ import annotations

import asyncio

import pytest

from app.job_discovery.connectors.base import (
    Connector,
    FailureReport,
    RawListing,
    classify_failure,
    run_connector,
)
from app.job_discovery.models import SearchFilters, SearchQuery, SourceFailure

pytestmark = pytest.mark.unit


class _OkConnector:
    name = "indeed"
    fetch_mode = "http"

    async def search(self, query, filters, failures):
        return [RawListing(source=self.name, title="Backend Engineer", company="Acme")]


class _RaisingConnector:
    name = "naukri"
    fetch_mode = "http"

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def search(self, query, filters, failures):
        raise self._exc


class _SelfReportingConnector:
    """A well-behaved connector that records its own failure and returns partial rows."""

    name = "careers-page"
    fetch_mode = "stealth"

    async def search(self, query, filters, failures):
        failures.append(SourceFailure(source=self.name, reason="page 2 blocked", kind="blocked"))
        return [RawListing(source=self.name, title="SRE", partial=True)]


def _query() -> SearchQuery:
    return SearchQuery(titles=["Backend Engineer"], search_string="backend engineer")


def _filters() -> SearchFilters:
    return SearchFilters(location="Bengaluru", results_wanted=10)


def test_connector_protocol_is_runtime_checkable():
    assert isinstance(_OkConnector(), Connector)
    # A plain object without the required members is not a Connector.
    assert not isinstance(object(), Connector)


async def test_successful_connector_returns_rows_and_no_failure():
    report = FailureReport()
    rows = await run_connector(_OkConnector(), _query(), _filters(), report)

    assert [r.title for r in rows] == ["Backend Engineer"]
    assert report.degraded is False
    assert len(report) == 0


async def test_failing_connector_yields_source_failure_not_exception():
    report = FailureReport()

    # Must NOT raise — the exception is converted into a collected failure.
    rows = await run_connector(
        _RaisingConnector(RuntimeError("scraper exploded")), _query(), _filters(), report
    )

    assert rows == []
    assert report.degraded is True
    assert len(report) == 1
    failure = report.failures[0]
    assert isinstance(failure, SourceFailure)
    assert failure.source == "naukri"
    assert "scraper exploded" in failure.reason
    assert failure.kind == "error"


async def test_connector_may_self_report_and_return_partial():
    report = FailureReport()
    rows = await run_connector(_SelfReportingConnector(), _query(), _filters(), report)

    assert len(rows) == 1
    assert rows[0].partial is True
    assert report.degraded is True
    assert report.failures[0].kind == "blocked"


async def test_cancellation_propagates():
    report = FailureReport()
    with pytest.raises(asyncio.CancelledError):
        await run_connector(
            _RaisingConnector(asyncio.CancelledError()), _query(), _filters(), report
        )
    # A cancellation is control-flow, not a source failure.
    assert len(report) == 0


@pytest.mark.parametrize(
    ("exc", "expected_kind"),
    [
        (TimeoutError("request timed out"), "timeout"),
        (RuntimeError("HTTP 403 Forbidden"), "blocked"),
        (RuntimeError("429 rate limit exceeded"), "blocked"),
        (ConnectionError("connection refused"), "unavailable"),
        (RuntimeError("503 service unavailable"), "unavailable"),
        (ValueError("bad selector"), "error"),
    ],
)
def test_classify_failure_taxonomy(exc, expected_kind):
    assert classify_failure(exc) == expected_kind


def test_failure_report_seeds_and_records():
    seed = SourceFailure(source="indeed", reason="seeded", kind="error")
    report = FailureReport([seed])
    assert len(report) == 1

    recorded = report.record("naukri", "blocked", kind="blocked")
    assert recorded.source == "naukri"
    assert len(report) == 2
    assert [f.source for f in report] == ["indeed", "naukri"]
