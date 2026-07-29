"""admin/privacy ops (phase 8): suspended status, quota overrides, data_exports, purge runs

Revision ID: b3f7c1d9e6a2
Revises: a8c2e6d4b9f1
Create Date: 2026-07-29

阶段 8（ADR-001 减配：只读管理视图 + CLI；数据主体权利不减）：

- users：新增 suspended 状态（CLI 封禁）、suspended_at、quota_overrides_json（额度覆盖）。
- data_exports：用户全量数据导出（ZIP），succeeded 必须有文件三元组，failed 绝不留 key。
- account_purge_runs：注销硬删任务 + 可验证清单（只有计数，无 PII）；
  user_id 不建 FK——用户行删除后凭证仍保留；任一子项失败整体不得标成功。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f7c1d9e6a2"
down_revision: str | None = "a8c2e6d4b9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- users：suspended 状态 + 额度覆盖 ----
    op.add_column(
        "users", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users",
        sa.Column("quota_overrides_json", postgresql.JSONB(), nullable=True),
    )
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.create_check_constraint(
        "ck_users_status",
        "users",
        "status IN ('active', 'suspended', 'deletion_pending')",
    )

    # ---- data_exports ----
    op.create_table(
        "data_exports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("file_purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("storage_key"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_data_exports_status",
        ),
        sa.CheckConstraint(
            "NOT (status = 'succeeded' AND (storage_key IS NULL "
            "OR file_sha256 IS NULL OR expires_at IS NULL))",
            name="ck_data_exports_succeeded_has_file",
        ),
        sa.CheckConstraint(
            "NOT (status = 'failed' AND storage_key IS NOT NULL)",
            name="ck_data_exports_failed_no_file",
        ),
    )
    op.create_index(
        "ix_data_exports_user_created", "data_exports", ["user_id", "created_at"]
    )

    # ---- account_purge_runs（无 FK：删除凭证要活得比用户行久） ----
    op.create_table(
        "account_purge_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_account_purge_runs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_account_purge_runs_attempts_nonnegative"
        ),
        sa.CheckConstraint(
            "NOT (status = 'succeeded' AND completed_at IS NULL)",
            name="ck_account_purge_runs_succeeded_completed",
        ),
    )
    op.create_index("ix_account_purge_runs_user", "account_purge_runs", ["user_id"])
    op.create_index("ix_account_purge_runs_status", "account_purge_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_account_purge_runs_status", table_name="account_purge_runs")
    op.drop_index("ix_account_purge_runs_user", table_name="account_purge_runs")
    op.drop_table("account_purge_runs")
    op.drop_index("ix_data_exports_user_created", table_name="data_exports")
    op.drop_table("data_exports")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.create_check_constraint(
        "ck_users_status", "users", "status IN ('active', 'deletion_pending')"
    )
    op.drop_column("users", "quota_overrides_json")
    op.drop_column("users", "suspended_at")
