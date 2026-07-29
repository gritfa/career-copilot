"""安全审计写入（append-only）。

允许字段：actor/action/resource/result/reason_code/ip_hash（docs/03 第 8 节）。
严禁：邮箱明文、token、正文、密钥。调用方只能传 ID / 哈希 / 枚举码。
"""

import uuid
from typing import Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.db.models import AuditEvent

logger = structlog.get_logger("app.audit")

ActorType = Literal["user", "admin", "system", "anonymous"]
AuditResult = Literal["success", "denied", "failure"]


async def record_audit(
    db: AsyncSession,
    *,
    actor_type: ActorType,
    action: str,
    actor_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    result: AuditResult = "success",
    reason_code: str | None = None,
    ip_hash: str | None = None,
) -> AuditEvent:
    """写入一条审计事件（不 commit，由调用方的事务统一提交）。"""
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        reason_code=reason_code,
        ip_hash=ip_hash,
    )
    db.add(event)
    _log_event(event)
    return event


def record_audit_sync(
    db: SyncSession,
    *,
    actor_type: ActorType,
    action: str,
    actor_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    result: AuditResult = "success",
    reason_code: str | None = None,
    ip_hash: str | None = None,
) -> AuditEvent:
    """同步版本（Celery 任务 / CLI 管理命令）；约束与异步版完全一致。"""
    event = AuditEvent(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        reason_code=reason_code,
        ip_hash=ip_hash,
    )
    db.add(event)
    _log_event(event)
    return event


def _log_event(event: AuditEvent) -> None:
    # 结构化日志同样只含伪匿名字段
    logger.info(
        "audit_event",
        action=event.action,
        actor_type=event.actor_type,
        actor_id=str(event.actor_id) if event.actor_id else None,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        result=event.result,
        reason_code=event.reason_code,
    )
