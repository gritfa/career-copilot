"""company normalization (phase 10 task D): normalized name keys + alias review queue

Revision ID: d8b4f6a1c3e7
Revises: c6a9e2d4f8b1
Create Date: 2026-07-30

阶段 10 任务 D：公司标准化基础。

- companies 新增 name_normalized / name_core / name_rules_version 三列并回填
  （回填直接调用 app.companies.normalize 的 company_norm_v1 规则；规则版本随行落库，
  未来规则升级用新版本号重算，不篡改历史标注）。
- 新增 company_alias_reviews：core 键相同 / normalized 多义的低置信候选进人工审核，
  绝不凭字符串相似自动合并公司主体。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.companies.normalize import normalize_company_name

# revision identifiers, used by Alembic.
revision: str = "d8b4f6a1c3e7"
down_revision: str | None = "c6a9e2d4f8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("name_normalized", sa.String(length=255)))
    op.add_column("companies", sa.Column("name_core", sa.String(length=255)))
    op.add_column("companies", sa.Column("name_rules_version", sa.String(length=32)))
    op.add_column(
        "companies",
        sa.Column("merged_into_company_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_foreign_key(
        "fk_companies_merged_into_company_id",
        "companies",
        "companies",
        ["merged_into_company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_companies_name_normalized", "companies", ["name_normalized"])
    op.create_index("ix_companies_name_core", "companies", ["name_core"])

    # 回填存量公司（规模小，逐行 UPDATE；标准化失败的行保持 NULL 不猜）
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, canonical_name FROM companies")).fetchall()
    for company_id, canonical_name in rows:
        norm = normalize_company_name(canonical_name)
        if norm is None:
            continue
        conn.execute(
            sa.text(
                "UPDATE companies SET name_normalized = :n, name_core = :c, "
                "name_rules_version = :v WHERE id = :id"
            ),
            {
                "n": norm.normalized,
                "c": norm.core,
                "v": norm.rules_version,
                "id": company_id,
            },
        )

    op.create_table(
        "company_alias_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("raw_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("core_name", sa.String(length=255), nullable=False),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("match_rule", sa.String(length=64), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column(
            "evidence_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(length=128)),
        sa.Column("resolution_note", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_company_alias_reviews_status",
        ),
        sa.CheckConstraint(
            "company_id <> candidate_company_id",
            name="ck_company_alias_reviews_distinct_companies",
        ),
        sa.UniqueConstraint(
            "company_id", "candidate_company_id", name="uq_company_alias_reviews_pair"
        ),
    )
    op.create_index("ix_company_alias_reviews_status", "company_alias_reviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_company_alias_reviews_status", table_name="company_alias_reviews")
    op.drop_table("company_alias_reviews")
    op.drop_index("ix_companies_name_core", table_name="companies")
    op.drop_index("ix_companies_name_normalized", table_name="companies")
    op.drop_constraint(
        "fk_companies_merged_into_company_id", "companies", type_="foreignkey"
    )
    op.drop_column("companies", "merged_into_company_id")
    op.drop_column("companies", "name_rules_version")
    op.drop_column("companies", "name_core")
    op.drop_column("companies", "name_normalized")
