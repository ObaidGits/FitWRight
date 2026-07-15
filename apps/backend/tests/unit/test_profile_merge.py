"""Unit tests for the Merge Engine (P3): similarity, plan, apply, adapters.

Covers the deterministic building blocks: entity similarity scoring, merge-plan
classification (add/update/duplicate/conflict), non-destructive apply semantics
(defaults never overwrite manual data; uids preserved on update), and the import
adapters (resume + JSON Resume).
"""

from __future__ import annotations

from app.profile import similarity as sim
from app.profile.import_adapters import ImportError_, derive_candidate
from app.profile.merge import apply_merge_plan, build_merge_plan
from app.profile.schemas import ProfileData


def _profile(**overrides) -> ProfileData:
    base = {
        "identity": {"name": "Ada", "headline": "Engineer", "email": "ada@x.co"},
        "summary": "Existing summary.",
        "workExperience": [
            {"uid": "w1", "title": "Engineer", "company": "Acme", "years": "2020-2022", "description": ["Built X"]}
        ],
        "skills": {"technical": [{"canonical": "python", "displayName": "Python"}]},
    }
    base.update(overrides)
    return ProfileData.model_validate(base)


# ---------------------------------------------------------------------------
# Similarity
# ---------------------------------------------------------------------------


class TestSimilarity:
    def test_identical_text(self):
        assert sim.text_similarity("Senior SWE", "senior swe") == 1.0

    def test_disjoint_text(self):
        assert sim.text_similarity("apple", "zzzzz") < 0.3

    def test_experience_same_job_scores_high(self):
        a = {"title": "Software Engineer", "company": "Acme Corp", "years": "2020-2022"}
        b = {"title": "Software Engineer", "company": "Acme Corp", "years": "2020-2022"}
        assert sim.experience_similarity(a, b) >= sim.DUPLICATE_THRESHOLD

    def test_experience_different_job_scores_low(self):
        a = {"title": "Software Engineer", "company": "Acme", "years": "2020"}
        b = {"title": "Product Manager", "company": "Globex", "years": "2015"}
        assert sim.experience_similarity(a, b) < sim.MATCH_THRESHOLD

    def test_best_match_below_threshold_is_none(self):
        inc = {"title": "Chef", "company": "Kitchen"}
        cands = [{"title": "Engineer", "company": "Acme"}]
        assert sim.best_match(inc, cands, sim.experience_similarity) is None


# ---------------------------------------------------------------------------
# Merge plan
# ---------------------------------------------------------------------------


class TestMergePlan:
    def test_new_experience_is_add(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[{"title": "Designer", "company": "Globex", "years": "2023"}]
        )
        plan = build_merge_plan(existing, incoming)
        exp_ops = [o for o in plan.operations if o.section == "workExperience"]
        assert len(exp_ops) == 1
        assert exp_ops[0].op == "add"

    def test_identical_experience_is_duplicate(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[
                {"title": "Engineer", "company": "Acme", "years": "2020-2022", "description": ["Built X"]}
            ]
        )
        plan = build_merge_plan(existing, incoming)
        exp_ops = [o for o in plan.operations if o.section == "workExperience"]
        assert exp_ops[0].op == "duplicate"

    def test_same_job_new_bullets_is_update(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[
                {"title": "Engineer", "company": "Acme", "years": "2020-2022", "description": ["Led migration"]}
            ]
        )
        plan = build_merge_plan(existing, incoming)
        exp_ops = [o for o in plan.operations if o.section == "workExperience"]
        assert exp_ops[0].op == "update"
        assert any(c.field == "description" for c in exp_ops[0].changes)

    def test_summary_conflict_when_both_present(self):
        existing = _profile()
        incoming = _profile(summary="A totally different summary.")
        plan = build_merge_plan(existing, incoming)
        summary_ops = [o for o in plan.operations if o.section == "summary"]
        assert summary_ops[0].op == "conflict"
        assert summary_ops[0].default_resolution == "keep_existing"

    def test_deterministic_plan_ids(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[{"uid": "inc1", "title": "Designer", "company": "Globex"}]
        )
        p1 = build_merge_plan(existing, incoming)
        p2 = build_merge_plan(existing, incoming)
        assert [o.id for o in p1.operations] == [o.id for o in p2.operations]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class TestApply:
    def test_add_appends_new_item_with_provenance(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[{"uid": "inc1", "title": "Designer", "company": "Globex"}]
        )
        merged, applied, skipped = apply_merge_plan(existing, incoming, {})
        titles = [e.title for e in merged.workExperience]
        assert "Designer" in titles
        assert applied >= 1
        # New item recorded in provenance as an import.
        new = next(e for e in merged.workExperience if e.title == "Designer")
        assert merged.meta.provenance[new.uid].source == "import"

    def test_default_does_not_overwrite_summary_conflict(self):
        existing = _profile()
        incoming = _profile(summary="Different.")
        merged, _, _ = apply_merge_plan(existing, incoming, {})
        assert merged.summary == "Existing summary."  # kept by default

    def test_replace_resolution_overwrites_summary(self):
        existing = _profile()
        incoming = _profile(summary="Different.")
        plan = build_merge_plan(existing, incoming)
        summary_op = next(o for o in plan.operations if o.section == "summary")
        merged, _, _ = apply_merge_plan(existing, incoming, {summary_op.id: "replace"})
        assert merged.summary == "Different."

    def test_merge_preserves_existing_uid_and_unions_bullets(self):
        existing = _profile()
        incoming = _profile(
            workExperience=[
                {"title": "Engineer", "company": "Acme", "years": "2020-2022", "description": ["New bullet"]}
            ]
        )
        plan = build_merge_plan(existing, incoming)
        op = next(o for o in plan.operations if o.section == "workExperience")
        merged, _, _ = apply_merge_plan(existing, incoming, {op.id: "merge"})
        exp = next(e for e in merged.workExperience if e.company == "Acme")
        assert exp.uid == "w1"  # identity preserved
        assert "Built X" in exp.description and "New bullet" in exp.description

    def test_empty_summary_filled_by_default(self):
        existing = _profile(summary="")
        incoming = _profile(summary="Fresh summary.")
        merged, _, _ = apply_merge_plan(existing, incoming, {})
        assert merged.summary == "Fresh summary."

    def test_duplicate_skill_not_added(self):
        existing = _profile()
        incoming = _profile(skills={"technical": [{"canonical": "python", "displayName": "Python"}]})
        merged, _, _ = apply_merge_plan(existing, incoming, {})
        py = [s for s in merged.skills.technical if s.canonical == "python"]
        assert len(py) == 1


# ---------------------------------------------------------------------------
# Import adapters
# ---------------------------------------------------------------------------


class TestImportAdapters:
    def test_resume_adapter(self):
        payload = {
            "processed_data": {
                "personalInfo": {"name": "Ada", "title": "Eng"},
                "workExperience": [{"title": "SWE", "company": "X", "description": ["a"]}],
            }
        }
        candidate = derive_candidate("resume", payload)
        assert candidate.identity.name == "Ada"
        assert candidate.workExperience[0].company == "X"

    def test_json_resume_adapter(self):
        payload = {
            "data": {
                "basics": {
                    "name": "Grace",
                    "label": "Engineer",
                    "email": "g@x.co",
                    "summary": "Hi",
                    "profiles": [{"network": "GitHub", "url": "https://gh/g"}],
                },
                "work": [{"name": "Navy", "position": "Officer", "highlights": ["Led"]}],
                "skills": [{"name": "COBOL", "keywords": ["COBOL"]}],
            }
        }
        candidate = derive_candidate("json_resume", payload)
        assert candidate.identity.name == "Grace"
        assert candidate.identity.github == "https://gh/g"
        assert candidate.workExperience[0].company == "Navy"
        assert any(s.displayName == "COBOL" for s in candidate.skills.technical)

    def test_unsupported_source_raises(self):
        try:
            derive_candidate("linkedin", {})
            assert False, "expected ImportError_"
        except ImportError_ as exc:
            assert exc.code == "unsupported"
