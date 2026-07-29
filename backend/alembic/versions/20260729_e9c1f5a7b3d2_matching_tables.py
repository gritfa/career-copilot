"""matching / recommendations / vectors / usage ledger (phase 5)

Revision ID: e9c1f5a7b3d2
Revises: d7e3a5b9c2f4
Create Date: 2026-07-29

recommendations / match_components / user_feedback / job_vectors /
profile_vectors / usage_ledger（docs/03 第 7、8 节，docs/07 第 2～5 节）。

DB 层硬约束：
- recommendations：同一方案+岗位+日期唯一；hard_filter_status 只允许 passed/uncertain
  （failed 岗位不生成推荐行）。
- user_feedback：不感兴趣必须带 9 类结构化原因之一。
- job_vectors / profile_vectors：model_id + preprocess_version 入唯一键，
  禁止新旧模型向量混用（docs/07 第 3 节）；向量列固定 768 维并建 HNSW 余弦索引。
- 本迁移在目标库启用 pgvector 扩展（CREATE EXTENSION IF NOT EXISTS vector）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9c1f5a7b3d2"
down_revision: str | None = "d7e3a5b9c2f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ---------------- recommendations ----------------
    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("score_total", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("hard_filter_status", sa.String(length=16), nullable=False),
        sa.Column(
            "hard_conditions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("scoring_version", sa.String(length=32), nullable=False),
        sa.Column(
            "versions_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("recommended_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "score_total BETWEEN 0 AND 100", name="ck_recommendations_score_range"
        ),
        sa.CheckConstraint(
            "grade IN ('high', 'potential', 'low')", name="ck_recommendations_grade"
        ),
        sa.CheckConstraint(
            "hard_filter_status IN ('passed', 'uncertain')",
            name="ck_recommendations_hard_filter_status",
        ),
        sa.CheckConstraint("rank >= 1", name="ck_recommendations_rank_positive"),
        sa.ForeignKeyConstraint(
            ["search_plan_id"], ["search_plans.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_job_id"], ["canonical_jobs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "search_plan_id",
            "canonical_job_id",
            "recommended_on",
            name="uq_recommendations_plan_job_day",
        ),
    )
    op.create_index(
        "ix_recommendations_plan_day", "recommendations", ["search_plan_id", "recommended_on"]
    )

    # ---------------- match_components ----------------
    op.create_table(
        "match_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("component", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column(
            "evidence_refs_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("gap_level", sa.String(length=16), nullable=False),
        sa.Column("uncertainty", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "component IN ('core_skills', 'experience', 'project_evidence', "
            "'role_semantic', 'industry', 'preference')",
            name="ck_match_components_component",
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100", name="ck_match_components_score_range"
        ),
        sa.CheckConstraint(
            "weight BETWEEN 0 AND 100", name="ck_match_components_weight_range"
        ),
        sa.CheckConstraint(
            "gap_level IN ('none', 'minor', 'major', 'unknown')",
            name="ck_match_components_gap_level",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "recommendation_id", "component", name="uq_match_components_rec_component"
        ),
    )

    # ---------------- user_feedback ----------------
    op.create_table(
        "user_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sentiment", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("optional_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sentiment IN ('interested', 'not_interested')",
            name="ck_user_feedback_sentiment",
        ),
        sa.CheckConstraint(
            "reason_code IS NULL OR reason_code IN ('location', 'salary', 'company', "
            "'tech_direction', 'job_content', 'experience_education', 'outsourcing', "
            "'risk_concern', 'seen_duplicate')",
            name="ck_user_feedback_reason_code",
        ),
        sa.CheckConstraint(
            "NOT (sentiment = 'not_interested' AND reason_code IS NULL)",
            name="ck_user_feedback_not_interested_reason",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("recommendation_id", name="uq_user_feedback_recommendation"),
    )

    # ---------------- job_vectors / profile_vectors ----------------
    op.create_table(
        "job_vectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("preprocess_version", sa.String(length=16), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dim > 0", name="ck_job_vectors_dim_positive"),
        sa.ForeignKeyConstraint(
            ["canonical_job_id"], ["canonical_jobs.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "canonical_job_id",
            "model_id",
            "preprocess_version",
            name="uq_job_vectors_job_model_version",
        ),
    )
    # HNSW 余弦索引：召回按 cosine 距离排序（pgvector 0.8.x）
    op.execute(
        "CREATE INDEX ix_job_vectors_embedding_hnsw ON job_vectors "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "profile_vectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("preprocess_version", sa.String(length=16), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("dim > 0", name="ck_profile_vectors_dim_positive"),
        sa.ForeignKeyConstraint(
            ["search_plan_id"], ["search_plans.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "search_plan_id",
            "model_id",
            "preprocess_version",
            name="uq_profile_vectors_plan_model_version",
        ),
    )

    # ---------------- usage_ledger ----------------
    op.create_table(
        "usage_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "amount_estimated",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("tokens_in >= 0", name="ck_usage_ledger_tokens_in_nonnegative"),
        sa.CheckConstraint(
            "tokens_out >= 0", name="ck_usage_ledger_tokens_out_nonnegative"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_usage_ledger_user_occurred", "usage_ledger", ["user_id", "occurred_at"]
    )
    op.create_index(
        "ix_usage_ledger_provider_occurred", "usage_ledger", ["provider", "occurred_at"]
    )


def downgrade() -> None:
    op.drop_table("usage_ledger")
    op.drop_table("profile_vectors")
    op.drop_table("job_vectors")
    op.drop_table("user_feedback")
    op.drop_table("match_components")
    op.drop_table("recommendations")
    # 不 drop vector 扩展：可能被其他版本共享，扩展本身无状态残留
