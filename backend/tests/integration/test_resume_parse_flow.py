"""解析管道：全流程、幂等、受保护属性丢弃、失败状态、超时、日志隐私。"""

import time

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import FactCandidate, Resume, ResumeParse
from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.resume_files import (
    SENSITIVE_STRINGS,
    make_blank_pdf,
    make_docx,
    make_pdf,
)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def login(client, db_factory):
    user = await create_user(db_factory, unique_email())
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)
    return user


async def upload_and_confirm(client, filename: str, data: bytes, mime: str):
    up = await client.post(
        "/api/v1/resumes/uploads", files={"file": (filename, data, mime)}
    )
    assert up.status_code == 201, up.text
    resp = await client.post("/api/v1/resumes", json={"upload_id": up.json()["upload_id"]})
    assert resp.status_code == 202, resp.text
    return resp.json()


def assert_full_parse(parse_body: dict) -> None:
    assert parse_body["status"] == "succeeded"
    assert parse_body["error_code"] is None
    candidates = parse_body["candidates"]
    types = {c["fact_type"] for c in candidates}
    assert {"contact_email", "contact_phone", "education", "skill", "work_experience"} <= types
    for c in candidates:
        assert c["status"] == "pending"
        assert c["source_span_end"] >= c["source_span_start"] >= 0
        assert 0 < c["confidence"] <= 1
    # 受保护属性：绝不生成候选，只计数（简历里有性别/年龄/民族/籍贯/婚姻）
    protected = {"gender", "age", "birth_date", "photo", "marital_status", "ethnicity",
                 "native_place"}
    assert not (types & protected)
    assert parse_body["protected_discarded_count"] >= 4


async def test_pdf_full_flow_upload_parse_candidates(client, db_factory):
    await login(client, db_factory)
    created = await upload_and_confirm(client, "resume.pdf", make_pdf(), PDF_MIME)
    assert created["duplicate"] is False
    assert created["resume"]["status"] == "parsed"  # eager 模式下解析已同步完成
    assert created["resume"]["text_extract_status"] == "succeeded"

    resume_id = created["resume"]["id"]
    parse = await client.get(f"/api/v1/resumes/{resume_id}/parse")
    assert parse.status_code == 200, parse.text
    assert_full_parse(parse.json())

    # 值来自真实文本 span，不虚构
    email = next(
        c for c in parse.json()["candidates"] if c["fact_type"] == "contact_email"
    )
    assert email["value_json"]["email"] == "chenhaoran.py@example.com"


async def test_docx_full_flow(client, db_factory):
    await login(client, db_factory)
    created = await upload_and_confirm(client, "resume.docx", make_docx(), DOCX_MIME)
    assert created["resume"]["status"] == "parsed"
    parse = await client.get(f"/api/v1/resumes/{created['resume']['id']}/parse")
    assert parse.status_code == 200
    assert_full_parse(parse.json())


async def test_duplicate_upload_is_idempotent(client, db_factory):
    """同文件重复上传确认：不重复建简历、不重复解析、不重复入库。"""
    user = await login(client, db_factory)
    data = make_pdf()
    first = await upload_and_confirm(client, "resume.pdf", data, PDF_MIME)
    second = await upload_and_confirm(client, "copy-of-resume.pdf", data, PDF_MIME)

    assert second["duplicate"] is True
    assert second["resume"]["id"] == first["resume"]["id"]
    assert second["parse_id"] is None

    async with db_factory() as db:
        resumes = (
            (await db.execute(select(Resume).where(Resume.user_id == user.id))).scalars().all()
        )
        assert len(resumes) == 1
        parses = (
            (
                await db.execute(
                    select(ResumeParse).where(ResumeParse.resume_id == resumes[0].id)
                )
            )
            .scalars()
            .all()
        )
        assert len(parses) == 1


async def test_task_rerun_does_not_duplicate_candidates(client, db_factory):
    """直接重复投递同一解析任务：候选不重复入库（任务级幂等）。"""
    await login(client, db_factory)
    created = await upload_and_confirm(client, "resume.pdf", make_pdf(), PDF_MIME)
    parse_id = created["parse_id"]

    async with db_factory() as db:
        before = (
            await db.execute(
                select(FactCandidate.id).where(FactCandidate.resume_parse_id == parse_id)
            )
        ).all()
    assert len(before) > 0

    from app.resumes.tasks import parse_resume_task

    result = parse_resume_task.apply(args=[str(parse_id)])
    assert result.get() == "already_succeeded"

    async with db_factory() as db:
        after = (
            await db.execute(
                select(FactCandidate.id).where(FactCandidate.resume_parse_id == parse_id)
            )
        ).all()
    assert len(after) == len(before)


async def test_scanned_pdf_without_text_layer_fails_honestly(client, db_factory):
    """扫描件（无文本层）：失败状态 NO_TEXT_LAYER，不伪装成功。"""
    await login(client, db_factory)
    created = await upload_and_confirm(client, "scan.pdf", make_blank_pdf(), PDF_MIME)
    assert created["resume"]["status"] == "parse_failed"
    assert created["resume"]["text_extract_status"] == "no_text_layer"

    parse = await client.get(f"/api/v1/resumes/{created['resume']['id']}/parse")
    body = parse.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "NO_TEXT_LAYER"
    assert body["candidates"] == []


async def test_parse_timeout_protection(client, db_factory, monkeypatch):
    """解析超时：任务安全失败并记 PARSE_TIMEOUT，不无限挂起。"""
    await login(client, db_factory)

    import app.resumes.tasks as tasks_mod

    def slow_extract(data, media_type):
        time.sleep(1.5)
        raise AssertionError("should have timed out before this matters")

    monkeypatch.setattr(tasks_mod, "_extract_and_parse", slow_extract)
    monkeypatch.setenv("PARSE_TIMEOUT_SECONDS", "0.2")
    get_settings.cache_clear()
    try:
        created = await upload_and_confirm(client, "resume.pdf", make_pdf(), PDF_MIME)
    finally:
        get_settings.cache_clear()

    assert created["resume"]["status"] == "parse_failed"
    parse = await client.get(f"/api/v1/resumes/{created['resume']['id']}/parse")
    assert parse.json()["status"] == "failed"
    assert parse.json()["error_code"] == "PARSE_TIMEOUT"


async def test_cross_user_resume_access_is_404(client, db_factory, make_client):
    await login(client, db_factory)
    created = await upload_and_confirm(client, "resume.pdf", make_pdf(), PDF_MIME)
    resume_id = created["resume"]["id"]

    other = make_client()
    async with other:
        other_user = await create_user(db_factory, unique_email("other"))
        other.cookies.set(session_cookie(), await create_session_for(db_factory, other_user))
        for method, path in (
            ("GET", f"/api/v1/resumes/{resume_id}"),
            ("GET", f"/api/v1/resumes/{resume_id}/parse"),
            ("DELETE", f"/api/v1/resumes/{resume_id}?confirm=true"),
        ):
            resp = await other.request(method, path)
            assert resp.status_code == 404, f"{method} {path}: {resp.status_code}"
            assert resp.json()["error"]["code"] == "NOT_FOUND"


async def test_logs_and_audit_contain_no_resume_content(client, db_factory, capfd):
    """日志与审计不得包含简历正文、联系方式、姓名（docs/08 第 8 节）。"""
    await login(client, db_factory)
    created = await upload_and_confirm(client, "resume.pdf", make_pdf(), PDF_MIME)
    parse = await client.get(f"/api/v1/resumes/{created['resume']['id']}/parse")
    assert parse.status_code == 200

    captured = capfd.readouterr()
    logs = captured.out + captured.err
    assert "resume_parsed" in logs  # 确认解析日志确实写过
    for needle in SENSITIVE_STRINGS:
        assert needle not in logs, f"log leaked sensitive string: {needle}"
    assert "resume.pdf" not in logs  # 原文件名也不进日志

    # 审计表全表扫描：不含正文/联系方式/文件名
    import psycopg

    from tests.integration.conftest import TEST_SYNC_DSN

    with psycopg.connect(TEST_SYNC_DSN) as conn:
        rows = conn.execute(
            "SELECT action, resource_type, resource_id, reason_code FROM audit_events"
        ).fetchall()
    dump = repr(rows)
    for needle in SENSITIVE_STRINGS:
        assert needle not in dump, f"audit leaked sensitive string: {needle}"
    assert "resume.pdf" not in dump


async def test_resume_list_and_detail(client, db_factory):
    await login(client, db_factory)
    a = await upload_and_confirm(client, "a.pdf", make_pdf(), PDF_MIME)
    b = await upload_and_confirm(client, "b.docx", make_docx(), DOCX_MIME)

    listing = await client.get("/api/v1/resumes")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert {i["id"] for i in items} == {a["resume"]["id"], b["resume"]["id"]}
    for item in items:
        assert "storage_key" not in item  # 列表不暴露存储 key

    detail = await client.get(f"/api/v1/resumes/{a['resume']['id']}")
    assert detail.status_code == 200
    assert detail.json()["latest_parse_status"] == "succeeded"

    # 游标分页
    page1 = await client.get("/api/v1/resumes?limit=1")
    assert len(page1.json()["items"]) == 1
    cursor = page1.json()["next_cursor"]
    assert cursor
    page2 = await client.get(f"/api/v1/resumes?limit=1&cursor={cursor}")
    assert len(page2.json()["items"]) == 1
    assert page2.json()["items"][0]["id"] != page1.json()["items"][0]["id"]
