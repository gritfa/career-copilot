"""事实库 API（docs/04 第 4 节）：GET/POST /profile/facts、PATCH/DELETE。

版本化规则（docs/03 第 4 节）：
- PATCH 生成新版本并 supersede 旧版本（superseded_by_id 链）。
- DELETE 废止（revoked）并做引用检查（resume_versions 未建，骨架返回空引用）。
- 受保护属性类型（性别/年龄/照片/婚育/民族/籍贯）任何入口都不允许写入。
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, ensure_owner, require_active_user, require_user
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.models import ProfileFact
from app.db.session import get_db
from app.resumes.parser import PROTECTED_FACT_TYPES
from app.resumes.router import _fact_out
from app.resumes.schemas import (
    ProfileFactCreateRequest,
    ProfileFactListOut,
    ProfileFactOut,
    ProfileFactPatchRequest,
)

router = APIRouter(tags=["profile-facts"])


def _reject_protected(fact_type: str) -> None:
    if fact_type in PROTECTED_FACT_TYPES:
        raise AppError(
            code="PROTECTED_ATTRIBUTE",
            message="受保护属性（性别/年龄/照片/婚育/民族/籍贯）不能进入事实库",
            status_code=400,
        )


async def _get_owned_fact(
    db: AsyncSession, fact_id: uuid.UUID, ctx: AuthContext
) -> ProfileFact:
    fact = (
        await db.execute(select(ProfileFact).where(ProfileFact.id == fact_id))
    ).scalar_one_or_none()
    ensure_owner(fact.user_id if fact else None, ctx.user)
    assert fact is not None
    return fact


@router.get("/profile/facts", response_model=ProfileFactListOut)
async def list_facts(
    fact_type: str | None = Query(default=None, max_length=64),
    include_history: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileFactListOut:
    """已确认事实库；默认只返回 active 版本。"""
    conditions = [ProfileFact.user_id == ctx.user.id]
    if not include_history:
        conditions.append(ProfileFact.status == "active")
    if fact_type:
        conditions.append(ProfileFact.fact_type == fact_type)
    if cursor:
        try:
            conditions.append(ProfileFact.id > uuid.UUID(cursor))
        except ValueError as exc:
            raise AppError(
                code="INVALID_CURSOR", message="无效的分页游标", status_code=400
            ) from exc

    rows = (
        (
            await db.execute(
                select(ProfileFact).where(*conditions).order_by(ProfileFact.id).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    return ProfileFactListOut(
        items=[_fact_out(f) for f in rows],
        next_cursor=str(rows[-1].id) if has_more and rows else None,
    )


@router.post("/profile/facts", response_model=ProfileFactOut, status_code=status.HTTP_201_CREATED)
async def create_fact(
    payload: ProfileFactCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileFactOut:
    """用户主动新增事实（provenance=user_answer，本人提交即确认）。"""
    _reject_protected(payload.fact_type)
    fact = ProfileFact(
        user_id=ctx.user.id,
        fact_type=payload.fact_type,
        value_json=payload.value_json,
        status="active",
        provenance_type="user_answer",
        confirmed_by_user_at=datetime.now(UTC),
    )
    db.add(fact)
    await db.flush()
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="profile_fact_created",
        resource_type="profile_fact",
        resource_id=str(fact.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _fact_out(fact)


@router.patch("/profile/facts/{fact_id}", response_model=ProfileFactOut)
async def patch_fact(
    fact_id: uuid.UUID,
    payload: ProfileFactPatchRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileFactOut:
    """修改事实：生成新版本，旧版本 superseded（原始 provenance 链保留）。"""
    fact = await _get_owned_fact(db, fact_id, ctx)
    if fact.status != "active":
        raise AppError(
            code="FACT_NOT_ACTIVE",
            message="只能修改当前生效版本",
            status_code=409,
            details={"status": fact.status},
        )

    new_fact = ProfileFact(
        user_id=ctx.user.id,
        fact_type=fact.fact_type,
        value_json=payload.value_json,
        status="active",
        provenance_type="user_answer",
        confirmed_by_user_at=datetime.now(UTC),
    )
    db.add(new_fact)
    await db.flush()
    fact.status = "superseded"
    fact.superseded_by_id = new_fact.id

    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="profile_fact_superseded",
        resource_type="profile_fact",
        resource_id=str(fact.id),
        reason_code=f"new_version:{new_fact.id}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _fact_out(new_fact)


@router.delete("/profile/facts/{fact_id}", response_model=ProfileFactOut)
async def revoke_fact(
    fact_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileFactOut:
    """废止事实（revoked）。

    引用影响检查骨架：定制简历版本（resume_versions，阶段 7）尚未建表，
    当前无下游引用；建表后此处必须阻断/提示受影响版本。
    """
    fact = await _get_owned_fact(db, fact_id, ctx)
    if fact.status == "revoked":
        raise AppError(code="FACT_ALREADY_REVOKED", message="事实已废止", status_code=409)
    if fact.status == "superseded":
        raise AppError(
            code="FACT_NOT_ACTIVE",
            message="该版本已被新版本替代，请操作当前版本",
            status_code=409,
        )

    # 引用预检查骨架：阶段 7 的 resume_versions 建表后在此加入真实检查
    references: list[str] = []
    if references:
        raise AppError(
            code="FACT_IN_USE",
            message="事实被简历版本引用，请先处理引用",
            status_code=409,
            details={"references": references},
        )

    fact.status = "revoked"
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="profile_fact_revoked",
        resource_type="profile_fact",
        resource_id=str(fact.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return _fact_out(fact)
