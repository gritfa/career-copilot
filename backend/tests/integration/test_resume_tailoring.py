"""岗位定制简历与导出集成测试（真实 PostgreSQL + Redis，Celery eager）。

覆盖：生成主路径（事实绑定 + 调整可追溯 + not_verified 标注）、每日额度、
虚构内容被确定性校验拦截、用户编辑校验（未确认事实/编造数字 422）、
确认后 DOCX/PDF 导出（文件真实可打开）、限时下载/过期清理、越权 404、
受保护属性绝不进内容、日志无正文。
"""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta

import structlog.testing
from sqlalchemy import select, update

from tests.integration.conftest import create_session_for, create_user, session_cookie
from tests.integration.test_standard_analysis import setup_recommendations


async def create_draft(client, rec_id: str) -> dict:
    resp = await client.post(f"/api/v1/recommendations/{rec_id}/resume-drafts")
    assert resp.status_code == 202, resp.text
    return resp.json()


async def confirm_and_export(client, version_id: str, fmt: str) -> dict:
    confirm = await client.post(f"/api/v1/resume-versions/{version_id}/confirm")
    assert confirm.status_code == 200, confirm.text
    resp = await client.post(
        f"/api/v1/resume-versions/{version_id}/exports", json={"format": fmt}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


async def user_fact_ids(db_factory, user) -> set[str]:
    from app.db.models import ProfileFact

    async with db_factory() as db:
        return {
            str(fid)
            for fid in (
                await db.execute(
                    select(ProfileFact.id).where(ProfileFact.user_id == user.id)
                )
            ).scalars()
        }


# ---------------- 主路径：生成 → 事实绑定 + 可追溯调整 ----------------


async def test_draft_generation_binds_confirmed_facts_only(db_factory, client):
    user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-main@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])

    # eager 模式下任务同步完成
    assert body["status"] == "draft"
    assert body["kind"] == "job_tailored"
    assert body["template_id"] == "standard_single_column_v1"
    assert body["created_by"] == "agent_draft"
    assert body["provider"] == "synthetic"
    assert body["model_id"] == "synthetic-tailor@1"
    assert body["verified"] is False  # 合成产出如实标注 not_verified

    fact_ids = await user_fact_ids(db_factory, user)
    content = body["content"]
    assert content["schema_version"] == "resume_content_v1"
    assert content["target_job_title"], "目标岗位标题应来自岗位侧信息"
    assert content["sections"], "应产出非空简历章节"
    for section in content["sections"]:
        for item in section["items"]:
            assert item["fact_ids"], "每条内容必须绑定事实引用"
            assert set(item["fact_ids"]) <= fact_ids, "只能引用已确认事实"

    # 每处调整可追溯：理由 + fact IDs + 岗位证据
    assert body["changes"], "定制应产出可追溯的调整记录"
    for change in body["changes"]:
        assert change["reason"]
        assert set(change["fact_ids"]) <= fact_ids
        assert change["job_span"], "排序决策必须带岗位原文证据"

    # 费用账本：llm_tailor 记账（合成实现零费用也要记）
    from app.db.models import UsageLedger

    async with db_factory() as db:
        ledger = (
            (
                await db.execute(
                    select(UsageLedger).where(UsageLedger.operation_type == "llm_tailor")
                )
            )
            .scalars()
            .all()
        )
    assert len(ledger) == 1
    assert ledger[0].provider == "synthetic"
    assert float(ledger[0].amount_estimated) == 0.0

    # 列表 / 详情
    listing = (
        await client.get(f"/api/v1/resume-versions?recommendation_id={items[0]['id']}")
    ).json()
    assert [v["id"] for v in listing["items"]] == [body["id"]]
    detail = (await client.get(f"/api/v1/resume-versions/{body['id']}")).json()
    assert detail["status"] == "draft"


async def test_protected_attribute_facts_never_enter_resume(db_factory, client):
    """受保护属性（性别等）即使在事实库中也绝不进入定制简历。"""
    user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-protected@cc-integration.dev"
    )
    from app.db.models import ProfileFact

    async with db_factory() as db:
        db.add(
            ProfileFact(
                user_id=user.id,
                fact_type="gender",
                value_json={"value": "男"},
                status="active",
                provenance_type="import",
                confirmed_by_user_at=datetime.now(UTC),
            )
        )
        await db.commit()

    body = await create_draft(client, items[0]["id"])
    assert body["status"] == "draft"
    dumped = json.dumps(body["content"], ensure_ascii=False)
    assert "男" not in dumped
    assert "gender" not in dumped


# ---------------- 每日额度：新版本 3 个；编辑/确认/导出不计数 ----------------


async def test_daily_quota_3_new_versions_then_429(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-quota@cc-integration.dev", n_jobs=4
    )
    assert len(items) == 4
    first = await create_draft(client, items[0]["id"])
    for item in items[1:3]:
        await create_draft(client, item["id"])

    resp = await client.post(
        f"/api/v1/recommendations/{items[3]['id']}/resume-drafts"
    )
    assert resp.status_code == 429
    err = resp.json()["error"]
    assert err["code"] == "RATE_LIMITED"
    assert err["details"] == {"limit": 3, "used": 3}

    # 编辑 / 确认 / 导出不计数：额度用尽后仍可执行
    patched = await client.patch(
        f"/api/v1/resume-versions/{first['id']}", json={"content": first["content"]}
    )
    assert patched.status_code == 200
    export = await confirm_and_export(client, first["id"], "docx")
    assert export["status"] == "succeeded"


# ---------------- 红线：虚构内容被确定性校验拦截 ----------------


def _fabricating_adapter(content_items):
    from app.integrations.llm_gateway import LLMRawResponse, LLMRequest

    fabricated = {
        "schema_version": "resume_tailor_v1",
        "content": {
            "schema_version": "resume_content_v1",
            "target_job_title": "Python 后端开发工程师",
            "target_company": "",
            "sections": [
                {"kind": "skills", "title": "专业技能", "items": content_items}
            ],
        },
        "changes": [],
    }

    class FabricatingAdapter:
        provider = "synthetic"
        model_id = "synthetic-tailor@1"
        configured = True

        def complete(self, request: LLMRequest):
            return LLMRawResponse(
                content=json.dumps(fabricated, ensure_ascii=False),
                tokens_in=10,
                tokens_out=10,
            )

    return FabricatingAdapter()


async def test_fabricated_fact_reference_blocks_draft(db_factory, client, monkeypatch):
    """模型虚构事实引用 → 校验拦截，版本明确失败且不留内容。"""
    monkeypatch.setattr(
        "app.tailoring.service.get_tailor_adapter",
        lambda: _fabricating_adapter(
            [{"text": "Kubernetes 专家经验", "fact_ids": [str(uuid.uuid4())]}]
        ),
    )
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-fab1@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])
    assert body["status"] == "failed"
    assert body["error_code"] == "EVIDENCE_VALIDATION_FAILED"
    assert body["content"] is None  # 失败绝不落半成品内容


async def test_fabricated_number_blocks_draft(db_factory, client, monkeypatch):
    """模型给已确认技能编造数字（夸大）→ 同样拦截。"""

    async def _setup():
        return await setup_recommendations(
            db_factory, client, "tailor-fab2@cc-integration.dev"
        )

    user, _plan, items = await _setup()
    fact_ids = sorted(await user_fact_ids(db_factory, user))
    monkeypatch.setattr(
        "app.tailoring.service.get_tailor_adapter",
        lambda: _fabricating_adapter(
            [{"text": "Python：8 年资深经验", "fact_ids": [fact_ids[0]]}]
        ),
    )
    body = await create_draft(client, items[0]["id"])
    assert body["status"] == "failed"
    assert body["error_code"] == "EVIDENCE_VALIDATION_FAILED"


# ---------------- 用户编辑：服务端重新验证事实引用 ----------------


async def test_patch_rewording_ok_but_unknown_fact_rejected(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-edit@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])
    content = body["content"]

    # 合法编辑：调整措辞（不引入新数字/新事实）
    content["sections"][0]["items"][0]["text"] = (
        content["sections"][0]["items"][0]["text"] + "（重点）"
    )
    patched = await client.patch(
        f"/api/v1/resume-versions/{body['id']}", json={"content": content}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["edited_by_user_at"] is not None

    # 未确认事实引用：拒绝（新技能必须走候选确认流程）
    bad = json.loads(json.dumps(content))
    bad["sections"][0]["items"].append(
        {"text": "精通 Rust", "fact_ids": [str(uuid.uuid4())]}
    )
    resp = await client.patch(
        f"/api/v1/resume-versions/{body['id']}", json={"content": bad}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "FACT_REFERENCE_INVALID"

    # 编造数字：拒绝（即使引用了真实事实）
    bad2 = json.loads(json.dumps(content))
    bad2["sections"][0]["items"][0]["text"] = "团队效率提升 300%"
    resp = await client.patch(
        f"/api/v1/resume-versions/{body['id']}", json={"content": bad2}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "FACT_REFERENCE_INVALID"


# ---------------- 导出：确认门槛 + 文件真实可打开 + 限时下载 ----------------


async def test_export_requires_confirmation(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-confirm@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])
    resp = await client.post(
        f"/api/v1/resume-versions/{body['id']}/exports", json={"format": "docx"}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VERSION_NOT_CONFIRMED"


async def test_docx_and_pdf_export_download_and_verify(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-export@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])

    docx_export = await confirm_and_export(client, body["id"], "docx")
    assert docx_export["status"] == "succeeded"
    assert docx_export["download_url"]
    assert docx_export["expires_at"]

    download = await client.get(f"/api/v1{docx_export['download_url']}")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    # DOCX 解包验证：合法 OOXML 且正文含已确认事实内容
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert zf.testzip() is None
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "星辰科技有限公司" in xml

    pdf_resp = await client.post(
        f"/api/v1/resume-versions/{body['id']}/exports", json={"format": "pdf"}
    )
    pdf_export = pdf_resp.json()
    assert pdf_export["status"] == "succeeded"
    pdf_download = await client.get(f"/api/v1{pdf_export['download_url']}")
    assert pdf_download.status_code == 200
    assert pdf_download.content.startswith(b"%PDF")  # PDF 魔数

    # 状态查询与 sha256 一致性
    status_body = (await client.get(f"/api/v1/resume-exports/{docx_export['id']}")).json()
    assert status_body["status"] == "succeeded"
    import hashlib

    assert status_body["file_sha256"] == hashlib.sha256(download.content).hexdigest()


async def test_expired_export_link_rejected_and_file_purged(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-expire@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])
    export = await confirm_and_export(client, body["id"], "docx")
    url = export["download_url"]

    from app.db.models import ResumeExport
    from app.integrations.storage import get_storage

    async with db_factory() as db:
        row = (
            await db.execute(
                select(ResumeExport).where(ResumeExport.id == uuid.UUID(export["id"]))
            )
        ).scalar_one()
        storage_key = row.storage_key
        await db.execute(
            update(ResumeExport)
            .where(ResumeExport.id == row.id)
            .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await db.commit()
    assert get_storage().exists(storage_key)

    # 过期签名链接被拒绝，且文件被清理（不留服务器长期副本）
    resp = await client.get(f"/api/v1{url}")
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "EXPORT_EXPIRED"
    assert not get_storage().exists(storage_key)

    # 状态接口不再给下载链接
    status_body = (await client.get(f"/api/v1/resume-exports/{export['id']}")).json()
    assert status_body["download_url"] is None

    # 签名被篡改：404（不泄露信息）
    fresh = await client.post(
        f"/api/v1/resume-versions/{body['id']}/exports", json={"format": "docx"}
    )
    good_url = fresh.json()["download_url"]
    tampered = good_url[:-4] + ("0000" if not good_url.endswith("0000") else "1111")
    assert (await client.get(f"/api/v1{tampered}")).status_code == 404


# ---------------- 越权（IDOR） ----------------


async def test_cross_user_access_404(db_factory, client, make_client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-owner@cc-integration.dev"
    )
    body = await create_draft(client, items[0]["id"])
    export = await confirm_and_export(client, body["id"], "docx")

    intruder = await create_user(db_factory, "tailor-intruder@cc-integration.dev")
    other = make_client()
    other.cookies.set(session_cookie(), await create_session_for(db_factory, intruder))
    try:
        assert (
            await other.post(f"/api/v1/recommendations/{items[0]['id']}/resume-drafts")
        ).status_code == 404
        assert (
            await other.get(f"/api/v1/resume-versions/{body['id']}")
        ).status_code == 404
        assert (
            await other.post(f"/api/v1/resume-versions/{body['id']}/confirm")
        ).status_code == 404
        assert (
            await other.get(f"/api/v1/resume-exports/{export['id']}")
        ).status_code == 404
        # 拿到完整签名链接也不行：下载绑定属主会话
        assert (await other.get(f"/api/v1{export['download_url']}")).status_code == 404
        # 越权者自己的列表看不到他人版本
        listing = (await other.get("/api/v1/resume-versions")).json()
        assert listing["items"] == []
    finally:
        await other.aclose()


# ---------------- 日志边界：简历正文绝不入日志 ----------------


async def test_tailoring_and_export_logs_contain_no_resume_content(db_factory, client):
    _user, _plan, items = await setup_recommendations(
        db_factory, client, "tailor-logs@cc-integration.dev"
    )
    with structlog.testing.capture_logs() as logs:
        body = await create_draft(client, items[0]["id"])
        assert body["status"] == "draft"
        export = await confirm_and_export(client, body["id"], "pdf")
        assert export["status"] == "succeeded"
        download = await client.get(f"/api/v1{export['download_url']}")
        assert download.status_code == 200

    dumped = repr(logs)
    # 事实内容（雇主/学校/技能明细）与岗位正文绝不入日志
    assert "星辰科技有限公司" not in dumped
    assert "郑州大学" not in dumped
    assert "FastAPI、asyncio" not in dumped
    assert any(e.get("event") == "resume_tailor_completed" for e in logs)
    assert any(e.get("event") == "resume_export_completed" for e in logs)
