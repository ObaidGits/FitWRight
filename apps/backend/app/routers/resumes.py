"""Resume management endpoints."""

import asyncio
import copy
import hashlib
import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote
from uuid import uuid4

import time

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import ValidationError

from app.auth import get_effective_user_id, require_verified_user_id
from app.auth.ratelimit import RateLimitRule, get_rate_limiter
from app.config_cache import get_content_language, load_config as _load_config
from app.database import db
from app.errors import ApiError
from app.pdf import render_resume_pdf, PDFRenderError
from app.llm_ratelimit import llm_rate_limit_dep
from app.config import settings
from app.resilience import get_idempotency_cache, get_stream_registry
from app.resilience.metrics import get_resilience_metrics
from app.llm import (
    StreamResult,
    get_llm_config,
    get_model_name,
    provider_supports_streaming,
    stream_complete,
)
from app import analysis_cache
from app.services.cover_letter import (
    COVER_LETTER_SYSTEM_PROMPT,
    OUTREACH_SYSTEM_PROMPT,
    build_cover_letter_prompt,
    build_outreach_prompt,
)

logger = logging.getLogger(__name__)
from app.versions import service as version_service
from app.schemas import (
    ATSScore,
    ATSSubScores,
    GenerateContentResponse,
    GenerateInterviewPrepResponse,
    ImproveResumeConfirmRequest,
    ImproveResumeRequest,
    ImproveResumeResponse,
    ImproveResumeData,
    InterviewPrepData,
    RefinementStats,
    ResumeDiffSummary,
    ResumeFieldDiff,
    ResumeData,
    ResumeFetchData,
    ResumeFetchResponse,
    ResumeListResponse,
    ResumeSummary,
    ResumeUploadResponse,
    RawResume,
    UpdateCoverLetterRequest,
    UpdateOutreachMessageRequest,
    UpdateTemplateSettingsRequest,
    UpdateTitleRequest,
    normalize_resume_data,
)
from app.schemas import CreateResumeFromDataRequest
from app.services.parser import parse_document, parse_resume_to_json, restore_dates_from_markdown
from app.services.improver import (
    MONTH_PATTERN,
    apply_diffs,
    build_skill_target_plan,
    extract_job_keywords,
    generate_improvements,
    generate_skill_target_plan,
    generate_resume_diffs,
    improve_resume,
    verify_skill_target_plan,
    verify_diff_result,
)
from app.services.refiner import refine_resume, calculate_keyword_match
from app.services.ats import compute_ats_score
from app.schemas.refinement import RefinementConfig
from app.services.cover_letter import (
    compose_resume_title,
    generate_cover_letter,
    generate_outreach_message,
    generate_resume_title,
)
from app.services.interview_prep import generate_interview_prep
from app.prompts import DEFAULT_IMPROVE_PROMPT_ID, IMPROVE_PROMPT_OPTIONS


async def _auto_create_tracker_application(
    *,
    user_id: str,
    job_id: str,
    tailored_resume_id: str,
    master_resume_id: str,
    job: dict[str, Any] | None,
    title: str | None,
) -> None:
    """Best-effort: drop an ``applied`` card on the tracker after a tailoring.

    Company/role come from the cached job (zero extra LLM call). Wrapped so a
    tracker failure can never break the tailoring flow.
    """
    try:
        company = (job or {}).get("company")
        role = title or (job or {}).get("role")
        await db.create_application(
            user_id,
            job_id=job_id,
            resume_id=tailored_resume_id,
            master_resume_id=master_resume_id,
            status="applied",
            company=company,
            role=role,
        )
    except Exception as e:  # noqa: BLE001 - tracker is non-critical
        logger.warning("Failed to auto-create tracker application: %s", e)


def _get_default_prompt_id() -> str:
    """Get configured default prompt id from config file."""
    config = _load_config()
    option_ids = {option["id"] for option in IMPROVE_PROMPT_OPTIONS}
    prompt_id = config.get("default_prompt_id", DEFAULT_IMPROVE_PROMPT_ID)
    return prompt_id if prompt_id in option_ids else DEFAULT_IMPROVE_PROMPT_ID


def _hash_job_content(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# --- PDF appearance resolution (Bug #1: PDF must use the stored template) -----
# The export must render the resume's PERSISTED template even when no query
# params are supplied (e.g. a share/deep link or any non-editor caller); an
# explicit query param still overrides the stored value, and anything malformed
# falls back to the documented default. This keeps preview == PDF == print.

_PDF_DEFAULTS: dict[str, Any] = {
    "template": "swiss-single",
    "pageSize": "A4",
    "marginTop": 10,
    "marginBottom": 10,
    "marginLeft": 10,
    "marginRight": 10,
    "sectionSpacing": 3,
    "itemSpacing": 2,
    "lineHeight": 3,
    "fontSize": 3,
    "headerScale": 3,
    "headerFont": "serif",
    "bodyFont": "sans-serif",
    "compactMode": False,
    "showContactIcons": False,
    "accentColor": "blue",
}

_PDF_ENUMS: dict[str, set[str]] = {
    "template": {
        "swiss-single",
        "swiss-two-column",
        "modern",
        "modern-two-column",
        "latex",
        "clean",
        "vivid",
    },
    "pageSize": {"A4", "LETTER"},
    "headerFont": {"serif", "sans-serif", "mono"},
    "bodyFont": {"serif", "sans-serif", "mono"},
    "accentColor": {"blue", "green", "orange", "red"},
}

_PDF_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "marginTop": (5, 25),
    "marginBottom": (5, 25),
    "marginLeft": (5, 25),
    "marginRight": (5, 25),
    "sectionSpacing": (1, 5),
    "itemSpacing": (1, 5),
    "lineHeight": (1, 5),
    "fontSize": (1, 5),
    "headerScale": (1, 5),
}


def _flatten_stored_template(stored: Any) -> dict[str, Any]:
    """Flatten a persisted ``TemplateSettings`` dict to the PDF param names."""
    if not isinstance(stored, dict):
        return {}
    margins = stored.get("margins") if isinstance(stored.get("margins"), dict) else {}
    spacing = stored.get("spacing") if isinstance(stored.get("spacing"), dict) else {}
    font = stored.get("fontSize") if isinstance(stored.get("fontSize"), dict) else {}
    candidate = {
        "template": stored.get("template"),
        "pageSize": stored.get("pageSize"),
        "marginTop": margins.get("top"),
        "marginBottom": margins.get("bottom"),
        "marginLeft": margins.get("left"),
        "marginRight": margins.get("right"),
        "sectionSpacing": spacing.get("section"),
        "itemSpacing": spacing.get("item"),
        "lineHeight": spacing.get("lineHeight"),
        "fontSize": font.get("base"),
        "headerScale": font.get("headerScale"),
        "headerFont": font.get("headerFont"),
        "bodyFont": font.get("bodyFont"),
        "compactMode": stored.get("compactMode"),
        "showContactIcons": stored.get("showContactIcons"),
        "accentColor": stored.get("accentColor"),
    }
    return {k: v for k, v in candidate.items() if v is not None}


def _resolve_pdf_settings(
    stored: Any, overrides: dict[str, Any]
) -> dict[str, Any]:
    """Resolve final PDF appearance: query override -> stored -> default.

    Every value is validated/clamped so a malformed stored blob or override can
    never produce an invalid render request.
    """
    flat = _flatten_stored_template(stored)
    resolved: dict[str, Any] = {}
    for key, default in _PDF_DEFAULTS.items():
        val = overrides.get(key)
        if val is None:
            val = flat.get(key, default)
        if key in _PDF_ENUMS:
            if val not in _PDF_ENUMS[key]:
                val = default
        elif isinstance(default, bool):
            val = bool(val)
        elif isinstance(default, int):
            lo, hi = _PDF_INT_BOUNDS[key]
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = default
            val = max(lo, min(hi, val))
        resolved[key] = val
    return resolved


async def _parse_resume_cached(user_id: str, markdown: str) -> dict[str, Any]:
    """Parse resume markdown -> structured JSON, reusing a cached result.

    Content-addressed via the persistent analysis cache: re-parsing identical
    resume text (a duplicate upload or a ``retry-processing`` on unchanged
    content) under an unchanged prompt+model returns the stored structured data
    instead of spending another LLM call - while a genuinely new resume, or a
    prompt/model change, still parses fresh. Scoped per user.
    """
    try:
        model_name = get_model_name(get_llm_config())
    except Exception:  # noqa: BLE001 - only used to key the cache; parse will surface real errors
        model_name = None
    checksum = analysis_cache.checksum_text(markdown)
    version = analysis_cache.version_key(analysis_cache.ARTIFACT_RESUME_PARSE, model_name)
    result, _from_cache = await analysis_cache.get_or_compute(
        user_id=user_id,
        artifact_type=analysis_cache.ARTIFACT_RESUME_PARSE,
        source_id=checksum,
        checksum=checksum,
        version=version,
        compute=lambda: parse_resume_to_json(markdown),
    )
    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_PARSER
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_PARSER, 1)
    except Exception:
        pass  # metrics never break user operations
    return result


def _normalize_payload(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[Any, Any] = {}
        for key, val in value.items():
            normalized_key = (
                unicodedata.normalize("NFC", key) if isinstance(key, str) else key
            )
            normalized[normalized_key] = _normalize_payload(val)
        return normalized
    return value


def _serialize_interview_prep(interview_prep: InterviewPrepData | None) -> str | None:
    if interview_prep is None:
        return None
    return json.dumps(interview_prep.model_dump(mode="json"), ensure_ascii=False)


def _parse_interview_prep(
    raw: Any,
    *,
    resume_id: str | None = None,
) -> InterviewPrepData | None:
    if raw in (None, ""):
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return InterviewPrepData.model_validate(payload)
    except (TypeError, json.JSONDecodeError, ValidationError, ValueError) as e:
        logger.warning(
            "Invalid interview_prep payload for resume %s: %s",
            resume_id or "<unknown>",
            e,
        )
        return None


def _hash_improved_data(data: dict[str, Any]) -> str:
    """Hash canonicalized improved data for preview/confirm validation.

    Canonicalize through ``ResumeData`` first so a payload that merely omits
    optional fields (which the schema defaults) hashes identically to its
    schema-complete form. Without this, ``improve/preview`` (which hashes the
    raw ``improved_data`` dict) and ``improve/confirm`` (which hashes the
    ``ResumeData`` round-trip, ``request.improved_data.model_dump()``) disagree
    for any stored resume whose ``processed_data`` is not schema-complete, and a
    valid tailoring is rejected with 400 ("preview hash mismatch").
    """
    try:
        canonical: dict[str, Any] = ResumeData.model_validate(data).model_dump()
    except ValidationError:
        canonical = data  # not a full resume payload; hash as-is
    normalized = _normalize_payload(canonical)
    serialized = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,  # Preserve original behavior for hash stability
        default=str,  # Handle non-serializable types gracefully
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_personal_info_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    normalized = _normalize_payload(value)
    return json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _raise_improve_error(
    action: str,
    stage: str,
    error: Exception,
    detail: str,
) -> NoReturn:
    logger.error("Resume %s failed during %s: %s", action, stage, error)
    raise HTTPException(status_code=500, detail=detail)


def _get_original_resume_data(resume: dict[str, Any]) -> dict[str, Any] | None:
    original_data = resume.get("processed_data")
    if not original_data and resume.get("content_type") == "json":
        try:
            original_data = json.loads(resume["content"])
        except json.JSONDecodeError as e:
            logger.warning("Skipping resume diff due to JSON parse failure: %s", e)
    return original_data


def _get_original_markdown(resume: dict[str, Any]) -> str | None:
    """Get the original markdown content from a resume.

    Checks ``original_markdown`` first (persisted at upload), then
    falls back to ``content`` if it's still in markdown format.
    """
    md = resume.get("original_markdown")
    if md and isinstance(md, str):
        return md
    if resume.get("content_type") == "md":
        content = resume.get("content", "")
        if content and isinstance(content, str):
            return content
    return None


def _has_month(date_str: str) -> bool:
    """Return True if the date string contains a month name."""
    return bool(MONTH_PATTERN.search(date_str))


def _restore_original_dates(
    original_data: dict[str, Any] | None,
    improved_data: dict[str, Any],
) -> dict[str, Any]:
    """Restore original date/years values that the LLM may have truncated.

    Compares each entry's ``years`` field in the tailored resume against
    the corresponding entry in the original.  If the original has more
    date precision (e.g. includes a month) and the tailored version lost
    it, the original value is restored.
    """
    if not original_data:
        return improved_data

    result = copy.deepcopy(improved_data)

    for section_key in ("workExperience", "education", "personalProjects"):
        orig_entries = original_data.get(section_key, [])
        result_entries = result.get(section_key, [])
        for idx, orig_entry in enumerate(orig_entries):
            if idx >= len(result_entries):
                break
            if not isinstance(orig_entry, dict) or not isinstance(result_entries[idx], dict):
                continue
            orig_years = orig_entry.get("years", "")
            result_years = result_entries[idx].get("years", "")
            if (
                isinstance(orig_years, str)
                and isinstance(result_years, str)
                and orig_years
                and orig_years != result_years
                and _has_month(orig_years)
                and not _has_month(result_years)
            ):
                logger.info(
                    "Restoring date in %s[%d]: %r -> %r",
                    section_key,
                    idx,
                    result_years,
                    orig_years,
                )
                result_entries[idx]["years"] = orig_years

    # Custom sections (itemList)
    orig_custom = original_data.get("customSections", {})
    result_custom = result.get("customSections", {})
    if isinstance(orig_custom, dict) and isinstance(result_custom, dict):
        for section_key, orig_section in orig_custom.items():
            if not isinstance(orig_section, dict):
                continue
            result_section = result_custom.get(section_key)
            if not isinstance(result_section, dict):
                continue
            if orig_section.get("sectionType") != "itemList":
                continue
            orig_items = orig_section.get("items", [])
            result_items = result_section.get("items", [])
            for idx, orig_item in enumerate(orig_items):
                if idx >= len(result_items):
                    break
                if not isinstance(orig_item, dict) or not isinstance(result_items[idx], dict):
                    continue
                orig_years = orig_item.get("years", "")
                result_years = result_items[idx].get("years", "")
                if (
                    isinstance(orig_years, str)
                    and isinstance(result_years, str)
                    and orig_years
                    and orig_years != result_years
                    and _has_month(orig_years)
                    and not _has_month(result_years)
                ):
                    result_items[idx]["years"] = orig_years

    return result


def _preserve_original_skills(
    original_data: dict[str, Any] | None,
    improved_data: dict[str, Any],
) -> dict[str, Any]:
    """Restore any skills, certs, languages, or awards dropped by the LLM.

    This is a hard safety net: regardless of what the LLM returns, no
    original item from these lists is ever lost.  Dropped items are
    appended at the end of the improved list.
    """
    if not original_data:
        return improved_data

    result = copy.deepcopy(improved_data)

    orig_additional = original_data.get("additional", {})
    if not isinstance(orig_additional, dict):
        return result
    result_additional = result.setdefault("additional", {})

    list_fields = [
        "technicalSkills",
        "certificationsTraining",
        "languages",
        "awards",
    ]
    for field in list_fields:
        orig_items = orig_additional.get(field, [])
        if not isinstance(orig_items, list) or not orig_items:
            continue
        current_items = result_additional.get(field, [])
        if not isinstance(current_items, list):
            current_items = []

        # Build a case-insensitive index of what the LLM kept
        current_lower = {
            item.casefold() for item in current_items if isinstance(item, str)
        }

        # Append any originals that were dropped
        restored = 0
        for item in orig_items:
            if isinstance(item, str) and item.casefold() not in current_lower:
                current_items.append(item)
                current_lower.add(item.casefold())
                restored += 1

        if restored:
            logger.info("Restored %d dropped items in additional.%s", restored, field)
        result_additional[field] = current_items

    return result


def _protect_custom_sections(
    original_data: dict[str, Any] | None,
    improved_data: dict[str, Any],
) -> dict[str, Any]:
    """Protect custom sections from LLM hallucination.

    - If an item originally had description: [], revert any fabricated descriptions.
    - If the LLM added items that weren't in the original, remove them.
    """
    if not original_data:
        return improved_data

    orig_custom = original_data.get("customSections")
    if not isinstance(orig_custom, dict) or not orig_custom:
        return improved_data

    result = copy.deepcopy(improved_data)
    result_custom = result.get("customSections")
    if not isinstance(result_custom, dict):
        return result

    for section_key, orig_section in orig_custom.items():
        if not isinstance(orig_section, dict):
            continue
        result_section = result_custom.get(section_key)
        if not isinstance(result_section, dict):
            # Section was removed by LLM - restore original
            result_custom[section_key] = copy.deepcopy(orig_section)
            logger.info("Restored missing custom section: %s", section_key)
            continue

        section_type = orig_section.get("sectionType", "")
        if section_type == "itemList":
            orig_items = orig_section.get("items", [])
            result_items = result_section.get("items", [])
            if not isinstance(orig_items, list) or not isinstance(result_items, list):
                continue

            # Trim any items the LLM added beyond the original count
            if len(result_items) > len(orig_items):
                logger.info(
                    "Trimming %d hallucinated items from customSections.%s",
                    len(result_items) - len(orig_items),
                    section_key,
                )
                result_items = result_items[: len(orig_items)]

            # Revert fabricated descriptions on items that had empty descriptions
            for idx, orig_item in enumerate(orig_items):
                if idx >= len(result_items):
                    break
                if not isinstance(orig_item, dict):
                    continue
                orig_desc = orig_item.get("description")
                if isinstance(orig_desc, list) and len(orig_desc) == 0:
                    result_desc = result_items[idx].get("description")
                    if isinstance(result_desc, list) and len(result_desc) > 0:
                        logger.info(
                            "Reverted fabricated description on customSections.%s.items[%d]",
                            section_key,
                            idx,
                        )
                        result_items[idx]["description"] = []

            result_section["items"] = result_items

    result["customSections"] = result_custom
    return result


def _preserve_personal_info(
    original_data: dict[str, Any] | None,
    improved_data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Preserve personal info from original, return warnings if unable.

    Uses deep copy to prevent mutation of original data.
    """
    warnings: list[str] = []

    if not original_data:
        warnings.append(
            "Original resume data unavailable - personal info may be AI-generated"
        )
        return improved_data, warnings

    original_info = original_data.get("personalInfo")
    if not isinstance(original_info, dict):
        warnings.append("Original personal info missing or invalid")
        return improved_data, warnings

    # SVC-001: Use deep copy to prevent any mutation of original data
    result = copy.deepcopy(improved_data)
    result["personalInfo"] = copy.deepcopy(original_info)
    return result, warnings


def _build_ats_score(
    improved_data: dict[str, Any],
    job_keywords: dict[str, Any],
    refinement_result: Any,
    refinement_successful: bool,
) -> ATSScore | None:
    """Build ATSScore from refinement result and resume data."""
    try:
        kw_analysis = (
            refinement_result.keyword_analysis
            if refinement_successful and refinement_result is not None
            else None
        )
        final_match = (
            refinement_result.final_match_percentage
            if refinement_successful and refinement_result is not None
            else calculate_keyword_match(improved_data, job_keywords)
        )
        ats_raw = compute_ats_score(
            refined_resume=improved_data,
            job_keywords=job_keywords,
            keyword_match_percentage=final_match,
            missing_keywords=kw_analysis.non_injectable_keywords if kw_analysis else [],
            injectable_keywords=kw_analysis.injectable_keywords if kw_analysis else [],
        )
        return ATSScore(
            overall_score=ats_raw["overall_score"],
            sub_scores=ATSSubScores(**ats_raw["sub_scores"]),
            missing_keywords=ats_raw["missing_keywords"],
            injectable_keywords=ats_raw["injectable_keywords"],
            recommendations=ats_raw["recommendations"],
        )
    except Exception as e:
        logger.warning("ATS score computation failed", exc_info=True)
        return None


def _calculate_diff_from_resume(
    resume: dict[str, Any],
    improved_data: dict[str, Any],
) -> tuple[ResumeDiffSummary | None, list[ResumeFieldDiff] | None, str | None]:
    """Calculate resume diffs when structured data is available.

    Returns (summary, changes, error_reason). Error reason is None on success,
    or a string describing why diff calculation failed.
    """
    original_data = _get_original_resume_data(resume)
    if not original_data:
        return None, None, "original_data_missing"
    from app.services.improver import calculate_resume_diff

    try:
        summary, changes = calculate_resume_diff(original_data, improved_data)
        return summary, changes, None
    except Exception as e:
        logger.warning("Skipping resume diff due to calculation failure: %s", e)
        return None, None, f"calculation_error: {str(e)}"


def _validate_confirm_payload(
    original_data: dict[str, Any] | None,
    improved_data: dict[str, Any],
) -> None:
    if not original_data:
        logger.warning(
            "Skipping confirm payload validation; structured resume data unavailable."
        )
        return
    original_info = original_data.get("personalInfo")
    improved_info = improved_data.get("personalInfo")
    # JSON-008: Explicit null checks with clear error messages
    if original_info is None:
        raise ValueError("Original resume missing personalInfo")
    if improved_info is None:
        raise ValueError("Improved resume missing personalInfo")
    if not isinstance(original_info, dict):
        raise ValueError(
            f"Original personalInfo is not a dict: {type(original_info).__name__}"
        )
    if not isinstance(improved_info, dict):
        raise ValueError(
            f"Improved personalInfo is not a dict: {type(improved_info).__name__}"
        )
    fields = set(original_info.keys()) | set(improved_info.keys())
    mismatches = [
        field
        for field in sorted(fields)
        if _normalize_personal_info_value(original_info.get(field))
        != _normalize_personal_info_value(improved_info.get(field))
    ]
    if mismatches:
        raise ValueError(f"personalInfo fields changed: {', '.join(mismatches)}")


def _compose_role_and_company_from_keywords(
    job_keywords: dict[str, Any] | None,
) -> str:
    """Build a ``"Role @ Company"`` fragment from extracted JD keywords.

    Mirrors the LLM title prompt's contract ("Role @ Company", or just the role
    when the company is unknown) using the role/company that
    ``extract_job_keywords`` already returned - avoiding a redundant LLM call.
    Returns "" when no role is available, signalling the caller to fall back to
    the LLM title generator.
    """
    if not isinstance(job_keywords, dict):
        return ""
    raw_role = job_keywords.get("role")
    raw_company = job_keywords.get("company")
    role = raw_role.strip() if isinstance(raw_role, str) else ""
    company = raw_company.strip() if isinstance(raw_company, str) else ""
    if not role:
        return ""
    return f"{role} @ {company}" if company else role


async def _generate_auxiliary_messages(
    improved_data: dict[str, Any],
    job_content: str,
    language: str,
    enable_cover_letter: bool,
    enable_outreach: bool,
    enable_interview_prep: bool,
    job_keywords: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, str | None, InterviewPrepData | None, list[str]]:
    """Generate cover letter, outreach, interview prep, and resume title.

    Returns (cover_letter, outreach_message, title, interview_prep, warnings).

    Token optimization: the resume title ("Role @ Company") is composed
    deterministically from the role/company that ``extract_job_keywords`` already
    produced for this JD (persisted on the job and used by the tracker), so no
    separate LLM round-trip is spent re-deriving the same two fields. The LLM
    title generator is used only as a fallback when the keyword extractor did not
    surface a role (identical output surface, zero quality change on the common
    path).
    """
    cover_letter = None
    outreach_message = None
    title = None
    interview_prep = None
    warnings: list[str] = []
    generation_tasks: list[Awaitable[str | InterviewPrepData]] = []
    task_labels: list[str] = []

    # Title: reuse the already-extracted role/company (deterministic, no LLM).
    personal_info = improved_data.get("personalInfo")
    candidate_name = (
        personal_info.get("name") if isinstance(personal_info, dict) else None
    )
    role_and_company = _compose_role_and_company_from_keywords(job_keywords)
    if role_and_company:
        # Deterministic composition - same "<Name> - <Role @ Company>" shape the
        # LLM path produces, at zero token cost.
        title = compose_resume_title(candidate_name, role_and_company)
    else:
        # Fallback: no role surfaced by keyword extraction - keep the LLM path so
        # a title is still produced (unchanged behavior for this edge case).
        generation_tasks.append(
            generate_resume_title(job_content, language, candidate_name)
        )
        task_labels.append("title")

    if enable_cover_letter:
        generation_tasks.append(
            generate_cover_letter(improved_data, job_content, language)
        )
        task_labels.append("cover_letter")
    if enable_outreach:
        generation_tasks.append(
            generate_outreach_message(improved_data, job_content, language)
        )
        task_labels.append("outreach")
    if enable_interview_prep:
        generation_tasks.append(
            generate_interview_prep(improved_data, job_content, language)
        )
        task_labels.append("interview_prep")

    results = await asyncio.gather(*generation_tasks, return_exceptions=True)
    for label, result in zip(task_labels, results):
        if isinstance(result, Exception):
            logger.warning(
                "%s generation failed: %s",
                label,
                result,
                exc_info=result,
            )
            if label != "title":
                warnings.append(f"{label.replace('_', ' ').title()} generation failed")
        else:
            if label == "title":
                title = result
            elif label == "cover_letter":
                cover_letter = result
            elif label == "outreach":
                outreach_message = result
            elif label == "interview_prep":
                interview_prep = result

    return cover_letter, outreach_message, title, interview_prep, warnings


router = APIRouter(prefix="/resumes", tags=["Resumes"])


async def _capture_version(
    user_id: str,
    resume_id: str,
    processed_data: Any,
    source: str,
    *,
    label: str | None = None,
) -> None:
    """Best-effort version snapshot (P3 §A). Never fails the user's request.

    Version capture is a decoupled side effect: a snapshot write must never
    break upload/save/tailor. Gated by the ``VERSION_HISTORY`` flag; dedupe +
    debounce happen inside the service.
    """
    if not settings.version_history_enabled or not processed_data:
        return
    try:
        await version_service.capture_snapshot(
            user_id, resume_id, processed_data, source, label=label
        )
    except Exception:  # pragma: no cover - snapshot is best-effort
        logger.warning("Version snapshot (%s) failed for resume %s", source, resume_id, exc_info=True)


async def _emit_event(event_type, payload: dict[str, Any], *, user_id: str | None) -> None:
    """Best-effort domain-event emission to the outbox. Never fails the request.

    Async consumers (notifier, search indexer) pick these up decoupled from the
    write path, so a consumer/outbox hiccup never breaks upload/save/tailor.
    """
    try:
        from app.events import emit

        await emit(event_type, payload, user_id=user_id)
    except Exception:  # pragma: no cover - event emission is best-effort
        logger.warning("Outbox emit (%s) failed", event_type, exc_info=True)

ALLOWED_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# Fallback validation by extension: many clients (notably Windows without Office
# installed, and some browsers) send a generic "application/octet-stream" MIME
# type for perfectly valid .pdf/.docx files. Rejecting on MIME alone caused
# valid resumes to be refused before parsing, so we also accept known extensions.
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx"}
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4MB
# Slack over MAX_FILE_SIZE for multipart framing (boundaries + part headers)
# when doing the early Content-Length reject, so a valid max-size file isn't
# refused for its envelope overhead.
_MULTIPART_OVERHEAD = 64 * 1024  # 64KB


def _is_allowed_upload(content_type: str | None, filename: str | None) -> bool:
    """Accept an upload if EITHER its MIME type or its file extension is known.

    Clients frequently mislabel the MIME type (e.g. ``application/octet-stream``),
    so a valid extension is treated as sufficient. markitdown validates the real
    content downstream, and an empty-text guard catches anything unparseable.
    """
    if content_type in ALLOWED_TYPES:
        return True
    if filename:
        return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS
    return False


@router.post(
    "/upload",
    response_model=ResumeUploadResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def upload_resume(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_effective_user_id),
) -> ResumeUploadResponse:
    """Upload and process a resume file (PDF/DOCX).

    Converts the file to Markdown and stores it in the database.
    Optionally parses to structured JSON if LLM is configured.
    """
    # Validate file type by MIME type OR file extension (clients often send a
    # generic "application/octet-stream" MIME for valid PDF/DOCX files).
    if not _is_allowed_upload(file.content_type, file.filename):
        raise HTTPException(
            status_code=400,
            detail="Invalid file type. Allowed: PDF, DOC, DOCX",
        )

    # Fail fast on a declared body larger than the cap (+ multipart overhead
    # margin) BEFORE reading anything - cheap DoS guard for the honest-large case.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_SIZE + _MULTIPART_OVERHEAD:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    # Bounded read: never buffer more than the cap (+1 byte to detect overflow),
    # so a missing/lying Content-Length or a chunked body can't exhaust memory.
    content = await file.read(MAX_FILE_SIZE + 1)
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    # Convert to markdown
    try:
        markdown_content = await parse_document(content, file.filename or "resume.pdf")
    except Exception as e:
        logger.error(f"Document parsing failed: {e}")
        raise HTTPException(
            status_code=422,
            detail="Failed to parse document. Please ensure it's a valid PDF or DOCX file.",
        )

    # Validate extracted text is not empty (image-based PDFs / scanned documents)
    if not markdown_content or not markdown_content.strip():
        raise HTTPException(
            status_code=422,
            detail="Could not extract text from the uploaded file. The document may be image-based or scanned. Please upload a file with selectable text.",
        )

    # Store in database first with "processing" status (atomic master assignment)
    # original_markdown is preserved permanently for date reference even after
    # builder saves overwrite `content` with JSON.
    resume = await db.create_resume_atomic_master(
        user_id,
        content=markdown_content,
        content_type="md",
        filename=file.filename,
        processed_data=None,
        processing_status="processing",
        original_markdown=markdown_content,
    )

    # Try to parse to structured JSON (optional, may fail if LLM not configured)
    try:
        processed_data = await _parse_resume_cached(user_id, markdown_content)
        await db.update_resume(
            user_id,
            resume["resume_id"],
            {
                "processed_data": processed_data,
                "processing_status": "ready",
            },
        )
        resume["processed_data"] = processed_data
        resume["processing_status"] = "ready"
        # Capture the initial parse as the retained ``original`` snapshot (R1.1).
        await _capture_version(user_id, resume["resume_id"], processed_data, "original")
        from app.events import EventType

        await _emit_event(
            EventType.RESUME_PARSED,
            {"resume_id": resume["resume_id"]},
            user_id=user_id,
        )
    except Exception as e:
        # LLM parsing failed, update status to failed
        logger.warning(f"Resume parsing to JSON failed for {file.filename}: {e}")
        await db.update_resume(user_id, resume["resume_id"], {"processing_status": "failed"})
        resume["processing_status"] = "failed"
        from app.events import EventType

        await _emit_event(
            EventType.RESUME_PARSE_FAILED,
            {"resume_id": resume["resume_id"]},
            user_id=user_id,
        )

    # Return accurate status to client (API-001 fix)
    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_IMPORT
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_IMPORT, 1)
    except Exception:
        pass  # metrics never break user operations

    # --- Resume source metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import RESUMES_IMPORTED
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), RESUMES_IMPORTED, 1)
    except Exception:
        pass  # metrics never break user operations

    return ResumeUploadResponse(
        message=(
            f"File {file.filename} uploaded successfully"
            if resume["processing_status"] == "ready"
            else f"File {file.filename} uploaded but parsing failed"
        ),
        request_id=str(uuid4()),
        resume_id=resume["resume_id"],
        processing_status=resume["processing_status"],
        is_master=resume.get("is_master", False),
    )


def _validate_upload_bytes(request: Request, file: UploadFile, content: bytes) -> None:
    """Shared upload validation (type/size/empty) -> JSON 4xx before any streaming."""
    if not _is_allowed_upload(file.content_type, file.filename):
        raise HTTPException(status_code=400, detail="Invalid file type. Allowed: PDF, DOC, DOCX")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Empty file")


@router.post("/upload/stream", dependencies=[Depends(llm_rate_limit_dep)])
async def upload_resume_stream(
    request: Request,
    file: UploadFile = File(...),
    user_id: str = Depends(get_effective_user_id),
) -> StreamingResponse:
    """Upload + parse a resume, emitting HONEST per-stage SSE progress.

    Progress events map 1:1 to the REAL pipeline boundaries - never a fabricated
    bar: ``received`` -> ``extracting`` (``parse_document``) -> ``structuring``
    (``parse_resume_to_json``) -> ``done`` (carrying the same payload the
    non-stream ``/upload`` returns). All validation (type/size/empty) happens as
    a normal JSON 4xx BEFORE the stream opens. Gated by ``streaming_ai_enabled``
    so the client transparently falls back to ``/upload`` when disabled.

    Events: ``stage`` ({stage, status}), ``done`` ({result}), ``error`` ({code, message}).
    """
    if not settings.streaming_ai_enabled:
        raise ApiError(
            status_code=409,
            code="streaming_disabled",
            message="Streaming is disabled; use the standard upload.",
        )

    # Read + validate up front so failures are plain JSON, not SSE error events.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_FILE_SIZE + _MULTIPART_OVERHEAD:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )
    content = await file.read(MAX_FILE_SIZE + 1)
    _validate_upload_bytes(request, file, content)
    filename = file.filename or "resume.pdf"

    # Heartbeat cadence - must stay well under the Heroku router's 55s idle
    # timeout (H15/H28). The upload pipeline has two long awaits (document parse
    # and the LLM structuring call) during which no stage event is emitted; a
    # slow PDF or a 30-90s LLM call would otherwise stall the SSE connection
    # long enough for the platform to sever it mid-parse (surfacing to the user
    # as a truncated stream -> non-stream fallback -> Heroku "Application Error").
    heartbeat_seconds = settings.stream_heartbeat_seconds

    async def event_gen():
        from app.events import EventType

        # Background awaits we may need to cancel if the client disconnects.
        bg_tasks: list[asyncio.Task] = []

        try:
            yield _sse("stage", {"stage": "received", "status": "done"})

            # Stage 1: extract text (real boundary). Awaited via a heartbeat pump
            # so a slow parse can never trip the platform idle timeout.
            yield _sse("stage", {"stage": "extracting", "status": "active"})
            parse_task = asyncio.ensure_future(parse_document(content, filename))
            bg_tasks.append(parse_task)
            while True:
                done_set, _ = await asyncio.wait({parse_task}, timeout=heartbeat_seconds)
                if done_set:
                    break
                yield _sse("heartbeat", {"stage": "extracting"})
            try:
                markdown = parse_task.result()
            except Exception as e:  # noqa: BLE001
                logger.error("Streaming upload: document parse failed: %s", e)
                yield _sse(
                    "error",
                    {
                        "code": "parse_failed",
                        "message": "Failed to parse document. Please upload a valid PDF or DOCX.",
                    },
                )
                return
            if not markdown or not markdown.strip():
                yield _sse(
                    "error",
                    {
                        "code": "empty_text",
                        "message": "Could not extract text - the file may be scanned/image-based.",
                    },
                )
                return
            yield _sse("stage", {"stage": "extracting", "status": "done"})

            resume = await db.create_resume_atomic_master(
                user_id,
                content=markdown,
                content_type="md",
                filename=file.filename,
                processed_data=None,
                processing_status="processing",
                original_markdown=markdown,
            )

            # Stage 2: structure with the LLM (real boundary). The LLM call is
            # the slowest stage (often 30-90s); pump heartbeats while it runs so
            # the Heroku router keeps the SSE connection open to completion.
            yield _sse("stage", {"stage": "structuring", "status": "active"})
            struct_task = asyncio.ensure_future(_parse_resume_cached(user_id, markdown))
            bg_tasks.append(struct_task)
            while True:
                done_set, _ = await asyncio.wait({struct_task}, timeout=heartbeat_seconds)
                if done_set:
                    break
                yield _sse("heartbeat", {"stage": "structuring"})
            try:
                processed_data = struct_task.result()
                await db.update_resume(
                    user_id,
                    resume["resume_id"],
                    {"processed_data": processed_data, "processing_status": "ready"},
                )
                resume["processing_status"] = "ready"
                await _capture_version(user_id, resume["resume_id"], processed_data, "original")
                await _emit_event(
                    EventType.RESUME_PARSED, {"resume_id": resume["resume_id"]}, user_id=user_id
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Streaming upload: JSON parse failed for %s: %s", filename, e)
                await db.update_resume(
                    user_id, resume["resume_id"], {"processing_status": "failed"}
                )
                resume["processing_status"] = "failed"
                await _emit_event(
                    EventType.RESUME_PARSE_FAILED,
                    {"resume_id": resume["resume_id"]},
                    user_id=user_id,
                )
            yield _sse("stage", {"stage": "structuring", "status": "done"})

            yield _sse(
                "done",
                {
                    "result": {
                        "resume_id": resume["resume_id"],
                        "processing_status": resume["processing_status"],
                        "is_master": resume.get("is_master", False),
                        "message": (
                            "Resume uploaded successfully"
                            if resume["processing_status"] == "ready"
                            else "Resume uploaded but parsing failed"
                        ),
                    }
                },
            )

            # --- Feature usage metric (daily aggregate, fire-and-forget) ---
            try:
                from datetime import datetime, timezone
                from app.admin.metric_store import get_metric_store
                from app.admin.metric_registry import FEAT_IMPORT
                await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_IMPORT, 1)
            except Exception:
                pass  # metrics never break user operations
        except asyncio.CancelledError:  # pragma: no cover - client disconnect
            raise
        except Exception as e:  # noqa: BLE001 - defensive
            logger.exception("Streaming upload failed: %s", e)
            yield _sse("error", {"code": "stream_error", "message": "Upload failed; please retry."})
        finally:
            # If the client disconnected mid-stream, don't leak the parse/LLM
            # tasks - cancel any that are still running.
            for t in bg_tasks:
                if not t.done():
                    t.cancel()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("", response_model=ResumeFetchResponse)
async def get_resume(
    resume_id: str = Query(...),
    user_id: str = Depends(get_effective_user_id),
) -> ResumeFetchResponse:
    """Fetch resume details by ID.

    Returns both raw markdown and structured data (if available),
    plus cover letter and outreach message if they exist.
    Applies lazy migration for section metadata if needed.
    """
    resume = await db.get_resume(user_id, resume_id)

    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    await _reresolve_canonical_photo(resume, user_id)
    return _build_get_resume_response(resume_id, resume)


async def _reresolve_canonical_photo(resume: dict, owner_id: str) -> None:
    """Re-point a resume's header photo at the *live* profile photo (Photo System).

    Photo provenance rule (single source: ``app.profile.photo.resolve_photo_url``):
    a resume with ``photo.ref == "canonical"`` tracks the user's current profile
    photo, so replacing the profile photo is reflected here on next read. A
    ``snapshot`` resume is pinned and left untouched. Best-effort: any lookup
    failure leaves the stored ``avatarUrl`` as-is (never breaks a read).
    """
    processed = resume.get("processed_data")
    if not isinstance(processed, dict):
        return
    personal = processed.get("personalInfo")
    if not isinstance(personal, dict):
        return
    photo = personal.get("photo")
    if not isinstance(photo, dict) or not photo.get("show") or photo.get("ref") != "canonical":
        return
    try:
        from app.auth.accounts import get_by_id

        record = await get_by_id(owner_id)
        personal["avatarUrl"] = record.avatar_url if record else None
    except Exception:  # pragma: no cover - defensive; render still works with stored url
        logger.debug("Canonical photo re-resolution failed", exc_info=True)


def _build_get_resume_response(resume_id: str, resume: dict) -> ResumeFetchResponse:
    """Shape a facade resume dict into the standard fetch response.

    Shared by the session-authenticated ``GET /resumes`` and the print-token
    authenticated ``GET /resumes/print-data`` so both return an identical shape.
    """
    # Get processing status (default to "pending" for old records)
    processing_status = resume.get("processing_status", "pending")

    # Build response
    raw_resume = RawResume(
        id=None,  # TinyDB doesn't have numeric IDs like SQL
        content=resume["content"],
        content_type=resume["content_type"],
        created_at=resume["created_at"],
        processing_status=processing_status,
    )

    # Get processed data if available (no more on-demand parsing)
    processed_data = resume.get("processed_data")

    # Apply lazy migration - add section metadata to old resumes
    if processed_data:
        processed_data = normalize_resume_data(processed_data)

    processed_resume = (
        ResumeData.model_validate(processed_data) if processed_data else None
    )

    return ResumeFetchResponse(
        request_id=str(uuid4()),
        data=ResumeFetchData(
            resume_id=resume_id,
            raw_resume=raw_resume,
            processed_resume=processed_resume,
            cover_letter=resume.get("cover_letter"),
            outreach_message=resume.get("outreach_message"),
            interview_prep=_parse_interview_prep(
                resume.get("interview_prep"),
                resume_id=resume_id,
            ),
            parent_id=resume.get("parent_id"),
            title=resume.get("title"),
            template_settings=resume.get("template_settings"),
            version=resume.get("version"),
        ),
    )


@router.get("/print-data", response_model=ResumeFetchResponse)
async def get_resume_print_data(
    resume_id: str = Query(...),
    token: str = Query(...),
) -> ResumeFetchResponse:
    """Read-only resume fetch for server-side PDF rendering (print-token auth).

    Authenticated by a short-lived signed print token (NOT the user session), so
    the headless-Chromium render of the ``/print`` route can load the resume in
    hosted mode. The token is bound to ``(user_id, resume_id)`` and expires in
    minutes; it only ever exposes the resume it was minted for.
    """
    from app.pdf_token import verify_print_token

    owner_id = verify_print_token(token, resume_id)
    if not owner_id:
        raise HTTPException(status_code=401, detail="invalid_or_expired_print_token")

    resume = await db.get_resume(owner_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    await _reresolve_canonical_photo(resume, owner_id)
    return _build_get_resume_response(resume_id, resume)


@router.get("/list", response_model=ResumeListResponse)
async def list_resumes(
    include_master: bool = Query(False),
    user_id: str = Depends(get_effective_user_id),
) -> ResumeListResponse:
    """List resumes, optionally including the master resume."""
    resumes = await db.list_resumes(user_id)
    if not include_master:
        resumes = [resume for resume in resumes if not resume.get("is_master", False)]

    resumes.sort(key=lambda item: item.get("updated_at", ""), reverse=True)

    summaries = [
        ResumeSummary(
            resume_id=resume["resume_id"],
            filename=resume.get("filename"),
            is_master=resume.get("is_master", False),
            parent_id=resume.get("parent_id"),
            processing_status=resume.get("processing_status", "pending"),
            created_at=resume.get("created_at", ""),
            updated_at=resume.get("updated_at", ""),
            title=resume.get("title"),
        )
        for resume in resumes
    ]

    return ResumeListResponse(request_id=str(uuid4()), data=summaries)


@router.post(
    "/improve/preview",
    response_model=ImproveResumeResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def improve_resume_preview_endpoint(
    request: ImproveResumeRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ImproveResumeResponse:
    """Preview a tailored resume without persisting it.

    The response includes resume_preview data but leaves resume_id null.
    """
    resume = await db.get_resume(user_id, request.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    job = await db.get_job(user_id, request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job description not found")

    language = get_content_language()
    prompt_id = request.prompt_id or _get_default_prompt_id()

    stage = "load_job_keywords"
    detail = "Failed to preview resume. Please try again."
    try:
        return await asyncio.wait_for(
            _improve_preview_flow(
                user_id=user_id,
                request=request,
                resume=resume,
                job=job,
                language=language,
                prompt_id=prompt_id,
            ),
            timeout=settings.request_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Improve preview timed out after %ss for resume %s / job %s",
            settings.request_timeout_seconds,
            request.resume_id,
            request.job_id,
        )
        raise HTTPException(
            status_code=504,
            detail=(
                f"Resume tailoring timed out after {settings.request_timeout_seconds}s. "
                "If you are running a local LLM, raise REQUEST_TIMEOUT_SECONDS (and the "
                "matching frontend NEXT_PUBLIC_REQUEST_TIMEOUT_MS); otherwise try a shorter "
                "job description or a simpler prompt."
            ),
        )
    except Exception as e:
        _raise_improve_error("preview", stage, e, detail)


class _TailorStreamCancelled(Exception):
    """Raised inside the preview flow when the client cancels a streamed tailor.

    Distinct from :class:`asyncio.CancelledError` so the streaming endpoint can
    emit a clean ``cancelled`` done-event (and persist nothing) rather than
    treating it as a hard failure that triggers a client fallback.
    """


@router.post("/improve/preview/stream")
async def stream_improve_preview_endpoint(
    request_body: ImproveResumeRequest,
    request: Request,
    request_id: str = Query(default="", max_length=100),
    user_id: str = Depends(require_verified_user_id),
) -> StreamingResponse:
    """Stream the tailor pipeline as *stage-progress* SSE (not token streaming).

    The tailor flow is a sequence of discrete stages (extract keywords -> plan
    skills -> rewrite -> refine -> score), so honest progress means emitting a
    ``stage`` event at each real boundary - never a fabricated progress bar. The
    final ``done`` event carries the complete ``ImproveResumeResponse`` (same
    shape the non-stream endpoint returns), so the client renders identical
    results. On cancellation nothing is persisted beyond the preview-hash the
    non-stream path also writes; on any error the client falls back to
    ``POST /resumes/improve/preview`` transparently.

    Events: ``stage`` ({stage, status}), ``heartbeat`` (liveness), ``done``
    ({result} | {cancelled: true}), ``error`` ({code, message} -> fallback).

    NOTE: this route is defined *before* ``/{resume_id}/{kind}/stream`` so its
    static path wins over the parameterized token-stream route (Starlette
    matches in registration order).
    """
    if len(request_id) < 8:
        raise HTTPException(status_code=422, detail="request_id must be at least 8 chars")

    # Flag gate (ADR-14): off -> client transparently uses the non-stream path.
    if not settings.streaming_ai_enabled:
        raise ApiError(
            status_code=409, code="streaming_disabled",
            message="Streaming is disabled; use the standard generation.",
        )

    # Validate ownership + existence BEFORE opening the stream so a 404 is a
    # normal JSON error (not an SSE error event).
    resume = await db.get_resume(user_id, request_body.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    job = await db.get_job(user_id, request_body.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job description not found")

    language = get_content_language()
    prompt_id = request_body.prompt_id or _get_default_prompt_id()

    # Rate-limit stream starts (ADR-8), shared with token streams.
    limiter = get_rate_limiter()
    rl = await limiter.check("stream", user_id, _STREAM_START_RULE, fail_closed=False)
    if not rl.allowed:
        raise ApiError(
            status_code=429, code="rate_limited",
            message="Too many streams started; please wait a moment.",
            headers={"Retry-After": str(rl.retry_after)},
        )

    registry = get_stream_registry()
    heartbeat_ttl = settings.stream_heartbeat_seconds * 4
    ok = await registry.try_register(
        user_id, request_id,
        max_concurrent=settings.stream_max_concurrent_per_user,
        heartbeat_ttl=heartbeat_ttl,
    )
    metrics = get_resilience_metrics()
    if not ok:
        metrics.stream_rejected_cap()
        raise ApiError(
            status_code=429, code="stream_limit_reached",
            message="You have too many active generations; cancel one and retry.",
            headers={"Retry-After": "5"},
        )

    heartbeat_seconds = settings.stream_heartbeat_seconds

    async def event_gen():
        queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()
        SENTINEL = "__done__"

        async def emit_stage(stage: str, status: str) -> None:
            await queue.put(("stage", {"stage": stage, "status": status}))

        async def cancel_check() -> bool:
            try:
                if await request.is_disconnected():
                    return True
            except Exception:  # pragma: no cover
                pass
            return await registry.is_cancelled(user_id, request_id)

        async def run_flow() -> None:
            try:
                result = await asyncio.wait_for(
                    _improve_preview_flow(
                        user_id=user_id,
                        request=request_body,
                        resume=resume,
                        job=job,
                        language=language,
                        prompt_id=prompt_id,
                        emit_stage=emit_stage,
                        cancel_check=cancel_check,
                    ),
                    timeout=settings.request_timeout_seconds,
                )
                await queue.put(("done", {"result": result.model_dump(mode="json")}))
            except _TailorStreamCancelled:
                await queue.put(("cancelled", None))
            except asyncio.TimeoutError:
                await queue.put((
                    "error",
                    {"code": "stream_timeout", "message": "Tailoring took too long and was stopped."},
                ))
            except Exception:
                logger.exception(
                    "Streaming improve preview failed for resume %s / job %s",
                    request_body.resume_id, request_body.job_id,
                )
                await queue.put((
                    "error",
                    {"code": "stream_error", "message": "Tailoring failed; falling back."},
                ))
            finally:
                await queue.put((SENTINEL, None))

        metrics.stream_started()
        task = asyncio.create_task(run_flow())
        try:
            # Kick off with a heartbeat so a cold dyno shows liveness immediately.
            yield _sse("heartbeat", {"request_id": request_id})
            while True:
                try:
                    kind, payload = await asyncio.wait_for(
                        queue.get(), timeout=heartbeat_seconds
                    )
                except TimeoutError:
                    yield _sse("heartbeat", {"request_id": request_id})
                    await registry.heartbeat(
                        user_id, request_id, heartbeat_ttl=heartbeat_ttl
                    )
                    continue
                if kind == SENTINEL:
                    break
                if kind == "stage":
                    yield _sse("stage", payload or {})
                elif kind == "done":
                    yield _sse("done", payload or {})
                elif kind == "cancelled":
                    metrics.stream_cancelled()
                    yield _sse("done", {"cancelled": True})
                elif kind == "error":
                    metrics.stream_error()
                    yield _sse("error", payload or {})
        except asyncio.CancelledError:  # pragma: no cover - server shutdown
            raise
        finally:
            if not task.done():
                task.cancel()
            metrics.stream_ended()
            await registry.unregister(user_id, request_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _improve_preview_flow(
    *,
    user_id: str,
    request: ImproveResumeRequest,
    resume: dict[str, Any],
    job: dict[str, Any],
    language: str,
    prompt_id: str,
    emit_stage: Callable[[str, str], Awaitable[None]] | None = None,
    cancel_check: Callable[[], Awaitable[bool]] | None = None,
) -> ImproveResumeResponse:
    """Inner flow for improve/preview, extracted so it can be wrapped in wait_for.

    When ``emit_stage`` is provided (streaming path), it is awaited at each real
    pipeline boundary with ``(stage_name, "start"|"done")`` so the client can
    render honest, non-fabricated progress. ``cancel_check`` is polled at stage
    starts; returning True raises :class:`_TailorStreamCancelled` to abort the
    pipeline before the next (possibly expensive) LLM call. Both default to
    ``None`` so the non-streaming endpoint is byte-for-byte unchanged.
    """

    async def _emit(stage: str, status: str) -> None:
        if emit_stage is not None:
            await emit_stage(stage, status)

    async def _guard() -> None:
        if cancel_check is not None and await cancel_check():
            raise _TailorStreamCancelled()

    await _guard()
    await _emit("keywords", "start")
    job_keywords = job.get("job_keywords")
    job_keywords_hash = job.get("job_keywords_hash")
    content_hash = _hash_job_content(job["content"])
    if not job_keywords or job_keywords_hash != content_hash:
        job_keywords = await extract_job_keywords(job["content"])
        # Cache extracted keywords with a content hash for basic invalidation.
        # Also surface company/role to the job's top level so the tracker's
        # auto-create-on-confirm path can read them without an extra LLM call.
        cache_updates: dict[str, Any] = {
            "job_keywords": job_keywords,
            "job_keywords_hash": content_hash,
        }
        # LLM output isn't guaranteed to be a string - guard before .strip().
        raw_company = job_keywords.get("company")
        raw_role = job_keywords.get("role")
        company = raw_company.strip() if isinstance(raw_company, str) else ""
        role = raw_role.strip() if isinstance(raw_role, str) else ""
        if company:
            cache_updates["company"] = company
        if role:
            cache_updates["role"] = role
        try:
            updated_job = await db.update_job(
                user_id,
                request.job_id,
                cache_updates,
            )
            if not updated_job:
                logger.warning(
                    "Failed to persist job keywords for job %s.",
                    request.job_id,
                )
        except Exception as e:
            logger.warning(
                "Failed to persist job keywords for job %s: %s",
                request.job_id,
                e,
            )
    await _emit("keywords", "done")
    original_resume_data = _get_original_resume_data(resume)
    # Collect warnings throughout the process
    response_warnings: list[str] = []

    # Diff-based improvement: generate targeted changes, apply with verification
    if original_resume_data:
        await _guard()
        await _emit("plan", "start")
        skill_targets: list[dict[str, Any]] = []
        try:
            # Deterministic skill-target plan (audit R2): the accepted set is
            # computed from existing resume skills + JD-stated skills - the exact
            # universe the LLM plan was filtered down to by verify_skill_target_plan.
            # This removes one LLM round-trip per tailor while keeping the identical
            # apply_diffs anti-fabrication gate (skills outside this set are never
            # added). Measured ~24% fewer tailor tokens with unchanged structural
            # scores (sections preserved, no fabricated employers, keyword coverage).
            verified_skill_plan = build_skill_target_plan(
                original_resume_data,
                job_keywords,
                job["content"],
            )
            accepted_targets = verified_skill_plan.get("accepted", [])
            if isinstance(accepted_targets, list):
                skill_targets = [
                    target
                    for target in accepted_targets
                    if isinstance(target, dict)
                ]
        except Exception as e:
            logger.warning("Skill target planning failed, continuing without it: %s", e)
            response_warnings.append("Skill target planning failed")

        await _emit("plan", "done")
        await _guard()
        await _emit("rewrite", "start")
        diff_result = await generate_resume_diffs(
            original_resume=resume["content"],
            job_description=job["content"],
            job_keywords=job_keywords,
            language=language,
            prompt_id=prompt_id,
            original_resume_data=original_resume_data,
            skill_targets=skill_targets,
        )

        improved_data, applied_changes, rejected_changes = apply_diffs(
            original=original_resume_data,
            changes=diff_result.changes,
            allowed_skill_targets=skill_targets,
        )

        diff_warnings = verify_diff_result(
            original=original_resume_data,
            result=improved_data,
            applied_changes=applied_changes,
            job_keywords=job_keywords,
        )
        response_warnings.extend(diff_warnings)

        if rejected_changes:
            response_warnings.append(
                f"{len(rejected_changes)} change(s) rejected during verification"
            )

        logger.info(
            "Diff-based improve: %d applied, %d rejected, %d warnings",
            len(applied_changes),
            len(rejected_changes),
            len(diff_warnings),
        )
        await _emit("rewrite", "done")
    else:
        await _guard()
        await _emit("rewrite", "start")
        # Fallback to full-output mode when no structured data available
        improved_data = await improve_resume(
            original_resume=resume["content"],
            job_description=job["content"],
            job_keywords=job_keywords,
            language=language,
            prompt_id=prompt_id,
            original_resume_data=original_resume_data,
        )
        await _emit("rewrite", "done")

    # Safety nets (defense in depth - should rarely activate with diff-based flow)
    improved_data, preserve_warnings = _preserve_personal_info(
        original_resume_data,
        improved_data,
    )
    response_warnings.extend(preserve_warnings)

    improved_data = _restore_original_dates(original_resume_data, improved_data)
    original_markdown = _get_original_markdown(resume)
    if original_markdown:
        improved_data = restore_dates_from_markdown(improved_data, original_markdown)
    improved_data = _preserve_original_skills(original_resume_data, improved_data)
    improved_data = _protect_custom_sections(original_resume_data, improved_data)

    # Multi-pass refinement: keyword injection, AI phrase removal, alignment validation
    await _guard()
    await _emit("refine", "start")
    refinement_stats: RefinementStats | None = None
    refinement_result = None
    refinement_attempted = False
    refinement_successful = False
    try:
        # Get master resume for alignment validation
        master_resume = await db.get_master_resume(user_id)
        master_data = (
            _get_original_resume_data(master_resume)
            if master_resume
            else _get_original_resume_data(resume)
        )
        if master_data:
            initial_match = calculate_keyword_match(improved_data, job_keywords)
            refinement_attempted = True
            refinement_result = await refine_resume(
                initial_tailored=improved_data,
                master_resume=master_data,
                job_description=job["content"],
                job_keywords=job_keywords,
                config=RefinementConfig(),
            )
            improved_data = refinement_result.refined_data
            refinement_stats = RefinementStats(
                passes_completed=refinement_result.passes_completed,
                keywords_injected=(
                    len(refinement_result.keyword_analysis.injectable_keywords)
                    if refinement_result.keyword_analysis
                    else 0
                ),
                ai_phrases_removed=refinement_result.ai_phrases_removed,
                alignment_violations_fixed=(
                    len(
                        [
                            v
                            for v in refinement_result.alignment_report.violations
                            if v.severity == "critical"
                        ]
                    )
                    if refinement_result.alignment_report
                    else 0
                ),
                initial_match_percentage=initial_match,
                final_match_percentage=refinement_result.final_match_percentage,
            )
            refinement_successful = True
            logger.info(
                "Refinement completed: %d passes, %d AI phrases removed",
                refinement_result.passes_completed,
                len(refinement_result.ai_phrases_removed),
            )
    except Exception as e:
        logger.warning("Refinement failed, using unrefined result: %s", e)
        if refinement_attempted:
            response_warnings.append(f"Refinement failed: {str(e)}")

    await _emit("refine", "done")
    await _emit("score", "start")
    improved_text = json.dumps(improved_data, indent=2)
    preview_hash = _hash_improved_data(improved_data)
    preview_hashes = job.get("preview_hashes")
    if not isinstance(preview_hashes, dict):
        preview_hashes = {}
    preview_hashes[prompt_id] = preview_hash
    # NOTE: preview_hashes updates are last-write-wins; concurrent previews can race.
    try:
        updated_job = await db.update_job(
            user_id,
            request.job_id,
            {
                "preview_hash": preview_hash,
                "preview_prompt_id": prompt_id,
                "preview_hashes": preview_hashes,
            },
        )
        if not updated_job:
            logger.warning(
                "Failed to persist preview hash for job %s.", request.job_id
            )
    except Exception as e:
        logger.warning(
            "Failed to persist preview hash for job %s: %s", request.job_id, e
        )
    diff_summary, detailed_changes, diff_error = _calculate_diff_from_resume(
        resume,
        improved_data,
    )
    if diff_error:
        response_warnings.append(f"Could not calculate changes: {diff_error}")
    improvements = generate_improvements(job_keywords)
    await _emit("score", "done")

    request_id = str(uuid4())
    return ImproveResumeResponse(
        request_id=request_id,
        data=ImproveResumeData(
            request_id=request_id,
            resume_id=None,
            job_id=request.job_id,
            resume_preview=ResumeData.model_validate(improved_data),
            improvements=[
                {
                    "suggestion": imp["suggestion"],
                    "lineNumber": imp.get("lineNumber"),
                }
                for imp in improvements
            ],
            markdownOriginal=resume["content"],
            markdownImproved=improved_text,
            cover_letter=None,
            outreach_message=None,
            interview_prep=None,
            diff_summary=diff_summary,
            detailed_changes=detailed_changes,
            refinement_stats=refinement_stats,
            ats_score=_build_ats_score(
                improved_data,
                job_keywords,
                refinement_result,
                refinement_successful,
            ),
            warnings=response_warnings,
            refinement_attempted=refinement_attempted,
            refinement_successful=refinement_successful,
        ),
    )


@router.post(
    "/improve/confirm",
    response_model=ImproveResumeResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def improve_resume_confirm_endpoint(
    request: ImproveResumeConfirmRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ImproveResumeResponse:
    """Confirm and persist a tailored resume."""
    resume = await db.get_resume(user_id, request.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    job = await db.get_job(user_id, request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job description not found")

    feature_config = _load_config()
    enable_cover_letter = feature_config.get("enable_cover_letter", False)
    enable_outreach = feature_config.get("enable_outreach_message", False)
    enable_interview_prep = feature_config.get("enable_interview_prep", False)
    language = get_content_language()

    stage = "serialize_improved_data"
    detail = "Failed to confirm resume. Please try again."
    try:
        improved_data = request.improved_data.model_dump()
        improved_text = json.dumps(improved_data, indent=2)
        # NOTE: This endpoint relies on preview-hash validation to ensure the payload matches a prior preview.
        # Stronger guarantees would require server-side preview storage or re-running the improvement.
        try:
            _validate_confirm_payload(_get_original_resume_data(resume), improved_data)
        except ValueError as e:
            logger.warning("Resume confirm rejected: %s", e)
            raise HTTPException(
                status_code=400,
                detail="Invalid improved resume data. Please retry preview.",
            )
        preview_hashes = job.get("preview_hashes")
        allowed_hashes: set[str] = set()
        if isinstance(preview_hashes, dict):
            allowed_hashes.update(preview_hashes.values())
        elif isinstance(preview_hashes, list):
            allowed_hashes.update(
                [value for value in preview_hashes if isinstance(value, str)]
            )
        else:
            preview_hash = job.get("preview_hash")
            if isinstance(preview_hash, str):
                allowed_hashes.add(preview_hash)

        if not allowed_hashes:
            logger.warning(
                "Rejecting confirm; preview hash missing for job %s.",
                request.job_id,
            )
            raise HTTPException(
                status_code=400,
                detail="Preview required before confirmation. Please retry preview.",
            )

        request_hash = _hash_improved_data(improved_data)
        if request_hash not in allowed_hashes:
            logger.warning("Resume confirm rejected due to preview hash mismatch.")
            raise HTTPException(
                status_code=400,
                detail="Invalid improved resume data. Please retry preview.",
            )

        stage = "calculate_diff"
        response_warnings: list[str] = []
        diff_summary, detailed_changes, diff_error = _calculate_diff_from_resume(
            resume,
            improved_data,
        )
        if diff_error:
            response_warnings.append(f"Could not calculate changes: {diff_error}")

        stage = "generate_auxiliary_messages"
        (
            cover_letter,
            outreach_message,
            title,
            interview_prep,
            aux_warnings,
        ) = await _generate_auxiliary_messages(
            improved_data,
            job["content"],
            language,
            enable_cover_letter,
            enable_outreach,
            enable_interview_prep,
            # Reuse the keywords persisted on the job during preview so the title
            # is composed deterministically (no extra LLM round-trip on confirm).
            job_keywords=job.get("job_keywords"),
        )
        response_warnings.extend(aux_warnings)

        stage = "create_resume"
        tailored_resume = await db.create_resume(
            user_id,
            content=improved_text,
            content_type="json",
            filename=f"tailored_{resume.get('filename', 'resume')}",
            is_master=False,
            parent_id=request.resume_id,
            processed_data=improved_data,
            processing_status="ready",
            cover_letter=cover_letter,
            outreach_message=outreach_message,
            interview_prep=_serialize_interview_prep(interview_prep),
            title=title,
            # Preserve the source resume's appearance on the tailored copy so a
            # tailored resume opens in the same template (Phase 5). The user can
            # still switch it in the editor afterwards.
            template_settings=resume.get("template_settings"),
        )

        improvements_payload = [imp.model_dump() for imp in request.improvements]
        stage = "create_improvement"
        request_id = str(uuid4())
        await db.create_improvement(
            user_id,
            original_resume_id=request.resume_id,
            tailored_resume_id=tailored_resume["resume_id"],
            job_id=request.job_id,
            improvements=improvements_payload,
        )

        # Capture the accepted AI generation as an ``ai`` snapshot (R1.1) and
        # emit the done event (-> notification, decoupled from this request).
        await _capture_version(
            user_id, tailored_resume["resume_id"], improved_data, "ai"
        )
        from app.events import EventType

        await _emit_event(
            EventType.AI_GENERATION_DONE,
            {"resume_id": tailored_resume["resume_id"]},
            user_id=user_id,
        )

        await _auto_create_tracker_application(
            user_id=user_id,
            job_id=request.job_id,
            tailored_resume_id=tailored_resume["resume_id"],
            master_resume_id=request.resume_id,
            job=job,
            title=title,
        )

        # --- Feature usage metric (daily aggregate, fire-and-forget) ---
        try:
            from datetime import datetime, timezone
            from app.admin.metric_store import get_metric_store
            from app.admin.metric_registry import FEAT_TAILOR
            await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_TAILOR, 1)
        except Exception:
            pass  # metrics never break user operations

        # --- Resume source metric (daily aggregate, fire-and-forget) ---
        try:
            from datetime import datetime, timezone
            from app.admin.metric_store import get_metric_store
            from app.admin.metric_registry import RESUMES_TAILORED
            await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), RESUMES_TAILORED, 1)
        except Exception:
            pass  # metrics never break user operations

        return ImproveResumeResponse(
            request_id=request_id,
            data=ImproveResumeData(
                request_id=request_id,
                resume_id=tailored_resume["resume_id"],
                job_id=request.job_id,
                resume_preview=request.improved_data,
                improvements=request.improvements,
                markdownOriginal=resume["content"],
                markdownImproved=improved_text,
                cover_letter=cover_letter,
                outreach_message=outreach_message,
                interview_prep=interview_prep,
                diff_summary=diff_summary,
                detailed_changes=detailed_changes,
                warnings=response_warnings,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        _raise_improve_error("confirm", stage, e, detail)


@router.post(
    "/improve",
    response_model=ImproveResumeResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def improve_resume_endpoint(
    request: ImproveResumeRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ImproveResumeResponse:
    """Improve/tailor a resume for a specific job description.

    Uses LLM to analyze the job and generate an optimized resume version
    with improvement suggestions. Also generates cover letter and outreach
    message if enabled in feature configuration.
    Persists the tailored resume and returns a non-null resume_id.
    """
    # Fetch resume
    resume = await db.get_resume(user_id, request.resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Fetch job description
    job = await db.get_job(user_id, request.job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job description not found")

    # Load feature configuration and content language
    feature_config = _load_config()
    enable_cover_letter = feature_config.get("enable_cover_letter", False)
    enable_outreach = feature_config.get("enable_outreach_message", False)
    enable_interview_prep = feature_config.get("enable_interview_prep", False)
    language = get_content_language()

    try:
        # Extract keywords from job description
        job_keywords = await extract_job_keywords(job["content"])

        # Generate improved resume in the configured language
        prompt_id = request.prompt_id or _get_default_prompt_id()

        original_resume_data = _get_original_resume_data(resume)
        # Collect warnings throughout the process
        response_warnings: list[str] = []

        # Diff-based improvement: generate targeted changes, apply with verification
        if original_resume_data:
            diff_result = await generate_resume_diffs(
                original_resume=resume["content"],
                job_description=job["content"],
                job_keywords=job_keywords,
                language=language,
                prompt_id=prompt_id,
                original_resume_data=original_resume_data,
            )

            improved_data, applied_changes, rejected_changes = apply_diffs(
                original=original_resume_data,
                changes=diff_result.changes,
            )

            diff_warnings = verify_diff_result(
                original=original_resume_data,
                result=improved_data,
                applied_changes=applied_changes,
                job_keywords=job_keywords,
            )
            response_warnings.extend(diff_warnings)

            if rejected_changes:
                response_warnings.append(
                    f"{len(rejected_changes)} change(s) rejected during verification"
                )

            logger.info(
                "Diff-based improve (legacy): %d applied, %d rejected, %d warnings",
                len(applied_changes),
                len(rejected_changes),
                len(diff_warnings),
            )
        else:
            # Fallback to full-output mode when no structured data available
            improved_data = await improve_resume(
                original_resume=resume["content"],
                job_description=job["content"],
                job_keywords=job_keywords,
                language=language,
                prompt_id=prompt_id,
                original_resume_data=original_resume_data,
            )

        # Safety nets (defense in depth)
        improved_data, preserve_warnings = _preserve_personal_info(
            original_resume_data,
            improved_data,
        )
        response_warnings.extend(preserve_warnings)

        improved_data = _restore_original_dates(original_resume_data, improved_data)
        original_markdown = _get_original_markdown(resume)
        if original_markdown:
            improved_data = restore_dates_from_markdown(improved_data, original_markdown)
        improved_data = _preserve_original_skills(original_resume_data, improved_data)
        improved_data = _protect_custom_sections(original_resume_data, improved_data)

        # Multi-pass refinement: keyword injection, AI phrase removal, alignment validation
        refinement_stats: RefinementStats | None = None
        refinement_result = None
        refinement_attempted = False
        refinement_successful = False
        try:
            # Get master resume for alignment validation
            master_resume = await db.get_master_resume(user_id)
            master_data = (
                _get_original_resume_data(master_resume)
                if master_resume
                else _get_original_resume_data(resume)
            )
            if master_data:
                initial_match = calculate_keyword_match(improved_data, job_keywords)
                refinement_attempted = True
                refinement_result = await refine_resume(
                    initial_tailored=improved_data,
                    master_resume=master_data,
                    job_description=job["content"],
                    job_keywords=job_keywords,
                    config=RefinementConfig(),
                )
                improved_data = refinement_result.refined_data
                refinement_stats = RefinementStats(
                    passes_completed=refinement_result.passes_completed,
                    keywords_injected=(
                        len(refinement_result.keyword_analysis.injectable_keywords)
                        if refinement_result.keyword_analysis
                        else 0
                    ),
                    ai_phrases_removed=refinement_result.ai_phrases_removed,
                    alignment_violations_fixed=(
                        len(
                            [
                                v
                                for v in refinement_result.alignment_report.violations
                                if v.severity == "critical"
                            ]
                        )
                        if refinement_result.alignment_report
                        else 0
                    ),
                    initial_match_percentage=initial_match,
                    final_match_percentage=refinement_result.final_match_percentage,
                )
                refinement_successful = True
                logger.info(
                    "Refinement completed: %d passes, %d AI phrases removed",
                    refinement_result.passes_completed,
                    len(refinement_result.ai_phrases_removed),
                )
        except Exception as e:
            logger.warning("Refinement failed, using unrefined result: %s", e)
            if refinement_attempted:
                response_warnings.append(f"Refinement failed: {str(e)}")

        # Convert improved data to JSON string for storage
        improved_text = json.dumps(improved_data, indent=2)

        # Calculate differences between original and improved resume
        diff_summary, detailed_changes, diff_error = _calculate_diff_from_resume(
            resume,
            improved_data,
        )
        if diff_error:
            response_warnings.append(f"Could not calculate changes: {diff_error}")

        # Generate improvement suggestions
        improvements = generate_improvements(job_keywords)

        # Generate cover letter, outreach message, and title in parallel if enabled
        (
            cover_letter,
            outreach_message,
            title,
            interview_prep,
            aux_warnings,
        ) = await _generate_auxiliary_messages(
            improved_data,
            job["content"],
            language,
            enable_cover_letter,
            enable_outreach,
            enable_interview_prep,
            job_keywords=job_keywords,
        )
        response_warnings.extend(aux_warnings)

        # Store the tailored resume with cover letter, outreach message, and title
        tailored_resume = await db.create_resume(
            user_id,
            content=improved_text,
            content_type="json",
            filename=f"tailored_{resume.get('filename', 'resume')}",
            is_master=False,
            parent_id=request.resume_id,
            processed_data=improved_data,
            processing_status="ready",
            cover_letter=cover_letter,
            outreach_message=outreach_message,
            interview_prep=_serialize_interview_prep(interview_prep),
            title=title,
        )

        # Store improvement record
        request_id = str(uuid4())
        await db.create_improvement(
            user_id,
            original_resume_id=request.resume_id,
            tailored_resume_id=tailored_resume["resume_id"],
            job_id=request.job_id,
            improvements=improvements,
        )

        # Capture the accepted AI generation as an ``ai`` snapshot (R1.1) and
        # emit the done event (-> notification, decoupled from this request).
        await _capture_version(
            user_id, tailored_resume["resume_id"], improved_data, "ai"
        )
        from app.events import EventType

        await _emit_event(
            EventType.AI_GENERATION_DONE,
            {"resume_id": tailored_resume["resume_id"]},
            user_id=user_id,
        )

        await _auto_create_tracker_application(
            user_id=user_id,
            job_id=request.job_id,
            tailored_resume_id=tailored_resume["resume_id"],
            master_resume_id=request.resume_id,
            job=job,
            title=title,
        )

        # --- Feature usage metric (daily aggregate, fire-and-forget) ---
        try:
            from datetime import datetime, timezone
            from app.admin.metric_store import get_metric_store
            from app.admin.metric_registry import FEAT_TAILOR
            await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_TAILOR, 1)
        except Exception:
            pass  # metrics never break user operations

        # --- Resume source metric (daily aggregate, fire-and-forget) ---
        try:
            from datetime import datetime, timezone
            from app.admin.metric_store import get_metric_store
            from app.admin.metric_registry import RESUMES_TAILORED
            await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), RESUMES_TAILORED, 1)
        except Exception:
            pass  # metrics never break user operations

        return ImproveResumeResponse(
            request_id=request_id,
            data=ImproveResumeData(
                request_id=request_id,
                resume_id=tailored_resume["resume_id"],
                job_id=request.job_id,
                resume_preview=ResumeData.model_validate(improved_data),
                improvements=[
                    {
                        "suggestion": imp["suggestion"],
                        "lineNumber": imp.get("lineNumber"),
                    }
                    for imp in improvements
                ],
                markdownOriginal=resume["content"],
                markdownImproved=improved_text,
                cover_letter=cover_letter,
                outreach_message=outreach_message,
                interview_prep=interview_prep,
                # Diff metadata
                diff_summary=diff_summary,
                detailed_changes=detailed_changes,
                refinement_stats=refinement_stats,
                ats_score=_build_ats_score(
                    improved_data,
                    job_keywords,
                    refinement_result,
                    refinement_successful,
                ),
                warnings=response_warnings,
                refinement_attempted=refinement_attempted,
                refinement_successful=refinement_successful,
            ),
        )

    except Exception as e:
        logger.error(f"Resume improvement failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to improve resume. Please try again.",
        )


def _parse_if_match(if_match: str | None) -> int | None:
    """Parse an ``If-Match`` header value into a base version, or ``None``.

    Accepts a bare integer (``42``) or a quoted ETag-style value (``"42"``) so
    the client may use standard ETag semantics. Non-integer / malformed values
    are treated as absent (a normal, non-CAS write) rather than erroring, so a
    stray header never blocks a save.
    """
    if if_match is None:
        return None
    value = if_match.strip().strip('"').strip()
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resume_fetch_response(resume_id: str, updated: dict[str, Any]) -> ResumeFetchResponse:
    """Build the standard resume-fetch response from a facade resume dict."""
    raw_resume = RawResume(
        id=None,
        content=updated["content"],
        content_type=updated["content_type"],
        created_at=updated["created_at"],
        processing_status=updated.get("processing_status", "pending"),
    )
    processed_resume = (
        ResumeData.model_validate(updated.get("processed_data"))
        if updated.get("processed_data")
        else None
    )
    return ResumeFetchResponse(
        request_id=str(uuid4()),
        data=ResumeFetchData(
            resume_id=resume_id,
            raw_resume=raw_resume,
            processed_resume=processed_resume,
            cover_letter=updated.get("cover_letter"),
            outreach_message=updated.get("outreach_message"),
            interview_prep=_parse_interview_prep(
                updated.get("interview_prep"),
                resume_id=resume_id,
            ),
            parent_id=updated.get("parent_id"),
            title=updated.get("title"),
            version=updated.get("version"),
        ),
    )


@router.patch("/{resume_id}", response_model=ResumeFetchResponse)
async def update_resume_endpoint(
    resume_id: str,
    resume_data: ResumeData,
    user_id: str = Depends(get_effective_user_id),
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ResumeFetchResponse:
    """Update a resume with new structured data (P4 version CAS - R3.1/3.4).

    Optimistic concurrency: when an ``If-Match: <version>`` header is present the
    write is applied atomically **only if** the stored ``version`` still equals
    that base (Property 1). A stale base returns **409** with the ADR-7 envelope
    carrying ``{your_base_version, current_version, current_data}`` so the client
    can drive the explicit conflict flow (keep-mine / take-latest / field-merge);
    the server never silently overwrites a newer version (R3.3).

    Idempotent retries: an ``Idempotency-Key`` header lets a retried autosave (the
    client couldn't tell whether the first attempt landed) dedupe server-side -
    an identical replayed request returns the cached result rather than applying
    the write twice (R4.2, Property 4). When neither header is present the write
    is a normal (non-CAS) update - preserving the pre-P4 client contract - but it
    still bumps ``version`` so other tabs observe a fresh token.
    """
    updated_data = resume_data.model_dump()
    updated_content = json.dumps(updated_data, indent=2)
    base_version = _parse_if_match(if_match)

    # Fingerprint pins an idempotency key to *this* operation so a key reused for
    # different content is treated as a new write, not a false replay hit.
    idem = get_idempotency_cache()
    fingerprint = hashlib.sha256(
        f"{resume_id}:{base_version}:{updated_content}".encode("utf-8")
    ).hexdigest()
    if idempotency_key:
        cached = await idem.get(user_id, idempotency_key)
        if cached is not None and cached.fingerprint == fingerprint:
            # Replay of an already-applied save - return the stored result
            # without touching the database (dedupe).
            return ResumeFetchResponse.model_validate(cached.result)

    updates = {
        "content": updated_content,
        "content_type": "json",
        "processed_data": updated_data,
        "processing_status": "ready",
    }

    if base_version is not None:
        status, resume = await db.update_resume_cas(
            user_id, resume_id, updates, base_version=base_version
        )
        if status == "not_found":
            raise HTTPException(status_code=404, detail="Resume not found")
        if status == "conflict":
            assert resume is not None
            raise ApiError(
                status_code=409,
                code="version_conflict",
                message="This resume was changed elsewhere; resolve the conflict to continue.",
                details={
                    "your_base_version": base_version,
                    "current_version": resume.get("version"),
                    "current_data": resume.get("processed_data"),
                },
                headers={"ETag": str(resume.get("version"))},
            )
        updated = resume
    else:
        existing = await db.get_resume(user_id, resume_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Resume not found")
        updated = await db.update_resume(user_id, resume_id, updates)

    if not updated:
        raise HTTPException(status_code=500, detail="Failed to update resume")

    # Capture a debounced/deduped ``manual`` snapshot of the saved state (R1.1/1.2).
    await _capture_version(user_id, resume_id, updated_data, "manual")

    response = _resume_fetch_response(resume_id, updated)

    if idempotency_key:
        await idem.store(
            user_id,
            idempotency_key,
            fingerprint=fingerprint,
            result=response.model_dump(mode="json"),
        )

    return response


@router.get("/{resume_id}/pdf")
async def download_resume_pdf(
    resume_id: str,
    template: str | None = Query(None),
    pageSize: str | None = Query(None, pattern="^(A4|LETTER)$"),
    marginTop: int | None = Query(None, ge=5, le=25),
    marginBottom: int | None = Query(None, ge=5, le=25),
    marginLeft: int | None = Query(None, ge=5, le=25),
    marginRight: int | None = Query(None, ge=5, le=25),
    sectionSpacing: int | None = Query(None, ge=1, le=5),
    itemSpacing: int | None = Query(None, ge=1, le=5),
    lineHeight: int | None = Query(None, ge=1, le=5),
    fontSize: int | None = Query(None, ge=1, le=5),
    headerScale: int | None = Query(None, ge=1, le=5),
    headerFont: str | None = Query(None, pattern="^(serif|sans-serif|mono)$"),
    bodyFont: str | None = Query(None, pattern="^(serif|sans-serif|mono)$"),
    compactMode: bool | None = Query(None),
    showContactIcons: bool | None = Query(None),
    accentColor: str | None = Query(None, pattern="^(blue|green|orange|red)$"),
    lang: str | None = Query(None, pattern="^[a-z]{2}(-[A-Z]{2})?$"),
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    """Generate a PDF for a resume using headless Chromium.

    Appearance resolution (WYSIWYG guarantee): each setting is taken from the
    query param when supplied, else from the resume's PERSISTED
    ``template_settings``, else the documented default. So a bare
    ``GET /resumes/{id}/pdf`` renders in the resume's own stored template - the
    export matches the editor preview even for non-editor callers - while the
    editor can still override any setting via query params.

    Settings: template (swiss-single|swiss-two-column|modern|modern-two-column|
    latex|clean|vivid), pageSize (A4|LETTER), margin{Top,Bottom,Left,Right} mm
    (5-25), sectionSpacing/itemSpacing/lineHeight/fontSize/headerScale (1-5),
    headerFont/bodyFont (serif|sans-serif|mono), compactMode, showContactIcons,
    accentColor (blue|green|orange|red), lang (print-page locale).
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    overrides = {
        "template": template,
        "pageSize": pageSize,
        "marginTop": marginTop,
        "marginBottom": marginBottom,
        "marginLeft": marginLeft,
        "marginRight": marginRight,
        "sectionSpacing": sectionSpacing,
        "itemSpacing": itemSpacing,
        "lineHeight": lineHeight,
        "fontSize": fontSize,
        "headerScale": headerScale,
        "headerFont": headerFont,
        "bodyFont": bodyFont,
        "compactMode": compactMode,
        "showContactIcons": showContactIcons,
        "accentColor": accentColor,
    }
    s = _resolve_pdf_settings(resume.get("template_settings"), overrides)

    # Build print URL from the RESOLVED settings so the headless render uses the
    # stored template even when the caller passed no params.
    params = (
        f"template={s['template']}"
        f"&pageSize={s['pageSize']}"
        f"&marginTop={s['marginTop']}"
        f"&marginBottom={s['marginBottom']}"
        f"&marginLeft={s['marginLeft']}"
        f"&marginRight={s['marginRight']}"
        f"&sectionSpacing={s['sectionSpacing']}"
        f"&itemSpacing={s['itemSpacing']}"
        f"&lineHeight={s['lineHeight']}"
        f"&fontSize={s['fontSize']}"
        f"&headerScale={s['headerScale']}"
        f"&headerFont={s['headerFont']}"
        f"&bodyFont={s['bodyFont']}"
        f"&compactMode={str(s['compactMode']).lower()}"
        f"&showContactIcons={str(s['showContactIcons']).lower()}"
        f"&accentColor={s['accentColor']}"
    )
    if lang:
        params = f"{params}&lang={lang}"
    # Mint a short-lived signed print token so the headless render can load the
    # resume in hosted mode (the browser has no user session cookie).
    from app.pdf_token import make_print_token
    print_token = make_print_token(user_id, resume_id)
    params = f"{params}&print_token={quote(print_token, safe='')}"
    url = f"{settings.frontend_base_url}/print/resumes/{resume_id}?{params}"

    # Use the resolved margins; compact mode only affects spacing.
    pdf_margins = {
        "top": s["marginTop"],
        "right": s["marginRight"],
        "bottom": s["marginBottom"],
        "left": s["marginLeft"],
    }

    # Render PDF with margins applied to every page
    try:
        pdf_bytes = await render_resume_pdf(url, pageSize, margins=pdf_margins)
    except PDFRenderError as e:
        raise HTTPException(status_code=503, detail=str(e))

    headers = {"Content-Disposition": f'attachment; filename="resume_{resume_id}.pdf"'}
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.delete("/{resume_id}")
async def delete_resume(
    resume_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Delete a resume by ID."""
    if not await db.delete_resume(user_id, resume_id):
        raise HTTPException(status_code=404, detail="Resume not found")

    # --- Resume source metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import RESUMES_DELETED
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), RESUMES_DELETED, 1)
    except Exception:
        pass  # metrics never break user operations

    return {"message": "Resume deleted successfully"}


@router.post(
    "/{resume_id}/retry-processing",
    response_model=ResumeUploadResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def retry_processing(
    resume_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> ResumeUploadResponse:
    """Retry AI processing for a failed or stuck resume.

    Re-runs parse_resume_to_json() on the stored markdown content.
    Works for resumes with processing_status == "failed" or "processing".
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    if resume.get("processing_status") not in ("failed", "processing"):
        raise HTTPException(
            status_code=400,
            detail="Only resumes with 'failed' or 'processing' status can be retried.",
        )

    markdown_content = resume.get("content", "")
    if not markdown_content:
        raise HTTPException(
            status_code=400,
            detail="Resume has no stored content to re-process.",
        )

    try:
        processed_data = await _parse_resume_cached(user_id, markdown_content)
        await db.update_resume(
            user_id,
            resume_id,
            {
                "processed_data": processed_data,
                "processing_status": "ready",
            },
        )
        return ResumeUploadResponse(
            message="Resume processing succeeded on retry",
            request_id=str(uuid4()),
            resume_id=resume_id,
            processing_status="ready",
            is_master=resume.get("is_master", False),
        )
    except Exception as e:
        logger.warning(f"Retry processing failed for resume {resume_id}: {e}")
        await db.update_resume(user_id, resume_id, {"processing_status": "failed"})
        return ResumeUploadResponse(
            message="Retry processing failed",
            request_id=str(uuid4()),
            resume_id=resume_id,
            processing_status="failed",
            is_master=resume.get("is_master", False),
        )


@router.patch("/{resume_id}/cover-letter")
async def update_cover_letter(
    resume_id: str,
    request: UpdateCoverLetterRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Update the cover letter for a resume."""
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    await db.update_resume(user_id, resume_id, {"cover_letter": request.content})
    return {"message": "Cover letter updated successfully"}


@router.patch("/{resume_id}/outreach-message")
async def update_outreach_message(
    resume_id: str,
    request: UpdateOutreachMessageRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Update the outreach message for a resume."""
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    await db.update_resume(user_id, resume_id, {"outreach_message": request.content})
    return {"message": "Outreach message updated successfully"}


@router.patch("/{resume_id}/title")
async def update_title(
    resume_id: str,
    request: UpdateTitleRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Update the title for a resume."""
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    title = request.title.strip()[:80]
    await db.update_resume(user_id, resume_id, {"title": title})
    return {"message": "Title updated successfully"}


@router.patch("/{resume_id}/template-settings")
async def update_template_settings(
    resume_id: str,
    request: UpdateTemplateSettingsRequest,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Persist a resume's appearance (chosen template + customization).

    This is a rendering artifact, not resume content, so it does NOT bump the
    optimistic-concurrency ``version`` (see ``db.update_resume``) - persisting a
    template change never conflicts with an in-flight content edit.
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    await db.update_resume(user_id, resume_id, {"template_settings": request.settings})
    return {"message": "Template settings updated successfully"}


@router.post("/from-data", response_model=ResumeUploadResponse)
async def create_resume_from_data(
    request: CreateResumeFromDataRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ResumeUploadResponse:
    """Create a new resume from structured data (Sample Library "Use", duplicate).

    Persists ready ``ResumeData`` (validated/normalized) as a brand-new resume
    with the given title + appearance. Never mutates an existing resume. Honors
    the single-master invariant via the atomic master path when ``as_master``.
    """
    normalized = normalize_resume_data(request.processed_data.model_dump())
    content = json.dumps(normalized, indent=2)
    title = (request.title or "").strip()[:80] or None

    if request.as_master:
        created = await db.create_resume_atomic_master(
            user_id,
            content=content,
            content_type="json",
            processed_data=normalized,
            processing_status="ready",
            title=title,
            template_settings=request.template_settings,
        )
    else:
        created = await db.create_resume(
            user_id,
            content=content,
            content_type="json",
            processed_data=normalized,
            processing_status="ready",
            title=title,
            template_settings=request.template_settings,
        )

    # Capture the initial state as the retained ``original`` snapshot (R1.1) and
    # emit the parsed event (decoupled -> search index / notifications).
    await _capture_version(user_id, created["resume_id"], normalized, "original")
    from app.events import EventType

    await _emit_event(
        EventType.RESUME_UPSERTED, {"resume_id": created["resume_id"]}, user_id=user_id
    )

    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_BUILDER
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_BUILDER, 1)
    except Exception:
        pass  # metrics never break user operations

    # --- Resume source metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import RESUMES_GENERATED
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), RESUMES_GENERATED, 1)
    except Exception:
        pass  # metrics never break user operations

    return ResumeUploadResponse(
        message="Resume created",
        request_id=str(uuid4()),
        resume_id=created["resume_id"],
        processing_status="ready",
        is_master=created.get("is_master", False),
    )


@router.post(
    "/{resume_id}/generate-cover-letter",
    response_model=GenerateContentResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def generate_cover_letter_endpoint(
    resume_id: str,
    regenerate: bool = False,
    user_id: str = Depends(require_verified_user_id),
) -> GenerateContentResponse:
    """Generate a cover letter on-demand for an existing tailored resume.

    This endpoint allows users to generate a cover letter after a resume has been
    tailored, without needing to re-tailor the entire resume. It requires:
    - The resume must be a tailored resume (has parent_id)
    - The resume must have an associated job context in the improvements table

    Persistent reuse: a previously generated cover letter is returned as-is
    unless ``regenerate=true``, so opening the resume again (or after a refresh)
    never spends another LLM call reproducing content we already stored.
    """
    # Get the resume
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Reuse the stored copy unless the user explicitly asks to regenerate.
    existing = resume.get("cover_letter")
    if existing and not regenerate:
        return GenerateContentResponse(
            content=existing,
            message="Loaded your saved cover letter",
        )

    # Check if it's a tailored resume (has parent_id)
    if not resume.get("parent_id"):
        raise HTTPException(
            status_code=400,
            detail="Cover letter can only be generated for tailored resumes. "
            "Please tailor this resume to a job description first.",
        )

    # Get improvement record to find the job_id
    improvement = await db.get_improvement_by_tailored_resume(user_id, resume_id)
    if not improvement:
        raise HTTPException(
            status_code=400,
            detail="No job context found for this resume. "
            "The resume may have been created before job tracking was implemented.",
        )

    # Get the job description
    job = await db.get_job(user_id, improvement["job_id"])
    if not job:
        raise HTTPException(
            status_code=404,
            detail="The associated job description was not found.",
        )

    # Get resume data
    resume_data = resume.get("processed_data")
    if not resume_data:
        raise HTTPException(
            status_code=400,
            detail="Resume has no processed data. Please re-upload the resume.",
        )

    # Get language setting
    language = get_content_language()

    # Generate cover letter
    try:
        cover_letter_content = await generate_cover_letter(
            resume_data, job["content"], language
        )
    except Exception as e:
        logger.error(f"Cover letter generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate cover letter. Please try again.",
        )

    # Save to resume record
    await db.update_resume(user_id, resume_id, {"cover_letter": cover_letter_content})

    # --- Feature usage metric (daily aggregate, fire-and-forget) ---
    try:
        from datetime import datetime, timezone
        from app.admin.metric_store import get_metric_store
        from app.admin.metric_registry import FEAT_COVER_LETTER
        await get_metric_store().add(datetime.now(timezone.utc).strftime("%Y-%m-%d"), FEAT_COVER_LETTER, 1)
    except Exception:
        pass  # metrics never break user operations

    return GenerateContentResponse(
        content=cover_letter_content,
        message="Cover letter generated successfully",
    )


@router.post(
    "/{resume_id}/generate-outreach",
    response_model=GenerateContentResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def generate_outreach_endpoint(
    resume_id: str,
    regenerate: bool = False,
    user_id: str = Depends(require_verified_user_id),
) -> GenerateContentResponse:
    """Generate an outreach message on-demand for an existing tailored resume.

    This endpoint allows users to generate a cold outreach message after a resume
    has been tailored. It requires:
    - The resume must be a tailored resume (has parent_id)
    - The resume must have an associated job context in the improvements table

    Persistent reuse: a previously generated outreach message is returned as-is
    unless ``regenerate=true`` (no wasted LLM call on reopen/refresh).
    """
    # Get the resume
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Reuse the stored copy unless the user explicitly asks to regenerate.
    existing = resume.get("outreach_message")
    if existing and not regenerate:
        return GenerateContentResponse(
            content=existing,
            message="Loaded your saved outreach message",
        )

    # Check if it's a tailored resume (has parent_id)
    if not resume.get("parent_id"):
        raise HTTPException(
            status_code=400,
            detail="Outreach message can only be generated for tailored resumes. "
            "Please tailor this resume to a job description first.",
        )

    # Get improvement record to find the job_id
    improvement = await db.get_improvement_by_tailored_resume(user_id, resume_id)
    if not improvement:
        raise HTTPException(
            status_code=400,
            detail="No job context found for this resume. "
            "The resume may have been created before job tracking was implemented.",
        )

    # Get the job description
    job = await db.get_job(user_id, improvement["job_id"])
    if not job:
        raise HTTPException(
            status_code=404,
            detail="The associated job description was not found.",
        )

    # Get resume data
    resume_data = resume.get("processed_data")
    if not resume_data:
        raise HTTPException(
            status_code=400,
            detail="Resume has no processed data. Please re-upload the resume.",
        )

    # Get language setting
    language = get_content_language()

    # Generate outreach message
    try:
        outreach_content = await generate_outreach_message(
            resume_data, job["content"], language
        )
    except Exception as e:
        logger.error(f"Outreach message generation failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="Failed to generate outreach message. Please try again.",
        )

    # Save to resume record
    await db.update_resume(user_id, resume_id, {"outreach_message": outreach_content})

    return GenerateContentResponse(
        content=outreach_content,
        message="Outreach message generated successfully",
    )


@router.post(
    "/{resume_id}/generate-interview-prep",
    response_model=GenerateInterviewPrepResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def generate_interview_prep_endpoint(
    resume_id: str,
    regenerate: bool = False,
    user_id: str = Depends(require_verified_user_id),
) -> GenerateInterviewPrepResponse:
    """Generate interview preparation on-demand for an existing tailored resume.

    Persistent reuse: previously generated interview prep is returned as-is
    unless ``regenerate=true`` (no wasted LLM call on reopen/refresh).
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Reuse the stored copy unless the user explicitly asks to regenerate.
    existing_prep = _parse_interview_prep(resume.get("interview_prep"), resume_id=resume_id)
    if existing_prep is not None and not regenerate:
        return GenerateInterviewPrepResponse(
            interview_prep=existing_prep,
            message="Loaded your saved interview preparation",
        )

    if not resume.get("parent_id"):
        raise HTTPException(
            status_code=400,
            detail="Interview preparation can only be generated for tailored resumes. "
            "Please tailor this resume to a job description first.",
        )

    improvement = await db.get_improvement_by_tailored_resume(user_id, resume_id)
    if not improvement:
        raise HTTPException(
            status_code=400,
            detail="No job context found for this resume. "
            "The resume may have been created before job tracking was implemented.",
        )

    job = await db.get_job(user_id, improvement["job_id"])
    if not job:
        raise HTTPException(
            status_code=404,
            detail="The associated job description was not found.",
        )

    resume_data = resume.get("processed_data")
    if not resume_data:
        raise HTTPException(
            status_code=400,
            detail="Resume has no processed data. Please re-upload the resume.",
        )

    language = get_content_language()

    try:
        interview_prep = await generate_interview_prep(
            resume_data,
            job["content"],
            language,
        )
    except Exception as e:
        logger.exception("Interview preparation generation failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Failed to generate interview preparation. Please try again.",
        )

    await db.update_resume(
        user_id,
        resume_id,
        {"interview_prep": _serialize_interview_prep(interview_prep)},
    )

    return GenerateInterviewPrepResponse(
        interview_prep=interview_prep,
        message="Interview preparation generated successfully",
    )


# ---------------------------------------------------------------------------
# Streaming AI (P4 Resilience - R1). SSE relay of LiteLLM chunks with a
# cross-worker task registry (cap + cancel + reap) and transparent fallback.
# ---------------------------------------------------------------------------

# Rate rule for stream *starts* (ADR-8) - blunts cancel-abuse / task exhaustion
# on top of the per-user concurrent-stream cap.
_STREAM_START_RULE = RateLimitRule(limit=20, window_seconds=60)

# Supported streaming generation kinds -> (prompt builder, system prompt).
_STREAM_KINDS = {
    "cover-letter": (build_cover_letter_prompt, COVER_LETTER_SYSTEM_PROMPT, 2048),
    "outreach": (build_outreach_prompt, OUTREACH_SYSTEM_PROMPT, 1024),
}


async def _load_ai_generation_context(
    user_id: str, resume_id: str
) -> tuple[dict[str, Any], str, str]:
    """Load (resume_data, job_content, language) for an AI generation.

    Shared by the streaming and non-streaming generation paths so the ownership,
    tailored-resume, and job-context checks never drift. Raises ``HTTPException``
    with the same status/detail the non-stream endpoints use.
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    if not resume.get("parent_id"):
        raise HTTPException(
            status_code=400,
            detail="This can only be generated for tailored resumes. "
            "Please tailor this resume to a job description first.",
        )
    improvement = await db.get_improvement_by_tailored_resume(user_id, resume_id)
    if not improvement:
        raise HTTPException(
            status_code=400,
            detail="No job context found for this resume.",
        )
    job = await db.get_job(user_id, improvement["job_id"])
    if not job:
        raise HTTPException(
            status_code=404, detail="The associated job description was not found."
        )
    resume_data = resume.get("processed_data")
    if not resume_data:
        raise HTTPException(
            status_code=400,
            detail="Resume has no processed data. Please re-upload the resume.",
        )
    return resume_data, job["content"], get_content_language()


def _sse(event: str, data: dict[str, Any]) -> str:
    """Frame a Server-Sent Event: ``event: <name>\\ndata: <json>\\n\\n``."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/{resume_id}/{kind}/stream")
async def stream_generation_endpoint(
    resume_id: str,
    kind: str,
    request: Request,
    request_id: str = Query(default="", max_length=100),
    user_id: str = Depends(require_verified_user_id),
) -> StreamingResponse:
    """Stream an AI generation (cover letter / outreach) as SSE (R1.1-R1.6).

    Events: ``token`` (delta), ``heartbeat`` (liveness/keep-warm), ``done`` (final
    text + token usage for cost accounting), ``error`` (terminal -> client falls
    back to the non-stream path). Cancellation (client close, explicit
    ``/cancel``, or lifetime/heartbeat reaping) aborts the provider call and
    persists nothing - streamed text is a preview until explicit accept (R1.4).
    """
    if kind not in _STREAM_KINDS:
        raise HTTPException(status_code=404, detail="Unknown streaming kind")
    if len(request_id) < 8:
        raise HTTPException(status_code=422, detail="request_id must be at least 8 chars")

    # Flag gate (R6.4 / ADR-14): off -> the client transparently uses the
    # non-stream path. Signalled as a typed 409 so `useStream` falls back.
    if not settings.streaming_ai_enabled:
        raise ApiError(
            status_code=409, code="streaming_disabled",
            message="Streaming is disabled; use the standard generation.",
        )

    # Capability probe (R1.3): a provider that can't stream -> fall back.
    if not provider_supports_streaming():
        raise ApiError(
            status_code=409, code="streaming_unsupported",
            message="The active provider does not support streaming.",
        )

    # Rate-limit stream starts (ADR-8).
    limiter = get_rate_limiter()
    rl = await limiter.check("stream", user_id, _STREAM_START_RULE, fail_closed=False)
    if not rl.allowed:
        raise ApiError(
            status_code=429, code="rate_limited",
            message="Too many streams started; please wait a moment.",
            headers={"Retry-After": str(rl.retry_after)},
        )

    # Load + validate the generation context BEFORE opening the stream so a
    # 400/404 is a normal JSON error (not an SSE error event).
    resume_data, job_content, language = await _load_ai_generation_context(
        user_id, resume_id
    )
    builder, system_prompt, max_tokens = _STREAM_KINDS[kind]
    prompt = builder(resume_data, job_content, language)

    # Enforce the per-user concurrent-stream cap (R1.5) across workers.
    registry = get_stream_registry()
    heartbeat_ttl = settings.stream_heartbeat_seconds * 4
    ok = await registry.try_register(
        user_id, request_id,
        max_concurrent=settings.stream_max_concurrent_per_user,
        heartbeat_ttl=heartbeat_ttl,
    )
    metrics = get_resilience_metrics()
    if not ok:
        metrics.stream_rejected_cap()
        raise ApiError(
            status_code=429, code="stream_limit_reached",
            message="You have too many active generations; cancel one and retry.",
            headers={"Retry-After": "5"},
        )

    max_lifetime = settings.stream_max_lifetime_seconds
    heartbeat_seconds = settings.stream_heartbeat_seconds

    async def event_gen():
        metrics.stream_started()
        start = time.monotonic()
        first = False
        last_hb = start
        result = StreamResult()

        async def cancel_check() -> bool:
            try:
                if await request.is_disconnected():
                    return True
            except Exception:  # pragma: no cover
                pass
            return await registry.is_cancelled(user_id, request_id)

        try:
            # Kick off with a heartbeat so the client (and a cold free-tier dyno)
            # sees liveness before the first token.
            yield _sse("heartbeat", {"request_id": request_id})
            async with asyncio.timeout(max_lifetime):
                async for piece in stream_complete(
                    prompt, result,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    cancel_check=cancel_check,
                ):
                    now = time.monotonic()
                    if not first:
                        metrics.record_first_token_ms((now - start) * 1000)
                        first = True
                    yield _sse("token", {"text": piece})
                    if now - last_hb >= heartbeat_seconds:
                        yield _sse("heartbeat", {"request_id": request_id})
                        await registry.heartbeat(
                            user_id, request_id, heartbeat_ttl=heartbeat_ttl
                        )
                        last_hb = now
            usage = {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            }
            metrics.record_tokens(result.usage.total_tokens)
            if result.cancelled:
                metrics.stream_cancelled()
            yield _sse("done", {
                "cancelled": result.cancelled,
                "text": result.text,
                "usage": usage,
            })
        except TimeoutError:
            # Max-lifetime reaper: bound abandoned/runaway streams (R1.5).
            metrics.stream_reaped()
            metrics.record_tokens(result.usage.total_tokens)
            yield _sse("error", {
                "code": "stream_timeout",
                "message": "The generation took too long and was stopped.",
                "text": result.text,
            })
        except asyncio.CancelledError:  # pragma: no cover - server shutdown
            raise
        except Exception:
            logger.exception("Streaming generation failed for %s", resume_id)
            metrics.stream_error()
            # Terminal error -> client transparently falls back (R1.3). Any
            # partial text is surfaced as a discardable preview.
            yield _sse("error", {
                "code": "stream_error",
                "message": "Generation failed; falling back.",
                "text": result.text,
            })
        finally:
            metrics.stream_ended()
            await registry.unregister(user_id, request_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx/render)
            "Connection": "keep-alive",
        },
    )


@router.post("/stream/{request_id}/cancel")
async def cancel_stream_endpoint(
    request_id: str,
    user_id: str = Depends(require_verified_user_id),
) -> dict[str, Any]:
    """Signal cancellation of an in-flight stream (R1.2), cross-worker.

    Sets a cancel flag the streaming loop polls between chunks; the loop aborts
    the provider call and closes. Idempotent - cancelling an already-finished or
    unknown stream is a harmless no-op.
    """
    await get_stream_registry().request_cancel(user_id, request_id)
    return {"cancelled": True, "request_id": request_id}


@router.get("/{resume_id}/job-description")
async def get_job_description_for_resume(
    resume_id: str,
    user_id: str = Depends(get_effective_user_id),
) -> dict:
    """Get the job description used to tailor this resume.

    This endpoint retrieves the original job description that was used
    to tailor a resume. Only works for tailored resumes (those with parent_id).
    """
    # Get the resume
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    # Check if it's a tailored resume (has parent_id)
    if not resume.get("parent_id"):
        raise HTTPException(
            status_code=400,
            detail="Job description is only available for tailored resumes.",
        )

    # Get improvement record to find the job_id
    improvement = await db.get_improvement_by_tailored_resume(user_id, resume_id)
    if not improvement:
        raise HTTPException(
            status_code=400,
            detail="No job context found for this resume. "
            "The resume may have been created before job tracking was implemented.",
        )

    # Get the job description
    job = await db.get_job(user_id, improvement["job_id"])
    if not job:
        raise HTTPException(
            status_code=404,
            detail="The associated job description was not found.",
        )

    return {
        "job_id": job["job_id"],
        "content": job["content"],
    }


@router.get("/{resume_id}/cover-letter/pdf")
async def download_cover_letter_pdf(
    resume_id: str,
    pageSize: str = Query("A4", pattern="^(A4|LETTER)$"),
    lang: str | None = Query(None, pattern="^[a-z]{2}(-[A-Z]{2})?$"),
    user_id: str = Depends(get_effective_user_id),
) -> Response:
    """Generate a PDF for a cover letter using headless Chromium.

    Args:
        resume_id: The ID of the resume containing the cover letter
        pageSize: A4 or LETTER
        lang: locale used for print page translations
    """
    resume = await db.get_resume(user_id, resume_id)
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    cover_letter = resume.get("cover_letter")
    if not cover_letter:
        raise HTTPException(
            status_code=404, detail="No cover letter found for this resume"
        )

    # Build print URL (same pattern as resume PDF)
    url = f"{settings.frontend_base_url}/print/cover-letter/{resume_id}?pageSize={pageSize}"
    if lang:
        url = f"{url}&lang={lang}"
    # Short-lived signed print token -> lets the headless render authenticate in
    # hosted mode (browser has no user session cookie).
    from app.pdf_token import make_print_token
    print_token = make_print_token(user_id, resume_id)
    url = f"{url}&print_token={quote(print_token, safe='')}"

    # Render PDF with cover letter selector
    try:
        pdf_bytes = await render_resume_pdf(
            url, pageSize, selector=".cover-letter-print"
        )
    except PDFRenderError as e:
        raise HTTPException(status_code=503, detail=str(e))

    headers = {
        "Content-Disposition": f'attachment; filename="cover_letter_{resume_id}.pdf"'
    }
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)
