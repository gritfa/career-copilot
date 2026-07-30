"""purge cross-user private-job recommendations (P-1 item 1)

Revision ID: e5a7c9d2b4f6
Revises: d8b4f6a1c3e7
Create Date: 2026-07-30

阶段 11 P-1 第 1 项：清理历史跨用户推荐存量。

阶段 10 批 1（736ec79）只在候选池生成侧堵住了「私有岗位被推荐给非属主用户」，
本迁移清除此前已产生的存量泄露：推荐所属方案的用户 ≠ 私有岗位属主的
recommendations 行（含属主已注销、owner_user_id 为 NULL 的私有岗位——对任何
非属主一律不可见，用 IS DISTINCT FROM 处理 NULL 语义）。

级联关系（全部由外键自动处理，无需逐表删除）：
- match_components / user_feedback / agent_runs → ON DELETE CASCADE
- resume_versions.recommendation_id → ON DELETE SET NULL（用户简历本体保留）

downgrade 为 no-op：删除的是不该存在的泄露数据，不可也不应恢复。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a7c9d2b4f6"
down_revision: str | None = "d8b4f6a1c3e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# 测试从本模块导入同一条 SQL 验证语义（单一事实源，防复制漂移）
PURGE_SQL = """
DELETE FROM recommendations r
USING search_plans sp, canonical_jobs cj
WHERE sp.id = r.search_plan_id
  AND cj.id = r.canonical_job_id
  AND cj.visibility = 'private'
  AND cj.owner_user_id IS DISTINCT FROM sp.user_id
"""


def upgrade() -> None:
    op.execute(PURGE_SQL)


def downgrade() -> None:
    # 泄露数据的删除不可逆，且不应恢复；迁移链可正常回退（结构无变更）。
    pass
