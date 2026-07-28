"""magic link 全流程：Mailpit 真发信、防重放、过期、防枚举、限流、年龄确认。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tests.integration.conftest import (
    create_invite,
    create_user,
    mailpit_find_token,
    request_magic_link,
    session_cookie,
    signup_and_login,
    unique_email,
)


async def test_full_signup_flow_via_mailpit(client, db_factory):
    """邀请码 + magic link 首次登录创建用户，会话 cookie 属性正确。"""
    email = unique_email()
    await create_invite(db_factory, "flow-code", max_uses=1)

    resp = await request_magic_link(client, email, "flow-code")
    assert resp.status_code == 202

    token = mailpit_find_token(email)
    verify = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    assert verify.status_code == 200
    body = verify.json()
    assert body["user"]["email"] == email
    assert body["user"]["role"] == "user"
    assert body["user"]["status"] == "active"
    assert body["user"]["age_attested_at"] is not None

    # cookie 必须 Secure/HttpOnly/SameSite
    set_cookie = verify.headers["set-cookie"].lower()
    assert "secure" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie

    # 会话可用
    me = await client.get("/api/v1/auth/session")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == email

    # 邀请码被消耗
    from app.db.models import Invite

    async with db_factory() as db:
        invite = (await db.execute(select(Invite))).scalars().one()
        assert invite.used_count == 1


async def test_magic_link_replay_rejected(client, db_factory):
    """token 单次使用：第二次 verify 必须失败（原子防重放）。"""
    email = unique_email()
    await create_invite(db_factory, "replay-code", max_uses=5)
    await request_magic_link(client, email, "replay-code")
    token = mailpit_find_token(email)

    first = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    assert first.status_code == 200
    second = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "MAGIC_LINK_INVALID"


async def test_magic_link_expired_rejected(client, db_factory):
    """过期 token 拒绝。"""
    email = unique_email()
    await create_invite(db_factory, "expire-code", max_uses=5)
    await request_magic_link(client, email, "expire-code")
    token = mailpit_find_token(email)

    from app.db.models import AuthToken

    async with db_factory() as db:
        await db.execute(
            update(AuthToken).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()

    resp = await client.post("/api/v1/auth/magic-links/verify", json={"token": token})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "MAGIC_LINK_INVALID"


async def test_uniform_response_prevents_email_enumeration(client, db_factory):
    """已注册邮箱和新邮箱的请求响应完全一致。"""
    existing = unique_email("existing")
    await create_user(db_factory, existing)
    await create_invite(db_factory, "enum-code", max_uses=10)

    resp_existing = await request_magic_link(client, existing, "enum-code")
    resp_new = await request_magic_link(client, unique_email("fresh"), "enum-code")

    assert resp_existing.status_code == resp_new.status_code == 202
    assert resp_existing.json() == resp_new.json()


async def test_magic_link_rate_limited_by_email(client, db_factory):
    """同一邮箱超过限额返回 429 RATE_LIMITED + retry_after。"""
    email = unique_email("ratelimit")
    await create_invite(db_factory, "rl-code", max_uses=10)

    from app.core.config import get_settings

    limit = get_settings().magic_link_rate_limit_per_email
    for _ in range(limit):
        resp = await request_magic_link(client, email, "rl-code")
        assert resp.status_code == 202

    blocked = await request_magic_link(client, email, "rl-code")
    assert blocked.status_code == 429
    err = blocked.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert err["details"]["retry_after"] > 0


async def test_age_attestation_required(client, db_factory):
    """未确认年满 18 岁不得继续。"""
    await create_invite(db_factory, "age-code", max_uses=5)
    resp = await request_magic_link(client, unique_email(), "age-code", age_attested=False)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "AGE_ATTESTATION_REQUIRED"


async def test_magic_link_requires_valid_invite(client, db_factory):
    """无效邀请码直接拒绝（与邮箱无关，不构成枚举通道）。"""
    resp = await request_magic_link(client, unique_email(), "no-such-code")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVITE_INVALID"


async def test_invite_validate_does_not_expose_remaining_uses(client, db_factory):
    """校验邀请码只返回有效与否。"""
    await create_invite(db_factory, "check-code", max_uses=7)

    ok = await client.post("/api/v1/auth/invites/validate", json={"code": "check-code"})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True}  # 只有 valid 字段，无剩余次数/期限

    bad = await client.post("/api/v1/auth/invites/validate", json={"code": "wrong"})
    assert bad.status_code == 200
    assert bad.json() == {"valid": False}


async def test_logout_and_session_management(client, db_factory, make_client):
    """登出、会话列表、撤销自己的其他会话。"""
    email = unique_email("sess")
    _, cookie_a = await signup_and_login(client, db_factory, email)

    # 第二个设备登录（同邮箱，新客户端）
    other = make_client()
    async with other:
        await signup_and_login(other, db_factory, email)

    sessions = await client.get("/api/v1/auth/sessions")
    assert sessions.status_code == 200
    items = sessions.json()
    assert len(items) == 2
    current = [s for s in items if s["current"]]
    assert len(current) == 1
    other_id = next(s["id"] for s in items if not s["current"])

    # 撤销另一台设备
    revoke = await client.delete(f"/api/v1/auth/sessions/{other_id}")
    assert revoke.status_code == 200
    remaining = (await client.get("/api/v1/auth/sessions")).json()
    assert len(remaining) == 1

    # 登出当前会话后 401
    logout = await client.delete("/api/v1/auth/session")
    assert logout.status_code == 200
    client.cookies.set(session_cookie(), cookie_a)
    me = await client.get("/api/v1/auth/session")
    assert me.status_code == 401
