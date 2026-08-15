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


class TestSavingTypedAnswers:
    """`POST /extension/answers` - the learn-in-place path.

    This is the one endpoint that accepts values, and the distinction from
    form-report is consent: it runs because the user pressed "save my answers",
    not automatically on every form they open.
    """

    async def test_saving_an_answer_records_the_value(self, db):
        await db.upsert_application_field(
            USER, label="Preferred pronouns", label_normalized="preferred pronouns"
        )
        assert await db.set_application_field_value(
            USER, label_normalized="preferred pronouns", company=None, value="they/them"
        )
        row = (await db.list_application_fields(USER))[0]
        assert row["value"] == "they/them"


class TestBrainDecisions:
    """The auto-apply-brain audit trail (Phase 0, no LLM involved yet).

    See app.brain_grading for the grading rules themselves; these tests cover
    only the storage contract - record, re-fill idempotency, and read-back.
    """

    async def test_records_one_row_per_field(self, db):
        recorded = await db.record_brain_decisions(
            USER,
            decisions=[
                {
                    "site_host": "boards.greenhouse.io",
                    "label": "Email",
                    "label_normalized": "email",
                    "resolved_target": "email",
                    "value_source": "exact_rule",
                    "confidence": 1.0,
                    "is_knockout": False,
                    "filled": True,
                    "readback_ok": True,
                    "grade_contribution": "green",
                    "brain_tokens": 0,
                },
                {
                    "site_host": "boards.greenhouse.io",
                    "label": "Visa status",
                    "label_normalized": "visa status",
                    "resolved_target": "visa_status",
                    "value_source": "user_answer",
                    "confidence": 1.0,
                    "is_knockout": True,
                    "filled": True,
                    "readback_ok": True,
                    "grade_contribution": "green",
                    "brain_tokens": 0,
                },
            ],
            application_id="app-1",
        )
        assert recorded == 2
        rows = await db.list_brain_decisions(USER, application_id="app-1")
        assert len(rows) == 2
        visa = next(r for r in rows if r["label_normalized"] == "visa status")
        assert visa["is_knockout"] is True
        assert visa["value_source"] == "user_answer"

    async def test_refilling_the_same_application_updates_rather_than_duplicates(self, db):
        # A multi-step wizard re-runs autofill on every step advance
        # (content/index.ts fillCurrentStep). The same field reappearing must
        # update its row, not pile up a duplicate that would double-count in
        # grading.
        decision = {
            "site_host": "jobs.lever.co",
            "label": "Notice period",
            "label_normalized": "notice period",
            "resolved_target": "notice_period",
            "value_source": "brain_classification",
            "confidence": 0.7,
            "is_knockout": True,
            "filled": False,
            "readback_ok": None,
            "grade_contribution": "red",
            "brain_tokens": 12,
        }
        await db.record_brain_decisions(USER, decisions=[decision], application_id="app-2")
        updated = {**decision, "filled": True, "readback_ok": True, "grade_contribution": "green"}
        recorded_second = await db.record_brain_decisions(
            USER, decisions=[updated], application_id="app-2"
        )
        assert recorded_second == 0  # updated in place, not inserted again
        rows = await db.list_brain_decisions(USER, application_id="app-2")
        assert len(rows) == 1
        assert rows[0]["filled"] is True
        assert rows[0]["grade_contribution"] == "green"

    async def test_decisions_for_different_applications_do_not_collide(self, db):
        base = {
            "site_host": "boards.greenhouse.io",
            "label": "City",
            "label_normalized": "city",
            "resolved_target": "city",
            "value_source": "exact_rule",
            "confidence": 1.0,
            "is_knockout": False,
            "filled": True,
            "readback_ok": True,
            "grade_contribution": "green",
            "brain_tokens": 0,
        }
        await db.record_brain_decisions(USER, decisions=[base], application_id="app-a")
        await db.record_brain_decisions(USER, decisions=[base], application_id="app-b")
        assert len(await db.list_brain_decisions(USER, application_id="app-a")) == 1
        assert len(await db.list_brain_decisions(USER, application_id="app-b")) == 1


class TestRecordDecisionsEndpoint:
    """`POST /application-fields/decisions` - grading computed server-side."""

    async def test_endpoint_computes_and_returns_the_grade(self, db):
        from app.routers.application_fields import DecisionBatch, DecisionIn, record_decisions

        payload = DecisionBatch(
            application_id="app-endpoint-1",
            decisions=[
                DecisionIn(
                    site_host="boards.greenhouse.io",
                    label="Email",
                    resolved_target="email",
                    value_source="exact_rule",
                    filled=True,
                    readback_ok=True,
                    required=True,
                ),
                DecisionIn(
                    site_host="boards.greenhouse.io",
                    label="Are you legally entitled to work here?",
                    resolved_target="visa_status",
                    value_source="brain_classification",
                    filled=True,
                    readback_ok=True,
                    required=True,
                ),
            ],
        )
        result = await record_decisions(payload, user_id=USER, db=db)
        # The second field's label matches the knockout heuristic AND was filled
        # by an untrusted source, so the whole application must grade red - not
        # yellow - per R1.4.
        assert result.grade == "red"
        assert result.recorded == 2

        rows = await db.list_brain_decisions(USER, application_id="app-endpoint-1")
        knockout_row = next(r for r in rows if "legally entitled" in r["label"])
        assert knockout_row["is_knockout"] is True
        assert knockout_row["grade_contribution"] == "red"

    async def test_endpoint_rejects_an_unknown_value_source(self):
        from app.routers.application_fields import DecisionIn

        with pytest.raises(Exception):
            DecisionIn(
                site_host="example.com",
                label="Anything",
                value_source="guessed_vibes",
            )


class TestGetApplicationDecisions:
    """`GET /application-fields/decisions/{application_id}` - the read side of
    the audit trail, used by the Applications page's "How this was filled" panel."""

    async def test_recomputes_the_grade_rather_than_trusting_the_stored_value(self, db):
        from app.routers.application_fields import get_application_decisions

        # Stored as green, but a knockout field from an untrusted source must
        # never actually grade green - this proves the endpoint recomputes
        # rather than echoing back whatever was written at record time.
        await db.record_brain_decisions(
            USER,
            decisions=[
                {
                    "site_host": "boards.greenhouse.io",
                    "label": "Visa status",
                    "label_normalized": "visa status",
                    "resolved_target": "visa_status",
                    "value_source": "brain_classification",
                    "confidence": 0.9,
                    "is_knockout": True,
                    "filled": True,
                    "readback_ok": True,
                    "grade_contribution": "green",  # stale/wrong on purpose
                    "brain_tokens": 5,
                },
            ],
            application_id="app-recompute",
        )
        result = await get_application_decisions(
            "app-recompute", user_id=USER, db=db
        )
        assert result.grade == "red"
        assert any("Screening question" in reason for reason in result.held_reasons)

    async def test_an_application_with_no_decisions_grades_yellow_not_green(self, db):
        from app.routers.application_fields import get_application_decisions

        result = await get_application_decisions("no-such-app", user_id=USER, db=db)
        assert result.grade == "yellow"
        assert result.decisions == []


class TestSavingTypedAnswers:
    """`POST /extension/answers` - the learn-in-place path.

    This is the one endpoint that accepts values, and the distinction from
    form-report is consent: it runs because the user pressed "save my answers",
    not automatically on every form they open.
    """

    async def test_saving_an_answer_records_the_value(self, db):
        await db.upsert_application_field(
            USER, label="Preferred pronouns", label_normalized="preferred pronouns"
        )
        assert await db.set_application_field_value(
            USER, label_normalized="preferred pronouns", company=None, value="they/them"
        )
        row = (await db.list_application_fields(USER))[0]
        assert row["status"] == "answered"

    async def test_saving_again_overwrites_because_the_user_said_so(self, db):
        """Unlike a form sighting, an explicit save is the user's latest word."""
        await db.upsert_application_field(USER, label="Notice", label_normalized="notice")
        await db.set_application_field_value(
            USER, label_normalized="notice", company=None, value="30 days"
        )
        await db.set_application_field_value(
            USER, label_normalized="notice", company=None, value="60 days"
        )
        assert (await db.list_application_fields(USER))[0]["value"] == "60 days"

    async def test_a_profile_backed_field_is_left_alone(self, db):
        """The answer belongs to the Profile; overwriting here would reintroduce
        exactly the stale copy the pointer exists to prevent."""
        await db.upsert_application_field(
            USER, label="Work Authorization", label_normalized="work authorization"
        )
        fid = (await db.list_application_fields(USER))[0]["id"]
        await db.update_application_field(
            USER, fid, {"profile_path": "identity.workAuthorization"}
        )

        assert (
            await db.set_application_field_value(
                USER, label_normalized="work authorization", company=None, value="OVERWRITE"
            )
            is False
        )
        row = (await db.list_application_fields(USER))[0]
        assert row["value"] is None
        assert row["profile_path"] == "identity.workAuthorization"

    async def test_unknown_label_is_not_created_silently(self, db):
        """The value setter answers an existing question; it does not invent one."""
        assert (
            await db.set_application_field_value(
                USER, label_normalized="never seen", company=None, value="x"
            )
            is False
        )
        assert await db.list_application_fields(USER) == []

    async def test_company_scoped_answer_is_addressed_by_company(self, db):
        await db.upsert_application_field(USER, label="Team", label_normalized="team")
        await db.upsert_application_field(
            USER, label="Team", label_normalized="team", company="Acme"
        )
        await db.set_application_field_value(
            USER, label_normalized="team", company="Acme", value="Platform"
        )
        rows = {(r["scope"], r["value"]) for r in await db.list_application_fields(USER)}
        assert ("company", "Platform") in rows
        assert ("global", None) in rows


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
