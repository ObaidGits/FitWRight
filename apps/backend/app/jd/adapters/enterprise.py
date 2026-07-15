"""Enterprise ATS detection adapters (§6, Phase 4).

Oracle Recruiting Cloud, SAP SuccessFactors, Taleo, BambooHR, and Rippling.

These are **detection + routing** adapters. Unlike Ashby/Greenhouse/Lever/
SmartRecruiters (which expose stable, documented, anonymous JSON APIs), these
platforms either require authentication for their APIs or expose no stable
public JSON endpoint. Rather than fabricate an endpoint that may not exist
(which would violate the honest-extraction principle), each adapter:

  1. Recognizes its domain (improves platform metrics + drift attribution), and
  2. Returns ``None`` from ``extract_api_url`` so the orchestrator falls through
     to the JSON-LD → hydration → DOM → Playwright cascade.

``REQUIRES_JS`` is set truthfully so the browser-decision engine knows these are
JS-heavy SPAs (see ``browser/decision.py``, which also lists their domains).

BambooHR is the exception: its public careers pages are largely server-rendered
with JobPosting JSON-LD, so ``REQUIRES_JS = False`` (the static cascade usually
succeeds without a browser).
"""

from __future__ import annotations

from urllib.parse import ParseResult

from app.jd.models import ConfidenceResult, ExtractionResult, FieldProvenance

__all__ = [
    "OracleAdapter",
    "SuccessFactorsAdapter",
    "TaleoAdapter",
    "BambooHrAdapter",
    "RipplingAdapter",
]


class _DetectionAdapter:
    """Base for detection-only adapters (defer extraction to the cascade)."""

    PLATFORM_ID = "unknown"
    VERSION = "1.0.0"
    RATE_LIMIT = 30
    REQUIRES_JS = True
    _HOSTS: tuple[str, ...] = ()
    _HOST_SUFFIXES: tuple[str, ...] = ()

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        if host in self._HOSTS:
            return True
        return any(host == s or host.endswith("." + s) for s in self._HOST_SUFFIXES)

    def extract_api_url(self, parsed: ParseResult) -> str | None:
        # No stable anonymous JSON API — defer to JSON-LD / DOM / Playwright.
        return None

    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        # Contract completeness only; never invoked (extract_api_url → None).
        title = data.get("title", "") if isinstance(data, dict) else ""
        return ExtractionResult(
            content=title,
            title=FieldProvenance(
                value=title, source="platform_api", confidence=60,
                extractor_version=self.VERSION, raw_location="api:title",
            ) if title else None,
            confidence=ConfidenceResult(level="MEDIUM", score=55, reasons=[f"{self.PLATFORM_ID} (detection-only)"]),
            source="platform_api",
            canonical_url=source_url,
        )


class OracleAdapter(_DetectionAdapter):
    PLATFORM_ID = "oracle"
    REQUIRES_JS = True
    # Oracle Recruiting Cloud / HCM career sites.
    _HOST_SUFFIXES = ("oraclecloud.com", "oracle.com")

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        # Oracle Cloud recruiting is hosted under *.oraclecloud.com paths like
        # /hcmUI/CandidateExperience/... — match the cloud host generically.
        if host.endswith("oraclecloud.com"):
            return True
        # Some tenants use fa-*.oraclecloud.com or company-specific ORC hosts.
        return "oraclecloud" in host


class SuccessFactorsAdapter(_DetectionAdapter):
    PLATFORM_ID = "successfactors"
    REQUIRES_JS = True
    _HOST_SUFFIXES = ("successfactors.com", "successfactors.eu", "sapsf.com", "sapsf.eu")

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        if any(host == s or host.endswith("." + s) for s in self._HOST_SUFFIXES):
            return True
        # SAP career sites: careers.sap.com / jobs.sap.com
        return host in ("careers.sap.com", "jobs.sap.com")


class TaleoAdapter(_DetectionAdapter):
    PLATFORM_ID = "taleo"
    REQUIRES_JS = True
    _HOST_SUFFIXES = ("taleo.net", "tbe.taleo.net")


class BambooHrAdapter(_DetectionAdapter):
    PLATFORM_ID = "bamboohr"
    # BambooHR careers pages are largely server-rendered with JobPosting JSON-LD.
    REQUIRES_JS = False
    _HOST_SUFFIXES = ("bamboohr.com",)

    def can_handle(self, parsed: ParseResult) -> bool:
        host = (parsed.hostname or "").lower()
        # {company}.bamboohr.com/careers/{id}
        return host == "bamboohr.com" or host.endswith(".bamboohr.com")


class RipplingAdapter(_DetectionAdapter):
    PLATFORM_ID = "rippling"
    REQUIRES_JS = True
    _HOST_SUFFIXES = ("rippling.com", "rippling-ats.com", "ats.rippling.com")
