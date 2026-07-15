"""Tests for the deterministic wizard scoring engine (W-P2.3)."""

from app.schemas.models import ResumeData
from app.services.resume_score import (
    compute_ats,
    compute_completeness,
    compute_resume_scores,
    compute_section_confidence,
)


def _rich_resume() -> ResumeData:
    return ResumeData.model_validate(
        {
            "personalInfo": {
                "name": "Jane Doe",
                "title": "Engineer",
                "email": "jane@example.com",
                "phone": "555",
                "location": "NYC",
                "linkedin": "linkedin.com/in/jane",
            },
            "summary": "A" * 130,
            "workExperience": [
                {
                    "title": "Eng",
                    "company": "Acme",
                    "years": "2021 - Present",
                    "description": ["Shipped billing", "Cut latency 40%"],
                }
            ],
            "education": [{"institution": "MIT", "degree": "BSc"}],
            "personalProjects": [{"name": "Alpha"}],
            "additional": {
                "technicalSkills": ["Py", "SQL", "Go", "AWS", "React", "TS", "K8s", "Docker"]
            },
        }
    )


def test_completeness_empty_is_zero_and_full_is_100() -> None:
    assert compute_completeness(ResumeData()) == 0
    assert compute_completeness(_rich_resume()) == 100


def test_completeness_is_monotonic_as_fields_fill() -> None:
    data = ResumeData()
    scores = [compute_completeness(data)]
    data.personalInfo.name = "Jane"
    scores.append(compute_completeness(data))
    data.additional.technicalSkills = ["Python"]
    scores.append(compute_completeness(data))
    assert scores == sorted(scores)  # never decreases
    assert scores[0] == 0 and scores[-1] > scores[0]


def test_ats_rewards_contact_skills_bullets_dates() -> None:
    assert compute_ats(ResumeData()) == 0
    assert compute_ats(_rich_resume()) == 100


def test_ats_bounded_0_100() -> None:
    score = compute_ats(_rich_resume())
    assert 0 <= score <= 100


def test_section_confidence_levels() -> None:
    confidence = {c.section: c.level for c in compute_section_confidence(ResumeData())}
    assert confidence["identity"] == "missing"
    assert confidence["skills"] == "missing"

    rich = {c.section: c.level for c in compute_section_confidence(_rich_resume())}
    assert rich["identity"] == "strong"  # name + title
    assert rich["experience"] == "strong"  # 2 bullets
    assert rich["skills"] == "strong"  # 8 skills
    assert rich["education"] == "strong"  # institution + degree


def test_section_confidence_weak_and_fair_bands() -> None:
    data = ResumeData()
    data.additional.technicalSkills = ["Python", "SQL"]  # 2 -> weak
    weak = {c.section: c.level for c in compute_section_confidence(data)}
    assert weak["skills"] == "weak"

    data.additional.technicalSkills = ["Python", "SQL", "Go", "AWS"]  # 4 -> fair
    fair = {c.section: c.level for c in compute_section_confidence(data)}
    assert fair["skills"] == "fair"


def test_compute_resume_scores_bundle() -> None:
    scores = compute_resume_scores(_rich_resume())
    assert scores.completeness == 100
    assert scores.ats == 100
    assert {c.section for c in scores.sections} == {
        "identity",
        "contact",
        "experience",
        "education",
        "skills",
        "summary",
    }
