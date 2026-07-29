"""业务表（docs/03）：阶段 2 账号/授权/审计 + 阶段 3 简历/事实库。

安全约束：
- 邀请码 / magic link token / 会话 token 只保存哈希。
- audit_events append-only（迁移里加触发器阻止 UPDATE/DELETE），
  且严禁写入邮箱明文、token、正文。
- 简历 storage_key 不含邮箱/原文件名；未确认候选绝不进入 profile_facts。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# ---- 受控取值（用 CheckConstraint 而非 PG enum，便于演进） ----

USER_ROLES = ("user", "admin")
USER_STATUSES = ("active", "deletion_pending")
CONSENT_PROVIDERS = ("deepseek", "qwen", "analytics", "support")
CONSENT_SCOPES = ("full_resume", "deidentified", "profile_fields")
ACTOR_TYPES = ("user", "admin", "system", "anonymous")
AUDIT_RESULTS = ("success", "denied", "failure")

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
