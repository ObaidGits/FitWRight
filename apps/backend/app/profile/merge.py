"""Merge Engine - the production subsystem behind resume import (P3).

Given the **existing** profile and an **incoming** candidate (derived from an
imported resume by ``app/profile/backfill.py``), the engine produces a reviewable
:class:`MergePlan` - a list of typed operations (add / update / duplicate /
conflict) - and, on apply, folds the user-resolved plan back into a new
``ProfileData``. It is **pure and deterministic** (no I/O), so:

- the *same* (existing, incoming) always yields the *same* plan with the *same*
  operation ids, which is what makes apply **stateless** - the client echoes
  ``incoming`` + per-operation resolutions and the server re-derives the plan;
- it is trivially unit-testable and safe to run on the write path.

Guarantees (design §P3):
- **Never overwrites automatically.** Every default resolution is non-destructive
  to existing data (``keep_existing`` for conflicts/duplicates; ``merge`` only
  fills empty fields and unions lists). The user can escalate to ``replace``.
- **Stable ids preserved.** An update/replace keeps the existing item's ``uid``
  so provenance and cross-references (skill evidence, project->experience) survive.
- **Provenance stamped.** Added/replaced items and filled fields record
  ``{source, confidence, at}`` in ``meta.provenance`` (ADR-9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from app.profile.schemas import (
    FieldChange,
    MergeOperation,
    MergePlan,
    ProfileData,
    new_uid,
)
from app.profile.similarity import (
    DUPLICATE_THRESHOLD,
    MATCH_THRESHOLD,
    achievement_similarity,
    certification_similarity,
    education_similarity,
    experience_similarity,
    project_similarity,
    skill_identity,
    text_similarity,
)

__all__ = ["build_merge_plan", "apply_merge_plan"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# List-section config: (scorer, identity/text fields to diff, list fields to diff).
_LIST_SECTIONS: dict[str, tuple[Callable, list[str], list[str]]] = {
    "workExperience": (
        experience_similarity,
        ["title", "company", "location", "years"],
        ["description", "tech"],
    ),
    "education": (education_similarity, ["institution", "degree", "years", "description"], []),
    "personalProjects": (
        project_similarity,
        ["name", "role", "years", "github", "website"],
        ["description", "tech"],
    ),
    "certifications": (certification_similarity, ["name", "issuer", "date", "url"], []),
    "achievements": (achievement_similarity, ["kind", "title", "description", "date", "url"], []),
}

_SKILL_CATEGORIES = ("technical", "soft", "languages", "tools")

# Identity scalar fields carried by an imported resume.
_IDENTITY_FIELDS = ("name", "headline", "email", "phone", "location", "linkedin", "github", "website")


def _label_for(section: str, item: dict[str, Any]) -> str:
    if section == "workExperience":
        return " - ".join(x for x in [item.get("title"), item.get("company")] if x) or "Experience"
    if section == "education":
        return " - ".join(x for x in [item.get("degree"), item.get("institution")] if x) or "Education"
    if section == "personalProjects":
        return item.get("name") or "Project"
    if section == "certifications":
        return item.get("name") or "Certification"
    if section == "achievements":
        return item.get("title") or "Achievement"
    if section.startswith("skills."):
        return item.get("displayName") or item.get("canonical") or "Skill"
    return section


def _nonempty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _field_changes(existing: dict, incoming: dict, text_fields: list[str], list_fields: list[str]) -> list[FieldChange]:
    """New/changed fields the incoming item would contribute to the existing one."""
    changes: list[FieldChange] = []
    for f in text_fields:
        ev, iv = existing.get(f), incoming.get(f)
        if _nonempty(iv) and text_similarity(ev, iv) < 0.99:
            changes.append(FieldChange(field=f, existing=ev, incoming=iv))
    for f in list_fields:
        ev = [x for x in (existing.get(f) or []) if isinstance(x, str)]
        iv = [x for x in (incoming.get(f) or []) if isinstance(x, str)]
        lowered = {x.lower() for x in ev}
        added = [x for x in iv if x.lower() not in lowered]
        if added:
            changes.append(FieldChange(field=f, existing=ev, incoming=iv))
    return changes


def _list_operations(
    section: str,
    existing_items: list[dict],
    incoming_items: list[dict],
    scorer: Callable,
    text_fields: list[str],
    list_fields: list[str],
) -> list[MergeOperation]:
    """Plan operations for one list section (experience/education/...).

    Scoring is delegated to the configured :class:`SimilarityProvider` (default
    deterministic - identical to the raw scorer), so a semantic/hybrid backend
    can be swapped in without touching this planner (dependency inversion).
    """
    from app.profile.similarity_provider import get_similarity_provider

    provider = get_similarity_provider()
    ops: list[MergeOperation] = []
    claimed: set[int] = set()  # existing indices already matched (1:1 matching)

    for inc in incoming_items:
        inc_uid = inc.get("uid") or new_uid()
        op_id = f"{section}:{inc_uid}"

        # Best unclaimed match.
        best_idx, best_score = -1, 0.0
        for i, ex in enumerate(existing_items):
            if i in claimed:
                continue
            s = provider.score(section, inc, ex).value
            if s > best_score:
                best_score, best_idx = s, i

        if best_idx < 0 or best_score < MATCH_THRESHOLD:
            ops.append(
                MergeOperation(
                    id=op_id,
                    section=section,
                    op="add",
                    label=_label_for(section, inc),
                    confidence=1.0,
                    incoming=inc,
                    default_resolution="accept",
                    allowed_resolutions=["accept", "reject"],
                )
            )
            continue

        claimed.add(best_idx)
        ex = existing_items[best_idx]
        changes = _field_changes(ex, inc, text_fields, list_fields)

        if best_score >= DUPLICATE_THRESHOLD:
            if changes:
                op, default, allowed = "update", "merge", ["merge", "replace", "keep_existing", "accept"]
            else:
                op, default, allowed = "duplicate", "keep_existing", ["keep_existing", "replace", "accept"]
        else:
            # Uncertain match band: never touch existing by default - add as new.
            op, default, allowed = "conflict", "accept", ["accept", "merge", "replace", "keep_existing"]

        ops.append(
            MergeOperation(
                id=op_id,
                section=section,
                op=op,
                label=_label_for(section, inc),
                confidence=round(best_score, 3),
                similarity=round(best_score, 3),
                existing_uid=ex.get("uid"),
                existing=ex,
                incoming=inc,
                changes=changes,
                default_resolution=default,
                allowed_resolutions=allowed,
            )
        )
    return ops


def _skill_operations(existing: dict, incoming: dict) -> list[MergeOperation]:
    """Plan skill additions per category (dedupe by canonical identity)."""
    ops: list[MergeOperation] = []
    for cat in _SKILL_CATEGORIES:
        section = f"skills.{cat}"
        ex_list = existing.get("skills", {}).get(cat, []) or []
        for inc in incoming.get("skills", {}).get(cat, []) or []:
            inc_uid = inc.get("uid") or new_uid()
            op_id = f"{section}:{inc_uid}"
            dup = any(skill_identity(inc, ex) >= 0.9 for ex in ex_list)
            if dup:
                ops.append(
                    MergeOperation(
                        id=op_id,
                        section=section,
                        op="duplicate",
                        label=_label_for(section, inc),
                        incoming=inc,
                        default_resolution="keep_existing",
                        allowed_resolutions=["keep_existing", "accept"],
                    )
                )
            else:
                ops.append(
                    MergeOperation(
                        id=op_id,
                        section=section,
                        op="add",
                        label=_label_for(section, inc),
                        incoming=inc,
                        default_resolution="accept",
                        allowed_resolutions=["accept", "reject"],
                    )
                )
    return ops


def _scalar_operations(existing: dict, incoming: dict) -> list[MergeOperation]:
    """Plan operations for scalar fields: summary + identity fields."""
    ops: list[MergeOperation] = []

    # Summary.
    ex_sum, inc_sum = (existing.get("summary") or "").strip(), (incoming.get("summary") or "").strip()
    if inc_sum and text_similarity(ex_sum, inc_sum) < 0.99:
        if ex_sum:
            ops.append(
                MergeOperation(
                    id="summary:$",
                    section="summary",
                    op="conflict",
                    label="Professional summary",
                    existing=ex_sum,
                    incoming=inc_sum,
                    default_resolution="keep_existing",
                    allowed_resolutions=["keep_existing", "replace"],
                )
            )
        else:
            ops.append(
                MergeOperation(
                    id="summary:$",
                    section="summary",
                    op="add",
                    label="Professional summary",
                    incoming=inc_sum,
                    default_resolution="accept",
                    allowed_resolutions=["accept", "reject"],
                )
            )

    # Identity fields.
    ex_id, inc_id = existing.get("identity", {}), incoming.get("identity", {})
    for f in _IDENTITY_FIELDS:
        ev, iv = (ex_id.get(f) or ""), (inc_id.get(f) or "")
        if isinstance(iv, str):
            iv = iv.strip()
        if not iv or text_similarity(ev, iv) >= 0.99:
            continue
        section = f"identity.{f}"
        if _nonempty(ev):
            ops.append(
                MergeOperation(
                    id=f"{section}:$",
                    section=section,
                    op="conflict",
                    label=f"Contact - {f}",
                    existing=ev,
                    incoming=iv,
                    default_resolution="keep_existing",
                    allowed_resolutions=["keep_existing", "replace"],
                )
            )
        else:
            ops.append(
                MergeOperation(
                    id=f"{section}:$",
                    section=section,
                    op="add",
                    label=f"Contact - {f}",
                    incoming=iv,
                    default_resolution="accept",
                    allowed_resolutions=["accept", "reject"],
                )
            )
    return ops


def build_merge_plan(existing: ProfileData, incoming: ProfileData) -> MergePlan:
    """Produce the deterministic, reviewable merge plan for (existing, incoming)."""
    ex = existing.model_dump(mode="json")
    inc = incoming.model_dump(mode="json")

    ops: list[MergeOperation] = []
    ops.extend(_scalar_operations(ex, inc))
    for section, (scorer, tf, lf) in _LIST_SECTIONS.items():
        ops.extend(
            _list_operations(section, ex.get(section, []) or [], inc.get(section, []) or [], scorer, tf, lf)
        )
    ops.extend(_skill_operations(ex, inc))

    counts: dict[str, int] = {}
    for op in ops:
        counts[op.op] = counts.get(op.op, 0) + 1
    return MergePlan(operations=ops, counts=counts)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _merge_item(existing: dict, incoming: dict, text_fields: list[str], list_fields: list[str]) -> dict:
    """Non-destructive field merge: fill empty scalars, union list fields."""
    out = dict(existing)
    for f in text_fields:
        if not _nonempty(out.get(f)) and _nonempty(incoming.get(f)):
            out[f] = incoming[f]
    for f in list_fields:
        ev = [x for x in (out.get(f) or []) if isinstance(x, str)]
        lowered = {x.lower() for x in ev}
        for x in incoming.get(f) or []:
            if isinstance(x, str) and x.lower() not in lowered:
                ev.append(x)
                lowered.add(x.lower())
        out[f] = ev
    return out


def apply_merge_plan(
    existing: ProfileData,
    incoming: ProfileData,
    resolutions: dict[str, str],
    *,
    source: str = "import",
) -> tuple[ProfileData, int, int]:
    """Fold the resolved plan into a new ``ProfileData``.

    Returns ``(new_profile, applied_count, skipped_count)``. Resolutions absent
    from the map fall back to each operation's non-destructive default.
    """
    plan = build_merge_plan(existing, incoming)
    result = existing.model_dump(mode="json")
    provenance = result.setdefault("meta", {}).setdefault("provenance", {})
    stamp = {"source": source, "at": _now(), "confidence": None}

    applied = 0
    skipped = 0

    for op in plan.operations:
        resolution = resolutions.get(op.id, op.default_resolution)
        if resolution not in op.allowed_resolutions:
            resolution = op.default_resolution
        if resolution in ("reject", "keep_existing"):
            skipped += 1
            continue

        section = op.section
        inc = op.incoming
        applied += 1

        # Scalar sections.
        if section == "summary":
            if resolution in ("accept", "replace"):
                result["summary"] = inc
            continue
        if section.startswith("identity."):
            field = section.split(".", 1)[1]
            if resolution in ("accept", "replace"):
                result.setdefault("identity", {})[field] = inc
            continue

        # Skill sections.
        if section.startswith("skills."):
            cat = section.split(".", 1)[1]
            item = dict(inc)
            item["uid"] = item.get("uid") or new_uid()
            result.setdefault("skills", {}).setdefault(cat, []).append(item)
            provenance[item["uid"]] = dict(stamp)
            continue

        # List sections.
        target_list = result.setdefault(section, [])
        cfg = _LIST_SECTIONS.get(section)
        text_fields, list_fields = (cfg[1], cfg[2]) if cfg else ([], [])

        if op.op == "add" or resolution == "accept":
            item = dict(inc)
            item["uid"] = item.get("uid") or new_uid()
            target_list.append(item)
            provenance[item["uid"]] = dict(stamp)
            continue

        # update/duplicate/conflict against a matched existing item.
        idx = next((i for i, it in enumerate(target_list) if it.get("uid") == op.existing_uid), None)
        if idx is None:
            # Existing item vanished (shouldn't happen with a fresh plan) -> add.
            item = dict(inc)
            item["uid"] = item.get("uid") or new_uid()
            target_list.append(item)
            provenance[item["uid"]] = dict(stamp)
            continue

        existing_item = target_list[idx]
        if resolution == "replace":
            merged = dict(inc)
            merged["uid"] = existing_item.get("uid")  # preserve identity
            target_list[idx] = merged
            provenance[merged["uid"]] = dict(stamp)
        elif resolution == "merge":
            merged = _merge_item(existing_item, inc, text_fields, list_fields)
            target_list[idx] = merged
            provenance[merged.get("uid", "")] = dict(stamp)
        else:  # pragma: no cover - defensive
            skipped += 1
            applied -= 1

    result["meta"]["source"] = source
    return ProfileData.model_validate(result), applied, skipped
