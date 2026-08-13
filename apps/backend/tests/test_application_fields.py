"""The field registry's behavioural rules.

These pin the decisions that make the learning loop trustworthy rather than
merely working:

* the same question seen repeatedly is ONE row with a count, not N rows - the
  difference between a usable Settings page and an unusable one
* an answer already given is never clobbered by a later sighting
* a row holds a value OR a Profile pointer, never both, so editing the Profile
  can never leave a stale answer behind that silently wins
* form reports carry labels and types only; a payload smuggling a value is
  refused rather than quietly stored
"""
import pytest

from app.routers.application_fields import (
    FormReport,
    normalize_label,
    resolve_profile_path,
)

USER: str = ""  # set per-test by the `db` fixture below


@pytest.fixture
async def db(isolated_db, owner_id):
    """The isolated database, with a real owner row.

    `application_fields.user_id` has a foreign key to `users.id`, so a literal
    test id would (correctly) be rejected. `owner_id` is the single-user
    bootstrap owner the endpoints resolve to anyway, which makes these tests
    exercise the same rows the API would touch.
    """
    global USER
    USER = owner_id
    return isolated_db


class TestNormalizeLabel:
    """Cosmetic differences in ATS labels must collapse to one key."""

    def test_required_marker_and_case_and_spacing(self):
        assert normalize_label("Years of Python *") == "years of python"
        assert normalize_label("years  OF   python") == "years of python"

    def test_punctuation_dropped(self):
        assert normalize_label("Work Authorization?") == "work authorization"
        assert normalize_label("E-mail address:") == "e mail address"

    def test_blank_stays_blank(self):
        assert normalize_label("   ") == ""
        assert normalize_label("***") == ""


class TestFormReportPrivacy:
    def test_value_bearing_payload_is_refused(self):
        """This endpoint sees every form the user opens; it must never become a
        transcript of their answers."""
        for leaked in ("value", "answer", "password", "text"):
            with pytest.raises(Exception) as err:
                FormReport(fields=[{"label": "Phone", leaked: "0123456789"}])
            assert leaked in str(err.value)

    def test_labels_and_types_are_accepted(self):
        report = FormReport(
            fields=[{"label": "Phone", "field_type": "text", "filled": True}], company="Acme"
        )
        assert report.fields[0].label == "Phone"

    def test_unknown_field_type_falls_back_to_text(self):
        report = FormReport(fields=[{"label": "X", "field_type": "wat"}])
        assert report.fields[0].field_type == "text"

    def test_label_is_required(self):
        with pytest.raises(Exception):
            FormReport(fields=[{"label": "  "}])


class TestUpsert:
    async def test_same_question_twice_is_one_row_with_a_count(self, db):
        for _ in range(3):
            await db.upsert_application_field(
                USER, label="Years of Python *", label_normalized="years of python"
            )
        rows = await db.list_application_fields(USER)
        assert len(rows) == 1
        assert rows[0]["times_seen"] == 3

    async def test_first_write_reports_created_then_updated(self, db):
        created = await db.upsert_application_field(
            USER, label="Phone", label_normalized="phone"
        )
        again = await db.upsert_application_field(USER, label="Phone", label_normalized="phone")
        assert created is True
        assert again is False

    async def test_company_scope_is_a_separate_row_from_global(self, db):
        await db.upsert_application_field(USER, label="Team", label_normalized="team")
        await db.upsert_application_field(
            USER, label="Team", label_normalized="team", company="Acme"
        )
        rows = await db.list_application_fields(USER)
        assert {(r["scope"], r["company"]) for r in rows} == {
            ("global", None),
            ("company", "Acme"),
        }

    async def test_richer_option_set_wins(self, db):
        await db.upsert_application_field(
            USER, label="Notice", label_normalized="notice", options=["30 days"]
        )
        await db.upsert_application_field(
            USER,
            label="Notice",
            label_normalized="notice",
            options=["Immediate", "30 days", "60 days"],
        )
        rows = await db.list_application_fields(USER)
        assert len(rows[0]["options"]) == 3

    async def test_an_answered_field_is_not_reopened_by_a_later_sighting(self, db):
        """A form that leaves it blank must not undo an answer already given."""
        await db.upsert_application_field(USER, label="Phone", label_normalized="phone")
        field_id = (await db.list_application_fields(USER))[0]["id"]
        await db.update_application_field(USER, field_id, {"value": "0123456789"})

        await db.upsert_application_field(
            USER, label="Phone", label_normalized="phone", status="needs_answer"
        )
        row = (await db.list_application_fields(USER))[0]
        assert row["status"] == "answered"
        assert row["value"] == "0123456789"


class TestOrdering:
    async def test_unanswered_first_then_most_seen(self, db):
        await db.upsert_application_field(USER, label="Rare", label_normalized="rare")
        rare_id = (await db.list_application_fields(USER))[0]["id"]
        await db.update_application_field(USER, rare_id, {"value": "x"})  # answered

        for _ in range(5):
            await db.upsert_application_field(USER, label="Common", label_normalized="common")

        rows = await db.list_application_fields(USER)
        assert rows[0]["label"] == "Common"  # needs_answer sorts above answered
        assert rows[0]["status"] == "needs_answer"


class TestValueOrPointer:
    async def test_setting_a_pointer_clears_a_stored_value(self, db):
        """Otherwise a Profile edit would leave a stale copy that still wins."""
        await db.upsert_application_field(
            USER, label="Work Authorization", label_normalized="work authorization"
        )
        fid = (await db.list_application_fields(USER))[0]["id"]

        await db.update_application_field(USER, fid, {"value": "STALE"})
        # Mirrors the router's rule: a pointer supersedes a stored value.
        await db.update_application_field(
            USER, fid, {"profile_path": "identity.workAuthorization", "value": None}
        )
        row = (await db.list_application_fields(USER))[0]
        assert row["profile_path"] == "identity.workAuthorization"
        assert row["value"] is None

    def test_pointer_resolves_through_lists(self):
        doc = {"education": [{"degree": "B.Tech CSE"}], "identity": {"phone": "1"}}
        assert resolve_profile_path(doc, "education.0.degree") == "B.Tech CSE"
        assert resolve_profile_path(doc, "identity.phone") == "1"

    def test_missing_pointer_resolves_to_none_not_an_error(self):
        assert resolve_profile_path({}, "identity.workAuthorization") is None
        assert resolve_profile_path({"a": 1}, "") is None


class TestScopeAndDeletion:
    async def test_switching_to_global_clears_the_company(self, db):
        await db.upsert_application_field(
            USER, label="Team", label_normalized="team", company="Acme"
        )
        fid = (await db.list_application_fields(USER))[0]["id"]
        await db.update_application_field(USER, fid, {"scope": "global"})
        row = (await db.list_application_fields(USER))[0]
        assert (row["scope"], row["company"]) == ("global", None)

    async def test_delete_removes_only_that_row(self, db):
        await db.upsert_application_field(USER, label="A", label_normalized="a")
        await db.upsert_application_field(USER, label="B", label_normalized="b")
        fid = next(r["id"] for r in await db.list_application_fields(USER) if r["label"] == "A")
        assert await db.delete_application_field(USER, fid) is True
        assert {r["label"] for r in await db.list_application_fields(USER)} == {"B"}

    async def test_delete_of_another_users_row_is_refused(self, db):
        await db.upsert_application_field(USER, label="A", label_normalized="a")
        fid = (await db.list_application_fields(USER))[0]["id"]
        assert await db.delete_application_field("someone-else", fid) is False


class TestMerge:
    async def test_merge_folds_wording_and_counts(self, db):
        await db.upsert_application_field(
            USER, label="Years of Python", label_normalized="years of python"
        )
        for _ in range(2):
            await db.upsert_application_field(
                USER, label="Python (years)", label_normalized="python years"
            )
        rows = await db.list_application_fields(USER)
        keep = next(r for r in rows if r["label_normalized"] == "years of python")
        drop = next(r for r in rows if r["label_normalized"] == "python years")

        merged = await db.merge_application_fields(USER, keep["id"], drop["id"])
        assert merged is not None
        assert "python years" in merged["synonyms"]
        assert merged["times_seen"] == 3  # 1 + 2
        assert len(await db.list_application_fields(USER)) == 1

    async def test_merge_with_a_missing_row_returns_none(self, db):
        await db.upsert_application_field(USER, label="A", label_normalized="a")
        fid = (await db.list_application_fields(USER))[0]["id"]
        assert await db.merge_application_fields(USER, fid, "does-not-exist") is None


class TestStatusFilter:
    async def test_filter_returns_only_that_status(self, db):
        await db.upsert_application_field(USER, label="Open", label_normalized="open")
        await db.upsert_application_field(USER, label="Done", label_normalized="done")
        done_id = next(
            r["id"] for r in await db.list_application_fields(USER) if r["label"] == "Done"
        )
        await db.update_application_field(USER, done_id, {"status": "answered"})

        needs = await db.list_application_fields(USER, status="needs_answer")
        assert [r["label"] for r in needs] == ["Open"]
