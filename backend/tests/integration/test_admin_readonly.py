"""阶段 8 只读管理视图：管理员鉴权隔离 + 指标正确 + 无 PII/正文（docs/04 第 10 节）。"""

import json
from datetime import UTC, datetime

from sqlalchemy import select

from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)

ADMIN_READONLY_PATHS = ("/api/v1/admin/overview", "/api/v1/admin/users",
                        "/api/v1/admin/usage", "/api/v1/admin/purge-runs")


async def login_as(client, db_factory, role="user", email=None):
    user = await create_user(db_factory, email or unique_email(role), role=role)
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    return user


async def test_admin_endpoints_reject_anonymous_and_normal_users(client, db_factory, make_client):
    # 匿名 → 401
    for path in ADMIN_READONLY_PATHS:
        resp = await client.get(path)
        assert resp.status_code == 401, path
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    # 普通用户 → 403 稳定错误码（严格隔离，不泄露内容）
    await login_as(client, db_factory, role="user")
    for path in ADMIN_READONLY_PATHS:
        resp = await client.get(path)
        assert resp.status_code == 403, path
        assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_overview_metrics_and_masked_user_list(client, db_factory):
    from app.db.models import AuditEvent, UsageLedger

    plain_email = unique_email("victim")
    plain = await create_user(db_factory, plain_email)
    await create_user(db_factory, unique_email("pending"), status="deletion_pending")
    async with db_factory() as db:
        db.add(
            UsageLedger(
                user_id=plain.id,
                provider="synthetic",
                model="synthetic-analysis@1",
                operation_type="llm_analysis",
                tokens_in=100,
                tokens_out=50,
                amount_estimated=0,
                occurred_at=datetime.now(UTC),
            )
        )
        await db.commit()

    admin = await login_as(client, db_factory, role="admin")

    overview = await client.get("/api/v1/admin/overview")
    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["users"]["total"] == 3
    assert body["users"]["deletion_pending"] == 1
    assert body["users"]["admins"] == 1
    assert body["recommendations"] == {"total": 0, "today": 0}
    assert body["queue"]["redis_ok"] is True
    assert body["usage_30d"]["by_provider"]["synthetic"]["calls"] == 1
    # 概览全文不含任何邮箱明文
    assert plain_email.split("@")[0] not in overview.text

    users = await client.get("/api/v1/admin/users")
    assert users.status_code == 200
    payload = users.json()
    assert payload["total"] == 3
    text = json.dumps(payload, ensure_ascii=False)
    # 邮箱脱敏：不出现完整本地部分；保留域名可排查
    local = plain_email.split("@")[0]
    assert local not in text
    assert any(u["email_masked"].endswith("@cc-integration.dev") for u in payload["items"])
    # 不含简历正文/联系方式字段
    for item in payload["items"]:
        assert "email_normalized" not in item
        assert set(item) <= {
            "id", "email_masked", "role", "status", "quota_overrides", "created_at",
            "last_active_at", "deletion_requested_at", "purge_after", "suspended_at",
        }

    # 状态过滤
    filtered = (await client.get("/api/v1/admin/users?status=deletion_pending")).json()
    assert filtered["total"] == 1

    # 用户列表访问写审计
    async with db_factory() as db:
        events = (
            (
                await db.execute(
                    select(AuditEvent).where(AuditEvent.action == "admin_users_viewed")
                )
            )
            .scalars()
            .all()
        )
    assert len(events) == 2
    assert all(e.actor_id == admin.id and e.actor_type == "admin" for e in events)


async def test_usage_aggregation_by_provider_model_operation(client, db_factory):
    from app.db.models import UsageLedger

    user = await create_user(db_factory, unique_email())
    async with db_factory() as db:
        for tokens in (100, 200):
            db.add(
                UsageLedger(
                    user_id=user.id,
                    provider="synthetic",
                    model="synthetic-tailor@1",
                    operation_type="llm_tailor",
                    tokens_in=tokens,
                    tokens_out=tokens // 2,
                    amount_estimated=0,
                    occurred_at=datetime.now(UTC),
                )
            )
        await db.commit()

    await login_as(client, db_factory, role="admin")
    resp = await client.get("/api/v1/admin/usage")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["calls"] == 2
    assert rows[0]["tokens_in"] == 300
    assert rows[0]["tokens_out"] == 150
