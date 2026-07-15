"""Platform adapters for JD extraction (§6 of enhancement plan)."""

from app.jd.adapters.registry import detect_platform, get_adapter

__all__ = ["detect_platform", "get_adapter"]
