"""Integration tests for the adaptive resume wizard endpoints."""

import json
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.schemas.resume_wizard import ResumeWizardHistoryEntry, ResumeWizardQuestion
from app.services.resume_wizard import (
    RESUME_WIZARD_MAX_QUESTIONS,
    build_initial_wizard_state,
)

_AI_RESULT = {
    "resume_data": {
        "personalInfo": {"name": "James"},
        "summary": "",
        "workExperience": [],
        "education": [],
        "personalProjects": [],
        "additional": {
            "technicalSkills": ["Python"],
            "languages": [],
            "certificationsTraining": [],
            "awards": [],
        },
        "sectionMeta": [],
        "customSections": {},
    },
    "next_question": {"text": "What tools do you use most?", "section": "skills"},
    "inferred_skills": ["FastAPI"],
    "is_complete": False,
}


async def test_turn_answer_runs_ai_and_returns_next_question(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question.section = "skills"

    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
        return_value=_AI_RESULT,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "answer",
                    "answer": {"text": "I use Python and FastAPI."},
                },
            )

    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["current_question"]["text"] == "What tools do you use most?"
    # W-P1.2: inferred skills are NOT auto-merged into the resume; only the
    # model's explicit resume_data skills are kept, and inferred skills are
    # surfaced separately as confirmable suggestions.
    assert payload["resume_data"]["additional"]["technicalSkills"] == ["Python"]
    assert payload["inferred_skills"] == ["FastAPI"]
    assert payload["asked_count"] == 1


async def test_turn_review_needs_no_llm(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.resume_data.personalInfo.name = "James"

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "review"},
        )

    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["step"] == "review"
    assert payload["warnings"]


async def test_turn_answer_without_answer_is_422(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "answer"},
        )
    assert response.status_code == 422


async def test_finalize_creates_ready_master_resume(isolated_db, owner_id) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.resume_data.personalInfo.name = "James"
    state.resume_data.personalInfo.email = "james@example.com"
    state.resume_data.additional.technicalSkills = ["Python"]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/finalize",
            json={"state": state.model_dump(mode="json")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_status"] == "ready"
    assert payload["is_master"] is True

    stored = await isolated_db.get_resume(owner_id, payload["resume_id"])
    assert stored is not None
    assert stored["is_master"] is True
    assert stored["content_type"] == "json"
    assert json.loads(stored["content"])["personalInfo"]["name"] == "James"


async def test_finalize_saves_regular_resume_when_master_exists(
    isolated_db, owner_id, sample_resume
) -> None:
    # When a master already exists, saving from the wizard must NOT fail - it
    # saves a regular (non-master) resume and never replaces the master.
    await isolated_db.create_resume(
        owner_id,
        content=json.dumps(sample_resume),
        content_type="json",
        filename="existing.json",
        is_master=True,
        processed_data=sample_resume,
        processing_status="ready",
    )
    state = build_initial_wizard_state()
    state.resume_data.personalInfo.name = "James"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/finalize",
            json={"state": state.model_dump(mode="json"), "is_master": True},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_master"] is False  # existing master is NOT replaced
    assert payload["message"] == "Resume saved."
    stored = await isolated_db.get_resume(owner_id, payload["resume_id"])
    assert stored["is_master"] is False
    # The original master is untouched.
    master = await isolated_db.get_master_resume(owner_id)
    assert master["filename"] == "existing.json"


async def test_finalize_saves_non_master_when_requested(isolated_db, owner_id) -> None:
    # is_master=False must save a regular resume even when no master exists yet.
    state = build_initial_wizard_state()
    state.resume_data.personalInfo.name = "James"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/finalize",
            json={"state": state.model_dump(mode="json"), "is_master": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_master"] is False
    assert payload["message"] == "Resume saved."
    assert await isolated_db.get_master_resume(owner_id) is None  # no master created


async def test_turn_start_returns_initial_state(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "start"},
        )

    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["step"] == "intro"
    assert payload["current_question"]["section"] == "intro"
    assert payload["asked_count"] == 0


async def test_turn_back_restores_previous_question(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.asked_count = 1
    state.current_question = ResumeWizardQuestion(text="Skills?", section="skills")
    state.resume_data.additional.technicalSkills = ["Python"]
    state.history = [
        ResumeWizardHistoryEntry(
            question="Where have you worked?",
            answer="Acme",
            section="workExperience",
            resume_data_before=build_initial_wizard_state().resume_data,
        )
    ]

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "back"},
        )

    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["asked_count"] == 0
    assert payload["current_question"]["section"] == "workExperience"
    # Non-destructive Back (W-P0.1): the current merged data is KEPT and the
    # previous answer is returned so the client can repopulate the input.
    assert payload["resume_data"]["additional"]["technicalSkills"] == ["Python"]
    assert payload["restored_answer"] == "Acme"


async def test_turn_skip_advances_without_llm_or_modifying_resume_data(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question = ResumeWizardQuestion(text="Education?", section="education")

    # W-P0.4: skip is deterministic - it must make ZERO LLM calls.
    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
    ) as mock_complete:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={"state": state.model_dump(mode="json"), "action": "skip"},
            )

    assert response.status_code == 200
    mock_complete.assert_not_awaited()  # no tokens spent on a skip
    payload = response.json()["state"]
    # Next gap for an otherwise-empty resume is workExperience.
    assert payload["current_question"]["section"] == "workExperience"
    assert payload["resume_data"]["education"] == []  # skip must not add data
    assert payload["asked_count"] == 1


async def test_turn_structured_identity_merges_without_llm(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()  # section intro

    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
    ) as mock_complete:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "structured",
                    "structured": {
                        "personal_info": {"name": "Jane Doe", "title": "Senior Engineer"},
                        "next_section": "contact",
                    },
                },
            )

    assert response.status_code == 200
    mock_complete.assert_not_awaited()  # structured turns spend no tokens
    payload = response.json()["state"]
    assert payload["resume_data"]["personalInfo"]["name"] == "Jane Doe"
    assert payload["resume_data"]["personalInfo"]["title"] == "Senior Engineer"
    assert payload["current_question"]["section"] == "contact"
    assert payload["asked_count"] == 1


async def test_turn_structured_skills_replaces_list(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question = ResumeWizardQuestion(text="Skills?", section="skills")
    state.resume_data.personalInfo.name = "Jane"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={
                "state": state.model_dump(mode="json"),
                "action": "structured",
                "structured": {"technical_skills": ["Python", "python", "SQL"]},
            },
        )

    assert response.status_code == 200
    payload = response.json()["state"]
    # Deduped case-insensitively, order preserved.
    assert payload["resume_data"]["additional"]["technicalSkills"] == ["Python", "SQL"]


async def test_turn_structured_education_appends_without_llm(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question = ResumeWizardQuestion(text="Education?", section="education")
    state.resume_data.personalInfo.name = "Jane"

    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
    ) as mock_complete:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "structured",
                    "structured": {
                        "education": {
                            "institution": "MIT",
                            "degree": "BSc CS",
                            "gradeType": "gpa",
                            "score": "3.9",
                        }
                    },
                },
            )

    assert response.status_code == 200
    mock_complete.assert_not_awaited()
    payload = response.json()["state"]
    assert payload["resume_data"]["education"][0]["institution"] == "MIT"
    assert payload["resume_data"]["education"][0]["gradeType"] == "gpa"
    # Live scores are surfaced in the state (W-P2.3).
    assert "scores" in payload
    assert payload["scores"]["completeness"] > 0


async def test_turn_start_prefills_from_existing_profile(isolated_db, owner_id) -> None:
    # W-P3.2: a returning user with a profile (and no master) starts prefilled.
    from app.profile.schemas import ProfileData

    profile = ProfileData.model_validate(
        {
            "identity": {"name": "Jane Doe", "headline": "Backend Engineer"},
            "workExperience": [
                {"title": "Eng", "company": "Acme", "years": "2021", "description": ["Shipped"]}
            ],
        }
    )
    await isolated_db.create_profile(
        owner_id, data=profile.model_dump(mode="json"), completeness=40
    )

    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "start"},
        )

    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["resume_data"]["personalInfo"]["name"] == "Jane Doe"
    assert payload["resume_data"]["workExperience"][0]["company"] == "Acme"
    # Professional persona with experience present -> jumps to the education gap.
    assert payload["current_question"]["section"] == "education"


async def test_turn_start_empty_without_profile(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "start"},
        )
    assert response.status_code == 200
    payload = response.json()["state"]
    assert payload["step"] == "intro"
    assert payload["resume_data"]["personalInfo"]["name"] == ""


async def test_finalize_backfills_canonical_profile(isolated_db, owner_id) -> None:
    # W-P3.1: finalizing the wizard derives the canonical profile from the new
    # master resume (the wizard becomes the on-ramp to the profile spine).
    assert await isolated_db.get_profile(owner_id) is None  # none yet

    state = build_initial_wizard_state()
    state.resume_data.personalInfo.name = "Jane Doe"
    state.resume_data.personalInfo.email = "jane@example.com"
    state.resume_data.additional.technicalSkills = ["Python"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/finalize",
            json={"state": state.model_dump(mode="json")},
        )

    assert response.status_code == 200
    profile = await isolated_db.get_profile(owner_id)
    assert profile is not None
    assert profile["data"]["identity"]["name"] == "Jane Doe"


async def test_turn_structured_experience_appends_without_llm(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question = ResumeWizardQuestion(text="Experience?", section="workExperience")
    state.resume_data.personalInfo.name = "Jane"

    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
    ) as mock_complete:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "structured",
                    "structured": {
                        "experiences": [
                            {
                                "title": "Full Stack Engineer Intern",
                                "company": "TechStax",
                                "location": "Remote",
                                "years": "Jul 2025 - Jan 2026",
                                "description": ["Built FastAPI services"],
                            }
                        ]
                    },
                },
            )

    assert response.status_code == 200
    mock_complete.assert_not_awaited()  # structured entry spends no tokens
    payload = response.json()["state"]
    assert payload["resume_data"]["workExperience"][0]["company"] == "TechStax"


async def test_assist_draft_bullets(isolated_db) -> None:
    transport = ASGITransport(app=app)
    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
        return_value={"bullets": ["Built FastAPI services", "Cut latency 30%"]},
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/assist",
                json={
                    "kind": "draft_bullets",
                    "section": "workExperience",
                    "title": "Engineer",
                    "company": "TechStax",
                    "text": "I built backend APIs and reduced latency.",
                },
            )
    assert response.status_code == 200
    assert response.json()["bullets"] == ["Built FastAPI services", "Cut latency 30%"]


async def test_assist_parse_entries(isolated_db) -> None:
    transport = ASGITransport(app=app)
    parsed = {
        "entries": [
            {
                "title": "Full Stack Engineer Intern",
                "company": "TechStax",
                "location": "Remote",
                "years": "Jul 2025 - Jan 2026",
                "description": ["Engineered backend APIs"],
            }
        ]
    }
    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
        return_value=parsed,
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/assist",
                json={
                    "kind": "parse_entries",
                    "section": "workExperience",
                    "text": "TechStax\nRemote\nFull Stack Engineer Intern\nJul 2025 - Jan 2026",
                },
            )
    assert response.status_code == 200
    entries = response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["company"] == "TechStax"
    assert entries[0]["description"] == ["Engineered backend APIs"]


async def test_assist_rejects_non_experience_section(isolated_db) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/assist",
            json={"kind": "draft_bullets", "section": "skills", "text": "x"},
        )
    assert response.status_code == 422


async def test_turn_structured_requires_payload(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resume-wizard/turn",
            json={"state": state.model_dump(mode="json"), "action": "structured"},
        )
    assert response.status_code == 422


async def test_deterministic_actions_do_not_consume_llm_rate_limit(isolated_db) -> None:
    # Only the `answer` action calls the LLM, so only it should enforce the LLM
    # rate limit. Deterministic turns (start/back/skip/review/structured) must NOT
    # - otherwise the prefill `start` fired on every mount would burn LLM budget.
    transport = ASGITransport(app=app)
    base = build_initial_wizard_state()
    base.step = "question"
    base.current_question = ResumeWizardQuestion(text="Skills?", section="skills")
    base.resume_data.personalInfo.name = "Jane"
    base.history = [
        ResumeWizardHistoryEntry(
            question="?", answer="x", section="workExperience",
            resume_data_before=build_initial_wizard_state().resume_data,
        )
    ]

    deterministic = [
        {"action": "start"},
        {"action": "back"},
        {"action": "review"},
        {"action": "skip"},
        {"action": "structured", "structured": {"technical_skills": ["Python"]}},
    ]
    with patch(
        "app.routers.resume_wizard.enforce_llm_rate_limit",
        new_callable=AsyncMock,
    ) as mock_rl:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for payload in deterministic:
                resp = await client.post(
                    "/api/v1/resume-wizard/turn",
                    json={"state": base.model_dump(mode="json"), **payload},
                )
                assert resp.status_code == 200, payload
        mock_rl.assert_not_awaited()  # no LLM budget spent on deterministic turns


async def test_answer_action_enforces_llm_rate_limit(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question.section = "skills"

    with (
        patch(
            "app.routers.resume_wizard.enforce_llm_rate_limit",
            new_callable=AsyncMock,
        ) as mock_rl,
        patch(
            "app.services.resume_wizard.complete_json",
            new_callable=AsyncMock,
            return_value=_AI_RESULT,
        ),
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "answer",
                    "answer": {"text": "I use Python."},
                },
            )
    assert resp.status_code == 200
    mock_rl.assert_awaited_once()  # the LLM path is rate-limited


async def test_turn_answer_past_cap_routes_to_review_without_llm(isolated_db) -> None:
    transport = ASGITransport(app=app)
    state = build_initial_wizard_state()
    state.step = "question"
    state.current_question.section = "skills"
    state.asked_count = RESUME_WIZARD_MAX_QUESTIONS  # at the cap

    with patch(
        "app.services.resume_wizard.complete_json",
        new_callable=AsyncMock,
    ) as mock_complete:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/v1/resume-wizard/turn",
                json={
                    "state": state.model_dump(mode="json"),
                    "action": "answer",
                    "answer": {"text": "one more thing"},
                },
            )

    assert response.status_code == 200
    assert response.json()["state"]["step"] == "review"
    mock_complete.assert_not_awaited()  # cap guard must skip the LLM call
