"""Admin read of user error reports.

A thin use-case seam over ``app.admin.repo`` so callers outside this module do not
reach into its repository directly (ARCHITECTURE Amendment E - mutation rights
belong to the owning module). ``routers/error_reports.py`` previously imported
``app.admin.repo``, which the module-ownership guard flags: a foreign router
holding a repo handle is how a module's storage details leak into surfaces that
have no business knowing them.

Deliberately thin. The audit trail stays in the router, because *who looked* is a
property of the request rather than of the query.
"""

from __future__ import annotations

from app.admin.repo import AdminErrorReportData, get_admin_repo

__all__ = ["AdminErrorReportData", "list_error_reports_page"]


async def list_error_reports_page(
    *, cursor: str | None, limit: int
) -> tuple[list[AdminErrorReportData], str | None]:
    """One newest-first page of error reports across users.

    Raises ``app.admin.cursor.CursorError`` for a malformed cursor, which the
    caller turns into a 400 - an invalid cursor is a bad request, not a fault.
    """
    return await get_admin_repo().list_error_reports(cursor=cursor, limit=limit)
