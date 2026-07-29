"""resume & fact tables (phase 3)

Revision ID: c5a7d9e1f3b6
Revises: b2e4d6f8a1c3
Create Date: 2026-07-28

resumes / resume_parses / fact_candidates / profile_facts / fact_evidence
（docs/03 第 4 节）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c5a7d9e1f3b6"
down_revision: str | None = "b2e4d6f8a1c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resumes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("malware_scan_status", sa.String(length=32), nullable=False),
        sa.Column("text_extract_status", sa.String(length=32), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('uploaded', 'parsing', 'parsed', 'parse_failed', "
            "'deleting', 'deleted')",
            name="ck_resumes_status",
        ),
        sa.CheckConstraint(
            "malware_scan_status IN ('pending', 'clean', 'infected', "
            "'skipped_not_configured')",
            name="ck_resumes_malware_scan_status",
        ),
        sa.CheckConstraint(
            "text_extract_status IN ('pending', 'succeeded', 'no_text_layer', 'failed')",
            name="ck_resumes_text_extract_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_resumes_size_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("storage_key", name="uq_resumes_storage_key"),
    )
    op.create_index("ix_resumes_user_id", "resumes", ["user_id"])
    op.create_index("ix_resumes_user_sha256", "resumes", ["user_id", "sha256"])

    op.create_table(
        "resume_parses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parser_name", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("model_provider", sa.String(length=32), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("extracted_text_storage_key", sa.String(length=255), nullable=True),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("protected_discarded_count", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_resume_parses_status",
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("idempotency_key", name="uq_resume_parses_idempotency_key"),
    )
    op.create_index("ix_resume_parses_resume_id", "resume_parses", ["resume_id"])

    op.create_table(
        "fact_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("resume_parse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("source_span_start", sa.Integer(), nullable=False),
        sa.Column("source_span_end", sa.Integer(), nullable=False),
        sa.Column("source_quote_hash", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'edited', 'rejected')",
            name="ck_fact_candidates_status",
        ),
        sa.CheckConstraint(
            "source_span_end >= source_span_start",
            name="ck_fact_candidates_span_order",
        ),
        sa.ForeignKeyConstraint(
            ["resume_parse_id"], ["resume_parses.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_fact_candidates_parse_id", "fact_candidates", ["resume_parse_id"])

    op.create_table(
        "profile_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fact_type", sa.String(length=64), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provenance_type", sa.String(length=32), nullable=False),
        sa.Column("confirmed_by_user_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'revoked')",
            name="ck_profile_facts_status",
        ),
        sa.CheckConstraint(
            "provenance_type IN ('resume', 'user_answer', 'import')",
            name="ck_profile_facts_provenance",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["profile_facts.id"], ondelete="SET NULL"
        ),
    )
    op.create_index("ix_profile_facts_user_type", "profile_facts", ["user_id", "fact_type"])

    op.create_table(
        "fact_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_fact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resume_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_locator", postgresql.JSONB(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("display_excerpt", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["profile_fact_id"], ["profile_facts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_fact_evidence_fact_id", "fact_evidence", ["profile_fact_id"])
    op.create_index("ix_fact_evidence_resume_id", "fact_evidence", ["resume_id"])


def downgrade() -> None:
    op.drop_table("fact_evidence")
    op.drop_table("profile_facts")
    op.drop_table("fact_candidates")
    op.drop_table("resume_parses")
    op.drop_table("resumes")
