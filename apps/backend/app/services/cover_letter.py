"""Cover letter, outreach message, and resume title generation service."""

import json
import logging
from typing import Any

from app.config import load_config_file
from app.llm import complete
from app.prompts.templates import (
    COVER_LETTER_PROMPT,
    GENERATE_TITLE_PROMPT,
    OUTREACH_MESSAGE_PROMPT,
)
from app.prompts import get_language_name


def _resolve_feature_prompt(
    custom_key: str,
    default_template: str,
) -> tuple[str, bool]:
    """Resolve a feature-prompt template at runtime.

    Returns ``(template, is_custom)``. If the stored custom prompt is
    empty or absent, returns the default template. The ``is_custom`` flag
    lets callers decide whether to fall back to the default on a format
    failure (defensive — save-time validation should have caught a
    malformed custom prompt).
    """
    stored = load_config_file()
    custom = (stored.get(custom_key) or "").strip()
    if not custom:
        return default_template, False
    return custom, True


COVER_LETTER_SYSTEM_PROMPT = (
    "You are a professional career coach and resume writer. Write compelling, "
    "personalized cover letters."
)
OUTREACH_SYSTEM_PROMPT = (
    "You are a professional networking coach. Write genuine, engaging cold "
    "outreach messages."
)


def _format_feature_prompt(
    custom_key: str,
    default_template: str,
    *,
    resume_data: dict[str, Any],
    job_description: str,
    output_language: str,
) -> str:
    """Resolve + format a feature prompt with fallback to the default template.

    Single source of truth for building the cover-letter / outreach prompt so the
    streaming (P4) and non-streaming paths never drift.
    """
    template, is_custom = _resolve_feature_prompt(custom_key, default_template)
    try:
        return template.format(
            job_description=job_description,
            resume_data=json.dumps(resume_data),
            output_language=output_language,
        )
    except (KeyError, IndexError, ValueError) as e:
        if not is_custom:
            raise
        logging.warning(
            "Custom prompt %s failed to format (%s); falling back to default",
            custom_key,
            e,
        )
        return default_template.format(
            job_description=job_description,
            resume_data=json.dumps(resume_data),
            output_language=output_language,
        )


def build_cover_letter_prompt(
    resume_data: dict[str, Any], job_description: str, language: str = "en"
) -> str:
    """Build the cover-letter user prompt (shared by stream + non-stream)."""
    return _format_feature_prompt(
        "cover_letter_prompt",
        COVER_LETTER_PROMPT,
        resume_data=resume_data,
        job_description=job_description,
        output_language=get_language_name(language),
    )


def build_outreach_prompt(
    resume_data: dict[str, Any], job_description: str, language: str = "en"
) -> str:
    """Build the outreach-message user prompt (shared by stream + non-stream)."""
    return _format_feature_prompt(
        "outreach_message_prompt",
        OUTREACH_MESSAGE_PROMPT,
        resume_data=resume_data,
        job_description=job_description,
        output_language=get_language_name(language),
    )


async def generate_cover_letter(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> str:
    """Generate a cover letter based on resume and job description.

    Args:
        resume_data: Structured resume data (ResumeData format)
        job_description: Target job description text
        language: Output language code (en, es, zh, ja)

    Returns:
        Generated cover letter as plain text
    """
    prompt = build_cover_letter_prompt(resume_data, job_description, language)

    result = await complete(
        prompt=prompt,
        system_prompt=COVER_LETTER_SYSTEM_PROMPT,
        max_tokens=2048,
    )

    return result.strip()


async def generate_outreach_message(
    resume_data: dict[str, Any],
    job_description: str,
    language: str = "en",
) -> str:
    """Generate a cold outreach message for networking.

    Args:
        resume_data: Structured resume data (ResumeData format)
        job_description: Target job description text
        language: Output language code (en, es, zh, ja)

    Returns:
        Generated outreach message as plain text
    """
    prompt = build_outreach_prompt(resume_data, job_description, language)

    result = await complete(
        prompt=prompt,
        system_prompt=OUTREACH_SYSTEM_PROMPT,
        max_tokens=1024,
    )

    return result.strip()


def _clean_title_fragment(value: str, *, max_len: int = 60) -> str:
    """Sanitize an LLM/title fragment into a short, single-line string.

    Collapses whitespace/newlines (guards against paragraph-length output),
    strips wrapping quotes, and truncates to ``max_len`` characters so a
    verbose model response can never produce a sentence-long title.
    """
    # Collapse any run of whitespace (incl. newlines) to a single space.
    cleaned = " ".join(value.split()).strip().strip("\"'").strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def compose_resume_title(
    candidate_name: str | None,
    role_and_company: str,
) -> str:
    """Compose a concise, human-readable resume title.

    Produces ``"<Name> — <Role @ Company>"`` when a candidate name is known,
    otherwise falls back to just the role/company fragment. The result is
    always a short single line (never a sentence/paragraph).
    """
    role = _clean_title_fragment(role_and_company)
    name = _clean_title_fragment(candidate_name or "", max_len=40)
    if name and role:
        return f"{name} — {role}"[:80]
    if role:
        return role[:80]
    if name:
        return f"{name} — Resume"[:80]
    return "Tailored resume"


async def generate_resume_title(
    job_description: str,
    language: str = "en",
    candidate_name: str | None = None,
) -> str:
    """Generate a short descriptive title from a job description.

    Args:
        job_description: Target job description text
        language: Output language code (en, es, zh, ja)
        candidate_name: The candidate's name (from resume personalInfo). When
            provided, the title is composed as ``"<Name> — <Role @ Company>"``.

    Returns:
        Generated title like "Jane Doe — Senior Frontend Engineer @ Stripe"
        (or "Senior Frontend Engineer @ Stripe" when the name is unknown).
    """
    output_language = get_language_name(language)

    prompt = GENERATE_TITLE_PROMPT.format(
        job_description=job_description,
        output_language=output_language,
    )

    result = await complete(
        prompt=prompt,
        system_prompt="You extract job titles and company names from job descriptions.",
        max_tokens=60,
        temperature=0.3,
    )

    # Compose the final concise title in code so a verbose model response can
    # never yield a sentence/paragraph-length name.
    return compose_resume_title(candidate_name, result)
