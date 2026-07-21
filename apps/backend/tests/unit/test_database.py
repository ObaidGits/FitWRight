"""Tests for the real SQLAlchemy/SQLite layer (app.database.Database).

Every integration test mocks `db`, so the actual persistence layer was barely
exercised. These run a real SQLite database against a temp file, so CRUD,
master-resume assignment, the jobs ``metadata_json`` round-trip, applications,
and stats are verified end-to-end on the storage.

Since Task 3, every owned method takes a mandatory ``user_id`` and is scoped to
it (ADR-4, R10.2). These tests thread a real owner user through the facade and
add cross-user isolation coverage (Property 1), the per-user single-master
invariant (R10.4/Property 2), and per-user api-key isolation (R10.6).
"""

from uuid import uuid4

import pytest

from app.database import Database
from app.db_engine import init_models_sync, make_sync_engine
from app.models import User


async def _make_user(database: Database, email: str) -> str:
    """Insert a minimal active user and return its id (satisfies the FK)."""
    user_id = str(uuid4())
    async with database.session_factory() as session:
        session.add(
            User(
                id=user_id,
                email=email,
                name="Test User",
                role="user",
                status="active",
            )
        )
        await session.commit()
    return user_id


@pytest.fixture
async def db(tmp_path):
    database = Database(db_path=tmp_path / "test_db.db")
    yield database
    await database.close()


@pytest.fixture
async def uid(db):
    """A real owner user id threaded through the scoped facade methods."""
    return await _make_user(db, "owner@example.com")


@pytest.fixture
async def other_uid(db):
    """A second user id used to prove cross-user isolation (Property 1)."""
    return await _make_user(db, "other@example.com")


class TestResumeCrud:
    async def test_create_and_get(self, db, uid):
        created = await db.create_resume(uid, content="# Resume", filename="r.pdf")
        assert created["resume_id"]
        fetched = await db.get_resume(uid, created["resume_id"])
        assert fetched is not None
        assert fetched["content"] == "# Resume"
        assert fetched["filename"] == "r.pdf"

    async def test_get_missing_returns_none(self, db, uid):
        assert await db.get_resume(uid, "does-not-exist") is None

    async def test_list_resumes(self, db, uid):
        await db.create_resume(uid, content="a")
        await db.create_resume(uid, content="b")
        assert len(await db.list_resumes(uid)) == 2

    async def test_update_resume_changes_field_and_timestamp(self, db, uid):
        created = await db.create_resume(uid, content="x")
        updated = await db.update_resume(uid, created["resume_id"], {"title": "New Title"})
        assert updated["title"] == "New Title"
        assert updated["updated_at"] >= created["updated_at"]

    async def test_update_missing_raises(self, db, uid):
        with pytest.raises(ValueError):
            await db.update_resume(uid, "missing", {"title": "X"})

    async def test_delete_resume(self, db, uid):
        created = await db.create_resume(uid, content="x")
        assert await db.delete_resume(uid, created["resume_id"]) is True
        assert await db.get_resume(uid, created["resume_id"]) is None

    async def test_delete_missing_returns_false(self, db, uid):
        assert await db.delete_resume(uid, "missing") is False

    async def test_original_markdown_absence_semantics(self, db, uid):
        # Omitted when None (preserve TinyDB behavior); present when supplied.
        without = await db.create_resume(uid, content="x")
        assert "original_markdown" not in without
        with_md = await db.create_resume(uid, content="x", original_markdown="# raw")
        fetched = await db.get_resume(uid, with_md["resume_id"])
        assert fetched["original_markdown"] == "# raw"

    async def test_interview_prep_round_trips_as_text(self, db, uid):
        created = await db.create_resume(
            uid,
            content="x",
            interview_prep='{"role_fit_analysis":["fit"]}',
        )
        fetched = await db.get_resume(uid, created["resume_id"])
        assert fetched["interview_prep"] == '{"role_fit_analysis":["fit"]}'

    def test_interview_prep_migration_is_idempotent(self, tmp_path):
        engine = make_sync_engine(tmp_path / "old.db")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql(
                    """
                    CREATE TABLE resumes (
                        resume_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        content_type TEXT DEFAULT 'md'
                    )
                    """
                )

            init_models_sync(engine)
            init_models_sync(engine)

            with engine.begin() as conn:
                columns = conn.exec_driver_sql("PRAGMA table_info(resumes)").mappings().all()
            names = [column["name"] for column in columns]
            assert names.count("interview_prep") == 1
        finally:
            engine.dispose()


class TestMasterResume:
    async def test_no_master_initially(self, db, uid):
        assert await db.get_master_resume(uid) is None

    async def test_set_master_unsets_previous(self, db, uid):
        r1 = await db.create_resume(uid, content="1")
        r2 = await db.create_resume(uid, content="2")

        assert await db.set_master_resume(uid, r1["resume_id"]) is True
        assert (await db.get_master_resume(uid))["resume_id"] == r1["resume_id"]

        assert await db.set_master_resume(uid, r2["resume_id"]) is True
        master = await db.get_master_resume(uid)
        assert master["resume_id"] == r2["resume_id"]
        # Only one master at a time.
        assert sum(1 for r in await db.list_resumes(uid) if r["is_master"]) == 1

    async def test_set_master_missing_returns_false(self, db, uid):
        assert await db.set_master_resume(uid, "missing") is False

    async def test_atomic_first_upload_becomes_master(self, db, uid):
        created = await db.create_resume_atomic_master(uid, content="first", processing_status="ready")
        assert created["is_master"] is True

    async def test_atomic_second_upload_not_master(self, db, uid):
        await db.create_resume_atomic_master(uid, content="first", processing_status="ready")
        second = await db.create_resume_atomic_master(uid, content="second", processing_status="ready")
        assert second["is_master"] is False

    async def test_atomic_recovers_when_master_stuck(self, db, uid):
        # Master stuck in "failed" -> next upload is promoted to master.
        first = await db.create_resume_atomic_master(uid, content="first", processing_status="failed")
        assert first["is_master"] is True
        second = await db.create_resume_atomic_master(uid, content="second", processing_status="ready")
        assert second["is_master"] is True
        assert (await db.get_master_resume(uid))["resume_id"] == second["resume_id"]

    async def test_single_master_is_per_user(self, db, uid, other_uid):
        """The single-master invariant is PER USER (R10.4, Property 2).

        Each user may hold their own master resume simultaneously without
        colliding on the partial unique ``(user_id, is_master)`` index.
        """
        a = await db.create_resume_atomic_master(uid, content="a", processing_status="ready")
        b = await db.create_resume_atomic_master(other_uid, content="b", processing_status="ready")
        assert a["is_master"] is True
        assert b["is_master"] is True
        # Each user sees only their own master.
        assert (await db.get_master_resume(uid))["resume_id"] == a["resume_id"]
        assert (await db.get_master_resume(other_uid))["resume_id"] == b["resume_id"]


class TestJobs:
    async def test_create_and_get_job(self, db, uid):
        created = await db.create_job(uid, content="Engineer role", resume_id="r1")
        fetched = await db.get_job(uid, created["job_id"])
        assert fetched["content"] == "Engineer role"
        assert fetched["resume_id"] == "r1"

    async def test_get_missing_job_returns_none(self, db, uid):
        assert await db.get_job(uid, "missing") is None

    async def test_update_job(self, db, uid):
        created = await db.create_job(uid, content="old")
        updated = await db.update_job(uid, created["job_id"], {"content": "new"})
        assert updated["content"] == "new"

    async def test_update_missing_job_returns_none(self, db, uid):
        assert await db.update_job(uid, "missing", {"content": "x"}) is None

    async def test_dynamic_fields_round_trip_as_top_level(self, db, uid):
        """Dynamic pipeline fields must survive write->read as top-level keys.

        This is the highest-risk migration detail: ``/improve/confirm`` rejects
        with 400 if ``preview_hash``/``preview_hashes`` don't round-trip.
        """
        created = await db.create_job(uid, content="jd")
        await db.update_job(
            uid,
            created["job_id"],
            {
                "job_keywords": {"required_skills": ["Python", "AWS"]},
                "job_keywords_hash": "deadbeef",
                "preview_hash": "abc123",
                "preview_hashes": {"keywords": "abc123", "nudge": "def456"},
                "preview_prompt_id": "keywords",
                "company": "Acme Corp",
                "role": "Staff Engineer",
            },
        )
        fetched = await db.get_job(uid, created["job_id"])
        # Core fields preserved.
        assert fetched["content"] == "jd"
        # Dynamic fields flattened to the top level.
        assert fetched["preview_hash"] == "abc123"
        assert fetched["preview_hashes"] == {"keywords": "abc123", "nudge": "def456"}
        assert fetched["job_keywords_hash"] == "deadbeef"
        assert fetched["job_keywords"]["required_skills"] == ["Python", "AWS"]
        assert fetched["company"] == "Acme Corp"
        assert fetched["role"] == "Staff Engineer"

    async def test_update_job_merges_metadata(self, db, uid):
        created = await db.create_job(uid, content="jd")
        await db.update_job(uid, created["job_id"], {"preview_hash": "h1"})
        await db.update_job(uid, created["job_id"], {"company": "Acme"})
        fetched = await db.get_job(uid, created["job_id"])
        # The second update must not wipe the first dynamic field.
        assert fetched["preview_hash"] == "h1"
        assert fetched["company"] == "Acme"


class TestImprovements:
    async def test_create_and_lookup_by_tailored_resume(self, db, uid):
        await db.create_improvement(
            uid,
            original_resume_id="orig",
            tailored_resume_id="tailored-1",
            job_id="job-1",
            improvements=[{"path": "summary"}],
        )
        found = await db.get_improvement_by_tailored_resume(uid, "tailored-1")
        assert found is not None
        assert found["job_id"] == "job-1"

    async def test_lookup_missing_returns_none(self, db, uid):
        assert await db.get_improvement_by_tailored_resume(uid, "nope") is None


class TestApplications:
    async def test_create_defaults_and_position(self, db, uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1")
        assert a["status"] == "applied"
        assert a["position"] == 0
        assert a["applied_at"] is not None  # applied -> stamped
        b = await db.create_application(uid, job_id="j2", resume_id="r2")
        assert b["position"] == 1  # appended to the column

    async def test_saved_status_has_no_applied_at(self, db, uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1", status="saved")
        assert a["applied_at"] is None

    async def test_create_dedupes_on_job_and_resume(self, db, uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1")
        again = await db.create_application(uid, job_id="j1", resume_id="r1")
        assert again["application_id"] == a["application_id"]
        assert len(await db.list_applications(uid)) == 1

    async def test_move_renumbers_columns(self, db, uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1")
        b = await db.create_application(uid, job_id="j2", resume_id="r2")
        # Move a to the front of "interview".
        moved = await db.update_application(uid, a["application_id"], {"status": "interview", "position": 0})
        assert moved["status"] == "interview"
        assert moved["position"] == 0
        # The "applied" column renumbered: b is now position 0.
        applied = await db.list_applications(uid, status="applied")
        assert [x["application_id"] for x in applied] == [b["application_id"]]
        assert applied[0]["position"] == 0

    async def test_bulk_update_and_delete(self, db, uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1")
        b = await db.create_application(uid, job_id="j2", resume_id="r2")
        moved = await db.bulk_update_applications(uid, [a["application_id"], b["application_id"]], "rejected")
        assert moved == 2
        rejected = await db.list_applications(uid, status="rejected")
        assert {x["position"] for x in rejected} == {0, 1}
        deleted = await db.bulk_delete_applications(uid, [a["application_id"]])
        assert deleted == 1
        remaining = await db.list_applications(uid, status="rejected")
        assert len(remaining) == 1
        assert remaining[0]["position"] == 0  # renumbered after delete


class TestApiKeyStore:
    async def test_set_get_delete_ciphertext(self, db, uid):
        db.set_api_key_ciphertext(uid, "openai", "ct-openai")
        db.set_api_key_ciphertext(uid, "anthropic", "ct-anthropic")
        assert db.get_api_key_ciphertexts(uid) == {"openai": "ct-openai", "anthropic": "ct-anthropic"}
        db.delete_api_key(uid, "openai")
        assert db.get_api_key_ciphertexts(uid) == {"anthropic": "ct-anthropic"}
        db.clear_api_keys(uid)
        assert db.get_api_key_ciphertexts(uid) == {}


class TestStatsAndReset:
    async def test_get_stats(self, db, uid):
        await db.create_resume(uid, content="a")
        await db.set_master_resume(uid, (await db.list_resumes(uid))[0]["resume_id"])
        await db.create_job(uid, content="jd")
        stats = await db.get_stats(uid)
        assert stats["total_resumes"] == 1
        assert stats["total_jobs"] == 1
        assert stats["has_master_resume"] is True

    async def test_reset_database_truncates(self, db, uid, tmp_path, monkeypatch):
        # reset_database also clears settings.data_dir/uploads - isolate it to tmp.
        monkeypatch.setattr("app.database.settings.data_dir", tmp_path)
        await db.create_resume(uid, content="a")
        await db.create_job(uid, content="jd")
        await db.create_application(uid, job_id="j1", resume_id="r1")
        await db.reset_database(uid)
        stats = await db.get_stats(uid)
        assert stats["total_resumes"] == 0
        assert stats["total_jobs"] == 0
        assert stats["has_master_resume"] is False
        # Applications are cleared too (no orphans after a full reset).
        assert await db.list_applications(uid) == []


class TestCrossUserIsolation:
    """User A can never read or mutate user B's owned rows (Property 1, R10.2/10.3).

    A foreign id resolves to ``None`` (the router turns that into a 404 - no
    existence disclosure), never another user's row.
    """

    async def test_resume_isolation(self, db, uid, other_uid):
        r = await db.create_resume(uid, content="secret")
        # B cannot read A's resume.
        assert await db.get_resume(other_uid, r["resume_id"]) is None
        # B does not see A's resume in their list.
        assert await db.list_resumes(other_uid) == []
        # B cannot mutate A's resume (treated as absent).
        with pytest.raises(ValueError):
            await db.update_resume(other_uid, r["resume_id"], {"title": "hijack"})
        # B cannot delete A's resume.
        assert await db.delete_resume(other_uid, r["resume_id"]) is False
        # A's resume is intact.
        assert (await db.get_resume(uid, r["resume_id"]))["content"] == "secret"

    async def test_job_isolation(self, db, uid, other_uid):
        j = await db.create_job(uid, content="A's job")
        assert await db.get_job(other_uid, j["job_id"]) is None
        assert await db.update_job(other_uid, j["job_id"], {"content": "hijack"}) is None
        assert await db.delete_job(other_uid, j["job_id"]) is False
        assert (await db.get_job(uid, j["job_id"]))["content"] == "A's job"

    async def test_application_isolation(self, db, uid, other_uid):
        a = await db.create_application(uid, job_id="j1", resume_id="r1")
        assert await db.get_application(other_uid, a["application_id"]) is None
        assert await db.list_applications(other_uid) == []
        assert await db.update_application(other_uid, a["application_id"], {"status": "interview"}) is None
        assert await db.delete_application(other_uid, a["application_id"]) is False
        # B's bulk ops skip A's card.
        assert await db.bulk_update_applications(other_uid, [a["application_id"]], "rejected") == 0
        assert await db.bulk_delete_applications(other_uid, [a["application_id"]]) == 0
        # A's card is intact.
        assert (await db.get_application(uid, a["application_id"]))["status"] == "applied"

    async def test_improvement_isolation(self, db, uid, other_uid):
        await db.create_improvement(
            uid,
            original_resume_id="orig",
            tailored_resume_id="t1",
            job_id="j1",
            improvements=[],
        )
        assert await db.get_improvement_by_tailored_resume(other_uid, "t1") is None
        assert await db.get_improvement_by_tailored_resume(uid, "t1") is not None

    async def test_api_key_isolation(self, db, uid, other_uid):
        """One user's provider key never appears in another user's key set (R10.6)."""
        db.set_api_key_ciphertext(uid, "openai", "ct-A")
        db.set_api_key_ciphertext(other_uid, "openai", "ct-B")
        assert db.get_api_key_ciphertexts(uid) == {"openai": "ct-A"}
        assert db.get_api_key_ciphertexts(other_uid) == {"openai": "ct-B"}
        # Clearing A's keys leaves B's intact.
        db.clear_api_keys(uid)
        assert db.get_api_key_ciphertexts(uid) == {}
        assert db.get_api_key_ciphertexts(other_uid) == {"openai": "ct-B"}
