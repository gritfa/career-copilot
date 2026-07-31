"""单模型标准分析：输出 Schema 与 API 模型（阶段 6，ADR-001 裁剪版）。

红线（docs/07 第 5 节、00-master）：
- 每条结论要么绑定证据（profile_fact_ids ⊆ 输入事实 + job_span 取自岗位原文），
  要么显式 ``uncertainty``；禁止模型常识补足。
- 报告只有最终结构化结果，绝不包含内部对话/思维链。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OUTPUT_SCHEMA_VERSION = "std_analysis_v1"
GRAPH_VERSION = "single_model_v1"  # 多 Agent（LangGraph）按 ADR-001 D2 推迟

AgentRunStatus = Literal["queued", "analyzing", "validating", "completed", "failed"]
AgentRunTrigger = Literal["auto", "manual"]
ClaimStrength = Literal["strong", "moderate", "weak"]
GapSeverity = Literal["minor", "major", "unknown"]

INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class AnalysisClaim(BaseModel):
    """匹配亮点：必须引用输入里的事实 ID 与岗位原文片段。"""

    claim: str
    profile_fact_ids: list[str] = Field(default_factory=list)
    job_span: str = ""
    strength: ClaimStrength = "moderate"
    uncertainty: str | None = None


class AnalysisGap(BaseModel):
    """关键缺口：描述 + 岗位原文证据；无证据时必须显式 uncertainty。"""

    description: str
    job_span: str = ""
    severity: GapSeverity = "unknown"
    uncertainty: str | None = None


class AnalysisRisk(BaseModel):
    """风险信号：只转述输入里的信号与原文证据，不定性公司违法/诈骗。"""

    code: str
    evidence: str = ""
    confidence: float | None = None


class ResumeSuggestion(BaseModel):
    """简历建议：只能基于已确认事实调整表达/突出重点，绝不发明技能或数字。"""

    suggestion: str
    based_on_fact_ids: list[str] = Field(default_factory=list)
    uncertainty: str | None = None


class StandardAnalysisReport(BaseModel):
    """标准分析最终报告（用户唯一可见的分析产物）。"""

    schema_version: str = OUTPUT_SCHEMA_VERSION
    overall_summary: str
    strengths: list[AnalysisClaim] = Field(default_factory=list)
    gaps: list[AnalysisGap] = Field(default_factory=list)
    risks: list[AnalysisRisk] = Field(default_factory=list)
    resume_suggestions: list[ResumeSuggestion] = Field(default_factory=list)


# ---------------- API 模型 ----------------


class AgentRunOut(BaseModel):
    """分析任务对外视图：只有状态与最终报告，无内部对话/思维链。"""

    id: uuid.UUID
    recommendation_id: uuid.UUID
    status: AgentRunStatus
    trigger: AgentRunTrigger
    provider: str
    model_id: str | None
    # 合成/未经真实模型验证的结果必须如实标注（00-master：不虚标能力）
    verified: bool
    graph_version: str
    output_schema_version: str
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
    report: StandardAnalysisReport | None


class AgentRunListOut(BaseModel):
    items: list[AgentRunOut]
