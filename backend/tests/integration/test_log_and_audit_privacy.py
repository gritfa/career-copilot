"""日志/审计敏感字段过滤：不得出现邮箱明文、token；审计 append-only。"""

import pytest
from sqlalchemy import select, text

from tests.integration.conftest import (
    mailpit_find_token,
    request_magic_link,
    signup_and_login,
    unique_email,
)


async def test_logs_do_not_contain_email_or_token(client, db_factory, capfd):
    """跑完整登录 + 授权流程，stdout/stderr 日志里不得有邮箱明文或 token。"""
    email = unique_email("logpriv")
    capfd.readouterr()  # 清掉之前的输出

    body, raw_cookie = await signup_and_login(client, db_factory, email)
    magic_token = mailpit_find_token(email)  # 已消费，但明文仍不得进日志

    from app.consents.notices import PROVIDER_NOTICE_VERSIONS

    resp = await client.post(
        "/api/v1/consents",
        json={
            "provider": "deepseek",
            "scope": "full_resume",
            "notice_version": PROVIDER_NOTICE_VERSIONS["deepseek"],
        },
    )
    assert resp.status_code == 201
    await client.delete("/api/v1/auth/session")

    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert logs, "应当产生结构化请求/审计日志"
    assert email not in logs, "日志包含邮箱明文"
    assert email.split("@")[0] not in logs, "日志包含邮箱局部明文"
    assert magic_token not in logs, "日志包含 magic link token"
    assert raw_cookie not in logs, "日志包含会话 token"


async def test_audit_events_contain_no_email_or_token(client, db_factory):
    """审计表任何字段都不得出现邮箱明文或 token。"""
    email = unique_email("auditpriv")
    _, raw_cookie = await signup_and_login(client, db_factory, email)
    magic_token = mailpit_find_token(email)

    from app.db.models import AuditEvent

    async with db_factory() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
    assert events, "关键动作必须写审计"
    assert {"magic_link_requested", "user_login", "invite_consumed"} <= {
        e.action for e in events
    }
    for event in events:
        blob = " ".join(
            str(v)
            for v in (
                event.action,
                event.resource_type,
                event.resource_id,
                event.reason_code,
                event.ip_hash,
                event.result,
            )
        )
        assert email not in blob
        assert "@" not in blob
        assert magic_token not in blob
        assert raw_cookie not in blob


async def test_audit_events_are_append_only(client, db_factory):
    """数据库触发器拒绝对审计事件的 UPDATE/DELETE。"""
    email = unique_email("appendonly")
    await signup_and_login(client, db_factory, email)

    from sqlalchemy.exc import DBAPIError

    async with db_factory() as db:
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text("UPDATE audit_events SET result = 'denied'"))
        await db.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            await db.execute(text("DELETE FROM audit_events"))
        await db.rollback()


async def test_rate_limit_bucket_keys_contain_no_email(client, db_factory, app):
    """Redis 限流桶键只含哈希，不含邮箱明文。"""
    from tests.integration.conftest import create_invite

    email = unique_email("redispriv")
    await create_invite(db_factory, "redis-code", max_uses=5)
    assert (await request_magic_link(client, email, "redis-code")).status_code == 202

    from app.core.redis import get_redis

    redis = get_redis(app)
    keys = await redis.keys("rl:*")
    assert keys, "限流键应已写入"
    for key in keys:
        assert email not in key
        assert "@" not in key
