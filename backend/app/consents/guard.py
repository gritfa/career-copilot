"""ModelAuthorizationGuard：模型服务商授权门控（docs/08 第 3 节）。

规则：
- provider + scope 精确匹配，DeepSeek 的授权不被 Qwen 继承（反之亦然）。
- 撤回（revoked_at）或过期（expires_at）后，新调用立即拒绝。
- 账号处于 deletion_pending 时停止新的模型处理，一律拒绝。
- 后续 Gateway 必须在每次供应商调用前经过本 Guard，备用切换不是例外。
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import CONSENT_PROVIDERS, CONSENT_SCOPES, Consent, User


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason_code: str | None = None  # NO_CONSENT / CONSENT_REVOKED / CONSENT_EXPIRED /
    #                                 ACCOUNT_DELETION_PENDING / USER_NOT_FOUND
    consent_id: uuid.UUID | None = None


class ModelAuthorizationGuard:
    """供 Gateway / 任务层在每次模型调用前强制检查。"""

    async def check(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        provider: str,
        scope: str,
    ) -> AuthorizationDecision:
        """返回决策；不抛异常，便于调用方选择降级路径。"""
        if provider not in CONSENT_PROVIDERS or scope not in CONSENT_SCOPES:
            return AuthorizationDecision(allowed=False, reason_code="NO_CONSENT")

        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user is None:
            return AuthorizationDecision(allowed=False, reason_code="USER_NOT_FOUND")
        if user.status == "deletion_pending":
            return AuthorizationDecision(allowed=False, reason_code="ACCOUNT_DELETION_PENDING")

        now = datetime.now(UTC)
        # provider 与 scope 均为精确匹配：deepseek 授权不覆盖 qwen；
        # full_resume 与 deidentified 是不同 scope，互不包含。
        consents = (
            (
                await db.execute(
                    select(Consent).where(
                        Consent.user_id == user_id,
                        Consent.provider == provider,
                        Consent.scope == scope,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not consents:
            return AuthorizationDecision(allowed=False, reason_code="NO_CONSENT")

        for consent in consents:
            if consent.revoked_at is not None:
                continue
            if consent.expires_at is not None and consent.expires_at <= now:
                continue
            return AuthorizationDecision(allowed=True, consent_id=consent.id)

        # 存在记录但全部撤回/过期
        any_revoked = any(c.revoked_at is not None for c in consents)
        return AuthorizationDecision(
            allowed=False,
            reason_code="CONSENT_REVOKED" if any_revoked else "CONSENT_EXPIRED",
        )

    async def require(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        provider: str,
        scope: str,
    ) -> AuthorizationDecision:
        """检查失败时抛 403 CONSENT_REQUIRED（稳定错误码，供 API 层直接使用）。"""
        decision = await self.check(db, user_id, provider, scope)
        if not decision.allowed:
            raise AppError(
                code="CONSENT_REQUIRED",
                message=f"需要先授权 {provider} 处理 {scope} 范围的数据",
                status_code=403,
                details={
                    "provider": provider,
                    "scope": scope,
                    "reason": decision.reason_code,
                    "allowed_fallback": "deidentified" if scope == "full_resume" else None,
                },
            )
        return decision


model_authorization_guard = ModelAuthorizationGuard()
