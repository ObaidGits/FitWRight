"""Abstract base for platform adapters (§6 of enhancement plan)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import ParseResult

from app.jd.models import ExtractionResult

__all__ = ["PlatformAdapter"]


class PlatformAdapter(ABC):
    """Base class for ATS platform adapters.

    Each adapter knows how to:
    1. Detect whether a URL belongs to its platform
    2. Construct the public API URL from the job page URL
    3. Parse the API response into a structured ExtractionResult
    """

    PLATFORM_ID: str = "unknown"
    VERSION: str = "1.0.0"
    RATE_LIMIT: int = 60  # max requests/min to this platform's API
    REQUIRES_JS: bool = False  # If True, skip API and go straight to Playwright

    @abstractmethod
    def can_handle(self, parsed: ParseResult) -> bool:
        """Return True if this adapter handles the given URL."""
        ...

    @abstractmethod
    def extract_api_url(self, parsed: ParseResult) -> str | None:
        """Construct the API endpoint URL, or None if the URL can't be parsed."""
        ...

    @abstractmethod
    def parse_response(self, data: dict, source_url: str) -> ExtractionResult:
        """Parse the API JSON response into a structured ExtractionResult."""
        ...
