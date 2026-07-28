"""隐私路由：账号注销状态机（docs/04 第 3 节、docs/08 第 9 节）。

- POST /privacy/account-deletion：进入 deletion_pending，7 天恢复期，purge_after = now + 7d。
  期间停止一切新的写操作/模型处理（由 require_active_user 与 Guard 强制）。
- DELETE /privacy/account-deletion：恢复期内撤销，恢复 active。
物理清理任务在后续阶段实现（purge_after 到期扫描）。
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.session import get_db

router = APIRouter(prefix="/privacy", tags=["privacy"])


@router.post("/account-deletion", status_code=status.HTTP_202_ACCEPTED)
async def request_account_deletion(
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """请求注销：进入 7 天恢复期，立即停止新的处理。"""
    user = ctx.user
    if user.status == "deletion_pending":
        raise AppError(
            code="DELETION_ALREADY_PENDING",
            message="账号已处于注销恢复期",
            status_code=409,
        )

    now = datetime.now(UTC)
    grace_days = get_settings().account_deletion_grace_days
    user.status = "deletion_pending"
    user.deletion_requested_at = now
    user.purge_after = now + timedelta(days=grace_days)
    await record_audit(
        db,
        actor_type="admin" if user.role == "admin" else "user",
        actor_id=user.id,
        action="account_deletion_requested",
        resource_type="user",
        resource_id=str(user.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return {
        "status": "deletion_pending",
        "deletion_requested_at": user.deletion_requested_at.isoformat(),
        "purge_after": user.purge_after.isoformat(),
        "message": f"账号将在 {grace_days} 天后清理；期间可随时撤销注销",
    }


@router.delete("/account-deletion")
async def cancel_account_deletion(
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """恢复期内撤销注销，恢复 active（这是 deletion_pending 状态下允许的操作）。"""
    user = ctx.user
    if user.status != "deletion_pending":
        raise AppError(
            code="NO_DELETION_PENDING",
            message="账号当前没有待处理的注销请求",
            status_code=409,
        )

    user.status = "active"
    user.deletion_requested_at = None
    user.purge_after = None
    await record_audit(
        db,
        actor_type="admin" if user.role == "admin" else "user",
        actor_id=user.id,
        action="account_deletion_cancelled",
        resource_type="user",
        resource_id=str(user.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return {"status": "active", "message": "注销请求已撤销，账号已恢复"}
