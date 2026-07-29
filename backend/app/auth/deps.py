"""认证/RBAC 依赖：require_user / require_active_user / require_admin + 对象级归属检查。

安全（docs/08 第 4 节）：
- 会话 cookie 只存 token 明文于客户端，服务端只存哈希。
- 权限检查在服务端资源查询中实施，防 IDOR。
- 越权访问他人资源返回 404（不泄露资源存在性）。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Session as DbSession
from app.db.models import User
from app.db.session import get_db


@dataclass
class AuthContext:
    """当前请求的认证上下文。"""

    user: User
    session: DbSession


def _unauthorized() -> AppError:
    return AppError(code="UNAUTHORIZED", message="未登录或会话已失效", status_code=401)


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthContext:
    """从会话 cookie 解析当前用户；无效/过期/撤销一律 401。"""
    from app.core.security import hash_token

    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        raise _unauthorized()

    token_hash = hash_token(raw)
    now = datetime.now(UTC)
    row = (
        await db.execute(
            select(DbSession, User)
            .join(User, User.id == DbSession.user_id)
            .where(
                DbSession.token_hash == token_hash,
                DbSession.revoked_at.is_(None),
                DbSession.expires_at > now,
            )
        )
    ).first()
    if row is None:
        raise _unauthorized()

    session, user = row
    # 封禁账号（阶段 8 CLI）：即使会话尚未撤销也一律拒绝
    if user.status == "suspended":
        raise AppError(
            code="ACCOUNT_SUSPENDED",
            message="账号已被封禁，如有疑问请联系支持",
            status_code=403,
        )
    session.last_seen_at = now
    user.last_active_at = now
    await db.commit()
    return AuthContext(user=user, session=session)


async def require_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """要求已登录（任意状态，含 deletion_pending —— 允许其登录以便撤销注销）。"""
    return ctx


async def require_active_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """要求已登录且账号非注销恢复期：deletion_pending 拒绝一切新的写/敏感操作。"""
    if ctx.user.status == "deletion_pending":
        raise AppError(
            code="ACCOUNT_DELETION_PENDING",
            message="账号处于注销恢复期，已停止新的处理；可在恢复期内撤销注销",
            status_code=403,
        )
    return ctx


async def require_admin(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """要求管理员角色。"""
    if ctx.user.role != "admin":
        raise AppError(code="FORBIDDEN", message="没有访问该资源的权限", status_code=403)
    if ctx.user.status == "deletion_pending":
        raise AppError(
            code="ACCOUNT_DELETION_PENDING",
            message="账号处于注销恢复期",
            status_code=403,
        )
    return ctx


def ensure_owner(resource_owner_id: uuid.UUID | None, user: User) -> None:
    """对象级归属检查：非本人资源按 404 处理（防 IDOR 且不泄露存在性）。"""
    if resource_owner_id is None or resource_owner_id != user.id:
        raise AppError(code="NOT_FOUND", message="资源不存在", status_code=404)
