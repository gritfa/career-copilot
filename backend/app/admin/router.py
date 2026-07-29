"""管理员路由骨架：邀请码管理 + 来源能力只读视图（docs/04 第 10 节）。

- 全部端点要求 admin 角色（require_admin）。
- 创建时明文邀请码只返回一次；库里只存哈希。
- 管理员操作全部写审计。
- 来源视图只读：能力状态如实（fixture=not_verified、boss=import_only），
  不显示未验证来源为“采集正常”。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_admin
from app.core.errors import AppError
from app.core.security import generate_invite_code, hash_invite_code, hash_ip
from app.db.models import Invite, JobSource, SourceRun
from app.db.session import get_db
from app.jobs.registry import seed_sources

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


# ---------------- 岗位来源只读视图（阶段 4） ----------------


class SourceRunSummaryOut(BaseModel):
    run_key: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    items_seen: int
    items_new: int
    items_failed: int
    error_code: str | None


class JobSourceAdminOut(BaseModel):
    id: uuid.UUID
    source_key: str
    name: str
    source_type: str
    base_url: str | None
    status: str
    consecutive_failures: int
    rate_limit_config: dict[str, Any]
    capabilities_json: dict[str, Any]
    last_run: SourceRunSummaryOut | None


@router.get("/job-sources", response_model=list[JobSourceAdminOut])
async def list_job_sources(
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[JobSourceAdminOut]:
    """来源能力矩阵只读视图：状态/能力/最近一次运行结果。"""
    await seed_sources(db)
    await db.commit()
    sources = (
        (await db.execute(select(JobSource).order_by(JobSource.source_key))).scalars().all()
    )
    out: list[JobSourceAdminOut] = []
    for source in sources:
        last_run = (
            await db.execute(
                select(SourceRun)
                .where(SourceRun.job_source_id == source.id)
                .order_by(SourceRun.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        out.append(
            JobSourceAdminOut(
                id=source.id,
                source_key=source.source_key,
                name=source.name,
                source_type=source.source_type,
                base_url=source.base_url,
                status=source.status,
                consecutive_failures=source.consecutive_failures,
                rate_limit_config=source.rate_limit_config,
                capabilities_json=source.capabilities_json,
                last_run=(
                    SourceRunSummaryOut(
                        run_key=last_run.run_key,
                        status=last_run.status,
                        started_at=last_run.started_at,
                        completed_at=last_run.completed_at,
                        items_seen=last_run.items_seen,
                        items_new=last_run.items_new,
                        items_failed=last_run.items_failed,
                        error_code=last_run.error_code,
                    )
                    if last_run
                    else None
                ),
            )
        )
    return out
