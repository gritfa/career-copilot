"""求职方案 API Schema（docs/04 第 5 节）。

校验硬规则：
- 薪资第一版只接受 CNY；minimum ≤ target。
- 城市只允许六城行政区码；办公方式 ∈ onsite/hybrid/remote。
- role_family ∈ 六方向。
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.jobs.constants import ROLE_FAMILIES, SUPPORTED_CITY_CODES, WORK_MODES

RoleFamily = Literal[
    "ai_application", "backend_python", "backend_java", "frontend", "data", "qa"
]
WorkMode = Literal["onsite", "hybrid", "remote"]
PlanStatus = Literal["active", "paused", "archived"]
PreferenceKind = Literal["follow", "priority", "block"]

assert set(RoleFamily.__args__) == set(ROLE_FAMILIES)  # 词表与常量保持同步
assert set(WorkMode.__args__) == set(WORK_MODES)


def _validate_city_codes(codes: list[str]) -> list[str]:
    deduped: list[str] = []
    for code in codes:
        if code not in SUPPORTED_CITY_CODES:
            raise ValueError(f"unsupported city code: {code}")
        if code not in deduped:
            deduped.append(code)
    return deduped


class SearchPlanCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    role_family: RoleFamily
    city_codes: list[str] = Field(min_length=1, max_length=6)
    work_modes: list[WorkMode] = Field(default=["onsite"], min_length=1, max_length=3)
    status: Literal["active", "paused"] = "active"
    priority: int = Field(default=0, ge=0, le=100)
    salary_currency: Literal["CNY"] = "CNY"
    minimum_monthly_salary: int | None = Field(default=None, gt=0, le=1_000_000)
    target_monthly_salary: int | None = Field(default=None, gt=0, le=1_000_000)
    salary_months_preference: int | None = Field(default=None, ge=12, le=18)
    minimum_match_score: int = Field(default=65, ge=0, le=100)
    allow_outsourcing: bool = False
    base_resume_version_id: uuid.UUID | None = None

    @field_validator("city_codes")
    @classmethod
    def city_codes_supported(cls, v: list[str]) -> list[str]:
        return _validate_city_codes(v)

    @field_validator("work_modes")
    @classmethod
    def work_modes_dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))

    @model_validator(mode="after")
    def minimum_le_target(self) -> "SearchPlanCreateRequest":
        if (
            self.minimum_monthly_salary is not None
            and self.target_monthly_salary is not None
            and self.minimum_monthly_salary > self.target_monthly_salary
        ):
            raise ValueError("minimum_monthly_salary must be <= target_monthly_salary")
        return self


class SearchPlanUpdateRequest(BaseModel):
    """PATCH：全部字段可选；薪资校验在服务层结合现有值执行。"""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    role_family: RoleFamily | None = None
    city_codes: list[str] | None = Field(default=None, min_length=1, max_length=6)
    work_modes: list[WorkMode] | None = Field(default=None, min_length=1, max_length=3)
    status: PlanStatus | None = None
    priority: int | None = Field(default=None, ge=0, le=100)
    salary_currency: Literal["CNY"] | None = None
    minimum_monthly_salary: int | None = Field(default=None, gt=0, le=1_000_000)
    target_monthly_salary: int | None = Field(default=None, gt=0, le=1_000_000)
    salary_months_preference: int | None = Field(default=None, ge=12, le=18)
    minimum_match_score: int | None = Field(default=None, ge=0, le=100)
    allow_outsourcing: bool | None = None
    base_resume_version_id: uuid.UUID | None = None

    @field_validator("city_codes")
    @classmethod
    def city_codes_supported(cls, v: list[str] | None) -> list[str] | None:
        return _validate_city_codes(v) if v is not None else None


class LinkOutURL(BaseModel):
    source_key: str
    city_code: str
    url: str


class SearchPlanOut(BaseModel):
    id: uuid.UUID
    name: str
    role_family: str
    status: str
    priority: int
    city_codes: list[str]
    work_modes: list[str]
    salary_currency: str
    minimum_monthly_salary: int | None
    target_monthly_salary: int | None
    salary_months_preference: int | None
    minimum_match_score: int
    allow_outsourcing: bool
    base_resume_version_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # BOSS 降级路径：按方案生成的原平台搜索入口（link-out，不采集）
    link_out_urls: list[LinkOutURL] = []


class SearchPlanListOut(BaseModel):
    items: list[SearchPlanOut]


class CompanyPreferenceItem(BaseModel):
    company_name: str = Field(min_length=1, max_length=255)
    preference: PreferenceKind


class CompanyPreferencesPutRequest(BaseModel):
    items: list[CompanyPreferenceItem] = Field(max_length=200)


class CompanyPreferenceOut(BaseModel):
    company_id: uuid.UUID
    company_name: str
    preference: str


class CompanyPreferencesOut(BaseModel):
    items: list[CompanyPreferenceOut]


class ResetLearnedOut(BaseModel):
    status: str
    new_version: int
