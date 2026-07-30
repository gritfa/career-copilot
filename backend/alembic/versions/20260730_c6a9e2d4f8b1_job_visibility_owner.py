"""job visibility & ownership (phase 10 task B): private user imports, purge exception

Revision ID: c6a9e2d4f8b1
Revises: b3f7c1d9e6a2
Create Date: 2026-07-30

阶段 10 任务 B：修复用户导入岗位跨用户泄露。

- canonical_jobs 新增 visibility('global'|'private') + owner_user_id（FK users，SET NULL）。
- 存量回填（保守策略）：凡带有任一 user_import 来源 posting 的 canonical 一律标 private；
  owner 取该 canonical 下 postings 的唯一非空 imported_by_user_id，推断不出（0 个或多个
  不同导入者）则 owner=NULL——private+NULL owner 不参与任何推荐。纯连接器岗位维持 global。
- job_snapshots append-only 触发器增加唯一例外：会话内 set_config
  ('app.allow_snapshot_purge','1',true) 时允许 DELETE（数据主体注销硬删专用）；
  UPDATE 仍然一律禁止，证据不可篡改。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6a9e2d4f8b1"
down_revision: str | None = "b3f7c1d9e6a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BACKFILL_SQL = """
WITH import_stats AS (
    SELECT
        p.canonical_job_id AS cid,
        COUNT(*) FILTER (WHERE js.source_type = 'user_import') AS import_cnt,
        ARRAY(
            SELECT DISTINCT p2.imported_by_user_id
            FROM job_postings p2
            WHERE p2.canonical_job_id = p.canonical_job_id
              AND p2.imported_by_user_id IS NOT NULL
        ) AS owners
    FROM job_postings p
    JOIN job_sources js ON js.id = p.job_source_id
    WHERE p.canonical_job_id IS NOT NULL
    GROUP BY p.canonical_job_id
)
UPDATE canonical_jobs c
SET visibility = 'private',
    owner_user_id = CASE
        WHEN array_length(s.owners, 1) = 1 THEN s.owners[1]
        ELSE NULL
    END
FROM import_stats s
WHERE c.id = s.cid AND s.import_cnt > 0
"""

# 触发器：DELETE 仅在会话显式声明注销硬删时放行；UPDATE 永远拒绝
_TRIGGER_FN_WITH_PURGE_EXCEPTION = """
CREATE OR REPLACE FUNCTION job_snapshots_block_mutation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('app.allow_snapshot_purge', true) = '1' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'job_snapshots is append-only (immutable raw evidence)';
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_FN_ORIGINAL = """
CREATE OR REPLACE FUNCTION job_snapshots_block_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'job_snapshots is append-only (immutable raw evidence)';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.add_column(
        "canonical_jobs",
        sa.Column(
            "visibility", sa.String(length=16), nullable=False, server_default="global"
        ),
    )
    op.add_column(
        "canonical_jobs",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_canonical_jobs_owner_user_id",
        "canonical_jobs",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_canonical_jobs_visibility",
        "canonical_jobs",
        "visibility IN ('global', 'private')",
    )
    op.create_index(
        "ix_canonical_jobs_visibility_owner",
        "canonical_jobs",
        ["visibility", "owner_user_id"],
    )

    # 存量数据回填：含 user_import posting 的 canonical 一律 private（保守）
    op.execute(_BACKFILL_SQL)

    # 注销硬删例外（只放行 DELETE，且必须显式 set_config）
    op.execute(_TRIGGER_FN_WITH_PURGE_EXCEPTION)


def downgrade() -> None:
    op.execute(_TRIGGER_FN_ORIGINAL)
    op.drop_index("ix_canonical_jobs_visibility_owner", table_name="canonical_jobs")
    op.drop_constraint("ck_canonical_jobs_visibility", "canonical_jobs", type_="check")
    op.drop_constraint(
        "fk_canonical_jobs_owner_user_id", "canonical_jobs", type_="foreignkey"
    )
    op.drop_column("canonical_jobs", "owner_user_id")
    op.drop_column("canonical_jobs", "visibility")
