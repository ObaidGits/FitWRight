"""Concurrency and atomicity tests for durable tailoring confirmation."""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Application, Improvement, Outbox, Resume, ResumeVersion, User


async def _seed(db, user_id: str):
    resume = await db.create_resume(
        user_id,
        content='{"personalInfo":{"name":"Jane"}}',
        content_type="json",
        processed_data={"personalInfo": {"name": "Jane"}},
        processing_status="ready",
        filename="resume.json",
    )
    job = await db.create_job(user_id, content="Senior Python engineer")
    return resume, job


def _confirm_args(preview, resume, job, *, payload_hash="payload-hash"):
    improved = {"personalInfo": {"name": "Jane"}, "summary": "Python engineer"}
    return {
        "preview_id": preview["preview_id"],
        "resume_id": resume["resume_id"],
        "job_id": job["job_id"],
        "payload_hash": payload_hash,
        "improved_data": improved,
        "improved_text": "{}",
        "improvements": [{"suggestion": "Target Python"}],
        "cover_letter": None,
        "outreach_message": None,
        "interview_prep": None,
        "title": "Python Engineer",
    }


async def _new_user(db, email: str) -> str:
    user_id = str(uuid4())
    async with db.session_factory() as session:
        session.add(User(id=user_id, email=email, name="Other", role="user", status="active"))
        await session.commit()
    return user_id


async def _created_rows(db, user_id: str, source_id: str):
    async with db.session_factory() as session:
        tailored = list(
            (
                await session.execute(
                    select(Resume).where(
                        Resume.user_id == user_id, Resume.parent_id == source_id
                    )
                )
            ).scalars()
        )
        tailored_ids = {row.resume_id for row in tailored}
        improvements = list(
            (
                await session.execute(
                    select(Improvement).where(
                        Improvement.user_id == user_id,
                        Improvement.original_resume_id == source_id,
                    )
                )
            ).scalars()
        )
        versions = list(
            (
                await session.execute(
                    select(ResumeVersion).where(
                        ResumeVersion.user_id == user_id,
                        ResumeVersion.resume_id.in_(tailored_ids),
                    )
                )
            ).scalars()
        ) if tailored_ids else []
        applications = list(
            (
                await session.execute(
                    select(Application).where(
                        Application.user_id == user_id,
                        Application.master_resume_id == source_id,
                    )
                )
            ).scalars()
        )
        outbox = list(
            (await session.execute(select(Outbox).where(Outbox.user_id == user_id))).scalars()
        )
    related_events = [
        row
        for row in outbox
        if (row.payload or {}).get("node_id") in tailored_ids
        or (row.payload or {}).get("resume_id") in tailored_ids
        or any((row.payload or {}).get("node_id") == app.application_id for app in applications)
    ]
    return tailored, improvements, versions, applications, related_events


class TestTailorPreviewDurability:
    async def test_completed_result_is_recoverable_only_by_owner_and_request(
        self, isolated_db, owner_id
    ):
        resume, job = await _seed(isolated_db, owner_id)
        other_user = await _new_user(isolated_db, "recovery-other@example.com")
        payload = {"request_id": "client-request-1", "data": {"preview_id": "p"}}
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="hash",
            request_id="client-request-1",
            result_payload=payload,
        )

        assert await isolated_db.get_tailor_preview_result(
            owner_id, "client-request-1"
        ) == payload
        assert await isolated_db.get_tailor_preview_result(
            other_user, "client-request-1"
        ) is None
        assert await isolated_db.get_tailor_preview_result(owner_id, "unknown") is None
        assert preview is not None

    async def test_cleanup_is_bounded_idempotent_and_preserves_live_previews(
        self, isolated_db, owner_id
    ):
        resume, job = await _seed(isolated_db, owner_id)
        expired = []
        for index in range(3):
            expired.append(
                await isolated_db.create_tailor_preview(
                    owner_id,
                    resume_id=resume["resume_id"],
                    job_id=job["job_id"],
                    prompt_id="keywords",
                    payload_hash=f"expired-{index}",
                    ttl_seconds=-1,
                )
            )
        live = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="live",
            ttl_seconds=3600,
        )

        assert await isolated_db.prune_expired_tailor_previews(batch_size=2) == 2
        remaining_expired = [
            await isolated_db.get_tailor_preview(owner_id, row["preview_id"])
            for row in expired
        ]
        assert sum(row is not None for row in remaining_expired) == 1
        assert await isolated_db.get_tailor_preview(owner_id, live["preview_id"])
        assert await isolated_db.prune_expired_tailor_previews(batch_size=2) == 1
        assert await isolated_db.prune_expired_tailor_previews(batch_size=2) == 0
        assert await isolated_db.get_tailor_preview(owner_id, live["preview_id"])

    async def test_concurrent_previews_coexist(self, isolated_db, owner_id):
        resume, job = await _seed(isolated_db, owner_id)

        previews = await asyncio.gather(
            *[
                isolated_db.create_tailor_preview(
                    owner_id,
                    resume_id=resume["resume_id"],
                    job_id=job["job_id"],
                    prompt_id="keywords",
                    payload_hash=f"hash-{index}",
                )
                for index in range(4)
            ]
        )

        assert all(previews)
        assert len({preview["preview_id"] for preview in previews}) == 4
        assert len({preview["request_id"] for preview in previews}) == 4
        for preview in previews:
            assert await isolated_db.get_tailor_preview(owner_id, preview["preview_id"])

    async def test_confirmation_requires_matching_owner_resume_job_and_hash(
        self, isolated_db, owner_id
    ):
        resume, job = await _seed(isolated_db, owner_id)
        other_resume, other_job = await _seed(isolated_db, owner_id)
        other_user = await _new_user(isolated_db, "other-preview@example.com")
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="payload-hash",
        )

        status, _ = await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, other_resume, job)
        )
        assert status == "invalid_preview"
        status, _ = await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, other_job)
        )
        assert status == "invalid_preview"
        status, _ = await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, job, payload_hash="wrong")
        )
        assert status == "invalid_preview"
        status, _ = await isolated_db.confirm_tailor_preview(
            other_user, **_confirm_args(preview, resume, job)
        )
        assert status == "not_found"

        status, created = await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, job)
        )
        assert status == "created"
        assert created is not None

    async def test_replay_and_expired_preview_are_rejected(self, isolated_db, owner_id):
        resume, job = await _seed(isolated_db, owner_id)
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="payload-hash",
        )
        assert (await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, job)
        ))[0] == "created"
        assert (await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, job)
        ))[0] == "invalid_preview"

        expired = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="expired-hash",
            ttl_seconds=0,
        )
        assert (await isolated_db.confirm_tailor_preview(
            owner_id,
            **_confirm_args(expired, resume, job, payload_hash="expired-hash"),
        ))[0] == "invalid_preview"

    async def test_concurrent_confirm_has_exactly_one_winner(self, isolated_db, owner_id):
        resume, job = await _seed(isolated_db, owner_id)
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="payload-hash",
        )

        results = await asyncio.gather(
            *[
                isolated_db.confirm_tailor_preview(
                    owner_id, **_confirm_args(preview, resume, job)
                )
                for _ in range(5)
            ]
        )

        assert [status for status, _ in results].count("created") == 1
        assert [status for status, _ in results].count("invalid_preview") == 4
        rows = await _created_rows(isolated_db, owner_id, resume["resume_id"])
        assert [len(group) for group in rows[:4]] == [1, 1, 1, 1]

    async def test_fault_rolls_back_consume_and_all_partial_rows(
        self, isolated_db, owner_id, monkeypatch
    ):
        resume, job = await _seed(isolated_db, owner_id)
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="payload-hash",
        )
        monkeypatch.setattr(
            isolated_db,
            "_adjust_user_counter",
            AsyncMock(side_effect=RuntimeError("fault after inserts")),
        )

        with pytest.raises(RuntimeError, match="fault after inserts"):
            await isolated_db.confirm_tailor_preview(
                owner_id, **_confirm_args(preview, resume, job)
            )

        stored = await isolated_db.get_tailor_preview(owner_id, preview["preview_id"])
        assert stored["consumed_at"] is None
        rows = await _created_rows(isolated_db, owner_id, resume["resume_id"])
        assert [len(group) for group in rows] == [0, 0, 0, 0, 0]

    async def test_success_creates_exactly_one_of_each_required_row(
        self, isolated_db, owner_id
    ):
        resume, job = await _seed(isolated_db, owner_id)
        preview = await isolated_db.create_tailor_preview(
            owner_id,
            resume_id=resume["resume_id"],
            job_id=job["job_id"],
            prompt_id="keywords",
            payload_hash="payload-hash",
        )

        status, created = await isolated_db.confirm_tailor_preview(
            owner_id, **_confirm_args(preview, resume, job)
        )
        assert status == "created"
        assert created is not None
        rows = await _created_rows(isolated_db, owner_id, resume["resume_id"])
        tailored, improvements, versions, applications, events = rows
        assert [len(group) for group in rows[:4]] == [1, 1, 1, 1]
        assert tailored[0].resume_id == created["resume_id"]
        assert improvements[0].tailored_resume_id == created["resume_id"]
        assert versions[0].source == "ai"
        assert applications[0].resume_id == created["resume_id"]
        assert sorted(event.event_type for event in events) == [
            "ai.generation_done",
            "application.upserted",
            "resume.upserted",
        ]
        stored = await isolated_db.get_tailor_preview(owner_id, preview["preview_id"])
        assert stored["consumed_at"] is not None
