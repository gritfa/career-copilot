"""授权与 ModelAuthorizationGuard：撤回即拒、provider 独立、不接受批量。"""

import pytest

from app.consents.guard import model_authorization_guard
from app.consents.notices import PROVIDER_NOTICE_VERSIONS
from app.core.errors import AppError
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)


async def _grant(client, provider: str, scope: str):
    return await client.post(
        "/api/v1/consents",
        json={
            "provider": provider,
            "scope": scope,
            "notice_version": PROVIDER_NOTICE_VERSIONS[provider],
        },
    )


async def test_notices_are_versioned_placeholders(client):
    resp = await client.get("/api/v1/consents/notices")
    assert resp.status_code == 200
    body = resp.json()
    assert body["terms_version"]
    assert body["privacy_version"]
    assert "专业人员复核" in body["disclaimer"]
    for provider in ("deepseek", "qwen", "analytics", "support"):
        notice = body["providers"][provider]
        assert notice["notice_version"]
        assert notice["default_checked"] is False  # 默认不勾选
        assert "专业人员复核" in notice["disclaimer"]


async def test_guard_denies_after_deepseek_revocation(client, db_factory):
    """撤回 DeepSeek 后新调用立即被 Guard 拒绝。"""
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    created = await _grant(client, "deepseek", "full_resume")
    assert created.status_code == 201
    consent_id = created.json()["id"]

    async with db_factory() as db:
        decision = await model_authorization_guard.check(db, user.id, "deepseek", "full_resume")
        assert decision.allowed

    revoked = await client.delete(f"/api/v1/consents/{consent_id}")
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    async with db_factory() as db:
        decision = await model_authorization_guard.check(db, user.id, "deepseek", "full_resume")
        assert not decision.allowed
        assert decision.reason_code == "CONSENT_REVOKED"
        with pytest.raises(AppError) as exc_info:
            await model_authorization_guard.require(db, user.id, "deepseek", "full_resume")
        assert exc_info.value.code == "CONSENT_REQUIRED"
        assert exc_info.value.status_code == 403


async def test_qwen_authorization_not_inherited_from_deepseek(client, db_factory):
    """DeepSeek 授权不覆盖 Qwen；scope 也必须精确匹配。"""
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    assert (await _grant(client, "deepseek", "full_resume")).status_code == 201

    async with db_factory() as db:
        # provider 独立判定
        qwen = await model_authorization_guard.check(db, user.id, "qwen", "full_resume")
        assert not qwen.allowed
        assert qwen.reason_code == "NO_CONSENT"
        # scope 独立判定：full_resume 授权不覆盖 deidentified
        deid = await model_authorization_guard.check(db, user.id, "deepseek", "deidentified")
        assert not deid.allowed


async def test_consent_rejects_batch_and_stale_notice(client, db_factory):
    """POST /consents 不接受批量；notice 版本过期拒绝；重复授权 409。"""
    user = await create_user(db_factory, unique_email())
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    # 批量数组 → 422
    batch = await client.post(
        "/api/v1/consents",
        json=[
            {"provider": "deepseek", "scope": "full_resume", "notice_version": "x"},
            {"provider": "qwen", "scope": "full_resume", "notice_version": "x"},
        ],
    )
    assert batch.status_code == 422

    # 过期告知版本 → 409
    stale = await client.post(
        "/api/v1/consents",
        json={
            "provider": "deepseek",
            "scope": "full_resume",
            "notice_version": "deepseek-2000-01-01.0",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "NOTICE_VERSION_OUTDATED"

    # 正常授权后重复 → 409
    assert (await _grant(client, "deepseek", "full_resume")).status_code == 201
    dup = await _grant(client, "deepseek", "full_resume")
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "CONSENT_ALREADY_GRANTED"

    # 授权/撤回动作有审计
    from sqlalchemy import select

    from app.db.models import AuditEvent

    async with db_factory() as db:
        actions = (await db.execute(select(AuditEvent.action))).scalars().all()
    assert "consent_granted" in actions
