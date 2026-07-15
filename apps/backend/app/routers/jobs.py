"""Job description management endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_effective_user_id, require_verified_user_id
from app.database import db
from app.llm_ratelimit import llm_rate_limit_dep
from app.schemas import (
    JobAnalyzeKeywords,
    JobAnalyzeRequest,
    JobAnalyzeResponse,
    JobUploadRequest,
    JobUploadResponse,
)
from app.llm import get_llm_config, get_model_name
from app import analysis_cache
from app.services.improver import extract_job_keywords
from app.services.refiner import (
    _extract_all_text,
    _keyword_in_text,
    calculate_keyword_match,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])


def _get_resume_processed_data(resume: dict[str, Any]) -> dict[str, Any] | None:
    """Return the structured resume data for keyword comparison, if available."""
    import json

    data = resume.get("processed_data")
    if not data and resume.get("content_type") == "json":
        try:
            data = json.loads(resume["content"])
        except (json.JSONDecodeError, KeyError, TypeError):
            data = None
    return data if isinstance(data, dict) else None


@router.post("/upload", response_model=JobUploadResponse)
async def upload_job_descriptions(
    request: JobUploadRequest,
    user_id: str = Depends(get_effective_user_id),
) -> JobUploadResponse:
    """Upload one or more job descriptions.

    Stores the raw text for later use in resume tailoring.
    Returns an array of job_ids corresponding to the input array.
    """
    if not request.job_descriptions:
        raise HTTPException(status_code=400, detail="No job descriptions provided")

    job_ids = []
    for jd in request.job_descriptions:
        if not jd.strip():
            raise HTTPException(status_code=400, detail="Empty job description")

        job = await db.create_job(
            user_id,
            content=jd.strip(),
            resume_id=request.resume_id,
        )
        job_ids.append(job["job_id"])

    return JobUploadResponse(
        message="data successfully processed",
        job_id=job_ids,
        request={
            "job_descriptions": request.job_descriptions,
            "resume_id": request.resume_id,
        },
    )


@router.post(
    "/analyze",
    response_model=JobAnalyzeResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def analyze_job(
    request: JobAnalyzeRequest,
    user_id: str = Depends(require_verified_user_id),
) -> JobAnalyzeResponse:
    """Analyze a job description before tailoring (explicit user action only).

    Extracts the keyword breakdown from the JD and, when a resume with
    processed data is supplied, computes which JD keywords the resume already
    covers (matched), which it does not (missing), and an overall fit score.

    This endpoint performs a single LLM keyword extraction. It is never fired
    automatically — the frontend only calls it when the user explicitly asks
    for a fit analysis (cost-consent principle).
    """
    jd = request.job_description.strip()
    if not jd:
        raise HTTPException(status_code=400, detail="Empty job description")

    # Cache the (expensive) LLM keyword extraction, content-addressed by the JD
    # text: re-analyzing an identical JD reuses the stored keywords instead of
    # calling the LLM again. The matched/missing/fit below are cheap,
    # deterministic re-computations against the *current* resume, so they never
    # go stale even when reusing cached keywords.
    try:
        model_name = get_model_name(get_llm_config())
    except Exception:  # noqa: BLE001 - only used to key the cache
        model_name = None
    jd_checksum = analysis_cache.checksum_text(jd)
    keywords, _from_cache = await analysis_cache.get_or_compute(
        user_id=user_id,
        artifact_type=analysis_cache.ARTIFACT_JOB_ANALYSIS,
        source_id=jd_checksum,
        checksum=jd_checksum,
        version=analysis_cache.version_key(analysis_cache.ARTIFACT_JOB_ANALYSIS, model_name),
        compute=lambda: extract_job_keywords(jd),
    )

    response = JobAnalyzeResponse(
        keywords=JobAnalyzeKeywords(
            required_skills=_as_str_list(keywords.get("required_skills")),
            preferred_skills=_as_str_list(keywords.get("preferred_skills")),
            keywords=_as_str_list(keywords.get("keywords")),
            experience_requirements=_as_str_list(
                keywords.get("experience_requirements")
            ),
            seniority_level=_as_opt_str(keywords.get("seniority_level")),
            experience_years=_as_opt_str(keywords.get("experience_years")),
        ),
    )

    if not request.resume_id:
        return response

    resume = await db.get_resume(user_id, request.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    processed = _get_resume_processed_data(resume)
    if not processed:
        # Resume has no structured data yet (still processing): return keywords
        # only rather than a misleading 0% fit.
        return response

    resume_text = _extract_all_text(processed)
    all_keywords: list[str] = []
    seen: set[str] = set()
    for field in ("required_skills", "preferred_skills", "keywords"):
        for kw in _as_str_list(keywords.get(field)):
            key = kw.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                all_keywords.append(kw.strip())

    matched = [kw for kw in all_keywords if _keyword_in_text(kw, resume_text)]
    missing = [kw for kw in all_keywords if not _keyword_in_text(kw, resume_text)]

    response.matched = matched
    response.missing = missing
    response.fit_score = round(calculate_keyword_match(processed, keywords), 1)
    return response


def _as_str_list(value: Any) -> list[str]:
    """Coerce an LLM field into a clean list of non-empty strings."""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if isinstance(v, (str, int, float)) and str(v).strip()]


def _as_opt_str(value: Any) -> str | None:
    """Coerce an LLM field into an optional non-empty string."""
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        return text or None
    return None


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Get job description by ID."""
    job = await db.get_job(user_id, job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return job
