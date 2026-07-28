"""管理员路由骨架：邀请码管理（docs/04 第 10 节）。

- 全部端点要求 admin 角色（require_admin）。
- 创建时明文邀请码只返回一次；库里只存哈希。
- 管理员操作全部写审计。
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_admin
from app.core.errors import AppError
from app.core.security import generate_invite_code, hash_invite_code, hash_ip
from app.db.models import Invite
from app.db.session import get_db

router = APIRouter(prefix="/admin", tags=["admin"])


class InviteCreateRequest(BaseModel):
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_at: datetime | None = None


class InviteAdminOut(BaseModel):
    id: uuid.UUID
    max_uses: int
    used_count: int
    expires_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime


class InviteCreatedOut(InviteAdminOut):
    # 明文只在创建响应里出现一次
    code: str


def _invite_out(invite: Invite) -> InviteAdminOut:
    return InviteAdminOut(
        id=invite.id,
        max_uses=invite.max_uses,
        used_count=invite.used_count,
        expires_at=invite.expires_at,
        disabled_at=invite.disabled_at,
        created_at=invite.created_at,
    )


@router.get("/invites", response_model=list[InviteAdminOut])
async def list_invites(
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[InviteAdminOut]:
    rows = (
        (await db.execute(select(Invite).order_by(Invite.created_at.desc()))).scalars().all()
    )
    return [_invite_out(i) for i in rows]


@router.post("/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    payload: InviteCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteCreatedOut:
    code = generate_invite_code()
    invite = Invite(
        code_hash=hash_invite_code(code),
        max_uses=payload.max_uses,
        used_count=0,
        expires_at=payload.expires_at,
        created_by=ctx.user.id,
    )
    db.add(invite)
    await db.flush()
    await record_audit(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_invite_created",
        resource_type="invite",
        resource_id=str(invite.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return InviteCreatedOut(code=code, **_invite_out(invite).model_dump())


@router.delete("/invites/{invite_id}", response_model=InviteAdminOut)
async def disable_invite(
    invite_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteAdminOut:
    invite = (
        await db.execute(select(Invite).where(Invite.id == invite_id))
    ).scalar_one_or_none()
    if invite is None:
        raise AppError(code="NOT_FOUND", message="邀请码不存在", status_code=404)
    if invite.disabled_at is None:
        invite.disabled_at = datetime.now(UTC)
    await record_audit(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_invite_disabled",
        resource_type="invite",
        resource_id=str(invite.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _invite_out(invite)
