"""认证相关请求/响应模型。"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class InviteValidateRequest(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class InviteValidateResponse(BaseModel):
    # 只回答有效与否；不暴露剩余次数/期限（docs/04 第 2 节）
    valid: bool


class MagicLinkRequest(BaseModel):
    email: EmailStr
    invite_code: str = Field(min_length=1, max_length=128)
    age_attested: bool


class MagicLinkVerifyRequest(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    status: str
    age_attested_at: datetime | None
    terms_version: str | None
    privacy_version: str | None


class SessionInfoOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


class SessionOut(BaseModel):
    user: UserOut
    session_id: uuid.UUID
    expires_at: datetime
