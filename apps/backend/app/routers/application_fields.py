"""Application field registry - the learning loop's API.

When a job application form asks something the extension cannot answer, that
question is recorded here as ``needs_answer``. The user answers it once in
Settings and every future form fills it, matched on the label the site actually
used rather than on a guess.

Two rules run through everything in this module:

* **A field holds a value OR a pointer, never both.** When a question maps onto
  something the user's Profile already models, ``profile_path`` is set and the
  answer is resolved live from the Profile at read time. Storing a copy would
  leave a stale duplicate that silently wins after they edit their Profile.
* **Labels and types only, never values.** The form report tells us what a form
  asked, not what anyone typed into it. Password fields are refused outright, and
  a payload that smuggles a value is rejected rather than quietly stored - this
  endpoint runs on every application form the user opens, so it must not become a
  transcript of their private answers.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from app.auth import get_effective_user_id
from app.database import Database

router = APIRouter(tags=["application-fields"])


def get_db() -> Database:
    """Return the process-wide database (overridable in tests).

    Defined per router in this codebase rather than shared, so a test can swap
    the database for one router without affecting the others.
    """
    from app.database import db

    return db


# Questions whose wrong answer silently rejects the application. They may only be
# answered from a stored fact - never inferred, never AI-drafted.
KNOCKOUT_KEYS = frozenset(
    {
        "work_authorization",
        "visa_status",
        "requires_sponsorship",
        "years_experience",
        "salary_expectation",
        "notice_period",
        "willing_to_relocate",
    }
)

# Never store or echo these, whatever a caller sends.
_VALUE_LIKE_KEYS = frozenset({"value", "values", "answer", "answers", "text", "input", "password"})

_FIELD_TYPES = frozenset(
    {"text", "textarea", "select", "radio", "checkbox", "date", "number", "file"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_label(label: str) -> str:
    """Reduce a form label to a stable matching key.

    ATS labels differ cosmetically far more than they differ in meaning:
    "Years of Python *", "years of python?" and "Years  of  Python" are one
    question. Case, punctuation, the required-marker asterisk and repeated
    whitespace are therefore all discarded.
    """
    text = (label or "").strip().lower()
    text = re.sub(r"[\*\u2217]+", " ", text)  # required markers
    text = re.sub(r"[^\w\s]+", " ", text)  # punctuation
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Wire models
# --------------------------------------------------------------------------- #
class ReportedField(BaseModel):
    """One field the extension saw on a form."""

    label: str
    field_type: str = "text"
    options: list[str] = Field(default_factory=list)
    filled: bool = False
    matched_key: str | None = None

    @field_validator("field_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        value = (value or "text").strip().lower()
        return value if value in _FIELD_TYPES else "text"

    @field_validator("label")
    @classmethod
    def _label_present(cls, value: str) -> str:
        if not (value or "").strip():
            raise ValueError("label is required")
        return value.strip()[:400]


class FormReport(BaseModel):
    """A form the extension just filled, described by its labels alone."""

    fields: list[ReportedField] = Field(default_factory=list, max_length=300)
    company: str | None = None
    ats: str | None = None
    url: str | None = None

    @field_validator("fields", mode="before")
    @classmethod
    def _strip_value_like(cls, value: Any) -> Any:
        """Drop any value-like key before it can reach the database.

        Defence in depth: `ReportedField` already ignores unknown keys, but a
        future edit could add one, and this endpoint sees every form the user
        opens. Rejecting loudly here means such a mistake fails a test rather
        than silently building a record of their answers.
        """
        if not isinstance(value, list):
            return value
        for item in value:
            if isinstance(item, dict):
                leaked = _VALUE_LIKE_KEYS.intersection(k.lower() for k in item)
                if leaked:
                    raise ValueError(
                        f"form reports carry labels and types only; refusing keys: {sorted(leaked)}"
                    )
        return value


class FieldOut(BaseModel):
    """A registry row, with any Profile pointer already resolved."""

    id: str
    label: str
    label_normalized: str
    synonyms: list[str] = Field(default_factory=list)
    field_type: str
    options: list[str] = Field(default_factory=list)
    value: Any | None = None
    profile_path: str | None = None
    # True when `value` was read live from the Profile rather than stored here.
    from_profile: bool = False
    scope: str
    company: str | None = None
    status: str
    source: str
    is_knockout: bool = False
    times_seen: int
    last_seen_at: str | None = None
    last_seen_url: str | None = None
    last_seen_ats: str | None = None


class FieldUpdate(BaseModel):
    value: Any | None = None
    field_type: str | None = None
    scope: str | None = None
    company: str | None = None
    status: str | None = None
    profile_path: str | None = None
    label: str | None = None


class MergeRequest(BaseModel):
    """Fold `other_id`'s label into this field's synonyms."""

    other_id: str


class ReportResult(BaseModel):
    seen: int
    created: int
    updated: int
    needs_answer: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post(
    "/extension/form-report",
    response_model=ReportResult,
    summary="Record the fields a form asked for",
)
async def report_form(
    report: FormReport,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> ReportResult:
    """Upsert every field a form asked for, queueing the unanswered ones.

    Called after each autofill. A field already answered still gets reported, so
    ``times_seen`` reflects how often a question actually comes up - that is what
    lets Settings lead with what matters instead of one-off junk.
    """
    created = updated = needs = 0

    for field in report.fields:
        normalized = normalize_label(field.label)
        if not normalized:
            continue

        # An unfilled field is a question we could not answer, so it goes to the
        # review queue. A filled one is recorded for its count and last-seen only.
        row_status = "answered" if field.filled else "needs_answer"
        if not field.filled:
            needs += 1

        was_created = await db.upsert_application_field(
            user_id,
            label=field.label,
            label_normalized=normalized,
            field_type=field.field_type,
            options=field.options,
            status=row_status,
            source="learned",
            company=report.company,
            last_seen_url=report.url,
            last_seen_ats=report.ats,
            last_seen_at=_now(),
        )
        if was_created:
            created += 1
        else:
            updated += 1

    return ReportResult(
        seen=len(report.fields), created=created, updated=updated, needs_answer=needs
    )


class SubmittedAnswer(BaseModel):
    """One answer the user typed on a form and chose to keep."""

    label: str
    value: Any
    field_type: str = "text"
    options: list[str] = Field(default_factory=list)

    @field_validator("field_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        value = (value or "text").strip().lower()
        return value if value in _FIELD_TYPES else "text"

    @field_validator("label")
    @classmethod
    def _label_present(cls, value: str) -> str:
        if not (value or "").strip():
            raise ValueError("label is required")
        return value.strip()[:400]


class SaveAnswers(BaseModel):
    """Answers the user explicitly asked to remember."""

    answers: list[SubmittedAnswer] = Field(default_factory=list, max_length=100)
    company: str | None = None
    ats: str | None = None
    url: str | None = None


class SaveAnswersResult(BaseModel):
    saved: int


@router.post(
    "/extension/answers",
    response_model=SaveAnswersResult,
    summary="Remember answers the user typed on a form",
)
async def save_answers(
    body: SaveAnswers,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> SaveAnswersResult:
    """Store answers the user typed on a form and chose to keep.

    This is the one place values are accepted, and the distinction from
    ``/form-report`` is consent rather than convenience. Form reporting happens
    automatically on every application the user opens, so it takes labels only.
    Saving answers happens because they pressed a button that says so, which is
    the whole point of the feature: teach FitWright from the form in front of you
    instead of retyping it in Settings later.

    An answer that maps onto a Profile field is still stored here rather than
    written into the Profile: silently rewriting curated Profile data from a form
    would be a surprise, and Settings can offer that promotion explicitly.
    """
    saved = 0
    for answer in body.answers:
        normalized = normalize_label(answer.label)
        if not normalized:
            continue

        await db.upsert_application_field(
            user_id,
            label=answer.label,
            label_normalized=normalized,
            field_type=answer.field_type,
            options=answer.options,
            status="answered",
            source="user",
            company=body.company,
            last_seen_url=body.url,
            last_seen_ats=body.ats,
            last_seen_at=_now(),
        )
        # Set the value in a second step: upsert deliberately never overwrites an
        # existing answer, but here the user has just told us what it should be.
        await db.set_application_field_value(
            user_id,
            label_normalized=normalized,
            company=body.company,
            value=answer.value,
        )
        saved += 1

    return SaveAnswersResult(saved=saved)


@router.get("/application-fields", response_model=list[FieldOut], summary="List answers")
async def list_fields(
    status_filter: str | None = None,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> list[FieldOut]:
    """Every known field, newest-needed first, with pointers resolved."""
    rows = await db.list_application_fields(user_id, status=status_filter)
    profile_row = await db.get_profile(user_id)
    document = _profile_document(profile_row)
    return [_to_out(row, document) for row in rows]


@router.patch(
    "/application-fields/{field_id}", response_model=FieldOut, summary="Answer or edit a field"
)
async def update_field(
    field_id: str,
    patch: FieldUpdate,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> FieldOut:
    """Set an answer, retype it, rescope it, or point it at the Profile."""
    changes = patch.model_dump(exclude_unset=True)

    if "field_type" in changes and changes["field_type"] not in _FIELD_TYPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown field_type")
    if "scope" in changes and changes["scope"] not in {"global", "company"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "scope must be global or company")
    if "status" in changes and changes["status"] not in {"needs_answer", "answered", "ignored"}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "unknown status")

    # The value-or-pointer rule. Setting one clears the other, so the two can
    # never disagree about what the answer is.
    if changes.get("profile_path"):
        changes["value"] = None
    elif "value" in changes and changes["value"] is not None:
        changes["profile_path"] = None

    # Answering a field resolves it, without the client having to say so.
    if ("value" in changes or "profile_path" in changes) and "status" not in changes:
        changes["status"] = "answered"

    if "label" in changes and changes["label"]:
        changes["label_normalized"] = normalize_label(changes["label"])

    row = await db.update_application_field(user_id, field_id, changes)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")

    document = _profile_document(await db.get_profile(user_id))
    return _to_out(row, document)


@router.delete(
    "/application-fields/{field_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Forget a field",
)
async def delete_field(
    field_id: str,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> None:
    if not await db.delete_application_field(user_id, field_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")


@router.post(
    "/application-fields/{field_id}/merge",
    response_model=FieldOut,
    summary="Merge a duplicate question into this one",
)
async def merge_field(
    field_id: str,
    body: MergeRequest,
    user_id: str = Depends(get_effective_user_id),
    db: Database = Depends(get_db),
) -> FieldOut:
    """Fold another row's label into this one's synonyms and delete it.

    Two labels for one question ("Years of Python" / "Python (years)") would
    otherwise each need answering. Merging keeps this row's answer and teaches it
    the other wording, so both forms fill from one answer.
    """
    if body.other_id == field_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "cannot merge a field into itself")

    row = await db.merge_application_fields(user_id, field_id, body.other_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "field not found")

    document = _profile_document(await db.get_profile(user_id))
    return _to_out(row, document)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _profile_document(row: dict[str, Any] | None) -> dict[str, Any]:
    """The profile's JSON document as a dict, whatever the driver returned."""
    if not row:
        return {}
    data = row.get("data")
    if isinstance(data, str):
        import json

        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def resolve_profile_path(document: dict[str, Any], path: str) -> Any | None:
    """Read a dotted path out of the profile document.

    Supports list indexes (``education.0.degree``) because the answer to "highest
    degree" lives in the first education entry rather than at a fixed key.
    """
    current: Any = document
    for part in (path or "").split("."):
        if not part:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _to_out(row: dict[str, Any], document: dict[str, Any]) -> FieldOut:
    """Row -> wire model, resolving a Profile pointer to its live value."""
    pointer = row.get("profile_path")
    value = row.get("value")
    from_profile = False
    if pointer:
        value = resolve_profile_path(document, pointer)
        from_profile = True

    return FieldOut(
        id=row["id"],
        label=row["label"],
        label_normalized=row["label_normalized"],
        synonyms=row.get("synonyms") or [],
        field_type=row.get("field_type") or "text",
        options=row.get("options") or [],
        value=value,
        profile_path=pointer,
        from_profile=from_profile,
        scope=row.get("scope") or "global",
        company=row.get("company"),
        status=row.get("status") or "needs_answer",
        source=row.get("source") or "learned",
        is_knockout=(row.get("label_normalized") or "").replace(" ", "_") in KNOCKOUT_KEYS
        or _looks_like_knockout(row.get("label_normalized") or ""),
        times_seen=int(row.get("times_seen") or 1),
        last_seen_at=row.get("last_seen_at"),
        last_seen_url=row.get("last_seen_url"),
        last_seen_ats=row.get("last_seen_ats"),
    )


def _looks_like_knockout(normalized: str) -> bool:
    """Flag screening questions so the UI can mark them as high-stakes.

    Advisory only - it drives a warning badge in Settings, never an auto-answer.
    """
    return bool(
        re.search(
            r"sponsor|visa|work authoriz|legally.*work|right to work|notice period"
            r"|salary|compensation expect|relocat|years? of experience",
            normalized,
        )
    )
