"""resume_versions + resume_exports (phase 7, tailored resume & export)

Revision ID: a8c2e6d4b9f1
Revises: f4b6d8a2c1e5
Create Date: 2026-07-29

阶段 7（ADR-001 裁剪：单模板、job_tailored 主路径）：

DB 层硬约束：
- resume_versions：confirmed 必须有 content_json + confirmed_at；
  failed 绝不留 content_json（失败不得伪装完成、不落半成品）。
- resume_exports：succeeded 必须有 storage_key/file_sha256/expires_at；
  failed 绝不留 storage_key。文件短时保留，过期清理（file_purged_at）。
- 两表都不保存简历正文以外的 PII 元数据；storage_key 只含 UUID。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c2e6d4b9f1"
down_revision: str | None = "f4b6d8a2c1e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resume_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("search_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("canonical_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=16), nullable=False),
        sa.Column("content_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "changes_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "generator_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("edited_by_user_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('plan_base', 'job_tailored')", name="ck_resume_versions_kind"
        ),
        sa.CheckConstraint(
            "status IN ('generating', 'draft', 'confirmed', 'failed', 'deleted')",
            name="ck_resume_versions_status",
        ),
        sa.CheckConstraint(
            "created_by IN ('user', 'agent_draft')",
            name="ck_resume_versions_created_by",
        ),
        sa.CheckConstraint(
            "NOT (status = 'confirmed' AND (content_json IS NULL OR confirmed_at IS NULL))",
            name="ck_resume_versions_confirmed_has_content",
        ),
        sa.CheckConstraint(
            "NOT (status = 'failed' AND content_json IS NOT NULL)",
            name="ck_resume_versions_failed_no_content",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["search_plan_id"], ["search_plans.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_job_id"], ["canonical_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["resume_versions.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "ix_resume_versions_user_created", "resume_versions", ["user_id", "created_at"]
    )
    op.create_index(
        "ix_resume_versions_recommendation", "resume_versions", ["recommendation_id"]
    )

    op.create_table(
        "resume_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True, unique=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("format IN ('docx', 'pdf')", name="ck_resume_exports_format"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_resume_exports_status",
        ),
        sa.CheckConstraint(
            "NOT (status = 'succeeded' AND (storage_key IS NULL "
            "OR file_sha256 IS NULL OR expires_at IS NULL))",
            name="ck_resume_exports_succeeded_has_file",
        ),
        sa.CheckConstraint(
            "NOT (status = 'failed' AND storage_key IS NOT NULL)",
            name="ck_resume_exports_failed_no_file",
        ),
        sa.ForeignKeyConstraint(
            ["resume_version_id"], ["resume_versions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_resume_exports_version", "resume_exports", ["resume_version_id"])
    op.create_index(
        "ix_resume_exports_user_created", "resume_exports", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_resume_exports_user_created", table_name="resume_exports")
    op.drop_index("ix_resume_exports_version", table_name="resume_exports")
    op.drop_table("resume_exports")
    op.drop_index("ix_resume_versions_recommendation", table_name="resume_versions")
    op.drop_index("ix_resume_versions_user_created", table_name="resume_versions")
    op.drop_table("resume_versions")
