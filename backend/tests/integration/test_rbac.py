"""RBAC 与对象级权限：普通用户禁入 /admin/*，跨用户资源按 404 拒绝。"""

from sqlalchemy import select

from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)


async def test_normal_user_cannot_access_admin_routes(client, db_factory):
    user = await create_user(db_factory, unique_email())
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)

    resp = await client.get("/api/v1/admin/invites")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"

    resp = await client.post("/api/v1/admin/invites", json={"max_uses": 3})
    assert resp.status_code == 403


async def test_unauthenticated_gets_401(client):
    for path in ("/api/v1/auth/session", "/api/v1/consents", "/api/v1/admin/invites"):
        resp = await client.get(path)
        assert resp.status_code == 401, path
        assert resp.json()["error"]["code"] == "UNAUTHORIZED"


async def test_user_cannot_revoke_other_users_session(client, db_factory, make_client):
    """用户 A 撤销用户 B 的会话：404（不泄露存在性），且 B 会话仍有效。"""
    user_a = await create_user(db_factory, unique_email("aaa"))
    user_b = await create_user(db_factory, unique_email("bbb"))
    cookie_a = await create_session_for(db_factory, user_a)
    cookie_b = await create_session_for(db_factory, user_b)

    from app.db.models import Session as DbSession

    async with db_factory() as db:
        b_session_id = (
            (await db.execute(select(DbSession.id).where(DbSession.user_id == user_b.id)))
            .scalars()
            .one()
        )

    client.cookies.set(session_cookie(), cookie_a)
    resp = await client.delete(f"/api/v1/auth/sessions/{b_session_id}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    # B 的会话未被撤销
    other = make_client()
    async with other:
        other.cookies.set(session_cookie(), cookie_b)
        me = await other.get("/api/v1/auth/session")
        assert me.status_code == 200


async def test_user_cannot_revoke_other_users_consent(client, db_factory, make_client):
    """跨用户撤回授权同样按 404 拒绝。"""
    user_a = await create_user(db_factory, unique_email("aaa"))
    user_b = await create_user(db_factory, unique_email("bbb"))
    cookie_a = await create_session_for(db_factory, user_a)
    cookie_b = await create_session_for(db_factory, user_b)

    from app.consents.notices import PROVIDER_NOTICE_VERSIONS

    other = make_client()
    async with other:
        other.cookies.set(session_cookie(), cookie_b)
        created = await other.post(
            "/api/v1/consents",
            json={
                "provider": "deepseek",
                "scope": "full_resume",
                "notice_version": PROVIDER_NOTICE_VERSIONS["deepseek"],
            },
        )
        assert created.status_code == 201
        consent_id = created.json()["id"]

    client.cookies.set(session_cookie(), cookie_a)
    resp = await client.delete(f"/api/v1/consents/{consent_id}")
    assert resp.status_code == 404

    # B 的授权仍然有效
    from app.consents.guard import model_authorization_guard

    async with db_factory() as db:
        decision = await model_authorization_guard.check(
            db, user_b.id, "deepseek", "full_resume"
        )
        assert decision.allowed


async def test_admin_invite_crud_and_audit(client, db_factory):
    """管理员可增删查邀请码；明文只出现一次；操作写审计。"""
    admin = await create_user(db_factory, unique_email("admin"), role="admin")
    cookie = await create_session_for(db_factory, admin)
    client.cookies.set(session_cookie(), cookie)

    created = await client.post(
        "/api/v1/admin/invites", json={"max_uses": 3}
    )
    assert created.status_code == 201
    body = created.json()
    code = body["code"]
    assert code.startswith("cc-")
    invite_id = body["id"]

    # 列表不返回明文/哈希
    listed = await client.get("/api/v1/admin/invites")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert "code" not in items[0]
    assert "code_hash" not in items[0]

    # 新邀请码可用
    ok = await client.post("/api/v1/auth/invites/validate", json={"code": code})
    assert ok.json() == {"valid": True}

    # 停用后立即失效
    disabled = await client.delete(f"/api/v1/admin/invites/{invite_id}")
    assert disabled.status_code == 200
    assert disabled.json()["disabled_at"] is not None
    bad = await client.post("/api/v1/auth/invites/validate", json={"code": code})
    assert bad.json() == {"valid": False}

    # 管理员操作写入审计
    from sqlalchemy import select

    from app.db.models import AuditEvent

    async with db_factory() as db:
        actions = (
            (await db.execute(select(AuditEvent.action))).scalars().all()
        )
    assert "admin_invite_created" in actions
    assert "admin_invite_disabled" in actions
