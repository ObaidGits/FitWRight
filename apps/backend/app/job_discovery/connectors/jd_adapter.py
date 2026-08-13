"""Known-ATS adapter connector (design §3.2, §4.2 — *optional* for MVP).

Some job postings live on well-known Applicant Tracking Systems (Greenhouse,
Lever, Workday, Ashby, iCIMS, SmartRecruiters, …). The base FitWright tree
already ships per-host parsers behind ``app/jd/adapters/registry.py``; rather
than re-implement extraction, this connector *reuses* that registry: for each
target career/ATS URL it resolves a matching adapter by host, fetches the page
through :mod:`app.job_discovery.fetch`, hands the HTML to the adapter, and maps
the adapter's parsed rows onto :class:`RawListing`.

This connector is **time-boxed / optional** (Req 4.2). The ``app/jd/adapters``
registry is not a hard dependency of the discovery feature: it is imported
lazily, exactly like the optional scraper/browser stacks elsewhere in this
package. When the registry is absent (e.g. a minimal build, or an MVP tree that
has not yet vendored the adapters), the connector records a single
:class:`~app.job_discovery.models.SourceFailure` and returns ``[]`` instead of
raising — honouring the cardinal connector rule that one source can never abort
the fan-out.

Contract, shared with every connector (see ``connectors/base.py``):

* Never raise on a single-source failure — append one :class:`SourceFailure`
  to the shared ``failures`` list and return whatever rows were gathered.
* An unknown host (no adapter registered for it) is **not** a failure: the
  target is simply skipped.

Expected adapter shape (the minimal surface this connector uses)::

    class HostAdapter(Protocol):
        name: str
        def parse(self, html: str, url: str) -> list[dict | RawListing]: ...

and a registry resolver that maps a hostname to an adapter (or ``None``). The
default resolver probes ``app/jd/adapters/registry`` for the common entry-point
names; tests inject an explicit ``resolve`` callable instead.

Requirements: 4.2.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from app.job_discovery.connectors.base import RawListing
from app.job_discovery.fetch import FetchResult
from app.job_discovery.fetch import fetch as default_fetch
from app.job_discovery.models import (
    FetchMode,
    SearchFilters,
    SearchQuery,
    SourceFailure,
)

__all__ = ["HostAdapter", "JdAdapterConnector", "map_adapter_row"]


@runtime_checkable
class HostAdapter(Protocol):
    """Minimal ATS-adapter surface this connector depends on.

    The real ``app/jd/adapters`` registry exposes richer objects; this connector
    only needs a stable ``name`` and a ``parse(html, url)`` that returns row
    mappings (or ready-made :class:`RawListing` instances).
    """

    name: str

    def parse(self, html: str, url: str) -> list:  # pragma: no cover - protocol
        ...


# A resolver maps a hostname to a matching adapter, or ``None`` if unknown.
AdapterResolver = Callable[[str], "HostAdapter | None"]
# A fetch fn takes a URL + mode and returns a FetchResult (injectable for tests).
FetchFn = Callable[..., Awaitable[FetchResult]]


def _load_registry_resolver() -> AdapterResolver:
    """Return a resolver backed by ``app/jd/adapters/registry`` (lazy import).

    Probes the registry module for the common entry-point shapes so this
    connector stays decoupled from the registry's exact public API:

    * a callable ``resolve`` / ``get_adapter`` / ``for_host`` / ``get``, or
    * a mapping ``ADAPTERS`` / ``REGISTRY`` keyed by host (exact or suffix).

    Raises :class:`ImportError` when the optional registry is not installed;
    the caller converts that into a recoverable :class:`SourceFailure`.
    """
    from app.jd.adapters import registry as _registry  # type: ignore

    for fn_name in ("resolve", "get_adapter", "for_host", "get"):
        fn = getattr(_registry, fn_name, None)
        if callable(fn):
            return fn  # type: ignore[return-value]

    for map_name in ("ADAPTERS", "REGISTRY", "adapters", "registry"):
        mapping = getattr(_registry, map_name, None)
        if isinstance(mapping, dict):

            def _resolve(host: str, _mapping=mapping) -> HostAdapter | None:
                if host in _mapping:
                    return _mapping[host]
                # Suffix match so "boards.greenhouse.io" hits a "greenhouse.io" key.
                for key, adapter in _mapping.items():
                    if isinstance(key, str) and host.endswith(key):
                        return adapter
                return None

            return _resolve

    raise ImportError(
        "app.jd.adapters.registry exposes no known resolver "
        "(resolve/get_adapter/for_host/get or an ADAPTERS/REGISTRY mapping)"
    )


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def map_adapter_row(row: object, *, source: str, page_url: str) -> RawListing | None:
    """Map one adapter-produced row onto a :class:`RawListing`.

    Accepts either a ready-made :class:`RawListing` (passed through, only
    back-filling ``source``/``url``) or a plain mapping with the usual keys.
    Returns ``None`` for rows without a title (nothing worth ranking).

    ``partial`` is inferred: a row without a description is a search/listing-page
    row and is flagged partial so the ranker scores it on title/keywords only
    and the tailor handoff back-fills the full JD later (Req 7.2, 8).
    """
    if isinstance(row, RawListing):
        if not row.title:
            return None
        if not row.source:
            row.source = source
        if not row.url:
            row.url = page_url
        return row

    if not isinstance(row, dict):
        return None

    title = str(row.get("title") or "").strip()
    if not title:
        return None

    known = {
        "title",
        "company",
        "location",
        "url",
        "is_remote",
        "description",
        "posted_at",
        "salary",
        "partial",
    }
    description = row.get("description")
    description = str(description) if description not in (None, "") else None
    extra = {k: v for k, v in row.items() if k not in known}

    return RawListing(
        source=source,
        title=title,
        company=str(row.get("company") or ""),
        location=str(row.get("location") or ""),
        url=str(row.get("url") or page_url),
        is_remote=row.get("is_remote") if isinstance(row.get("is_remote"), bool) else None,
        description=description,
        posted_at=_coerce_datetime(row.get("posted_at")),
        salary=str(row["salary"]) if row.get("salary") not in (None, "") else None,
        partial=bool(row["partial"]) if "partial" in row else description is None,
        extra=extra,
    )


class JdAdapterConnector:
    """Reuse ``app/jd/adapters`` parsers to scrape known-ATS career URLs.

    Args:
        targets: Career/ATS URLs to attempt. Each is matched to an adapter by
            host; unknown hosts are silently skipped.
        fetch_mode: Fetch lane for the target pages (``"http"`` by default;
            most ATS listing pages are server-rendered).
        resolve: Optional adapter resolver. Defaults to a resolver backed by the
            (optional) ``app/jd/adapters/registry`` module, loaded lazily.
        fetch_fn: Optional fetch function (injected in tests); defaults to
            :func:`app.job_discovery.fetch.fetch`.
    """

    name = "jd_adapter"
    fetch_mode: FetchMode

    def __init__(
        self,
        targets: list[str] | None = None,
        *,
        fetch_mode: FetchMode = "http",
        resolve: AdapterResolver | None = None,
        fetch_fn: FetchFn | None = None,
    ) -> None:
        self.targets = list(targets or [])
        self.fetch_mode = fetch_mode
        self._resolve = resolve
        self._fetch = fetch_fn or default_fetch

    def _resolver(self) -> AdapterResolver:
        if self._resolve is not None:
            return self._resolve
        # Lazy-load once and cache; ImportError bubbles to search() which
        # converts it into a single recoverable SourceFailure.
        self._resolve = _load_registry_resolver()
        return self._resolve

    async def search(
        self,
        query: SearchQuery,
        filters: SearchFilters,
        failures: list[SourceFailure],
    ) -> list[RawListing]:
        if not self.targets:
            return []

        try:
            resolve = self._resolver()
        except Exception as exc:  # noqa: BLE001 - optional registry absent is recoverable
            failures.append(
                SourceFailure(
                    source=self.name,
                    reason=f"jd adapters registry unavailable: {exc}",
                    kind="unavailable",
                )
            )
            return []

        cap = filters.results_wanted if filters and filters.results_wanted else None
        rows: list[RawListing] = []

        for url in self.targets:
            host = (urlsplit(url).hostname or "").lower()
            adapter = None
            try:
                adapter = resolve(host)
            except Exception as exc:  # noqa: BLE001 - a bad resolver call is a per-target failure
                failures.append(
                    SourceFailure(source=host or self.name, reason=str(exc), kind="error")
                )
                continue

            if adapter is None:
                # No adapter for this host — not a failure, just unsupported.
                continue

            source = getattr(adapter, "name", None) or host or self.name
            try:
                result = await self._fetch(url, fetch_mode=self.fetch_mode)
                parsed = adapter.parse(result.text, result.final_url or url)
            except Exception as exc:  # noqa: BLE001 - per-source contract (Req 3.2)
                from app.job_discovery.connectors.base import classify_failure

                failures.append(
                    SourceFailure(source=source, reason=str(exc), kind=classify_failure(exc))
                )
                continue

            for raw in parsed or []:
                mapped = map_adapter_row(raw, source=source, page_url=result.final_url or url)
                if mapped is not None:
                    rows.append(mapped)
                    if cap is not None and len(rows) >= cap:
                        return rows

        return rows
