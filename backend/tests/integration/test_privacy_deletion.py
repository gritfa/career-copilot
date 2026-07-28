"""账号注销状态机：7 天恢复期、期间拒绝新写操作、可撤销恢复。"""

from datetime import UTC, datetime, timedelta

from app.consents.guard import model_authorization_guard
from app.consents.notices import PROVIDER_NOTICE_VERSIONS
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)


async def test_deletion_request_sets_pending_and_purge_after(client, db_factory):
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    before = datetime.now(UTC)
    resp = await client.post("/api/v1/privacy/account-deletion")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "deletion_pending"

    purge_after = datetime.fromisoformat(body["purge_after"])
    expected = before + timedelta(days=7)
    assert abs((purge_after - expected).total_seconds()) < 60  # purge_after = +7 天

    # 重复请求 → 409
    dup = await client.post("/api/v1/privacy/account-deletion")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "DELETION_ALREADY_PENDING"


async def test_deletion_pending_blocks_new_writes_but_allows_recovery(client, db_factory):
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    assert (await client.post("/api/v1/privacy/account-deletion")).status_code == 202

    # 新的写操作（授权）→ 403 稳定错误码
    blocked = await client.post(
        "/api/v1/consents",
        json={
            "provider": "deepseek",
            "scope": "full_resume",
            "notice_version": PROVIDER_NOTICE_VERSIONS["deepseek"],
        },
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ACCOUNT_DELETION_PENDING"

    # Guard 同样停止新模型处理
    async with db_factory() as db:
        decision = await model_authorization_guard.check(db, user.id, "deepseek", "full_resume")
        assert not decision.allowed
        assert decision.reason_code == "ACCOUNT_DELETION_PENDING"

    # 读会话仍允许（登录态可用于撤销注销）
    me = await client.get("/api/v1/auth/session")
    assert me.status_code == 200
    assert me.json()["user"]["status"] == "deletion_pending"

    # 7 天内撤销 → 恢复 active，写操作恢复
    cancel = await client.delete("/api/v1/privacy/account-deletion")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "active"

    ok = await client.post(
        "/api/v1/consents",
        json={
            "provider": "deepseek",
            "scope": "full_resume",
            "notice_version": PROVIDER_NOTICE_VERSIONS["deepseek"],
        },
    )
    assert ok.status_code == 201

    from sqlalchemy import select

    from app.db.models import AuditEvent, User

    async with db_factory() as db:
        refreshed = (
            (await db.execute(select(User).where(User.id == user.id))).scalars().one()
        )
        assert refreshed.status == "active"
        assert refreshed.deletion_requested_at is None
        assert refreshed.purge_after is None
        actions = (await db.execute(select(AuditEvent.action))).scalars().all()
    assert "account_deletion_requested" in actions
    assert "account_deletion_cancelled" in actions


async def test_cancel_without_pending_returns_conflict(client, db_factory):
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    resp = await client.delete("/api/v1/privacy/account-deletion")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NO_DELETION_PENDING"
