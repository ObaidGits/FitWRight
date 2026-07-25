"""privacy-safe user error reports

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-25 00:00:03.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ALLOWED_ROUTES = (
    "'/jobs/upload',"
    "'/resumes/improve/preview/stream',"
    "'/resumes/improve/preview',"
    "'/resumes/improve/preview/result/{requestId}'"
)


def upgrade() -> None:
    """Create bounded, owner-scoped, idempotent error-report storage."""
    op.create_table(
        "user_error_reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("client_report_id", sa.String(length=100), nullable=False),
        sa.Column("issue_type", sa.String(length=40), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("api_method", sa.String(length=8), nullable=False),
        sa.Column("api_route", sa.String(length=100), nullable=False),
        sa.Column("operation_request_id", sa.String(length=100), nullable=True),
        sa.Column("api_request_id", sa.String(length=100), nullable=True),
        sa.Column("pipeline_stage", sa.String(length=32), nullable=True),
        sa.Column("stream_phase", sa.String(length=16), nullable=True),
        sa.Column("fallback_safe", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint(
            "length(client_report_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_client_report_id_length",
        ),
        sa.CheckConstraint(
            "length(message) BETWEEN 1 AND 500",
            name="ck_user_error_reports_message_length",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR length(error_code) BETWEEN 1 AND 100",
            name="ck_user_error_reports_error_code_length",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status BETWEEN 100 AND 599)",
            name="ck_user_error_reports_http_status",
        ),
        sa.CheckConstraint(
            "issue_type = 'tailor_generation_failed'",
            name="ck_user_error_reports_issue_type",
        ),
        sa.CheckConstraint(
            "api_method IN ('GET','POST')",
            name="ck_user_error_reports_api_method",
        ),
        sa.CheckConstraint(
            f"api_route IN ({''.join(_ALLOWED_ROUTES)})",
            name="ck_user_error_reports_api_route",
        ),
        sa.CheckConstraint(
            "operation_request_id IS NULL OR "
            "length(operation_request_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_operation_request_id_length",
        ),
        sa.CheckConstraint(
            "api_request_id IS NULL OR length(api_request_id) BETWEEN 1 AND 100",
            name="ck_user_error_reports_api_request_id_length",
        ),
        sa.CheckConstraint(
            "pipeline_stage IS NULL OR pipeline_stage IN "
            "('keywords','plan','rewrite','refine','score')",
            name="ck_user_error_reports_pipeline_stage",
        ),
        sa.CheckConstraint(
            "stream_phase IS NULL OR stream_phase IN "
            "('open','before-event','after-event')",
            name="ck_user_error_reports_stream_phase",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "client_report_id", name="uq_user_error_reports_user_client"
        ),
    )
    op.create_index(
        "ix_user_error_reports_created_at_id",
        "user_error_reports",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_user_error_reports_user_created_at_id",
        "user_error_reports",
        ["user_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop error-report storage and its indexes."""
    op.drop_index(
        "ix_user_error_reports_user_created_at_id", table_name="user_error_reports"
    )
    op.drop_index(
        "ix_user_error_reports_created_at_id", table_name="user_error_reports"
    )
    op.drop_table("user_error_reports")