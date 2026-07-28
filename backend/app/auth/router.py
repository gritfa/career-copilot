"""认证路由：邀请码校验、magic link、会话管理（docs/04 第 2 节）。

安全（docs/08 第 4 节）：
- token/邀请码只存哈希；magic link 256 bit 随机、15 分钟、单次（原子消费防重放）。
- 请求 magic link 无论邮箱是否已注册都返回统一响应，防枚举。
- 限流：邮箱哈希 + IP 双桶。
- 本模块日志/审计不得出现邮箱明文和 token。
"""

import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, ensure_owner, require_user
from app.auth.schemas import (
    InviteValidateRequest,
    InviteValidateResponse,
    MagicLinkRequest,
    MagicLinkVerifyRequest,
    SessionInfoOut,
    SessionOut,
    UserOut,
)
from app.consents.notices import PRIVACY_VERSION, TERMS_VERSION
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis_dep
from app.core.security import (
    generate_token,
    hash_email,
    hash_invite_code,
    hash_ip,
    hash_token,
    normalize_email,
)
from app.db.models import AuthToken, Invite, User
from app.db.models import Session as DbSession
from app.db.session import get_db
from app.integrations.mailer import send_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])
logger = structlog.get_logger("app.auth")


def _now() -> datetime:
    return datetime.now(UTC)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _find_usable_invite(db: AsyncSession, code: str) -> Invite | None:
    """按哈希查找当前可用的邀请码（未停用、未过期、未用尽）。"""
    invite = (
        await db.execute(select(Invite).where(Invite.code_hash == hash_invite_code(code)))
    ).scalar_one_or_none()
    if invite is None:
        return None
    now = _now()
    if invite.disabled_at is not None:
        return None
    if invite.expires_at is not None and invite.expires_at <= now:
        return None
    if invite.used_count >= invite.max_uses:
        return None
    return invite


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email_normalized,
        role=user.role,
        status=user.status,
        age_attested_at=user.age_attested_at,
        terms_version=user.terms_version,
        privacy_version=user.privacy_version,
    )


@router.post("/invites/validate", response_model=InviteValidateResponse)
async def validate_invite(
    payload: InviteValidateRequest,
    db: AsyncSession = Depends(get_db),
) -> InviteValidateResponse:
    """校验邀请码；只返回有效与否，不暴露剩余次数。"""
    invite = await _find_usable_invite(db, payload.code)
    return InviteValidateResponse(valid=invite is not None)


@router.post("/magic-links", status_code=status.HTTP_202_ACCEPTED)
async def request_magic_link(
    payload: MagicLinkRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_dep),
) -> dict:
    """发送登录链接。需要年龄确认 + 有效邀请码；统一响应防邮箱枚举。"""
    settings = get_settings()

    if payload.age_attested is not True:
        raise AppError(
            code="AGE_ATTESTATION_REQUIRED",
            message="必须确认已年满 18 周岁才能继续",
            status_code=400,
        )

    # 限流：邮箱哈希桶 + IP 桶（桶键只含哈希）
    email_h = hash_email(payload.email)
    ip = _client_ip(request)
    await enforce_rate_limit(
        redis,
        bucket=f"magic:email:{email_h}",
        limit=settings.magic_link_rate_limit_per_email,
        window_seconds=settings.magic_link_rate_window_seconds,
    )
    await enforce_rate_limit(
        redis,
        bucket=f"magic:ip:{hash_ip(ip)}",
        limit=settings.magic_link_rate_limit_per_ip,
        window_seconds=settings.magic_link_rate_window_seconds,
    )

    invite = await _find_usable_invite(db, payload.invite_code)
    if invite is None:
        await record_audit(
            db,
            actor_type="anonymous",
            action="magic_link_requested",
            result="denied",
            reason_code="INVITE_INVALID",
            ip_hash=hash_ip(ip),
        )
        await db.commit()
        raise AppError(
            code="INVITE_INVALID",
            message="邀请码无效或已失效",
            status_code=400,
        )

    email_normalized = normalize_email(payload.email)
    raw_token = generate_token()
    token = AuthToken(
        token_hash=hash_token(raw_token),
        email_normalized=email_normalized,
        invite_id=invite.id,
        expires_at=_now() + timedelta(seconds=settings.magic_link_ttl_seconds),
    )
    db.add(token)
    await db.flush()
    await record_audit(
        db,
        actor_type="anonymous",
        action="magic_link_requested",
        resource_type="auth_token",
        resource_id=str(token.id),
        ip_hash=hash_ip(ip),
    )
    await db.commit()

    # 发信到请求邮箱（本地 = Mailpit）；不打含邮箱/token 的日志
    await send_magic_link_email(email_normalized, raw_token)
    logger.info("magic_link_sent", auth_token_id=str(token.id))

    # 统一响应：不区分邮箱是否已注册
    return {"status": "sent", "message": "如果信息有效，登录链接已发送到该邮箱"}


@router.post("/magic-links/verify", response_model=SessionOut)
async def verify_magic_link(
    payload: MagicLinkVerifyRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    """消费一次性 token：原子防重放；首次登录创建用户并消耗邀请码（并发安全）。"""
    settings = get_settings()
    now = _now()
    ip_h = hash_ip(_client_ip(request))

    # 事务 1：原子消费 token（UPDATE ... WHERE consumed_at IS NULL 防重放）
    consumed = (
        await db.execute(
            update(AuthToken)
            .where(
                AuthToken.token_hash == hash_token(payload.token),
                AuthToken.consumed_at.is_(None),
                AuthToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(AuthToken.id, AuthToken.email_normalized, AuthToken.invite_id)
        )
    ).first()
    if consumed is None:
        await record_audit(
            db,
            actor_type="anonymous",
            action="magic_link_verify",
            result="denied",
            reason_code="MAGIC_LINK_INVALID",
            ip_hash=ip_h,
        )
        await db.commit()
        raise AppError(
            code="MAGIC_LINK_INVALID",
            message="登录链接无效、已过期或已被使用",
            status_code=400,
        )
    await db.commit()  # 立即固化单次使用标记，后续失败也不可重放

    token_id, email_normalized, invite_id = consumed

    # 事务 2：get-or-create 用户 + 原子消耗邀请码 + 建会话 + 审计
    user = (
        await db.execute(select(User).where(User.email_normalized == email_normalized))
    ).scalar_one_or_none()

    created = False
    if user is None:
        # 原子消耗邀请码：行级 UPDATE 带 used_count < max_uses 条件，防并发超用
        invite_row = None
        if invite_id is not None:
            invite_row = (
                await db.execute(
                    update(Invite)
                    .where(
                        Invite.id == invite_id,
                        Invite.used_count < Invite.max_uses,
                        Invite.disabled_at.is_(None),
                    )
                    .values(used_count=Invite.used_count + 1)
                    .returning(Invite.id)
                )
            ).first()
        if invite_row is None:
            await record_audit(
                db,
                actor_type="anonymous",
                action="invite_consume",
                resource_type="invite",
                resource_id=str(invite_id) if invite_id else None,
                result="denied",
                reason_code="INVITE_EXHAUSTED",
                ip_hash=ip_h,
            )
            await db.commit()
            raise AppError(
                code="INVITE_EXHAUSTED",
                message="邀请码已用尽或失效，无法完成注册",
                status_code=409,
            )

        user = User(
            email_normalized=email_normalized,
            role="user",
            status="active",
            age_attested_at=now,  # 请求 magic link 时已强制年龄确认
            terms_version=TERMS_VERSION,
            privacy_version=PRIVACY_VERSION,
        )
        try:
            async with db.begin_nested():
                db.add(user)
                await db.flush()
        except IntegrityError:
            # 极端并发下同邮箱已被并行请求创建：回退邀请码计数并复用已有用户
            await db.execute(
                update(Invite)
                .where(Invite.id == invite_id)
                .values(used_count=Invite.used_count - 1)
            )
            user = (
                await db.execute(select(User).where(User.email_normalized == email_normalized))
            ).scalar_one()
        else:
            created = True

    if created:
        await record_audit(
            db,
            actor_type="user",
            actor_id=user.id,
            action="invite_consumed",
            resource_type="invite",
            resource_id=str(invite_id),
            ip_hash=ip_h,
        )
        await record_audit(
            db,
            actor_type="user",
            actor_id=user.id,
            action="user_created",
            resource_type="user",
            resource_id=str(user.id),
            ip_hash=ip_h,
        )

    # 会话：管理员用更短 TTL
    ttl = (
        settings.admin_session_ttl_seconds
        if user.role == "admin"
        else settings.session_ttl_seconds
    )
    raw_session_token = generate_token()
    session = DbSession(
        user_id=user.id,
        token_hash=hash_token(raw_session_token),
        expires_at=now + timedelta(seconds=ttl),
        ip_hash=ip_h,
        user_agent=(request.headers.get("user-agent") or "")[:256] or None,
    )
    db.add(session)
    await db.flush()
    user.last_active_at = now
    await record_audit(
        db,
        actor_type="user",
        actor_id=user.id,
        action="user_login",
        resource_type="session",
        resource_id=str(session.id),
        ip_hash=ip_h,
    )
    await db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_session_token,
        max_age=ttl,
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return SessionOut(user=_user_out(user), session_id=session.id, expires_at=session.expires_at)


@router.get("/session", response_model=SessionOut)
async def get_session(ctx: AuthContext = Depends(require_user)) -> SessionOut:
    """当前用户、角色与会话信息。"""
    return SessionOut(
        user=_user_out(ctx.user),
        session_id=ctx.session.id,
        expires_at=ctx.session.expires_at,
    )


@router.delete("/session")
async def logout(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """登出当前会话（deletion_pending 状态也允许）。"""
    await db.execute(
        update(DbSession).where(DbSession.id == ctx.session.id).values(revoked_at=_now())
    )
    await record_audit(
        db,
        actor_type="admin" if ctx.user.role == "admin" else "user",
        actor_id=ctx.user.id,
        action="user_logout",
        resource_type="session",
        resource_id=str(ctx.session.id),
        ip_hash=hash_ip(_client_ip(request)),
    )
    await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return {"status": "logged_out"}


@router.get("/sessions", response_model=list[SessionInfoOut])
async def list_sessions(
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[SessionInfoOut]:
    """当前用户的活跃会话（设备列表）。"""
    now = _now()
    rows = (
        (
            await db.execute(
                select(DbSession)
                .where(
                    DbSession.user_id == ctx.user.id,
                    DbSession.revoked_at.is_(None),
                    DbSession.expires_at > now,
                )
                .order_by(DbSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        SessionInfoOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            current=s.id == ctx.session.id,
        )
        for s in rows
    ]


@router.delete("/sessions/{session_id}")
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """撤销指定会话；只能撤销自己的会话（他人会话按 404 处理，防 IDOR）。"""
    target = (
        await db.execute(select(DbSession).where(DbSession.id == session_id))
    ).scalar_one_or_none()
    ensure_owner(target.user_id if target else None, ctx.user)
    assert target is not None  # ensure_owner 已保证
    if target.revoked_at is None:
        target.revoked_at = _now()
    await record_audit(
        db,
        actor_type="admin" if ctx.user.role == "admin" else "user",
        actor_id=ctx.user.id,
        action="session_revoked",
        resource_type="session",
        resource_id=str(target.id),
        ip_hash=hash_ip(_client_ip(request)),
    )
    await db.commit()
    return {"status": "revoked"}
