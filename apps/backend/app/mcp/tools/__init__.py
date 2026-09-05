"""MCP tool modules (Task 5+).

Importing this package registers every tool module on the memoized FastMCP
instance: ``app.mcp.server.get_mcp_instance`` imports this package after the
instance exists, and each submodule decorates its tools at import time.

New tool groups (Tasks 6-8): add the module here, following the
``resumes.py`` / ``applications.py`` pattern.
"""

from __future__ import annotations

from app.mcp.tools import applications, reminders, resumes  # noqa: F401  (import = registration)
