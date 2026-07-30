"""canonical_jobs.data_origin（阶段 11 P1：种子数据双重标注之 DB 层）

Revision ID: f7c1a3e9d5b8
Revises: e5a7c9d2b4f6
Create Date: 2026-07-30

新增 canonical_jobs.data_origin 受控字段：
- connector：连接器采集（当前仅合成 fixture 连接器）
- user_import：用户手动导入（正文/URL）
- synthetic_seed：演示种子数据（前端必须展示「合成示例」徽标）

存量回填：private 岗位只可能来自用户导入 → user_import；
global 岗位当前只可能来自连接器 → connector。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c1a3e9d5b8"
down_revision: str | None = "e5a7c9d2b4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "canonical_jobs",
        sa.Column(
            "data_origin",
            sa.String(length=32),
            nullable=False,
            server_default="user_import",
        ),
    )
    # 存量回填：可见性即当前唯一可靠的来源信号（global=连接器，private=用户导入）
    op.execute("UPDATE canonical_jobs SET data_origin = 'connector' WHERE visibility = 'global'")
    op.create_check_constraint(
        "ck_canonical_jobs_data_origin",
        "canonical_jobs",
        "data_origin IN ('connector', 'user_import', 'synthetic_seed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_canonical_jobs_data_origin", "canonical_jobs", type_="check")
    op.drop_column("canonical_jobs", "data_origin")
