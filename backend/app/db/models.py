"""业务表（docs/03）：阶段 2 账号/授权/审计 + 阶段 3 简历/事实库。

安全约束：
- 邀请码 / magic link token / 会话 token 只保存哈希。
- audit_events append-only（迁移里加触发器阻止 UPDATE/DELETE），
  且严禁写入邮箱明文、token、正文。
- 简历 storage_key 不含邮箱/原文件名；未确认候选绝不进入 profile_facts。
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ---- 受控取值（用 CheckConstraint 而非 PG enum，便于演进） ----

USER_ROLES = ("user", "admin")
USER_STATUSES = ("active", "deletion_pending")
CONSENT_PROVIDERS = ("deepseek", "qwen", "analytics", "support")
CONSENT_SCOPES = ("full_resume", "deidentified", "profile_fields")
ACTOR_TYPES = ("user", "admin", "system", "anonymous")
AUDIT_RESULTS = ("success", "denied", "failure")

SEARCH_PLAN_STATUSES = ("active", "paused", "archived")
COMPANY_PREFERENCES = ("follow", "priority", "block")
JOB_SOURCE_TYPES = ("company_site", "platform", "user_import")
JOB_SOURCE_STATUSES = ("enabled", "paused", "circuit_open", "disabled")
SOURCE_RUN_STATUSES = ("running", "success", "partial_failure", "failed")
JOB_POSTING_STATUSES = ("active", "inactive", "unknown")
DEDUPE_STATUSES = ("unique", "merged", "pending_review")
CANONICAL_REVIEW_STATUSES = ("auto", "pending_review", "confirmed")

RECOMMENDATION_GRADES = ("high", "potential", "low")
HARD_FILTER_STATUSES = ("passed", "uncertain")
MATCH_COMPONENT_NAMES = (
    "core_skills",
    "experience",
    "project_evidence",
    "role_semantic",
    "industry",
    "preference",
)
GAP_LEVELS = ("none", "minor", "major", "unknown")
FEEDBACK_SENTIMENTS = ("interested", "not_interested")
# “不感兴趣”结构化原因（docs/05 第 11 节，9 类）
FEEDBACK_REASON_CODES = (
    "location",
    "salary",
    "company",
    "tech_direction",
    "job_content",
    "experience_education",
    "outsourcing",
    "risk_concern",
    "seen_duplicate",
)
# 向量维度：确定性合成 Adapter 与阿里云 text-embedding-v3(dimensions=768) 统一 768 维；
# 不同 model_id/preprocess_version 的向量靠唯一键隔离，禁止混用（docs/07 第 3 节）。
EMBEDDING_DIM = 768

RESUME_STATUSES = ("uploaded", "parsing", "parsed", "parse_failed", "deleting", "deleted")
MALWARE_SCAN_STATUSES = ("pending", "clean", "infected", "skipped_not_configured")
TEXT_EXTRACT_STATUSES = ("pending", "succeeded", "no_text_layer", "failed")
PARSE_STATUSES = ("queued", "running", "succeeded", "failed")
CANDIDATE_STATUSES = ("pending", "accepted", "edited", "rejected")
FACT_STATUSES = ("active", "superseded", "revoked")
PROVENANCE_TYPES = ("resume", "user_answer", "import")


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    age_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(64))
    privacy_version: Mapped[str | None] = mapped_column(String(64))
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purge_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active', 'deletion_pending')", name="ck_users_status"),
    )


class Invite(Base):
    __tablename__ = "invites"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint("max_uses > 0", name="ck_invites_max_uses_positive"),
        CheckConstraint("used_count >= 0", name="ck_invites_used_count_nonnegative"),
        CheckConstraint("used_count <= max_uses", name="ck_invites_not_overused"),
    )


class AuthToken(Base):
    """magic link 一次性 token；只存哈希，短时、单次。"""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # 创建用户所需的最小信息（DB 属受控存储；日志/审计不得出现该字段）
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invites.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class Session(Base):
    """可撤销会话；token 只存哈希。"""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(256))

    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)


class Consent(Base):
    """模型服务商/分析/支持授权：单 provider + scope，撤回立即生效。"""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(256))
    data_categories: Mapped[str | None] = mapped_column(String(512))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "provider IN ('deepseek', 'qwen', 'analytics', 'support')",
            name="ck_consents_provider",
        ),
        CheckConstraint(
            "scope IN ('full_resume', 'deidentified', 'profile_fields')",
            name="ck_consents_scope",
        ),
        # 同一用户对同一 provider+scope 只允许一条“未撤回”授权
        Index(
            "uq_consents_active_provider_scope",
            "user_id",
            "provider",
            "scope",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


class AuditEvent(Base):
    """append-only 安全审计；严禁正文、密钥、邮箱明文、token。"""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    reason_code: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'admin', 'system', 'anonymous')",
            name="ck_audit_events_actor_type",
        ),
        CheckConstraint(
            "result IN ('success', 'denied', 'failure')",
            name="ck_audit_events_result",
        ),
        Index("ix_audit_events_actor_id", "actor_id"),
        Index("ix_audit_events_action", "action"),
    )


# ---------------- 阶段 3：简历与事实库（docs/03 第 4 节） ----------------


class Resume(Base):
    """原始简历文件元数据；原始快照不可变，删除走异步清理并保留墓碑行。"""

    __tablename__ = "resumes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # 不含邮箱/原文件名，只由 UUID 组成（docs/08 第 5 节）
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # 原文件名仅存 DB 供 UI 展示；严禁进入日志/审计/存储 key
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploaded")
    malware_scan_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    text_extract_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'parsing', 'parsed', 'parse_failed', "
            "'deleting', 'deleted')",
            name="ck_resumes_status",
        ),
        CheckConstraint(
            "malware_scan_status IN ('pending', 'clean', 'infected', "
            "'skipped_not_configured')",
            name="ck_resumes_malware_scan_status",
        ),
        CheckConstraint(
            "text_extract_status IN ('pending', 'succeeded', 'no_text_layer', 'failed')",
            name="ck_resumes_text_extract_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_resumes_size_positive"),
        Index("ix_resumes_user_id", "user_id"),
        Index("ix_resumes_user_sha256", "user_id", "sha256"),
    )


class ResumeParse(Base):
    """一次解析运行；幂等 key = sha256 + parser 版本（按用户资源隔离）。"""

    __tablename__ = "resume_parses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    parser_name: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(32))
    model_version: Mapped[str | None] = mapped_column(String(64))
    extracted_text_storage_key: Mapped[str | None] = mapped_column(String(255))
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(String(64))
    # 受保护属性（性别/年龄/照片/婚育/民族/籍贯）被丢弃的计数——只记数，不记内容
    protected_discarded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_resume_parses_status",
        ),
        Index("ix_resume_parses_resume_id", "resume_id"),
    )


class FactCandidate(Base):
    """候选事实：只有用户确认（accept/edit）后才会派生 profile_fact。"""

    __tablename__ = "fact_candidates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_parse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resume_parses.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'edited', 'rejected')",
            name="ck_fact_candidates_status",
        ),
        CheckConstraint(
            "source_span_end >= source_span_start",
            name="ck_fact_candidates_span_order",
        ),
        Index("ix_fact_candidates_parse_id", "resume_parse_id"),
    )


class ProfileFact(Base):
    """已确认事实（版本化）：PATCH 生成新版本 supersede 旧的；DELETE 废止。

    禁止没有来源（provenance）和确认记录的事实进入定制简历。
    """

    __tablename__ = "profile_facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    provenance_type: Mapped[str] = mapped_column(String(32), nullable=False)
    confirmed_by_user_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profile_facts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded', 'revoked')",
            name="ck_profile_facts_status",
        ),
        CheckConstraint(
            "provenance_type IN ('resume', 'user_answer', 'import')",
            name="ck_profile_facts_provenance",
        ),
        Index("ix_profile_facts_user_type", "user_id", "fact_type"),
    )


class FactEvidence(Base):
    """事实证据定位：简历删除后 resume_id 置空，保留 hash 与最小必要片段。"""

    __tablename__ = "fact_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    profile_fact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profile_facts.id", ondelete="CASCADE"),
        nullable=False,
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="SET NULL")
    )
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    display_excerpt: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("ix_fact_evidence_fact_id", "profile_fact_id"),
        Index("ix_fact_evidence_resume_id", "resume_id"),
    )


# ---------------- 阶段 4：求职方案 / 岗位来源与标准化（docs/03 第 5、6 节） ----------------


class SearchPlan(Base):
    """求职方案：每用户最多 3 个 active（DB 触发器 + 事务级 advisory lock 双保险）。"""

    __tablename__ = "search_plans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role_family: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    city_codes: Mapped[list[str]] = mapped_column(ARRAY(String(12)), nullable=False)
    work_modes: Mapped[list[str]] = mapped_column(ARRAY(String(16)), nullable=False)
    # 薪资：第一版只接受 CNY 月薪（docs/04 第 5 节）
    salary_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    minimum_monthly_salary: Mapped[int | None] = mapped_column(Integer)
    target_monthly_salary: Mapped[int | None] = mapped_column(Integer)
    salary_months_preference: Mapped[int | None] = mapped_column(Integer)
    minimum_match_score: Mapped[int] = mapped_column(Integer, nullable=False, default=65)
    allow_outsourcing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # resume_versions 属阶段 7；先留 UUID 引用，不建 FK
    base_resume_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')", name="ck_search_plans_status"
        ),
        CheckConstraint("salary_currency = 'CNY'", name="ck_search_plans_currency_cny"),
        CheckConstraint(
            "minimum_monthly_salary IS NULL OR minimum_monthly_salary > 0",
            name="ck_search_plans_min_salary_positive",
        ),
        CheckConstraint(
            "target_monthly_salary IS NULL OR target_monthly_salary > 0",
            name="ck_search_plans_target_salary_positive",
        ),
        CheckConstraint(
            "minimum_monthly_salary IS NULL OR target_monthly_salary IS NULL "
            "OR minimum_monthly_salary <= target_monthly_salary",
            name="ck_search_plans_min_le_target",
        ),
        CheckConstraint(
            "salary_months_preference IS NULL "
            "OR salary_months_preference BETWEEN 12 AND 18",
            name="ck_search_plans_salary_months_range",
        ),
        CheckConstraint(
            "minimum_match_score BETWEEN 0 AND 100",
            name="ck_search_plans_match_score_range",
        ),
        Index("ix_search_plans_user_id", "user_id"),
    )


class Company(Base):
    """公司主数据：规范名 + 别名 + 官方域名（docs/03 第 6 节）。"""

    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    aliases: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    official_domain: Mapped[str | None] = mapped_column(String(255))
    city_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String(12)))
    verification_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified"
    )
    source_refs_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "verification_status IN ('unverified', 'verified')",
            name="ck_companies_verification_status",
        ),
    )


class CompanyPreference(Base):
    """方案级公司偏好：屏蔽优先级高于关注和算法分数（匹配阶段实施）。"""

    __tablename__ = "company_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    search_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    preference: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "preference IN ('follow', 'priority', 'block')",
            name="ck_company_preferences_preference",
        ),
        UniqueConstraint(
            "search_plan_id", "company_id", name="uq_company_preferences_plan_company"
        ),
    )


class LearnedPreference(Base):
    """反馈学习偏好骨架：版本化，可一键重置；不覆盖用户直接配置。"""

    __tablename__ = "learned_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    weights_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    derived_from: Mapped[str] = mapped_column(String(64), nullable=False, default="feedback")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'reset')", name="ck_learned_preferences_status"
        ),
        Index("ix_learned_preferences_user_id", "user_id"),
    )


class JobSource(Base):
    """岗位来源注册表：能力/政策状态如实记录，未验证不得标 ready。"""

    __tablename__ = "job_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enabled")
    access_policy_url: Mapped[str | None] = mapped_column(String(255))
    robots_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_limit_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('company_site', 'platform', 'user_import')",
            name="ck_job_sources_source_type",
        ),
        CheckConstraint(
            "status IN ('enabled', 'paused', 'circuit_open', 'disabled')",
            name="ck_job_sources_status",
        ),
    )


class SourceRun(Base):
    """来源运行统计：任一条目失败时状态不得为 success（规格硬约束）。"""

    __tablename__ = "source_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False
    )
    run_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', 'failed')",
            name="ck_source_runs_status",
        ),
        # 不变量：有条目失败绝不允许绿色 success
        CheckConstraint(
            "NOT (status = 'success' AND items_failed > 0)",
            name="ck_source_runs_success_requires_no_failures",
        ),
        UniqueConstraint("job_source_id", "run_key", name="uq_source_runs_source_run_key"),
    )


class JobSnapshot(Base):
    """不可变原始证据：DB 只存 key + 哈希，原始内容在对象存储。

    迁移里加触发器阻止 UPDATE/DELETE；人工修正只能写派生层。
    """

    __tablename__ = "job_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    http_meta_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        Index("ix_job_snapshots_source_id", "job_source_id"),
        Index("ix_job_snapshots_content_hash", "content_hash"),
    )


class CanonicalJob(Base):
    """去重合并后的岗位主体；企业官网优先主来源，保留全部来源链接。"""

    __tablename__ = "canonical_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    role_family: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    city_code: Mapped[str | None] = mapped_column(String(12))
    # 主展示来源 posting；与 job_postings 循环引用，不建 FK
    primary_posting_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dedupe_version: Mapped[str] = mapped_column(String(16), nullable=False)
    dedupe_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "review_status IN ('auto', 'pending_review', 'confirmed')",
            name="ck_canonical_jobs_review_status",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'unknown')",
            name="ck_canonical_jobs_status",
        ),
        Index("ix_canonical_jobs_company_city", "company_id", "city_code"),
    )


class JobPosting(Base):
    """来源级岗位（标准化派生层，可更新）；原始证据在 job_snapshots。"""

    __tablename__ = "job_postings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_job_id: Mapped[str | None] = mapped_column(String(128))
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("canonical_jobs.id", ondelete="SET NULL")
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL")
    )
    title_raw: Mapped[str] = mapped_column(String(255), nullable=False)
    title_normalized: Mapped[str] = mapped_column(String(255), nullable=False)
    role_family: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    role_family_confidence: Mapped[float | None] = mapped_column(Float)
    description_text: Mapped[str | None] = mapped_column(Text)
    city_code: Mapped[str | None] = mapped_column(String(12))
    city_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    work_mode: Mapped[str | None] = mapped_column(String(16))
    employment_type: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_months: Mapped[int | None] = mapped_column(Integer)
    salary_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    salary_raw: Mapped[str | None] = mapped_column(String(120))
    salary_confidence: Mapped[float | None] = mapped_column(Float)
    experience_min: Mapped[int | None] = mapped_column(Integer)
    experience_max: Mapped[int | None] = mapped_column(Integer)
    experience_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    education_level: Mapped[str | None] = mapped_column(String(32))
    education_requirement_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    outsourcing_signals: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    dedupe_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unique")
    dedupe_candidate_canonical_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True)
    )
    source_url: Mapped[str | None] = mapped_column(String(1000))
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_snapshots.id", ondelete="SET NULL")
    )
    imported_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    normalizer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'inactive', 'unknown')", name="ck_job_postings_status"
        ),
        CheckConstraint(
            "dedupe_status IN ('unique', 'merged', 'pending_review')",
            name="ck_job_postings_dedupe_status",
        ),
        CheckConstraint(
            "city_kind IN ('city', 'remote', 'nationwide', 'other', 'unknown')",
            name="ck_job_postings_city_kind",
        ),
        CheckConstraint(
            "employment_type IN ('full_time', 'other', 'unknown')",
            name="ck_job_postings_employment_type",
        ),
        CheckConstraint(
            "experience_type IN ('range', 'fresh_grad', 'unrestricted', 'unknown')",
            name="ck_job_postings_experience_type",
        ),
        CheckConstraint(
            "education_requirement_type IN ('required', 'preferred', 'unknown')",
            name="ck_job_postings_education_req_type",
        ),
        # 薪资不确定时不得伪造数值（面议 → salary_unknown 且无 min/max）
        CheckConstraint(
            "NOT (salary_unknown AND (salary_min IS NOT NULL OR salary_max IS NOT NULL))",
            name="ck_job_postings_unknown_salary_no_values",
        ),
        Index(
            "uq_job_postings_source_job",
            "job_source_id",
            "source_job_id",
            unique=True,
            postgresql_where=text("source_job_id IS NOT NULL"),
        ),
        Index(
            "uq_job_postings_source_url",
            "job_source_id",
            "source_url",
            unique=True,
            postgresql_where=text("source_job_id IS NULL AND source_url IS NOT NULL"),
        ),
        Index("ix_job_postings_canonical", "canonical_job_id"),
        Index("ix_job_postings_company_title_city", "company_id", "title_normalized", "city_code"),
    )


# ---------------- 阶段 5：匹配 / 推荐 / 反馈 / 向量（docs/03 第 7、8 节） ----------------


class Recommendation(Base):
    """每日推荐结果：同一方案、岗位、日期唯一（docs/03 第 7 节）。

    hard_filter_status 只有 passed / uncertain 两态入库——failed 的岗位不生成推荐。
    hard_conditions_json 保存逐项三态明细与证据（绝无裸布尔）。
    """

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    search_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    score_total: Mapped[int] = mapped_column(Integer, nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    hard_filter_status: Mapped[str] = mapped_column(String(16), nullable=False)
    hard_conditions_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    scoring_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # 版本链（docs/07 第 13 节）：hard_rule_version / embedding model / preprocess
    versions_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_on: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint("score_total BETWEEN 0 AND 100", name="ck_recommendations_score_range"),
        CheckConstraint(
            "grade IN ('high', 'potential', 'low')", name="ck_recommendations_grade"
        ),
        CheckConstraint(
            "hard_filter_status IN ('passed', 'uncertain')",
            name="ck_recommendations_hard_filter_status",
        ),
        CheckConstraint("rank >= 1", name="ck_recommendations_rank_positive"),
        UniqueConstraint(
            "search_plan_id",
            "canonical_job_id",
            "recommended_on",
            name="uq_recommendations_plan_job_day",
        ),
        Index("ix_recommendations_plan_day", "search_plan_id", "recommended_on"),
    )


class MatchComponent(Base):
    """评分分项：每项带证据引用或显式 insufficient_evidence（docs/07 第 5 节）。"""

    __tablename__ = "match_components"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recommendations.id", ondelete="CASCADE"),
        nullable=False,
    )
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_refs_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    gap_level: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    uncertainty: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        CheckConstraint(
            "component IN ('core_skills', 'experience', 'project_evidence', "
            "'role_semantic', 'industry', 'preference')",
            name="ck_match_components_component",
        ),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_match_components_score_range"),
        CheckConstraint("weight BETWEEN 0 AND 100", name="ck_match_components_weight_range"),
        CheckConstraint(
            "gap_level IN ('none', 'minor', 'major', 'unknown')",
            name="ck_match_components_gap_level",
        ),
        UniqueConstraint(
            "recommendation_id", "component", name="uq_match_components_rec_component"
        ),
    )


class UserFeedback(Base):
    """显式反馈：感兴趣/不感兴趣 + 结构化原因；note 不得进日志/审计/第三方分析。"""

    __tablename__ = "user_feedback"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recommendation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recommendations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    optional_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "sentiment IN ('interested', 'not_interested')",
            name="ck_user_feedback_sentiment",
        ),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN ('location', 'salary', 'company', "
            "'tech_direction', 'job_content', 'experience_education', 'outsourcing', "
            "'risk_concern', 'seen_duplicate')",
            name="ck_user_feedback_reason_code",
        ),
        # 不感兴趣必须给结构化原因（docs/05 第 11 节）
        CheckConstraint(
            "NOT (sentiment = 'not_interested' AND reason_code IS NULL)",
            name="ck_user_feedback_not_interested_reason",
        ),
    )


class JobVector(Base):
    """岗位向量：模型 ID + 维度 + 预处理版本入唯一键，禁止新旧模型混用。"""

    __tablename__ = "job_vectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    canonical_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocess_version: Mapped[str] = mapped_column(String(16), nullable=False)
    # 向量文本哈希：文本变化时才重算（文本本身不入库）
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("dim > 0", name="ck_job_vectors_dim_positive"),
        UniqueConstraint(
            "canonical_job_id",
            "model_id",
            "preprocess_version",
            name="uq_job_vectors_job_model_version",
        ),
    )


class ProfileVector(Base):
    """简历侧向量（按求职方案）：已确认事实 + 方案基础信息构造，绝不含联系方式/受保护属性。"""

    __tablename__ = "profile_vectors"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    search_plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("search_plans.id", ondelete="CASCADE"),
        nullable=False,
    )
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    model_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocess_version: Mapped[str] = mapped_column(String(16), nullable=False)
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("dim > 0", name="ck_profile_vectors_dim_positive"),
        UniqueConstraint(
            "search_plan_id",
            "model_id",
            "preprocess_version",
            name="uq_profile_vectors_plan_model_version",
        ),
    )


class UsageLedger(Base):
    """模型/Embedding 费用账本（docs/03 第 8 节）：只记计数与估算金额，无正文。"""

    __tablename__ = "usage_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amount_estimated: Mapped[Any] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint("tokens_in >= 0", name="ck_usage_ledger_tokens_in_nonnegative"),
        CheckConstraint("tokens_out >= 0", name="ck_usage_ledger_tokens_out_nonnegative"),
        Index("ix_usage_ledger_user_occurred", "user_id", "occurred_at"),
        Index("ix_usage_ledger_provider_occurred", "provider", "occurred_at"),
    )
