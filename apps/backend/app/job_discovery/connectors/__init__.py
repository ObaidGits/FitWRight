"""Job-discovery source connectors.

Each connector adapts one job source (fixed board, custom site recipe, known
ATS) to the common :class:`~app.job_discovery.connectors.base.Connector`
protocol. The shared contract — return :class:`RawListing` rows, record a
single :class:`~app.job_discovery.models.SourceFailure` instead of raising — is
defined in :mod:`app.job_discovery.connectors.base`; concrete connectors
(JobSpy, site recipe, jd_adapter) land in later waves.
"""

from __future__ import annotations

from app.job_discovery.connectors.base import (
    Connector,
    FailureReport,
    RawListing,
    classify_failure,
    run_connector,
)

__all__ = [
    "Connector",
    "FailureReport",
    "RawListing",
    "classify_failure",
    "run_connector",
]
