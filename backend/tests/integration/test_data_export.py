"""阶段 8 用户数据导出：内容完整性、重新认证、限时下载、越权与过期（docs/08 第 9 节）。"""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.config import get_settings
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.resume_files import make_pdf
from tests.integration.test_standard_analysis import setup_recommendations


async def request_export(client):
    return await client.post("/api/v1/privacy/data-exports")


async def download_zip(client, export_body) -> zipfile.ZipFile:
    resp = await client.get(f"/api/v1{export_body['download_url']}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.content))


async def test_export_contains_full_own_data_and_nothing_foreign(
    client, db_factory, make_client
):
    # 构造完整数据图：事实 + 方案 + 推荐 + 反馈 + 分析 + 定制简历 + 上传原件
    user, plan, items = await setup_recommendations(
        db_factory, client, "export-main@cc-integration.dev"
    )
    rec_id = items[0]["id"]
    assert (
        await client.post(
            f"/api/v1/recommendations/{rec_id}/feedback",
            json={"sentiment": "interested"},
        )
    ).status_code in (200, 201)
    assert (
        await client.post(f"/api/v1/recommendations/{rec_id}/deep-analysis")
    ).status_code == 202
    assert (
        await client.post(f"/api/v1/recommendations/{rec_id}/resume-drafts")
    ).status_code == 202

    pdf_bytes = make_pdf()
    up = await client.post(
        "/api/v1/resumes/uploads",
        files={"file": ("resume.pdf", pdf_bytes, "application/pdf")},
    )
    assert up.status_code == 201, up.text
    confirm = await client.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    assert confirm.status_code == 202, confirm.text
    resume_id = confirm.json()["resume"]["id"]

    # 另一个用户的独特数据：绝不允许混入导出
    other_marker = "OTHER_USER_SECRET_MARKER_9f3a"
    other = await create_user(db_factory, unique_email("other"))
    from app.db.models import ProfileFact

    async with db_factory() as db:
        db.add(
            ProfileFact(
                user_id=other.id,
                fact_type="skill",
                value_json={"name": other_marker},
                status="active",
                provenance_type="resume",
                confirmed_by_user_at=datetime.now(UTC),
            )
        )
        await db.commit()

    resp = await request_export(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"  # eager 模式同步完成
    assert body["download_url"], "成功导出必须有限时下载链接"
    assert body["file_sha256"] and body["size_bytes"] > 0

    zf = await download_zip(client, body)
    names = zf.namelist()
    assert "export.json" in names
    payload = json.loads(zf.read("export.json"))

    assert payload["schema_version"] == "user_data_export_v1"
    assert payload["account"]["email"] == "export-main@cc-integration.dev"
    # facts / 方案 / 推荐 / 反馈 / 分析 / 简历版本 全部在场
    assert payload["profile_facts"], "导出必须包含已确认事实"
    assert any(p["name"] == "后端方案" for p in payload["search_plans"])
    assert payload["recommendations"] and payload["recommendations"][0]["job"]
    assert payload["feedback"][0]["sentiment"] == "interested"
    assert payload["analyses"][0]["report"] is not None
    assert payload["resume_versions"][0]["content"] is not None
    # 上传原件进 ZIP 且逐字节一致
    entry = next(r for r in payload["resumes"] if r["id"] == resume_id)
    assert entry["file_in_zip"] in names
    assert zf.read(entry["file_in_zip"]) == pdf_bytes

    # 红线：不含其他用户数据、不含系统密钥、不含内部提示词
    full_text = json.dumps(payload, ensure_ascii=False)
    assert other_marker not in full_text
    assert get_settings().secret_pepper not in full_text
    assert "prompt" not in {k.lower() for k in payload}

    # 越权：其他用户访问该导出 → 404
    other_client = make_client()
    other_client.cookies.set(session_cookie(), await create_session_for(db_factory, other))
    foreign = await other_client.get(f"/api/v1/privacy/data-exports/{body['id']}")
    assert foreign.status_code == 404
    await other_client.aclose()


async def test_export_requires_recent_login_reauth(client, db_factory):
    from app.db.models import Session as DbSession

    user = await create_user(db_factory, unique_email("stale"))
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    # 把会话做旧到重新认证窗口之外
    stale = datetime.now(UTC) - timedelta(
        seconds=get_settings().data_export_reauth_window_seconds + 60
    )
    async with db_factory() as db:
        await db.execute(
            update(DbSession).where(DbSession.user_id == user.id).values(created_at=stale)
        )
        await db.commit()

    resp = await request_export(client)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "REAUTH_REQUIRED"


async def test_export_daily_limit_and_quota_override(client, db_factory):
    from app.db.models import User

    user = await create_user(db_factory, unique_email("quota"))
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    async with db_factory() as db:
        await db.execute(
            update(User)
            .where(User.id == user.id)
            .values(quota_overrides_json={"data_export_daily": 1})
        )
        await db.commit()

    first = await request_export(client)
    assert first.status_code == 202
    assert first.json()["status"] == "succeeded"

    second = await request_export(client)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"
    assert second.json()["error"]["details"]["limit"] == 1


async def test_export_link_expires_and_file_purged(client, db_factory):
    from app.db.models import DataExport
    from app.integrations.storage import get_storage

    user = await create_user(db_factory, unique_email("expire"))
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    body = (await request_export(client)).json()
    assert body["status"] == "succeeded"
    url = body["download_url"]

    # 过期：状态接口不再给链接，文件被清理；旧签名链接 → 410
    async with db_factory() as db:
        await db.execute(
            update(DataExport)
            .where(DataExport.id == uuid.UUID(body["id"]))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()

    status_resp = await client.get(f"/api/v1/privacy/data-exports/{body['id']}")
    assert status_resp.status_code == 200
    assert status_resp.json()["download_url"] is None

    gone = await client.get(f"/api/v1{url}")
    assert gone.status_code == 410
    assert gone.json()["error"]["code"] == "EXPORT_EXPIRED"

    async with db_factory() as db:
        export = (
            await db.execute(select(DataExport).where(DataExport.id == uuid.UUID(body["id"])))
        ).scalar_one()
        assert export.file_purged_at is not None
        assert not get_storage().exists(export.storage_key)

    # 篡改签名 → 404（不泄露信息）
    fresh = (await request_export(client)).json()
    tampered = fresh["download_url"].rsplit("sig=", 1)[0] + "sig=" + "0" * 64
    assert (await client.get(f"/api/v1{tampered}")).status_code == 404


async def test_export_allowed_during_deletion_grace_period(client, db_factory):
    """注销恢复期内仍可行使数据导出权（数据主体权利不属于"新的处理"）。"""
    user = await create_user(db_factory, unique_email("grace"))
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    assert (await client.post("/api/v1/privacy/account-deletion")).status_code == 202

    resp = await request_export(client)
    assert resp.status_code == 202
    assert resp.json()["status"] == "succeeded"
