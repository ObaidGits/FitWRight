"""Which resume goes out with an application.

The rule this pins is the product's whole premise: a resume tailored for *this*
job must be the one attached. The failure it replaces silently sent the master
resume to every employer, discarding the tailoring at the one moment it counted.

The other half is restraint. Attaching the *wrong* tailored resume is worse than
attaching the master, so a partial match is not a match: company and role must
both agree, and anything less falls back honestly.
"""
import pytest

from app.applications import resume_choice

USER: str = ""


@pytest.fixture
async def db(isolated_db, owner_id):
    global USER
    USER = owner_id
    return isolated_db


async def make_application(db, *, resume_id, company, role, suffix="1"):
    from app.models import Application

    async with db._session() as session:
        async with session.begin():
            session.add(
                Application(
                    application_id=f"app-{suffix}",
                    user_id=USER,
                    job_id=f"job-{suffix}",
                    resume_id=resume_id,
                    status="saved",
                    company=company,
                    role=role,
                )
            )


class TestResolveResumeForRole:
    async def test_matches_company_and_role(self, db):
        await make_application(db, resume_id="tailored-1", company="Acme", role="Backend Engineer")

        found = await resume_choice.resolve_resume_id_for_role(
            db, USER, company="Acme", role="Backend Engineer"
        )
        assert found == "tailored-1"

    async def test_matching_ignores_case_and_spacing(self, db):
        await make_application(db, resume_id="tailored-1", company="Acme Inc", role="Backend Eng")

        found = await resume_choice.resolve_resume_id_for_role(
            db, USER, company="  ACME   inc ", role="backend eng"
        )
        assert found == "tailored-1"

    async def test_same_company_different_role_does_not_match(self, db):
        """Two roles at one employer deserve two resumes."""
        await make_application(db, resume_id="tailored-backend", company="Acme", role="Backend")

        found = await resume_choice.resolve_resume_id_for_role(
            db, USER, company="Acme", role="Frontend"
        )
        assert found is None

    async def test_same_role_different_company_does_not_match(self, db):
        await make_application(db, resume_id="tailored-acme", company="Acme", role="Backend")

        found = await resume_choice.resolve_resume_id_for_role(
            db, USER, company="Globex", role="Backend"
        )
        assert found is None

    async def test_no_company_or_role_returns_none(self, db):
        await make_application(db, resume_id="tailored-1", company="Acme", role="Backend")

        assert await resume_choice.resolve_resume_id_for_role(db, USER, company=None, role="Backend") is None
        assert await resume_choice.resolve_resume_id_for_role(db, USER, company="Acme", role=None) is None
        assert await resume_choice.resolve_resume_id_for_role(db, USER, company="", role="") is None

    async def test_newest_application_wins_on_retailoring(self, db):
        """Re-tailoring for the same role means the later resume is the intent."""
        await make_application(db, resume_id="older", company="Acme", role="Backend", suffix="old")
        await make_application(db, resume_id="newer", company="Acme", role="Backend", suffix="new")

        found = await resume_choice.resolve_resume_id_for_role(
            db, USER, company="Acme", role="Backend"
        )
        # Both rows match; ordering is newest-first, so the later one is returned.
        assert found == "newer"

    async def test_describe_reports_whether_it_was_tailored(self, db):
        await make_application(db, resume_id="tailored-1", company="Acme", role="Backend")

        hit = await resume_choice.describe_resume_choice(db, USER, company="Acme", role="Backend")
        assert hit == {"resume_id": "tailored-1", "tailored": True}

        miss = await resume_choice.describe_resume_choice(db, USER, company="Nowhere", role="X")
        assert miss == {"resume_id": None, "tailored": False}


class TestAutofillProfileUsesIt:
    async def test_profile_attaches_the_tailored_resume(self, db):
        """End to end through the builder the extension actually calls."""
        from app.models import Resume
        from app.routers.extension import build_autofill_profile

        async with db._session() as session:
            async with session.begin():
                session.add(
                    Resume(
                        resume_id="tailored-1",
                        user_id=USER,
                        content="# Tailored",
                        filename="acme-backend.pdf",
                    )
                )
                session.add(
                    Resume(
                        resume_id="master-1",
                        user_id=USER,
                        content="# Master",
                        filename="master.pdf",
                        is_master=True,
                    )
                )
        await make_application(db, resume_id="tailored-1", company="Acme", role="Backend")

        tailored = await build_autofill_profile(db, USER, None, company="Acme", title="Backend")
        assert tailored.resume_id == "tailored-1"
        assert tailored.resume_tailored_for_role is True
        assert tailored.resume_pdf_path == "/api/v1/resumes/tailored-1/pdf"

    async def test_profile_falls_back_to_master_without_a_match(self, db):
        from app.models import Resume
        from app.routers.extension import build_autofill_profile

        async with db._session() as session:
            async with session.begin():
                session.add(
                    Resume(
                        resume_id="master-1",
                        user_id=USER,
                        content="# Master",
                        filename="master.pdf",
                        is_master=True,
                    )
                )

        generic = await build_autofill_profile(db, USER, None, company="Nowhere", title="X")
        assert generic.resume_id == "master-1"
        # Must not claim tailoring that did not happen.
        assert generic.resume_tailored_for_role is False

    async def test_explicit_resume_id_still_wins(self, db):
        """A caller naming a resume is not second-guessed."""
        from app.models import Resume
        from app.routers.extension import build_autofill_profile

        async with db._session() as session:
            async with session.begin():
                session.add(
                    Resume(resume_id="chosen", user_id=USER, content="# C", filename="chosen.pdf")
                )
        await make_application(db, resume_id="tailored-1", company="Acme", role="Backend")

        result = await build_autofill_profile(
            db, USER, "chosen", company="Acme", title="Backend"
        )
        assert result.resume_id == "chosen"
        assert result.resume_tailored_for_role is False
