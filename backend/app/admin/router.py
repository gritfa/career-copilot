"""管理员路由：邀请码管理 + 只读监控视图（docs/04 第 10 节，ADR-001 减配版）。

- 全部端点要求 admin 角色（require_admin）；普通用户 403、匿名 401。
- 创建时明文邀请码只返回一次；库里只存哈希。
- 管理员操作全部写审计；用户列表访问也写审计。
- 只读视图红线（docs/08 第 7、8 节）：绝不返回简历正文、联系方式明文
  （邮箱脱敏显示）、token、密钥；管理操作（封禁/额度/重跑）走 CLI，不做写 UI。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_admin
from app.core.errors import AppError
from app.core.redis import get_redis
from app.core.security import generate_invite_code, hash_invite_code, hash_ip
from app.db.models import (
    AccountPurgeRun,
    AgentRun,
    DataExport,
    Invite,
    JobSource,
    Recommendation,
    ResumeExport,
    ResumeParse,
    SourceRun,
    UsageLedger,
    User,
    UserFeedback,
)
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


# ---------------- 阶段 8：只读监控视图（ADR-001：只读 + CLI，无写操作 UI） ----------------


def mask_email(email: str) -> str:
    """邮箱脱敏：只保留首字符与域名（管理视图不得出现联系方式明文）。"""
    local, _, domain = email.partition("@")
    head = local[0] if local else "*"
    return f"{head}***@{domain}"


class AdminOverviewOut(BaseModel):
    users: dict[str, int]
    recommendations: dict[str, int]
    feedback: dict[str, int]
    tasks: dict[str, dict[str, int]]
    queue: dict[str, Any]
    usage_30d: dict[str, Any]


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email_masked: str
    role: str
    status: str
    quota_overrides: dict[str, Any] | None
    created_at: datetime
    last_active_at: datetime | None
    deletion_requested_at: datetime | None
    purge_after: datetime | None
    suspended_at: datetime | None


class AdminUserListOut(BaseModel):
    items: list[AdminUserOut]
    total: int
    page: int
    page_size: int


class AdminUsageRowOut(BaseModel):
    provider: str
    model: str
    operation_type: str
    calls: int
    tokens_in: int
    tokens_out: int
    amount_estimated: float


class AdminPurgeRunOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    status: str
    attempts: int
    manifest: dict[str, Any]
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None


async def _status_counts(db: AsyncSession, model, column) -> dict[str, int]:
    rows = (await db.execute(select(column, func.count()).group_by(column))).all()
    return {str(value): int(count) for value, count in rows}


@router.get("/overview", response_model=AdminOverviewOut)
async def admin_overview(
    request: Request,
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminOverviewOut:
    """只读运行概览：用户数 / 推荐量 / 任务健康 / 队列 / 费用汇总（无正文，无 PII）。"""
    now = datetime.now(UTC)
    today = now.date()

    users = await _status_counts(db, User, User.status)
    users["total"] = sum(users.values())
    users["admins"] = int(
        (
            await db.execute(select(func.count()).select_from(User).where(User.role == "admin"))
        ).scalar_one()
    )

    rec_total = int(
        (await db.execute(select(func.count()).select_from(Recommendation))).scalar_one()
    )
    rec_today = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Recommendation)
                .where(Recommendation.recommended_on == today)
            )
        ).scalar_one()
    )

    feedback = await _status_counts(db, UserFeedback, UserFeedback.sentiment)

    tasks = {
        "resume_parses": await _status_counts(db, ResumeParse, ResumeParse.status),
        "agent_runs": await _status_counts(db, AgentRun, AgentRun.status),
        "resume_exports": await _status_counts(db, ResumeExport, ResumeExport.status),
        "data_exports": await _status_counts(db, DataExport, DataExport.status),
        "account_purge_runs": await _status_counts(db, AccountPurgeRun, AccountPurgeRun.status),
    }

    # 队列健康：Redis 可达性 + celery 默认队列积压深度（探测失败如实上报，不伪装健康）
    queue: dict[str, Any] = {"redis_ok": False, "celery_queue_depth": None}
    try:
        redis = get_redis(request.app)
        await redis.ping()
        queue["redis_ok"] = True
        queue["celery_queue_depth"] = int(await redis.llen("celery"))
    except Exception:
        pass

    since = now - timedelta(days=30)
    usage_rows = (
        await db.execute(
            select(
                UsageLedger.provider,
                func.count(),
                func.coalesce(func.sum(UsageLedger.tokens_in), 0),
                func.coalesce(func.sum(UsageLedger.tokens_out), 0),
                func.coalesce(func.sum(UsageLedger.amount_estimated), 0),
            )
            .where(UsageLedger.occurred_at >= since)
            .group_by(UsageLedger.provider)
        )
    ).all()
    usage_30d = {
        "by_provider": {
            provider: {
                "calls": int(calls),
                "tokens_in": int(tin),
                "tokens_out": int(tout),
                "amount_estimated": float(amount),
            }
            for provider, calls, tin, tout, amount in usage_rows
        },
        "total_amount_estimated": float(sum(row[4] for row in usage_rows)),
    }

    return AdminOverviewOut(
        users=users,
        recommendations={"total": rec_total, "today": rec_today},
        feedback=feedback,
        tasks=tasks,
        queue=queue,
        usage_30d=usage_30d,
    )


@router.get("/users", response_model=AdminUserListOut)
async def admin_list_users(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user_status: str | None = Query(default=None, alias="status"),
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListOut:
    """用户列表（只读）：邮箱脱敏、无简历正文、无联系方式明文；访问写审计。"""
    stmt = select(User)
    count_stmt = select(func.count()).select_from(User)
    if user_status is not None:
        stmt = stmt.where(User.status == user_status)
        count_stmt = count_stmt.where(User.status == user_status)
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        (
            await db.execute(
                stmt.order_by(User.created_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    await record_audit(
        db,
        actor_type="admin",
        actor_id=ctx.user.id,
        action="admin_users_viewed",
        resource_type="user_list",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return AdminUserListOut(
        items=[
            AdminUserOut(
                id=u.id,
                email_masked=mask_email(u.email_normalized),
                role=u.role,
                status=u.status,
                quota_overrides=u.quota_overrides_json,
                created_at=u.created_at,
                last_active_at=u.last_active_at,
                deletion_requested_at=u.deletion_requested_at,
                purge_after=u.purge_after,
                suspended_at=u.suspended_at,
            )
            for u in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/usage", response_model=list[AdminUsageRowOut])
async def admin_usage(
    days: int = Query(default=30, ge=1, le=365),
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUsageRowOut]:
    """费用账本汇总（只读）：按 provider/model/操作类型聚合，只有计数与金额。"""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                UsageLedger.provider,
                UsageLedger.model,
                UsageLedger.operation_type,
                func.count(),
                func.coalesce(func.sum(UsageLedger.tokens_in), 0),
                func.coalesce(func.sum(UsageLedger.tokens_out), 0),
                func.coalesce(func.sum(UsageLedger.amount_estimated), 0),
            )
            .where(UsageLedger.occurred_at >= since)
            .group_by(UsageLedger.provider, UsageLedger.model, UsageLedger.operation_type)
            .order_by(UsageLedger.provider, UsageLedger.model)
        )
    ).all()
    return [
        AdminUsageRowOut(
            provider=provider,
            model=model,
            operation_type=op,
            calls=int(calls),
            tokens_in=int(tin),
            tokens_out=int(tout),
            amount_estimated=float(amount),
        )
        for provider, model, op, calls, tin, tout, amount in rows
    ]


@router.get("/purge-runs", response_model=list[AdminPurgeRunOut])
async def admin_purge_runs(
    ctx: AuthContext = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminPurgeRunOut]:
    """注销硬删任务（只读）：可验证清单只有计数，无任何正文/联系方式。"""
    rows = (
        (
            await db.execute(
                select(AccountPurgeRun).order_by(AccountPurgeRun.started_at.desc()).limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        AdminPurgeRunOut(
            id=r.id,
            user_id=r.user_id,
            status=r.status,
            attempts=r.attempts,
            manifest=r.manifest_json,
            error_code=r.error_code,
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in rows
    ]
