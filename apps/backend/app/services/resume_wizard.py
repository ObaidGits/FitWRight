"""Service helpers for the adaptive resume wizard."""

import copy
import json
import re
from collections.abc import Callable
from typing import Any

from app.config_cache import get_content_language
from app.llm import _scrub_secrets, complete_json
from app.prompts.resume_wizard import RESUME_WIZARD_TURN_PROMPT
from app.prompts.templates import get_language_name
from app.services.improver import _sanitize_user_input
from app.schemas.models import (
    Education,
    Experience,
    Project,
    ResumeData,
    _coerce_string_list,
    normalize_resume_data,
)
from app.schemas.resume_wizard import (
    STRUCTURED_PERSONAL_INFO_FIELDS,
    ResumeWizardHistoryEntry,
    ResumeWizardProgress,
    ResumeWizardQuestion,
    ResumeWizardState,
    ResumeWizardStructuredUpdate,
)

RESUME_WIZARD_MAX_QUESTIONS = 15
# Fixed number of content milestones used for the progress bar denominator. The
# denominator is CONSTANT so the goal never recedes while the user answers
# (W-P0.3): Identity, Contact, Experience, Education, Skills, Summary.
_PROGRESS_MILESTONES = 6

_VALID_SECTIONS = {
    "intro",
    "contact",
    "summary",
    "workExperience",
    "internships",
    "education",
    "personalProjects",
    "skills",
    "review",
}

_INTRO_QUESTION = (
    "Hi — I'll help you build your master resume. "
    "What's your name, and what kind of role are you going for?"
)

_SECTION_PROMPTS = {
    "intro": _INTRO_QUESTION,
    "contact": "What's the best email, phone, or links (LinkedIn / GitHub / site) to include?",
    "summary": "In a sentence or two, how would you describe yourself professionally?",
    "workExperience": (
        "Tell me about one role: title, company, dates, what you did, and any measurable impact."
    ),
    "internships": (
        "Tell me about one internship: title, company, dates, what you worked on, "
        "and what changed because of it."
    ),
    "education": (
        "Tell me about your education: school, degree, dates, and any honors or standout coursework."
    ),
    "personalProjects": (
        "Tell me about one project: what you built, why it mattered, the tech you used, and any results."
    ),
    "skills": "What tools, technologies, or skills do you want on your resume?",
    "review": "Let's review what's here before we create your master resume.",
}

# The keyword ("my name", "name") may be lower- or upper-cased, but the captured
# name must start uppercase — so we case the keyword explicitly with [Mm]/[Nn]
# instead of re.IGNORECASE (which would let the [A-Z] capture match lowercase
# words and produce false positives like "domain name facebook is" -> "facebook is").
_INTRO_NAME_PATTERNS = (
    re.compile(r"\bI(?:'| a)m\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"),
    re.compile(r"\b[Mm]y name is\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"),
    re.compile(r"\b[Nn]ame(?:'s| is)?\s+([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"),
)


def section_prompt(section: str) -> str:
    """Deterministic fallback question text for a section."""
    return _SECTION_PROMPTS.get(section, "What would you like to add next?")


def valid_section(section: str) -> str:
    """Clamp an LLM-provided section to a known value (defaults to review)."""
    return section if section in _VALID_SECTIONS else "review"


def build_initial_wizard_state() -> ResumeWizardState:
    """Build the first state shown to a user entering the wizard."""
    return ResumeWizardState(
        step="intro",
        resume_data=ResumeData(),
        current_question=ResumeWizardQuestion(text=_INTRO_QUESTION, section="intro"),
        progress=ResumeWizardProgress(current=0, total=_PROGRESS_MILESTONES),
    )


def build_prefilled_wizard_state(resume_data: ResumeData) -> ResumeWizardState:
    """Build a wizard state pre-populated from an existing profile (W-P3.2).

    Returning users with a profile shouldn't re-enter known facts: we seed
    ``resume_data`` from the profile projection and jump straight to the first
    gap (or intro if the name is somehow missing), so the wizard only asks for
    what's missing. Deterministic, no LLM.
    """
    has_name = bool(resume_data.personalInfo.name.strip())
    if not has_name:
        return ResumeWizardState(
            step="intro",
            resume_data=resume_data,
            current_question=ResumeWizardQuestion(text=_INTRO_QUESTION, section="intro"),
            progress=compute_progress(resume_data),
        )
    gap = _next_gap_section(resume_data)
    return ResumeWizardState(
        step="review" if gap == "review" else "question",
        resume_data=resume_data,
        current_question=ResumeWizardQuestion(text=section_prompt(gap), section=gap),
        progress=compute_progress(resume_data),
    )


def extract_intro_name(answer: str) -> str:
    """Extract a likely user name from the intro answer."""
    for pattern in _INTRO_NAME_PATTERNS:
        match = pattern.search(answer)
        if match:
            return match.group(1).strip().rstrip(".")
    return ""


def merge_unique_skills(existing: list[str], inferred: list[str]) -> list[str]:
    """Merge skills while preserving first-seen casing and order."""
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *inferred]:
        skill = item.strip()
        key = skill.casefold()
        if skill and key not in seen:
            merged.append(skill)
            seen.add(key)
    return merged


def build_review_warnings(data: ResumeData) -> list[str]:
    """Deterministic, gentle notes about useful resume facts that are missing."""
    warnings: list[str] = []
    info = data.personalInfo
    # Name is the one HARD requirement for finalize (the request 422s without it),
    # so surface it at review rather than letting the user hit a generic failure.
    if not info.name.strip():
        warnings.append("Add your name — it's required to create your resume.")
    contact = [
        info.email,
        info.phone,
        info.linkedin or "",
        info.github or "",
        info.website or "",
    ]
    if not any(value.strip() for value in contact):
        warnings.append("Add at least one contact method (email, phone, or a link).")
    if not data.workExperience and not data.personalProjects:
        warnings.append("Add at least one experience, internship, or project.")
    if not data.education:
        warnings.append("Education is empty — skip only if that's intentional.")
    if not data.additional.technicalSkills:
        warnings.append("Skills are empty — add tools or technologies you've used.")
    return warnings


def compute_progress(data: ResumeData) -> ResumeWizardProgress:
    """Milestone-based progress with a FIXED denominator (W-P0.3).

    ``current`` counts how many of the six content milestones are satisfied by
    ``data``; ``total`` is constant. This replaces the old ``asked_count``-driven
    formula whose denominator grew with every answer (defeating the goal-gradient
    effect). Progress can only move forward as sections fill in.
    """
    info = data.personalInfo
    milestones = (
        bool(info.name.strip()),
        any(
            value.strip()
            for value in (
                info.email,
                info.phone,
                info.linkedin or "",
                info.github or "",
                info.website or "",
            )
        ),
        bool(data.workExperience or data.personalProjects),
        bool(data.education),
        bool(data.additional.technicalSkills),
        bool(data.summary.strip()),
    )
    current = sum(1 for satisfied in milestones if satisfied)
    return ResumeWizardProgress(current=current, total=_PROGRESS_MILESTONES)


def normalize_wizard_resume_data(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize wizard resume data through the shared resume schema."""
    normalized = normalize_resume_data(copy.deepcopy(data))
    return ResumeData.model_validate(normalized).model_dump()


def _string_list(value: Any) -> list[str]:
    """Return string items from a list-like LLM field."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


# W-P3.3: persona detection + branch-specific section ordering. Different users
# should experience different, relevant flows. Detection is deterministic from
# the (early-collected) target role; ordering is data-driven so branches are easy
# to extend and test. Summary is always appended LAST (W-P2.4) regardless of
# persona.
_CREATIVE_KEYWORDS = (
    "design",
    "designer",
    "ux",
    "ui",
    "artist",
    "creative",
    "illustrat",
    "photograph",
    "brand",
)
_STUDENT_KEYWORDS = ("student", "graduate", "intern", "undergrad", "fresher")

_PERSONA_GAP_ORDER: dict[str, tuple[str, ...]] = {
    # Students lead with education + projects (little/no work history yet).
    "student": ("education", "personalProjects", "workExperience", "skills"),
    # Creatives lead with a portfolio of projects.
    "creative": ("personalProjects", "workExperience", "education", "skills"),
    # Professionals lead with work experience.
    "professional": ("workExperience", "education", "personalProjects", "skills"),
}
_DEFAULT_GAP_ORDER: tuple[str, ...] = (
    "workExperience",
    "education",
    "personalProjects",
    "skills",
)


def persona_for(data: ResumeData) -> str:
    """Classify the user into a flow persona from their target role (W-P3.3)."""
    title = data.personalInfo.title.casefold()
    if any(keyword in title for keyword in _CREATIVE_KEYWORDS):
        return "creative"
    if any(keyword in title for keyword in _STUDENT_KEYWORDS):
        return "student"
    return "professional"


def _section_is_empty(data: ResumeData, section: str) -> bool:
    if section == "workExperience":
        return not data.workExperience
    if section == "education":
        return not data.education
    if section == "personalProjects":
        return not data.personalProjects
    if section == "skills":
        return not data.additional.technicalSkills
    return False


def _next_gap_section(data: ResumeData) -> str:
    """Pick the next empty section using the persona's order, else summary/review.

    Persona-aware (W-P3.3): the order of the content sections depends on the
    detected persona. Summary is intentionally LAST (W-P2.4): only requested once
    there is substantive content (experience or projects) to summarise.
    """
    order = _PERSONA_GAP_ORDER.get(persona_for(data), _DEFAULT_GAP_ORDER)
    for section in order:
        if _section_is_empty(data, section):
            return section
    if not data.summary.strip() and (data.workExperience or data.personalProjects):
        return "summary"
    return "review"


def _merge_entries[T](
    existing: list[T],
    updated: list[T],
    key: Callable[[T], tuple[str, ...]],
) -> list[T]:
    """Union list entries by identity signature.

    A partial model reply (e.g. it echoes only the role the user just described
    instead of the full list) must NOT erase earlier entries. So: existing
    entries the model omits are kept, entries it echoes (same signature) are
    replaced in place, and genuinely new entries are appended. Signatures are
    content-based rather than ``id``-based because wizard entry ids default to 0.
    """
    result = list(existing)
    index: dict[tuple[str, ...], int] = {}
    for position, item in enumerate(result):
        index.setdefault(key(item), position)
    for item in updated:
        signature = key(item)
        if signature in index:
            result[index[signature]] = item
        else:
            index[signature] = len(result)
            result.append(item)
    return result


def _experience_key(item: Experience) -> tuple[str, ...]:
    return (
        item.title.strip().casefold(),
        item.company.strip().casefold(),
        item.years.strip().casefold(),
    )


def _education_key(item: Education) -> tuple[str, ...]:
    return (
        item.institution.strip().casefold(),
        item.degree.strip().casefold(),
        item.years.strip().casefold(),
    )


def _project_key(item: Project) -> tuple[str, ...]:
    return (item.name.strip().casefold(), item.years.strip().casefold())


def _merge_section(
    *,
    existing: ResumeData,
    updated: ResumeData,
    raw_updated: dict[str, Any],
    section: str,
    inferred_skills: list[str],
) -> ResumeData:
    """Merge LLM output ONLY into the active section, never clobbering the rest."""
    merged = existing.model_copy(deep=True)

    if section in {"intro", "contact"}:
        if isinstance(raw_updated.get("personalInfo"), dict):
            for field in ("name", "title", "email", "phone", "location"):
                new_val = getattr(updated.personalInfo, field)
                if isinstance(new_val, str) and new_val.strip():
                    setattr(merged.personalInfo, field, new_val)
            for field in ("website", "linkedin", "github"):
                new_val = getattr(updated.personalInfo, field)
                if new_val:
                    setattr(merged.personalInfo, field, new_val)
        return merged

    if section == "summary":
        if "summary" in raw_updated and updated.summary.strip():
            merged.summary = updated.summary
        return merged

    if section in {"workExperience", "internships"}:
        if "workExperience" in raw_updated:
            merged.workExperience = _merge_entries(
                merged.workExperience, updated.workExperience, _experience_key
            )
        return merged

    if section == "education":
        if "education" in raw_updated:
            merged.education = _merge_entries(
                merged.education, updated.education, _education_key
            )
        return merged

    if section == "personalProjects":
        if "personalProjects" in raw_updated:
            merged.personalProjects = _merge_entries(
                merged.personalProjects, updated.personalProjects, _project_key
            )
        return merged

    if section == "skills":
        raw_additional = raw_updated.get("additional")
        if isinstance(raw_additional, dict):
            if "technicalSkills" in raw_additional:
                merged.additional.technicalSkills = merge_unique_skills(
                    merged.additional.technicalSkills,
                    updated.additional.technicalSkills,
                )
            if "languages" in raw_additional:
                merged.additional.languages = merge_unique_skills(
                    merged.additional.languages, updated.additional.languages
                )
            if "certificationsTraining" in raw_additional:
                merged.additional.certificationsTraining = merge_unique_skills(
                    merged.additional.certificationsTraining,
                    updated.additional.certificationsTraining,
                )
            if "awards" in raw_additional:
                merged.additional.awards = merge_unique_skills(
                    merged.additional.awards, updated.additional.awards
                )
        # W-P1.2: inferred skills are NO LONGER auto-merged here. They are
        # returned in the state as *suggestions* the user explicitly confirms via
        # the skills chip UI, honouring the prompt's "never invent skills" rule.
        # (``inferred_skills`` is intentionally unused in this branch now.)
        return merged

    # Unknown / review section: never mutate resume_data.
    return merged


def _assign_entry_ids(data: ResumeData) -> None:
    """Give every list entry a unique 1-based id (in place).

    The LLM omits ``id`` (the wizard prompt's schema doesn't request it), so
    entries default to ``id=0``. Downstream consumers — the live preview's React
    keys and the builder's ``Math.max(...ids)+1`` add logic — assume unique ids,
    so renumber them deterministically by position (order is append-stable).
    """
    for index, item in enumerate(data.workExperience, start=1):
        item.id = index
    for index, item in enumerate(data.education, start=1):
        item.id = index
    for index, item in enumerate(data.personalProjects, start=1):
        item.id = index


# W-P1.4: per-section completion budgets. A single-section bullet rewrite needs
# far fewer tokens than the old blanket 8192; summary/experience get the most.
_SECTION_MAX_TOKENS = {
    "workExperience": 1600,
    "internships": 1600,
    "personalProjects": 1200,
    "education": 800,
    "summary": 900,
}
_DEFAULT_MAX_TOKENS = 1200

# W-P1.4: which resume slices to send to the model for each section. Sending only
# the relevant slice (plus dependencies for summary) shrinks the prompt and can't
# accidentally leak/clobber unrelated sections (``_merge_section`` guards writes).
_SECTION_CONTEXT_KEYS = {
    "intro": ("personalInfo",),
    "contact": ("personalInfo",),
    "summary": ("summary", "workExperience", "personalProjects"),
    "workExperience": ("workExperience",),
    "internships": ("workExperience",),
    "education": ("education",),
    "personalProjects": ("personalProjects",),
    "skills": ("additional",),
}


def max_tokens_for_section(section: str) -> int:
    """Return the completion-token budget for ``section`` (W-P1.4)."""
    return _SECTION_MAX_TOKENS.get(section, _DEFAULT_MAX_TOKENS)


def scoped_resume_json(data: ResumeData, section: str) -> str:
    """Serialize only the resume slices relevant to ``section`` (W-P1.4).

    Falls back to the full document for unknown sections so behaviour is never
    worse than before.
    """
    full = data.model_dump(mode="json")
    keys = _SECTION_CONTEXT_KEYS.get(section)
    if not keys:
        payload = full
    else:
        payload = {key: full[key] for key in keys if key in full}
    return json.dumps(payload, ensure_ascii=False)


def _build_turn_state(
    *,
    data: ResumeData,
    next_question: ResumeWizardQuestion,
    history: list[ResumeWizardHistoryEntry],
    asked_count: int,
    inferred_skills: list[str],
    is_complete: bool,
    warnings: list[str],
) -> ResumeWizardState:
    """Build the post-turn state, auto-advancing to the review STEP when there is
    nothing left to ask.

    ``_next_gap_section`` (and the model) can resolve the next section to
    ``"review"`` once every content section is filled. Emitting that as a
    ``step="question"`` with ``section="review"`` renders a degenerate question
    card ("Let's review…") with a free-text box. Instead, transition to the
    review step so the client shows the review/save surface. History is preserved
    so Back from review restores the last real question with data intact.
    """
    if next_question.section == "review":
        return ResumeWizardState(
            step="review",
            resume_data=data,
            current_question=ResumeWizardQuestion(
                text=section_prompt("review"), section="review"
            ),
            history=history,
            asked_count=asked_count,
            inferred_skills=inferred_skills,
            is_complete=is_complete,
            progress=compute_progress(data),
            warnings=build_review_warnings(data),
        )
    return ResumeWizardState(
        step="question",
        resume_data=data,
        current_question=next_question,
        history=history,
        asked_count=asked_count,
        inferred_skills=inferred_skills,
        is_complete=is_complete,
        progress=compute_progress(data),
        warnings=warnings,
    )


def _next_question(result: dict[str, Any], data: ResumeData) -> ResumeWizardQuestion:
    """Use the model's next_question, or fall back to the next empty section."""
    candidate = result.get("next_question")
    if isinstance(candidate, dict):
        text = candidate.get("text")
        section = candidate.get("section")
        if isinstance(text, str) and text.strip() and isinstance(section, str):
            return ResumeWizardQuestion(text=text.strip(), section=valid_section(section))
    gap = _next_gap_section(data)
    return ResumeWizardQuestion(text=section_prompt(gap), section=gap)


async def run_ai_turn(
    state: ResumeWizardState,
    answer_text: str,
    *,
    skip: bool,
) -> ResumeWizardState:
    """Run one adaptive AI turn (answer or skip) and validate the result."""
    section = state.current_question.section
    # W-P1.4: send only the section-relevant slice, not the whole resume.
    resume_json = scoped_resume_json(state.resume_data, section)
    prompt_answer = (
        "(The user skipped this question. Do NOT modify resume_data. "
        "Ask the next most useful question for a different section.)"
        if skip
        # Strip prompt-injection patterns AND redact credential-like tokens
        # (sk-…/AIza…/Bearer …) before the answer reaches the LLM.
        else _scrub_secrets(_sanitize_user_input(answer_text))
    )
    prompt = RESUME_WIZARD_TURN_PROMPT.format(
        output_language=get_language_name(get_content_language()),
        current_section=section,
        resume_json=resume_json,
        answer_text=prompt_answer,
    )
    result = await complete_json(
        prompt, max_tokens=max_tokens_for_section(section), schema_type="resume"
    )
    if not isinstance(result, dict):
        raise ValueError("Resume wizard LLM response must be a JSON object.")

    raw_resume = result.get("resume_data")
    inferred = _string_list(result.get("inferred_skills"))

    if skip or not isinstance(raw_resume, dict):
        data = state.resume_data.model_copy(deep=True)
    else:
        updated = ResumeData.model_validate(normalize_wizard_resume_data(raw_resume))
        data = _merge_section(
            existing=state.resume_data,
            updated=updated,
            raw_updated=raw_resume,
            section=section,
            inferred_skills=inferred,
        )

    if section == "intro" and not data.personalInfo.name.strip():
        fallback = extract_intro_name(answer_text)
        if fallback:
            data.personalInfo.name = fallback

    # Entries from the LLM default to id=0; give them unique ids so the preview
    # keys and the builder's id-based logic work on a finalized wizard resume.
    _assign_entry_ids(data)

    asked_count = state.asked_count + 1

    # W-P0.5: the name is the one hard requirement for finalize. If we're still
    # on intro and couldn't capture a name, re-ask for it now rather than letting
    # the user discover the failure at Save time (a 422). This keeps the fix at
    # the point of collection instead of the end of the flow.
    missing_name_at_intro = section == "intro" and not data.personalInfo.name.strip()
    if missing_name_at_intro:
        next_question = ResumeWizardQuestion(
            text=(
                "Thanks! One thing first — what's your name? "
                "It goes at the top of your resume."
            ),
            section="intro",
        )
        warnings = ["Add your name — it's required to create your resume."]
    else:
        next_question = _next_question(result, data)
        warnings = []

    # `is_complete` is a SUGGESTION to surface "Review & finish" — the step stays
    # "question" and never auto-finalizes. The client decides when to call /review.
    is_complete = (
        not missing_name_at_intro
        and (bool(result.get("is_complete")) or asked_count >= RESUME_WIZARD_MAX_QUESTIONS)
    )

    history = list(state.history)
    history.append(
        ResumeWizardHistoryEntry(
            question=state.current_question.text,
            answer="" if skip else answer_text,
            section=section,
            resume_data_before=state.resume_data,
        )
    )

    return _build_turn_state(
        data=data,
        next_question=next_question,
        history=history,
        asked_count=asked_count,
        inferred_skills=inferred,
        is_complete=is_complete,
        warnings=warnings,
    )


def _sanitize(text: str) -> str:
    """Strip prompt-injection patterns + redact credential-like tokens."""
    return _scrub_secrets(_sanitize_user_input(text))


def _entry_kind(section: str) -> str:
    return "project" if section == "personalProjects" else "work experience"


async def draft_bullets(*, section: str, title: str, company: str, description: str) -> list[str]:
    """AI-draft 2-4 truthful resume bullets for one entry (W-P2.2).

    Facts (title/company) are context only; the plain ``description`` is the
    source of truth. Never mutates state; returns bullets for the card to show.
    """
    from app.prompts.resume_wizard import RESUME_WIZARD_BULLETS_PROMPT

    facts = ", ".join(part for part in (title.strip(), company.strip()) if part) or "(none)"
    prompt = RESUME_WIZARD_BULLETS_PROMPT.format(
        entry_kind=_entry_kind(section),
        output_language=get_language_name(get_content_language()),
        facts=_sanitize(facts),
        description=_sanitize(description),
    )
    result = await complete_json(prompt, max_tokens=600, schema_type="resume")
    if not isinstance(result, dict):
        return []
    # Reuse the resume schema's bullet coercion so numbered/΄dashed lines normalize.
    return _coerce_string_list(result.get("bullets"))


async def parse_entries(*, section: str, text: str) -> list[dict[str, Any]]:
    """AI-parse a pasted blob into structured entries for confirmation (W-P2.2).

    Never mutates state; returns Experience/Project-shaped dicts the client loads
    into the card(s). Truthful extraction only (no invention).
    """
    from app.prompts.resume_wizard import RESUME_WIZARD_PARSE_PROMPT

    prompt = RESUME_WIZARD_PARSE_PROMPT.format(
        entry_kind=_entry_kind(section),
        pasted_text=_sanitize(text),
    )
    result = await complete_json(prompt, max_tokens=2000, schema_type="resume")
    if not isinstance(result, dict):
        return []
    raw_entries = result.get("entries")
    if not isinstance(raw_entries, list):
        return []
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        entries.append(
            {
                "title": str(raw.get("title") or ""),
                "company": str(raw.get("company") or ""),
                "location": str(raw.get("location") or ""),
                "years": str(raw.get("years") or ""),
                "name": str(raw.get("name") or ""),
                "role": str(raw.get("role") or ""),
                "description": _coerce_string_list(raw.get("description")),
            }
        )
    return entries


def apply_back(state: ResumeWizardState) -> ResumeWizardState:
    """Navigate to the previous question WITHOUT destroying entered data (W-P0.1).

    Back is navigation, not undo: the current merged ``resume_data`` is kept
    (so the live preview never loses what the user just entered), the previous
    question is restored, and that question's earlier answer is returned in
    ``restored_answer`` so the client can repopulate the input for editing. When
    the user re-submits, ``_merge_entries`` replaces the matching entry in place.
    """
    if not state.history:
        return state.model_copy(deep=True)
    history = list(state.history)
    last = history.pop()
    asked_count = max(0, state.asked_count - 1)
    # Keep the current merged draft rather than rewinding to the pre-answer
    # snapshot — this is the core of the non-destructive fix.
    kept_data = state.resume_data.model_copy(deep=True)
    # Derive step from the restored question itself, not just the count, so a
    # restored non-intro question never renders under the intro step (which hides
    # the question-step actions).
    return ResumeWizardState(
        step="intro" if last.section == "intro" else "question",
        resume_data=kept_data,
        current_question=ResumeWizardQuestion(text=last.question, section=last.section),
        history=history,
        asked_count=asked_count,
        inferred_skills=[],
        is_complete=False,
        progress=compute_progress(kept_data),
        warnings=[],
        restored_answer=last.answer,
    )


def apply_skip(state: ResumeWizardState) -> ResumeWizardState:
    """Advance to the next gap section deterministically, with NO LLM call (W-P0.4).

    Skipping never modifies ``resume_data`` and never needs the model to choose
    the next question — ``_next_gap_section`` already does that on the server. The
    turn is recorded in history (so Back still works after a skip) but costs zero
    tokens and no latency.
    """
    data = state.resume_data.model_copy(deep=True)
    asked_count = state.asked_count + 1
    history = list(state.history)
    history.append(
        ResumeWizardHistoryEntry(
            question=state.current_question.text,
            answer="",
            section=state.current_question.section,
            resume_data_before=state.resume_data,
        )
    )
    gap = _next_gap_section(data)
    return _build_turn_state(
        data=data,
        next_question=ResumeWizardQuestion(text=section_prompt(gap), section=gap),
        history=history,
        asked_count=asked_count,
        inferred_skills=[],
        is_complete=asked_count >= RESUME_WIZARD_MAX_QUESTIONS,
        warnings=[],
    )


_INTRO_NAME_REASK = (
    "Thanks! One thing first — what's your name? It goes at the top of your resume."
)


def apply_structured(
    state: ResumeWizardState, update: ResumeWizardStructuredUpdate
) -> ResumeWizardState:
    """Apply a structured section update deterministically, with NO LLM call (W-P1.1).

    Merges discrete identity/contact fields and/or a confirmed skills list, then
    advances to the client-named ``next_section`` (or the next content gap). The
    turn is recorded in history so Back stays non-destructive.
    """
    section = state.current_question.section
    data = state.resume_data.model_copy(deep=True)

    for field, raw in update.personal_info.items():
        if field in STRUCTURED_PERSONAL_INFO_FIELDS:
            setattr(data.personalInfo, field, raw.strip())

    if update.technical_skills is not None:
        # Confirmed skills fully define the list (deduped, order-preserving).
        data.additional.technicalSkills = merge_unique_skills([], update.technical_skills)

    # A structured Education entry is appended/replaced by signature (W-P2.1),
    # never clobbering earlier entries — same rule as the AI merge path.
    if update.education is not None and (
        update.education.institution.strip() or update.education.degree.strip()
    ):
        data.education = _merge_entries(
            data.education, [update.education], _education_key
        )

    # Structured Experience / Project entries (W-P2.2 hybrid cards). Merged by
    # signature so re-submitting the same role replaces it in place; lists support
    # confirming a parsed multi-role paste in one turn.
    if update.experiences:
        valid_exp = [
            exp for exp in update.experiences if exp.title.strip() or exp.company.strip()
        ]
        if valid_exp:
            data.workExperience = _merge_entries(
                data.workExperience, valid_exp, _experience_key
            )
    if update.projects:
        valid_proj = [proj for proj in update.projects if proj.name.strip()]
        if valid_proj:
            data.personalProjects = _merge_entries(
                data.personalProjects, valid_proj, _project_key
            )

    _assign_entry_ids(data)
    asked_count = state.asked_count + 1

    history = list(state.history)
    history.append(
        ResumeWizardHistoryEntry(
            question=state.current_question.text,
            answer="",
            section=section,
            resume_data_before=state.resume_data,
        )
    )

    # Identity must yield a name; if it didn't, re-ask intro (mirrors W-P0.5).
    if section == "intro" and not data.personalInfo.name.strip():
        next_question = ResumeWizardQuestion(text=_INTRO_NAME_REASK, section="intro")
        warnings = ["Add your name — it's required to create your resume."]
    elif update.next_section:
        next_question = ResumeWizardQuestion(
            text=section_prompt(update.next_section), section=update.next_section
        )
        warnings = []
    else:
        gap = _next_gap_section(data)
        next_question = ResumeWizardQuestion(text=section_prompt(gap), section=gap)
        warnings = []

    return _build_turn_state(
        data=data,
        next_question=next_question,
        history=history,
        asked_count=asked_count,
        # Preserve skill suggestions across structured turns so the chip UI can
        # still offer them (W-P1.2).
        inferred_skills=state.inferred_skills,
        is_complete=False,
        warnings=warnings,
    )


def apply_review(state: ResumeWizardState) -> ResumeWizardState:
    """Move to the review step (no LLM call) and compute gentle warnings."""
    next_state = state.model_copy(deep=True)
    next_state.step = "review"
    next_state.current_question = ResumeWizardQuestion(
        text=section_prompt("review"), section="review"
    )
    next_state.warnings = build_review_warnings(next_state.resume_data)
    # A restored answer is only meaningful for the question that was restored;
    # never carry it into review.
    next_state.restored_answer = ""
    return next_state
