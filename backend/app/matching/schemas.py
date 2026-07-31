"""推荐/反馈 API 模型（docs/04 第 6 节）。"""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FeedbackSentiment = Literal["interested", "not_interested"]
FeedbackReasonCode = Literal[
    "location",
    "salary",
    "company",
    "tech_direction",
    "job_content",
    "experience_education",
    "outsourcing",
    "risk_concern",
    "seen_duplicate",
]


class FeedbackRequest(BaseModel):
    sentiment: FeedbackSentiment
    reason_code: FeedbackReasonCode | None = None
    note: str | None = Field(default=None, max_length=500)


class FeedbackOut(BaseModel):
    recommendation_id: uuid.UUID
    sentiment: FeedbackSentiment
    reason_code: FeedbackReasonCode | None
    note: str | None
    created_at: datetime


class HardConditionItemOut(BaseModel):
    condition: str
    status: str
    evidence: str
    detail: dict[str, Any] = Field(default_factory=dict)


class MatchComponentOut(BaseModel):
    component: str
    score: int
    weight: int
    evidence_refs: list[dict[str, Any]]
    gap_level: str
    uncertainty: str | None


class RecommendationSummaryOut(BaseModel):
    id: uuid.UUID
    search_plan_id: uuid.UUID
    canonical_job_id: uuid.UUID
    job_title: str
    company_name: str | None
    city_code: str | None
    # 岗位数据来源（阶段 11 P1）：synthetic_seed 时前端必须展示「合成示例」徽标
    data_origin: str
    score: int
    grade: str
    hard_filter_status: str
    rank: int
    recommended_on: date
    feedback_sentiment: FeedbackSentiment | None


class RecommendationListOut(BaseModel):
    items: list[RecommendationSummaryOut]
    next_cursor: str | None


class SourceLinkOut(BaseModel):
    source_key: str
    source_name: str
    url: str | None


class RiskSignalOut(BaseModel):
    code: str
    keyword: str | None = None
    evidence: str
    confidence: float | None = None


class RecommendationDetailOut(BaseModel):
    id: uuid.UUID
    search_plan_id: uuid.UUID
    canonical_job_id: uuid.UUID
    job_title: str
    company_name: str | None
    city_code: str | None
    # 岗位数据来源（阶段 11 P1）：synthetic_seed 时前端必须展示「合成示例」徽标
    data_origin: str
    description_text: str | None
    salary_raw: str | None
    score: int
    grade: str
    hard_filter_status: str
    hard_conditions: list[HardConditionItemOut]
    components: list[MatchComponentOut]
    risks: list[RiskSignalOut]
    source_links: list[SourceLinkOut]
    scoring_version: str
    versions: dict[str, Any]
    rank: int
    recommended_on: date
    feedback: FeedbackOut | None
