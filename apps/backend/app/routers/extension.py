"""HTTP surface for the FitWright Companion browser extension.

The extension runs inside the user's own browser, which gives it three things
the server-side scrapers cannot have: a residential IP, a real browser
fingerprint, and the user's existing logins. That makes it the reliable path
for the job boards that block datacenter traffic (Cloudflare / Akamai / login
walls), and the only sane place to autofill an application form.

This router is the narrow, audited boundary between that browser code and
FitWright. It deliberately owns no scraping logic of its own: the extension
sends already-extracted data, and everything here reuses the existing
discovery/resume/LLM machinery.

Endpoints (all under ``/extension``):

| Method & path                   | Purpose                              | Auth           |
|---------------------------------|--------------------------------------|----------------|
| ``GET  /extension/ping``        | Handshake: is the user signed in?    | effective user |
| ``GET  /extension/profile``     | Autofill profile from the resume     | effective user |
| ``POST /extension/capture``     | Save one captured job to the feed    | verified user  |
| ``POST /extension/scrape``      | Bulk-ingest browser-scraped jobs     | verified user  |
| ``POST /extension/match``       | Score one JD against a resume        | verified user  |
| ``POST /extension/draft``       | LLM-draft an application answer      | verified user  |
| ``POST /extension/applied``     | Mark a job applied (by fingerprint)  | verified user  |

Gated by the same ``JOB_DISCOVERY`` kill-switch as the discovery router: while
the feature is off every route returns 404, so a disabled deployment is
indistinguishable from one where this surface does not exist.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.auth import get_effective_user_id, require_verified_user_id
from app.config import Settings, settings
from app.database import Database
from app.llm_ratelimit import llm_rate_limit_dep

logger = logging.getLogger(__name__)

# Bumped when the wire contract changes so an old extension build can warn the
# user instead of failing in confusing ways. The extension compares this to its
# own manifest version's supported range.
EXTENSION_API_VERSION = 1

# The extension build shipped with this server. Bump alongside the extension's
# manifest version - it is what lets a client that never auto-updates discover it
# is behind, which for an unpacked extension is otherwise unknowable.
EXTENSION_LATEST_VERSION = "0.2.0"


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
def get_settings_dep() -> Settings:
    """Return the active settings snapshot (overridable in tests)."""
    return settings


def get_db() -> Database:
    """Return the process-wide database (overridable in tests)."""
    from app.database import db

    return db


def require_extension_enabled(
    config: Settings = Depends(get_settings_dep),
) -> None:
    """Kill-switch gate for the whole router.

    Shares ``JOB_DISCOVERY`` with the discovery surface because the extension is
    a client of that feature - enabling one without the other has no meaning.
    """
    if not config.JOB_DISCOVERY:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="not_found"
        )


# Router-level dependency: the kill-switch runs before every route below, so a
# disabled deployment 404s the whole surface rather than leaking its shape.
router = APIRouter(
    prefix="/extension",
    tags=["Extension"],
    dependencies=[Depends(require_extension_enabled)],
)


# --------------------------------------------------------------------------- #
# Wire models
# --------------------------------------------------------------------------- #
class CapturedJob(BaseModel):
    """One job as the extension extracted it from a live page.

    Every field is length-bounded to match its database column. Only ``title`` was
    before, which meant an over-long company or URL was accepted here and then
    handled differently by each database: SQLite ignores declared column widths and
    stores whatever it is given, while Postgres raises and the request becomes a
    500. A validation rule that only bites in production is the worst kind, so the
    bounds live here where both backends see the same answer.

    Values are truncated rather than rejected. A 300-character company name is a
    page that rendered oddly, not an attack, and losing the whole job over it would
    be a worse outcome than storing a clipped name.
    """

    # A title is the one field that must be present: an empty title means DOM
    # extraction failed, and storing a blank row is worse than telling the
    # extension to fall back to its generic adapter.
    title: str = Field(min_length=1, max_length=500)
    company: str = Field(default="", max_length=255)
    location: str = Field(default="", max_length=255)
    url: str = Field(max_length=2048)
    source: str = Field(default="extension", max_length=50)
    # The only genuinely large field. Bounded well above any real posting (a long
    # job description is ~10k characters) but far below "unbounded write path",
    # which with 200 jobs per batch is what it was.
    description: str | None = Field(default=None, max_length=60_000)
    salary: str | None = Field(default=None, max_length=100)
    posted_at: str | None = Field(default=None, max_length=64)
    is_remote: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _truncate_to_column_widths(cls, data: Any) -> Any:
        """Clip over-long text to each field's own limit instead of failing.

        A 300-character company name is a page that rendered oddly, not an attack,
        and losing the whole job over it is a worse outcome than storing a clipped
        name. Limits are read from the field definitions, so adding a field cannot
        forget to truncate it.

        ``url`` and ``title`` are excluded: a truncated URL is a broken link and a
        truncated title is a mislabelled job, both worse than a rejected capture.
        """
        if not isinstance(data, dict):
            return data

        never_truncate = {"url", "title"}
        for name, field in cls.model_fields.items():
            if name in never_truncate:
                continue
            value = data.get(name)
            if not isinstance(value, str):
                continue
            limit = next(
                (m.max_length for m in field.metadata if getattr(m, "max_length", None)),
                None,
            )
            if limit and len(value) > limit:
                data[name] = value[:limit]
        return data

    @field_validator("title", "company", "location", "source", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        """Collapse scraped whitespace before validation.

        Page text arrives full of newlines and non-breaking spaces, and a title
        of only whitespace has to fail `min_length` rather than pass it.
        """
        if isinstance(value, str):
            return " ".join(value.replace("\u00a0", " ").split())
        return value


class CaptureResponse(BaseModel):
    saved: int
    duplicate: bool
    fingerprint: str


# One batch is one page of search results. Enforced rather than truncated: a
# silent `[:200]` would report every job as received while dropping the rest,
# leaving the extension no way to notice it lost data.
MAX_SCRAPE_BATCH = 200


class ScrapeBatch(BaseModel):
    """A batch of jobs the extension scraped from a board in a background tab."""

    source: str
    jobs: list[CapturedJob] = Field(default_factory=list, max_length=MAX_SCRAPE_BATCH)


class ScrapeResponse(BaseModel):
    received: int
    saved: int
    source: str


class MatchRequest(BaseModel):
    description: str
    title: str = ""
    resume_id: str | None = None


class MatchResponse(BaseModel):
    match_score: float
    matched: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    resume_id: str | None = None
    degraded: bool = False


class DraftRequest(BaseModel):
    question: str
    description: str = ""
    company: str = ""
    title: str = ""
    resume_id: str | None = None
    max_words: int = 150


class DraftResponse(BaseModel):
    answer: str
    degraded: bool = False


class AppliedRequest(BaseModel):
    """Mark a job applied. Identified by fingerprint, else by URL."""

    fingerprint: str | None = None
    url: str | None = None


class AppliedResponse(BaseModel):
    updated: bool
    fingerprint: str | None = None


class PingResponse(BaseModel):
    ok: bool
    api_version: int
    user_id: str
    has_resume: bool
    resume_count: int
    # The extension build this server was released alongside. Loaded unpacked,
    # extensions do not auto-update, so without this the user has no way to learn
    # a newer build exists - every fix would reach nobody until told in person.
    latest_extension_version: str = EXTENSION_LATEST_VERSION
    # None when the client did not say which build it is.
    client_current: bool | None = None


class AutofillProfile(BaseModel):
    """Everything the extension needs to fill a standard application form.

    Sourced from the user's **Profile** first and the resume only as a fallback
    (see :func:`get_autofill_profile`). Every field is additive against the
    previously shipped shape, so an extension build from before this change keeps
    working - it simply ignores what it does not know about.
    """

    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""
    website: str = ""
    current_title: str = ""
    current_company: str = ""
    # Profile first, then an estimate computed from the resume's experience dates.
    # Deliberately NOT in the eligibility block below: unlike a visa status, this
    # is derivable from dates rather than guessed, and a screening filter asking
    # "minimum 5 years" is better served by a good estimate than by a blank. The
    # user's Profile value always overrides it.
    years_experience: float | None = None

    # --- Structured address ------------------------------------------------- #
    # ATS forms ask for these as separate required inputs, and a "Pune, India"
    # one-liner cannot be split back into them reliably.
    address_line1: str = ""
    address_line2: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = ""

    # --- Eligibility / knockout answers ------------------------------------- #
    # These decide whether an application is auto-rejected, so they come ONLY
    # from curated Profile facts - never inferred from resume prose. Blank means
    # "not answered", which is safer than a guess.
    work_authorization: str = ""
    visa_status: str = ""
    notice_period: str = ""
    salary_expectation: str = ""
    willing_to_relocate: bool | None = None
    availability: str = ""
    remote_preference: str = ""

    # --- Highest education -------------------------------------------------- #
    highest_degree: str = ""
    highest_institution: str = ""
    education_years: str = ""

    resume_id: str | None = None
    resume_filename: str = ""
    # Relative API path the extension fetches to attach the PDF to a form.
    resume_pdf_path: str | None = None
    # True when this resume was tailored for the company+role the extension named,
    # rather than the master resume. Surfaced to the user, because "we attached
    # your tailored resume" and "we attached your generic one" are different
    # promises and they deserve to know which one happened.
    resume_tailored_for_role: bool = False
    # Legacy escape hatch: answers the older extension stored locally. Kept for
    # backwards compatibility, but Profile values above take precedence - the
    # server is the source of truth now.
    preferences: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _fingerprint(job: CapturedJob) -> str:
    """Fingerprint a captured job with the same function the pipeline uses."""
    from app.job_discovery.normalize import fingerprint

    return fingerprint(job.title, job.company, job.location, job.url)


def _to_feed_row(job: CapturedJob, *, match_score: float = 0.0) -> dict[str, Any]:
    """Map a captured job onto the ``discovery_results`` row shape."""
    return {
        "fingerprint": _fingerprint(job),
        "source": job.source or "extension",
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "url": job.url,
        "is_remote": job.is_remote,
        "description": job.description,
        "salary": job.salary,
        "posted_at": job.posted_at,
        "match_score": match_score,
        "matched": [],
        "missing": [],
        # A capture without a JD body is a partial row: the tailor handoff
        # back-fills the full description on demand.
        "partial": not job.description,
    }


async def _resolve_resume(
    db: Database, user_id: str, resume_id: str | None
) -> dict[str, Any] | None:
    """Return the requested resume, else the user's master resume."""
    if resume_id:
        return await db.get_resume(user_id, resume_id)
    return await db.get_master_resume(user_id)


def _years_of_experience(processed: dict[str, Any]) -> float | None:
    """Best-effort total years across experience entries.

    Resume date fields are free text, so this parses leading years out of the
    common ``YYYY``/``MM/YYYY``/``Mon YYYY`` shapes and gives up quietly rather
    than guessing. ``None`` means "unknown" - the extension then leaves the
    field for the user instead of filling a wrong number.
    """
    import re
    from datetime import date

    entries = processed.get("experience")
    if not isinstance(entries, list) or not entries:
        return None

    def year_of(value: Any) -> int | None:
        if not isinstance(value, str):
            return None
        match = re.search(r"(19|20)\d{2}", value)
        return int(match.group(0)) if match else None

    this_year = date.today().year
    total = 0.0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = year_of(entry.get("start_date"))
        if start is None:
            continue
        end_raw = str(entry.get("end_date") or "")
        end = (
            this_year
            if not end_raw or re.search(r"present|current", end_raw, re.I)
            else year_of(end_raw)
        )
        if end is None or end < start:
            continue
        total += end - start
    return round(total, 1) if total else None


# --------------------------------------------------------------------------- #
# Handshake
# --------------------------------------------------------------------------- #
@router.get("/ping", response_model=PingResponse, summary="Extension handshake")
async def ping(
    client_version: str | None = None,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> PingResponse:
    """Confirm the extension is talking to a signed-in FitWright.

    The extension calls this on startup and on every popup open: a 401 tells it
    to show "sign in to FitWright" instead of failing later mid-autofill.

    ``client_version`` lets the extension learn it is behind. An unpacked
    extension never auto-updates, so a user can run a build from weeks ago
    indefinitely and report bugs that were already fixed. Comparison is exact
    equality rather than semver ordering: the only question worth answering is
    "is this the build this server expects", and a wrong guess about ordering
    would nag someone running a newer build.
    """
    resumes = await db.list_resumes(user_id)
    return PingResponse(
        ok=True,
        api_version=EXTENSION_API_VERSION,
        user_id=user_id,
        has_resume=bool(resumes),
        resume_count=len(resumes),
        latest_extension_version=EXTENSION_LATEST_VERSION,
        client_current=(client_version == EXTENSION_LATEST_VERSION) if client_version else None,
    )


# --------------------------------------------------------------------------- #
# Autofill profile
# --------------------------------------------------------------------------- #
def _pick(*candidates: Any) -> str:
    """First non-empty candidate as a trimmed string.

    The precedence rule of this whole endpoint in one helper: pass the curated
    Profile value first and the resume-derived value second, and the user's own
    answer always wins.
    """
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _split_name(name: str) -> tuple[str, str]:
    """`"Ada Lovelace"` -> `("Ada", "Lovelace")`; a single token has no surname."""
    parts = name.split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def _profile_document(row: dict[str, Any] | None) -> dict[str, Any]:
    """The profile's JSON document as a plain dict, however it was stored.

    SQLite hands back the JSON column as a string on some driver paths and as a
    dict on others, so both are accepted rather than assumed.
    """
    if not row:
        return {}
    data = row.get("data")
    if isinstance(data, str):
        import json

        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


class InstallInfo(BaseModel):
    """Where the unpacked extension lives, so the setup page can say it."""

    # Absolute path to load in chrome://extensions, or null when we must not say.
    dist_path: str | None = None
    # False when the folder is missing: the user has not run the build yet.
    built: bool = False
    # Only true for a local single-user install; a hosted server never answers.
    local: bool = False


@router.get("/install-info", response_model=InstallInfo, summary="Where to load the extension from")
async def get_install_info(
    config: Settings = Depends(get_settings_dep),
) -> InstallInfo:
    """Resolve the extension's ``dist`` folder for the setup page.

    Chrome offers no way to install an unpacked extension programmatically, so
    the one thing software can still do for a non-technical user is remove the
    guesswork: name the exact folder, and say plainly whether it has been built.

    The gate is a policy question asked of the platform seam, not a mode branch
    here - "may this process describe its own disk to its reader". On a hosted
    deployment the answer is no: the path would describe the server, which is
    useless to the person reading it and more than a stranger should learn about
    the host.
    """
    from app.platform import allows_local_filesystem_hints

    if not allows_local_filesystem_hints(config):
        return InstallInfo(local=False)

    from pathlib import Path

    # app/routers/extension.py -> app/routers -> app -> backend -> apps -> repo
    dist = Path(__file__).resolve().parents[3] / "extension" / "dist"
    return InstallInfo(dist_path=str(dist), built=dist.is_dir(), local=True)


class BoardOutcome(BaseModel):
    """One board's result inside a harvest run, as the extension saw it."""

    source: str = Field(min_length=1, max_length=50)
    found: int = 0
    saved: int = 0
    error: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=30)


class BoardOutcomeReport(BaseModel):
    per_site: list[BoardOutcome] = Field(default_factory=list, max_length=40)


@router.post("/board-health", summary="Report how each board behaved")
async def report_board_health(
    payload: BoardOutcomeReport,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
):
    """Record what happened on each board, so breakage becomes visible.

    Without this, a dead adapter is silent: the board returns nothing, the toast
    disappears, and the user concludes their search was too narrow. Three empty
    runs in a row against a board that has produced rows before is a fact worth
    surfacing, and only the server can remember it across sessions.
    """
    from app.job_discovery.board_health import record_run

    recorded = await record_run(db, user_id, [o.model_dump() for o in payload.per_site])
    return {"recorded": recorded}


@router.get("/profile", response_model=AutofillProfile, summary="Autofill profile")
async def get_autofill_profile(
    resume_id: str | None = None,
    company: str | None = None,
    title: str | None = None,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> AutofillProfile:
    """Serve the profile the extension fills forms from.

    ``company`` and ``title`` let the extension name the job the form belongs to,
    so the resume attached is the one tailored for it. Without them the master
    resume goes out - correct, but the generic answer, and the whole point of
    tailoring is lost at the one moment it counts.
    """
    return await build_autofill_profile(db, user_id, resume_id, company=company, title=title)


async def build_autofill_profile(
    db: Database,
    user_id: str,
    resume_id: str | None = None,
    *,
    company: str | None = None,
    title: str | None = None,
) -> AutofillProfile:
    """Build the autofill profile: **Profile first, resume as fallback**.

    The Profile is what the user curates in FitWright, so it is authoritative -
    it holds the answers a resume cannot carry (work authorization, visa status,
    notice period, salary expectation, relocation, structured address) and the
    ones they may deliberately want to differ from the resume. The resume only
    fills gaps the Profile has left empty, which keeps a fresh account useful
    before anyone has visited the Profile page.

    Eligibility answers are the exception to "fall back": they are never derived
    from resume prose. Guessing a visa status or salary wrong auto-rejects an
    application, so an unanswered field stays blank on purpose.
    """
    profile_row = await db.get_profile(user_id)
    document = _profile_document(profile_row)
    identity = document.get("identity") if isinstance(document.get("identity"), dict) else {}
    address = identity.get("address") if isinstance(identity.get("address"), dict) else {}

    # Which resume goes out. An explicit id wins; otherwise, if the extension
    # named the job, use the resume tailored for that company+role; only then
    # fall back to the master. This is the difference between FitWright's whole
    # premise working at the apply step and being quietly discarded there.
    tailored_for_role = False
    if resume_id is None and (company or title):
        from app.applications.resume_choice import resolve_resume_id_for_role

        matched = await resolve_resume_id_for_role(db, user_id, company=company, role=title)
        if matched:
            resume_id = matched
            tailored_for_role = True

    resume = await _resolve_resume(db, user_id, resume_id)
    processed: dict[str, Any] = {}
    if resume is not None:
        raw = resume.get("processed_data")
        if isinstance(raw, str):
            import json

            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        if isinstance(raw, dict):
            processed = raw

    personal = processed.get("personal_info")
    personal = personal if isinstance(personal, dict) else {}

    experience = processed.get("experience")
    latest_resume_role = (
        experience[0]
        if isinstance(experience, list) and experience and isinstance(experience[0], dict)
        else {}
    )

    # Current role: the Profile's explicit currentRole/currentCompany, else the
    # work-experience entry flagged `current`, else the resume's latest entry.
    profile_roles = document.get("workExperience")
    current_profile_role: dict[str, Any] = {}
    if isinstance(profile_roles, list):
        for entry in profile_roles:
            if isinstance(entry, dict) and entry.get("current"):
                current_profile_role = entry
                break
        if not current_profile_role and profile_roles and isinstance(profile_roles[0], dict):
            current_profile_role = profile_roles[0]

    # Highest education: the first entry, which the Profile editor keeps ordered
    # most-recent-first (same convention as the resume).
    education = document.get("education")
    top_education = (
        education[0]
        if isinstance(education, list) and education and isinstance(education[0], dict)
        else {}
    )

    name = _pick(identity.get("name"), personal.get("name"))
    first_name, last_name = _split_name(name)

    years = identity.get("yearsExperience")
    if not isinstance(years, (int, float)):
        years = _years_of_experience(processed) if processed else None

    rid = resume.get("resume_id") if resume else None
    relocation = identity.get("relocation")

    return AutofillProfile(
        full_name=name,
        first_name=first_name,
        last_name=last_name,
        email=_pick(identity.get("email"), personal.get("email")),
        phone=_pick(identity.get("phone"), personal.get("phone")),
        location=_pick(identity.get("location"), personal.get("location")),
        linkedin=_pick(identity.get("linkedin"), personal.get("linkedin")),
        github=_pick(identity.get("github"), personal.get("github")),
        website=_pick(identity.get("website"), personal.get("website")),
        current_title=_pick(
            identity.get("currentRole"),
            current_profile_role.get("title"),
            latest_resume_role.get("title"),
            personal.get("title"),
        ),
        current_company=_pick(
            identity.get("currentCompany"),
            current_profile_role.get("company"),
            latest_resume_role.get("company"),
        ),
        years_experience=float(years) if isinstance(years, (int, float)) else None,
        # Structured address - Profile only; a resume has no such breakdown.
        address_line1=_pick(address.get("line1")),
        address_line2=_pick(address.get("line2")),
        city=_pick(address.get("city")),
        state=_pick(address.get("state")),
        postal_code=_pick(address.get("postalCode")),
        country=_pick(address.get("country")),
        # Eligibility - Profile only, never inferred. See the docstring.
        work_authorization=_pick(identity.get("workAuthorization")),
        visa_status=_pick(identity.get("visaStatus")),
        notice_period=_pick(identity.get("noticePeriod")),
        salary_expectation=_pick(identity.get("salaryExpectation")),
        willing_to_relocate=relocation if isinstance(relocation, bool) else None,
        availability=_pick(identity.get("availability")),
        remote_preference=_pick(identity.get("remotePreference")),
        highest_degree=_pick(top_education.get("degree")),
        highest_institution=_pick(top_education.get("institution")),
        education_years=_pick(top_education.get("years")),
        resume_id=rid,
        resume_filename=str(resume.get("filename") or "resume.pdf") if resume else "",
        resume_pdf_path=f"/api/v1/resumes/{rid}/pdf" if rid else None,
        # Only claim "tailored" when the lookup actually matched and that resume
        # is the one being returned.
        resume_tailored_for_role=tailored_for_role and rid is not None,
    )


# --------------------------------------------------------------------------- #
# Capture + bulk scrape
# --------------------------------------------------------------------------- #
@router.post("/capture", response_model=CaptureResponse, summary="Capture one job")
async def capture_job(
    job: CapturedJob,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> CaptureResponse:
    """Save a job the user hit "Save to FitWright" on.

    Writes through the same deduplicated feed table as background discovery, so
    a captured job is indistinguishable downstream from a scraped one.
    """
    row = _to_feed_row(job)
    saved = await db.upsert_discovery_results(user_id, "extension-capture", [row])
    return CaptureResponse(
        saved=saved, duplicate=saved == 0, fingerprint=row["fingerprint"]
    )


@router.post("/scrape", response_model=ScrapeResponse, summary="Bulk-ingest scraped jobs")
async def ingest_scraped(
    batch: ScrapeBatch,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> ScrapeResponse:
    """Ingest a batch the extension scraped in a background tab.

    This is the path that fixes the boards the server cannot reach: the browser
    already holds the residential IP and the user's session, so the rows arrive
    here as plain data with no anti-bot problem to solve.
    """
    if not batch.jobs:
        return ScrapeResponse(received=0, saved=0, source=batch.source)

    rows = []
    for job in batch.jobs:  # size already capped by ScrapeBatch validation
        job.source = job.source or batch.source
        rows.append(_to_feed_row(job))

    saved = await db.upsert_discovery_results(
        user_id, f"extension-scrape:{batch.source}", rows
    )
    logger.info(
        "Extension scrape from %s for user %s: %d received, %d new",
        batch.source, user_id, len(rows), saved,
    )
    return ScrapeResponse(received=len(rows), saved=saved, source=batch.source)


# --------------------------------------------------------------------------- #
# Inline match score
# --------------------------------------------------------------------------- #
@router.post(
    "/match",
    response_model=MatchResponse,
    summary="Score a JD against the resume",
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def match_job(
    payload: MatchRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> MatchResponse:
    """Score one job description against a resume for the inline badge.

    Reuses the tailor pipeline's cached keyword extraction and scorer, so the
    number the badge shows is the same number the Discover feed shows - not a
    second, subtly different implementation.
    """
    resume = await _resolve_resume(db, user_id, payload.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="resume_not_found")

    processed = resume.get("processed_data")
    if not isinstance(processed, dict):
        # Unparsed resume: no structured skills to compare against.
        return MatchResponse(
            match_score=0.0, resume_id=resume.get("resume_id"), degraded=True
        )

    jd_text = f"{payload.title}\n\n{payload.description}".strip()

    from app.services.improver import extract_job_keywords_cached
    from app.services.refiner import calculate_keyword_match

    try:
        keywords = await extract_job_keywords_cached(user_id, jd_text)
    except Exception as exc:  # noqa: BLE001 - LLM outage must not break the badge
        logger.warning("Extension match: keyword extraction failed (%s)", exc)
        return MatchResponse(
            match_score=0.0, resume_id=resume.get("resume_id"), degraded=True
        )

    score = calculate_keyword_match(processed, keywords)

    # ``keywords`` carries the JD's required skills; split them by presence in
    # the resume so the badge can show what is matched vs missing.
    required = keywords.get("required_skills") or keywords.get("keywords") or []
    if not isinstance(required, list):
        required = []
    blob = str(processed).lower()
    matched = [k for k in required if isinstance(k, str) and k.lower() in blob]
    missing = [k for k in required if isinstance(k, str) and k.lower() not in blob]

    return MatchResponse(
        match_score=float(score),
        matched=matched[:20],
        missing=missing[:20],
        resume_id=resume.get("resume_id"),
    )


# --------------------------------------------------------------------------- #
# AI answer drafting
# --------------------------------------------------------------------------- #
_DRAFT_SYSTEM_PROMPT = (
    "You draft answers to job-application questions on behalf of a candidate. "
    "Write in the candidate's first person, plainly and specifically, grounded "
    "ONLY in the resume facts you are given. Never invent employers, dates, "
    "metrics, or credentials. If the resume does not support an answer, say so "
    "in one short sentence instead of inventing detail. Return the answer text "
    "only - no preamble, no quotes, no markdown."
)


@router.post(
    "/draft",
    response_model=DraftResponse,
    summary="Draft an application answer",
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def draft_answer(
    payload: DraftRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> DraftResponse:
    """Draft an answer to a free-text application question.

    The draft is returned, never submitted: the extension fills it into the
    field and the user edits before sending. That boundary is deliberate - an
    unreviewed generated answer going to a real employer is not a tradeoff
    worth making.
    """
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question_required")

    resume = await _resolve_resume(db, user_id, payload.resume_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="resume_not_found")

    processed = resume.get("processed_data")
    resume_context = (
        str(processed)[:6000]
        if isinstance(processed, dict)
        else str(resume.get("content") or "")[:6000]
    )

    prompt = (
        f"Question from the application form:\n{question}\n\n"
        f"Role: {payload.title or 'unspecified'} at "
        f"{payload.company or 'unspecified company'}\n\n"
        f"Job description (may be truncated):\n{payload.description[:4000]}\n\n"
        f"Candidate resume facts:\n{resume_context}\n\n"
        f"Write the answer in at most {max(40, min(payload.max_words, 400))} words."
    )

    from app.llm import complete

    try:
        answer = await complete(
            prompt,
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            max_tokens=700,
            temperature=0.5,
        )
    except Exception as exc:  # noqa: BLE001 - surface a usable message, not a 500
        logger.warning("Extension draft failed (%s)", exc)
        raise HTTPException(status_code=503, detail="llm_unavailable") from exc

    return DraftResponse(answer=(answer or "").strip())


# --------------------------------------------------------------------------- #
# Application tracking
# --------------------------------------------------------------------------- #
@router.post("/applied", response_model=AppliedResponse, summary="Mark a job applied")
async def mark_applied(
    payload: AppliedRequest,
    user_id: str = Depends(require_verified_user_id),
    db: Database = Depends(get_db),
) -> AppliedResponse:
    """Flip a feed row to ``applied`` after the extension saw a submission.

    Identified by fingerprint when the extension captured the job, else matched
    on URL. A miss is not an error - the user may have applied to something that
    never entered the feed - so this reports ``updated: false`` instead of 404.
    """
    from sqlalchemy import select, update as sa_update

    from app.models import DiscoveryResult

    fingerprint = payload.fingerprint
    if not fingerprint and not payload.url:
        raise HTTPException(status_code=422, detail="fingerprint_or_url_required")

    async with db._session() as session:
        async with session.begin():
            if not fingerprint:
                found = await session.execute(
                    select(DiscoveryResult.fingerprint).where(
                        (DiscoveryResult.user_id == user_id)
                        & (DiscoveryResult.url == payload.url)
                    )
                )
                fingerprint = found.scalar_one_or_none()
                if not fingerprint:
                    return AppliedResponse(updated=False, fingerprint=None)

            result = await session.execute(
                sa_update(DiscoveryResult)
                .where(
                    (DiscoveryResult.user_id == user_id)
                    & (DiscoveryResult.fingerprint == fingerprint)
                )
                .values(status="applied")
            )
            updated = (result.rowcount or 0) > 0

    return AppliedResponse(updated=updated, fingerprint=fingerprint)


__all__ = ["router", "EXTENSION_API_VERSION"]
