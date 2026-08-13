"""Connector protocol and the single-source failure contract (design §3.2, §6).

A *connector* is one source of raw job postings: a fixed-board scraper
(JobSpy), a custom site recipe, or a known-ATS adapter. The discovery service
fans out across every enabled connector and merges their output, so the cardinal
rule is:

    A single source failing must NEVER fail the whole request.

Connectors therefore return :class:`RawListing` rows on success and, on failure,
record exactly one :class:`~app.job_discovery.models.SourceFailure` into a shared
:class:`FailureReport` instead of raising. The orchestrator (Wave 3) treats a
non-empty report as the ``degraded`` signal and still returns partial results.

Use :func:`run_connector` to invoke a connector safely: it enforces the contract
(catches any exception, records a classified ``SourceFailure``, returns ``[]``)
so individual connectors don't each have to re-implement the try/except.

Requirements: 3.2, 4.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.job_discovery.models import (
    FetchMode,
    SearchFilters,
    SearchQuery,
    SourceFailure,
)

__all__ = [
    "RawListing",
    "Connector",
    "FailureReport",
    "classify_failure",
    "run_connector",
]


@dataclass
class RawListing:
    """A job posting as a connector emits it, *before* normalization (design §3.2).

    Connectors do the minimum shaping needed to name a source and carry the
    fields they can extract; ``normalize.py`` (Wave 1) is responsible for
    cleaning, fingerprinting, and converting these into the canonical
    :class:`~app.job_discovery.models.JobListing`. Unknown/source-specific
    fields ride along in :attr:`extra` so no data is lost before normalization.

    ``partial`` marks a row scraped without a full description (e.g. a search
    results page that lists titles but not the JD body); the ranker scores such
    rows on titles/keywords only and the tailor handoff back-fills the full JD
    on demand (Req 7.2, 8).
    """

    source: str
    title: str
    company: str = ""
    location: str = ""
    url: str = ""
    is_remote: bool | None = None
    description: str | None = None
    posted_at: datetime | None = None
    salary: str | None = None
    # True when the description was not fetched (search-page-only row).
    partial: bool = False
    # Source-specific fields preserved verbatim for normalization/debugging.
    extra: dict = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """One source of raw job listings (design §3.2, §6).

    Implementations MUST NOT raise on a single-source failure. Either handle the
    failure internally and record a :class:`SourceFailure` (append to the
    ``failures`` list), or let :func:`run_connector` wrap the call and do it for
    them. Returning an empty list is a valid, non-fatal outcome.
    """

    # Stable identifier surfaced in results and failure reports
    # (e.g. "indeed", "naukri", or a recipe slug).
    name: str

    # How this connector fetches (informational; dispatch lives in fetch.py).
    fetch_mode: FetchMode

    async def search(
        self,
        query: SearchQuery,
        filters: SearchFilters,
        failures: list[SourceFailure],
    ) -> list[RawListing]:
        """Return raw listings for ``query``/``filters``.

        On a recoverable per-source problem, append one :class:`SourceFailure`
        to ``failures`` and return whatever partial rows were gathered (possibly
        ``[]``). Never raise for a single-source failure.
        """
        ...


class FailureReport:
    """A mutable collection of non-fatal :class:`SourceFailure` records.

    Thin wrapper over a list so the orchestrator can pass one report through the
    whole fan-out and read ``degraded`` / ``failures`` off it afterwards. Kept
    deliberately simple: connectors only ever *append*.
    """

    def __init__(self, failures: list[SourceFailure] | None = None) -> None:
        self.failures: list[SourceFailure] = list(failures) if failures else []

    def record(
        self, source: str, reason: str, *, kind: str | None = None
    ) -> SourceFailure:
        """Append and return a :class:`SourceFailure`."""
        failure = SourceFailure(source=source, reason=reason, kind=kind)
        self.failures.append(failure)
        return failure

    @property
    def degraded(self) -> bool:
        """True when at least one source failed."""
        return bool(self.failures)

    def __len__(self) -> int:
        return len(self.failures)

    def __iter__(self):
        return iter(self.failures)


def classify_failure(exc: BaseException) -> str:
    """Coarsely classify an exception into a :attr:`SourceFailure.kind`.

    Keeps the taxonomy small and stable so the UI can group failures:
    ``"timeout" | "blocked" | "unavailable" | "error"``. Matching is on type
    name / message text to avoid importing optional scraper dependencies here.
    """
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    haystack = f"{name} {message}"

    if "timeout" in haystack or "timederror" in haystack:
        return "timeout"
    if any(
        token in haystack
        for token in ("403", "forbidden", "blocked", "captcha", "denied", "429", "rate limit")
    ):
        return "blocked"
    if any(
        token in haystack
        for token in ("connection", "unreachable", "dns", "resolve", "502", "503", "504", "unavailable")
    ):
        return "unavailable"
    return "error"


async def run_connector(
    connector: Connector,
    query: SearchQuery,
    filters: SearchFilters,
    report: FailureReport,
) -> list[RawListing]:
    """Invoke ``connector.search`` and enforce the single-source failure contract.

    Any exception (except cooperative cancellation) is caught, classified, and
    recorded on ``report`` as a :class:`SourceFailure`; the connector's own
    output is returned on success. The caller therefore never has to guard a
    single connector, and one failing source can never abort the fan-out.

    Returns the listings the connector produced, or ``[]`` when it failed.
    """
    try:
        return await connector.search(query, filters, report.failures)
    except asyncio.CancelledError:
        # Cancellation is control-flow, not a source failure — let it propagate.
        raise
    except BaseException as exc:  # noqa: BLE001 — deliberate catch-all per contract
        report.record(
            source=getattr(connector, "name", "unknown"),
            reason=str(exc) or type(exc).__name__,
            kind=classify_failure(exc),
        )
        return []
