"""授权路由（docs/04 第 3 节）：告知、授予、撤回。

规则（docs/08 第 3 节）：
- 单 provider + scope 授权，不接受批量勾选。
- 默认不勾选；notice_version 必须匹配当前告知版本。
- 撤回后新调用立即阻断（由 ModelAuthorizationGuard 强制）。
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, ensure_owner, require_active_user, require_user
from app.consents.notices import PROVIDER_NOTICE_VERSIONS, notices_payload
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.models import CONSENT_PROVIDERS, CONSENT_SCOPES, Consent
from app.db.session import get_db

router = APIRouter(tags=["consents"])


class ConsentCreateRequest(BaseModel):
    """单一 provider + scope；请求体为单个对象，批量数组会被 422 拒绝。"""

    provider: str = Field(min_length=1, max_length=32)
    scope: str = Field(min_length=1, max_length=32)
    notice_version: str = Field(min_length=1, max_length=64)


class ConsentOut(BaseModel):
    id: uuid.UUID
    provider: str
    scope: str
    notice_version: str
    granted_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None
    active: bool


def _consent_out(c: Consent) -> ConsentOut:
    now = datetime.now(UTC)
    active = c.revoked_at is None and (c.expires_at is None or c.expires_at > now)
    return ConsentOut(
        id=c.id,
        provider=c.provider,
        scope=c.scope,
        notice_version=c.notice_version,
        granted_at=c.granted_at,
        revoked_at=c.revoked_at,
        expires_at=c.expires_at,
        active=active,
    )


@router.get("/consents/notices")
async def get_notices() -> dict:
    """当前隐私、服务商与分析告知版本（版本化占位文案）。"""
    return notices_payload()


@router.get("/consents", response_model=list[ConsentOut])
async def list_consents(
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConsentOut]:
    """当前用户全部授权记录（含已撤回，供审阅历史）。"""
    rows = (
        (
            await db.execute(
                select(Consent)
                .where(Consent.user_id == ctx.user.id)
                .order_by(Consent.granted_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_consent_out(c) for c in rows]


@router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
async def grant_consent(
    payload: ConsentCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentOut:
    """对单一 provider + scope 主动授权。"""
    if payload.provider not in CONSENT_PROVIDERS:
        raise AppError(
            code="INVALID_PROVIDER", message="不支持的服务商", status_code=400
        )
    if payload.scope not in CONSENT_SCOPES:
        raise AppError(code="INVALID_SCOPE", message="不支持的数据范围", status_code=400)

    current_version = PROVIDER_NOTICE_VERSIONS[payload.provider]
    if payload.notice_version != current_version:
        raise AppError(
            code="NOTICE_VERSION_OUTDATED",
            message="告知内容已更新，请阅读最新版本后重新授权",
            status_code=409,
            details={"current_notice_version": current_version},
        )

    existing = (
        await db.execute(
            select(Consent).where(
                Consent.user_id == ctx.user.id,
                Consent.provider == payload.provider,
                Consent.scope == payload.scope,
                Consent.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            code="CONSENT_ALREADY_GRANTED",
            message="该服务商与范围已授权",
            status_code=409,
            details={"consent_id": str(existing.id)},
        )

    consent = Consent(
        user_id=ctx.user.id,
        provider=payload.provider,
        scope=payload.scope,
        notice_version=payload.notice_version,
    )
    db.add(consent)
    await db.flush()
    await record_audit(
        db,
        actor_type="admin" if ctx.user.role == "admin" else "user",
        actor_id=ctx.user.id,
        action="consent_granted",
        resource_type="consent",
        resource_id=str(consent.id),
        reason_code=f"{payload.provider}:{payload.scope}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _consent_out(consent)


@router.delete("/consents/{consent_id}", response_model=ConsentOut)
async def revoke_consent(
    consent_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ConsentOut:
    """撤回授权：新调用立即阻断（不追溯已合法发生的调用）。

    注销恢复期内也允许撤回授权（隐私权利操作，非“新处理”）。
    """
    consent = (
        await db.execute(select(Consent).where(Consent.id == consent_id))
    ).scalar_one_or_none()
    ensure_owner(consent.user_id if consent else None, ctx.user)
    assert consent is not None
    if consent.revoked_at is None:
        consent.revoked_at = datetime.now(UTC)
    await record_audit(
        db,
        actor_type="admin" if ctx.user.role == "admin" else "user",
        actor_id=ctx.user.id,
        action="consent_revoked",
        resource_type="consent",
        resource_id=str(consent.id),
        reason_code=f"{consent.provider}:{consent.scope}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _consent_out(consent)
