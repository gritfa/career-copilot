"""隐私路由：账号注销状态机 + 用户数据导出（docs/04 第 3 节、docs/08 第 9 节）。

- POST /privacy/account-deletion：进入 deletion_pending，7 天恢复期，purge_after = now + 7d。
  期间停止一切新的写操作/模型处理（由 require_active_user 与 Guard 强制）。
- DELETE /privacy/account-deletion：恢复期内撤销，恢复 active。
- 物理清理由 privacy.purge_due_accounts beat 任务执行（阶段 8）。
- POST /privacy/data-exports：异步生成本人全量数据 ZIP；要求"重新认证"
  （当前会话为近期登录，超窗返回 403 REAUTH_REQUIRED）；短时 HMAC 签名下载。
  注销恢复期内仍允许导出（数据主体权利，不属于"新的处理"）。
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta
from datetime import time as dtime

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.models import DataExport
from app.db.session import get_db
from app.integrations.storage import get_storage
from app.privacy.schemas import DataExportOut
from app.privacy.tasks import generate_data_export_task
from app.tasks.celery_app import dispatch_task

router = APIRouter(prefix="/privacy", tags=["privacy"])

_NOT_FOUND = AppError(code="NOT_FOUND", message="资源不存在", status_code=404)


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


# ---------------- 用户数据导出（阶段 8） ----------------


def _export_download_sig(export_id: uuid.UUID, expires_epoch: int) -> str:
    secret = get_settings().secret_pepper.encode("utf-8")
    message = f"data-export:{export_id}:{expires_epoch}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _export_out(export: DataExport) -> DataExportOut:
    download_url = None
    now = datetime.now(UTC)
    if (
        export.status == "succeeded"
        and export.expires_at is not None
        and export.expires_at > now
        and export.file_purged_at is None
    ):
        expires_epoch = int(export.expires_at.timestamp())
        sig = _export_download_sig(export.id, expires_epoch)
        download_url = (
            f"/privacy/data-exports/{export.id}/download?expires={expires_epoch}&sig={sig}"
        )
    return DataExportOut(
        id=export.id,
        status=export.status,
        error_code=export.error_code,
        size_bytes=export.size_bytes,
        file_sha256=export.file_sha256,
        download_url=download_url,
        expires_at=export.expires_at,
        created_at=export.created_at,
        completed_at=export.completed_at,
    )


async def _purge_data_export_file(db: AsyncSession, export: DataExport) -> None:
    if export.storage_key and export.file_purged_at is None:
        storage = get_storage()
        if storage.exists(export.storage_key):
            storage.delete(export.storage_key)
        export.file_purged_at = datetime.now(UTC)
        await db.commit()


@router.post(
    "/data-exports", response_model=DataExportOut, status_code=status.HTTP_202_ACCEPTED
)
async def create_data_export(
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> DataExportOut:
    """发起本人全量数据导出（异步 ZIP）。

    - 重新认证：当前会话必须是近期登录（超窗 403 REAUTH_REQUIRED，请重新登录后再试）。
    - 已有进行中的导出幂等复用；每日次数受限防滥用。
    - 注销恢复期（deletion_pending）内允许（数据主体权利）。
    """
    settings = get_settings()
    now = datetime.now(UTC)
    session_age = (now - ctx.session.created_at).total_seconds()
    if session_age > settings.data_export_reauth_window_seconds:
        raise AppError(
            code="REAUTH_REQUIRED",
            message="数据导出需要重新认证：请重新登录后再发起导出",
            status_code=403,
        )

    # 进行中的导出：幂等复用
    active = (
        await db.execute(
            select(DataExport)
            .where(
                DataExport.user_id == ctx.user.id,
                DataExport.status.in_(("queued", "running")),
            )
            .order_by(DataExport.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        return _export_out(active)

    from app.core.quotas import effective_limit

    limit = effective_limit(ctx.user.quota_overrides_json, "data_export_daily")
    day_start = datetime.combine(now.date(), dtime.min, tzinfo=UTC)
    used = (
        await db.execute(
            select(func.count())
            .select_from(DataExport)
            .where(DataExport.user_id == ctx.user.id, DataExport.created_at >= day_start)
        )
    ).scalar_one()
    if used >= limit:
        raise AppError(
            code="RATE_LIMITED",
            message="今日数据导出次数已用完，明天再试",
            status_code=429,
            details={"limit": limit, "used": int(used)},
        )

    export = DataExport(user_id=ctx.user.id, status="queued")
    db.add(export)
    await db.flush()
    await record_audit(
        db,
        actor_type="admin" if ctx.user.role == "admin" else "user",
        actor_id=ctx.user.id,
        action="data_export_requested",
        resource_type="data_export",
        resource_id=str(export.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    await db.refresh(export)
    dispatch_task(generate_data_export_task, str(export.id))
    await db.refresh(export)
    return _export_out(export)


@router.get("/data-exports", response_model=list[DataExportOut])
async def list_data_exports(
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[DataExportOut]:
    """本人最近的导出记录（新→旧，最多 10 条）。"""
    rows = (
        (
            await db.execute(
                select(DataExport)
                .where(DataExport.user_id == ctx.user.id)
                .order_by(DataExport.created_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    return [_export_out(e) for e in rows]


@router.get("/data-exports/{export_id}", response_model=DataExportOut)
async def get_data_export(
    export_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> DataExportOut:
    """导出状态与限时下载链接（过期链接不再返回，文件顺带清理）。"""
    export = (
        await db.execute(select(DataExport).where(DataExport.id == export_id))
    ).scalar_one_or_none()
    if export is None or export.user_id != ctx.user.id:
        raise _NOT_FOUND
    if export.expires_at is not None and export.expires_at <= datetime.now(UTC):
        await _purge_data_export_file(db, export)
    return _export_out(export)


@router.get("/data-exports/{export_id}/download")
async def download_data_export(
    export_id: uuid.UUID,
    expires: int = Query(),
    sig: str = Query(min_length=64, max_length=64),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """限时签名下载：越权、签名不符、过期或文件已清理一律拒绝。"""
    export = (
        await db.execute(select(DataExport).where(DataExport.id == export_id))
    ).scalar_one_or_none()
    if export is None or export.user_id != ctx.user.id or export.status != "succeeded":
        raise _NOT_FOUND
    if not hmac.compare_digest(sig, _export_download_sig(export.id, expires)):
        raise _NOT_FOUND  # 签名不符不泄露更多信息
    now = datetime.now(UTC)
    if (
        datetime.fromtimestamp(expires, tz=UTC) <= now
        or export.expires_at is None
        or export.expires_at <= now
        or export.file_purged_at is not None
    ):
        await _purge_data_export_file(db, export)
        raise AppError(
            code="EXPORT_EXPIRED",
            message="下载链接已过期，文件已清理；请重新发起导出",
            status_code=410,
        )
    storage = get_storage()
    if not export.storage_key or not storage.exists(export.storage_key):
        raise _NOT_FOUND
    data = storage.open(export.storage_key)
    # 文件名只含导出短 ID（无姓名/邮箱等 PII）
    filename = f"careercopilot_export_{export.id.hex[:8]}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
