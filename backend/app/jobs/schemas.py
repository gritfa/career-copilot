"""岗位导入 / 来源 API Schema（docs/04 第 6 节）。"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class JobImportRequest(BaseModel):
    """用户导入：URL（只存引用不抓取）或岗位正文（走完整标准化管道）。"""

    url: HttpUrl | None = None
    description_text: str | None = Field(default=None, min_length=20, max_length=50_000)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    company_name: str | None = Field(default=None, max_length=255)
    city: str | None = Field(default=None, max_length=64)
    salary_text: str | None = Field(default=None, max_length=120)
    experience_text: str | None = Field(default=None, max_length=64)
    education_text: str | None = Field(default=None, max_length=64)
    employment_text: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def url_or_text_required(self) -> "JobImportRequest":
        if self.url is None and self.description_text is None:
            raise ValueError("either url or description_text is required")
        if self.description_text is not None and not (self.title and self.title.strip()):
            raise ValueError("title is required when importing description_text")
        return self


class JobImportOut(BaseModel):
    imported_via: Literal["text", "url"]
    posting_id: uuid.UUID
    canonical_job_id: uuid.UUID | None
    dedupe_status: str
    created: bool


class JobSourceLinkOut(BaseModel):
    posting_id: uuid.UUID
    source_key: str
    source_name: str
    source_type: str
    source_url: str | None
    published_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    is_primary: bool


class JobSourcesOut(BaseModel):
    canonical_job_id: uuid.UUID
    items: list[JobSourceLinkOut]


class ValidityCheckOut(BaseModel):
    canonical_job_id: uuid.UUID
    status: Literal["active", "inactive", "unknown"]
    checked_at: datetime
    reason: str | None = None
