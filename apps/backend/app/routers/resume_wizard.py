"""Resume wizard endpoints (adaptive one-question-at-a-time flow)."""

import json
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_effective_user_id, require_verified_user_id
from app.llm_ratelimit import enforce_llm_rate_limit, llm_rate_limit_dep
from app.database import db
from app.profile.service import profile_service
from app.schemas.models import ResumeData, normalize_resume_data
from app.schemas.resume_wizard import (
    ResumeWizardAssistRequest,
    ResumeWizardAssistResponse,
    ResumeWizardFinalizeRequest,
    ResumeWizardFinalizeResponse,
    ResumeWizardParsedEntry,
    ResumeWizardTurnRequest,
    ResumeWizardTurnResponse,
)
from app.services.resume_wizard import (
    RESUME_WIZARD_MAX_QUESTIONS,
    apply_back,
    apply_review,
    apply_skip,
    apply_structured,
    build_initial_wizard_state,
    build_prefilled_wizard_state,
    draft_bullets,
    parse_entries,
    run_ai_turn,
)

logger = logging.getLogger(__name__)


async def _start_state(user_id: str):
    """Return the opening wizard state, prefilled from the user's profile if one
    exists and no master resume is present yet (W-P3.2).

    Best-effort: any failure (no profile, projection error) falls back to the
    empty initial state so the wizard always opens. Prefill is skipped when a
    ready master already exists (finalize would 409 anyway).
    """
    try:
        master = await db.get_master_resume(user_id)
        if master and master.get("processing_status") == "ready":
            return build_initial_wizard_state()
        profile_row = await db.get_profile(user_id)
        if not profile_row or not profile_row.get("data"):
            return build_initial_wizard_state()

        from app.profile.projection import ProjectionEngine
        from app.profile.schemas import ProfileData

        profile = ProfileData.model_validate(profile_row["data"])
        projected = ProjectionEngine.project_resume(profile)
        resume_data = ResumeData.model_validate(normalize_resume_data(projected))
        # Only prefill when there's something worth reusing.
        if not (
            resume_data.personalInfo.name.strip()
            or resume_data.workExperience
            or resume_data.education
            or resume_data.additional.technicalSkills
        ):
            return build_initial_wizard_state()
        return build_prefilled_wizard_state(resume_data)
    except Exception as e:  # pragma: no cover - defensive; wizard must still open
        logger.warning("Resume wizard prefill failed, starting empty: %s", e)
        return build_initial_wizard_state()

router = APIRouter(prefix="/resume-wizard", tags=["Resume Wizard"])


@router.post(
    "/turn",
    response_model=ResumeWizardTurnResponse,
)
async def resume_wizard_turn(
    request: ResumeWizardTurnRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ResumeWizardTurnResponse:
    """Advance the resume wizard by one structured turn.

    Only the ``answer`` action calls the LLM (``run_ai_turn`` → ``complete_json``);
    every other action (``start``/``back``/``skip``/``review``/``structured``) is
    deterministic and free. The per-user LLM rate limit is therefore enforced
    ONLY on the ``answer`` path (below) rather than as a blanket route dependency
    — otherwise cheap/deterministic turns (including the profile-prefill ``start``
    fired on every wizard load) would spuriously burn the user's LLM budget and
    could 429 them out of real AI turns.

    ``require_verified_user_id`` still gates every action: (1) 401s an anonymous
    hosted request, (2) publishes the request-scoped api-key context var so the
    wizard's LLM call uses *this* user's encrypted provider key, and (3) gates an
    active-but-unverified account with ``403 email_verification_required`` when
    verification is enabled. In ``SINGLE_USER_MODE`` it resolves to the verified
    bootstrap owner, so local zero-config behaves exactly as before.
    """
    try:
        action = request.action
        if action == "start":
            return ResumeWizardTurnResponse(state=await _start_state(user_id))
        if action == "back":
            return ResumeWizardTurnResponse(state=apply_back(request.state))
        if action == "review":
            return ResumeWizardTurnResponse(state=apply_review(request.state))

        # Skip never touches resume_data and never needs the model to choose the
        # next question — resolve it deterministically with zero tokens (W-P0.4).
        if action == "skip":
            return ResumeWizardTurnResponse(state=apply_skip(request.state))

        # Structured sections (identity/contact/skills) merge discrete fields
        # deterministically with zero tokens (W-P1.1). ``structured`` is
        # guaranteed present by the request validator.
        if action == "structured":
            return ResumeWizardTurnResponse(
                state=apply_structured(request.state, request.structured)
            )

        # Cost guard: once the question cap is reached, stop making LLM calls for
        # answer turns and route the user to review instead of advancing.
        if request.state.asked_count >= RESUME_WIZARD_MAX_QUESTIONS:
            return ResumeWizardTurnResponse(state=apply_review(request.state))

        # Only the answer path spends provider tokens — enforce the LLM rate
        # limit here (not on the whole route).
        await enforce_llm_rate_limit(user_id)
        answer_text = request.answer.text if request.answer else ""
        state = await run_ai_turn(request.state, answer_text, skip=False)
        return ResumeWizardTurnResponse(state=state)
    except HTTPException:
        raise
    except ValueError as e:
        logger.error("Resume wizard turn validation failed: %s", e)
        raise HTTPException(status_code=422, detail="Could not update the resume draft.")
    except Exception as e:
        logger.error("Resume wizard turn failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Resume wizard failed. Please try again.",
        )


@router.post("/assist", response_model=ResumeWizardAssistResponse)
async def resume_wizard_assist(
    request: ResumeWizardAssistRequest,
    user_id: str = Depends(require_verified_user_id),
) -> ResumeWizardAssistResponse:
    """Focused AI assist for the hybrid Experience/Project cards (W-P2.2).

    ``draft_bullets`` writes resume bullets from a plain description; ``parse_entries``
    turns a pasted resume blob into structured entries. Both are LLM calls that
    return content for the user to confirm — they never mutate wizard state — so
    the LLM rate limit is enforced here (same guard as the ``answer`` turn).
    """
    try:
        await enforce_llm_rate_limit(user_id)
        if request.kind == "draft_bullets":
            bullets = await draft_bullets(
                section=request.section,
                title=request.title,
                company=request.company,
                description=request.text,
            )
            return ResumeWizardAssistResponse(bullets=bullets)
        parsed = await parse_entries(section=request.section, text=request.text)
        return ResumeWizardAssistResponse(
            entries=[ResumeWizardParsedEntry.model_validate(entry) for entry in parsed]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume wizard assist failed: %s", e)
        raise HTTPException(status_code=500, detail="AI assist failed. Please try again.")


@router.post(
    "/finalize",
    response_model=ResumeWizardFinalizeResponse,
    dependencies=[Depends(llm_rate_limit_dep)],
)
async def finalize_resume_wizard(
    request: ResumeWizardFinalizeRequest,
    user_id: str = Depends(get_effective_user_id),
) -> ResumeWizardFinalizeResponse:
    """Save the wizard draft as a resume — as the master, or as a regular resume.

    Never fails when a master already exists: instead it saves the draft as a
    normal resume (the user can promote it later from the editor). The wizard
    only becomes the master when the user asks for it AND no master exists yet.
    """
    try:
        current_master = await db.get_master_resume(user_id)
        has_master = bool(
            current_master and current_master.get("processing_status") == "ready"
        )
        # Intent: explicit flag wins; default to master only if none exists.
        want_master = request.is_master if request.is_master is not None else (not has_master)
        # Never silently replace an existing master.
        make_master = want_master and not has_master

        normalized = normalize_resume_data(
            request.state.resume_data.model_dump(mode="json")
        )
        data = ResumeData.model_validate(normalized).model_dump(mode="json")
        content = json.dumps(data, ensure_ascii=False, sort_keys=True)
        name = data.get("personalInfo", {}).get("name", "").strip() or "Resume"
        filename = f"AI Resume Wizard - {name}.json"

        if make_master:
            # Atomic master create (title set inline so a committed-but-untitled
            # master can't linger). If a master appears concurrently the row is
            # still persisted as a regular resume — we keep it rather than fail.
            resume = await db.create_resume_atomic_master(
                user_id,
                content=content,
                content_type="json",
                filename=filename,
                processed_data=data,
                processing_status="ready",
                title=f"{name} Master Resume",
            )
        else:
            resume = await db.create_resume(
                user_id,
                content=content,
                content_type="json",
                filename=filename,
                processed_data=data,
                processing_status="ready",
                title=f"{name} Resume",
                is_master=False,
            )

        created_master = bool(resume.get("is_master", False))

        # W-P3.1: only backfill the canonical profile when THIS wizard produced
        # the master (the profile mirrors the master). Best-effort — never fails
        # the save.
        if created_master:
            try:
                await profile_service.get_or_create(user_id)
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("Profile backfill after wizard finalize failed: %s", e)

        return ResumeWizardFinalizeResponse(
            message="Master resume created." if created_master else "Resume saved.",
            request_id=str(uuid4()),
            resume_id=resume["resume_id"],
            processing_status="ready",
            is_master=created_master,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Resume wizard finalize failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not save your resume.")
