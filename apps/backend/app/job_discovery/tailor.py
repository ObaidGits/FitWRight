"""Discovery → tailor handoff (design §8, Req 8).

The end of the discovery journey: the user picks a recommended job and taps
"Tailor my resume for this". That hands the job off to the *existing* tailor
flow, which is keyed on a persisted **job** record plus the resume to tailor.
This module is the bridge between a discovery :class:`JobListing` and that flow.

It does exactly three things (Req 8.1–8.3):

1. **Back-fill partial listings** — a listing scraped from a search-results page
   carries no full JD body (``description`` empty). The tailor flow needs the
   real requirements text, so for a *partial* listing we first fetch the full JD
   through the **existing ``app/jd`` URL path** — the same fetch+extract choke
   point the manual "analyze a job from its URL" flow uses (Req 8.2). Full
   listings already carry their description and skip the fetch entirely.
2. **Reuse ``db.create_job``** — the handoff persists the job through the exact
   same accessor the manual flow uses, so a discovery-sourced job is
   indistinguishable downstream from a hand-entered one (Req 8.3).
3. **Return the tailor keys** — ``{job_id, resume_id}``: everything the tailor
   flow needs to start (Req 8.1).

Every external collaborator (``create_job``, the JD fetcher, the SSRF guard) is
injectable so the handoff is unit-testable with fakes and never touches a live
DB, browser, or the network. The JD fetcher and SSRF guard default to the real
``app/jd`` machinery, imported lazily so the base import graph stays free of the
optional fetch stack — mirroring the connectors and :mod:`app.job_discovery.fetch`.

Design reference: ``.kiro/specs/job-discovery/design.md`` §8 (tailor handoff).
Requirements: 8.1, 8.2, 8.3.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.job_discovery.models import JobListing

logger = logging.getLogger(__name__)

__all__ = [
    "JdFetcher",
    "JobCreator",
    "JobDraft",
    "PartialJDFetchError",
    "TailorError",
    "TailorHandoff",
    "TailorService",
    "UrlValidator",
    "is_partial_listing",
]


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class TailorError(Exception):
    """Base class for tailor-handoff failures."""


class PartialJDFetchError(TailorError):
    """Fetching the full JD for a partial listing failed (Req 8.2).

    The handoff cannot produce a useful job record without the JD body, so this
    is surfaced to the caller (the router returns a 4xx/5xx) rather than silently
    persisting an empty job.
    """


# --------------------------------------------------------------------------- #
# Shapes
# --------------------------------------------------------------------------- #
@dataclass
class JobDraft:
    """The job fields handed to ``create_job`` (design §8).

    Deliberately mirrors the shape the manual "analyze from URL" flow persists,
    so a discovery-sourced job looks identical downstream (Req 8.3).
    """

    title: str
    company: str
    description: str
    url: str
    location: str = ""
    source: str = ""
    is_remote: bool | None = None
    salary: str | None = None


@dataclass
class TailorHandoff:
    """The tailor flow's entry keys (Req 8.1)."""

    job_id: str
    resume_id: str


# ``(user_id, draft) -> job_id``. The persistence boundary; defaults to
# ``db.create_job`` so discovery reuses the manual flow's accessor (Req 8.3).
JobCreator = Callable[[str, JobDraft], Awaitable[str]]

# ``url -> full JD description text``. The existing ``app/jd`` URL path (Req 8.2).
JdFetcher = Callable[[str], Awaitable[str]]

# ``url -> None`` (raises on rejection). Fail-closed SSRF guard for the fetch.
UrlValidator = Callable[[str], Any]


# --------------------------------------------------------------------------- #
# Partial detection
# --------------------------------------------------------------------------- #
def is_partial_listing(listing: JobListing) -> bool:
    """True when ``listing`` was scraped without a full JD body (Req 7.2, 8.2).

    The canonical :class:`JobListing` has no ``partial`` flag — partial-ness is
    re-derived from description presence (matching ``normalize``/``ranker``), so
    a missing or whitespace-only description means the JD must be back-filled.
    """
    return not (listing.description and listing.description.strip())


# --------------------------------------------------------------------------- #
# Default collaborators (lazy — keep the base import graph clean)
# --------------------------------------------------------------------------- #
# Entry-point names probed on the ``app/jd`` URL path. The full FitWright tree
# exposes one of these as its "extract a JD from a URL" function; we stay
# decoupled from the exact public name, exactly like the ATS-adapter resolver.
_JD_FETCH_MODULES = ("app.jd", "app.jd.extract", "app.jd.from_url", "app.jd.fetcher")
_JD_FETCH_ENTRYPOINTS = (
    "fetch_job_description",
    "extract_job_description",
    "jd_from_url",
    "from_url",
    "fetch_jd",
)


def _coerce_jd_text(result: Any) -> str:
    """Coerce whatever the jd URL path returns into a JD description string."""
    if isinstance(result, str):
        return result
    for attr in ("description", "text", "jd_text", "body"):
        value = getattr(result, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(result, dict):
        for key in ("description", "text", "jd_text", "body"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return str(result or "")


def _resolve_jd_fetcher() -> Callable[[str], Any]:
    """Locate the existing ``app/jd`` URL→JD entry point (lazy import).

    Raises :class:`PartialJDFetchError` when no known entry point is present;
    the caller turns that into a recoverable handoff error rather than a crash.
    """
    for module_name in _JD_FETCH_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - optional module absent in minimal builds
            logger.debug("jd fetch probe: %s not importable (%s)", module_name, exc)
            continue
        for fn_name in _JD_FETCH_ENTRYPOINTS:
            fn = getattr(module, fn_name, None)
            if callable(fn):
                return fn
    raise PartialJDFetchError(
        "no JD URL entry point found on the app/jd path "
        f"(looked for {_JD_FETCH_ENTRYPOINTS} in {_JD_FETCH_MODULES}); "
        "inject fetch_jd=... to wire the handoff explicitly"
    )


async def _default_jd_fetch(url: str) -> str:
    """Fetch the full JD for ``url`` via the existing ``app/jd`` URL path."""
    fn = _resolve_jd_fetcher()
    result = fn(url)
    if inspect.isawaitable(result):
        result = await result
    return _coerce_jd_text(result)


def _default_url_validator(url: str) -> None:
    """Fail-closed SSRF guard, delegating to ``app/jd/ssrf`` (lazy import).

    When the ssrf module is absent (minimal build) validation is skipped — the
    ``app/jd`` fetch path is itself the choke point in that case.
    """
    try:
        from app.jd.ssrf import validate_url  # type: ignore
    except Exception:  # noqa: BLE001 - ssrf optional; jd path guards otherwise
        return
    validate_url(url)


def _bind_db_creator(db: Any) -> JobCreator:
    """Bind :meth:`create_job` off the injected ``db`` as the default creator.

    Reuses the manual flow's accessor so discovery jobs share its shape (Req 8.3).
    Resolution is deferred to call time so importing this module never requires
    a ``db`` with ``create_job``.
    """

    async def _create(user_id: str, draft: JobDraft) -> str:
        create = getattr(db, "create_job", None)
        if not callable(create):
            raise TailorError(
                "database has no create_job accessor; inject create_job=... "
                "into TailorService"
            )
        result = create(
            user_id=user_id,
            title=draft.title,
            company=draft.company,
            location=draft.location,
            description=draft.description,
            url=draft.url,
            source=draft.source,
            is_remote=draft.is_remote,
            salary=draft.salary,
        )
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    return _create


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #
class TailorService:
    """Runs one discovery→tailor handoff (design §8).

    Args:
        db: data-access facade; used only to bind the default ``create_job``.
        create_job: ``(user_id, draft) -> job_id``. Defaults to ``db.create_job``
            (Req 8.3). Injected as a fake in tests.
        fetch_jd: ``url -> full JD text`` for partial listings; defaults to the
            existing ``app/jd`` URL path (Req 8.2).
        validate_url: fail-closed SSRF guard applied before a JD fetch; defaults
            to ``app/jd/ssrf.validate_url``.
    """

    def __init__(
        self,
        db: Any = None,
        *,
        create_job: JobCreator | None = None,
        fetch_jd: JdFetcher | None = None,
        validate_url: UrlValidator | None = None,
    ) -> None:
        self._db = db
        self._create_job = create_job
        self._fetch_jd = fetch_jd or _default_jd_fetch
        self._validate_url = validate_url or _default_url_validator

    # ------------------------------------------------------------------ #
    async def tailor(
        self, *, user_id: str, resume_id: str, listing: JobListing
    ) -> TailorHandoff:
        """Create a job from ``listing`` and return the tailor keys (Req 8).

        For a *partial* listing the full JD is fetched via the existing
        ``app/jd`` URL path first (Req 8.2), then the job is persisted through
        ``create_job`` (Req 8.3) and ``{job_id, resume_id}`` is returned (Req 8.1).

        Raises:
            TailorError: ``resume_id`` missing, a partial listing has no URL to
                back-fill from, or ``create_job`` is unavailable.
            PartialJDFetchError: the full-JD fetch (or its SSRF guard) failed.
        """
        if not resume_id:
            raise TailorError("resume_id is required for the tailor handoff")

        description = listing.description or ""

        # Req 8.2 — back-fill a partial listing's JD via the existing jd/ path.
        if is_partial_listing(listing):
            description = await self._fetch_full_jd(listing)

        draft = JobDraft(
            title=listing.title,
            company=listing.company,
            description=description,
            url=listing.url,
            location=listing.location,
            source=listing.source,
            is_remote=listing.is_remote,
            salary=listing.salary,
        )

        # Req 8.3 — persist via the same accessor the manual flow uses.
        creator = self._create_job or _bind_db_creator(self._db)
        job_id = await creator(user_id, draft)
        if not job_id:
            raise TailorError("create_job returned no job id")

        logger.debug(
            "tailor handoff: user=%s resume=%s -> job=%s (partial=%s)",
            user_id,
            resume_id,
            job_id,
            is_partial_listing(listing),
        )

        # Req 8.1 — the keys the tailor flow starts from.
        return TailorHandoff(job_id=str(job_id), resume_id=resume_id)

    # ------------------------------------------------------------------ #
    async def _fetch_full_jd(self, listing: JobListing) -> str:
        """Fetch and return the full JD body for a partial ``listing`` (Req 8.2)."""
        url = (listing.url or "").strip()
        if not url:
            raise TailorError(
                "partial listing has no URL to fetch the full JD from"
            )

        # SSRF choke point: the URL derives from scraped/user-controlled data,
        # so validate fail-closed before any outbound fetch.
        try:
            self._validate_url(url)
        except PartialJDFetchError:
            raise
        except Exception as exc:
            raise PartialJDFetchError(
                f"refusing to fetch full JD from unsafe url {url!r}: {exc}"
            ) from exc

        try:
            text = await self._fetch_jd(url)
        except PartialJDFetchError:
            raise
        except Exception as exc:
            raise PartialJDFetchError(
                f"failed to fetch full JD from {url!r}: {exc}"
            ) from exc

        text = (text or "").strip()
        if not text:
            raise PartialJDFetchError(
                f"full JD fetch for {url!r} returned no description"
            )
        return text
