"""岗位定制简历：内容 Schema 与 API 模型（阶段 7，ADR-001 裁剪版）。

红线（docs/07 第 10 节、00-master）：
- 简历条目只能来自用户已确认事实：每条 item 必须绑定非空 fact_ids，
  且 fact_ids ⊆ 该用户 active 的 profile_facts（服务端确定性校验强制）。
- 定制 = 筛选 / 排序 / 措辞调整；每处调整（changes）带修改前/后、理由、
  岗位证据与 fact IDs，可追溯。
- 模板只做 1 套标准单栏（ADR-001 D2）；多模板推迟。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CONTENT_SCHEMA_VERSION = "resume_content_v1"
TAILOR_OUTPUT_SCHEMA_VERSION = "resume_tailor_v1"
TEMPLATE_ID = "standard_single_column_v1"  # 唯一模板（多模板按 ADR-001 推迟）

ResumeVersionStatus = Literal["generating", "draft", "confirmed", "failed", "deleted"]
ResumeExportStatus = Literal["queued", "running", "succeeded", "failed"]
ExportFormat = Literal["docx", "pdf"]

SECTION_KINDS = ("summary", "skills", "work_experience", "projects", "education")


class ResumeItem(BaseModel):
    """一条简历内容：文本 + 事实引用（非空，服务端校验其真实存在）。"""

    text: str = Field(min_length=1, max_length=500)
    fact_ids: list[str] = Field(min_length=1)


class ResumeSection(BaseModel):
    """单栏模板中的一个章节。"""

    kind: Literal["summary", "skills", "work_experience", "projects", "education"]
    title: str = Field(min_length=1, max_length=40)
    items: list[ResumeItem] = Field(default_factory=list)


class ResumeContent(BaseModel):
    """定制简历内容（resume_content_v1）。

    meta.target_job_title 来自岗位侧信息（非用户主张，不需要事实引用）；
    联系方式不在 MVP 定制内容内（事实库不向生成流程暴露联系方式）。
    """

    schema_version: str = CONTENT_SCHEMA_VERSION
    target_job_title: str = ""
    target_company: str = ""
    sections: list[ResumeSection] = Field(default_factory=list)


class ResumeChange(BaseModel):
    """一处 AI 调整：修改前/后、理由、岗位证据与事实引用（可追溯）。"""

    change_type: Literal["selected", "reordered", "rephrased", "omitted"]
    section: str
    before: str = ""
    after: str = ""
    reason: str
    fact_ids: list[str] = Field(default_factory=list)
    job_span: str = ""


class TailoredResumeDraft(BaseModel):
    """LLM/合成实现的结构化输出（Gateway Schema 校验目标）。"""

    schema_version: str = TAILOR_OUTPUT_SCHEMA_VERSION
    content: ResumeContent
    changes: list[ResumeChange] = Field(default_factory=list)


# ---------------- API 模型 ----------------


class ResumeVersionOut(BaseModel):
    """简历版本对外视图：内容、调整明细与生成方式（合成 → not_verified）。"""

    id: uuid.UUID
    kind: Literal["plan_base", "job_tailored"]
    recommendation_id: uuid.UUID | None
    canonical_job_id: uuid.UUID | None
    parent_version_id: uuid.UUID | None
    template_id: str
    status: ResumeVersionStatus
    created_by: Literal["user", "agent_draft"]
    provider: str | None
    model_id: str | None
    # 合成/未经真实模型验证的产出必须如实标注（00-master：不虚标能力）
    verified: bool
    error_code: str | None
    content: ResumeContent | None
    changes: list[ResumeChange]
    edited_by_user_at: datetime | None
    confirmed_at: datetime | None
    created_at: datetime


class ResumeVersionListOut(BaseModel):
    items: list[ResumeVersionOut]


class ResumeVersionPatchIn(BaseModel):
    """用户编辑草稿：整体替换内容；服务端重新执行事实引用校验。

    新技能/新经历不能从这里进入——没有对应已确认事实的条目会被 422 拒绝，
    提示走候选事实确认流程。
    """

    content: ResumeContent


class ResumeExportCreateIn(BaseModel):
    format: ExportFormat


class ResumeExportOut(BaseModel):
    """导出任务对外视图；download_url 为限时签名链接（过期失效）。"""

    id: uuid.UUID
    resume_version_id: uuid.UUID
    format: ExportFormat
    status: ResumeExportStatus
    error_code: str | None
    size_bytes: int | None
    file_sha256: str | None
    download_url: str | None
    expires_at: datetime | None
    created_at: datetime
    completed_at: datetime | None
