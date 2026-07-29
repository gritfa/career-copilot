"""简历与事实库 API 模型（docs/04 第 4 节）。"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class UploadSessionOut(BaseModel):
    upload_id: str
    sha256: str
    size_bytes: int
    media_type: str
    page_count: int | None
    malware_scan_status: str
    expires_in_seconds: int


class ResumeCreateRequest(BaseModel):
    upload_id: str = Field(min_length=8, max_length=64)


class ResumeOut(BaseModel):
    id: uuid.UUID
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    status: str
    malware_scan_status: str
    text_extract_status: str
    uploaded_at: datetime
    latest_parse_status: str | None = None


class ResumeCreateOut(BaseModel):
    resume: ResumeOut
    parse_id: uuid.UUID | None
    duplicate: bool = False


class ResumeListOut(BaseModel):
    items: list[ResumeOut]
    next_cursor: str | None = None


class CandidateOut(BaseModel):
    id: uuid.UUID
    fact_type: str
    value_json: dict[str, Any]
    source_span_start: int
    source_span_end: int
    confidence: float
    status: str


class ParseOut(BaseModel):
    id: uuid.UUID
    status: str
    error_code: str | None
    parser_name: str
    parser_version: str
    schema_version: str
    protected_discarded_count: int
    started_at: datetime | None
    completed_at: datetime | None
    candidates: list[CandidateOut]


class FactDecision(BaseModel):
    candidate_id: uuid.UUID
    action: Literal["accept", "edit", "reject"]
    value_json: dict[str, Any] | None = None


class FactsConfirmRequest(BaseModel):
    decisions: list[FactDecision] = Field(min_length=1, max_length=200)


class ProfileFactOut(BaseModel):
    id: uuid.UUID
    fact_type: str
    value_json: dict[str, Any]
    status: str
    provenance_type: str
    confirmed_by_user_at: datetime
    superseded_by_id: uuid.UUID | None
    created_at: datetime


class FactsConfirmOut(BaseModel):
    accepted: int
    edited: int
    rejected: int
    facts: list[ProfileFactOut]


class ProfileFactCreateRequest(BaseModel):
    fact_type: str = Field(min_length=1, max_length=64)
    value_json: dict[str, Any]


class ProfileFactPatchRequest(BaseModel):
    value_json: dict[str, Any]


class ProfileFactListOut(BaseModel):
    items: list[ProfileFactOut]
    next_cursor: str | None = None


class ResumeDeleteOut(BaseModel):
    id: uuid.UUID
    status: str
    dependencies: dict[str, int]
