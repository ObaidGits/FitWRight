"""Deterministic resume scoring for the wizard (W-P2.3).

Pure, explainable functions that score a ``ResumeData`` draft - completeness,
ATS readiness, and per-section confidence - mirroring the weight philosophy of
``app/profile/completion.py`` but operating directly on the resume schema the
wizard produces (no ``ProfileData`` dependency, no LLM). Surfaced live so the
wizard can show a quality score instead of an opaque step count.
"""

from __future__ import annotations

from app.schemas.models import ResumeData
from app.schemas.resume_wizard import ResumeScores, ResumeSectionConfidence

# (key, weight) - weights sum to 100, ordered by importance.
_COMPLETENESS_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("name", 8),
    ("title", 8),
    ("summary", 12),
    ("contact", 10),
    ("location", 4),
    ("experience", 18),
    ("experience_detail", 10),
    ("education", 8),
    ("skills", 12),
    ("projects", 5),
    ("links", 5),
)


def _has_contact(data: ResumeData) -> bool:
    info = data.personalInfo
    return bool(info.email.strip() or info.phone.strip())


def _has_links(data: ResumeData) -> bool:
    info = data.personalInfo
    return bool((info.linkedin or "").strip() or (info.github or "").strip() or (info.website or "").strip())


def _completeness_satisfied(key: str, data: ResumeData) -> bool:
    info = data.personalInfo
    if key == "name":
        return bool(info.name.strip())
    if key == "title":
        return bool(info.title.strip())
    if key == "summary":
        return bool(data.summary.strip())
    if key == "contact":
        return _has_contact(data)
    if key == "location":
        return bool(info.location.strip())
    if key == "experience":
        return len(data.workExperience) > 0
    if key == "experience_detail":
        return any(exp.description for exp in data.workExperience)
    if key == "education":
        return len(data.education) > 0
    if key == "skills":
        return len(data.additional.technicalSkills) > 0
    if key == "projects":
        return len(data.personalProjects) > 0
    if key == "links":
        return _has_links(data)
    return False


def compute_completeness(data: ResumeData) -> int:
    """Weighted completeness score 0..100."""
    score = sum(
        weight for key, weight in _COMPLETENESS_WEIGHTS if _completeness_satisfied(key, data)
    )
    return max(0, min(100, score))


def compute_ats(data: ResumeData) -> int:
    """Heuristic ATS-readiness 0..100 (contact, keyworded skills, bullets, dates)."""
    score = 0
    info = data.personalInfo
    if _has_contact(data):
        score += 15
    if info.name.strip():
        score += 5
    n_skills = len(data.additional.technicalSkills)
    if n_skills >= 8:
        score += 25
    elif n_skills >= 4:
        score += 15
    elif n_skills >= 1:
        score += 8
    if data.workExperience:
        score += 15
        if all(e.years.strip() or e.current for e in data.workExperience):
            score += 10  # parseable timeline
        if any(len(e.description) >= 2 for e in data.workExperience):
            score += 15  # substantive bullets
    if data.education:
        score += 10
    if data.summary.strip():
        score += 5
    return max(0, min(100, score))


def _confidence_identity(data: ResumeData) -> str:
    info = data.personalInfo
    if not info.name.strip():
        return "missing"
    return "strong" if info.title.strip() else "fair"


def _confidence_contact(data: ResumeData) -> str:
    info = data.personalInfo
    methods = sum(
        1
        for v in (info.email, info.phone, info.linkedin or "", info.github or "", info.website or "")
        if v.strip()
    )
    if methods == 0:
        return "missing"
    if methods == 1:
        return "weak"
    return "strong"


def _confidence_experience(data: ResumeData) -> str:
    if not data.workExperience:
        return "missing"
    if any(len(e.description) >= 2 for e in data.workExperience):
        return "strong"
    if any(e.description for e in data.workExperience):
        return "fair"
    return "weak"


def _confidence_education(data: ResumeData) -> str:
    if not data.education:
        return "missing"
    if any(e.institution.strip() and e.degree.strip() for e in data.education):
        return "strong"
    return "fair"


def _confidence_skills(data: ResumeData) -> str:
    n = len(data.additional.technicalSkills)
    if n == 0:
        return "missing"
    if n <= 2:
        return "weak"
    if n < 8:
        return "fair"
    return "strong"


def _confidence_summary(data: ResumeData) -> str:
    text = data.summary.strip()
    if not text:
        return "missing"
    return "strong" if len(text) >= 120 else "fair"


def compute_section_confidence(data: ResumeData) -> list[ResumeSectionConfidence]:
    """Per-section quality label (missing/weak/fair/strong)."""
    return [
        ResumeSectionConfidence(section="identity", level=_confidence_identity(data)),
        ResumeSectionConfidence(section="contact", level=_confidence_contact(data)),
        ResumeSectionConfidence(section="experience", level=_confidence_experience(data)),
        ResumeSectionConfidence(section="education", level=_confidence_education(data)),
        ResumeSectionConfidence(section="skills", level=_confidence_skills(data)),
        ResumeSectionConfidence(section="summary", level=_confidence_summary(data)),
    ]


def compute_resume_scores(data: ResumeData) -> ResumeScores:
    """Bundle all deterministic scores for the wizard state."""
    return ResumeScores(
        completeness=compute_completeness(data),
        ats=compute_ats(data),
        sections=compute_section_confidence(data),
    )
