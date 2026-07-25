"""Strict allowlisted contracts for privacy-safe user error reports."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IssueType = Literal["tailor_generation_failed"]
ApiMethod = Literal["GET", "POST"]
ApiRoute = Literal[
    "/jobs/upload",
    "/resumes/improve/preview/stream",
    "/resumes/improve/preview",
    "/resumes/improve/preview/result/{requestId}",
]
PipelineStage = Literal[
    "keywords",
    "plan",
    "rewrite",
    "refine",
    "score",
]
StreamPhase = Literal["open", "before-event", "after-event"]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_ID_RE = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_SAFE_CODE_RE = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class ErrorReportCreate(BaseModel):
    """Only bounded, non-content metadata accepted from the authenticated client."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        populate_by_name=True,
    )

    client_report_id: str = Field(
        min_length=1, max_length=100, pattern=_SAFE_ID_RE, alias="clientReportId"
    )
    issue_type: IssueType = Field(alias="issueType")
    message: str = Field(min_length=1, max_length=500)
    error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_SAFE_CODE_RE,
        alias="errorCode",
    )
    http_status: int | None = Field(default=None, ge=100, le=599, alias="httpStatus")
    retryable: bool
    api_method: ApiMethod = Field(alias="apiMethod")
    api_route: ApiRoute = Field(alias="apiRoute")
    operation_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_SAFE_ID_RE,
        alias="operationRequestId",
    )
    api_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        pattern=_SAFE_ID_RE,
        alias="apiRequestId",
    )
    pipeline_stage: PipelineStage | None = Field(default=None, alias="pipelineStage")
    stream_phase: StreamPhase | None = Field(default=None, alias="streamPhase")
    fallback_safe: bool | None = Field(default=None, alias="fallbackSafe")

    @field_validator("message")
    @classmethod
    def _safe_message(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            raise ValueError("must not be empty")
        if _CONTROL_RE.search(candidate):
            raise ValueError("contains invalid control characters")
        return candidate


class ErrorReportCreated(BaseModel):
    """Small idempotent acknowledgement returned for new and duplicate reports."""

    model_config = ConfigDict(extra="forbid")

    reportId: str
    createdAt: str


class ErrorReportUser(BaseModel):
    """Current allowlisted user identity shown to an authorized admin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    email: str


class AdminErrorReport(BaseModel):
    """One allowlisted report row with its current user identity."""

    model_config = ConfigDict(extra="forbid")

    id: str
    userId: str
    clientReportId: str
    issueType: IssueType
    message: str
    errorCode: str | None = None
    httpStatus: int | None = None
    retryable: bool
    apiMethod: ApiMethod
    apiRoute: ApiRoute
    operationRequestId: str | None = None
    apiRequestId: str | None = None
    pipelineStage: PipelineStage | None = None
    streamPhase: StreamPhase | None = None
    fallbackSafe: bool | None = None
    createdAt: str
    user: ErrorReportUser


class AdminErrorReportList(BaseModel):
    """Newest-first keyset page of user error reports."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminErrorReport]
    nextCursor: str | None = None