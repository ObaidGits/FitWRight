"""Platform detection registry (§6 of enhancement plan).

Maps URLs to platform adapters via domain/path pattern matching.
Detection runs in < 1ms (pure string matching, no I/O).
"""

from __future__ import annotations

from urllib.parse import ParseResult, urlparse

from app.jd.adapters.ashby import AshbyAdapter
from app.jd.adapters.base import PlatformAdapter
from app.jd.adapters.enterprise import (
    BambooHrAdapter,
    OracleAdapter,
    RipplingAdapter,
    SuccessFactorsAdapter,
    TaleoAdapter,
)
from app.jd.adapters.greenhouse import GreenhouseAdapter
from app.jd.adapters.icims import IcimsAdapter
from app.jd.adapters.indeed import IndeedAdapter
from app.jd.adapters.lever import LeverAdapter
from app.jd.adapters.linkedin import LinkedInAdapter
from app.jd.adapters.smartrecruiters import SmartRecruitersAdapter
from app.jd.adapters.workday import WorkdayAdapter

__all__ = ["detect_platform", "get_adapter"]

# Registered adapters (order doesn't matter — detection is domain-based)
_ADAPTERS: list[PlatformAdapter] = [
    AshbyAdapter(),
    GreenhouseAdapter(),
    LeverAdapter(),
    WorkdayAdapter(),
    IcimsAdapter(),
    SmartRecruitersAdapter(),
    LinkedInAdapter(),
    IndeedAdapter(),
    # Phase 4 enterprise detection adapters (defer extraction to the cascade).
    OracleAdapter(),
    SuccessFactorsAdapter(),
    TaleoAdapter(),
    BambooHrAdapter(),
    RipplingAdapter(),
]


def detect_platform(url: str) -> str | None:
    """Detect the ATS platform from a URL. Returns platform ID or None."""
    parsed = urlparse(url)
    for adapter in _ADAPTERS:
        if adapter.can_handle(parsed):
            return adapter.PLATFORM_ID
    return None


def get_adapter(url: str) -> PlatformAdapter | None:
    """Get the adapter for a URL, or None if no platform detected."""
    parsed = urlparse(url)
    for adapter in _ADAPTERS:
        if adapter.can_handle(parsed):
            return adapter
    return None
