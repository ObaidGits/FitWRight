"""AI Intelligence Layer - assists that improve *existing* profile content (P5).

Firm rule (design §P5): **AI never fabricates experience.** Every assist takes
content the user already has and improves its wording/structure; it cannot add
jobs, employers, dates, or achievements that aren't present. Suggestions are
returned for review - never auto-applied - so the user stays in control.

- ``normalize_skills`` is **pure** (Canonical Skill Engine, no LLM): dedupes and
  canonicalizes the user's skills; always available and deterministic.
- ``suggest_summary`` / ``suggest_experience_bullets`` use the shared LLM client
  with a truthfulness-constrained prompt, and degrade gracefully to a clear note
  when no model is configured (no crash, no fabrication).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.profile.schemas import ProfileData
from app.profile.skills import canonicalize

logger = logging.getLogger(__name__)

__all__ = [
    "is_llm_available",
    "normalize_skills",
    "suggest_summary",
    "suggest_experience_bullets",
    "skills_gap",
    "suggest_keywords",
]

# Deterministic role -> commonly-expected skills map (grounding for gap analysis).
# Small on purpose; a future taxonomy service can widen it. Never used to invent
# *experience* - only to suggest skills the user may want to add/learn.
_ROLE_SKILLS: dict[str, list[str]] = {
    "frontend": ["JavaScript", "TypeScript", "React", "CSS", "HTML", "Testing"],
    "backend": ["Python", "SQL", "PostgreSQL", "REST", "Docker", "Testing"],
    "fullstack": ["JavaScript", "TypeScript", "React", "Node.js", "SQL", "Docker"],
    "data": ["Python", "SQL", "Pandas", "Machine Learning", "Statistics"],
    "ml": ["Python", "Machine Learning", "PyTorch", "Statistics", "SQL"],
    "devops": ["Docker", "Kubernetes", "CI/CD", "AWS", "Terraform", "Linux"],
    "mobile": ["Swift", "Kotlin", "React Native", "REST", "Testing"],
}

_TRUTHFULNESS = (
    "You are an expert resume editor. Improve ONLY the wording and clarity of the "
    "text provided. NEVER invent employers, roles, dates, metrics, technologies, "
    "or achievements that are not present in the input. Do not exaggerate. Output "
    "valid JSON only."
)


def is_llm_available(user_id: str | None = None) -> bool:
    """Whether a usable LLM is configured for this user (keyless providers count)."""
    try:
        from app.llm import get_llm_config

        config = get_llm_config(user_id)
        return bool(config.api_key) or config.provider in ("ollama", "openai_compatible")
    except Exception:  # pragma: no cover - defensive
        return False


def normalize_skills(profile: ProfileData) -> dict[str, Any]:
    """Deduplicate + canonicalize the profile's skills (pure, no LLM).

    Returns ``{"skills": <ProfileSkills dict>, "changed": bool}`` - the caller
    decides whether to apply it. Order-preserving within each category; canonical
    duplicates are collapsed (keeping the first, merging aliases).
    """
    data = profile.model_dump(mode="json")
    skills = data.get("skills", {})
    changed = False
    for cat in ("technical", "soft", "languages", "tools"):
        seen: dict[str, dict] = {}
        for skill in skills.get(cat, []) or []:
            raw = skill.get("displayName") or skill.get("canonical") or ""
            canonical, display = canonicalize(raw)
            if not canonical:
                continue
            if canonical in seen:
                changed = True
                # Merge aliases into the kept skill.
                kept = seen[canonical]
                for alias in skill.get("aliases", []) or []:
                    if alias and alias not in kept["aliases"]:
                        kept["aliases"].append(alias)
                continue
            if display != raw or skill.get("canonical") != canonical:
                changed = True
            normalized = dict(skill)
            normalized["canonical"] = canonical
            normalized["displayName"] = display
            normalized.setdefault("aliases", [])
            seen[canonical] = normalized
        new_list = list(seen.values())
        if len(new_list) != len(skills.get(cat, []) or []):
            changed = True
        skills[cat] = new_list
    return {"skills": skills, "changed": changed}


def _held_skill_names(profile: ProfileData) -> set[str]:
    names: set[str] = set()
    for group in (profile.skills.technical, profile.skills.tools, profile.skills.languages, profile.skills.soft):
        for s in group:
            n = (s.displayName or s.canonical or "").strip().lower()
            if n:
                names.add(n)
    return names


def skills_gap(profile: ProfileData) -> dict[str, Any]:
    """Deterministic skills-gap analysis vs. the profile's target roles.

    Matches ``identity.targetRoles`` (and current role) against a small role->
    skills map and reports which expected skills are already held vs. missing.
    **Never fabricates experience** - it only recommends skills to add/learn, with
    an explicit, explainable basis. Returns ``{target_roles, have, missing, basis}``.
    """
    ident = profile.identity
    role_texts = [*(ident.targetRoles or []), ident.currentRole, ident.headline]
    matched_roles: list[str] = []
    expected: list[str] = []
    seen_roles: set[str] = set()
    for text in role_texts:
        low = (text or "").lower()
        for key, skills in _ROLE_SKILLS.items():
            if key in low and key not in seen_roles:
                seen_roles.add(key)
                matched_roles.append(key)
                for s in skills:
                    if s not in expected:
                        expected.append(s)

    held = _held_skill_names(profile)
    have = [s for s in expected if s.lower() in held]
    missing = [s for s in expected if s.lower() not in held]
    return {
        "kind": "skills_gap",
        "suggestion": {"have": have, "missing": missing},
        "target_roles": matched_roles,
        "note": (
            "Add a target role (e.g. 'frontend', 'backend', 'data') to your profile for tailored gap analysis."
            if not matched_roles
            else None
        ),
    }


def suggest_keywords(profile: ProfileData) -> dict[str, Any]:
    """Deterministic ATS keyword extraction from existing content (no fabrication).

    Surfaces the most frequent meaningful tokens across experience bullets, tech,
    and skills - the terms an ATS is likely to key on - so the user can ensure
    they appear. Purely derived from what's already in the profile.
    """
    from collections import Counter

    stop = {
        "the", "and", "for", "with", "was", "were", "our", "your", "from", "that",
        "this", "have", "has", "are", "not", "but", "all", "using", "used", "use",
        "a", "an", "of", "to", "in", "on", "by", "at", "as", "is", "it",
    }
    counter: Counter[str] = Counter()
    for e in profile.workExperience:
        for bullet in e.description:
            for tok in re.findall(r"[A-Za-z][A-Za-z0-9+.#-]{2,}", bullet):
                low = tok.lower()
                if low not in stop:
                    counter[low] += 1
        for t in e.tech:
            counter[t.lower()] += 2
    for group in (profile.skills.technical, profile.skills.tools):
        for s in group:
            name = (s.displayName or s.canonical or "").strip().lower()
            if name:
                counter[name] += 2
    keywords = [k for k, _ in counter.most_common(20)]
    return {"kind": "keywords", "suggestion": keywords, "note": None}


async def suggest_summary(profile: ProfileData, *, user_id: str | None = None) -> dict[str, Any]:
    """Suggest an improved professional summary from existing profile content."""
    existing = (profile.summary or profile.identity.careerObjective or "").strip()
    context_bits = [profile.identity.headline, profile.identity.currentRole]
    context = " - ".join(b for b in context_bits if b)
    experience_titles = [e.title for e in profile.workExperience if e.title][:5]

    if not existing and not experience_titles:
        return {
            "kind": "summary",
            "suggestion": None,
            "note": "Add your experience or a draft summary first - AI improves what you have, it won't invent it.",
        }
    if not is_llm_available(user_id):
        return {
            "kind": "summary",
            "suggestion": None,
            "note": "No AI model is configured. Add one in Settings to enable suggestions.",
        }

    from app.llm import complete_json

    prompt = (
        "Rewrite the following professional summary to be concise, specific, and "
        "impactful (2-4 sentences). Base it strictly on the provided details.\n\n"
        f"Headline/role: {context or 'n/a'}\n"
        f"Recent roles: {', '.join(experience_titles) or 'n/a'}\n"
        f"Current summary: {existing or '(none - draft one from the roles above)'}\n\n"
        'Return JSON: {"summary": "<improved summary>"}'
    )
    try:
        result = await complete_json(prompt=prompt, system_prompt=_TRUTHFULNESS, max_tokens=512)
        suggestion = (result.get("summary") or "").strip() if isinstance(result, dict) else ""
    except Exception:
        logger.exception("AI summary suggestion failed")
        return {"kind": "summary", "suggestion": None, "note": "AI is temporarily unavailable. Try again shortly."}
    return {"kind": "summary", "suggestion": suggestion or None, "note": None}


async def suggest_experience_bullets(
    profile: ProfileData, experience_uid: str, *, user_id: str | None = None
) -> dict[str, Any]:
    """Suggest improved bullet points for one existing experience entry."""
    exp = next((e for e in profile.workExperience if e.uid == experience_uid), None)
    if exp is None:
        return {"kind": "experience_bullets", "suggestion": None, "note": "That experience was not found."}
    if not exp.description:
        return {
            "kind": "experience_bullets",
            "suggestion": None,
            "note": "Add at least one bullet first - AI polishes what you have, it won't invent it.",
        }
    if not is_llm_available(user_id):
        return {
            "kind": "experience_bullets",
            "suggestion": None,
            "note": "No AI model is configured. Add one in Settings to enable suggestions.",
        }

    from app.llm import complete_json

    prompt = (
        f"Improve these resume bullet points for the role '{exp.title}' at "
        f"'{exp.company}'. Keep every fact; make each bullet action-led and "
        "quantified only where a number is already implied. Do not add new "
        "responsibilities.\n\n"
        f"Bullets:\n- " + "\n- ".join(exp.description) + "\n\n"
        'Return JSON: {"bullets": ["...", "..."]}'
    )
    try:
        result = await complete_json(prompt=prompt, system_prompt=_TRUTHFULNESS, max_tokens=1024)
        bullets = result.get("bullets") if isinstance(result, dict) else None
        bullets = [b.strip() for b in bullets if isinstance(b, str) and b.strip()] if isinstance(bullets, list) else []
    except Exception:
        logger.exception("AI bullet suggestion failed")
        return {"kind": "experience_bullets", "suggestion": None, "note": "AI is temporarily unavailable. Try again shortly."}
    return {"kind": "experience_bullets", "suggestion": bullets or None, "note": None}
