"""Completion Engine — weighted profile completeness + suggestions.

Pure, deterministic, and explainable: a single documented weight table maps
profile aspects to points; the score is the sum of satisfied weights clamped to
0..100. The same table drives the prioritized "add X to reach Y%" suggestions,
so the UI nudge and the number never disagree. Cached in
``profiles.completeness`` for O(1) list reads; recomputed on every write.
"""

from __future__ import annotations

from app.profile.schemas import CompletenessSuggestion, ProfileData

# (key, human label, weight). Weights sum to 100. Ordered by importance so the
# suggestion list is naturally prioritized (highest-impact missing item first).
_WEIGHTS: tuple[tuple[str, str, int], ...] = (
    ("name", "Add your name", 8),
    ("headline", "Add a professional headline", 10),
    ("summary", "Write a professional summary", 12),
    ("contact", "Add an email or phone number", 8),
    ("location", "Add your location", 4),
    ("experience", "Add work experience", 18),
    ("experience_detail", "Describe your experience with bullet points", 10),
    ("education", "Add your education", 8),
    ("skills", "Add your skills", 12),
    ("projects", "Add a project", 5),
    ("links", "Add a portfolio or professional link", 5),
)


def _is_satisfied(key: str, profile: ProfileData) -> bool:
    """Whether the aspect ``key`` is present/complete in ``profile``."""
    ident = profile.identity
    if key == "name":
        return bool(ident.name.strip())
    if key == "headline":
        return bool((ident.headline or ident.currentRole).strip())
    if key == "summary":
        return bool((profile.summary or ident.careerObjective).strip())
    if key == "contact":
        return bool(ident.email.strip() or ident.phone.strip())
    if key == "location":
        return bool(ident.location.strip())
    if key == "experience":
        return len(profile.workExperience) > 0
    if key == "experience_detail":
        return any(exp.description for exp in profile.workExperience)
    if key == "education":
        return len(profile.education) > 0
    if key == "skills":
        s = profile.skills
        return bool(s.technical or s.soft or s.languages or s.tools)
    if key == "projects":
        return len(profile.personalProjects) > 0
    if key == "links":
        return len(profile.links) > 0 or bool(ident.linkedin or ident.github or ident.website)
    return False


def compute_completeness(profile: ProfileData) -> int:
    """Return the weighted completion score for ``profile`` (0..100)."""
    score = 0
    for key, _label, weight in _WEIGHTS:
        if _is_satisfied(key, profile):
            score += weight
    return max(0, min(100, score))


def build_suggestions(profile: ProfileData) -> list[CompletenessSuggestion]:
    """Return prioritized suggestions (highest-weight unmet first)."""
    items = [
        CompletenessSuggestion(
            key=key,
            label=label,
            weight=weight,
            done=_is_satisfied(key, profile),
        )
        for key, label, weight in _WEIGHTS
    ]
    # Unmet first, then by descending weight (most impactful nudge on top).
    items.sort(key=lambda s: (s.done, -s.weight))
    return items


def compute_ats_readiness(profile: ProfileData) -> int:
    """Heuristic ATS-readiness score (0..100).

    Rewards the signals applicant-tracking systems parse well: contact details,
    a keyword-rich skills list, quantified/action bullets, clear dates, and
    education. Deterministic and explainable (no LLM).
    """
    score = 0
    ident = profile.identity
    if ident.email.strip() or ident.phone.strip():
        score += 15
    if ident.name.strip():
        score += 5
    skills = profile.skills
    n_skills = len(skills.technical) + len(skills.tools) + len(skills.languages)
    if n_skills >= 8:
        score += 25
    elif n_skills >= 4:
        score += 15
    elif n_skills >= 1:
        score += 8
    if profile.workExperience:
        score += 15
        if all(e.years for e in profile.workExperience):
            score += 10  # dates present → parseable timeline
        if any(len(e.description) >= 2 for e in profile.workExperience):
            score += 15  # substantive bullets
    if profile.education:
        score += 10
    if (profile.summary or ident.careerObjective).strip():
        score += 5
    return max(0, min(100, score))


def compute_ai_readiness(profile: ProfileData) -> int:
    """How much signal the AI assists have to work with (0..100).

    High when there is existing prose to improve (summary, rich bullets) and AI
    memory preferences are set — i.e. the AI layer can be genuinely useful
    without inventing anything.
    """
    score = 0
    if (profile.summary or profile.identity.careerObjective).strip():
        score += 25
    bulleted = sum(1 for e in profile.workExperience if e.description)
    score += min(35, bulleted * 12)
    if profile.personalProjects:
        score += 10
    mem = profile.aiMemory
    if mem.tone or mem.writingStyle:
        score += 15
    if mem.targetCompanies or mem.targetIndustries:
        score += 15
    return max(0, min(100, score))
