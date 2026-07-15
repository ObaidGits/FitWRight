"""Backward-compat + new-field tests for the P2 resume schema extensions."""

from app.schemas.models import Education, Experience, Project, ResumeData


def test_education_legacy_shape_still_validates() -> None:
    # Pre-P2 education (only the original four fields) must validate unchanged.
    edu = Education.model_validate(
        {"id": 1, "institution": "MIT", "degree": "BSc", "years": "2019 - 2023"}
    )
    assert edu.institution == "MIT"
    # New fields default safely.
    assert edu.specialization == ""
    assert edu.currentlyStudying is False
    assert edu.gradeType is None
    assert edu.achievements == []


def test_education_new_fields_round_trip() -> None:
    edu = Education.model_validate(
        {
            "institution": "MIT",
            "degree": "BSc",
            "specialization": "ML",
            "location": "Cambridge",
            "startYear": "2019",
            "endYear": "2023",
            "currentlyStudying": False,
            "gradeType": "gpa",
            "score": "3.9",
            "achievements": ["Dean's List", "Valedictorian"],
        }
    )
    dumped = edu.model_dump()
    assert dumped["gradeType"] == "gpa"
    assert dumped["achievements"] == ["Dean's List", "Valedictorian"]


def test_education_rejects_invalid_grade_type() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Education.model_validate({"gradeType": "letters"})


def test_experience_and_project_new_fields_default_and_coerce() -> None:
    exp = Experience.model_validate(
        {"title": "Eng", "company": "Acme", "tech": "Python, but as a string"}
    )
    assert exp.current is False
    # tech uses the shared string-list coercion.
    assert isinstance(exp.tech, list)

    proj = Project.model_validate({"name": "Alpha", "tech": ["Rust", "WASM"]})
    assert proj.tech == ["Rust", "WASM"]


def test_full_resume_with_new_fields_normalizes() -> None:
    data = ResumeData.model_validate(
        {
            "personalInfo": {"name": "Jane"},
            "education": [
                {"institution": "MIT", "degree": "BSc", "gradeType": "cgpa", "score": "9.1"}
            ],
            "workExperience": [{"title": "Eng", "company": "Acme", "current": True}],
        }
    )
    assert data.education[0].gradeType == "cgpa"
    assert data.workExperience[0].current is True
