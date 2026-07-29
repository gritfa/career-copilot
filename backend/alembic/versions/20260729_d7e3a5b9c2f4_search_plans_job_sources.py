"""search plans & job source tables (phase 4)

Revision ID: d7e3a5b9c2f4
Revises: c5a7d9e1f3b6
Create Date: 2026-07-29

search_plans / companies / company_preferences / learned_preferences /
job_sources / source_runs / job_snapshots / canonical_jobs / job_postings
（docs/03 第 5、6 节）。

DB 层硬约束：
- search_plans：每用户最多 3 个 active —— 触发器内先取 advisory xact lock
  再计数（READ COMMITTED 下 volatile 函数语句取新快照，并发安全）。
- job_snapshots：append-only 触发器拒绝 UPDATE/DELETE（不可变原始证据）。
- source_runs：CHECK 保证 items_failed > 0 时状态不可能是 success。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e3a5b9c2f4"
down_revision: str | None = "c5a7d9e1f3b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- search_plans ----------------
    op.create_table(
        "search_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role_family", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("city_codes", postgresql.ARRAY(sa.String(length=12)), nullable=False),
        sa.Column("work_modes", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("salary_currency", sa.String(length=8), nullable=False),
        sa.Column("minimum_monthly_salary", sa.Integer(), nullable=True),
        sa.Column("target_monthly_salary", sa.Integer(), nullable=True),
        sa.Column("salary_months_preference", sa.Integer(), nullable=True),
        sa.Column("minimum_match_score", sa.Integer(), nullable=False, server_default="65"),
        sa.Column(
            "allow_outsourcing", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("base_resume_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')", name="ck_search_plans_status"
        ),
        sa.CheckConstraint("salary_currency = 'CNY'", name="ck_search_plans_currency_cny"),
        sa.CheckConstraint(
            "minimum_monthly_salary IS NULL OR minimum_monthly_salary > 0",
            name="ck_search_plans_min_salary_positive",
        ),
        sa.CheckConstraint(
            "target_monthly_salary IS NULL OR target_monthly_salary > 0",
            name="ck_search_plans_target_salary_positive",
        ),
        sa.CheckConstraint(
            "minimum_monthly_salary IS NULL OR target_monthly_salary IS NULL "
            "OR minimum_monthly_salary <= target_monthly_salary",
            name="ck_search_plans_min_le_target",
        ),
        sa.CheckConstraint(
            "salary_months_preference IS NULL OR salary_months_preference BETWEEN 12 AND 18",
            name="ck_search_plans_salary_months_range",
        ),
        sa.CheckConstraint(
            "minimum_match_score BETWEEN 0 AND 100",
            name="ck_search_plans_match_score_range",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_search_plans_user_id", "search_plans", ["user_id"])

    # 每用户最多 3 个 active：advisory xact lock + 计数触发器（并发安全）
    op.execute(
        """
        CREATE OR REPLACE FUNCTION search_plans_enforce_active_limit() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'active' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtext('search_plans_active:' || NEW.user_id::text)
                );
                IF (SELECT count(*) FROM search_plans
                    WHERE user_id = NEW.user_id
                      AND status = 'active'
                      AND id <> NEW.id) >= 3 THEN
                    RAISE EXCEPTION 'active search plan limit (3) exceeded'
                        USING ERRCODE = 'check_violation',
                              CONSTRAINT = 'ck_search_plans_max_active';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_search_plans_active_limit
        BEFORE INSERT OR UPDATE OF status ON search_plans
        FOR EACH ROW EXECUTE FUNCTION search_plans_enforce_active_limit();
        """
    )

    # ---------------- companies ----------------
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("official_domain", sa.String(length=255), nullable=True),
        sa.Column("city_codes", postgresql.ARRAY(sa.String(length=12)), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False),
        sa.Column("source_refs_json", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verified')",
            name="ck_companies_verification_status",
        ),
        sa.UniqueConstraint("canonical_name", name="uq_companies_canonical_name"),
    )

    # ---------------- company_preferences ----------------
    op.create_table(
        "company_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("search_plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("preference", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "preference IN ('follow', 'priority', 'block')",
            name="ck_company_preferences_preference",
        ),
        sa.ForeignKeyConstraint(["search_plan_id"], ["search_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "search_plan_id", "company_id", name="uq_company_preferences_plan_company"
        ),
    )

    # ---------------- learned_preferences ----------------
    op.create_table(
        "learned_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("weights_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("derived_from", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'reset')", name="ck_learned_preferences_status"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_learned_preferences_user_id", "learned_preferences", ["user_id"])

    # ---------------- job_sources ----------------
    op.create_table(
        "job_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("access_policy_url", sa.String(length=255), nullable=True),
        sa.Column("robots_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rate_limit_config", postgresql.JSONB(), nullable=False),
        sa.Column("capabilities_json", postgresql.JSONB(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('company_site', 'platform', 'user_import')",
            name="ck_job_sources_source_type",
        ),
        sa.CheckConstraint(
            "status IN ('enabled', 'paused', 'circuit_open', 'disabled')",
            name="ck_job_sources_status",
        ),
        sa.UniqueConstraint("source_key", name="uq_job_sources_source_key"),
    )

    # ---------------- source_runs ----------------
    op.create_table(
        "source_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False),
        sa.Column("items_new", sa.Integer(), nullable=False),
        sa.Column("items_failed", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', 'failed')",
            name="ck_source_runs_status",
        ),
        sa.CheckConstraint(
            "NOT (status = 'success' AND items_failed > 0)",
            name="ck_source_runs_success_requires_no_failures",
        ),
        sa.ForeignKeyConstraint(["job_source_id"], ["job_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_source_id", "run_key", name="uq_source_runs_source_run_key"),
    )

    # ---------------- job_snapshots（不可变） ----------------
    op.create_table(
        "job_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("http_meta_json", postgresql.JSONB(), nullable=True),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_source_id"], ["job_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("storage_key", name="uq_job_snapshots_storage_key"),
    )
    op.create_index("ix_job_snapshots_source_id", "job_snapshots", ["job_source_id"])
    op.create_index("ix_job_snapshots_content_hash", "job_snapshots", ["content_hash"])
    # append-only：原始快照不可变（人工修正写派生层 job_postings）
    op.execute(
        """
        CREATE OR REPLACE FUNCTION job_snapshots_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'job_snapshots is append-only (immutable raw evidence)';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_job_snapshots_append_only
        BEFORE UPDATE OR DELETE ON job_snapshots
        FOR EACH ROW EXECUTE FUNCTION job_snapshots_block_mutation();
        """
    )

    # ---------------- canonical_jobs ----------------
    op.create_table(
        "canonical_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title_normalized", sa.String(length=255), nullable=False),
        sa.Column("role_family", sa.String(length=32), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("city_code", sa.String(length=12), nullable=True),
        sa.Column("primary_posting_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedupe_version", sa.String(length=16), nullable=False),
        sa.Column("dedupe_confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "review_status IN ('auto', 'pending_review', 'confirmed')",
            name="ck_canonical_jobs_review_status",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'unknown')",
            name="ck_canonical_jobs_status",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_canonical_jobs_company_city", "canonical_jobs", ["company_id", "city_code"]
    )

    # ---------------- job_postings ----------------
    op.create_table(
        "job_postings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_job_id", sa.String(length=128), nullable=True),
        sa.Column("canonical_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title_raw", sa.String(length=255), nullable=False),
        sa.Column("title_normalized", sa.String(length=255), nullable=False),
        sa.Column("role_family", sa.String(length=32), nullable=False),
        sa.Column("role_family_confidence", sa.Float(), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=True),
        sa.Column("city_code", sa.String(length=12), nullable=True),
        sa.Column("city_kind", sa.String(length=16), nullable=False),
        sa.Column("work_mode", sa.String(length=16), nullable=True),
        sa.Column("employment_type", sa.String(length=32), nullable=False),
        sa.Column("salary_min", sa.Integer(), nullable=True),
        sa.Column("salary_max", sa.Integer(), nullable=True),
        sa.Column("salary_months", sa.Integer(), nullable=True),
        sa.Column("salary_unknown", sa.Boolean(), nullable=False),
        sa.Column("salary_raw", sa.String(length=120), nullable=True),
        sa.Column("salary_confidence", sa.Float(), nullable=True),
        sa.Column("experience_min", sa.Integer(), nullable=True),
        sa.Column("experience_max", sa.Integer(), nullable=True),
        sa.Column("experience_type", sa.String(length=16), nullable=False),
        sa.Column("education_level", sa.String(length=32), nullable=True),
        sa.Column("education_requirement_type", sa.String(length=16), nullable=False),
        sa.Column("outsourcing_signals", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("dedupe_status", sa.String(length=16), nullable=False),
        sa.Column(
            "dedupe_candidate_canonical_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("normalizer_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'unknown')", name="ck_job_postings_status"
        ),
        sa.CheckConstraint(
            "dedupe_status IN ('unique', 'merged', 'pending_review')",
            name="ck_job_postings_dedupe_status",
        ),
        sa.CheckConstraint(
            "city_kind IN ('city', 'remote', 'nationwide', 'other', 'unknown')",
            name="ck_job_postings_city_kind",
        ),
        sa.CheckConstraint(
            "employment_type IN ('full_time', 'other', 'unknown')",
            name="ck_job_postings_employment_type",
        ),
        sa.CheckConstraint(
            "experience_type IN ('range', 'fresh_grad', 'unrestricted', 'unknown')",
            name="ck_job_postings_experience_type",
        ),
        sa.CheckConstraint(
            "education_requirement_type IN ('required', 'preferred', 'unknown')",
            name="ck_job_postings_education_req_type",
        ),
        sa.CheckConstraint(
            "NOT (salary_unknown AND (salary_min IS NOT NULL OR salary_max IS NOT NULL))",
            name="ck_job_postings_unknown_salary_no_values",
        ),
        sa.ForeignKeyConstraint(["job_source_id"], ["job_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["canonical_job_id"], ["canonical_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["job_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["imported_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "uq_job_postings_source_job",
        "job_postings",
        ["job_source_id", "source_job_id"],
        unique=True,
        postgresql_where=sa.text("source_job_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_postings_source_url",
        "job_postings",
        ["job_source_id", "source_url"],
        unique=True,
        postgresql_where=sa.text("source_job_id IS NULL AND source_url IS NOT NULL"),
    )
    op.create_index("ix_job_postings_canonical", "job_postings", ["canonical_job_id"])
    op.create_index(
        "ix_job_postings_company_title_city",
        "job_postings",
        ["company_id", "title_normalized", "city_code"],
    )


def downgrade() -> None:
    op.drop_table("job_postings")
    op.drop_table("canonical_jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_job_snapshots_append_only ON job_snapshots;")
    op.execute("DROP FUNCTION IF EXISTS job_snapshots_block_mutation();")
    op.drop_table("job_snapshots")
    op.drop_table("source_runs")
    op.drop_table("job_sources")
    op.drop_table("learned_preferences")
    op.drop_table("company_preferences")
    op.drop_table("companies")
    op.execute("DROP TRIGGER IF EXISTS trg_search_plans_active_limit ON search_plans;")
    op.execute("DROP FUNCTION IF EXISTS search_plans_enforce_active_limit();")
    op.drop_table("search_plans")
