"""JobSpy connector: the fixed-board fast lane (design §3.2, §6; Req 3).

`python-jobspy <https://github.com/Bunsly/JobSpy>`_ scrapes a handful of fixed
job boards (Indeed, Naukri, LinkedIn, …) and returns a single pandas
``DataFrame``. This connector adapts that to the discovery
:class:`~app.job_discovery.connectors.base.Connector` protocol:

* **Lazy import** — ``jobspy`` (and its heavy ``pandas``/scraper deps) live
  behind the optional ``job-discovery`` extra. They are imported *inside*
  :meth:`JobSpyConnector.search` so the base app boots without them installed
  (Req 3.1 / 10.5); a missing dependency degrades to a single recorded
  :class:`~app.job_discovery.models.SourceFailure`, never an import crash.
* **Threadpool-wrapped** — ``scrape_jobs`` is blocking (network + parsing), so
  every call is dispatched through :func:`asyncio.to_thread` to keep the event
  loop free (Req 3.3).
* **Per-site isolation** — each configured board is scraped independently, so
  one board failing (blocked / timeout) yields exactly one ``SourceFailure``
  for *that* board while the others still return rows (Req 3.2). The connector
  never raises for a single-source failure.
* **Filter pass-through** — ``country_indeed`` and the
  :class:`~app.job_discovery.models.SearchFilters` (location, remoteness,
  freshness, count) are forwarded to ``scrape_jobs`` (Req 3.4); each row of the
  resulting ``DataFrame`` maps to a :class:`RawListing`.

The canonical cleaning / fingerprinting / dedup happens later in
``normalize.py``; this connector only names a source and carries the fields it
can read off each row.

Design reference: ``.kiro/specs/job-discovery/design.md`` §3 (JobSpy connector).
Requirements: 3.1, 3.2, 3.3, 3.4.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any, Callable

from app.config import settings
from app.job_discovery.connectors.base import RawListing, classify_failure
from app.job_discovery.models import SearchFilters, SearchQuery, SourceFailure

# A ``scrape_jobs``-shaped callable: keyword-only board query -> a DataFrame-like
# object (anything exposing ``to_dict(orient="records")`` or iterable rows).
ScrapeFn = Callable[..., Any]


def _load_scrape_jobs() -> ScrapeFn:
    """Lazily import ``jobspy.scrape_jobs`` (optional ``job-discovery`` extra).

    Raises ``ImportError`` when the extra is not installed; the caller converts
    that into a recoverable :class:`SourceFailure` rather than letting it abort
    the fan-out.
    """
    from jobspy import scrape_jobs  # type: ignore[import-not-found]

    return scrape_jobs


def _is_missing(value: Any) -> bool:
    """True for ``None`` and pandas/NumPy ``NaN``/``NaT`` sentinels.

    ``NaN``/``NaT`` are the only values that are unequal to themselves, so this
    detects them without importing pandas — the mapping stays usable in tests
    that inject a plain fixture with ``float('nan')`` holes.
    """
    if value is None:
        return True
    try:
        return bool(value != value)  # noqa: PLR0124 - NaN self-inequality probe
    except Exception:  # pragma: no cover - exotic objects define __eq__ oddly
        return False


def _str(value: Any) -> str:
    """Coerce a cell to a trimmed string; missing/NaN becomes ``""``."""
    if _is_missing(value):
        return ""
    return str(value).strip()


def _str_opt(value: Any) -> str | None:
    """Coerce a cell to a trimmed string or ``None`` when absent/blank."""
    return _str(value) or None


def _bool_opt(value: Any) -> bool | None:
    """Coerce a cell to ``bool`` or ``None`` when absent/NaN."""
    if _is_missing(value):
        return None
    return bool(value)


def _datetime_opt(value: Any) -> datetime | None:
    """Best-effort coercion of a ``date_posted`` cell to :class:`datetime`.

    JobSpy may hand back a ``datetime``, a ``date``, or an ISO-ish string
    depending on the board; anything unparseable degrades to ``None`` (the row
    is still usable, just without a posted date).
    """
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _salary(row: dict[str, Any]) -> str | None:
    """Derive a human salary string from JobSpy's split salary columns.

    Prefers an explicit ``salary`` cell; otherwise composes one from
    ``min_amount``/``max_amount`` (+ ``currency``/``interval``) when present.
    """
    explicit = _str_opt(row.get("salary"))
    if explicit:
        return explicit

    lo = row.get("min_amount")
    hi = row.get("max_amount")
    lo_s = _str(lo)
    hi_s = _str(hi)
    if not lo_s and not hi_s:
        return None

    currency = _str(row.get("currency"))
    interval = _str(row.get("interval"))
    if lo_s and hi_s:
        amount = f"{lo_s}-{hi_s}"
    else:
        amount = lo_s or hi_s
    parts = [p for p in (currency, amount, f"per {interval}" if interval else "") if p]
    return " ".join(parts) or None


def _records(df: Any) -> list[dict[str, Any]]:
    """Normalize a JobSpy result into a list of plain dict rows.

    Accepts a pandas ``DataFrame`` (via ``to_dict(orient="records")``), a
    list/iterable of dicts, or ``None`` (empty). Keeping this tolerant lets the
    unit tests exercise the mapping against a lightweight recorded fixture
    without a pandas dependency at test time.
    """
    if df is None:
        return []
    empty = getattr(df, "empty", None)
    if empty is True:
        return []
    to_dict = getattr(df, "to_dict", None)
    if callable(to_dict):
        return list(to_dict(orient="records"))
    if isinstance(df, list):
        return list(df)
    return list(df)


def _row_to_listing(row: dict[str, Any], *, default_source: str) -> RawListing | None:
    """Map one JobSpy row to a :class:`RawListing`, or ``None`` if unusable.

    A row with no title carries no useful signal for ranking, so it is dropped.
    ``partial`` is set when the row lacks a description body (a search-results
    row); the ranker scores those on title/keywords and the tailor handoff
    back-fills the JD later (Req 7.2).
    """
    title = _str(row.get("title"))
    if not title:
        return None

    description = _str_opt(row.get("description"))
    source = _str(row.get("site")) or default_source
    url = _str(row.get("job_url")) or _str(row.get("job_url_direct"))

    return RawListing(
        source=source,
        title=title,
        company=_str(row.get("company")),
        location=_str(row.get("location")),
        url=url,
        is_remote=_bool_opt(row.get("is_remote")),
        description=description,
        posted_at=_datetime_opt(row.get("date_posted")),
        salary=_salary(row),
        partial=description is None,
        extra={
            k: row[k]
            for k in ("job_type", "site", "company_url")
            if k in row and not _is_missing(row.get(k))
        },
    )


class JobSpyConnector:
    """Fixed-board connector backed by ``python-jobspy`` (Req 3).

    Args:
        sites: Board slugs to scrape (e.g. ``["indeed", "naukri"]``). Defaults
            to the parsed :attr:`Settings.job_discovery_jobspy_sites`.
        results_wanted: Fallback per-board result cap when the request's
            :class:`SearchFilters` does not set one.
        scrape_fn: Injectable ``scrape_jobs``-shaped callable (tests pass a fake
            returning a recorded DataFrame); production lazily imports the real
            one on first use.
    """

    fetch_mode = "http"

    def __init__(
        self,
        *,
        sites: list[str] | None = None,
        results_wanted: int = 15,
        scrape_fn: ScrapeFn | None = None,
    ) -> None:
        self.sites = sites if sites is not None else settings.job_discovery_jobspy_sites
        self.results_wanted = results_wanted
        self._scrape_fn = scrape_fn

    @property
    def name(self) -> str:
        return "jobspy"

    def _resolve_scrape_fn(self) -> ScrapeFn:
        """Return the injected fn, else lazily import the real ``scrape_jobs``."""
        if self._scrape_fn is not None:
            return self._scrape_fn
        return _load_scrape_jobs()

    def _scrape_params(
        self, site: str, query: SearchQuery, filters: SearchFilters
    ) -> dict[str, Any]:
        """Build the keyword args for a single-board ``scrape_jobs`` call.

        Forwards the search intent and every understood filter (Req 3.4);
        ``None`` values are dropped so JobSpy applies its own defaults.
        """
        search_term = query.search_string or " ".join(query.titles)
        params: dict[str, Any] = {
            "site_name": [site],
            "search_term": search_term or None,
            "location": filters.location or query.location,
            "is_remote": filters.is_remote,
            "hours_old": filters.hours_old,
            "results_wanted": filters.results_wanted or self.results_wanted,
            "country_indeed": filters.country_indeed or query.country_indeed,
            "job_type": filters.job_type,
            "distance": filters.distance,
        }
        return {k: v for k, v in params.items() if v is not None}

    def _scrape_site(
        self, scrape_fn: ScrapeFn, site: str, query: SearchQuery, filters: SearchFilters
    ) -> list[RawListing]:
        """Blocking single-board scrape + row mapping (runs in a worker thread)."""
        df = scrape_fn(**self._scrape_params(site, query, filters))
        listings: list[RawListing] = []
        for row in _records(df):
            listing = _row_to_listing(row, default_source=site)
            if listing is not None:
                listings.append(listing)
        return listings

    async def search(
        self,
        query: SearchQuery,
        filters: SearchFilters,
        failures: list[SourceFailure],
    ) -> list[RawListing]:
        """Scrape every configured board, isolating per-board failures (Req 3.2).

        The blocking ``scrape_jobs`` call for each board runs in a threadpool
        (Req 3.3). A board that raises (blocked, timeout, unavailable) is
        recorded as one :class:`SourceFailure` and skipped; the remaining boards
        still contribute rows. A missing ``jobspy`` install degrades the same
        way rather than crashing the fan-out.
        """
        try:
            scrape_fn = self._resolve_scrape_fn()
        except Exception as exc:  # noqa: BLE001 - optional dependency is recoverable
            failures.append(
                SourceFailure(
                    source=self.name,
                    reason=f"python-jobspy not installed: {exc}",
                    kind="unavailable",
                )
            )
            return []

        listings: list[RawListing] = []
        for site in self.sites:
            try:
                rows = await asyncio.to_thread(
                    self._scrape_site, scrape_fn, site, query, filters
                )
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - per-source contract (Req 3.2)
                failures.append(
                    SourceFailure(
                        source=site,
                        reason=str(exc) or type(exc).__name__,
                        kind=classify_failure(exc),
                    )
                )
                continue
            listings.extend(rows)
        return listings


__all__ = ["JobSpyConnector"]
