"""agent_runs (phase 6, single-model standard analysis)

Revision ID: f4b6d8a2c1e5
Revises: e9c1f5a7b3d2
Create Date: 2026-07-29

单模型标准分析任务表（docs/03 第 7 节 agent_runs，ADR-001 裁剪版）。

DB 层硬约束：
- status 只允许 queued/analyzing/validating/completed/failed（用户可见状态语义，
  docs/07 第 8.4 节）；trigger 只允许 auto/manual。
- completed 必须有 final_report_json，其他状态必须为 NULL——失败不得伪装完成，
  也绝不落半成品报告。
- 不保存模型思维链/内部对话，只有结构化最终报告与费用计数。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4b6d8a2c1e5"
down_revision: str | None = "e9c1f5a7b3d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recommendation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("graph_version", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column(
            "model_map_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_stage", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("output_schema_version", sa.String(length=32), nullable=False),
        sa.Column("final_report_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("cost_tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "cost_amount", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'analyzing', 'validating', 'completed', 'failed')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('auto', 'manual')", name="ck_agent_runs_trigger"
        ),
        sa.CheckConstraint(
            "cost_tokens_in >= 0", name="ck_agent_runs_tokens_in_nonnegative"
        ),
        sa.CheckConstraint(
            "cost_tokens_out >= 0", name="ck_agent_runs_tokens_out_nonnegative"
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (final_report_json IS NOT NULL)",
            name="ck_agent_runs_report_iff_completed",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_agent_runs_recommendation", "agent_runs", ["recommendation_id", "created_at"]
    )
    op.create_index(
        "ix_agent_runs_user_created", "agent_runs", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("agent_runs")
